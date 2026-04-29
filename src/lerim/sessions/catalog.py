"""Session catalog + durable queue for Lerim 004 core runtime."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from typing import Any, Callable

from lerim.adapters import registry as adapter_registry
from lerim.config.project_scope import match_session_project
from lerim.config.logging import logger
from lerim.config.settings import get_config, reload_config


JOB_TYPE_EXTRACT = "extract"
JOB_STATUS_PENDING = "pending"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_DONE = "done"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_DEAD_LETTER = "dead_letter"
SESSION_JOB_ACTIVE = {JOB_STATUS_PENDING, JOB_STATUS_RUNNING}
SESSION_JOB_CLAIM_NEWEST = "newest"
SESSION_JOB_CLAIM_OLDEST = "oldest"
SESSION_JOB_CLAIM_ORDERS = {SESSION_JOB_CLAIM_NEWEST, SESSION_JOB_CLAIM_OLDEST}
_DB_INIT_LOCK = threading.Lock()
_DB_INITIALIZED_PATH: Path | None = None
DEFAULT_RUNNING_JOB_LEASE_SECONDS = 2 * 60


@dataclass(frozen=True)
class IndexedSession:
    """Minimal indexed-session payload returned by ``index_new_sessions``."""

    run_id: str
    agent_type: str
    session_path: str
    start_time: str | None
    repo_path: str | None = None
    changed: bool = False


def _utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    """Return current UTC datetime as ISO8601 text."""
    return _utc_now().isoformat()


def _to_iso(value: datetime | None) -> str | None:
    """Convert datetime to UTC-aware ISO string when value is present."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    """Parse ISO timestamps, supporting ``Z`` suffix values."""
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _db_path() -> Path:
    """Return the configured SQLite path for session catalog storage."""
    return get_config().sessions_db_path


def _dict_row(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    """Convert SQLite row tuples into dictionary rows."""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def _connect() -> sqlite3.Connection:
    """Open catalog SQLite connection with dictionary row factory."""
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = _dict_row
    return conn


def _ensure_sessions_db_initialized() -> None:
    """Initialize schema once per resolved database path."""
    global _DB_INITIALIZED_PATH
    path = _db_path()
    if _DB_INITIALIZED_PATH == path and path.exists():
        return
    with _DB_INIT_LOCK:
        path = _db_path()
        if _DB_INITIALIZED_PATH == path and path.exists():
            return
        init_sessions_db()


def init_sessions_db() -> None:
    """Create/upgrade session catalog, queue, and service-run tables."""
    global _DB_INITIALIZED_PATH
    with _connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(
            """
                    CREATE TABLE IF NOT EXISTS session_docs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL UNIQUE,
                        agent_type TEXT NOT NULL,
                        repo_path TEXT,
                        repo_name TEXT,
                        start_time TEXT,
                        content TEXT,
                        indexed_at TEXT NOT NULL,
                        status TEXT DEFAULT 'completed',
                        duration_ms INTEGER DEFAULT 0,
                        message_count INTEGER DEFAULT 0,
                        tool_call_count INTEGER DEFAULT 0,
                        error_count INTEGER DEFAULT 0,
                        total_tokens INTEGER DEFAULT 0,
                        summaries TEXT,
                        summary_text TEXT,
                        turns_json TEXT,
                        session_path TEXT,
                        content_hash TEXT,
                        tags TEXT,
                        outcome TEXT
                    )
                    """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_docs_run ON session_docs (run_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_docs_agent ON session_docs (agent_type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_docs_time ON session_docs (start_time)"
        )

        conn.execute(
            """
                    CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
                        run_id,
                        agent_type,
                        repo_name,
                        content,
                        content='session_docs',
                        content_rowid='id'
                    )
                    """
        )
        conn.execute(
            """
                    CREATE TRIGGER IF NOT EXISTS session_docs_ai AFTER INSERT ON session_docs BEGIN
                        INSERT INTO sessions_fts(rowid, run_id, agent_type, repo_name, content)
                        VALUES (new.id, new.run_id, new.agent_type, new.repo_name, new.content);
                    END
                    """
        )
        conn.execute(
            """
                    CREATE TRIGGER IF NOT EXISTS session_docs_ad AFTER DELETE ON session_docs BEGIN
                        INSERT INTO sessions_fts(sessions_fts, rowid, run_id, agent_type, repo_name, content)
                        VALUES ('delete', old.id, old.run_id, old.agent_type, old.repo_name, old.content);
                    END
                    """
        )
        conn.execute(
            """
                    CREATE TRIGGER IF NOT EXISTS session_docs_au AFTER UPDATE ON session_docs BEGIN
                        INSERT INTO sessions_fts(sessions_fts, rowid, run_id, agent_type, repo_name, content)
                        VALUES ('delete', old.id, old.run_id, old.agent_type, old.repo_name, old.content);
                        INSERT INTO sessions_fts(rowid, run_id, agent_type, repo_name, content)
                        VALUES (new.id, new.run_id, new.agent_type, new.repo_name, new.content);
                    END
                    """
        )

        conn.execute(
            """
                    CREATE TABLE IF NOT EXISTS session_jobs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        job_type TEXT NOT NULL DEFAULT 'extract',
                        agent_type TEXT,
                        session_path TEXT,
                        start_time TEXT,
                        status TEXT NOT NULL,
                        attempts INTEGER DEFAULT 0,
                        max_attempts INTEGER DEFAULT 3,
                        trigger TEXT,
                        available_at TEXT NOT NULL,
                        claimed_at TEXT,
                        completed_at TEXT,
                        heartbeat_at TEXT,
                        error TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        repo_path TEXT,
                        UNIQUE(run_id, job_type)
                    )
                    """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_jobs_status_available ON session_jobs (status, available_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_jobs_updated ON session_jobs (updated_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_jobs_repo ON session_jobs (repo_path)"
        )

        conn.execute(
            """
                    CREATE TABLE IF NOT EXISTS service_runs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_type TEXT NOT NULL,
                        status TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        completed_at TEXT,
                        trigger TEXT,
                        details_json TEXT
                    )
                    """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_service_runs_job ON service_runs (job_type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_service_runs_started ON service_runs (started_at)"
        )
        conn.commit()
    _DB_INITIALIZED_PATH = _db_path()


def index_session_for_fts(
    run_id: str,
    agent_type: str,
    content: str,
    repo_path: str | None = None,
    repo_name: str | None = None,
    start_time: str | None = None,
    status: str = "completed",
    duration_ms: int = 0,
    message_count: int = 0,
    tool_call_count: int = 0,
    error_count: int = 0,
    total_tokens: int = 0,
    summaries: str | None = None,
    summary_text: str | None = None,
    turns_json: str | None = None,
    session_path: str | None = None,
    content_hash: str | None = None,
) -> bool:
    """Insert or replace one session document row and keep FTS index synced."""
    if not run_id or not agent_type:
        return False
    _ensure_sessions_db_initialized()

    if summary_text is None and summaries:
        try:
            parsed = json.loads(summaries)
        except (json.JSONDecodeError, TypeError):
            parsed = []
        if isinstance(parsed, list):
            summary_text = "\n".join(str(item) for item in parsed if item)

    try:
        indexed_at = _iso_now()
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO session_docs (
                    run_id, agent_type, repo_path, repo_name, start_time, content,
                    indexed_at, status, duration_ms, message_count, tool_call_count,
                    error_count, total_tokens, summaries, summary_text, turns_json,
                    session_path, content_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    agent_type = excluded.agent_type,
                    repo_path = excluded.repo_path,
                    repo_name = excluded.repo_name,
                    start_time = excluded.start_time,
                    content = excluded.content,
                    indexed_at = excluded.indexed_at,
                    status = excluded.status,
                    duration_ms = excluded.duration_ms,
                    message_count = excluded.message_count,
                    tool_call_count = excluded.tool_call_count,
                    error_count = excluded.error_count,
                    total_tokens = excluded.total_tokens,
                    summaries = excluded.summaries,
                    summary_text = CASE
                        WHEN session_docs.content_hash IS NOT NULL
                         AND session_docs.content_hash = excluded.content_hash
                        THEN session_docs.summary_text
                        ELSE excluded.summary_text
                    END,
                    turns_json = excluded.turns_json,
                    session_path = excluded.session_path,
                    content_hash = excluded.content_hash,
                    tags = CASE
                        WHEN session_docs.content_hash IS NOT NULL
                         AND session_docs.content_hash = excluded.content_hash
                        THEN session_docs.tags
                        ELSE NULL
                    END,
                    outcome = CASE
                        WHEN session_docs.content_hash IS NOT NULL
                         AND session_docs.content_hash = excluded.content_hash
                        THEN session_docs.outcome
                        ELSE NULL
                    END
                """,
                (
                    run_id,
                    agent_type,
                    repo_path,
                    repo_name,
                    start_time,
                    content,
                    indexed_at,
                    status,
                    duration_ms,
                    message_count,
                    tool_call_count,
                    error_count,
                    total_tokens,
                    summaries,
                    summary_text,
                    turns_json,
                    session_path,
                    content_hash,
                ),
            )
            conn.commit()
        return True
    except sqlite3.Error as exc:
        logger.warning("session index failed | run_id={} error={}", run_id, str(exc))
        return False


