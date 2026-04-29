"""Canonical SQLite context store for Lerim.

This module keeps the durable context model intentionally small:

- one global context database
- one honest records table
- one versions table for history
- derived embedding + FTS tables for retrieval
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import sqlite_vec

from lerim.context.embedding import get_embedding_provider
from lerim.context.project_identity import ProjectIdentity
from lerim.context.query_spec import (
    QUERY_ENTITIES as QUERY_ENTITIES,
    QUERY_MODES as QUERY_MODES,
    QUERY_ORDER_FIELDS as QUERY_ORDER_FIELDS,
)
import lerim.context.retrieval as retrieval
from lerim.context.spec import (
    ALLOWED_CHANGE_KINDS,
    normalize_record_payload,
    record_search_text,
)

if TYPE_CHECKING:
    from lerim.context.retrieval import SearchHit

SCHEMA_VERSION = "2"
LOGGER = logging.getLogger(__name__)
TIMESTAMP_COLUMNS = {
    "projects": ("created_at", "updated_at"),
    "sessions": ("started_at", "created_at"),
    "records": ("created_at", "updated_at", "valid_from", "valid_until"),
    "record_versions": (
        "created_at",
        "updated_at",
        "valid_from",
        "valid_until",
        "changed_at",
    ),
}


def _utc_now() -> str:
    """Return current UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _normalize_datetime_utc(value: str | None) -> str | None:
    """Normalize a parseable ISO timestamp to UTC ISO text."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _normalize_datetime_filter_bound(value: str | None, *, upper: bool) -> str | None:
    """Expand bare YYYY-MM-DD filters into inclusive UTC day boundaries."""
    text = str(value or "").strip()
    if not text:
        return None
    if "T" in text:
        return _normalize_datetime_utc(text)
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return text
    boundary = time.max if upper else time.min
    return datetime.combine(parsed, boundary, tzinfo=timezone.utc).isoformat()


def _effective_current_valid_at(
    *,
    valid_at: str | None,
    include_archived: bool,
    statuses: list[str] | None,
) -> str | None:
    """Return the timestamp that should define current-record visibility."""
    if valid_at:
        return _normalize_datetime_filter_bound(valid_at, upper=True)
    status_set = {str(status or "").strip() for status in (statuses or []) if str(status or "").strip()}
    if status_set and status_set != {"active"}:
        return None
    if not include_archived:
        return _utc_now()
    if status_set == {"active"}:
        return _utc_now()
    return None


def _parse_iso_utc(raw: str | None) -> datetime | None:
    """Parse one ISO timestamp into an aware UTC datetime when possible."""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _new_id(prefix: str) -> str:
    """Return a short prefixed identifier."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _normalize_optional_text(value: Any) -> str | None:
    """Normalize optional text fields to stripped strings or None."""
    text = str(value or "").strip()
    return text or None


