"""Cloud shipper — ships local data (logs, sessions, records) to lerim-cloud.

Reads new entries from local storage, batches them, and POSTs to the cloud API.
Tracks shipping offsets in ``~/.lerim/cloud_shipper_state.json`` to avoid
re-sending.  Designed to run inside the daemon loop via ``ship_once()``.

Uses only stdlib ``urllib.request`` for HTTP — no third-party HTTP deps.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lerim.config.logging import LOG_DIR, logger
from lerim.config.settings import Config
from lerim.context import ContextStore, resolve_project_identity

# ── constants ────────────────────────────────────────────────────────────────

_STATE_PATH = Path.home() / ".lerim" / "cloud_shipper_state.json"

_BATCH_LOGS = 500
_BATCH_SESSIONS = 100
_BATCH_RECORDS = 100

_HTTP_TIMEOUT_SECONDS = 30
_GZIP_THRESHOLD_BYTES = 1024


# ── state persistence ────────────────────────────────────────────────────────


@dataclass
class _ShipperState:
    """Mutable shipping-offset state persisted between daemon cycles."""

    log_offset_bytes: int = 0
    log_file: str = "lerim.jsonl"
    sessions_shipped_at: str = ""
    records_shipped_at: str = ""
    records_pulled_at: str = ""
    service_runs_shipped_at: str = ""
    jobs_shipped_at: str = ""

    def save(self) -> None:
        """Write state to disk."""
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(
            json.dumps(asdict(self), ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls) -> "_ShipperState":
        """Load state from disk, returning defaults on any error."""
        try:
            raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return cls()
            return cls(
                log_offset_bytes=int(raw.get("log_offset_bytes") or 0),
                log_file=str(raw.get("log_file") or "lerim.jsonl"),
                sessions_shipped_at=str(raw.get("sessions_shipped_at") or ""),
                records_shipped_at=str(raw.get("records_shipped_at") or ""),
                records_pulled_at=str(raw.get("records_pulled_at") or ""),
                service_runs_shipped_at=str(raw.get("service_runs_shipped_at") or ""),
                jobs_shipped_at=str(raw.get("jobs_shipped_at") or ""),
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return cls()


# ── HTTP helper ──────────────────────────────────────────────────────────────


def _post_batch_sync(
    endpoint: str, path: str, token: str, payload: dict[str, Any]
) -> bool:
    """POST a JSON payload to the cloud API.  Returns ``True`` on 2xx.

    Compresses the body with gzip when it exceeds ``_GZIP_THRESHOLD_BYTES``.
    This is a *synchronous* call — callers wrap it with ``asyncio.to_thread``.
    """
    url = f"{endpoint.rstrip('/')}{path}"
    body = json.dumps(payload, ensure_ascii=True, default=str).encode("utf-8")

    headers: dict[str, str] = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # Note: gzip disabled — FastAPI does not decompress Content-Encoding: gzip
    # by default.  Re-enable once the cloud API adds gzip middleware.

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        logger.warning("cloud POST {} failed: {} — {}", path, exc, body_text)
        return False
    except (urllib.error.URLError, OSError) as exc:
        logger.debug("cloud POST {} failed: {}", path, exc)
        return False


async def _post_batch(
    endpoint: str, path: str, token: str, payload: dict[str, Any]
) -> bool:
    """Async wrapper that offloads the synchronous HTTP call to a thread."""
    return await asyncio.to_thread(_post_batch_sync, endpoint, path, token, payload)


# ── HTTP GET helper ──────────────────────────────────────────────────────────


def _get_json_sync(
    endpoint: str, path: str, token: str, params: dict[str, str]
) -> dict[str, Any] | None:
    """Synchronous GET request returning parsed JSON."""
    qs = "&".join(f"{k}={v}" for k, v in params.items()) if params else ""
    url = f"{endpoint.rstrip('/')}{path}{'?' + qs if qs else ''}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        logger.warning("cloud GET {} failed: {}", path, exc)
        return None
    except (urllib.error.URLError, OSError) as exc:
        logger.debug("cloud GET {} failed: {}", path, exc)
        return None


# ── pull helpers ─────────────────────────────────────────────────────────────


def _normalize_cloud_kind(raw: str | None) -> str:
    """Map cloud record kinds onto canonical context kinds."""
    kind = str(raw or "").strip().lower()
    if kind in {"decision", "preference", "constraint", "fact", "reference", "episode"}:
        return kind
    if kind in {"project", "learning", "feedback", "implementation"}:
        return "fact"
    return "fact"


def _typed_fields_from_cloud_record(record: dict[str, Any], *, kind: str) -> dict[str, str]:
    """Derive typed record fields for pulled cloud records."""
    title = str(record.get("title") or record.get("name") or "").strip()
    summary = str(record.get("description") or "").strip()
    body = str(record.get("body") or "").strip()
    if kind == "decision":
        return {
            "decision": title or summary or body,
            "why": body or summary,
        }
    if kind == "episode":
        return {
            "user_intent": summary or title,
            "what_happened": body or summary or title,
        }
    return {}


def _upsert_pulled_record(
    *,
    context_db_path: Path,
    project_name: str,
    project_path: Path,
    record: dict[str, Any],
) -> bool:
    """Upsert one pulled cloud record into the canonical context DB."""
    record_id = str(record.get("record_id") or "").strip()
    if not record_id:
        return False
    store = ContextStore(context_db_path)
    store.initialize()
    identity = resolve_project_identity(project_path)
    store.register_project(identity)
    kind = _normalize_cloud_kind(str(record.get("record_kind") or ""))
    summary = str(record.get("description") or "").strip()
    body = str(record.get("body") or "").strip() or summary or str(record.get("title") or "").strip()
    typed_fields = _typed_fields_from_cloud_record(record, kind=kind)
    cloud_edited = str(record.get("cloud_edited_at") or "").strip() or datetime.now(timezone.utc).isoformat()
    with store.connect() as conn:
        row = conn.execute(
            "SELECT record_id FROM records WHERE record_id = ? AND project_id = ?",
            (record_id, identity.project_id),
        ).fetchone()
    if row is None:
        store.create_record(
            project_id=identity.project_id,
            session_id=None,
            record_id=record_id,
            kind=kind,
            title=str(record.get("title") or record.get("name") or record_id).strip(),
            body=body,
            status=str(record.get("status") or "active").strip() or "active",
            valid_from=cloud_edited,
            change_reason="cloud_pull",
            **typed_fields,
        )
        return True
    store.update_record(
        record_id=record_id,
        session_id=None,
        project_ids=[identity.project_id],
        changes={
            "kind": kind,
            "title": str(record.get("title") or record.get("name") or record_id).strip(),
            "body": body,
            "status": str(record.get("status") or "active").strip() or "active",
            "valid_from": cloud_edited,
            **typed_fields,
        },
        change_reason="cloud_pull",
    )
    return True


async def _pull_records(
    endpoint: str, token: str, config: Config, state: _ShipperState
) -> int:
    """Pull dashboard-edited records into the canonical context DB."""
    params: dict[str, str] = {"limit": "200"}
    if state.records_pulled_at:
        params["since"] = state.records_pulled_at

    try:
        data = await asyncio.to_thread(
            _get_json_sync, endpoint, "/api/v1/sync/records", token, params
        )
    except Exception as exc:
        logger.warning("cloud pull records failed: {}", exc)
        return 0

    if not data or not data.get("records"):
        return 0

    pulled = 0
    latest_edited = state.records_pulled_at

    for record in data["records"]:
        cloud_edited = record.get("cloud_edited_at", "")
        if not cloud_edited:
            continue

        project_name = record.get("project")
        if not project_name or project_name not in (config.projects or {}):
            if config.projects:
                project_name = next(iter(config.projects))
            else:
                continue

        try:
            project_path = Path(config.projects[project_name]).expanduser().resolve()
            if _upsert_pulled_record(
                context_db_path=config.context_db_path,
                project_name=str(project_name),
                project_path=project_path,
                record=record,
            ):
                pulled += 1
        except OSError as exc:
            logger.warning("failed to persist pulled record {}: {}", record.get("record_id", ""), exc)

        if cloud_edited > latest_edited:
            latest_edited = cloud_edited

    if latest_edited and latest_edited != state.records_pulled_at:
        state.records_pulled_at = latest_edited

    return pulled


# ── log shipping ─────────────────────────────────────────────────────────────


async def _ship_logs(endpoint: str, token: str, state: _ShipperState) -> int:
    """Ship new log entries from ``lerim.jsonl`` since last offset.

    Handles log rotation: if the file is smaller than the stored offset the
    offset is reset to zero.
    """
    log_path = LOG_DIR / state.log_file
    if not log_path.exists():
        return 0

    file_size = log_path.stat().st_size
    offset = state.log_offset_bytes

    # Detect rotation: file shrunk below our bookmark.
    if file_size < offset:
        logger.info("cloud shipper: log file rotated, resetting offset")
        offset = 0

    if offset >= file_size:
        return 0

    shipped = 0
    new_offset = offset
    try:
        with open(log_path, "r", encoding="utf-8") as fh:
            fh.seek(offset)
            batch: list[dict[str, Any]] = []
            while True:
                line = fh.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                batch.append(entry)

                if len(batch) >= _BATCH_LOGS:
                    ok = await _post_batch(
                        endpoint,
                        "/api/v1/ingest/logs",
                        token,
                        {"entries": batch},
                    )
                    if ok:
                        shipped += len(batch)
                    else:
                        # Stop shipping on first failure; resume next cycle.
                        new_offset = fh.tell()
                        state.log_offset_bytes = new_offset
                        return shipped
                    batch = []

            # Flush remaining partial batch.
            if batch:
                ok = await _post_batch(
                    endpoint,
                    "/api/v1/ingest/logs",
                    token,
                    {"entries": batch},
                )
                if ok:
                    shipped += len(batch)

            new_offset = fh.tell()
    except OSError as exc:
        logger.warning("cloud shipper: failed reading log file: {}", exc)

    state.log_offset_bytes = new_offset
    return shipped


# ── session shipping ─────────────────────────────────────────────────────────


def _query_new_sessions(
    db_path: Path, since_iso: str, limit: int
) -> list[dict[str, Any]]:
    """Query sessions with ``indexed_at`` after *since_iso* (synchronous)."""
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = lambda cur, row: {
            col[0]: row[idx] for idx, col in enumerate(cur.description)
        }
        if since_iso:
            rows = conn.execute(
                """
                SELECT run_id, agent_type, repo_path, repo_name, start_time,
                       indexed_at, status, duration_ms, message_count,
                       tool_call_count, error_count, total_tokens,
                       summary_text, tags, outcome, session_path
                FROM session_docs
                WHERE indexed_at > ?
                ORDER BY indexed_at ASC
                LIMIT ?
                """,
                (since_iso, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT run_id, agent_type, repo_path, repo_name, start_time,
                       indexed_at, status, duration_ms, message_count,
                       tool_call_count, error_count, total_tokens,
                       summary_text, tags, outcome, session_path
                FROM session_docs
                ORDER BY indexed_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        conn.close()
        return rows
    except sqlite3.Error as exc:
        logger.warning("cloud shipper: session query failed: {}", exc)
        return []


def _read_transcript(session_path: str | None) -> str | None:
    """Read the cached JSONL transcript file for a session, if it exists."""
    if not session_path:
        return None
    try:
        p = Path(session_path).expanduser()
        if p.is_file() and p.stat().st_size < 5_000_000:  # skip files > 5MB
            return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return None


async def _ship_sessions(
    endpoint: str, token: str, state: _ShipperState, db_path: Path
) -> int:
    """Ship new/updated sessions from SQLite, including cached transcripts."""
    shipped = 0
    latest_indexed_at = state.sessions_shipped_at

    while True:
        rows = await asyncio.to_thread(
            _query_new_sessions, db_path, latest_indexed_at, _BATCH_SESSIONS
        )
        if not rows:
            break

        # Map SQLite rows to the API's expected SessionEntry fields
        _API_FIELDS = {
            "run_id", "agent_type", "repo_name", "start_time",
            "duration_ms", "message_count", "tool_call_count",
            "error_count", "total_tokens", "summary_text",
            "project", "machine_id", "transcript_jsonl",
        }
        sessions_payload = []
        for row in rows:
            entry = {k: v for k, v in row.items() if k in _API_FIELDS and v is not None}
            # Attach transcript from cached JSONL file
            transcript = _read_transcript(row.get("session_path"))
            if transcript:
                entry["transcript_jsonl"] = transcript
            # Use repo_path as project fallback
            if "project" not in entry and row.get("repo_path"):
                entry["project"] = Path(row["repo_path"]).name
            sessions_payload.append(entry)

        ok = await _post_batch(
            endpoint,
            "/api/v1/ingest/sessions",
            token,
            {"sessions": sessions_payload},
        )
        if ok:
            shipped += len(rows)
            # Advance watermark to the latest indexed_at in this batch.
            last_row_ts = str(rows[-1].get("indexed_at") or "")
            if last_row_ts > latest_indexed_at:
                latest_indexed_at = last_row_ts
        else:
            break

        # If we got fewer than a full batch, we have caught up.
        if len(rows) < _BATCH_SESSIONS:
            break

    if latest_indexed_at and latest_indexed_at != state.sessions_shipped_at:
        state.sessions_shipped_at = latest_indexed_at
    return shipped


# ── record shipping ──────────────────────────────────────────────────────────


def _query_context_records(
    context_db_path: Path,
    projects: dict[str, str],
    since_iso: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Query durable context records for the selected projects."""
    if not context_db_path.exists() or not projects:
        return []
    selected_ids = {
        resolve_project_identity(Path(project_path)).project_id: project_name
        for project_name, project_path in projects.items()
    }
    placeholders = ", ".join("?" for _ in selected_ids)
    sql = (
        "SELECT record_id, project_id, kind, title, body, status, updated_at, "
        "decision, why, alternatives, consequences, user_intent, what_happened, outcomes "
        f"FROM records WHERE project_id IN ({placeholders})"
    )
    params: list[Any] = list(selected_ids.keys())
    if since_iso:
        sql += " AND updated_at > ?"
        params.append(since_iso)
    sql += " ORDER BY updated_at ASC LIMIT ?"
    params.append(limit)
    try:
        conn = sqlite3.connect(context_db_path)
        conn.row_factory = lambda cur, row: {
            col[0]: row[idx] for idx, col in enumerate(cur.description)
        }
        rows = conn.execute(sql, tuple(params)).fetchall()
        conn.close()
    except sqlite3.Error as exc:
        logger.warning("cloud shipper: context record query failed: {}", exc)
        return []
    for row in rows:
        row["project"] = selected_ids.get(str(row.get("project_id") or ""), "")
    return rows