def fetch_session_doc(run_id: str) -> dict[str, Any] | None:
    """Fetch one indexed session document by run id."""
    if not run_id:
        return None
    _ensure_sessions_db_initialized()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM session_docs WHERE run_id = ?", (run_id,)
        ).fetchone()
    return row if isinstance(row, dict) else None


def update_session_extract_fields(
    run_id: str,
    summary_text: str | None = None,
    tags: str | None = None,
    outcome: str | None = None,
) -> bool:
    """Update extraction-derived fields for one indexed session row."""
    if not run_id:
        return False
    _ensure_sessions_db_initialized()

    updates: list[str] = []
    params: list[Any] = []
    if summary_text is not None:
        updates.append("summary_text = ?")
        params.append(summary_text)
    if tags is not None:
        updates.append("tags = ?")
        params.append(tags)
    if outcome is not None:
        updates.append("outcome = ?")
        params.append(outcome)
    if not updates:
        return False

    params.append(run_id)
    with _connect() as conn:
        cursor = conn.execute(
            f"UPDATE session_docs SET {', '.join(updates)} WHERE run_id = ?", params
        )
        conn.commit()
    return int(cursor.rowcount or 0) > 0


def count_fts_indexed() -> int:
    """Return count of indexed session documents."""
    _ensure_sessions_db_initialized()
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(1) AS total FROM session_docs").fetchone()
    return int((row or {}).get("total") or 0)


def get_indexed_run_ids() -> set[str]:
    """Return the set of run IDs already indexed in session docs."""
    _ensure_sessions_db_initialized()
    with _connect() as conn:
        rows = conn.execute("SELECT run_id FROM session_docs").fetchall()
    return {str(row.get("run_id")) for row in rows if row.get("run_id")}


def _get_indexed_content_hashes() -> dict[str, str | None]:
    """Return indexed session content hashes keyed by run id."""
    _ensure_sessions_db_initialized()
    with _connect() as conn:
        rows = conn.execute("SELECT run_id, content_hash FROM session_docs").fetchall()
    return {
        str(row.get("run_id")): row.get("content_hash")
        for row in rows
        if row.get("run_id")
    }


