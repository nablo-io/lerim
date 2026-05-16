"""HTTP server: JSON APIs for sessions, pipeline, queue, and config.

Optional bundled static files under ``dashboard/`` (repo or ``/opt/lerim/dashboard``)
may serve a local UI. If no ``index.html`` is present, GET ``/`` returns a
minimal page pointing to Lerim Cloud; the web app itself lives in ``lerim-cloud``.
"""

from __future__ import annotations

import json
import mimetypes
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from lerim.config.logging import logger
from lerim.server.api import (
    api_answer,
    api_connect,
    api_connect_list,
    api_curate,
    api_health,
    api_ingest,
    api_project_add,
    api_project_list,
    api_query,
    api_project_remove,
    api_queue_jobs,
    api_retry_all_dead_letter,
    api_retry_job,
    api_skip_all_dead_letter,
    api_skip_job,
    api_status,
    api_unscoped,
)
from lerim.config.settings import (
    Config,
    get_config,
    save_config_patch,
)

from lerim.adapters.common import load_jsonl_dict_lines
from lerim.config.providers import list_provider_models
from lerim.server.dashboard_data import build_extract_report, extract_session_details
from lerim.sessions.catalog import (
    count_session_jobs_by_status,
    fetch_session_doc,
    init_sessions_db,
    latest_service_run,
    list_session_jobs,
    list_sessions_window,
)


_REPO_DASHBOARD = Path(__file__).resolve().parents[3] / "dashboard"
DASHBOARD_DIR = _REPO_DASHBOARD if _REPO_DASHBOARD.is_dir() else Path("/opt/lerim/dashboard")
# Public web UI is hosted separately (lerim-cloud); this is the marketing/docs entry.
_LERIM_CLOUD_UI_URL = "https://lerim.dev"
MAX_BODY_BYTES = 1_000_000
READ_ONLY_MESSAGE = "Dashboard is read-only. Use CLI commands for write actions."
_REPORT_CACHE: dict[str, Any] = {"at": None, "value": None}


def _iso_now() -> str:
	"""Return current UTC time as ISO string."""
	return datetime.now(timezone.utc).isoformat()


def _query_param(query: dict[str, list[str]], key: str, default: str = "") -> str:
	"""Extract a single query parameter value."""
	return (query.get(key) or [default])[0]


