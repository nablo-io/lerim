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
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from lerim.config.logging import LOG_DIR, logger
from lerim.config.project_scope import match_session_project
from lerim.config.settings import Config, get_global_data_dir_path
from lerim.context import ContextStore, ProjectIdentity, resolve_project_identity
from lerim.context.spec import ALLOWED_KINDS, RECORD_KIND_SPECS

# ── constants ────────────────────────────────────────────────────────────────

_STATE_PATH = get_global_data_dir_path() / "cloud_shipper_state.json"

_BATCH_LOGS = 500
_BATCH_SESSIONS = 100
_BATCH_RECORDS = 100

_HTTP_TIMEOUT_SECONDS = 30
_GZIP_THRESHOLD_BYTES = 1024

_PullRecordOutcome = Literal["applied", "permanent_drop", "retry"]


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

    def save(self, path: Path | None = None) -> None:
        """Write state to disk."""
        state_path = path or _STATE_PATH
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(asdict(self), ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path | None = None) -> "_ShipperState":
        """Load state from disk, returning defaults on any error."""
        state_path = path or _STATE_PATH
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
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
    qs = urllib.parse.urlencode(params) if params else ""
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
    if kind in ALLOWED_KINDS:
        return kind
    raise ValueError(f"invalid_cloud_record_kind:{kind or '<empty>'}")


def _typed_fields_from_cloud_record(record: dict[str, Any], *, kind: str) -> dict[str, str]:
    """Derive typed record fields for pulled cloud records."""
    kind_spec = RECORD_KIND_SPECS.get(kind)
    if kind_spec is None:
        return {}
    return {
        field_name: str(record.get(field_name) or "").strip()
        for field_name in kind_spec.typed_field_names
    }


def _parse_sync_timestamp(raw: str | None) -> datetime | None:
    """Parse a sync timestamp into UTC, returning ``None`` for invalid values."""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp_lte(left: str, right: str) -> bool:
    """Return whether timestamp *left* is less than or equal to *right*."""
    left_dt = _parse_sync_timestamp(left)
    right_dt = _parse_sync_timestamp(right)
    if left_dt is not None and right_dt is not None:
        return left_dt <= right_dt
    return left <= right


def _timestamp_gt(left: str, right: str) -> bool:
    """Return whether timestamp *left* is greater than *right*."""
    left_dt = _parse_sync_timestamp(left)
    right_dt = _parse_sync_timestamp(right)
    if left_dt is not None and right_dt is not None:
        return left_dt > right_dt
    return left > right


def _pull_record_sort_key(record: dict[str, Any]) -> tuple[datetime, str, str]:
    """Return a deterministic chronological sort key for cloud pull records."""
    timestamp = str(record.get("cloud_edited_at") or "")
    parsed = _parse_sync_timestamp(timestamp) or datetime.min.replace(tzinfo=timezone.utc)
    return (parsed, timestamp, str(record.get("record_id") or ""))


def _configured_project_identities(projects: dict[str, str]) -> dict[str, ProjectIdentity]:
    """Resolve configured cloud project names to local project identities."""
    identities: dict[str, ProjectIdentity] = {}
    for project_name, raw_path in projects.items():
        try:
            project_path = Path(raw_path).expanduser().resolve()
        except OSError as exc:
            logger.warning(
                "skipping configured cloud project {} because path cannot be resolved: {}",
                project_name,
                exc,
            )
            continue
        identities[str(project_name)] = resolve_project_identity(project_path)
    return identities