def list_sessions_window(
    *,
    limit: int = 100,
    offset: int = 0,
    agent_types: list[str] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """List sessions in a filtered window plus total row count."""
    _ensure_sessions_db_initialized()
    where: list[str] = []
    params: list[Any] = []

    if agent_types:
        placeholders = ",".join("?" for _ in agent_types)
        where.append(f"agent_type IN ({placeholders})")
        params.extend(agent_types)
    if since is not None:
        where.append("(start_time >= ? OR start_time IS NULL)")
        params.append(_to_iso(since))
    if until is not None:
        where.append("(start_time <= ? OR start_time IS NULL)")
        params.append(_to_iso(until))

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    limit = max(1, int(limit))
    offset = max(0, int(offset))

    with _connect() as conn:
        total_row = conn.execute(
            f"SELECT COUNT(1) AS total FROM session_docs {where_sql}",
            params,
        ).fetchone()
        rows = conn.execute(
            f"""
            SELECT *
            FROM session_docs
            {where_sql}
            ORDER BY start_time DESC, indexed_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()
    return rows, int((total_row or {}).get("total") or 0)


def reset_indexed_sessions_for_project(repo_path: str) -> dict[str, int]:
    """Delete indexed session and queue state for one registered project path."""
    _ensure_sessions_db_initialized()
    normalized = str(Path(repo_path).expanduser().resolve())
    with _connect() as conn:
        counts = _count_project_session_state(conn, normalized)
        _delete_project_session_jobs(conn, normalized)
        conn.execute("DELETE FROM session_docs WHERE repo_path = ?", (normalized,))
        conn.commit()
    return counts


def count_indexed_sessions_for_project(repo_path: str) -> dict[str, int]:
    """Count indexed session and queue state for one registered project path."""
    _ensure_sessions_db_initialized()
    normalized = str(Path(repo_path).expanduser().resolve())
    with _connect() as conn:
        return _count_project_session_state(conn, normalized)


def reset_all_session_state() -> dict[str, int]:
    """Delete all indexed session, queue, and service-run state."""
    _ensure_sessions_db_initialized()
    with _connect() as conn:
        counts = _count_all_session_state(conn)
        conn.execute("DELETE FROM session_jobs")
        conn.execute("DELETE FROM session_docs")
        conn.execute("DELETE FROM service_runs")
        conn.commit()
    return counts


def count_all_session_state() -> dict[str, int]:
    """Count all indexed session, queue, and service-run state."""
    _ensure_sessions_db_initialized()
    with _connect() as conn:
        return _count_all_session_state(conn)


def _count_project_session_state(
    conn: sqlite3.Connection, repo_path: str
) -> dict[str, int]:
    """Count session catalog rows that belong to repo_path."""
    rows = conn.execute(
        "SELECT run_id FROM session_docs WHERE repo_path = ?",
        (repo_path,),
    ).fetchall()
    run_ids = [str(row.get("run_id") or "") for row in rows if row.get("run_id")]
    if run_ids:
        placeholders = ",".join("?" for _ in run_ids)
        job_count = int(
            conn.execute(
                f"SELECT COUNT(1) AS total FROM session_jobs WHERE repo_path = ? OR run_id IN ({placeholders})",
                (repo_path, *run_ids),
            ).fetchone()["total"]
            or 0
        )
    else:
        job_count = int(
            conn.execute(
                "SELECT COUNT(1) AS total FROM session_jobs WHERE repo_path = ?",
                (repo_path,),
            ).fetchone()["total"]
            or 0
        )
    return {
        "indexed_sessions": len(run_ids),
        "session_jobs": job_count,
        "service_runs": 0,
    }


def _delete_project_session_jobs(conn: sqlite3.Connection, repo_path: str) -> None:
    """Delete session jobs for repo_path, including jobs linked by indexed run id."""
    rows = conn.execute(
        "SELECT run_id FROM session_docs WHERE repo_path = ?",
        (repo_path,),
    ).fetchall()
    run_ids = [str(row.get("run_id") or "") for row in rows if row.get("run_id")]
    if not run_ids:
        conn.execute("DELETE FROM session_jobs WHERE repo_path = ?", (repo_path,))
        return
    placeholders = ",".join("?" for _ in run_ids)
    conn.execute(
        f"DELETE FROM session_jobs WHERE repo_path = ? OR run_id IN ({placeholders})",
        (repo_path, *run_ids),
    )


def _count_all_session_state(conn: sqlite3.Connection) -> dict[str, int]:
    """Count all session catalog rows."""
    indexed_count = int(
        conn.execute("SELECT COUNT(1) AS total FROM session_docs").fetchone()["total"]
        or 0
    )
    job_count = int(
        conn.execute("SELECT COUNT(1) AS total FROM session_jobs").fetchone()["total"]
        or 0
    )
    service_run_count = int(
        conn.execute("SELECT COUNT(1) AS total FROM service_runs").fetchone()["total"]
        or 0
    )
    return {
        "indexed_sessions": indexed_count,
        "session_jobs": job_count,
        "service_runs": service_run_count,
    }


def index_new_sessions(
    *,
    agents: list[str] | None = None,
    return_details: bool = False,
    start: datetime | None = None,
    end: datetime | None = None,
    projects: dict[str, str] | None = None,
    skip_unscoped: bool = False,
    stats: dict[str, int] | None = None,
) -> int | list[IndexedSession]:
    """Discover and index new sessions from connected adapters.

    Known sessions with the same content hash are skipped. New sessions are
    returned with ``changed=False``; known sessions with a missing or different
    stored hash are returned with ``changed=True`` so extraction can backfill or
    refresh their DB context.

    When ``skip_unscoped=True`` and ``projects`` is provided, sessions whose
    ``repo_path`` does not map to a registered project are ignored and counted
    under ``stats['skipped_unscoped']``.
    """
    _ensure_sessions_db_initialized()
    config = get_config()

    connected_paths = adapter_registry.get_connected_platform_paths(
        config.platforms_path
    )
    selected_agents = agents or adapter_registry.get_connected_agents(
        config.platforms_path
    )
    known_ids = get_indexed_run_ids()
    known_hashes = _get_indexed_content_hashes()

    new_sessions: list[IndexedSession] = []

    for agent_name in selected_agents:
        adapter = adapter_registry.get_adapter(agent_name)
        traces_dir = connected_paths.get(agent_name)
        if adapter is None or traces_dir is None:
            continue

        try:
            sessions = adapter.iter_sessions(
                traces_dir=traces_dir,
                start=start,
                end=end,
                known_run_ids=None,
            )
        except Exception as exc:
            logger.warning(
                "session discovery failed | agent={} error={}", agent_name, str(exc)
            )
            continue

        for session in sessions:
            if skip_unscoped:
                if (
                    not projects
                    or match_session_project(session.repo_path, projects) is None
                ):
                    if stats is not None:
                        stats["skipped_unscoped"] = (
                            int(stats.get("skipped_unscoped") or 0) + 1
                        )
                    continue

            content_hash = getattr(session, "content_hash", None)
            previous_hash = known_hashes.get(session.run_id)
            is_known = session.run_id in known_ids
            if is_known and content_hash is not None and previous_hash == content_hash:
                continue
            is_changed = is_known

            summaries_json = json.dumps(session.summaries, ensure_ascii=True)
            summary_text = "\n".join(item for item in session.summaries if item)
            content = summary_text
            if not content:
                content = f"run:{session.run_id} agent:{session.agent_type}"

            indexed = index_session_for_fts(
                run_id=session.run_id,
                agent_type=session.agent_type,
                content=content,
                repo_path=session.repo_path,
                repo_name=session.repo_name,
                start_time=session.start_time,
                status=session.status,
                duration_ms=session.duration_ms,
                message_count=session.message_count,
                tool_call_count=session.tool_call_count,
                error_count=session.error_count,
                total_tokens=session.total_tokens,
                summaries=summaries_json,
                summary_text=summary_text,
                session_path=session.session_path,
                content_hash=content_hash,
            )
            if not indexed:
                continue

            known_ids.add(session.run_id)
            known_hashes[session.run_id] = content_hash
            new_sessions.append(
                IndexedSession(
                    run_id=session.run_id,
                    agent_type=session.agent_type,
                    session_path=session.session_path,
                    start_time=session.start_time,
                    repo_path=session.repo_path,
                    changed=is_changed,
                )
            )

    # Sort chronologically (oldest first) so later sessions can update earlier ones.
    new_sessions.sort(key=lambda s: s.start_time or "")
    return new_sessions if return_details else len(new_sessions)


def enqueue_session_job(
    run_id: str,
    *,
    job_type: str = JOB_TYPE_EXTRACT,
    agent_type: str | None = None,
    session_path: str | None = None,
    start_time: str | None = None,
    trigger: str | None = None,
    force: bool = False,
    max_attempts: int = 3,
    repo_path: str | None = None,
) -> bool:
    """Create or reset one queue job for session extraction."""
    if not run_id:
        return False
    if not repo_path:
        return False
    _ensure_sessions_db_initialized()
    now = _iso_now()

    with _connect() as conn:
        existing = conn.execute(
            "SELECT status FROM session_jobs WHERE run_id = ? AND job_type = ?",
            (run_id, job_type),
        ).fetchone()

        if (
            existing
            and not force
            and str(existing.get("status") or "")
            in SESSION_JOB_ACTIVE.union({JOB_STATUS_DONE})
        ):
            return False

        if existing:
            conn.execute(
                """
                UPDATE session_jobs
                SET agent_type = ?, session_path = ?, start_time = ?, status = ?,
                    attempts = 0, trigger = ?, available_at = ?, claimed_at = NULL,
                    completed_at = NULL, heartbeat_at = NULL, error = NULL,
                    updated_at = ?, max_attempts = ?, repo_path = ?
                WHERE run_id = ? AND job_type = ?
                """,
                (
                    agent_type,
                    session_path,
                    start_time,
                    JOB_STATUS_PENDING,
                    trigger,
                    now,
                    now,
                    max(1, int(max_attempts)),
                    repo_path,
                    run_id,
                    job_type,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO session_jobs (
                    run_id, job_type, agent_type, session_path, start_time, status,
                    attempts, max_attempts, trigger, available_at, created_at,
                    updated_at, repo_path
                )
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    job_type,
                    agent_type,
                    session_path,
                    start_time,
                    JOB_STATUS_PENDING,
                    max(1, int(max_attempts)),
                    trigger,
                    now,
                    now,
                    now,
                    repo_path,
                ),
            )
        conn.commit()
    return True


def claim_session_jobs(
    *,
    limit: int = 20,
    run_ids: list[str] | None = None,
    job_type: str = JOB_TYPE_EXTRACT,
    claim_order: str = SESSION_JOB_CLAIM_NEWEST,
) -> list[dict[str, Any]]:
    """Claim available jobs and mark claimed rows as running.

    Normal backlog extraction claims the newest session per project first so a
    fresh install surfaces recent corrections quickly.  Explicit historical
    replay can pass ``claim_order="oldest"`` to preserve chronological
    extraction semantics.
    """
    _ensure_sessions_db_initialized()
    limit = max(1, int(limit))
    if claim_order not in SESSION_JOB_CLAIM_ORDERS:
        raise ValueError(
            f"claim_order must be one of {sorted(SESSION_JOB_CLAIM_ORDERS)!r}"
        )
    now = _utc_now()
    now_iso = now.isoformat()
    if claim_order == SESSION_JOB_CLAIM_OLDEST:
        per_project_order = (
            "CASE WHEN start_time IS NULL OR start_time = '' THEN 1 ELSE 0 END ASC, "
            "start_time ASC, available_at ASC, id ASC"
        )
        global_order = (
            "CASE WHEN start_time IS NULL OR start_time = '' THEN 1 ELSE 0 END ASC, "
            "start_time ASC, available_at ASC, id ASC"
        )
    else:
        per_project_order = (
            "CASE WHEN start_time IS NULL OR start_time = '' THEN 1 ELSE 0 END ASC, "
            "start_time DESC, available_at ASC, id DESC"
        )
        global_order = (
            "CASE WHEN start_time IS NULL OR start_time = '' THEN 1 ELSE 0 END ASC, "
            "start_time DESC, available_at ASC, id DESC"
        )

    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")

        # Per-project ordered claiming: partition by repo_path, pick only one
        # active job per project using the requested backlog policy.  Normal
        # sync uses newest-first for first-run quality; chronological replay
        # callers can request oldest-first explicitly.
        #
        # IMPORTANT: The run_id filter is applied before ranking so targeted
        # historical jobs can be claimed even when newer project rows exist.
        # Dead-letter blockers are checked per project for backlog claims.
        # Explicit run_id claims are operator-selected and should stay claimable
        # so one unrelated dead letter cannot block targeted debugging/retry.
        run_id_filter = ""
        params: list[Any] = [
            JOB_STATUS_DEAD_LETTER,
            job_type,
            JOB_STATUS_PENDING,
            JOB_STATUS_FAILED,
            job_type,
            now_iso,
        ]

        if run_ids:
            placeholders = ",".join("?" for _ in run_ids)
            run_id_filter = f"AND run_id IN ({placeholders})"
            params.extend(run_ids)
        params.extend([JOB_STATUS_PENDING, JOB_STATUS_FAILED, limit])

        dead_letter_blocker = ""
        if not run_ids:
            dead_letter_blocker = """
              AND NOT EXISTS (
                SELECT 1
                FROM dead_letter_projects
                WHERE dead_letter_projects.repo_path = ranked_per_project.repo_path
              )
            """

        job_columns = (
            "id, run_id, job_type, agent_type, session_path, start_time, status, "
            "attempts, max_attempts, trigger, available_at, claimed_at, "
            "completed_at, heartbeat_at, error, created_at, updated_at, repo_path"
        )

        rows = conn.execute(
            f"""
            WITH dead_letter_projects AS (
                SELECT DISTINCT repo_path
                FROM session_jobs
                WHERE status = ?
                  AND job_type = ?
                  AND repo_path IS NOT NULL AND repo_path != ''
            ),
            ranked_per_project AS (
                SELECT {job_columns}, ROW_NUMBER() OVER (
                    PARTITION BY repo_path
                    ORDER BY {per_project_order}
                ) AS rn
                FROM session_jobs
                WHERE status IN (?, ?)
                  AND job_type = ?
                  AND available_at <= ?
                  AND repo_path IS NOT NULL AND repo_path != ''
                  {run_id_filter}
            )
            SELECT {job_columns} FROM ranked_per_project
            WHERE rn = 1
              AND status IN (?, ?)
              {dead_letter_blocker}
            ORDER BY {global_order}
            LIMIT ?
            """,
            params,
        ).fetchall()

        claimed: list[dict[str, Any]] = []
        for row in rows:
            job_id = int(row.get("id") or 0)
            attempts = int(row.get("attempts") or 0) + 1
            conn.execute(
                """
                UPDATE session_jobs
                SET status = ?, attempts = ?, claimed_at = ?, heartbeat_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (JOB_STATUS_RUNNING, attempts, now_iso, now_iso, now_iso, job_id),
            )
            row["attempts"] = attempts
            row["status"] = JOB_STATUS_RUNNING
            row["claimed_at"] = now_iso
            row["heartbeat_at"] = now_iso
            row["updated_at"] = now_iso
            claimed.append(row)

        conn.commit()
    return claimed


def heartbeat_session_job(run_id: str, *, job_type: str = JOB_TYPE_EXTRACT) -> bool:
    """Refresh the lease timestamp for one running queue job."""
    if not run_id:
        return False
    _ensure_sessions_db_initialized()
    now = _iso_now()
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE session_jobs
            SET heartbeat_at = ?, updated_at = ?
            WHERE run_id = ? AND job_type = ? AND status = ?
            """,
            (now, now, run_id, job_type, JOB_STATUS_RUNNING),
        )
        conn.commit()
    return int(cursor.rowcount or 0) > 0


def complete_session_job(run_id: str, *, job_type: str = JOB_TYPE_EXTRACT) -> bool:
    """Mark one queue job as completed."""
    if not run_id:
        return False
    _ensure_sessions_db_initialized()
    now = _iso_now()
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE session_jobs
            SET status = ?, completed_at = ?, updated_at = ?
            WHERE run_id = ? AND job_type = ?
            """,
            (JOB_STATUS_DONE, now, now, run_id, job_type),
        )
        conn.commit()
    return int(cursor.rowcount or 0) > 0


