"""Shared API logic for CLI and HTTP endpoints.

Extracts the core business logic for ask, sync, maintain, and project
management so both the argparse CLI and the HTTP API call the same code.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
	from collections.abc import Generator

from lerim import __version__
from lerim.adapters.registry import (
    connect_platform,
    list_platforms,
)
from lerim.context import ContextStore, resolve_project_identity
from lerim.server.daemon import (
    resolve_window_bounds,
    run_maintain_once,
    run_sync_once,
)
from lerim.config.settings import (
    Config,
    get_config,
    get_global_data_dir_path,
    get_user_config_path,
    load_toml_file,
    reload_config,
    save_config_patch,
    _write_config_full,
)
from lerim.server.runtime import LerimRuntime
from lerim.sessions.catalog import (
    count_fts_indexed,
    count_session_jobs_by_status,
    count_unscoped_sessions_by_agent,
    latest_service_run,
    list_session_jobs,
    list_service_runs,
    list_queue_jobs,
    list_unscoped_sessions,
    queue_health_snapshot,
    retry_session_job,
    skip_session_job,
)


# ── Argument parsing helpers (inlined from arg_utils.py) ────────────


def parse_duration_to_seconds(raw: str) -> int:
	"""Parse ``<number><unit>`` durations like ``30s`` or ``7d`` to seconds."""
	value = (raw or "").strip().lower()
	if len(value) < 2:
		raise ValueError("duration must be <number><unit>, for example: 30s, 2m, 1h, 7d")
	unit = value[-1]
	amount_text = value[:-1]
	if not amount_text.isdigit():
		raise ValueError("duration must be <number><unit>, for example: 30s, 2m, 1h, 7d")
	amount = int(amount_text)
	if amount <= 0:
		raise ValueError("duration must be greater than 0")
	multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
	if unit not in multipliers:
		raise ValueError("duration unit must be one of: s, m, h, d")
	return amount * multipliers[unit]


def parse_csv(raw: str | None) -> list[str]:
	"""Split a comma-delimited string into trimmed non-empty values."""
	if not raw:
		return []
	return [part.strip() for part in raw.split(",") if part.strip()]


def parse_agent_filter(raw: str | None) -> list[str] | None:
	"""Normalize agent filter input and drop the ``all`` sentinel."""
	values = parse_csv(raw)
	cleaned = [value for value in values if value and value != "all"]
	if not cleaned:
		return None
	return sorted(set(cleaned))


def looks_like_auth_error(response: str) -> bool:
    """Return whether response text indicates authentication failure."""
    text = str(response or "").lower()
    return (
        "failed to authenticate" in text
        or "authentication_error" in text
        or "oauth token has expired" in text
        or "invalid api key" in text
        or "unauthorized" in text
    )


# ── Ollama model lifecycle (inlined from ollama_lifecycle.py) ───────


def _ollama_models(config: Config) -> list[tuple[str, str]]:
	"""Return deduplicated (base_url, model) pairs for all ollama roles."""
	seen: set[tuple[str, str]] = set()
	pairs: list[tuple[str, str]] = []

	default_base = config.provider_api_bases.get("ollama", "http://127.0.0.1:11434")

	for role in (config.agent_role,):
		if role.provider == "ollama":
			base = role.api_base or default_base
			key = (base, role.model)
			if key not in seen:
				seen.add(key)
				pairs.append(key)

	return pairs


def _is_ollama_reachable(base_url: str, timeout: float = 5.0) -> bool:
	"""Check if Ollama is reachable at the given base URL."""
	try:
		resp = httpx.get(f"{base_url}/api/tags", timeout=timeout)
		return resp.status_code == 200
	except (httpx.ConnectError, httpx.TimeoutException, OSError):
		return False


def _load_model(base_url: str, model: str, timeout: float = 120.0) -> None:
	"""Warm-load an Ollama model by sending a minimal generation request."""
	httpx.post(
		f"{base_url}/api/generate",
		json={"model": model, "prompt": "hi", "options": {"num_predict": 1}},
		timeout=timeout,
	)


def _unload_model(base_url: str, model: str, timeout: float = 30.0) -> None:
	"""Unload an Ollama model by setting keep_alive to 0."""
	httpx.post(
		f"{base_url}/api/generate",
		json={"model": model, "keep_alive": 0},
		timeout=timeout,
	)


@contextmanager
def ollama_lifecycle(config: Config) -> Generator[None, None, None]:
	"""Context manager that loads Ollama models on enter and unloads on exit.

	No-op when no roles use provider="ollama" or when auto_unload is False.
	Logs warnings on failure but never raises — the daemon must not crash
	because of lifecycle issues.
	"""
	if config.agent_role.provider != "ollama":
		yield
		return

	from lerim.config.logging import logger

	models = _ollama_models(config)

	if not models:
		yield
		return

	# Group models by base_url for a single reachability check per server.
	bases = {base for base, _ in models}

	reachable_bases: set[str] = set()
	for base in bases:
		if _is_ollama_reachable(base):
			reachable_bases.add(base)
		else:
			logger.warning("ollama not reachable at {}, skipping lifecycle", base)

	# Warm-load models on reachable servers.
	for base, model in models:
		if base not in reachable_bases:
			continue
		try:
			logger.info("loading ollama model {}/{}", base, model)
			_load_model(base, model)
		except Exception as exc:
			logger.warning("failed to warm-load {}/{}: {}", base, model, exc)

	try:
		yield
	finally:
		if not config.auto_unload:
			return

		for base, model in models:
			if base not in reachable_bases:
				continue
			try:
				logger.info("unloading ollama model {}/{}", base, model)
				_unload_model(base, model)
			except Exception as exc:
				logger.warning("failed to unload {}/{}: {}", base, model, exc)


# ── Known agent default paths ───────────────────────────────────────

AGENT_DEFAULT_PATHS: dict[str, str] = {
    "claude": "~/.claude/projects",
    "codex": "~/.codex/sessions",
    "cursor": "~/Library/Application Support/Cursor/User/globalStorage",
    "opencode": "~/.local/share/opencode",
}


def api_health() -> dict[str, Any]:
    """Return health check payload."""
    return {"status": "ok", "version": __version__}


def _registered_projects(config: Config) -> list[tuple[str, Path]]:
    """Return registered projects as resolved (name, path) pairs."""
    items: list[tuple[str, Path]] = []
    for name, path_str in config.projects.items():
        items.append((name, Path(path_str).expanduser().resolve()))
    return items


def _context_store(config: Config) -> ContextStore:
    """Return the canonical global context store."""
    store = ContextStore(config.context_db_path)
    store.initialize()
    return store


def _resolve_selected_projects(
    *,
    config: Config,
    scope: str,
    project: str | None,
) -> list[tuple[str, Path]]:
    """Resolve target projects for scoped read/query APIs."""
    all_projects = _registered_projects(config)
    if scope != "project":
        return all_projects

    if project:
        token = project.strip()
        if token in config.projects:
            return [(token, Path(config.projects[token]).expanduser().resolve())]
        try:
            project_path = Path(token).expanduser().resolve()
        except Exception:
            project_path = None
        if project_path is not None:
            for name, path in all_projects:
                if path == project_path:
                    return [(name, path)]
        raise ValueError(f"Project not found: {project}")

    if len(all_projects) == 1:
        return [all_projects[0]]
    if not all_projects:
        return []
    raise ValueError("scope=project requires a project name when multiple projects are registered.")


def _count_project_records(config: Config, project_path: Path) -> int:
    """Count canonical records for one registered project."""
    store = _context_store(config)
    identity = resolve_project_identity(project_path)
    store.register_project(identity)
    with store.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(1) AS total FROM records WHERE project_id = ?",
            (identity.project_id,),
        ).fetchone()
    return int(row["total"]) if row else 0


def _session_stats_for_repo(
    *,
    sessions_db_path: Path,
    repo_path: str,
) -> tuple[int, str | None]:
    """Return indexed-session count and latest session start time for one repo."""
    if not sessions_db_path.exists():
        return 0, None

    try:
        with sqlite3.connect(sessions_db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT COUNT(1) AS total, MAX(start_time) AS latest_start_time
                FROM session_docs
                WHERE repo_path = ?
                """,
                (repo_path,),
            ).fetchone()
    except sqlite3.Error:
        return 0, None

    if not row:
        return 0, None
    return int(row["total"] or 0), str(row["latest_start_time"] or "") or None


