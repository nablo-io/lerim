"""Shared API logic for CLI and HTTP endpoints.

Extracts the core business logic for answer, ingest, curate, and project
management so both the argparse CLI and the HTTP API call the same code.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from collections.abc import Generator

from lerim import __version__
from lerim.adapters.registry import (
    KNOWN_PLATFORMS,
    connect_platform,
    default_path_for,
    list_platforms,
)
from lerim.context import ContextStore, resolve_project_identity
from lerim.server.daemon import (
    LockBusyError,
    ServiceLock,
    WRITER_LOCK_NAME,
    resolve_window_bounds,
    run_curate_once,
    run_ingest_once,
)
from lerim.server.docker_runtime import (
    RUNTIME_IMAGE_ENV,
    RUNTIME_SOURCE_ENV,
    docker_available,
)
from lerim.config.settings import (
    Config,
    get_config,
    get_user_config_path,
    load_toml_file,
    remove_legacy_memory_dir,
    save_config_patch,
    _write_config_full,
)
from lerim.server.runtime import LerimRuntime
from lerim.sessions.catalog import (
    count_all_session_state,
    count_fts_indexed,
    count_indexed_sessions_for_project,
    count_session_jobs_by_status,
    count_unscoped_sessions_by_agent,
    latest_service_run,
    list_session_jobs,
    list_service_runs,
    list_queue_jobs,
    list_unscoped_sessions,
    queue_health_snapshot,
    reset_all_session_state,
    reset_indexed_sessions_for_project,
    retry_all_dead_letter_jobs,
    retry_session_job,
    skip_all_dead_letter_jobs,
    skip_session_job,
)


# ── Argument parsing helpers (inlined from arg_utils.py) ────────────


def parse_duration_to_seconds(raw: str) -> int:
    """Parse ``<number><unit>`` durations like ``30s`` or ``7d`` to seconds."""
    value = (raw or "").strip().lower()
    if len(value) < 2:
        raise ValueError(
            "duration must be <number><unit>, for example: 30s, 2m, 1h, 7d"
        )
    unit = value[-1]
    amount_text = value[:-1]
    if not amount_text.isdigit():
        raise ValueError(
            "duration must be <number><unit>, for example: 30s, 2m, 1h, 7d"
        )
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
    raise ValueError(
        "scope=project requires a project name when multiple projects are registered."
    )


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


def api_answer(
    question: str,
    *,
    scope: str = "all",
    project: str | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Run one answer query against the runtime agent and return result dict."""
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
    response, session_id, cost_usd, debug = agent.answer(
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
            project_ids=project_ids,
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
    except ValueError as exc:
        return {
            "error": True,
            "message": str(exc),
            "projects_used": [name for name, _ in selected_projects],
            "status_code": 400,
        }
    except sqlite3.Error:
        return {
            "error": True,
            "message": "Context query storage is unavailable.",
            "projects_used": [name for name, _ in selected_projects],
            "status_code": 503,
        }
    return {
        **payload,
        "error": False,
        "projects_used": [name for name, _ in selected_projects],
        "scope": normalized_scope,
    }


def api_memory_reset(
    *,
    project: str | None = None,
    all_projects: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Reset learned memory for one project or all projects, preserving setup."""
    if bool(all_projects) == bool(project):
        return {
            "error": True,
            "message": "Provide exactly one of --project or --all.",
        }

    config = get_config()
    store = _context_store(config)
    kept = ["config", "env", "platforms", "projects"]
    lock: ServiceLock | None = None
    if not dry_run:
        lock = ServiceLock(
            config.global_data_dir / "index" / WRITER_LOCK_NAME,
            stale_seconds=60,
        )
        try:
            lock.acquire("memory-reset", "lerim memory reset")
        except LockBusyError as exc:
            return {
                "error": True,
                "message": (
                    f"Cannot reset memory while ingest or curate is writing. {exc}"
                ),
            }

    try:
        if all_projects:
            context_counts = (
                store.count_all_memory() if dry_run else store.reset_all_memory()
            )
            session_counts = (
                count_all_session_state() if dry_run else reset_all_session_state()
            )
            cloud_state_path = config.global_data_dir / "cloud_shipper_state.json"
            cloud_state_count = 1 if cloud_state_path.exists() else 0
            if not dry_run and cloud_state_path.exists():
                cloud_state_path.unlink()
            return {
                "error": False,
                "scope": "all",
                "project": None,
                "dry_run": dry_run,
                "deleted": {
                    **context_counts,
                    **session_counts,
                    "cloud_shipper_state": cloud_state_count,
                },
                "kept": kept,
                "notes": [],
            }

        try:
            selected = _resolve_selected_projects(
                config=config,
                scope="project",
                project=project,
            )
        except ValueError as exc:
            return {"error": True, "message": str(exc)}
        if not selected:
            return {
                "error": True,
                "message": "No registered project matched reset scope.",
            }

        project_name, project_path = selected[0]
        identity = resolve_project_identity(project_path)
        context_counts = (
            store.count_project_memory(identity.project_id)
            if dry_run
            else store.reset_project_memory(identity.project_id)
        )
        session_counts = (
            count_indexed_sessions_for_project(str(project_path))
            if dry_run
            else reset_indexed_sessions_for_project(str(project_path))
        )
        return {
            "error": False,
            "scope": "project",
            "project": project_name,
            "project_path": str(project_path),
            "project_id": identity.project_id,
            "dry_run": dry_run,
            "deleted": {
                **context_counts,
                **session_counts,
                "cloud_shipper_state": 0,
            },
            "kept": kept,
            "notes": [
                "service_runs unchanged for project reset because service runs are global",
                "cloud_shipper_state unchanged for project reset because watermarks are global",
            ],
        }
    finally:
        if not dry_run:
            try:
                remove_legacy_memory_dir(config.global_data_dir)
            except Exception as exc:
                from lerim.config.logging import logger

                logger.warning("legacy memory cleanup failed: {}", exc)
        if lock is not None:
            lock.release()


def api_ingest(
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
    """Run one blocking ingest cycle and return a summary dict."""
    config = get_config()
    window_start, window_end = resolve_window_bounds(
        window=window or f"{config.ingest_window_days}d",
        since_raw=since,
        until_raw=until,
        parse_duration_to_seconds=parse_duration_to_seconds,
    )
    with ollama_lifecycle(config):
        code, summary = run_ingest_once(
            run_id=run_id,
            agent_filter=parse_agent_filter(agent) if agent else None,
            no_extract=no_extract,
            force=force,
            max_sessions=max_sessions or config.ingest_max_sessions,
            dry_run=dry_run,
            ignore_lock=ignore_lock,
            trigger="api",
            window_start=window_start,
            window_end=window_end,
        )
    queue_health = _safe_queue_health_snapshot()
    payload: dict[str, Any] = {
        "code": code,
        **asdict(summary),
        "queue_health": queue_health,
    }
    if queue_health.get("degraded"):
        payload["warning"] = "Queue degraded. " + str(
            queue_health.get("advice") or "Run `lerim queue --failed`."
        )
    return payload


def api_curate(dry_run: bool = False) -> dict[str, Any]:
    """Run one blocking context-curation cycle and return a result dict."""
    config = get_config()
    with ollama_lifecycle(config):
        code, payload = run_curate_once(dry_run=dry_run)
    queue_health = _safe_queue_health_snapshot()
    result: dict[str, Any] = {"code": code, **payload, "queue_health": queue_health}
    if queue_health.get("degraded"):
        result["warning"] = "Queue degraded. " + str(
            queue_health.get("advice") or "Run `lerim queue --failed`."
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


def _schedule_item(
    *,
    latest: dict[str, Any] | None,
    interval_minutes: int,
    now: datetime,
) -> dict[str, Any]:
    """Build public schedule metadata for one recurring daemon task."""
    interval_seconds = max(int(interval_minutes) * 60, 30)
    latest = latest or {}
    status = str(latest.get("status") or "").strip().lower()
    started_at = str(latest.get("started_at") or "").strip()
    completed_at = str(latest.get("completed_at") or "").strip()
    running = status == "started" and not completed_at

    anchor = _parse_iso_time(completed_at) or _parse_iso_time(started_at)
    next_due: datetime | None = None
    seconds_until: int | None = None
    if not running:
        next_due = (anchor + timedelta(seconds=interval_seconds)) if anchor else now
        seconds_until = max(0, int((next_due - now).total_seconds()))

    return {
        "interval_minutes": int(interval_minutes),
        "interval_seconds": interval_seconds,
        "running": running,
        "last_status": status or None,
        "last_started_at": started_at or None,
        "last_completed_at": completed_at or None,
        "next_due_at": next_due.isoformat() if next_due else None,
        "seconds_until_next": seconds_until,
    }


def _ingest_metrics_from_details(details: dict[str, Any]) -> dict[str, Any]:
    """Return the structured ingest metrics payload from service-run details."""
    ingest_metrics = details.get("ingest_metrics")
    return ingest_metrics if isinstance(ingest_metrics, dict) else {}


def _public_error_message(raw: Any) -> str:
    """Return a public-safe error marker without internal paths or provider details."""
    return "Error details hidden" if str(raw or "").strip() else ""


def _empty_queue_counts() -> dict[str, int]:
    """Return zero-filled queue counts for unavailable catalog reads."""
    return {
        "pending": 0,
        "running": 0,
        "done": 0,
        "failed": 0,
        "dead_letter": 0,
    }


def _catalog_unavailable_health(_exc: sqlite3.Error) -> dict[str, Any]:
    """Return degraded queue health when the session catalog cannot be read."""
    return {
        "degraded": True,
        "stale_running_count": 0,
        "dead_letter_count": 0,
        "oldest_running_age_seconds": None,
        "oldest_dead_letter_age_seconds": None,
        "advice": (
            "Session catalog is unavailable; stop Lerim and rebuild "
            "the session index."
        ),
        "error": "Session catalog storage is unavailable.",
    }


def _safe_queue_health_snapshot() -> dict[str, Any]:
    """Return queue health without letting catalog storage abort API responses."""
    try:
        return queue_health_snapshot()
    except sqlite3.Error as exc:
        return _catalog_unavailable_health(exc)


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

    raw_job_type = str(run.get("job_type") or "ingest").strip()
    op_type = raw_job_type if raw_job_type in {"curate", "context-brief"} else "ingest"
    base: dict[str, Any] = {
        "time": run.get("started_at"),
        "op_type": op_type,
        "status": str(run.get("status") or "unknown"),
        "duration_ms": _duration_ms_from_run(run),
        "projects": project_names,
        "project_label": project_label,
        "error": _public_error_message(details.get("error")),
    }

    if op_type == "curate":
        curate_metrics = (
            details.get("curate_metrics")
            if isinstance(details.get("curate_metrics"), dict)
            else {}
        )
        counts = (
            curate_metrics.get("counts")
            if isinstance(curate_metrics.get("counts"), dict)
            else {}
        )
        base.update(
            {
                "curate_counts": {
                    "created": int(counts.get("created") or 0),
                    "updated": int(counts.get("updated") or 0),
                    "archived": int(counts.get("archived") or 0),
                },
                "records_created": int(curate_metrics.get("records_created") or 0),
                "records_updated": int(curate_metrics.get("records_updated") or 0),
                "records_archived": int(curate_metrics.get("records_archived") or 0),
            }
        )
        return base

    ingest_metrics = _ingest_metrics_from_details(details)
    base.update(
        {
            "sessions_analyzed": int(ingest_metrics.get("sessions_analyzed") or 0),
            "sessions_extracted": int(ingest_metrics.get("sessions_extracted") or 0),
            "sessions_failed": int(ingest_metrics.get("sessions_failed") or 0),
            "sessions_skipped": int(ingest_metrics.get("sessions_skipped") or 0),
            "skipped_unscoped": int(ingest_metrics.get("skipped_unscoped") or 0),
            "records_created": int(ingest_metrics.get("records_created") or 0),
            "records_updated": int(ingest_metrics.get("records_updated") or 0),
            "records_archived": int(ingest_metrics.get("records_archived") or 0),
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
        and int(item.get("skipped_unscoped") or 0) == 0
        and int(item.get("records_created") or 0) == 0
        and int(item.get("records_updated") or 0) == 0
        and int(item.get("records_archived") or 0) == 0
        and not item.get("curate_counts")
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
        "error": _public_error_message(details.get("error")),
    }
    if str(normalized.get("op_type") or "") == "curate":
        details_payload["curate_counts"] = normalized.get("curate_counts") or {}
        details_payload["records_created"] = int(normalized.get("records_created") or 0)
        details_payload["records_updated"] = int(normalized.get("records_updated") or 0)
        details_payload["records_archived"] = int(
            normalized.get("records_archived") or 0
        )
    else:
        details_payload["sessions_analyzed"] = int(
            normalized.get("sessions_analyzed") or 0
        )
        details_payload["sessions_extracted"] = int(
            normalized.get("sessions_extracted") or 0
        )
        details_payload["sessions_failed"] = int(normalized.get("sessions_failed") or 0)
        details_payload["sessions_skipped"] = int(
            normalized.get("sessions_skipped") or 0
        )
        details_payload["records_created"] = int(normalized.get("records_created") or 0)
        details_payload["records_updated"] = int(normalized.get("records_updated") or 0)
        details_payload["records_archived"] = int(
            normalized.get("records_archived") or 0
        )
        details_payload["skipped_unscoped"] = int(
            normalized.get("skipped_unscoped") or 0
        )
    return {
        "id": run.get("id"),
        "job_type": normalized.get("op_type"),
        "status": run.get("status"),
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "trigger": run.get("trigger"),
        "details": details_payload,
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
        project_name = repo_to_name.get(repo_path) or (
            Path(repo_path).name if repo_path else "global"
        )
        row = grouped.setdefault(
            project_name,
            {
                "time": now.isoformat(),
                "op_type": "ingest",
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


def _public_platforms(platforms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return platform metadata without local agent paths."""
    output: list[dict[str, Any]] = []
    for item in platforms:
        public_item: dict[str, Any] = {
            "name": str(item.get("name") or ""),
            "connected_at": item.get("connected_at") or "",
            "session_count": int(item.get("session_count") or 0),
            "exists": bool(item.get("exists")),
        }
        status = str(item.get("status") or "").strip()
        if status:
            public_item["status"] = status
        validation = item.get("validation")
        if isinstance(validation, dict):
            public_item["validation"] = {"ok": bool(validation.get("ok"))}
        output.append(public_item)
    return output


def _runtime_identity() -> dict[str, Any]:
    """Return runtime build identity surfaced by Docker or direct execution."""
    source = os.environ.get(RUNTIME_SOURCE_ENV) or "direct"
    identity: dict[str, Any] = {
        "version": __version__,
        "source": source,
    }
    image = os.environ.get(RUNTIME_IMAGE_ENV)
    if image:
        identity["image"] = image
    return identity


def api_status(
    *,
    scope: str = "all",
    project: str | None = None,
) -> dict[str, Any]:
    """Return runtime status summary."""
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
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "runtime": _runtime_identity(),
            "error": str(exc),
            "projects": [],
            "scope": {
                "strict_project_only": True,
                "mode": normalized_scope,
                "skipped_unscoped": 0,
            },
        }

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
                "project_id": resolve_project_identity(path).project_id,
                "record_count": record_count,
                "indexed_sessions_count": indexed_sessions_count,
                "latest_session_start_time": latest_session_start_time,
                "queue": queue_counts,
                "oldest_blocked_run_id": blocked_run_id,
                "last_error": _public_error_message(last_error),
            }
        )
    if not projects_payload and normalized_scope == "all":
        total_records = 0

    now = datetime.now(timezone.utc)
    catalog_error: sqlite3.Error | None = None
    try:
        latest_ingest_raw = latest_service_run("ingest")
        latest_curate_raw = latest_service_run("curate")
        queue = count_session_jobs_by_status()
        queue_health = queue_health_snapshot()
        sessions_indexed_count = count_fts_indexed()
        unscoped_by_agent = count_unscoped_sessions_by_agent(projects=config.projects)
    except sqlite3.Error as exc:
        catalog_error = exc
        latest_ingest_raw = None
        latest_curate_raw = None
        queue = _empty_queue_counts()
        queue_health = _catalog_unavailable_health(exc)
        sessions_indexed_count = 0
        unscoped_by_agent = {}

    latest_ingest_details = (latest_ingest_raw or {}).get("details") or {}
    latest_ingest_metrics = (
        _ingest_metrics_from_details(latest_ingest_details)
        if isinstance(latest_ingest_details, dict)
        else {}
    )

    selected_project_names = {name for name, _ in selected_projects}

    platforms = _public_platforms(list_platforms(config.platforms_path))
    if catalog_error is None:
        try:
            recent_activity = (
                _running_activity_rows(selected_projects=selected_projects)
                + _recent_activity(
                    limit=12,
                    allowed_projects=selected_project_names
                    if normalized_scope == "project"
                    else None,
                )
            )[:12]
        except sqlite3.Error as exc:
            catalog_error = exc
            queue_health = _catalog_unavailable_health(exc)
            recent_activity = []
    else:
        recent_activity = []

    latest_ingest = _normalize_latest_run(latest_ingest_raw)

    payload: dict[str, Any] = {
        "timestamp": now.isoformat(),
        "runtime": _runtime_identity(),
        "connected_agents": [
            str(item.get("name") or "") for item in platforms if item.get("name")
        ],
        "platforms": platforms,
        "record_count": total_records,
        "sessions_indexed_count": sessions_indexed_count,
        "queue": queue,
        "queue_health": queue_health,
        "session_catalog": {
            "status": "unavailable" if catalog_error else "available",
            "error": "Session catalog storage is unavailable."
            if catalog_error
            else "",
        },
        "projects": projects_payload,
        "ingest_window_days": config.ingest_window_days,
        "schedule": {
            "ingest": _schedule_item(
                latest=latest_ingest_raw,
                interval_minutes=config.ingest_interval_minutes,
                now=now,
            ),
            "curate": _schedule_item(
                latest=latest_curate_raw,
                interval_minutes=config.curate_interval_minutes,
                now=now,
            ),
        },
        "unscoped_sessions": {
            "total": sum(unscoped_by_agent.values()),
            "by_agent": unscoped_by_agent,
        },
        "scope": {
            "strict_project_only": True,
            "mode": normalized_scope,
            "skipped_unscoped": int(
                latest_ingest_metrics.get("skipped_unscoped") or 0
            ),
        },
        "latest_ingest": latest_ingest,
        "latest_curate": _normalize_latest_run(latest_curate_raw),
        "recent_activity": recent_activity,
    }
    return payload