def fail_session_job(
    run_id: str,
    *,
    error: str,
    retry_backoff_seconds: int = 60,
    job_type: str = JOB_TYPE_EXTRACT,
    require_status: str | None = None,
) -> bool:
    """Record job failure and schedule retry or dead-letter transition."""
    if not run_id:
        return False
    _ensure_sessions_db_initialized()
    now = _utc_now()
    now_iso = now.isoformat()

    with _connect() as conn:
        where = "run_id = ? AND job_type = ?"
        params: list[Any] = [run_id, job_type]
        if require_status:
            where += " AND status = ?"
            params.append(require_status)

        row = conn.execute(
            f"SELECT attempts, max_attempts FROM session_jobs WHERE {where}",
            params,
        ).fetchone()
        if not row:
            return False

        attempts = int(row.get("attempts") or 0)
        max_attempts = int(row.get("max_attempts") or 3)
        exhausted = attempts >= max_attempts
        status = JOB_STATUS_DEAD_LETTER if exhausted else JOB_STATUS_FAILED
        available_at = (
            now
            if exhausted
            else now + timedelta(seconds=max(1, int(retry_backoff_seconds)))
        )

        update_params: list[Any] = [
            status,
            available_at.isoformat(),
            now_iso if exhausted else None,
            now_iso,
            error,
            run_id,
            job_type,
        ]
        update_where = "run_id = ? AND job_type = ?"
        if require_status:
            update_where += " AND status = ?"
            update_params.append(require_status)

        cursor = conn.execute(
            f"""
            UPDATE session_jobs
            SET status = ?, available_at = ?, completed_at = ?,
                updated_at = ?, error = ?
            WHERE {update_where}
            """,
            update_params,
        )
        conn.commit()
    return int(cursor.rowcount or 0) > 0