def _queue_counts_for_repo(
    *,
    sessions_db_path: Path,
    repo_path: str,
) -> tuple[dict[str, int], str | None, str | None]:
    """Return queue counts + oldest dead-letter blocker + latest error for repo."""
    counts: dict[str, int] = {}
    blocked_run_id: str | None = None
    last_error: str | None = None
    if not sessions_db_path.exists():
        return counts, blocked_run_id, last_error

    try:
        with sqlite3.connect(sessions_db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT status, COUNT(1) AS total
                FROM session_jobs
                WHERE repo_path = ?
                GROUP BY status
                """,
                (repo_path,),
            ).fetchall()
            counts = {str(row["status"]): int(row["total"]) for row in rows}

            oldest = conn.execute(
                """
                SELECT run_id, status
                FROM session_jobs
                WHERE repo_path = ?
                  AND status IN ('pending', 'failed', 'dead_letter')
                ORDER BY start_time ASC, available_at ASC, id ASC
                LIMIT 1
                """,
                (repo_path,),
            ).fetchone()
            if oldest and str(oldest["status"]) == "dead_letter":
                blocked_run_id = str(oldest["run_id"])

            latest_err = conn.execute(
                """
                SELECT error
                FROM session_jobs
                WHERE repo_path = ?
                  AND status IN ('failed', 'dead_letter')
                  AND error IS NOT NULL
                  AND error != ''
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (repo_path,),
            ).fetchone()
            if latest_err:
                last_error = str(latest_err["error"])
    except sqlite3.Error:
        return {}, None, None

    for status in ("pending", "running", "done", "failed", "dead_letter"):
        counts.setdefault(status, 0)
    return counts, blocked_run_id, last_error