def api_connect_list() -> list[dict[str, Any]]:
    """Return list of connected platforms."""
    config = get_config()
    return _public_platforms(list_platforms(config.platforms_path))


def api_connect(platform: str, path: str | None = None) -> dict[str, Any]:
    """Connect a platform and return result."""
    config = get_config()
    result = connect_platform(config.platforms_path, platform, custom_path=path)
    public = _public_platforms([result])
    return public[0] if public else {"name": platform, "status": "unknown_platform"}


# ── Job queue management ─────────────────────────────────────────────


def api_retry_job(run_id: str) -> dict[str, Any]:
    """Retry a dead_letter job, returning result."""
    return _queue_action_result(
        run_id=run_id,
        result_key="retried",
        mutate=retry_session_job,
    )


def api_skip_job(run_id: str) -> dict[str, Any]:
    """Skip a dead_letter job, returning result."""
    return _queue_action_result(
        run_id=run_id,
        result_key="skipped",
        mutate=skip_session_job,
    )


def api_retry_all_dead_letter() -> dict[str, Any]:
    """Retry all dead_letter jobs across all projects."""
    return _queue_bulk_action_result(
        result_key="retried",
        mutate_all=retry_all_dead_letter_jobs,
    )


def api_skip_all_dead_letter() -> dict[str, Any]:
    """Skip all dead_letter jobs across all projects."""
    return _queue_bulk_action_result(
        result_key="skipped",
        mutate_all=skip_all_dead_letter_jobs,
    )