def list_stale_running_jobs(
    *,
    lease_seconds: int = DEFAULT_RUNNING_JOB_LEASE_SECONDS,
    job_type: str = JOB_TYPE_EXTRACT,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """List running jobs whose lease expired based on their latest heartbeat."""
    _ensure_sessions_db_initialized()
    effective_lease = max(1, int(lease_seconds))
    cutoff = (_utc_now() - timedelta(seconds=effective_lease)).isoformat()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, run_id, attempts, claimed_at, repo_path
            FROM session_jobs
            WHERE status = ? AND job_type = ?
              AND COALESCE(heartbeat_at, claimed_at) IS NOT NULL
              AND COALESCE(heartbeat_at, claimed_at) <= ?
            ORDER BY claimed_at ASC, id ASC
            LIMIT ?
            """,
            (JOB_STATUS_RUNNING, job_type, cutoff, max(1, int(limit))),
        ).fetchall()
    return rows


def reap_stale_running_jobs(
    *,
    lease_seconds: int = DEFAULT_RUNNING_JOB_LEASE_SECONDS,
    retry_backoff_fn: Callable[[int], int] | None = None,
    job_type: str = JOB_TYPE_EXTRACT,
) -> int:
    """Fail stale running jobs through the existing retry/dead-letter path."""
    stale = list_stale_running_jobs(
        lease_seconds=lease_seconds,
        job_type=job_type,
        limit=500,
    )
    if not stale:
        return 0

    recovered = 0
    for row in stale:
        run_id = str(row.get("run_id") or "")
        if not run_id:
            continue
        attempts = max(int(row.get("attempts") or 1), 1)
        retry_backoff_seconds = (
            max(1, int(retry_backoff_fn(attempts))) if retry_backoff_fn else 60
        )
        ok = fail_session_job(
            run_id,
            error=f"stale running lease expired after {max(1, int(lease_seconds))}s",
            retry_backoff_seconds=retry_backoff_seconds,
            job_type=job_type,
            require_status=JOB_STATUS_RUNNING,
        )
        if ok:
            recovered += 1
    return recovered


def queue_health_snapshot(
    *,
    lease_seconds: int = DEFAULT_RUNNING_JOB_LEASE_SECONDS,
) -> dict[str, Any]:
    """Return queue health indicators for CLI/API visibility."""
    _ensure_sessions_db_initialized()
    now = _utc_now()
    effective_lease = max(1, int(lease_seconds))
    cutoff = (now - timedelta(seconds=effective_lease)).isoformat()

    counts = count_session_jobs_by_status()
    dead_letter_count = int(counts.get(JOB_STATUS_DEAD_LETTER, 0))

    with _connect() as conn:
        stale_row = conn.execute(
            """
            SELECT COUNT(1) AS total
            FROM session_jobs
            WHERE status = ?
              AND COALESCE(heartbeat_at, claimed_at) IS NOT NULL
              AND COALESCE(heartbeat_at, claimed_at) <= ?
            """,
            (JOB_STATUS_RUNNING, cutoff),
        ).fetchone()
        oldest_running_row = conn.execute(
            """
            SELECT COALESCE(heartbeat_at, claimed_at) AS lease_at
            FROM session_jobs
            WHERE status = ? AND COALESCE(heartbeat_at, claimed_at) IS NOT NULL
            ORDER BY COALESCE(heartbeat_at, claimed_at) ASC, id ASC
            LIMIT 1
            """,
            (JOB_STATUS_RUNNING,),
        ).fetchone()
        oldest_dead_row = conn.execute(
            """
            SELECT updated_at
            FROM session_jobs
            WHERE status = ?
            ORDER BY updated_at ASC, id ASC
            LIMIT 1
            """,
            (JOB_STATUS_DEAD_LETTER,),
        ).fetchone()

    stale_running_count = int((stale_row or {}).get("total") or 0)
    oldest_running_at = _parse_iso((oldest_running_row or {}).get("lease_at"))
    oldest_dead_at = _parse_iso((oldest_dead_row or {}).get("updated_at"))

    oldest_running_age_seconds = (
        max(0, int((now - oldest_running_at).total_seconds()))
        if oldest_running_at
        else None
    )
    oldest_dead_letter_age_seconds = (
        max(0, int((now - oldest_dead_at).total_seconds())) if oldest_dead_at else None
    )

    degraded = stale_running_count > 0 or dead_letter_count > 0
    advice_parts: list[str] = []
    if stale_running_count > 0:
        advice_parts.append("run `lerim sync` to trigger stale-job recovery")
    if dead_letter_count > 0:
        advice_parts.append(
            "inspect with `lerim queue --failed`, then `lerim retry <run_id>` or `lerim skip <run_id>`"
        )
    advice = "; ".join(advice_parts)

    return {
        "degraded": degraded,
        "stale_running_count": stale_running_count,
        "dead_letter_count": dead_letter_count,
        "oldest_running_age_seconds": oldest_running_age_seconds,
        "oldest_dead_letter_age_seconds": oldest_dead_letter_age_seconds,
        "advice": advice,
    }


def list_session_jobs(
    *,
    limit: int = 100,
    status: str | None = None,
    job_type: str | None = None,
) -> list[dict[str, Any]]:
    """List queue jobs with optional status/job-type filters."""
    _ensure_sessions_db_initialized()
    limit = max(1, int(limit))
    where: list[str] = []
    params: list[Any] = []

    if status:
        where.append("status = ?")
        params.append(status)
    if job_type:
        where.append("job_type = ?")
        params.append(job_type)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM session_jobs
            {where_sql}
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return rows


def count_session_jobs_by_status() -> dict[str, int]:
    """Return queue counts keyed by status with zero-filled defaults."""
    _ensure_sessions_db_initialized()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(1) AS total FROM session_jobs GROUP BY status"
        ).fetchall()
    counts = {
        str(row.get("status") or "unknown"): int(row.get("total") or 0) for row in rows
    }
    for status in (
        JOB_STATUS_PENDING,
        JOB_STATUS_RUNNING,
        JOB_STATUS_DONE,
        JOB_STATUS_FAILED,
        JOB_STATUS_DEAD_LETTER,
    ):
        counts.setdefault(status, 0)
    return counts


# ── Queue management: retry / skip / inspect ─────────────────────────


def _transition_dead_letter(
    *,
    new_status: str,
    reset_attempts: bool,
    where_clause: str,
    params: list[Any],
) -> int:
    """Transition dead_letter jobs to *new_status*. Returns rows affected.

    When *reset_attempts* is True the job is fully reset (attempts, error,
    claimed_at, completed_at, heartbeat_at cleared) for a fresh retry.
    Otherwise only status, completed_at, and updated_at are touched (skip).
    """
    _ensure_sessions_db_initialized()
    now = _iso_now()
    if reset_attempts:
        sql = f"""
			UPDATE session_jobs
			SET status = ?, attempts = 0, available_at = ?, error = NULL,
				claimed_at = NULL, completed_at = NULL, heartbeat_at = NULL,
				updated_at = ?
			{where_clause}
		"""
        all_params = [new_status, now, now, *params]
    else:
        sql = f"""
			UPDATE session_jobs
			SET status = ?, completed_at = ?, updated_at = ?
			{where_clause}
		"""
        all_params = [new_status, now, now, *params]
    with _connect() as conn:
        cursor = conn.execute(sql, all_params)
        conn.commit()
    return int(cursor.rowcount or 0)


def retry_session_job(
    run_id: str,
    *,
    job_type: str = JOB_TYPE_EXTRACT,
) -> bool:
    """Reset a dead_letter job to pending for retry.

    The ``AND status = 'dead_letter'`` guard is atomic -- safe against
    concurrent daemon claiming.
    """
    if not run_id:
        return False
    return (
        _transition_dead_letter(
            new_status=JOB_STATUS_PENDING,
            reset_attempts=True,
            where_clause="WHERE run_id = ? AND job_type = ? AND status = ?",
            params=[run_id, job_type, JOB_STATUS_DEAD_LETTER],
        )
        > 0
    )


def skip_session_job(
    run_id: str,
    *,
    job_type: str = JOB_TYPE_EXTRACT,
) -> bool:
    """Mark a dead_letter job as done (skipped), unblocking the project."""
    if not run_id:
        return False
    return (
        _transition_dead_letter(
            new_status=JOB_STATUS_DONE,
            reset_attempts=False,
            where_clause="WHERE run_id = ? AND job_type = ? AND status = ?",
            params=[run_id, job_type, JOB_STATUS_DEAD_LETTER],
        )
        > 0
    )


def retry_project_jobs(
    repo_path: str,
    *,
    job_type: str = JOB_TYPE_EXTRACT,
) -> int:
    """Retry all dead_letter jobs for a project. Returns count affected."""
    if not repo_path:
        return 0
    return _transition_dead_letter(
        new_status=JOB_STATUS_PENDING,
        reset_attempts=True,
        where_clause="WHERE repo_path = ? AND job_type = ? AND status = ?",
        params=[repo_path, job_type, JOB_STATUS_DEAD_LETTER],
    )


def skip_project_jobs(
    repo_path: str,
    *,
    job_type: str = JOB_TYPE_EXTRACT,
) -> int:
    """Skip all dead_letter jobs for a project. Returns count affected."""
    if not repo_path:
        return 0
    return _transition_dead_letter(
        new_status=JOB_STATUS_DONE,
        reset_attempts=False,
        where_clause="WHERE repo_path = ? AND job_type = ? AND status = ?",
        params=[repo_path, job_type, JOB_STATUS_DEAD_LETTER],
    )


def retry_all_dead_letter_jobs(*, job_type: str = JOB_TYPE_EXTRACT) -> int:
    """Retry all dead_letter jobs without applying display-list pagination."""
    return _transition_dead_letter(
        new_status=JOB_STATUS_PENDING,
        reset_attempts=True,
        where_clause="WHERE job_type = ? AND status = ?",
        params=[job_type, JOB_STATUS_DEAD_LETTER],
    )


def skip_all_dead_letter_jobs(*, job_type: str = JOB_TYPE_EXTRACT) -> int:
    """Skip all dead_letter jobs without applying display-list pagination."""
    return _transition_dead_letter(
        new_status=JOB_STATUS_DONE,
        reset_attempts=False,
        where_clause="WHERE job_type = ? AND status = ?",
        params=[job_type, JOB_STATUS_DEAD_LETTER],
    )


def resolve_run_id_prefix(prefix: str) -> str | None:
    """Resolve a short prefix to a full run_id.

    Returns the full run_id if exactly one match, None otherwise.
    Requires at least 6 characters to avoid overly broad matches.
    """
    if not prefix or len(prefix) < 6:
        return None
    _ensure_sessions_db_initialized()
    # Escape LIKE wildcards so user-supplied % or _ are matched literally.
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    with _connect() as conn:
        rows = conn.execute(
            "SELECT run_id FROM session_jobs WHERE run_id LIKE ? ESCAPE '\\' LIMIT 2",
            (escaped + "%",),
        ).fetchall()
    if len(rows) == 1:
        return str(rows[0]["run_id"])
    return None


def list_queue_jobs(
    *,
    status_filter: str | None = None,
    project_filter: str | None = None,
    project_exact: bool = False,
    failed_only: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List queue jobs with optional filters for CLI display.

    When *failed_only* is True, shows only failed + dead_letter jobs.
    """
    _ensure_sessions_db_initialized()
    where: list[str] = []
    params: list[Any] = []

    if failed_only:
        where.append("status IN (?, ?)")
        params.extend([JOB_STATUS_FAILED, JOB_STATUS_DEAD_LETTER])
    elif status_filter:
        where.append("status = ?")
        params.append(status_filter)
    else:
        # Default: show non-done jobs
        where.append("status != ?")
        params.append(JOB_STATUS_DONE)

    if project_filter:
        if project_exact:
            where.append("repo_path = ?")
            params.append(project_filter)
        else:
            where.append("repo_path LIKE ?")
            params.append(f"%{project_filter}%")

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    with _connect() as conn:
        rows = conn.execute(
            f"""
			SELECT run_id, status, agent_type, repo_path, start_time,
				   error, attempts, max_attempts, updated_at
			FROM session_jobs
			{where_sql}
			ORDER BY start_time ASC, id ASC
			LIMIT ?
			""",
            [*params, max(1, limit)],
        ).fetchall()
    return rows


def count_unscoped_sessions_by_agent(
    *,
    projects: dict[str, str],
) -> dict[str, int]:
    """Count indexed sessions that do not map to any registered project."""
    _ensure_sessions_db_initialized()
    with _connect() as conn:
        rows = conn.execute(
            """
			SELECT agent_type, repo_path
			FROM session_docs
			"""
        ).fetchall()

    counts: dict[str, int] = {}
    for row in rows:
        repo_path = str(row.get("repo_path") or "").strip()
        if match_session_project(repo_path, projects) is not None:
            continue
        agent = str(row.get("agent_type") or "unknown")
        counts[agent] = counts.get(agent, 0) + 1
    return counts


def list_unscoped_sessions(
    *,
    projects: dict[str, str],
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List indexed sessions that do not map to any registered project."""
    _ensure_sessions_db_initialized()
    with _connect() as conn:
        rows = conn.execute(
            """
			SELECT run_id, agent_type, repo_path, start_time, session_path
			FROM session_docs
			ORDER BY start_time DESC, indexed_at DESC
			LIMIT ?
			""",
            (max(1, int(limit)) * 10,),
        ).fetchall()

    items: list[dict[str, Any]] = []
    for row in rows:
        repo_path = str(row.get("repo_path") or "").strip()
        if match_session_project(repo_path, projects) is not None:
            continue
        items.append(
            {
                "run_id": row.get("run_id"),
                "agent_type": row.get("agent_type"),
                "repo_path": repo_path or None,
                "start_time": row.get("start_time"),
                "session_path": row.get("session_path"),
            }
        )
        if len(items) >= max(1, int(limit)):
            break
    return items


def record_service_run(
    *,
    job_type: str,
    status: str,
    started_at: str,
    completed_at: str | None,
    trigger: str | None,
    details: dict[str, Any] | None,
) -> int:
    """Insert one service-run audit row and return inserted id."""
    _ensure_sessions_db_initialized()
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO service_runs (job_type, status, started_at, completed_at, trigger, details_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                job_type,
                status,
                started_at,
                completed_at,
                trigger,
                json.dumps(details or {}, ensure_ascii=True),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid or 0)


def latest_service_run(job_type: str) -> dict[str, Any] | None:
    """Return most recent recorded service run for the requested job type."""
    if not job_type:
        return None
    _ensure_sessions_db_initialized()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id, job_type, status, started_at, completed_at, trigger, details_json
            FROM service_runs
            WHERE job_type = ?
            ORDER BY started_at DESC, id DESC
            LIMIT 1
            """,
            (job_type,),
        ).fetchone()
    if not row:
        return None
    details_raw = row.get("details_json")
    try:
        details = json.loads(details_raw) if details_raw else {}
    except (json.JSONDecodeError, TypeError):
        details = {}
    return {
        "id": row.get("id"),
        "job_type": row.get("job_type"),
        "status": row.get("status"),
        "started_at": row.get("started_at"),
        "completed_at": row.get("completed_at"),
        "trigger": row.get("trigger"),
        "details": details,
    }


def list_service_runs(*, limit: int = 20) -> list[dict[str, Any]]:
    """Return latest service runs across all job types, newest first."""
    _ensure_sessions_db_initialized()
    safe_limit = max(1, int(limit))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, job_type, status, started_at, completed_at, trigger, details_json
            FROM service_runs
            ORDER BY started_at DESC, id DESC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        details_raw = row.get("details_json")
        try:
            details = json.loads(details_raw) if details_raw else {}
        except (json.JSONDecodeError, TypeError):
            details = {}
        items.append(
            {
                "id": row.get("id"),
                "job_type": row.get("job_type"),
                "status": row.get("status"),
                "started_at": row.get("started_at"),
                "completed_at": row.get("completed_at"),
                "trigger": row.get("trigger"),
                "details": details,
            }
        )
    return items


if __name__ == "__main__":
    prev_cfg = os.getenv("LERIM_CONFIG")
    try:
        with tempfile.TemporaryDirectory(prefix="lerim-catalog-selftest-") as tmp:
            cfg_path = Path(tmp) / "test_config.toml"
            cfg_path.write_text(
                f'[data]\ndir = "{tmp}"\n',
                encoding="utf-8",
            )
            os.environ["LERIM_CONFIG"] = str(cfg_path)
            reload_config()
            init_sessions_db()
            run_id = "selftest-run"
            queued = enqueue_session_job(run_id, force=True, repo_path=tmp)
            claimed = claim_session_jobs(limit=1, run_ids=[run_id])
            assert queued
            assert claimed and str(claimed[0].get("run_id")) == run_id
            assert complete_session_job(run_id)
            counts = count_session_jobs_by_status()
            assert counts.get(JOB_STATUS_DONE, 0) >= 1
    finally:
        if prev_cfg is None:
            os.environ.pop("LERIM_CONFIG", None)
        else:
            os.environ["LERIM_CONFIG"] = prev_cfg
        reload_config()