def api_ask(
    question: str,
    *,
    scope: str = "all",
    project: str | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Run one ask query against the runtime agent and return result dict."""
    config = get_config()
    selected_projects: list[tuple[str, Path]] = []
    normalized_scope = "project" if str(scope).strip().lower() == "project" else "all"
    try:
        selected_projects = _resolve_selected_projects(
            config=config,
            scope=normalized_scope,
            project=project,
        )
    except ValueError as exc:
        return {
            "answer": str(exc),
            "agent_session_id": "",
            "projects_used": [],
            "error": True,
            "cost_usd": 0.0,
        }

    agent = LerimRuntime()
    project_ids: list[str] = []
    repo_root: str | Path | None = None
    if selected_projects:
        for _name, path in selected_projects:
            identity = resolve_project_identity(path)
            _context_store(config).register_project(identity)
            project_ids.append(identity.project_id)
        repo_root = selected_projects[0][1]
    response, session_id, cost_usd, debug = agent.ask(
        question,
        project_ids=project_ids or None,
        repo_root=repo_root,
        include_debug=bool(verbose),
    )
    error = looks_like_auth_error(response)
    payload = {
        "answer": response,
        "agent_session_id": session_id,
        "projects_used": [name for name, _ in selected_projects],
        "error": bool(error),
        "cost_usd": cost_usd,
        "scope": normalized_scope,
    }
    if verbose and debug is not None:
        payload["debug"] = debug
    return payload


def api_query(
    *,
    entity: str,
    mode: str,
    scope: str = "all",
    project: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    source_session_id: str | None = None,
    created_since: str | None = None,
    created_until: str | None = None,
    updated_since: str | None = None,
    updated_until: str | None = None,
    valid_at: str | None = None,
    order_by: str = "created_at",
    limit: int = 20,
    offset: int = 0,
    include_total: bool = False,
) -> dict[str, Any]:
    """Run one deterministic context query against the canonical context store."""
    config = get_config()
    normalized_scope = "project" if str(scope).strip().lower() == "project" else "all"
    try:
        selected_projects = _resolve_selected_projects(
            config=config,
            scope=normalized_scope,
            project=project,
        )
    except ValueError as exc:
        return {
            "error": True,
            "message": str(exc),
            "projects_used": [],
        }

    store = _context_store(config)
    project_ids: list[str] = []
    for _name, path in selected_projects:
        identity = resolve_project_identity(path)
        store.register_project(identity)
        project_ids.append(identity.project_id)

    try:
        payload = store.query(
            entity=entity,
            mode=mode,
            project_ids=project_ids or None,
            kind=kind,
            status=status,
            source_session_id=source_session_id,
            created_since=created_since,
            created_until=created_until,
            updated_since=updated_since,
            updated_until=updated_until,
            valid_at=valid_at,
            order_by=order_by,
            limit=limit,
            offset=offset,
            include_total=include_total,
        )
    except Exception as exc:
        return {
            "error": True,
            "message": str(exc),
            "projects_used": [name for name, _ in selected_projects],
        }
    return {
        **payload,
        "error": False,
        "projects_used": [name for name, _ in selected_projects],
        "scope": normalized_scope,
    }


def api_sync(
    agent: str | None = None,
    window: str | None = None,
    since: str | None = None,
    until: str | None = None,
    max_sessions: int | None = None,
    run_id: str | None = None,
    no_extract: bool = False,
    force: bool = False,
    dry_run: bool = False,
    ignore_lock: bool = False,
) -> dict[str, Any]:
    """Run one sync cycle and return summary dict."""
    config = get_config()
    window_start, window_end = resolve_window_bounds(
        window=window or f"{config.sync_window_days}d",
        since_raw=since,
        until_raw=until,
        parse_duration_to_seconds=parse_duration_to_seconds,
    )
    with ollama_lifecycle(config):
        code, summary = run_sync_once(
            run_id=run_id,
            agent_filter=parse_agent_filter(agent) if agent else None,
            no_extract=no_extract,
            force=force,
            max_sessions=max_sessions or config.sync_max_sessions,
            dry_run=dry_run,
            ignore_lock=ignore_lock,
            trigger="api",
            window_start=window_start,
            window_end=window_end,
        )
    queue_health = queue_health_snapshot()
    payload: dict[str, Any] = {"code": code, **asdict(summary), "queue_health": queue_health}
    if queue_health.get("degraded"):
        payload["warning"] = (
            "Queue degraded. "
            + str(queue_health.get("advice") or "Run `lerim queue --failed`.")
        )
    return payload


def api_maintain(force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    """Run one maintain cycle and return result dict."""
    config = get_config()
    with ollama_lifecycle(config):
        code, payload = run_maintain_once(force=force, dry_run=dry_run)
    queue_health = queue_health_snapshot()
    result: dict[str, Any] = {"code": code, **payload, "queue_health": queue_health}
    if queue_health.get("degraded"):
        result["warning"] = (
            "Queue degraded. "
            + str(queue_health.get("advice") or "Run `lerim queue --failed`.")
        )
    return result


def _parse_iso_time(raw: str | None) -> datetime | None:
    """Parse an ISO timestamp safely."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_ms_from_run(run: dict[str, Any]) -> int | None:
    """Compute run duration in milliseconds when timestamps are valid."""
    started = _parse_iso_time(str(run.get("started_at") or ""))
    completed = _parse_iso_time(str(run.get("completed_at") or ""))
    if not started or not completed:
        return None
    return int((completed - started).total_seconds() * 1000)


def _normalize_activity_item(run: dict[str, Any]) -> dict[str, Any]:
    """Normalize one service_run row into status activity item."""
    details = run.get("details") if isinstance(run.get("details"), dict) else {}
    projects_metrics = (
        details.get("projects_metrics")
        if isinstance(details.get("projects_metrics"), dict)
        else {}
    )
    project_names = sorted(str(k) for k in projects_metrics.keys())
    if len(project_names) == 1:
        project_label = project_names[0]
    elif len(project_names) > 1:
        project_label = f"{len(project_names)} projects"
    else:
        project_label = "global"

    op_type = str(run.get("job_type") or "sync")
    base: dict[str, Any] = {
        "time": run.get("started_at"),
        "op_type": op_type,
        "status": str(run.get("status") or "unknown"),
        "duration_ms": _duration_ms_from_run(run),
        "projects": project_names,
        "project_label": project_label,
        "error": str(details.get("error") or ""),
    }

    if op_type == "maintain":
        maintain_metrics = (
            details.get("maintain_metrics")
            if isinstance(details.get("maintain_metrics"), dict)
            else {}
        )
        counts = (
            maintain_metrics.get("counts")
            if isinstance(maintain_metrics.get("counts"), dict)
            else {}
        )
        if not counts:
            agg = {"merged": 0, "archived": 0, "consolidated": 0, "unchanged": 0}
            projects = details.get("projects") if isinstance(details.get("projects"), dict) else {}
            for project_result in projects.values():
                if not isinstance(project_result, dict):
                    continue
                raw = project_result.get("counts") if isinstance(project_result.get("counts"), dict) else {}
                agg["merged"] += int(raw.get("merged") or 0)
                agg["archived"] += int(raw.get("archived") or raw.get("decayed") or 0)
                agg["consolidated"] += int(raw.get("consolidated") or 0)
                agg["unchanged"] += int(raw.get("unchanged") or 0)
            counts = agg
        base.update(
            {
                "maintain_counts": {
                    "merged": int(counts.get("merged") or 0),
                    "archived": int(counts.get("archived") or 0),
                    "consolidated": int(counts.get("consolidated") or 0),
                    "unchanged": int(counts.get("unchanged") or 0),
                },
                "records_created": int(maintain_metrics.get("records_created") or 0),
                "records_updated": int(maintain_metrics.get("records_updated") or 0),
                "records_archived": int(maintain_metrics.get("records_archived") or 0),
            }
        )
        return base

    sync_metrics = details.get("sync_metrics") if isinstance(details.get("sync_metrics"), dict) else {}
    base.update(
        {
            "sessions_analyzed": int(
                sync_metrics.get("sessions_analyzed")
                or details.get("indexed_sessions")
                or details.get("queued_sessions")
                or 0
            ),
            "sessions_extracted": int(
                sync_metrics.get("sessions_extracted")
                or details.get("extracted_sessions")
                or 0
            ),
            "sessions_failed": int(
                sync_metrics.get("sessions_failed")
                or details.get("failed_sessions")
                or 0
            ),
            "sessions_skipped": int(
                sync_metrics.get("sessions_skipped")
                or details.get("skipped_sessions")
                or 0
            ),
            "records_created": int(sync_metrics.get("records_created") or 0),
            "records_updated": int(sync_metrics.get("records_updated") or 0),
            "records_archived": int(sync_metrics.get("records_archived") or 0),
        }
    )
    return base


def _recent_activity(
    *,
    limit: int = 12,
    allowed_projects: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return normalized recent service activity for status UI."""
    rows = list_service_runs(limit=max(1, int(limit)))
    items = [_normalize_activity_item(run) for run in rows]
    items = [item for item in items if not _is_empty_activity_item(item)]
    if allowed_projects:
        filtered: list[dict[str, Any]] = []
        for item in items:
            projects = item.get("projects")
            if isinstance(projects, list) and projects:
                if any(str(name) in allowed_projects for name in projects):
                    filtered.append(item)
            elif item.get("project_label") in allowed_projects:
                filtered.append(item)
        items = filtered
    return items


def _is_empty_activity_item(item: dict[str, Any]) -> bool:
    """Return whether an activity row carries no project scope and no useful counters."""
    return (
        not item.get("projects")
        and not item.get("error")
        and int(item.get("sessions_analyzed") or 0) == 0
        and int(item.get("sessions_extracted") or 0) == 0
        and int(item.get("sessions_failed") or 0) == 0
        and int(item.get("sessions_skipped") or 0) == 0
        and int(item.get("records_created") or 0) == 0
        and int(item.get("records_updated") or 0) == 0
        and int(item.get("records_archived") or 0) == 0
        and not item.get("maintain_counts")
    )


def _normalize_latest_run(run: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a stable latest-run payload with normalized record-era fields only."""
    if not isinstance(run, dict):
        return None
    details = run.get("details") if isinstance(run.get("details"), dict) else {}
    normalized = _normalize_activity_item(run)
    details_payload: dict[str, Any] = {
        "projects": normalized.get("projects") or [],
        "project_label": normalized.get("project_label") or "",
        "error": str(details.get("error") or ""),
    }
    if str(run.get("job_type") or "") == "maintain":
        details_payload["maintain_counts"] = normalized.get("maintain_counts") or {}
        details_payload["records_created"] = int(normalized.get("records_created") or 0)
        details_payload["records_updated"] = int(normalized.get("records_updated") or 0)
        details_payload["records_archived"] = int(normalized.get("records_archived") or 0)
    else:
        details_payload["sessions_analyzed"] = int(normalized.get("sessions_analyzed") or 0)
        details_payload["sessions_extracted"] = int(normalized.get("sessions_extracted") or 0)
        details_payload["sessions_failed"] = int(normalized.get("sessions_failed") or 0)
        details_payload["sessions_skipped"] = int(normalized.get("sessions_skipped") or 0)
        details_payload["records_created"] = int(normalized.get("records_created") or 0)
        details_payload["records_updated"] = int(normalized.get("records_updated") or 0)
        details_payload["records_archived"] = int(normalized.get("records_archived") or 0)
        details_payload["skipped_unscoped"] = int(details.get("skipped_unscoped") or 0)
    return {
        "id": run.get("id"),
        "job_type": run.get("job_type"),
        "status": run.get("status"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "trigger": run.get("trigger"),
        "details": details_payload,
    }


def _activity_as_latest_run(item: dict[str, Any]) -> dict[str, Any]:
    """Convert a normalized activity row into latest-run response shape."""
    details: dict[str, Any] = {
        "projects": item.get("projects") or [],
        "project_label": item.get("project_label") or "",
        "error": item.get("error") or "",
    }
    if item.get("op_type") == "maintain":
        details["maintain_counts"] = item.get("maintain_counts") or {}
        details["records_created"] = int(item.get("records_created") or 0)
        details["records_updated"] = int(item.get("records_updated") or 0)
        details["records_archived"] = int(item.get("records_archived") or 0)
    else:
        details["sessions_analyzed"] = int(item.get("sessions_analyzed") or 0)
        details["sessions_extracted"] = int(item.get("sessions_extracted") or 0)
        details["sessions_failed"] = int(item.get("sessions_failed") or 0)
        details["sessions_skipped"] = int(item.get("sessions_skipped") or 0)
        details["records_created"] = int(item.get("records_created") or 0)
        details["records_updated"] = int(item.get("records_updated") or 0)
        details["records_archived"] = int(item.get("records_archived") or 0)
        details["skipped_unscoped"] = 0
    return {
        "id": None,
        "job_type": item.get("op_type"),
        "status": item.get("status"),
        "started_at": item.get("time"),
        "completed_at": None,
        "trigger": "derived",
        "details": details,
    }


def _running_activity_rows(
    *,
    selected_projects: list[tuple[str, Path]],
) -> list[dict[str, Any]]:
    """Build live activity rows from currently running queue jobs."""
    jobs = list_session_jobs(limit=200, status="running")
    if not jobs:
        return []

    repo_to_name: dict[str, str] = {
        str(path): str(name) for name, path in selected_projects
    }
    allowed_repo_paths = set(repo_to_name.keys())
    now = datetime.now(timezone.utc)
    grouped: dict[str, dict[str, Any]] = {}

    for job in jobs:
        repo_path = str(job.get("repo_path") or "").strip()
        if allowed_repo_paths and repo_path not in allowed_repo_paths:
            continue
        project_name = repo_to_name.get(repo_path) or (Path(repo_path).name if repo_path else "global")
        row = grouped.setdefault(
            project_name,
            {
                "time": now.isoformat(),
                "op_type": "sync",
                "status": "running",
                "duration_ms": 0,
                "projects": [project_name],
                "project_label": project_name,
                "error": "",
                "sessions_analyzed": 0,
                "sessions_extracted": 0,
                "sessions_failed": 0,
                "sessions_skipped": 0,
                "records_created": 0,
                "records_updated": 0,
                "records_archived": 0,
            },
        )
        row["sessions_analyzed"] = int(row.get("sessions_analyzed") or 0) + 1
        claimed = _parse_iso_time(str(job.get("claimed_at") or ""))
        if claimed is not None:
            elapsed_ms = max(0, int((now - claimed).total_seconds() * 1000))
            row["duration_ms"] = max(int(row.get("duration_ms") or 0), elapsed_ms)

    items = list(grouped.values())
    items.sort(key=lambda item: int(item.get("duration_ms") or 0), reverse=True)
    return items


def api_status(
    *,
    scope: str = "all",
    project: str | None = None,
) -> dict[str, Any]:
    """Return runtime status summary."""
    config = get_config()
    normalized_scope = "project" if str(scope).strip().lower() == "project" else "all"
    selection_error: str | None = None
    try:
        selected_projects = _resolve_selected_projects(
            config=config,
            scope=normalized_scope,
            project=project,
        )
    except ValueError as exc:
        selection_error = str(exc)
        selected_projects = []

    projects_payload: list[dict[str, Any]] = []
    total_records = 0
    for name, path in selected_projects:
        record_count = _count_project_records(config, path)
        total_records += record_count
        indexed_sessions_count, latest_session_start_time = _session_stats_for_repo(
            sessions_db_path=config.sessions_db_path,
            repo_path=str(path),
        )
        queue_counts, blocked_run_id, last_error = _queue_counts_for_repo(
            sessions_db_path=config.sessions_db_path,
            repo_path=str(path),
        )
        projects_payload.append(
            {
                "name": name,
                "path": str(path),
                "project_id": resolve_project_identity(path).project_id,
                "record_count": record_count,
                "indexed_sessions_count": indexed_sessions_count,
                "latest_session_start_time": latest_session_start_time,
                "queue": queue_counts,
                "oldest_blocked_run_id": blocked_run_id,
                "last_error": last_error,
            }
        )
    if not projects_payload and normalized_scope == "all":
        total_records = 0

    latest_sync_raw = latest_service_run("sync")
    latest_maintain_raw = latest_service_run("maintain")
    queue = count_session_jobs_by_status()
    queue_health = queue_health_snapshot()
    latest_sync_details = (latest_sync_raw or {}).get("details") or {}
    unscoped_by_agent = count_unscoped_sessions_by_agent(projects=config.projects)

    selected_project_names = {name for name, _ in selected_projects}

    platforms = list_platforms(config.platforms_path)
    recent_activity = (
        _running_activity_rows(selected_projects=selected_projects)
        + _recent_activity(
            limit=12,
            allowed_projects=selected_project_names if normalized_scope == "project" else None,
        )
    )[:12]

    latest_sync = _normalize_latest_run(latest_sync_raw)
    if latest_sync and _is_empty_activity_item(
        _normalize_activity_item(latest_sync_raw or {})
    ):
        fallback_sync = next(
            (item for item in recent_activity if item.get("op_type") == "sync"),
            None,
        )
        if fallback_sync is not None:
            latest_sync = _activity_as_latest_run(fallback_sync)

    payload: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "connected_agents": [str(item.get("name") or "") for item in platforms if item.get("name")],
        "platforms": platforms,
        "record_count": total_records,
        "sessions_indexed_count": count_fts_indexed(),
        "queue": queue,
        "queue_health": queue_health,
        "projects": projects_payload,
        "sync_window_days": config.sync_window_days,
        "unscoped_sessions": {
            "total": sum(unscoped_by_agent.values()),
            "by_agent": unscoped_by_agent,
        },
        "scope": {
            "strict_project_only": True,
            "mode": normalized_scope,
            "skipped_unscoped": int(latest_sync_details.get("skipped_unscoped") or 0),
        },
        "latest_sync": latest_sync,
        "latest_maintain": _normalize_latest_run(latest_maintain_raw),
        "recent_activity": recent_activity,
    }
    if selection_error:
        payload["error"] = selection_error
    return payload


def api_connect_list() -> list[dict[str, Any]]:
    """Return list of connected platforms."""
    config = get_config()
    return list_platforms(config.platforms_path)


def api_connect(platform: str, path: str | None = None) -> dict[str, Any]:
    """Connect a platform and return result."""
    config = get_config()
    return connect_platform(config.platforms_path, platform, custom_path=path)


# ── Job queue management ─────────────────────────────────────────────


def api_retry_job(run_id: str) -> dict[str, Any]:
    """Retry a dead_letter job, returning result."""
    ok = retry_session_job(run_id)
    return {"retried": ok, "run_id": run_id, "queue": count_session_jobs_by_status()}


def api_skip_job(run_id: str) -> dict[str, Any]:
    """Skip a dead_letter job, returning result."""
    ok = skip_session_job(run_id)
    return {"skipped": ok, "run_id": run_id, "queue": count_session_jobs_by_status()}


def api_retry_all_dead_letter() -> dict[str, Any]:
    """Retry all dead_letter jobs across all projects."""
    dead = list_queue_jobs(status_filter="dead_letter")
    retried = 0
    for job in dead:
        rid = str(job.get("run_id") or "")
        if rid and retry_session_job(rid):
            retried += 1
    return {"retried": retried, "queue": count_session_jobs_by_status()}


def api_skip_all_dead_letter() -> dict[str, Any]:
    """Skip all dead_letter jobs across all projects."""
    dead = list_queue_jobs(status_filter="dead_letter")
    skipped = 0
    for job in dead:
        rid = str(job.get("run_id") or "")
        if rid and skip_session_job(rid):
            skipped += 1
    return {"skipped": skipped, "queue": count_session_jobs_by_status()}


def api_queue_jobs(
    status: str | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """List queue jobs with optional filters."""
    project_filter: str | None = None
    project_exact = False
    if project:
        config = get_config()
        selected = _resolve_selected_projects(
            config=config, scope="project", project=project
        )
        if selected:
            _name, project_path = selected[0]
            project_filter = str(project_path)
            project_exact = True

    jobs = list_queue_jobs(
        status_filter=status,
        project_filter=project_filter,
        project_exact=project_exact,
        failed_only=(status == "failed"),
    )
    return {"jobs": jobs, "total": len(jobs), "queue": count_session_jobs_by_status()}


def api_unscoped(*, limit: int = 50) -> dict[str, Any]:
    """List unscoped indexed sessions and aggregate counts."""
    config = get_config()
    items = list_unscoped_sessions(projects=config.projects, limit=limit)
    counts = count_unscoped_sessions_by_agent(projects=config.projects)
    return {
        "items": items,
        "total": len(items),
        "count_by_agent": counts,
    }


# ── Project management ───────────────────────────────────────────────


def api_project_list() -> list[dict[str, Any]]:
    """Return registered projects from config."""
    config = get_config()
    result: list[dict[str, Any]] = []
    for name, path_str in config.projects.items():
        resolved = Path(path_str).expanduser().resolve()
        result.append(
            {
                "name": name,
                "path": str(resolved),
                "exists": resolved.exists(),
            }
        )
    return result


def _project_config_name(resolved: Path, existing_projects: dict[str, str]) -> str:
    """Return a deterministic config key for one registered project path."""
    base_name = resolved.name
    current = existing_projects.get(base_name)
    if current is None or Path(current).expanduser().resolve() == resolved:
        return base_name
    suffix = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:6]
    return f"{base_name}-{suffix}"


def api_project_add(path_str: str) -> dict[str, Any]:
    """Register a project directory and return status."""
    resolved = Path(path_str).expanduser().resolve()
    if not resolved.is_dir():
        return {"error": f"Not a directory: {resolved}", "name": None}

    config = get_config()
    name = _project_config_name(resolved, config.projects or {})
    # Update config
    save_config_patch({"projects": {name: str(resolved)}})
    config = get_config()
    store = _context_store(config)
    identity = resolve_project_identity(resolved)
    store.register_project(identity)

    return {
        "name": name,
        "path": str(resolved),
        "project_id": identity.project_id,
        "context_db_path": str(config.context_db_path),
    }


def api_project_remove(name: str) -> dict[str, Any]:
    """Unregister a project by name."""
    config = get_config()
    if name not in config.projects:
        return {"error": f"Project not registered: {name}", "removed": False}

    existing: dict[str, Any] = {}
    user_config_path = get_user_config_path()
    if user_config_path.exists():
        existing = load_toml_file(user_config_path)

    projects = existing.get("projects", {})
    if isinstance(projects, dict) and name in projects:
        del projects[name]
        existing["projects"] = projects

    # Write directly — save_config_patch would re-merge the deleted key
    _write_config_full(existing)
    return {"name": name, "removed": True}


# ── Init wizard helpers ──────────────────────────────────────────────


def detect_agents() -> dict[str, dict[str, Any]]:
    """Detect available coding agents by checking known default paths."""
    result: dict[str, dict[str, Any]] = {}
    for name, default_path in AGENT_DEFAULT_PATHS.items():
        resolved = Path(default_path).expanduser()
        result[name] = {
            "path": str(resolved),
            "exists": resolved.exists(),
        }
    return result


def docker_available() -> bool:
    """Check if Docker is installed and the daemon is running."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def write_init_config(selected_agents: dict[str, str]) -> Path:
    """Write initial [agents] config and return the config file path."""
    save_config_patch({"agents": selected_agents})
    return get_user_config_path()


# ── Docker management ────────────────────────────────────────────────


COMPOSE_PATH = get_global_data_dir_path() / "docker-compose.yml"
GHCR_IMAGE = "ghcr.io/lerim-dev/lerim-cli"


_API_KEY_ENV_NAMES = (
    "ANTHROPIC_API_KEY",
    "MINIMAX_API_KEY",
    "OPENAI_API_KEY",
    "OPENCODE_API_KEY",
    "OPENROUTER_API_KEY",
    "ZAI_API_KEY",
)



def _find_package_root() -> Path | None:
    """Locate the Lerim source tree root by walking up from this file."""
    candidate = Path(__file__).resolve().parent
    for _ in range(5):
        if (candidate / "Dockerfile").is_file():
            return candidate
        candidate = candidate.parent
    return None


def _generate_compose_yml(build_local: bool = False) -> str:
    """Generate docker-compose.yml content from current config.

    When *build_local* is True the compose file uses a ``build:`` directive
    pointing at the local source tree (requires a Dockerfile).  Otherwise it
    references the pre-built GHCR image tagged with the current version.
    """
    config = reload_config()
    home = str(Path.home())
    user_spec = f"{os.getuid()}:{os.getgid()}"

    # Mount global Lerim state only. Project roots are identifiers for routing,
    # not local runtime state.
    lerim_dir = str(config.global_data_dir)
    volumes = [f"      - {lerim_dir}:{lerim_dir}"]

    # Agent session dirs (read-only — agent reads traces but never modifies them)
    for _name, path_str in config.agents.items():
        resolved = str(Path(path_str).expanduser().resolve())
        volumes.append(f"      - {resolved}:{resolved}:ro")

    volumes_block = "\n".join(volumes)
    port = config.server_port

    # Forward API keys by name only — Docker reads values from host env.
    # NEVER write secret values into the compose file.
    env_lines = [
        f"      - HOME={home}",
        "      - FASTEMBED_CACHE_PATH=/opt/lerim/models",
    ]
    for key in _API_KEY_ENV_NAMES:
        if os.environ.get(key):
            env_lines.append(f"      - {key}")
    # Forward MLflow flag so tracing is enabled inside the container
    if os.environ.get("LERIM_MLFLOW"):
        env_lines.append("      - LERIM_MLFLOW")
    env_block = "\n".join(env_lines)

    if build_local:
        pkg_root = _find_package_root()
        if pkg_root is None:
            raise FileNotFoundError(
                "Cannot find Dockerfile in the Lerim source tree. "
                "Use 'lerim up' without --build to pull the GHCR image."
            )
        image_or_build = f"    build: {pkg_root}"
    else:
        image_or_build = f"    image: {GHCR_IMAGE}:{__version__}"

    # Resolve seccomp profile path (shipped with the package)
    seccomp_path = Path(__file__).parent / "lerim-seccomp.json"
    seccomp_line = ""
    if seccomp_path.exists():
        seccomp_line = f"\n      - seccomp={seccomp_path}"

    return f"""\
# Auto-generated by lerim up — do not edit manually.
# Regenerated from the active Lerim config on every `lerim up`.
services:
  lerim:
{image_or_build}
    user: "{user_spec}"
    command: ["--host", "0.0.0.0", "--port", "{port}"]
    restart: "no"
    ports:
      - "127.0.0.1:{port}:{port}"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    # Container hardening
    read_only: true
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true{seccomp_line}
    pids_limit: 256
    mem_limit: 2g
    tmpfs:
      - /tmp:size=100M
      - {home}/.codex:size=50M
      - {home}/.config:size=10M
      - /root/.codex:size=50M
      - /root/.config:size=10M
    environment:
{env_block}
    volumes:
{volumes_block}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:{port}/api/health"]
      interval: 30s
      timeout: 5s
      retries: 3
"""


def api_up(build_local: bool = False) -> dict[str, Any]:
    """Generate compose file and start Docker container.

    When *build_local* is True the image is built from the local Dockerfile
    instead of pulling the pre-built GHCR image.  Docker output is streamed
    to stderr in real-time so the user sees pull/build progress.
    """
    if not docker_available():
        return {"error": "Docker is not installed or not running."}

    try:
        compose_content = _generate_compose_yml(build_local=build_local)
    except FileNotFoundError as exc:
        return {"error": str(exc)}

    COMPOSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    COMPOSE_PATH.write_text(compose_content, encoding="utf-8")
    # Owner-only read/write — compose file may reference secret key names.
    COMPOSE_PATH.chmod(0o600)

    cmd = ["docker", "compose", "-f", str(COMPOSE_PATH), "up", "-d"]
    if build_local:
        cmd.append("--build")

    try:
        result = subprocess.run(cmd, timeout=300)
    except subprocess.TimeoutExpired:
        return {"error": "Docker compose up timed out after 300 seconds."}
    if result.returncode != 0:
        return {"error": "docker compose up failed"}

    return {"status": "started", "compose_path": str(COMPOSE_PATH)}


def api_down() -> dict[str, Any]:
    """Stop Docker container. Reports whether it was actually running."""
    if not COMPOSE_PATH.exists():
        return {"status": "not_running", "message": "No compose file found."}

    was_running = is_container_running()

    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_PATH), "down"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        return {"error": result.stderr.strip() or "docker compose down failed"}
    return {"status": "stopped", "was_running": was_running}


def is_container_running() -> bool:
    """Check if the Lerim Docker container API is reachable."""
    import urllib.request
    import urllib.error

    config = get_config()
    url = f"http://localhost:{config.server_port}/api/health"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


if __name__ == "__main__":
    health = api_health()
    assert health["status"] == "ok"
    assert "version" in health

    agents = detect_agents()
    assert isinstance(agents, dict)
    assert "claude" in agents

    docker_ok = docker_available()
    assert isinstance(docker_ok, bool)

    projects = api_project_list()
    assert isinstance(projects, list)

    print(f"api.py self-test passed: health={health}, docker={docker_ok}")