def _queue_action_result(
    *,
    run_id: str,
    result_key: str,
    mutate: Any,
) -> dict[str, Any]:
    """Apply one queue mutation and return the common action payload."""
    ok = mutate(run_id)
    return {result_key: ok, "run_id": run_id, "queue": count_session_jobs_by_status()}


def _queue_bulk_action_result(*, result_key: str, mutate_all: Any) -> dict[str, Any]:
    """Apply one queue mutation to every dead-letter job."""
    changed = int(mutate_all())
    return {result_key: changed, "queue": count_session_jobs_by_status()}


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


def api_project_list(*, include_paths: bool = True) -> list[dict[str, Any]]:
    """Return registered projects from config."""
    config = get_config()
    result: list[dict[str, Any]] = []
    for name, path_str in config.projects.items():
        resolved = Path(path_str).expanduser().resolve()
        item: dict[str, Any] = {
            "name": name,
            "project_id": resolve_project_identity(resolved).project_id,
            "exists": resolved.exists(),
        }
        if include_paths:
            item["path"] = str(resolved)
        result.append(item)
    return result


def _project_config_name(resolved: Path, existing_projects: dict[str, str]) -> str:
    """Return a deterministic config key for one registered project path."""
    base_name = resolved.name
    current = existing_projects.get(base_name)
    if current is None or Path(current).expanduser().resolve() == resolved:
        return base_name
    suffix = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:6]
    return f"{base_name}-{suffix}"


def api_project_add(path_str: str, *, include_paths: bool = True) -> dict[str, Any]:
    """Register a project directory and return status."""
    resolved = Path(path_str).expanduser().resolve()
    if not resolved.is_dir():
        message = f"Not a directory: {resolved}" if include_paths else "Not a directory"
        return {"error": message, "name": None}

    config = get_config()
    name = _project_config_name(resolved, config.projects or {})
    # Update config
    save_config_patch({"projects": {name: str(resolved)}})
    config = get_config()
    store = _context_store(config)
    identity = resolve_project_identity(resolved)
    store.register_project(identity)

    payload: dict[str, Any] = {
        "name": name,
        "project_id": identity.project_id,
    }
    if include_paths:
        payload["path"] = str(resolved)
        payload["context_db_path"] = str(config.context_db_path)
    return payload


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
    for name in KNOWN_PLATFORMS:
        resolved = default_path_for(name)
        result[name] = {
            "path": str(resolved) if resolved else "",
            "exists": resolved.exists() if resolved else False,
        }
    return result


def write_init_config(selected_agents: dict[str, str]) -> Path:
    """Write initial [agents] config and return the config file path."""
    save_config_patch({"agents": selected_agents})
    return get_user_config_path()


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