async def _ship_records(
    endpoint: str,
    token: str,
    config: Config,
    state: _ShipperState,
) -> int:
    """Ship durable context records from the canonical context DB."""
    projects = config.projects or {}
    if not projects:
        return 0

    all_records = await asyncio.to_thread(
        _query_context_records,
        config.context_db_path,
        projects,
        state.records_shipped_at,
        _BATCH_RECORDS * 10,
    )
    if not all_records:
        return 0

    shipped = 0
    latest_updated = state.records_shipped_at

    for i in range(0, len(all_records), _BATCH_RECORDS):
        raw_batch = all_records[i : i + _BATCH_RECORDS]
        batch = []
        for record in raw_batch:
            entry: dict[str, Any] = {
                "record_id": record.get("record_id", ""),
                "record_kind": record.get("kind"),
                "title": record.get("title", ""),
                "description": "",
                "body": record.get("body", ""),
                "project": record.get("project"),
                "status": record.get("status", "active"),
                "decision": record.get("decision", ""),
                "why": record.get("why", ""),
                "alternatives": record.get("alternatives", ""),
                "consequences": record.get("consequences", ""),
                "user_intent": record.get("user_intent", ""),
                "what_happened": record.get("what_happened", ""),
                "outcomes": record.get("outcomes", ""),
                "updated": record.get("updated_at", ""),
            }
            batch.append(entry)
        ok = await _post_batch(
            endpoint,
            "/api/v1/ingest/records",
            token,
            {"records": batch},
        )
        if ok:
            shipped += len(batch)
            for record in batch:
                ts = str(record.get("updated") or "")
                if ts > latest_updated:
                    latest_updated = ts
        else:
            break

    if latest_updated and latest_updated != state.records_shipped_at:
        state.records_shipped_at = latest_updated
    return shipped