def _parse_int(
    raw: str | None, default: int, *, minimum: int = 0, maximum: int = 10_000
) -> int:
    """Parse integer query/body parameter and clamp to bounds."""
    try:
        value = int(raw or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _scope_bounds(scope: str | None) -> tuple[datetime | None, datetime]:
    """Resolve dashboard scope token to time window bounds."""
    now = datetime.now(timezone.utc)
    normalized = (scope or "week").strip().lower()
    if normalized == "today":
        return now - timedelta(days=1), now
    if normalized == "week":
        return now - timedelta(days=7), now
    if normalized == "month":
        return now - timedelta(days=30), now
    if normalized == "all":
        return None, now
    return now - timedelta(days=7), now


def _sqlite_rows(
    since: datetime | None,
    until: datetime,
    agent_type: str | None,
) -> list[sqlite3.Row]:
    """Load session rows for dashboard statistics queries."""
    config = get_config()
    init_sessions_db()
    where: list[str] = []
    params: list[Any] = []
    if since is not None:
        where.append("(start_time >= ? OR start_time IS NULL)")
        params.append(since.isoformat())
    where.append("(start_time <= ? OR start_time IS NULL)")
    params.append(until.isoformat())
    if agent_type and agent_type != "all":
        where.append("agent_type = ?")
        params.append(agent_type)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    sql = f"""\
SELECT run_id, agent_type, repo_name, start_time, status, duration_ms, message_count, \
tool_call_count, error_count, total_tokens, summary_text, session_path \
FROM session_docs {where_sql} ORDER BY start_time DESC, indexed_at DESC"""
    with sqlite3.connect(config.sessions_db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, params).fetchall()


def _serialize_run(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a DB row to dashboard run JSON payload shape."""
    started = row.get("start_time")
    repo_name = row.get("repo_name") or ""
    session_path = str(row.get("session_path") or "")
    # Extract project name from repo_path (folder path) or session_path
    repo_path = str(row.get("repo_path") or "")
    project = ""
    if repo_path:
        project = Path(repo_path).name
    elif session_path:
        # Claude paths: ~/.claude/projects/-Users-...-project/uuid.jsonl
        sp = Path(session_path)
        if "projects" in sp.parts:
            idx = sp.parts.index("projects")
            if idx + 1 < len(sp.parts):
                encoded = sp.parts[idx + 1]
                project = encoded.rsplit("-", 1)[-1] if "-" in encoded else encoded
    # Build display label: prefer branch/project over raw path
    branch_display = repo_name
    if (
        branch_display
        and "/" in branch_display
        and not branch_display.startswith(("feat/", "fix/", "main", "master", "dev"))
    ):
        # Looks like a full path, extract last component
        branch_display = Path(branch_display).name
    run_id = row.get("run_id") or ""
    short_id = str(run_id)[:8] if run_id else ""
    return {
        "run_id": run_id,
        "agent_type": row.get("agent_type") or "unknown",
        "status": row.get("status") or "completed",
        "started_at": started,
        "duration_ms": int(row.get("duration_ms") or 0),
        "message_count": int(row.get("message_count") or 0),
        "tool_call_count": int(row.get("tool_call_count") or 0),
        "error_count": int(row.get("error_count") or 0),
        "total_tokens": int(row.get("total_tokens") or 0),
        "repo_name": repo_name,
        "project": project,
        "branch_display": branch_display or project or short_id,
        "short_id": short_id,
        "session_path": session_path,
        "snippet": row.get("summary_text") or "",
        "preview": row.get("summary_text") or "",
        "source": "trace",
    }


def _compute_stats(rows: list[sqlite3.Row]) -> dict[str, Any]:
    """Aggregate dashboard metrics across session rows."""
    totals = {
        "runs": len(rows),
        "messages": 0,
        "tool_calls": 0,
        "errors": 0,
        "tokens": 0,
        "duration_ms": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "runs_with_errors": 0,
        "unique_tools": 0,
    }
    by_agent: dict[str, dict[str, int]] = {}
    daily: dict[str, dict[str, int]] = {}
    hourly: dict[int, dict[str, int]] = {}
    for row in rows:
        agent = str(row["agent_type"] or "unknown")
        start_time = str(row["start_time"] or "")
        messages = int(row["message_count"] or 0)
        tools = int(row["tool_call_count"] or 0)
        errors = int(row["error_count"] or 0)
        tokens = int(row["total_tokens"] or 0)
        duration = int(row["duration_ms"] or 0)

        totals["messages"] += messages
        totals["tool_calls"] += tools
        totals["errors"] += errors
        totals["tokens"] += tokens
        totals["duration_ms"] += duration
        if errors > 0 or str(row["status"] or "").strip().lower() == "error":
            totals["runs_with_errors"] += 1

        agent_stats = by_agent.setdefault(
            agent, {"runs": 0, "messages": 0, "tool_calls": 0, "tokens": 0}
        )
        agent_stats["runs"] += 1
        agent_stats["messages"] += messages
        agent_stats["tool_calls"] += tools
        agent_stats["tokens"] += tokens

        try:
            dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        except ValueError:
            continue
        day_key = dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
        hour_key = int(dt.astimezone(timezone.utc).hour)
        bucket = daily.setdefault(
            day_key, {"messages": 0, "tool_calls": 0, "tokens": 0}
        )
        bucket[agent] = bucket.get(agent, 0) + 1
        bucket["messages"] += messages
        bucket["tool_calls"] += tools
        bucket["tokens"] += tokens
        hour_bucket = hourly.setdefault(
            hour_key, {"sessions": 0, "messages": 0, "tool_calls": 0, "tokens": 0}
        )
        hour_bucket["sessions"] += 1
        hour_bucket["messages"] += messages
        hour_bucket["tool_calls"] += tools
        hour_bucket["tokens"] += tokens

    # Extract model and tool usage from JSONL traces (cached per session)
    model_usage: dict[str, dict[str, int]] = {}
    tool_usage: dict[str, int] = {}
    max_detail_sessions = 200
    for row in rows[:max_detail_sessions]:
        try:
            session_path = str(row["session_path"] or "").strip()
        except (KeyError, IndexError):
            continue
        if not session_path:
            continue
        details = extract_session_details(session_path)
        model_name = str(details.get("model") or "").strip()
        if model_name:
            bucket = model_usage.setdefault(
                model_name, {"input": 0, "output": 0, "total": 0}
            )
            tokens = int(row["total_tokens"] or 0)
            bucket["total"] += tokens
            bucket["input"] += tokens // 2  # approximate split
            bucket["output"] += tokens - (tokens // 2)
        for tool_name, count in details.get("tools", {}).items():
            tool_usage[tool_name] = tool_usage.get(tool_name, 0) + count

    totals["unique_tools"] = len(tool_usage)
    if totals["tokens"] > 0:
        totals["input_tokens"] = totals["tokens"] // 2
        totals["output_tokens"] = totals["tokens"] - totals["input_tokens"]

    runs = totals["runs"] or 1
    duration_data_available = totals["duration_ms"] > 0
    run_error_rate = round((totals["runs_with_errors"] / runs) * 100, 2)
    avg_messages = round(totals["messages"] / runs, 2)
    avg_tool_calls = round(totals["tool_calls"] / runs, 2)
    avg_duration = round(totals["duration_ms"] / runs, 2)
    derived = {
        "avg_messages_per_session": avg_messages,
        "avg_tool_calls_per_session": avg_tool_calls,
        "avg_session_duration_ms": avg_duration,
        "error_rate": run_error_rate,
        "duration_data_available": duration_data_available,
    }
    daily_activity = []
    for day in sorted(daily.keys()):
        item = {"date": day, **daily[day]}
        daily_activity.append(item)
    hourly_activity = []
    for hour in sorted(hourly.keys()):
        hourly_activity.append({"hour": hour, **hourly[hour]})
    return {
        "totals": totals,
        "derived": derived,
        "by_agent": by_agent,
        "model_usage": model_usage,
        "tool_usage": tool_usage,
        "daily_activity": daily_activity,
        "hourly_activity": hourly_activity,
        "cache": {
            "cached_at": _iso_now(),
            "age_seconds": 0,
            "stale": False,
            "source": "live",
        },
    }


def _load_messages_for_run(run_doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Load normalized message list from source trace path in a run document."""
    session_path = str(run_doc.get("session_path") or "").strip()
    if not session_path:
        return []
    rows = load_jsonl_dict_lines(Path(session_path).expanduser())
    output: list[dict[str, Any]] = []
    for row in rows:
        content = row.get("content")
        if content is None and isinstance(row.get("message"), dict):
            content = row.get("message", {}).get("content")
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=True)
        output.append(
            {
                "role": row.get("role") or "assistant",
                "content": str(content or ""),
                "timestamp": row.get("timestamp"),
                "tool_name": row.get("tool_name"),
                "tool_input": row.get("tool_input"),
                "tool_output": row.get("tool_output"),
            }
        )
    return output


def _serialize_full_config(config: Config) -> dict[str, Any]:
    """Serialize full Config dataclass to a dashboard-friendly JSON dict."""

    def _role_dict(role: Any) -> dict[str, Any]:
        """Convert RoleConfig to a dict."""
        return {
            "provider": role.provider,
            "model": role.model,
        }

    return {
        "server": {
            "host": config.server_host,
            "port": config.server_port,
            "ingest_interval_minutes": config.ingest_interval_minutes,
            "curate_interval_minutes": config.curate_interval_minutes,
            "ingest_window_days": config.ingest_window_days,
            "ingest_max_sessions": config.ingest_max_sessions,
        },
        "embedding": {
            "model_id": config.embedding_model_id,
            "semantic_shortlist_size": config.semantic_shortlist_size,
            "lexical_shortlist_size": config.lexical_shortlist_size,
        },
        "roles": {
            "agent": _role_dict(config.agent_role),
        },
        "mlflow_enabled": config.mlflow_enabled,
        "auto_unload": config.auto_unload,
        "cloud_authenticated": config.cloud_token is not None,
        "connections": {
            "agents": sorted(config.agents),
            "projects": sorted(config.projects),
        },
    }


def _save_config_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Apply config patch to user config TOML file and return updated config."""
    config = save_config_patch(patch)
    return _serialize_full_config(config)


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler: JSON API routes and optional static files; stub root if no UI."""

    server_version = "LerimDashboard/0.1"

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
        """Route request logs through project logger."""
        logger.debug("dashboard | " + fmt, *args)

    def _json(self, payload: dict | list, status: int = HTTPStatus.OK) -> None:
        """Write JSON response with status code."""
        body = json.dumps(payload, ensure_ascii=True, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        """Write standard JSON error payload."""
        self._json({"error": message}, status=status)

    def _read_json_body(self) -> dict[str, Any]:
        """Read and parse request body as a JSON object."""
        raw_len = self.headers.get("Content-Length")
        size = _parse_int(raw_len, 0, minimum=0, maximum=MAX_BODY_BYTES)
        if size <= 0:
            return {}
        body = self.rfile.read(size)
        if not body:
            return {}
        try:
            parsed = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON body") from exc
        if not isinstance(parsed, dict):
            raise ValueError("JSON body must be an object")
        return parsed

    def _serve_cloud_stub_html(self) -> None:
        """Serve minimal HTML when no bundled dashboard assets exist."""
        body = (
            "<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"/>"
            f"<title>Lerim</title></head><body><p>Lerim API is running on this host.</p>"
            f"<p>The web UI is on <a href=\"{_LERIM_CLOUD_UI_URL}\">Lerim Cloud</a>.</p>"
            "<p><a href=\"/api/health\">GET /api/health</a></p></body></html>\n"
        ).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, relative_path: str) -> None:
        """Serve static dashboard asset with directory traversal protection."""
        path = (DASHBOARD_DIR / relative_path.lstrip("/")).resolve()
        if (
            not str(path).startswith(str(DASHBOARD_DIR.resolve()))
            or not path.exists()
            or not path.is_file()
        ):
            self._error(HTTPStatus.NOT_FOUND, "Not found")
            return
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        raw = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _api_runs_stats(self, query: dict[str, list[str]]) -> None:
        """Return aggregate run stats for selected scope and agent filter."""
        scope = _query_param(query, "scope", "week")
        agent = _query_param(query, "agent_type", "all")
        since, until = _scope_bounds(scope)
        rows = _sqlite_rows(since, until, agent)
        self._json(_compute_stats(rows))

    def _api_runs(self, query: dict[str, list[str]]) -> None:
        """Return paginated run list for selected scope and agent filter."""
        scope = _query_param(query, "scope", "week")
        agent = _query_param(query, "agent_type", "all")
        limit = _parse_int(
            _query_param(query, "limit", "30"), 30, minimum=1, maximum=200
        )
        offset = _parse_int(
            _query_param(query, "offset", "0"), 0, minimum=0, maximum=10_000
        )
        since, until = _scope_bounds(scope)
        rows, total = list_sessions_window(
            limit=limit,
            offset=offset,
            agent_types=None if agent in {"", "all"} else [agent],
            since=since,
            until=until,
        )
        runs = [_serialize_run(row) for row in rows]
        self._json(
            {
                "runs": runs,
                "pagination": {
                    "offset": offset,
                    "total": total,
                    "has_more": (offset + len(runs)) < total,
                },
            }
        )

    def _api_search(self, query: dict[str, list[str]]) -> None:
        """Run FTS/keyword session search with optional filters and pagination."""
        config = get_config()
        run_query = _query_param(query, "query").strip()
        scope = _query_param(query, "scope", "week")
        agent = _query_param(query, "agent_type", "all")
        status_filter = _query_param(query, "status").strip()
        repo_filter = _query_param(query, "repo").strip()
        limit = _parse_int(
            _query_param(query, "limit", "30"), 30, minimum=1, maximum=200
        )
        offset = _parse_int(
            _query_param(query, "offset", "0"), 0, minimum=0, maximum=10_000
        )
        since, until = _scope_bounds(scope)
        where = []
        params: list[Any] = []
        if since is not None:
            where.append("(d.start_time >= ? OR d.start_time IS NULL)")
            params.append(since.isoformat())
        where.append("(d.start_time <= ? OR d.start_time IS NULL)")
        params.append(until.isoformat())
        if agent and agent != "all":
            where.append("d.agent_type = ?")
            params.append(agent)
        if status_filter:
            where.append("d.status = ?")
            params.append(status_filter)
        if repo_filter:
            where.append("d.repo_name LIKE ?")
            params.append(f"%{repo_filter}%")
        where_sql = (" AND " + " AND ".join(where)) if where else ""
        with sqlite3.connect(config.sessions_db_path) as conn:
            conn.row_factory = sqlite3.Row
            if run_query:
                search_sql = f"""\
SELECT d.run_id, d.agent_type, d.status, d.start_time, d.duration_ms, d.message_count, \
d.tool_call_count, d.error_count, d.total_tokens, d.repo_name, d.summary_text, \
snippet(sessions_fts, 3, '<mark>', '</mark>', '...', 24) AS snippet \
FROM sessions_fts JOIN session_docs d ON d.id = sessions_fts.rowid \
WHERE sessions_fts MATCH ?{where_sql} ORDER BY d.start_time DESC LIMIT ? OFFSET ?"""
                rows = conn.execute(
                    search_sql, [run_query, *params, limit, offset]
                ).fetchall()
                count_sql = f"""\
SELECT COUNT(1) AS total FROM sessions_fts JOIN session_docs d ON d.id = sessions_fts.rowid \
WHERE sessions_fts MATCH ?{where_sql}"""
                total = int(
                    conn.execute(count_sql, [run_query, *params]).fetchone()["total"]
                    or 0
                )
            else:
                search_sql = f"""\
SELECT d.run_id, d.agent_type, d.status, d.start_time, d.duration_ms, d.message_count, \
d.tool_call_count, d.error_count, d.total_tokens, d.repo_name, d.summary_text, d.summary_text AS snippet \
FROM session_docs d WHERE 1=1{where_sql} ORDER BY d.start_time DESC LIMIT ? OFFSET ?"""
                rows = conn.execute(search_sql, [*params, limit, offset]).fetchall()
                count_sql = f"""\
SELECT COUNT(1) AS total FROM session_docs d WHERE 1=1{where_sql}"""
                total = int(conn.execute(count_sql, params).fetchone()["total"] or 0)
        results = []
        for row in rows:
            run = _serialize_run(dict(row))
            run["snippet"] = row["snippet"] or run.get("snippet") or ""
            results.append(run)
        self._json(
            {
                "mode": "fts" if run_query else "keyword",
                "results": results,
                "pagination": {
                    "offset": offset,
                    "total": total,
                    "has_more": (offset + len(results)) < total,
                },
            }
        )

    def _api_run_messages(self, path: str) -> None:
        """Return normalized message timeline for one run id."""
        run_id = unquote(path.split("/api/runs/", 1)[1].rsplit("/messages", 1)[0])
        run_doc = fetch_session_doc(run_id)
        if run_doc is None:
            self._error(HTTPStatus.NOT_FOUND, "Run not found")
            return
        messages = _load_messages_for_run(run_doc)
        self._json({"messages": messages})

    def _api_refine_status(self) -> None:
        """Return queue and recent run status for refine panel."""
        payload = {
            "queue": count_session_jobs_by_status(),
            "ingest": latest_service_run("ingest"),
            "curate": latest_service_run("curate"),
        }
        self._json(payload)

    def _api_live(self) -> None:
        """Lightweight live-status endpoint for frequent polling.

        Designed for 2-3 second poll intervals from the cloud dashboard.
        Avoids heavy queries: uses count aggregates and thread enumeration.
        """
        now = datetime.now(timezone.utc)

        # -- Active background threads (ingest-* / curate-*) --
        ingest_threads: list[str] = []
        curate_threads: list[str] = []
        for t in threading.enumerate():
            name = t.name or ""
            if name.startswith("ingest-"):
                ingest_threads.append(name)
            elif name.startswith("curate-"):
                curate_threads.append(name)

        # -- Queue counts (single lightweight GROUP BY) --
        queue = count_session_jobs_by_status()

        # -- Running job run_ids (small result set) --
        running_jobs = list_session_jobs(limit=50, status="running")
        running_run_ids = [
            str(j.get("run_id") or "") for j in running_jobs if j.get("run_id")
        ]

        # -- Latest ingest / curate service runs --
        last_ingest_raw = latest_service_run("ingest")
        last_curate_raw = latest_service_run("curate")

        def _format_service_run(run: dict[str, Any] | None) -> dict[str, Any] | None:
            if run is None:
                return None
            details = run.get("details") or {}
            started = run.get("started_at")
            completed = run.get("completed_at")
            duration_ms: int | None = None
            if started and completed:
                try:
                    t0 = datetime.fromisoformat(started)
                    t1 = datetime.fromisoformat(completed)
                    duration_ms = int((t1 - t0).total_seconds() * 1000)
                except (ValueError, TypeError):
                    pass
            return {
                "status": run.get("status"),
                "started_at": started,
                "completed_at": completed,
                "duration_ms": duration_ms,
                "sessions_processed": details.get("sessions_processed")
                    or details.get("indexed")
                    or details.get("enqueued"),
            }

        payload = {
            "timestamp": now.isoformat(),
            "ingest_active": len(ingest_threads) > 0,
            "ingest_threads": ingest_threads,
            "curate_active": len(curate_threads) > 0,
            "curate_threads": curate_threads,
            "ingest_sessions_processing": queue.get("running", 0),
            "queue": queue,
            "running_run_ids": running_run_ids,
            "last_ingest": _format_service_run(last_ingest_raw),
            "last_curate": _format_service_run(last_curate_raw),
        }
        self._json(payload)

    def _api_refine_report(self) -> None:
        """Return cached or freshly built extraction quality report."""
        now = datetime.now(timezone.utc)
        cached_at = _REPORT_CACHE.get("at")
        if (
            isinstance(cached_at, datetime)
            and _REPORT_CACHE.get("value")
            and (now - cached_at).total_seconds() < 60
        ):
            self._json(_REPORT_CACHE["value"])
            return
        try:
            report = build_extract_report()
        except Exception as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"Report unavailable: {exc}")
            return
        _REPORT_CACHE["at"] = now
        _REPORT_CACHE["value"] = report
        self._json(report)

    def _api_config(self) -> None:
        """Return full effective runtime config used by dashboard."""
        config = get_config()
        payload = {
            "effective": _serialize_full_config(config),
            "read_only": False,
        }
        self._json(payload)

    def _api_config_models(self, query: dict[str, list[str]]) -> None:
        """Return available model list for the selected provider."""
        config = get_config()
        provider = _query_param(query, "provider", config.agent_role.provider)
        models = sorted(set(list_provider_models(provider)))
        self._json({"models": models})

    def _handle_api_get(self, path: str, query: dict[str, list[str]]) -> None:
        """Dispatch GET API routes to the matching handler."""
        query_handlers = {
            "/api/runs/stats": self._api_runs_stats,
            "/api/runs": self._api_runs,
            "/api/search": self._api_search,
            "/api/config/models": self._api_config_models,
        }
        if path == "/api/jobs/queue":
            status_f = _query_param(query, "status") or None
            project_f = _query_param(query, "project") or None
            try:
                self._json(api_queue_jobs(status=status_f, project=project_f))
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if path == "/api/status":
            scope = _query_param(query, "scope", "all")
            project = _query_param(query, "project") or None
            try:
                if scope == "all" and not project:
                    self._json(api_status())
                else:
                    self._json(api_status(scope=scope, project=project))
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if path == "/api/unscoped":
            raw_limit = _query_param(query, "limit", "50")
            try:
                limit = max(1, int(raw_limit))
            except ValueError:
                self._error(HTTPStatus.BAD_REQUEST, "limit must be an integer")
                return
            self._json(api_unscoped(limit=limit))
            return
        no_query_handlers = {
            "/api/health": lambda: self._json(api_health()),
            "/api/live": self._api_live,
            "/api/connect": lambda: self._json({"platforms": api_connect_list()}),
            "/api/project/list": lambda: self._json(
                {"projects": api_project_list(include_paths=False)}
            ),
            "/api/refine/status": self._api_refine_status,
            "/api/refine/report": self._api_refine_report,
            "/api/config": self._api_config,
        }
        if path in query_handlers:
            query_handlers[path](query)
            return
        if path in no_query_handlers:
            no_query_handlers[path]()
            return
        if path.startswith("/api/runs/") and path.endswith("/messages"):
            self._api_run_messages(path)
            return
        self._error(HTTPStatus.NOT_FOUND, "Not found")

    def _api_config_save(self) -> None:
        """Save config patch to user config TOML file."""
        try:
            body = self._read_json_body()
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        patch = body.get("patch")
        if not isinstance(patch, dict) or not patch:
            self._error(HTTPStatus.BAD_REQUEST, "Missing 'patch' object in body")
            return
        try:
            updated = _save_config_patch(patch)
            self._json(
                {
                    "effective": updated,
                }
            )
        except Exception as exc:
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"Failed to save config: {exc}",
            )

    def _handle_api_post(self, path: str) -> None:
        """Dispatch POST API routes to supported actions."""
        def read_body() -> dict[str, Any] | None:
            try:
                return self._read_json_body()
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
                return None

        if path == "/api/answer":
            body = read_body()
            if body is None:
                return
            question = str(body.get("question") or "").strip()
            if not question:
                self._error(HTTPStatus.BAD_REQUEST, "Missing 'question'")
                return
            allowed = {"question", "scope", "project", "verbose"}
            unknown = sorted(key for key in body if key not in allowed)
            if unknown:
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    f"Unsupported field(s): {', '.join(unknown)}",
                )
                return
            result_holder: list[dict[str, Any]] = []
            scope = str(body.get("scope") or "all")
            project = body.get("project")
            project_name = str(project).strip() if isinstance(project, str) else None
            verbose = bool(body.get("verbose"))

            def _run_context_answerer() -> None:
                """Execute answer in background thread."""
                if scope == "all" and not project_name:
                    result_holder.append(api_answer(question, verbose=verbose))
                else:
                    result_holder.append(
                        api_answer(question, scope=scope, project=project_name, verbose=verbose)
                    )

            thread = threading.Thread(target=_run_context_answerer)
            thread.start()
            thread.join(timeout=300)
            if result_holder:
                self._json(result_holder[0])
            else:
                self._error(HTTPStatus.GATEWAY_TIMEOUT, "Answer timed out")
            return
        if path == "/api/query":
            body = read_body()
            if body is None:
                return
            try:
                limit = int(body.get("limit") or 20)
                offset = int(body.get("offset") or 0)
            except (TypeError, ValueError):
                self._error(HTTPStatus.BAD_REQUEST, "limit and offset must be integers")
                return
            result = api_query(
                entity=str(body.get("entity") or "").strip(),
                mode=str(body.get("mode") or "").strip(),
                scope=str(body.get("scope") or "all"),
                project=str(body.get("project") or "").strip() or None,
                kind=str(body.get("kind") or "").strip() or None,
                status=str(body.get("status") or "").strip() or None,
                source_session_id=str(body.get("source_session_id") or "").strip() or None,
                created_since=str(body.get("created_since") or "").strip() or None,
                created_until=str(body.get("created_until") or "").strip() or None,
                updated_since=str(body.get("updated_since") or "").strip() or None,
                updated_until=str(body.get("updated_until") or "").strip() or None,
                valid_at=str(body.get("valid_at") or "").strip() or None,
                order_by=str(body.get("order_by") or "created_at"),
                limit=limit,
                offset=offset,
                include_total=bool(body.get("include_total")),
            )
            if result.get("error"):
                status = int(result.get("status_code") or HTTPStatus.BAD_REQUEST)
                self._error(HTTPStatus(status), str(result.get("message") or "query failed"))
                return
            self._json(result)
            return
        if path == "/api/ingest":
            body = read_body()
            if body is None:
                return
            if "ignore_lock" in body:
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    f"ignore_lock is not supported for {path}.",
                )
                return

            ingest_kwargs = {
                "agent": body.get("agent"),
                "window": body.get("window") or None,
                "since": body.get("since"),
                "until": body.get("until"),
                "max_sessions": body.get("max_sessions"),
                "run_id": body.get("run_id"),
                "no_extract": bool(body.get("no_extract")),
                "force": bool(body.get("force")),
                "dry_run": bool(body.get("dry_run")),
            }
            if bool(body.get("blocking")):
                payload = api_ingest(**ingest_kwargs)
                self._json(payload)
                return

            job_id = str(uuid.uuid4())[:8]

            def _run_ingest() -> None:
                """Execute ingest in background."""
                api_ingest(**ingest_kwargs)

            threading.Thread(
                target=_run_ingest, name=f"ingest-{job_id}", daemon=True
            ).start()
            payload = {"status": "started", "job_id": job_id, "mode": "async"}
            self._json(payload)
            return
        if path == "/api/curate":
            body = read_body()
            if body is None:
                return
            if "force" in body:
                self._error(
                    HTTPStatus.BAD_REQUEST,
                    f"force is not supported for {path}.",
                )
                return

            curate_kwargs = {"dry_run": bool(body.get("dry_run"))}
            if bool(body.get("blocking")):
                payload = api_curate(**curate_kwargs)
                self._json(payload)
                return

            job_id = str(uuid.uuid4())[:8]

            def _run_context_curator() -> None:
                """Execute context curation in background."""
                api_curate(**curate_kwargs)

            threading.Thread(
                target=_run_context_curator, name=f"curate-{job_id}", daemon=True
            ).start()
            payload = {"status": "started", "job_id": job_id, "mode": "async"}
            self._json(payload)
            return
        if path == "/api/connect":
            body = read_body()
            if body is None:
                return
            platform = str(body.get("platform") or "").strip()
            if not platform:
                self._error(HTTPStatus.BAD_REQUEST, "Missing 'platform'")
                return
            result = api_connect(platform, path=body.get("path"))
            self._json(result)
            return
        if path == "/api/project/add":
            body = read_body()
            if body is None:
                return
            proj_path = str(body.get("path") or "").strip()
            if not proj_path:
                self._error(HTTPStatus.BAD_REQUEST, "Missing 'path'")
                return
            project_type = str(body.get("type") or "supported").strip() or "supported"
            result = api_project_add(
                proj_path,
                project_type=project_type,
                include_paths=False,
            )
            status_code = (
                HTTPStatus.BAD_REQUEST if result.get("error") else HTTPStatus.OK
            )
            self._json(result, status=status_code)
            return
        if path == "/api/project/remove":
            body = read_body()
            if body is None:
                return
            name = str(body.get("name") or "").strip()
            if not name:
                self._error(HTTPStatus.BAD_REQUEST, "Missing 'name'")
                return
            result = api_project_remove(name)
            status_code = (
                HTTPStatus.BAD_REQUEST if result.get("error") else HTTPStatus.OK
            )
            self._json(result, status=status_code)
            return
        # ── Job queue management routes ──────────────────────────────
        if path == "/api/jobs/retry-all":
            self._json(api_retry_all_dead_letter())
            return
        if path == "/api/jobs/skip-all":
            self._json(api_skip_all_dead_letter())
            return
        if path.startswith("/api/jobs/") and path.endswith("/retry"):
            run_id = unquote(path.split("/api/jobs/", 1)[1].rsplit("/retry", 1)[0])
            if not run_id:
                self._error(HTTPStatus.BAD_REQUEST, "Missing run_id in path")
                return
            self._json(api_retry_job(run_id))
            return
        if path.startswith("/api/jobs/") and path.endswith("/skip"):
            run_id = unquote(path.split("/api/jobs/", 1)[1].rsplit("/skip", 1)[0])
            if not run_id:
                self._error(HTTPStatus.BAD_REQUEST, "Missing run_id in path")
                return
            self._json(api_skip_job(run_id))
            return
        if path == "/api/config":
            self._api_config_save()
            return
        if path in {"/api/refine/run", "/api/reflect"}:
            self._error(HTTPStatus.FORBIDDEN, READ_ONLY_MESSAGE)
            return
        self._error(HTTPStatus.NOT_FOUND, "Not found")

    def do_GET(self) -> None:  # noqa: N802
        """Serve API routes and static dashboard assets for GET requests."""
        parsed = urlparse(self.path)
        path = parsed.path or "/"
        query = parse_qs(parsed.query or "", keep_blank_values=True)
        if path.startswith("/api/"):
            self._handle_api_get(path, query)
            return
        if path == "/" or path == "/index.html":
            index_file = (DASHBOARD_DIR / "index.html").resolve()
            if (
                index_file.is_file()
                and str(index_file).startswith(str(DASHBOARD_DIR.resolve()))
            ):
                self._serve_file("index.html")
            else:
                self._serve_cloud_stub_html()
            return
        if path.startswith("/session/"):
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/?tab=runs")
            self.end_headers()
            return
        self._serve_file(path.lstrip("/"))

    def do_POST(self) -> None:  # noqa: N802
        """Serve read-only POST API endpoints."""
        parsed = urlparse(self.path)
        self._handle_api_post(parsed.path or "/")

    def do_PUT(self) -> None:  # noqa: N802
        """Reject mutating PUT requests in read-only dashboard mode."""
        self._error(HTTPStatus.FORBIDDEN, READ_ONLY_MESSAGE)

    def do_PATCH(self) -> None:  # noqa: N802
        """Handle PATCH requests - config save allowed, others rejected."""
        parsed = urlparse(self.path)
        if parsed.path == "/api/config":
            self._api_config_save()
            return
        self._error(HTTPStatus.FORBIDDEN, READ_ONLY_MESSAGE)

    def do_DELETE(self) -> None:  # noqa: N802
        """Reject mutating DELETE requests in read-only dashboard mode."""
        self._error(HTTPStatus.FORBIDDEN, READ_ONLY_MESSAGE)