def _upsert_pulled_record(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity,
    record: dict[str, Any],
) -> _PullRecordOutcome:
    """Upsert one pulled cloud record into the canonical context DB."""
    record_id = str(record.get("record_id") or "").strip()
    if not record_id:
        logger.warning("skipping pulled record without record_id")
        return "permanent_drop"
    store = ContextStore(context_db_path)
    store.initialize()
    store.register_project(project_identity)
    try:
        kind = _normalize_cloud_kind(str(record.get("record_kind") or ""))
    except ValueError as exc:
        logger.warning("skipping pulled record {}: {}", record_id, exc)
        return "permanent_drop"
    if kind == "episode":
        logger.warning(
            "skipping pulled episode record {} because cloud sync only supports durable records",
            record_id,
        )
        return "permanent_drop"
    body = str(record.get("body") or "").strip()
    typed_fields = _typed_fields_from_cloud_record(record, kind=kind)
    cloud_edited = (
        str(record.get("cloud_edited_at") or "").strip()
        or datetime.now(timezone.utc).isoformat()
    )
    created_at_raw = str(record.get("created_at") or "").strip()
    created_at = created_at_raw or cloud_edited
    valid_from_raw = str(record.get("valid_from") or "").strip()
    valid_until_present = "valid_until" in record
    valid_until_raw = str(record.get("valid_until") or "").strip()
    valid_until = valid_until_raw or None
    with store.connect() as conn:
        row = conn.execute(
            "SELECT record_id, updated_at FROM records WHERE record_id = ? AND project_id = ?",
            (record_id, project_identity.project_id),
        ).fetchone()
    if row is None:
        try:
            store.create_record(
                project_id=project_identity.project_id,
                session_id=None,
                record_id=record_id,
                kind=kind,
                title=str(record.get("title") or "").strip(),
                body=body,
                status=str(record.get("status") or "active").strip() or "active",
                created_at=created_at,
                updated_at=cloud_edited,
                valid_from=valid_from_raw or created_at,
                valid_until=valid_until,
                change_reason="cloud_pull",
                **typed_fields,
            )
        except ValueError as exc:
            logger.warning("skipping pulled record {}: {}", record_id, exc)
            return "retry"
        return "applied"
    local_updated_at = str(row["updated_at"] or "").strip()
    if local_updated_at and _timestamp_lte(cloud_edited, local_updated_at):
        logger.info(
            "skipping pulled record {} because local updated_at {} is newer than cloud_edited_at {}",
            record_id,
            local_updated_at,
            cloud_edited,
        )
        return "permanent_drop"
    changes: dict[str, Any] = {
        "kind": kind,
        "title": str(record.get("title") or "").strip(),
        "body": body,
        "status": str(record.get("status") or "active").strip() or "active",
        "updated_at": cloud_edited,
        **typed_fields,
    }
    if valid_from_raw:
        changes["valid_from"] = valid_from_raw
    if valid_until_present:
        changes["valid_until"] = valid_until
    try:
        store.update_record(
            record_id=record_id,
            session_id=None,
            project_ids=[project_identity.project_id],
            changes=changes,
            change_reason="cloud_pull",
        )
    except ValueError as exc:
        if str(exc) == "no_changes":
            logger.info("pulled record {} has no local changes", record_id)
            return "permanent_drop"
        logger.warning("skipping pulled record {}: {}", record_id, exc)
        return "retry"
    return "applied"


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
    project_identities = _configured_project_identities(config.projects or {})

    records = sorted(
        (record for record in data["records"] if isinstance(record, dict)),
        key=_pull_record_sort_key,
    )

    def _mark_processed(cloud_edited_at: str) -> None:
        """Advance the pull cursor after a record is intentionally handled."""
        nonlocal latest_edited
        if not latest_edited or _timestamp_gt(cloud_edited_at, latest_edited):
            latest_edited = cloud_edited_at

    for record in records:
        cloud_edited = str(record.get("cloud_edited_at") or "").strip()
        if not cloud_edited:
            continue

        project_name = str(record.get("project") or "").strip()
        if not project_name:
            logger.warning(
                "skipping pulled record {} because cloud project is missing",
                record.get("record_id", ""),
            )
            _mark_processed(cloud_edited)
            continue
        project_identity = project_identities.get(project_name)
        if project_identity is None:
            logger.warning(
                "skipping pulled record {} because project {} is not configured locally",
                record.get("record_id", ""),
                project_name,
            )
            _mark_processed(cloud_edited)
            continue

        try:
            outcome = _upsert_pulled_record(
                context_db_path=config.context_db_path,
                project_identity=project_identity,
                record=record,
            )
            if outcome == "applied":
                pulled += 1
            if outcome in {"applied", "permanent_drop"}:
                _mark_processed(cloud_edited)
            if outcome == "retry":
                break
        except (OSError, sqlite3.Error) as exc:
            logger.warning(
                "failed to persist pulled record {}: {}",
                record.get("record_id", ""),
                exc,
            )
            break

    if latest_edited:
        state.records_pulled_at = latest_edited

    return pulled