class ContextStore:
    """Canonical global SQLite context store."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Any:
        """Open a SQLite connection with row access and foreign keys enabled."""
        conn = sqlite3.connect(self.db_path, timeout=60.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 60000")
        try:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
        except Exception as exc:
            raise RuntimeError("failed_to_load_sqlite_vec_extension") from exc
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def embedding_provider(self) -> Any:
        """Return the embedding provider used by this store."""
        return get_embedding_provider()

    def initialize(self) -> None:
        """Create all canonical tables and indexes idempotently."""
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    project_slug TEXT NOT NULL,
                    repo_path TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    agent_type TEXT NOT NULL,
                    source_trace_ref TEXT NOT NULL,
                    repo_path TEXT,
                    cwd TEXT,
                    started_at TEXT,
                    model_name TEXT,
                    instructions_text TEXT,
                    prompt_text TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(project_id)
                );

                CREATE TABLE IF NOT EXISTS records (
                    record_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_session_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_until TEXT,
                    superseded_by_record_id TEXT,
                    decision TEXT,
                    why TEXT,
                    alternatives TEXT,
                    consequences TEXT,
                    user_intent TEXT,
                    what_happened TEXT,
                    outcomes TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(project_id),
                    FOREIGN KEY(source_session_id) REFERENCES sessions(session_id),
                    FOREIGN KEY(superseded_by_record_id) REFERENCES records(record_id),
                    CHECK (length(trim(title)) > 0),
                    CHECK (length(trim(body)) > 0)
                );

                CREATE TABLE IF NOT EXISTS record_versions (
                    version_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    version_no INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_session_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_until TEXT,
                    superseded_by_record_id TEXT,
                    decision TEXT,
                    why TEXT,
                    alternatives TEXT,
                    consequences TEXT,
                    user_intent TEXT,
                    what_happened TEXT,
                    outcomes TEXT,
                    change_kind TEXT NOT NULL,
                    change_reason TEXT,
                    changed_at TEXT NOT NULL,
                    changed_by_session_id TEXT,
                    FOREIGN KEY(project_id) REFERENCES projects(project_id),
                    FOREIGN KEY(record_id) REFERENCES records(record_id),
                    FOREIGN KEY(source_session_id) REFERENCES sessions(session_id),
                    FOREIGN KEY(superseded_by_record_id) REFERENCES records(record_id),
                    FOREIGN KEY(changed_by_session_id) REFERENCES sessions(session_id)
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
                    record_id UNINDEXED,
                    project_id UNINDEXED,
                    updated_at UNINDEXED,
                    title,
                    body,
                    decision,
                    why,
                    user_intent,
                    what_happened
                );

                CREATE INDEX IF NOT EXISTS idx_projects_repo_path ON projects(repo_path);
                CREATE INDEX IF NOT EXISTS idx_sessions_project_id ON sessions(project_id);
                CREATE INDEX IF NOT EXISTS idx_records_project_id ON records(project_id);
                CREATE INDEX IF NOT EXISTS idx_records_kind ON records(kind);
                CREATE INDEX IF NOT EXISTS idx_records_status ON records(status);
                CREATE INDEX IF NOT EXISTS idx_records_created_at ON records(created_at);
                CREATE INDEX IF NOT EXISTS idx_records_updated_at ON records(updated_at);
                CREATE INDEX IF NOT EXISTS idx_records_valid_from ON records(valid_from);
                CREATE INDEX IF NOT EXISTS idx_records_source_session_id ON records(source_session_id);
                CREATE INDEX IF NOT EXISTS idx_record_versions_record_id ON record_versions(record_id);
                CREATE INDEX IF NOT EXISTS idx_record_versions_changed_at ON record_versions(changed_at);
                """
            )
            self._validate_schema(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_records_valid_until ON records(valid_until)"
            )
            self._normalize_stored_timestamps(conn)
            conn.execute(
                """
                INSERT INTO schema_meta(key, value)
                VALUES('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (SCHEMA_VERSION,),
            )

    def _validate_schema(self, conn: sqlite3.Connection) -> None:
        """Ensure the on-disk DB matches the simplified canonical schema."""
        required_columns = {
            "projects": {"project_id", "project_slug", "repo_path"},
            "sessions": {"session_id", "project_id", "agent_type", "source_trace_ref"},
            "records": {"record_id", "project_id", "kind", "title", "body", "status"},
            "record_versions": {"version_id", "record_id", "version_no", "change_kind"},
            "records_fts": {
                "record_id",
                "project_id",
                "updated_at",
                "title",
                "body",
                "decision",
                "why",
                "user_intent",
                "what_happened",
            },
        }
        for table_name, expected in required_columns.items():
            rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            actual = {str(row[1]) for row in rows}
            missing = expected - actual
            if missing:
                missing_list = ", ".join(sorted(missing))
                raise sqlite3.OperationalError(
                    f"context schema incompatible: table {table_name} missing columns {missing_list}"
                )
        embeddings_sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'record_embeddings'"
        ).fetchone()
        if embeddings_sql_row is not None:
            embeddings_sql = str(embeddings_sql_row["sql"] or "")
            if "vec0" not in embeddings_sql.lower():
                raise sqlite3.OperationalError("context schema incompatible: record_embeddings is not vec0")
            rows = conn.execute("PRAGMA table_info(record_embeddings)").fetchall()
            actual = {str(row[1]) for row in rows}
            expected = {
                "embedding",
                "project_id",
                "record_id",
                "embedding_model",
                "updated_at",
            }
            missing = expected - actual
            if missing:
                missing_list = ", ".join(sorted(missing))
                raise sqlite3.OperationalError(
                    f"context schema incompatible: table record_embeddings missing columns {missing_list}"
                )

    def _normalize_stored_timestamps(self, conn: sqlite3.Connection) -> None:
        """Canonicalize stored timestamp text so lexicographic filters stay correct."""
        for table_name, column_names in TIMESTAMP_COLUMNS.items():
            columns = ", ".join(["rowid", *column_names])
            rows = conn.execute(f"SELECT {columns} FROM {table_name}").fetchall()
            for row in rows:
                updates: dict[str, str] = {}
                for column_name in column_names:
                    current = row[column_name]
                    normalized = _normalize_datetime_utc(current)
                    if normalized is not None and normalized != current:
                        updates[column_name] = normalized
                if not updates:
                    continue
                set_sql = ", ".join(f"{column_name} = ?" for column_name in updates)
                conn.execute(
                    f"UPDATE {table_name} SET {set_sql} WHERE rowid = ?",
                    tuple(updates.values()) + (row["rowid"],),
                )

    def _ensure_record_embeddings_index(self, conn: sqlite3.Connection) -> bool:
        """Ensure record_embeddings uses sqlite-vec and return whether to rebuild rows."""
        provider = self.embedding_provider()
        expected_fragment = f"float[{provider.embedding_dims}]"
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'record_embeddings'"
        ).fetchone()
        sql = str(row["sql"] or "") if row else ""
        rebuild = False
        if row and ("vec0" not in sql.lower() or expected_fragment not in sql):
            conn.execute("DROP TABLE IF EXISTS record_embeddings")
            row = None
            rebuild = True
        if not row:
            conn.execute(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS record_embeddings USING vec0(
                    embedding float[{provider.embedding_dims}],
                    project_id text,
                    record_id text auxiliary,
                    embedding_model text auxiliary,
                    updated_at text auxiliary
                )
                """
            )
            return True

        current_model = conn.execute(
            "SELECT DISTINCT embedding_model FROM record_embeddings WHERE embedding_model IS NOT NULL"
        ).fetchall()
        current_models = {str(item["embedding_model"]) for item in current_model if item["embedding_model"]}
        if current_models and current_models != {provider.model_id}:
            rebuild = True

        record_count = int(conn.execute("SELECT COUNT(*) FROM records").fetchone()[0])
        embedding_count = int(conn.execute("SELECT COUNT(*) FROM record_embeddings").fetchone()[0])
        if embedding_count != record_count:
            rebuild = True
        rebuild = rebuild or self._stale_embedding_count(conn, provider_model=provider.model_id) > 0
        return rebuild

    def register_project(self, identity: ProjectIdentity) -> dict[str, Any]:
        """Upsert one project row."""
        self.initialize()
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO projects(project_id, project_slug, repo_path, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    project_slug=excluded.project_slug,
                    repo_path=excluded.repo_path,
                    updated_at=excluded.updated_at
                """,
                (
                    identity.project_id,
                    identity.project_slug,
                    str(identity.repo_path),
                    now,
                    now,
                ),
            )
        return {
            "project_id": identity.project_id,
            "project_slug": identity.project_slug,
            "repo_path": str(identity.repo_path),
        }

    def reset_project_memory(self, project_id: str) -> dict[str, int]:
        """Delete learned context for one project while keeping its registration row."""
        self.initialize()
        if not project_id:
            return self._empty_reset_counts()
        with self.connect() as conn:
            return self._reset_memory(conn, project_id=project_id)

    def reset_all_memory(self) -> dict[str, int]:
        """Delete learned context for all projects while keeping project registrations."""
        self.initialize()
        with self.connect() as conn:
            return self._reset_memory(conn, project_id=None)

    def count_project_memory(self, project_id: str) -> dict[str, int]:
        """Count learned context rows for one project."""
        self.initialize()
        if not project_id:
            return self._empty_reset_counts()
        with self.connect() as conn:
            return self._count_memory(conn, project_id=project_id)

    def count_all_memory(self) -> dict[str, int]:
        """Count learned context rows for all projects."""
        self.initialize()
        with self.connect() as conn:
            return self._count_memory(conn, project_id=None)

    def _reset_memory(
        self, conn: sqlite3.Connection, *, project_id: str | None
    ) -> dict[str, int]:
        """Delete context rows for project_id, or all context rows when omitted."""
        counts = self._count_memory(conn, project_id=project_id)
        self._delete_rows(conn, "records_fts", project_id=project_id)
        self._delete_rows(conn, "record_embeddings", project_id=project_id)
        self._delete_rows(conn, "record_versions", project_id=project_id)
        self._delete_rows(conn, "records", project_id=project_id)
        self._delete_rows(conn, "sessions", project_id=project_id)
        return counts

    def _count_memory(
        self, conn: sqlite3.Connection, *, project_id: str | None
    ) -> dict[str, int]:
        """Count context rows for project_id, or all context rows when omitted."""
        return {
            "records": self._count_rows(conn, "records", project_id=project_id),
            "record_versions": self._count_rows(conn, "record_versions", project_id=project_id),
            "context_sessions": self._count_rows(conn, "sessions", project_id=project_id),
            "records_fts": self._count_rows(conn, "records_fts", project_id=project_id),
            "record_embeddings": self._count_rows(conn, "record_embeddings", project_id=project_id),
        }

    def _empty_reset_counts(self) -> dict[str, int]:
        """Return the context reset counter shape with zero values."""
        return {
            "records": 0,
            "record_versions": 0,
            "context_sessions": 0,
            "records_fts": 0,
            "record_embeddings": 0,
        }

    def _count_rows(
        self, conn: sqlite3.Connection, table_name: str, *, project_id: str | None
    ) -> int:
        """Count rows in a resettable table if it exists."""
        if not self._table_exists(conn, table_name):
            return 0
        if project_id is None:
            row = conn.execute(f"SELECT COUNT(1) AS total FROM {table_name}").fetchone()
        else:
            row = conn.execute(
                f"SELECT COUNT(1) AS total FROM {table_name} WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        return int(row["total"] or 0) if row else 0

    def _delete_rows(
        self, conn: sqlite3.Connection, table_name: str, *, project_id: str | None
    ) -> None:
        """Delete rows from a resettable table if it exists."""
        if not self._table_exists(conn, table_name):
            return
        if project_id is None:
            conn.execute(f"DELETE FROM {table_name}")
        else:
            conn.execute(f"DELETE FROM {table_name} WHERE project_id = ?", (project_id,))

    def _table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
        """Return whether a SQLite table or virtual table exists."""
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = ? LIMIT 1",
            (table_name,),
        ).fetchone()
        return row is not None

    def upsert_session(
        self,
        *,
        project_id: str,
        session_id: str,
        agent_type: str,
        source_trace_ref: str,
        repo_path: str | None,
        cwd: str | None,
        started_at: str | None,
        model_name: str | None,
        instructions_text: str | None,
        prompt_text: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Insert or update one session provenance row."""
        del metadata
        self.initialize()
        now = _utc_now()
        started_at_text = _normalize_datetime_utc(started_at)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions(
                    session_id, project_id, agent_type, source_trace_ref, repo_path, cwd,
                    started_at, model_name, instructions_text, prompt_text, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    project_id=excluded.project_id,
                    agent_type=excluded.agent_type,
                    source_trace_ref=excluded.source_trace_ref,
                    repo_path=excluded.repo_path,
                    cwd=excluded.cwd,
                    started_at=excluded.started_at,
                    model_name=excluded.model_name,
                    instructions_text=excluded.instructions_text,
                    prompt_text=excluded.prompt_text
                """,
                (
                    session_id,
                    project_id,
                    agent_type,
                    source_trace_ref,
                    repo_path,
                    cwd,
                    started_at_text,
                    model_name,
                    instructions_text,
                    prompt_text,
                    now,
                ),
            )
        return {"session_id": session_id, "project_id": project_id}

    def fetch_record(
        self,
        record_id: str,
        *,
        project_ids: list[str] | None = None,
        include_versions: bool = False,
    ) -> dict[str, Any] | None:
        """Fetch one record plus optional versions."""
        self.initialize()
        filter_sql, params = self._build_record_filter_sql(
            project_ids=project_ids,
            kind_filters=None,
            statuses=None,
            source_session_id=None,
            created_since=None,
            created_until=None,
            updated_since=None,
            updated_until=None,
            valid_at=None,
            include_archived=True,
            table_alias="",
        )
        with self.connect() as conn:
            row = conn.execute(
                f"SELECT * FROM records WHERE record_id = ? AND {filter_sql}",
                tuple([record_id] + params),
            ).fetchone()
            if row is None:
                return None
            payload = self._record_row_to_dict(row)
            if include_versions:
                versions = conn.execute(
                    """
                    SELECT *
                    FROM record_versions
                    WHERE record_id = ?
                    ORDER BY version_no DESC
                    """,
                    (record_id,),
                ).fetchall()
                payload["versions"] = [self._version_row_to_dict(item) for item in versions]
            return payload

    def create_record(
        self,
        *,
        project_id: str,
        session_id: str | None,
        kind: str,
        title: str,
        body: str,
        status: str = "active",
        record_id: str | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
        valid_from: str | None = None,
        valid_until: str | None = None,
        superseded_by_record_id: str | None = None,
        decision: str | None = None,
        why: str | None = None,
        alternatives: str | None = None,
        consequences: str | None = None,
        user_intent: str | None = None,
        what_happened: str | None = None,
        outcomes: str | None = None,
        change_reason: str | None = None,
    ) -> dict[str, Any]:
        """Create a new canonical record and its first version."""
        self.initialize()
        record_id = str(record_id or _new_id("rec")).strip()
        if not record_id:
            raise ValueError("record_id_required")
        now = _utc_now()
        effective_created_at = created_at or now
        effective_updated_at = updated_at or effective_created_at
        payload = self._normalize_record_payload(
            kind=kind,
            title=title,
            body=body,
            status=status,
            source_session_id=session_id,
            created_at=effective_created_at,
            updated_at=effective_updated_at,
            valid_from=valid_from or effective_created_at,
            valid_until=valid_until,
            superseded_by_record_id=superseded_by_record_id,
            decision=decision,
            why=why,
            alternatives=alternatives,
            consequences=consequences,
            user_intent=user_intent,
            what_happened=what_happened,
            outcomes=outcomes,
        )
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_episode_uniqueness(
                conn,
                project_id=project_id,
                kind=payload["kind"],
                session_id=session_id,
                exclude_record_id=None,
            )
            conn.execute(
                """
                INSERT INTO records(
                    record_id, project_id, kind, title, body, status, source_session_id,
                    created_at, updated_at, valid_from, valid_until, superseded_by_record_id,
                    decision, why, alternatives, consequences, user_intent, what_happened, outcomes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    project_id,
                    payload["kind"],
                    payload["title"],
                    payload["body"],
                    payload["status"],
                    payload["source_session_id"],
                    payload["created_at"],
                    payload["updated_at"],
                    payload["valid_from"],
                    payload["valid_until"],
                    payload["superseded_by_record_id"],
                    payload["decision"],
                    payload["why"],
                    payload["alternatives"],
                    payload["consequences"],
                    payload["user_intent"],
                    payload["what_happened"],
                    payload["outcomes"],
                ),
            )
            self._insert_record_version(
                conn,
                project_id=project_id,
                record_id=record_id,
                version_no=1,
                payload=payload,
                change_kind="create",
                change_reason=change_reason,
                changed_by_session_id=session_id,
            )
        self._refresh_derived_indexes_after_write(record_ids=[record_id])
        return self.fetch_record(record_id, project_ids=[project_id], include_versions=True) or {}

    def update_record(
        self,
        *,
        record_id: str,
        session_id: str | None,
        project_ids: list[str] | None,
        changes: dict[str, Any],
        change_reason: str | None = None,
        change_kind_override: str | None = None,
    ) -> dict[str, Any]:
        """Apply a partial update and append a version snapshot."""
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT * FROM records WHERE record_id = ?", (record_id,)).fetchone()
            if current is None:
                raise ValueError(f"record_not_found:{record_id}")
            self._update_record_in_conn(
                conn,
                current=current,
                record_id=record_id,
                session_id=session_id,
                project_ids=project_ids,
                changes=changes,
                change_reason=change_reason,
                change_kind_override=change_kind_override,
            )
        self._refresh_derived_indexes_after_write(record_ids=[record_id])
        return self.fetch_record(record_id, project_ids=project_ids, include_versions=True) or {}

    def _update_record_in_conn(
        self,
        conn: sqlite3.Connection,
        *,
        current: sqlite3.Row,
        record_id: str,
        session_id: str | None,
        project_ids: list[str] | None,
        changes: dict[str, Any],
        change_reason: str | None,
        change_kind_override: str | None,
    ) -> dict[str, Any]:
        """Apply one record update inside the caller's active transaction."""
        merged = self._record_row_to_dict(current)
        if project_ids is not None and merged["project_id"] not in project_ids:
            raise ValueError(f"record_out_of_scope:{record_id}")
        now = _utc_now()
        effective_updated_at = changes.get("updated_at", now)
        payload = self._normalize_record_payload(
            kind=changes.get("kind", merged["kind"]),
            title=changes.get("title", merged["title"]),
            body=changes.get("body", merged["body"]),
            status=changes.get("status", merged["status"]),
            source_session_id=merged["source_session_id"],
            created_at=merged["created_at"],
            updated_at=effective_updated_at,
            valid_from=changes.get("valid_from", merged["valid_from"]),
            valid_until=changes.get("valid_until", merged["valid_until"]),
            superseded_by_record_id=changes.get(
                "superseded_by_record_id", merged["superseded_by_record_id"]
            ),
            decision=changes.get("decision", merged["decision"]),
            why=changes.get("why", merged["why"]),
            alternatives=changes.get("alternatives", merged["alternatives"]),
            consequences=changes.get("consequences", merged["consequences"]),
            user_intent=changes.get("user_intent", merged["user_intent"]),
            what_happened=changes.get("what_happened", merged["what_happened"]),
            outcomes=changes.get("outcomes", merged["outcomes"]),
        )
        meaningful_fields = (
            "kind",
            "title",
            "body",
            "status",
            "valid_from",
            "valid_until",
            "superseded_by_record_id",
            "decision",
            "why",
            "alternatives",
            "consequences",
            "user_intent",
            "what_happened",
            "outcomes",
        )
        if all(payload[field] == merged[field] for field in meaningful_fields):
            raise ValueError("no_changes")
        self._ensure_episode_uniqueness(
            conn,
            project_id=merged["project_id"],
            kind=payload["kind"],
            session_id=payload["source_session_id"],
            exclude_record_id=record_id,
        )
        conn.execute(
            """
            UPDATE records
            SET kind=?, title=?, body=?, status=?, updated_at=?, valid_from=?,
                valid_until=?, superseded_by_record_id=?, decision=?, why=?,
                alternatives=?, consequences=?, user_intent=?, what_happened=?, outcomes=?
            WHERE record_id=?
            """,
            (
                payload["kind"],
                payload["title"],
                payload["body"],
                payload["status"],
                payload["updated_at"],
                payload["valid_from"],
                payload["valid_until"],
                payload["superseded_by_record_id"],
                payload["decision"],
                payload["why"],
                payload["alternatives"],
                payload["consequences"],
                payload["user_intent"],
                payload["what_happened"],
                payload["outcomes"],
                record_id,
            ),
        )
        version_no = (
            int(
                conn.execute(
                    "SELECT COALESCE(MAX(version_no), 0) FROM record_versions WHERE record_id = ?",
                    (record_id,),
                ).fetchone()[0]
            )
            + 1
        )
        change_kind = change_kind_override or ("archive" if payload["status"] == "archived" else "update")
        self._insert_record_version(
            conn,
            project_id=merged["project_id"],
            record_id=record_id,
            version_no=version_no,
            payload=payload,
            change_kind=change_kind,
            change_reason=change_reason,
            changed_by_session_id=session_id,
        )
        return payload

    def archive_record(
        self,
        *,
        record_id: str,
        session_id: str | None,
        project_ids: list[str] | None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Archive an existing record."""
        self.initialize()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT * FROM records WHERE record_id = ?", (record_id,)).fetchone()
            if current is None:
                raise ValueError(f"record_not_found:{record_id}")
            current_dict = self._record_row_to_dict(current)
            if project_ids is not None and current_dict["project_id"] not in project_ids:
                raise ValueError(f"record_out_of_scope:{record_id}")
            created_at = _parse_iso_utc(current_dict.get("created_at"))
            now = datetime.now(timezone.utc)
            if (
                str(current_dict.get("status") or "") == "active"
                and str(current_dict.get("kind") or "") != "episode"
                and not str(current_dict.get("superseded_by_record_id") or "").strip()
                and created_at is not None
                and (now - created_at) < timedelta(hours=24)
            ):
                raise ValueError(f"refuse_archive_recent_active_record:{record_id}")
            self._update_record_in_conn(
                conn,
                current=current,
                record_id=record_id,
                session_id=session_id,
                project_ids=project_ids,
                changes={
                    "status": "archived",
                    "valid_until": str(current_dict.get("valid_until") or _utc_now()),
                },
                change_reason=reason or "archive_record",
                change_kind_override="archive",
            )
        self._refresh_derived_indexes_after_write(record_ids=[record_id])
        return self.fetch_record(record_id, project_ids=project_ids, include_versions=True) or {}

    def supersede_record(
        self,
        *,
        record_id: str,
        session_id: str | None,
        project_ids: list[str] | None,
        replacement_record_id: str,
        reason: str | None = None,
        valid_until: str | None = None,
    ) -> dict[str, Any]:
        """Mark one record as superseded by another record."""
        if project_ids is not None and not project_ids:
            raise ValueError(f"record_out_of_scope:{record_id}")
        replacement = self.fetch_record(replacement_record_id, project_ids=project_ids, include_versions=False)
        if replacement is None:
            raise ValueError(f"replacement_record_not_found:{replacement_record_id}")
        return self.update_record(
            record_id=record_id,
            session_id=session_id,
            project_ids=project_ids,
            changes={
                "valid_until": valid_until or _utc_now(),
                "superseded_by_record_id": replacement_record_id,
            },
            change_reason=reason or "supersede_record",
            change_kind_override="supersede",
        )

    def search(
        self,
        *,
        project_ids: list[str] | None,
        query: str,
        kind_filters: list[str] | None = None,
        statuses: list[str] | None = None,
        valid_at: str | None = None,
        include_archived: bool = False,
        limit: int = 8,
    ) -> list[SearchHit]:
        """Run hybrid retrieval over records."""
        self.initialize()
        return retrieval.search_records(
            self,
            project_ids=project_ids,
            query=query,
            kind_filters=kind_filters,
            statuses=statuses,
            valid_at=valid_at,
            include_archived=include_archived,
            limit=limit,
        )

    def index_health(self, *, project_ids: list[str] | None = None) -> dict[str, int]:
        """Return read-only derived index health counts without repairing indexes."""
        self.initialize()
        with self.connect() as conn:
            record_where = ""
            params: list[Any] = []
            if project_ids is not None:
                if not project_ids:
                    return {
                        "record_count": 0,
                        "fts_count": 0,
                        "embedding_count": 0,
                        "missing_embedding_count": 0,
                        "stale_fts_count": 0,
                        "stale_embedding_count": 0,
                    }
                placeholders = ", ".join("?" for _ in project_ids)
                record_where = f" WHERE project_id IN ({placeholders})"
                params = [str(project_id) for project_id in project_ids]

            record_count = int(
                conn.execute(f"SELECT COUNT(*) FROM records{record_where}", params).fetchone()[0]
            )
            fts_count = int(
                conn.execute(f"SELECT COUNT(*) FROM records_fts{record_where}", params).fetchone()[0]
            )
            stale_project_filter = ""
            if project_ids is not None:
                stale_project_filter = f" AND r.project_id IN ({placeholders})"
            stale_fts_count = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM (
                        SELECT f.record_id
                        FROM records AS r
                        LEFT JOIN records_fts AS f ON f.record_id = r.record_id
                        WHERE (
                            f.record_id IS NULL
                            OR f.updated_at IS NULL
                            OR f.updated_at != r.updated_at
                            OR COALESCE(f.title, '') != COALESCE(r.title, '')
                            OR COALESCE(f.body, '') != COALESCE(r.body, '')
                            OR COALESCE(f.decision, '') != COALESCE(r.decision, '')
                            OR COALESCE(f.why, '') != COALESCE(r.why, '')
                            OR COALESCE(f.user_intent, '') != COALESCE(r.user_intent, '')
                            OR COALESCE(f.what_happened, '') != COALESCE(r.what_happened, '')
                        )
                        {stale_project_filter}
                        UNION ALL
                        SELECT f.record_id
                        FROM records_fts AS f
                        LEFT JOIN records AS r ON r.record_id = f.record_id
                        WHERE r.record_id IS NULL
                        {"AND f.project_id IN (" + placeholders + ")" if project_ids is not None else ""}
                    )
                    """,
                    [*params, *params],
                ).fetchone()[0]
            )
            embeddings_exists = (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name = 'record_embeddings'"
                ).fetchone()
                is not None
            )
            if not embeddings_exists:
                return {
                    "record_count": record_count,
                    "fts_count": fts_count,
                    "embedding_count": 0,
                    "missing_embedding_count": record_count,
                    "stale_fts_count": stale_fts_count,
                    "stale_embedding_count": record_count,
                }

            embedding_count = int(
                conn.execute(
                    f"SELECT COUNT(*) FROM record_embeddings{record_where}", params
                ).fetchone()[0]
            )
            missing_embedding_count = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM records AS r
                    LEFT JOIN record_embeddings AS e ON e.record_id = r.record_id
                    WHERE e.record_id IS NULL
                    {"AND r.project_id IN (" + placeholders + ")" if project_ids is not None else ""}
                    """,
                    params,
                ).fetchone()[0]
            )
            provider = self.embedding_provider()
            stale_embedding_count = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM (
                        SELECT e.record_id
                        FROM records AS r
                        LEFT JOIN record_embeddings AS e ON e.record_id = r.record_id
                        WHERE (
                            e.record_id IS NULL
                            OR e.updated_at IS NULL
                            OR e.updated_at != r.updated_at
                        OR COALESCE(e.embedding_model, '') != ?
                        )
                        {stale_project_filter}
                        UNION ALL
                        SELECT e.record_id
                        FROM record_embeddings AS e
                        LEFT JOIN records AS r ON r.record_id = e.record_id
                        WHERE r.record_id IS NULL
                        {"AND e.project_id IN (" + placeholders + ")" if project_ids is not None else ""}
                    )
                    """,
                    [provider.model_id, *params, *params],
                ).fetchone()[0]
            )
            return {
                "record_count": record_count,
                "fts_count": fts_count,
                "embedding_count": embedding_count,
                "missing_embedding_count": missing_embedding_count,
                "stale_fts_count": stale_fts_count,
                "stale_embedding_count": stale_embedding_count,
            }

    def query(
        self,
        *,
        entity: str,
        mode: str,
        project_ids: list[str] | None = None,
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
        include_archived: bool | None = None,
    ) -> dict[str, Any]:
        """Run a deterministic list/count query for records, versions, or sessions."""
        entity_name = str(entity or "").strip().lower()
        mode_name = str(mode or "").strip().lower()
        if entity_name not in QUERY_ENTITIES:
            raise ValueError(f"invalid_query_entity:{entity}")
        if mode_name not in QUERY_MODES:
            raise ValueError(f"invalid_query_mode:{mode}")
        order_field = str(order_by or "created_at").strip()
        if order_field not in QUERY_ORDER_FIELDS:
            raise ValueError(f"invalid_query_order:{order_by}")
        if entity_name == "sessions" and order_field != "created_at":
            raise ValueError(f"invalid_query_order:{entity_name}:{order_by}")
        unsupported_filters = self._unsupported_query_filters(
            entity_name=entity_name,
            kind=kind,
            status=status,
            updated_since=updated_since,
            updated_until=updated_until,
            valid_at=valid_at,
            include_archived=include_archived,
        )
        if unsupported_filters:
            filter_names = ",".join(unsupported_filters)
            raise ValueError(f"unsupported_query_filter:{entity_name}:{filter_names}")

        self.initialize()
        if entity_name == "records":
            return self._query_records(
                mode=mode_name,
                project_ids=project_ids,
                kind=kind,
                status=status,
                source_session_id=source_session_id,
                created_since=created_since,
                created_until=created_until,
                updated_since=updated_since,
                updated_until=updated_until,
                valid_at=valid_at,
                order_by=order_field,
                limit=limit,
                offset=offset,
                include_total=include_total,
                include_archived=bool(include_archived or valid_at),
            )
        if entity_name == "versions":
            return self._query_versions(
                mode=mode_name,
                project_ids=project_ids,
                kind=kind,
                status=status,
                source_session_id=source_session_id,
                created_since=created_since,
                created_until=created_until,
                updated_since=updated_since,
                updated_until=updated_until,
                valid_at=valid_at,
                order_by=order_field,
                limit=limit,
                offset=offset,
                include_total=include_total,
            )
        return self._query_sessions(
            mode=mode_name,
            project_ids=project_ids,
            source_session_id=source_session_id,
            created_since=created_since,
            created_until=created_until,
            order_by=order_field,
            limit=limit,
            offset=offset,
            include_total=include_total,
        )

    def _unsupported_query_filters(
        self,
        *,
        entity_name: str,
        kind: str | None,
        status: str | None,
        updated_since: str | None,
        updated_until: str | None,
        valid_at: str | None,
        include_archived: bool | None,
    ) -> list[str]:
        """Return query filters that are not meaningful for one entity."""
        unsupported: list[str] = []
        if entity_name == "sessions":
            unsupported.extend(
                name
                for name, value in (
                    ("kind", kind),
                    ("status", status),
                    ("updated_since", updated_since),
                    ("updated_until", updated_until),
                    ("valid_at", valid_at),
                )
                if value is not None
            )
        if entity_name in {"sessions", "versions"} and include_archived is not None:
            unsupported.append("include_archived")
        return unsupported

    def _query_records(
        self,
        *,
        mode: str,
        project_ids: list[str] | None,
        kind: str | None,
        status: str | None,
        source_session_id: str | None,
        created_since: str | None,
        created_until: str | None,
        updated_since: str | None,
        updated_until: str | None,
        valid_at: str | None,
        order_by: str,
        limit: int,
        offset: int,
        include_total: bool,
        include_archived: bool,
    ) -> dict[str, Any]:
        filter_sql, params = self._build_record_filter_sql(
            project_ids=project_ids,
            kind_filters=[kind] if kind else None,
            statuses=[status] if status else None,
            source_session_id=source_session_id,
            created_since=created_since,
            created_until=created_until,
            updated_since=updated_since,
            updated_until=updated_until,
            valid_at=valid_at,
            include_archived=include_archived,
            table_alias="",
        )
        with self.connect() as conn:
            total = None
            if include_total or mode == "count":
                total = int(
                    conn.execute(f"SELECT COUNT(1) AS total FROM records WHERE {filter_sql}", tuple(params)).fetchone()["total"]
                )
            if mode == "count":
                return {"entity": "records", "mode": "count", "count": int(total or 0)}
            rows = conn.execute(
                f"""
                SELECT *
                FROM records
                WHERE {filter_sql}
                ORDER BY {order_by} DESC, record_id DESC
                LIMIT ? OFFSET ?
                """,
                tuple(params + [max(1, int(limit)), max(0, int(offset))]),
            ).fetchall()
        return {
            "entity": "records",
            "mode": "list",
            "count": len(rows),
            "total": total,
            "rows": [self._record_row_to_dict(row) for row in rows],
        }

    def _query_versions(
        self,
        *,
        mode: str,
        project_ids: list[str] | None,
        kind: str | None,
        status: str | None,
        source_session_id: str | None,
        created_since: str | None,
        created_until: str | None,
        updated_since: str | None,
        updated_until: str | None,
        valid_at: str | None,
        order_by: str,
        limit: int,
        offset: int,
        include_total: bool,
    ) -> dict[str, Any]:
        filter_sql, params = self._build_version_filter_sql(
            project_ids=project_ids,
            kind=kind,
            status=status,
            source_session_id=source_session_id,
            created_since=created_since,
            created_until=created_until,
            updated_since=updated_since,
            updated_until=updated_until,
            valid_at=valid_at,
        )
        order_column = {
            "created_at": "changed_at",
            "updated_at": "changed_at",
            "valid_from": "valid_from",
        }[order_by]
        with self.connect() as conn:
            total = None
            if include_total or mode == "count":
                total = int(
                    conn.execute(
                        f"SELECT COUNT(1) AS total FROM record_versions WHERE {filter_sql}",
                        tuple(params),
                    ).fetchone()["total"]
                )
            if mode == "count":
                return {"entity": "versions", "mode": "count", "count": int(total or 0)}
            rows = conn.execute(
                f"""
                SELECT *
                FROM record_versions
                WHERE {filter_sql}
                ORDER BY {order_column} DESC, version_no DESC
                LIMIT ? OFFSET ?
                """,
                tuple(params + [max(1, int(limit)), max(0, int(offset))]),
            ).fetchall()
        return {
            "entity": "versions",
            "mode": "list",
            "count": len(rows),
            "total": total,
            "rows": [self._version_row_to_dict(row) for row in rows],
        }

    def _query_sessions(
        self,
        *,
        mode: str,
        project_ids: list[str] | None,
        source_session_id: str | None,
        created_since: str | None,
        created_until: str | None,
        order_by: str,
        limit: int,
        offset: int,
        include_total: bool,
    ) -> dict[str, Any]:
        order_column = "created_at"
        clauses = ["1=1"]
        params: list[Any] = []
        if source_session_id:
            clauses.append("session_id = ?")
            params.append(source_session_id)
        if project_ids:
            placeholders = ", ".join("?" for _ in project_ids)
            clauses.append(f"project_id IN ({placeholders})")
            params.extend(project_ids)
        elif project_ids is not None:
            clauses.append("0=1")
        if created_since:
            clauses.append("created_at >= ?")
            params.append(_normalize_datetime_filter_bound(created_since, upper=False))
        if created_until:
            clauses.append("created_at <= ?")
            params.append(_normalize_datetime_filter_bound(created_until, upper=True))
        filter_sql = " AND ".join(clauses)
        with self.connect() as conn:
            total = None
            if include_total or mode == "count":
                total = int(
                    conn.execute(f"SELECT COUNT(1) AS total FROM sessions WHERE {filter_sql}", tuple(params)).fetchone()["total"]
                )
            if mode == "count":
                return {"entity": "sessions", "mode": "count", "count": int(total or 0)}
            rows = conn.execute(
                f"""
                SELECT *
                FROM sessions
                WHERE {filter_sql}
                ORDER BY {order_column} DESC, session_id DESC
                LIMIT ? OFFSET ?
                """,
                tuple(params + [max(1, int(limit)), max(0, int(offset))]),
            ).fetchall()
        return {
            "entity": "sessions",
            "mode": "list",
            "count": len(rows),
            "total": total,
            "rows": [dict(row) for row in rows],
        }

    def _record_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        """Convert one record row into JSON-like data."""
        return {
            "record_id": str(row["record_id"]),
            "project_id": str(row["project_id"]),
            "kind": str(row["kind"]),
            "title": str(row["title"]),
            "body": str(row["body"]),
            "status": str(row["status"]),
            "source_session_id": row["source_session_id"],
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "valid_from": str(row["valid_from"]),
            "valid_until": row["valid_until"],
            "superseded_by_record_id": row["superseded_by_record_id"],
            "decision": row["decision"],
            "why": row["why"],
            "alternatives": row["alternatives"],
            "consequences": row["consequences"],
            "user_intent": row["user_intent"],
            "what_happened": row["what_happened"],
            "outcomes": row["outcomes"],
        }

    def _version_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        """Convert one version row into JSON-like data."""
        return dict(row)

    def _normalize_record_payload(
        self,
        *,
        kind: Any,
        title: Any,
        body: Any,
        status: Any,
        source_session_id: Any,
        created_at: Any,
        updated_at: Any,
        valid_from: Any,
        valid_until: Any,
        superseded_by_record_id: Any,
        decision: Any,
        why: Any,
        alternatives: Any,
        consequences: Any,
        user_intent: Any,
        what_happened: Any,
        outcomes: Any,
    ) -> dict[str, Any]:
        """Normalize and validate one record payload."""
        payload = normalize_record_payload(
            kind=kind,
            title=title,
            body=body,
            status=status,
            source_session_id=source_session_id,
            created_at=created_at,
            updated_at=updated_at,
            valid_from=valid_from,
            valid_until=valid_until,
            superseded_by_record_id=superseded_by_record_id,
            decision=decision,
            why=why,
            alternatives=alternatives,
            consequences=consequences,
            user_intent=user_intent,
            what_happened=what_happened,
            outcomes=outcomes,
        )
        for field_name in ("created_at", "updated_at", "valid_from", "valid_until"):
            payload[field_name] = _normalize_datetime_utc(payload[field_name])
        return payload

    def _ensure_episode_uniqueness(
        self,
        conn: sqlite3.Connection,
        *,
        project_id: str,
        kind: str,
        session_id: str | None,
        exclude_record_id: str | None,
    ) -> None:
        """Enforce one episode record per session."""
        if kind != "episode" or not session_id:
            return
        query = """
            SELECT record_id
            FROM records
            WHERE project_id = ? AND kind = 'episode' AND source_session_id = ?
        """
        params: list[Any] = [project_id, session_id]
        if exclude_record_id:
            query += " AND record_id != ?"
            params.append(exclude_record_id)
        row = conn.execute(query, tuple(params)).fetchone()
        if row is not None:
            raise ValueError("duplicate_episode_for_session")

    def _insert_record_version(
        self,
        conn: sqlite3.Connection,
        *,
        project_id: str,
        record_id: str,
        version_no: int,
        payload: dict[str, Any],
        change_kind: str,
        change_reason: str | None,
        changed_by_session_id: str | None,
    ) -> None:
        """Insert one immutable version row."""
        if change_kind not in ALLOWED_CHANGE_KINDS:
            raise ValueError(f"invalid_change_kind:{change_kind}")
        conn.execute(
            """
            INSERT INTO record_versions(
                version_id, project_id, record_id, version_no, kind, title, body, status,
                source_session_id, created_at, updated_at, valid_from, valid_until,
                superseded_by_record_id, decision, why, alternatives, consequences,
                user_intent, what_happened, outcomes, change_kind, change_reason,
                changed_at, changed_by_session_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id("ver"),
                project_id,
                record_id,
                version_no,
                payload["kind"],
                payload["title"],
                payload["body"],
                payload["status"],
                payload["source_session_id"],
                payload["created_at"],
                payload["updated_at"],
                payload["valid_from"],
                payload["valid_until"],
                payload["superseded_by_record_id"],
                payload["decision"],
                payload["why"],
                payload["alternatives"],
                payload["consequences"],
                payload["user_intent"],
                payload["what_happened"],
                payload["outcomes"],
                change_kind,
                _normalize_optional_text(change_reason),
                _utc_now(),
                _normalize_optional_text(changed_by_session_id),
            ),
        )

    def _upsert_embedding(
        self,
        conn: sqlite3.Connection,
        *,
        project_id: str,
        record_id: str,
        text: str,
        updated_at: str,
    ) -> None:
        """Refresh derived embedding storage for one record."""
        provider = self.embedding_provider()
        vector = sqlite_vec.serialize_float32(provider.embed_document(text))
        conn.execute("DELETE FROM record_embeddings WHERE record_id = ?", (record_id,))
        conn.execute(
            """
            INSERT INTO record_embeddings(
                embedding, project_id, record_id, embedding_model, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (vector, project_id, record_id, provider.model_id, updated_at),
        )

    def _upsert_fts(
        self,
        conn: sqlite3.Connection,
        *,
        project_id: str,
        record_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Refresh derived FTS storage for one record."""
        conn.execute("DELETE FROM records_fts WHERE record_id = ?", (record_id,))
        conn.execute(
            """
            INSERT INTO records_fts(
                record_id, project_id, updated_at, title, body, decision, why, user_intent, what_happened
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                project_id,
                payload["updated_at"],
                payload["title"],
                payload["body"],
                payload["decision"] or "",
                payload["why"] or "",
                payload["user_intent"] or "",
                payload["what_happened"] or "",
            ),
        )

    def _search_text(self, payload: dict[str, Any]) -> str:
        """Build canonical search text from one record payload."""
        return record_search_text(payload)

    def _rebuild_embeddings(self, conn: sqlite3.Connection) -> None:
        """Rebuild all derived embedding rows from canonical record text."""
        conn.execute("DELETE FROM record_embeddings")
        rows = conn.execute("SELECT * FROM records ORDER BY created_at ASC, record_id ASC").fetchall()
        for row in rows:
            payload = self._record_row_to_dict(row)
            self._upsert_embedding(
                conn,
                project_id=str(row["project_id"]),
                record_id=str(row["record_id"]),
                text=self._search_text(payload),
                updated_at=str(row["updated_at"]),
            )

    def _prepare_search_indexes(self, conn: sqlite3.Connection) -> None:
        """Ensure search sees fresh derived FTS and embedding tables."""
        self._prepare_search_fts(conn)
        self._prepare_search_embeddings(conn)

    def _prepare_search_embeddings(self, conn: sqlite3.Connection) -> None:
        """Ensure semantic search sees a complete derived embedding table."""
        needs_embedding_rebuild = self._ensure_record_embeddings_index(conn)
        if needs_embedding_rebuild:
            self._rebuild_embeddings(conn)

    def _prepare_search_fts(self, conn: sqlite3.Connection) -> None:
        """Ensure lexical search sees a fresh derived FTS table."""
        if self._ensure_records_fts_index(conn):
            self._rebuild_fts(conn)

    def _stale_embedding_count(self, conn: sqlite3.Connection, *, provider_model: str) -> int:
        """Return records missing a fresh embedding row for the active model."""
        return int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT e.record_id
                    FROM records AS r
                    LEFT JOIN record_embeddings AS e ON e.record_id = r.record_id
                    WHERE e.record_id IS NULL
                       OR e.updated_at IS NULL
                       OR e.updated_at != r.updated_at
                       OR COALESCE(e.embedding_model, '') != ?
                    UNION ALL
                    SELECT e.record_id
                    FROM record_embeddings AS e
                    LEFT JOIN records AS r ON r.record_id = e.record_id
                    WHERE r.record_id IS NULL
                )
                """,
                (provider_model,),
            ).fetchone()[0]
        )

    def _ensure_records_fts_index(self, conn: sqlite3.Connection) -> bool:
        """Return whether the derived FTS table should be rebuilt from records."""
        record_count = int(conn.execute("SELECT COUNT(*) FROM records").fetchone()[0])
        fts_count = int(conn.execute("SELECT COUNT(*) FROM records_fts").fetchone()[0])
        if fts_count != record_count:
            return True
        stale_count = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT f.record_id
                    FROM records AS r
                    LEFT JOIN records_fts AS f ON f.record_id = r.record_id
                    WHERE f.record_id IS NULL
                       OR f.updated_at IS NULL
                       OR f.updated_at != r.updated_at
                       OR COALESCE(f.title, '') != COALESCE(r.title, '')
                       OR COALESCE(f.body, '') != COALESCE(r.body, '')
                       OR COALESCE(f.decision, '') != COALESCE(r.decision, '')
                       OR COALESCE(f.why, '') != COALESCE(r.why, '')
                       OR COALESCE(f.user_intent, '') != COALESCE(r.user_intent, '')
                       OR COALESCE(f.what_happened, '') != COALESCE(r.what_happened, '')
                    UNION ALL
                    SELECT f.record_id
                    FROM records_fts AS f
                    LEFT JOIN records AS r ON r.record_id = f.record_id
                    WHERE r.record_id IS NULL
                )
                """
            ).fetchone()[0]
        )
        return stale_count > 0

    def _rebuild_fts(self, conn: sqlite3.Connection) -> None:
        """Rebuild all derived FTS rows from canonical record text."""
        conn.execute("DELETE FROM records_fts")
        rows = conn.execute("SELECT * FROM records ORDER BY created_at ASC, record_id ASC").fetchall()
        for row in rows:
            payload = self._record_row_to_dict(row)
            self._upsert_fts(
                conn,
                project_id=str(row["project_id"]),
                record_id=str(row["record_id"]),
                payload=payload,
            )

    def _refresh_derived_indexes_after_write(self, *, record_ids: list[str]) -> None:
        """Best-effort refresh of derived indexes after canonical writes commit."""
        self._refresh_fts_after_write(record_ids=record_ids)
        self._refresh_embeddings_after_write(record_ids=record_ids)

    def _refresh_fts_after_write(self, *, record_ids: list[str]) -> None:
        """Best-effort refresh of derived FTS storage after canonical writes commit."""
        if not record_ids:
            return
        try:
            with self.connect() as conn:
                if self._ensure_records_fts_index(conn):
                    self._rebuild_fts(conn)
                    return
                placeholders = ", ".join("?" for _ in record_ids)
                rows = conn.execute(
                    f"""
                    SELECT *
                    FROM records
                    WHERE record_id IN ({placeholders})
                    ORDER BY created_at ASC, record_id ASC
                    """,
                    tuple(record_ids),
                ).fetchall()
                for row in rows:
                    payload = self._record_row_to_dict(row)
                    self._upsert_fts(
                        conn,
                        project_id=str(row["project_id"]),
                        record_id=str(row["record_id"]),
                        payload=payload,
                    )
        except Exception:
            LOGGER.warning("record_fts_refresh_failed", exc_info=True)

    def _refresh_embeddings_after_write(self, *, record_ids: list[str]) -> None:
        """Best-effort refresh of derived embeddings after canonical writes commit."""
        if not record_ids:
            return
        try:
            with self.connect() as conn:
                needs_embedding_rebuild = self._ensure_record_embeddings_index(conn)
                if needs_embedding_rebuild:
                    self._rebuild_embeddings(conn)
                    return
                placeholders = ", ".join("?" for _ in record_ids)
                rows = conn.execute(
                    f"""
                    SELECT *
                    FROM records
                    WHERE record_id IN ({placeholders})
                    ORDER BY created_at ASC, record_id ASC
                    """,
                    tuple(record_ids),
                ).fetchall()
                for row in rows:
                    payload = self._record_row_to_dict(row)
                    self._upsert_embedding(
                        conn,
                        project_id=str(row["project_id"]),
                        record_id=str(row["record_id"]),
                        text=self._search_text(payload),
                        updated_at=str(row["updated_at"]),
                    )
        except Exception:
            LOGGER.warning("record_embedding_refresh_failed", exc_info=True)

    def _build_record_filter_sql(
        self,
        *,
        project_ids: list[str] | None,
        kind_filters: list[str] | None,
        statuses: list[str] | None,
        source_session_id: str | None,
        created_since: str | None,
        created_until: str | None,
        updated_since: str | None,
        updated_until: str | None,
        valid_at: str | None,
        include_archived: bool,
        table_alias: str = "",
    ) -> tuple[str, list[Any]]:
        """Build reusable record filter fragments."""
        prefix = f"{table_alias}." if table_alias else ""
        clauses = ["1=1"]
        params: list[Any] = []
        effective_valid_at = _effective_current_valid_at(
            valid_at=valid_at,
            include_archived=include_archived,
            statuses=statuses,
        )
        if project_ids:
            placeholders = ", ".join("?" for _ in project_ids)
            clauses.append(f"{prefix}project_id IN ({placeholders})")
            params.extend(project_ids)
        elif project_ids is not None:
            clauses.append("0=1")
        if kind_filters:
            placeholders = ", ".join("?" for _ in kind_filters)
            clauses.append(f"{prefix}kind IN ({placeholders})")
            params.extend(kind_filters)
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"{prefix}status IN ({placeholders})")
            params.extend(statuses)
        elif not include_archived:
            clauses.append(f"{prefix}status = 'active'")
        if source_session_id:
            clauses.append(f"{prefix}source_session_id = ?")
            params.append(source_session_id)
        if created_since:
            clauses.append(f"{prefix}created_at >= ?")
            params.append(_normalize_datetime_filter_bound(created_since, upper=False))
        if created_until:
            clauses.append(f"{prefix}created_at <= ?")
            params.append(_normalize_datetime_filter_bound(created_until, upper=True))
        if updated_since:
            clauses.append(f"{prefix}updated_at >= ?")
            params.append(_normalize_datetime_filter_bound(updated_since, upper=False))
        if updated_until:
            clauses.append(f"{prefix}updated_at <= ?")
            params.append(_normalize_datetime_filter_bound(updated_until, upper=True))
        if effective_valid_at:
            clauses.append(f"{prefix}valid_from <= ?")
            clauses.append(f"({prefix}valid_until IS NULL OR {prefix}valid_until >= ?)")
            params.extend([effective_valid_at, effective_valid_at])
        return " AND ".join(clauses), params

    def _build_version_filter_sql(
        self,
        *,
        project_ids: list[str] | None,
        kind: str | None,
        status: str | None,
        source_session_id: str | None,
        created_since: str | None,
        created_until: str | None,
        updated_since: str | None,
        updated_until: str | None,
        valid_at: str | None,
    ) -> tuple[str, list[Any]]:
        """Build reusable version filter fragments."""
        clauses = ["1=1"]
        params: list[Any] = []
        effective_valid_at = _normalize_datetime_filter_bound(valid_at, upper=True) if valid_at else None
        if project_ids:
            placeholders = ", ".join("?" for _ in project_ids)
            clauses.append(f"project_id IN ({placeholders})")
            params.extend(project_ids)
        elif project_ids is not None:
            clauses.append("0=1")
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if source_session_id:
            clauses.append("source_session_id = ?")
            params.append(source_session_id)
        if created_since:
            clauses.append("created_at >= ?")
            params.append(_normalize_datetime_filter_bound(created_since, upper=False))
        if created_until:
            clauses.append("created_at <= ?")
            params.append(_normalize_datetime_filter_bound(created_until, upper=True))
        if updated_since:
            clauses.append("updated_at >= ?")
            params.append(_normalize_datetime_filter_bound(updated_since, upper=False))
        if updated_until:
            clauses.append("updated_at <= ?")
            params.append(_normalize_datetime_filter_bound(updated_until, upper=True))
        if effective_valid_at:
            clauses.append("valid_from <= ?")
            clauses.append("(valid_until IS NULL OR valid_until >= ?)")
            params.extend([effective_valid_at, effective_valid_at])
        return " AND ".join(clauses), params

if __name__ == "__main__":
    """Run a small schema and search smoke check."""
    import tempfile

    from lerim.context.project_identity import resolve_project_identity

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "context.sqlite3"
        store = ContextStore(db_path)
        identity = resolve_project_identity(Path.cwd())
        store.register_project(identity)
        store.upsert_session(
            project_id=identity.project_id,
            session_id="sess_demo",
            agent_type="codex",
            source_trace_ref="/tmp/trace.jsonl",
            repo_path=str(identity.repo_path),
            cwd=str(identity.repo_path),
            started_at=None,
            model_name="demo",
            instructions_text=None,
            prompt_text=None,
            metadata={},
        )
        record = store.create_record(
            project_id=identity.project_id,
            session_id="sess_demo",
            kind="decision",
            title="Use one global DB",
            body="Use ~/.lerim/context.sqlite3 as the canonical context store.",
            decision="Use one global DB",
            why="One source of truth.",
        )
        assert record["record_id"]
        hits = store.search(project_ids=[identity.project_id], query="global sqlite db", limit=4)
        assert hits
        print("context store: self-test passed")