# ── service-run shipping ─────────────────────────────────────────────────


def _query_service_runs(db_path: Path, since_iso: str, limit: int) -> list[dict[str, Any]]:
    """Query local service_runs table for new entries."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = lambda cur, row: {col[0]: row[idx] for idx, col in enumerate(cur.description)}
        if since_iso:
            rows = conn.execute(
                "SELECT job_type, status, started_at, completed_at, trigger, details_json "
                "FROM service_runs WHERE started_at > ? ORDER BY started_at ASC LIMIT ?",
                (since_iso, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT job_type, status, started_at, completed_at, trigger, details_json "
                "FROM service_runs ORDER BY started_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        conn.close()
        # Parse details_json from string to dict
        for row in rows:
            dj = row.get("details_json")
            if isinstance(dj, str):
                try:
                    row["details_json"] = json.loads(dj)
                except json.JSONDecodeError:
                    row["details_json"] = {}
            elif dj is None:
                row["details_json"] = {}
        return rows
    except sqlite3.Error as exc:
        logger.warning("cloud shipper: service_runs query failed: {}", exc)
        return []


async def _ship_service_runs(
    endpoint: str, token: str, state: _ShipperState, db_path: Path
) -> int:
    """Ship service run records from local SQLite."""
    shipped = 0
    latest_started = state.service_runs_shipped_at

    while True:
        rows = await asyncio.to_thread(
            _query_service_runs, db_path, latest_started, 100
        )
        if not rows:
            break

        ok = await _post_batch(
            endpoint, "/api/v1/ingest/service_runs", token, {"runs": rows}
        )
        if ok:
            shipped += len(rows)
            last_ts = str(rows[-1].get("started_at") or "")
            if last_ts > latest_started:
                latest_started = last_ts
        else:
            break

        if len(rows) < 100:
            break

    if latest_started and latest_started != state.service_runs_shipped_at:
        state.service_runs_shipped_at = latest_started
    return shipped


# ── job status shipping ──────────────────────────────────────────────────────


_JOB_STATUS_MAP: dict[str, str] = {
	"pending": "queued",
	"running": "processing",
	"done": "processed",
	"failed": "failed",
	"dead_letter": "blocked",
}


def _query_job_statuses(
	db_path: Path, since_iso: str, limit: int,
) -> list[dict[str, Any]]:
	"""Query session_jobs with ``updated_at`` after *since_iso*."""
	if not db_path.exists():
		return []
	try:
		conn = sqlite3.connect(db_path)
		conn.row_factory = lambda cur, row: {
			col[0]: row[idx] for idx, col in enumerate(cur.description)
		}
		if since_iso:
			rows = conn.execute(
				"""
				SELECT run_id, status, error, attempts, updated_at
				FROM session_jobs
				WHERE updated_at > ?
				ORDER BY updated_at ASC
				LIMIT ?
				""",
				(since_iso, limit),
			).fetchall()
		else:
			rows = conn.execute(
				"""
				SELECT run_id, status, error, attempts, updated_at
				FROM session_jobs
				ORDER BY updated_at ASC
				LIMIT ?
				""",
				(limit,),
			).fetchall()
		conn.close()
		return rows
	except sqlite3.Error as exc:
		logger.warning("cloud shipper: job status query failed: {}", exc)
		return []


async def _ship_job_statuses(
	endpoint: str, token: str, state: _ShipperState, db_path: Path,
) -> int:
	"""Ship processing status updates from session_jobs."""
	shipped = 0
	latest_updated = state.jobs_shipped_at

	while True:
		rows = await asyncio.to_thread(
			_query_job_statuses, db_path, latest_updated, 500,
		)
		if not rows:
			break

		statuses_payload = []
		for row in rows:
			local_status = str(row.get("status") or "")
			statuses_payload.append({
				"run_id": str(row.get("run_id") or ""),
				"processing_status": _JOB_STATUS_MAP.get(local_status, local_status),
				"processing_error": row.get("error"),
				"processing_attempts": int(row.get("attempts") or 0),
			})

		ok = await _post_batch(
			endpoint, "/api/v1/ingest/job_statuses", token,
			{"statuses": statuses_payload},
		)
		if ok:
			shipped += len(rows)
			last_ts = str(rows[-1].get("updated_at") or "")
			if last_ts > latest_updated:
				latest_updated = last_ts
		else:
			break

		if len(rows) < 500:
			break

	if latest_updated and latest_updated != state.jobs_shipped_at:
		state.jobs_shipped_at = latest_updated
	return shipped


# ── public entry point ───────────────────────────────────────────────────────


def _is_cloud_configured(config: Config) -> bool:
    """Check if cloud token and endpoint are both set."""
    return bool(config.cloud_token and config.cloud_endpoint)


async def ship_once(config: Config) -> dict[str, int]:
    """Run one sync cycle (pull then push).

    Returns counts of items synced per type, or an empty dict if cloud
    is not configured.
    """
    if not _is_cloud_configured(config):
        return {}

    endpoint = config.cloud_endpoint
    token = config.cloud_token or ""
    state = _ShipperState.load()

    # Phase 1: Pull (cloud -> local)
    records_pulled = await _pull_records(endpoint, token, config, state)

    # Phase 2: Push (local -> cloud)
    logs_shipped = await _ship_logs(endpoint, token, state)
    sessions_shipped = await _ship_sessions(
        endpoint, token, state, config.sessions_db_path
    )
    service_runs_shipped = await _ship_service_runs(
        endpoint, token, state, config.sessions_db_path
    )
    job_statuses_shipped = await _ship_job_statuses(
        endpoint, token, state, config.sessions_db_path
    )
    records_shipped = await _ship_records(endpoint, token, config, state)

    state.save()

    return {
        "logs": logs_shipped,
        "sessions": sessions_shipped,
        "service_runs": service_runs_shipped,
        "job_statuses": job_statuses_shipped,
        "records": records_shipped,
        "records_pulled": records_pulled,
    }