# ── log shipping ─────────────────────────────────────────────────────────────


async def _ship_logs(endpoint: str, token: str, state: _ShipperState) -> int:
    """Ship new entries from the newest dated ``lerim.jsonl`` since last offset.

    The bookmark is per relative log path. When a new day starts, shipping
    switches to that day's file and resets the offset.
    """
    log_paths = sorted(
        LOG_DIR.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]/lerim.jsonl")
    )
    if not log_paths:
        return 0
    log_path = log_paths[-1]
    relative_log_file = str(log_path.relative_to(LOG_DIR))
    if state.log_file != relative_log_file:
        state.log_file = relative_log_file
        state.log_offset_bytes = 0

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
            batch_offset = offset
            while True:
                line_offset = fh.tell()
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
                if not batch:
                    batch_offset = line_offset
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
                        new_offset = fh.tell()
                    else:
                        # Stop shipping on first failure; resume next cycle.
                        state.log_offset_bytes = batch_offset
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
                else:
                    state.log_offset_bytes = batch_offset
                    return shipped

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


def _resolve_session_project(
    repo_path: str | None,
    projects: dict[str, str],
) -> str | None:
    """Resolve a session repo path to a configured project name."""
    if not repo_path or not projects:
        return None
    try:
        match = match_session_project(repo_path, projects)
    except OSError as exc:
        logger.debug("cloud shipper: failed resolving session project {}: {}", repo_path, exc)
        return None
    if match is None:
        return None
    project_name, _project_path = match
    return project_name


async def _ship_sessions(
    endpoint: str,
    token: str,
    state: _ShipperState,
    db_path: Path,
    projects: dict[str, str] | None = None,
) -> int:
    """Ship new/updated sessions from SQLite, including cached transcripts."""
    shipped = 0
    latest_indexed_at = state.sessions_shipped_at
    configured_projects = projects or {}

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
            project_name = _resolve_session_project(
                row.get("repo_path"), configured_projects
            )
            if project_name is not None:
                entry["project"] = project_name
            # Attach transcript from cached JSONL file
            transcript = _read_transcript(row.get("session_path"))
            if transcript:
                entry["transcript_jsonl"] = transcript
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
        "SELECT record_id, project_id, kind, title, body, status, created_at, updated_at, valid_from, valid_until, "
        "decision, why, alternatives, consequences, user_intent, what_happened, outcomes "
        f"FROM records WHERE project_id IN ({placeholders}) AND kind != 'episode'"
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
                "created_at": record.get("created_at", ""),
                "valid_from": record.get("valid_from", ""),
                "valid_until": record.get("valid_until"),
                "updated": record.get("updated_at", ""),
                "cloud_edited_at": record.get("updated_at", ""),
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
    state_path = config.global_data_dir / "cloud_shipper_state.json"
    state = _ShipperState.load(path=state_path)

    # Phase 1: Pull (cloud -> local)
    records_pulled = await _pull_records(endpoint, token, config, state)

    # Phase 2: Push (local -> cloud)
    logs_shipped = await _ship_logs(endpoint, token, state)
    sessions_shipped = await _ship_sessions(
        endpoint, token, state, config.sessions_db_path, config.projects
    )
    service_runs_shipped = await _ship_service_runs(
        endpoint, token, state, config.sessions_db_path
    )
    job_statuses_shipped = await _ship_job_statuses(
        endpoint, token, state, config.sessions_db_path
    )
    records_shipped = await _ship_records(endpoint, token, config, state)

    state.save(path=state_path)

    return {
        "logs": logs_shipped,
        "sessions": sessions_shipped,
        "service_runs": service_runs_shipped,
        "job_statuses": job_statuses_shipped,
        "records": records_shipped,
        "records_pulled": records_pulled,
    }
