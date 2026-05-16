"""Unit tests for src/lerim/context/store.py."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from lerim.context import (
    ALLOWED_CHANGE_KINDS,
    ALLOWED_KINDS,
    ALLOWED_STATUSES,
    resolve_scope_identity,
)
from lerim.context.spec import (
    MAX_DURABLE_BODY_CHARS,
    MAX_EPISODE_BODY_CHARS,
    MAX_EPISODE_OUTCOMES_CHARS,
    MAX_EPISODE_USER_INTENT_CHARS,
    MAX_EPISODE_WHAT_HAPPENED_CHARS,
    MAX_RECORD_TITLE_CHARS,
)
from lerim.context.store import (
    QUERY_ORDER_FIELDS,
    SCHEMA_VERSION,
    ContextStore,
    _new_id,
    _normalize_datetime_filter_bound,
    _normalize_optional_text,
    _parse_iso_utc,
    _utc_now,
)


@pytest.fixture
def mock_embeddings(monkeypatch):
    provider = MagicMock()
    provider.embedding_dims = 384
    provider.model_id = "test-model"
    provider.embed_document.return_value = [0.1] * 384
    provider.embed_query.return_value = [0.1] * 384
    monkeypatch.setattr("lerim.context.store.get_embedding_provider", lambda: provider)
    return provider


@pytest.fixture
def mock_store(tmp_path, mock_embeddings):
    db_path = tmp_path / "context.sqlite3"
    s = ContextStore(db_path)
    s.initialize()
    return s


@pytest.fixture
def project_id(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "lerim.config.project_scope.git_root_for",
        lambda _p=None: tmp_path,
    )
    from lerim.context.project_identity import resolve_project_identity

    identity = resolve_project_identity(tmp_path)
    return identity


@pytest.fixture
def mock_seeded(mock_store, project_id):
    mock_store.register_project(project_id)
    mock_store.upsert_session(
        project_id=project_id.project_id,
        session_id="sess_test",
        agent_type="test",
        source_trace_ref="test.jsonl",
        repo_path=str(project_id.repo_path),
        cwd=str(project_id.repo_path),
        started_at="2026-01-01T00:00:00Z",
        model_name="test-model",
        instructions_text=None,
        prompt_text=None,
    )
    return mock_store, project_id.project_id


def _make_decision(store, project_id, **overrides):
    defaults = dict(
        project_id=project_id,
        session_id="sess_test",
        kind="decision",
        title="Use SQLite",
        body="One global context database.",
        decision="Use SQLite",
        why="Simplicity and reliability.",
    )
    defaults.update(overrides)
    return store.create_record(**defaults)


def _make_episode(store, project_id, **overrides):
    defaults = dict(
        project_id=project_id,
        session_id="sess_test",
        kind="episode",
        title="Debugging session",
        body="Found and fixed a race condition.",
        user_intent="Fix the flaky test",
        what_happened="Identified race condition in worker pool",
    )
    defaults.update(overrides)
    return store.create_record(**defaults)


class TestUtcNow:
    def test_returns_iso_string(self):
        result = _utc_now()
        assert isinstance(result, str)
        datetime.fromisoformat(result)

    def test_has_utc_timezone(self):
        parsed = datetime.fromisoformat(_utc_now())
        assert parsed.tzinfo is not None


class TestParseIsoUtc:
    def test_valid_iso_string(self):
        result = _parse_iso_utc("2026-01-15T10:30:00+00:00")
        assert result is not None
        assert result.year == 2026
        assert result.hour == 10

    def test_none_returns_none(self):
        assert _parse_iso_utc(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_iso_utc("") is None

    def test_whitespace_returns_none(self):
        assert _parse_iso_utc("   ") is None

    def test_naive_datetime_gets_utc(self):
        result = _parse_iso_utc("2026-03-01T12:00:00")
        assert result is not None
        assert result.tzinfo is not None
        assert result.utcoffset() == timedelta(0)

    def test_invalid_string_returns_none(self):
        assert _parse_iso_utc("not-a-date") is None


class TestNormalizeDatetimeFilterBound:
    def test_z_suffix_normalizes_to_utc_offset(self):
        assert (
            _normalize_datetime_filter_bound("2026-01-01T00:00:00Z", upper=False)
            == "2026-01-01T00:00:00+00:00"
        )

    def test_offset_normalizes_to_utc(self):
        assert (
            _normalize_datetime_filter_bound("2026-01-01T03:30:00+03:00", upper=False)
            == "2026-01-01T00:30:00+00:00"
        )

    def test_date_only_upper_expands_to_end_of_day(self):
        assert (
            _normalize_datetime_filter_bound("2026-01-01", upper=True)
            == "2026-01-01T23:59:59.999999+00:00"
        )


class TestNewId:
    def test_has_prefix(self):
        assert _new_id("rec").startswith("rec_")

    def test_has_12_hex_chars_after_prefix(self):
        parts = _new_id("rec").split("_", 1)
        assert len(parts) == 2
        assert len(parts[1]) == 12

    def test_generates_unique_ids(self):
        ids = {_new_id("rec") for _ in range(100)}
        assert len(ids) == 100


class TestNormalizeOptionalText:
    def test_strips_whitespace(self):
        assert _normalize_optional_text("  hello  ") == "hello"

    def test_none_returns_none(self):
        assert _normalize_optional_text(None) is None

    def test_empty_string_returns_none(self):
        assert _normalize_optional_text("") is None

    def test_whitespace_only_returns_none(self):
        assert _normalize_optional_text("   ") is None

    def test_integer_becomes_string(self):
        assert _normalize_optional_text(42) == "42"


class TestConstants:
    def test_allowed_kinds(self):
        assert ALLOWED_KINDS == (
            "decision",
            "preference",
            "constraint",
            "fact",
            "reference",
            "episode",
        )

    def test_allowed_statuses(self):
        assert ALLOWED_STATUSES == ("active", "archived")

    def test_allowed_change_kinds(self):
        assert ALLOWED_CHANGE_KINDS == (
            "create",
            "update",
            "archive",
            "supersede",
        )

    def test_max_record_title_chars(self):
        assert MAX_RECORD_TITLE_CHARS == 120

    def test_max_episode_body_chars(self):
        assert MAX_EPISODE_BODY_CHARS == 1200

    def test_max_durable_body_chars(self):
        assert MAX_DURABLE_BODY_CHARS == 850

    def test_max_episode_user_intent_chars(self):
        assert MAX_EPISODE_USER_INTENT_CHARS == 300

    def test_max_episode_what_happened_chars(self):
        assert MAX_EPISODE_WHAT_HAPPENED_CHARS == 1000

    def test_max_episode_outcomes_chars(self):
        assert MAX_EPISODE_OUTCOMES_CHARS == 300

    def test_query_order_fields(self):
        assert QUERY_ORDER_FIELDS == ("created_at", "updated_at", "valid_from")


class TestContextStoreInit:
    def test_creates_parent_dir(self, tmp_path):
        db_path = tmp_path / "nested" / "dir" / "test.db"
        ContextStore(db_path)
        assert db_path.parent.exists()

    def test_resolves_path(self, tmp_path):
        store = ContextStore(tmp_path / "test.db")
        assert store.db_path.is_absolute()

    def test_connect_yields_connection(self, mock_store):
        with mock_store.connect() as conn:
            assert isinstance(conn, sqlite3.Connection)

    def test_connect_enables_foreign_keys(self, mock_store):
        with mock_store.connect() as conn:
            fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1

    def test_initialize_creates_tables(self, mock_store):
        with mock_store.connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        for expected in (
            "records",
            "record_versions",
            "projects",
            "scopes",
            "sessions",
            "schema_meta",
        ):
            assert expected in tables

    def test_register_project_creates_project_scope(self, mock_store, project_id):
        mock_store.register_project(project_id)

        with mock_store.connect() as conn:
            row = conn.execute(
                """
                SELECT scope_type, scope_id, scope_label, repo_path
                FROM scopes
                WHERE scope_type = 'project' AND scope_id = ?
                """,
                (project_id.project_id,),
            ).fetchone()

        assert row is not None
        assert row["scope_label"] == project_id.project_slug
        assert row["repo_path"] == str(project_id.repo_path)

    def test_initialize_sets_schema_version(self, mock_store):
        with mock_store.connect() as conn:
            ver = conn.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()
        assert ver is not None and ver[0] == SCHEMA_VERSION

    def test_initialize_indexes_valid_until(self, mock_store):
        with mock_store.connect() as conn:
            indexes = {
                row[1]
                for row in conn.execute("PRAGMA index_list(records)").fetchall()
            }
        assert "idx_records_valid_until" in indexes

    def test_initialize_idempotent(self, mock_store):
        mock_store.initialize()
        mock_store.initialize()
        with mock_store.connect() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
        assert n == 1

    def test_initialize_migrates_v2_tables_with_session_foreign_keys(
        self, tmp_path, mock_embeddings
    ):
        db_path = tmp_path / "legacy.sqlite3"
        now = "2026-01-01T00:00:00+00:00"
        with sqlite3.connect(db_path) as raw:
            raw.execute("PRAGMA foreign_keys = ON")
            raw.executescript(
                """
                CREATE TABLE projects (
                    project_id TEXT PRIMARY KEY,
                    project_slug TEXT NOT NULL,
                    repo_path TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE sessions (
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
                CREATE TABLE records (
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
                    FOREIGN KEY(superseded_by_record_id) REFERENCES records(record_id)
                );
                CREATE TABLE record_versions (
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
                """
            )
            raw.execute(
                "INSERT INTO projects VALUES (?, ?, ?, ?, ?)",
                ("proj_legacy", "legacy", str(tmp_path), now, now),
            )
            raw.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "sess_legacy",
                    "proj_legacy",
                    "codex",
                    "trace.jsonl",
                    str(tmp_path),
                    str(tmp_path),
                    now,
                    "test-model",
                    None,
                    "prompt",
                    now,
                ),
            )
            raw.execute(
                "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "rec_legacy",
                    "proj_legacy",
                    "decision",
                    "Keep migration safe",
                    "Existing records must survive schema upgrades.",
                    "active",
                    "sess_legacy",
                    now,
                    now,
                    now,
                    None,
                    None,
                    "Keep migration safe",
                    "Existing databases have session foreign keys.",
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
            )
            raw.execute(
                "INSERT INTO record_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "ver_legacy",
                    "proj_legacy",
                    "rec_legacy",
                    1,
                    "decision",
                    "Keep migration safe",
                    "Existing records must survive schema upgrades.",
                    "active",
                    "sess_legacy",
                    now,
                    now,
                    now,
                    None,
                    None,
                    "Keep migration safe",
                    "Existing databases have session foreign keys.",
                    None,
                    None,
                    None,
                    None,
                    None,
                    "create",
                    "initial",
                    now,
                    "sess_legacy",
                ),
            )
            raw.commit()

        store = ContextStore(db_path)
        store.initialize()

        with store.connect() as conn:
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            session = conn.execute(
                "SELECT project_id, scope_type, scope_id FROM sessions"
            ).fetchone()
            record = conn.execute(
                "SELECT project_id, scope_type, scope_id, source_session_id FROM records"
            ).fetchone()
            version = conn.execute(
                "SELECT project_id, scope_type, scope_id, changed_by_session_id FROM record_versions"
            ).fetchone()

        assert session["project_id"] == "proj_legacy"
        assert session["scope_type"] == "project"
        assert session["scope_id"] == "proj_legacy"
        assert record["source_session_id"] == "sess_legacy"
        assert record["scope_id"] == "proj_legacy"
        assert version["changed_by_session_id"] == "sess_legacy"
        assert version["scope_id"] == "proj_legacy"

    def test_initialize_normalizes_existing_timestamp_text_for_filters(
        self, mock_seeded
    ):
        store, pid = mock_seeded
        rec = store.create_record(
            project_id=pid,
            session_id="sess_test",
            kind="fact",
            title="Legacy timestamp fact",
            body="Existing DB rows may have timestamps ending in Z.",
            created_at="2026-01-01T00:00:00+00:00",
        )
        with store.connect() as conn:
            conn.execute(
                """
                UPDATE records
                SET created_at = ?, updated_at = ?, valid_from = ?, valid_until = ?
                WHERE record_id = ?
                """,
                (
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T03:00:00+03:00",
                    "2026-01-01T00:00:00Z",
                    "2026-01-02T00:00:00Z",
                    rec["record_id"],
                ),
            )
            conn.execute(
                """
                UPDATE record_versions
                SET created_at = ?, updated_at = ?, valid_from = ?, valid_until = ?, changed_at = ?
                WHERE record_id = ?
                """,
                (
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T03:00:00+03:00",
                    "2026-01-01T00:00:00Z",
                    "2026-01-02T00:00:00Z",
                    "2026-01-01T00:00:00Z",
                    rec["record_id"],
                ),
            )

        store.initialize()

        result = store.query(
            entity="records",
            mode="list",
            project_ids=[pid],
            created_since="2026-01-01T00:00:00Z",
            created_until="2026-01-01T00:00:00Z",
            include_archived=True,
        )
        assert any(row["record_id"] == rec["record_id"] for row in result["rows"])
        with store.connect() as conn:
            row = conn.execute(
                "SELECT created_at, updated_at, valid_from, valid_until FROM records WHERE record_id = ?",
                (rec["record_id"],),
            ).fetchone()
            version = conn.execute(
                """
                SELECT created_at, updated_at, valid_from, valid_until, changed_at
                FROM record_versions
                WHERE record_id = ?
                """,
                (rec["record_id"],),
            ).fetchone()
        assert row["created_at"] == "2026-01-01T00:00:00+00:00"
        assert row["updated_at"] == "2026-01-01T00:00:00+00:00"
        assert row["valid_from"] == "2026-01-01T00:00:00+00:00"
        assert row["valid_until"] == "2026-01-02T00:00:00+00:00"
        assert version["created_at"] == "2026-01-01T00:00:00+00:00"
        assert version["updated_at"] == "2026-01-01T00:00:00+00:00"
        assert version["valid_from"] == "2026-01-01T00:00:00+00:00"
        assert version["valid_until"] == "2026-01-02T00:00:00+00:00"
        assert version["changed_at"] == "2026-01-01T00:00:00+00:00"

    def test_validate_schema_detects_missing_columns(self, tmp_path, mock_embeddings):
        db_path = tmp_path / "bad.db"
        with sqlite3.connect(db_path) as raw:
            raw.execute(
                "CREATE TABLE records ("
                "record_id TEXT PRIMARY KEY, project_id TEXT, kind TEXT, "
                "status TEXT, created_at TEXT, updated_at TEXT, "
                "valid_from TEXT, source_session_id TEXT)"
            )
            raw.commit()
        store = ContextStore(db_path)
        with pytest.raises(
            sqlite3.OperationalError, match="context schema incompatible"
        ):
            store.initialize()

    def test_initialize_and_register_project_do_not_require_embedding_provider(
        self, tmp_path, monkeypatch, project_id
    ):
        db_path = tmp_path / "context.sqlite3"
        store = ContextStore(db_path)
        monkeypatch.setattr(
            "lerim.context.store.get_embedding_provider",
            lambda: (_ for _ in ()).throw(RuntimeError("embedding provider should not load")),
        )
        store.initialize()
        result = store.register_project(project_id)
        assert result["project_id"] == project_id.project_id

    def test_fetch_record_and_query_sessions_do_not_require_embedding_provider(
        self, tmp_path, monkeypatch, project_id
    ):
        db_path = tmp_path / "context.sqlite3"
        store = ContextStore(db_path)
        monkeypatch.setattr(
            "lerim.context.store.get_embedding_provider",
            lambda: (_ for _ in ()).throw(RuntimeError("embedding provider should not load")),
        )
        store.initialize()
        store.register_project(project_id)
        store.upsert_session(
            project_id=project_id.project_id,
            session_id="sess_offline",
            agent_type="test",
            source_trace_ref="offline.jsonl",
            repo_path=str(project_id.repo_path),
            cwd=str(project_id.repo_path),
            started_at="2026-01-01T00:00:00Z",
            model_name="test-model",
            instructions_text=None,
            prompt_text=None,
        )
        now = _utc_now()
        with store.connect() as conn:
            conn.execute(
                """
                INSERT INTO records(
                    record_id, project_id, scope_type, scope_id, scope_label,
                    source_name, source_profile, kind, title, body, status,
                    source_session_id, created_at, updated_at, valid_from,
                    valid_until, superseded_by_record_id, decision, why,
                    alternatives, consequences, user_intent, what_happened, outcomes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "rec_offline",
                    project_id.project_id,
                    "project",
                    project_id.project_id,
                    project_id.project_slug,
                    "test",
                    "test",
                    "fact",
                    "Offline fact",
                    "Exact reads should not need embeddings.",
                    "active",
                    "sess_offline",
                    now,
                    now,
                    now,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
            )
        fetched = store.fetch_record("rec_offline", project_ids=[project_id.project_id])
        sessions = store.query(entity="sessions", mode="count", project_ids=[project_id.project_id])
        assert fetched is not None
        assert sessions["count"] == 1


class TestRegisterProject:
    def test_register_project_inserts_row(self, mock_store, project_id):
        result = mock_store.register_project(project_id)
        assert result["project_id"] == project_id.project_id
        assert result["project_slug"] == project_id.project_slug
        with mock_store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE project_id=?",
                (project_id.project_id,),
            ).fetchone()
        assert row is not None
        assert row["project_slug"] == project_id.project_slug

    def test_register_project_upsert_updates(self, mock_store, project_id):
        mock_store.register_project(project_id)
        updated = project_id.__class__(
            project_id=project_id.project_id,
            project_slug="new-slug",
            repo_path=project_id.repo_path,
        )
        result = mock_store.register_project(updated)
        assert result["project_slug"] == "new-slug"

    def test_upsert_session_inserts_row(self, mock_store, project_id):
        mock_store.register_project(project_id)
        result = mock_store.upsert_session(
            project_id=project_id.project_id,
            session_id="sess_001",
            agent_type="codex",
            source_trace_ref="trace.jsonl",
            repo_path="/tmp/repo",
            cwd="/tmp/repo",
            started_at="2026-01-01T00:00:00Z",
            model_name="gpt-4",
            instructions_text=None,
            prompt_text=None,
        )
        assert result["session_id"] == "sess_001"
        with mock_store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id=?", ("sess_001",)
            ).fetchone()
        assert row is not None
        assert row["agent_type"] == "codex"
        assert row["scope_type"] == "project"
        assert row["scope_id"] == project_id.project_id

    def test_upsert_session_updates_existing(self, mock_store, project_id):
        mock_store.register_project(project_id)
        mock_store.upsert_session(
            project_id=project_id.project_id,
            session_id="sess_001",
            agent_type="codex",
            source_trace_ref="old.jsonl",
            repo_path=None,
            cwd=None,
            started_at=None,
            model_name=None,
            instructions_text=None,
            prompt_text=None,
        )
        mock_store.upsert_session(
            project_id=project_id.project_id,
            session_id="sess_001",
            agent_type="claude",
            source_trace_ref="new.jsonl",
            repo_path=None,
            cwd=None,
            started_at=None,
            model_name=None,
            instructions_text=None,
            prompt_text=None,
        )
        with mock_store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id=?", ("sess_001",)
            ).fetchone()
        assert row["agent_type"] == "claude"
        assert row["source_trace_ref"] == "new.jsonl"

    def test_upsert_session_accepts_generic_scope(self, mock_store):
        scope = resolve_scope_identity(scope_type="domain", scope="support")
        result = mock_store.upsert_session(
            project_id=None,
            session_id="sess_generic",
            agent_type="generic-agent",
            source_trace_ref="generic.jsonl",
            repo_path=None,
            cwd=None,
            started_at="2026-01-01T00:00:00Z",
            model_name="test-model",
            instructions_text=None,
            prompt_text=None,
            scope_identity=scope,
            source_name="customer-bot",
            source_profile="support",
        )

        with mock_store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                ("sess_generic",),
            ).fetchone()

        assert result["scope_type"] == "domain"
        assert row["project_id"] is None
        assert row["scope_id"] == scope.scope_id
        assert row["source_name"] == "customer-bot"


class TestCreateRecord:
    def test_create_decision(self, mock_seeded):
        store, pid = mock_seeded
        rec = _make_decision(store, pid)
        assert rec["kind"] == "decision"
        assert rec["decision"] == "Use SQLite"
        assert rec["why"] == "Simplicity and reliability."
        assert rec["status"] == "active"

    def test_create_preference(self, mock_seeded):
        store, pid = mock_seeded
        rec = store.create_record(
            project_id=pid,
            session_id="sess_test",
            kind="preference",
            title="Prefer explicit errors",
            body="Always raise explicit exceptions.",
        )
        assert rec["kind"] == "preference"
        assert rec["decision"] is None
        assert rec["user_intent"] is None

    def test_create_constraint(self, mock_seeded):
        store, pid = mock_seeded
        rec = store.create_record(
            project_id=pid,
            session_id="sess_test",
            kind="constraint",
            title="No raw SQL",
            body="Never expose raw SQL as a tool.",
        )
        assert rec["kind"] == "constraint"

    def test_create_fact(self, mock_seeded):
        store, pid = mock_seeded
        rec = store.create_record(
            project_id=pid,
            session_id="sess_test",
            kind="fact",
            title="Python 3.12 required",
            body="Runtime uses features from 3.12.",
        )
        assert rec["kind"] == "fact"

    def test_create_reference(self, mock_seeded):
        store, pid = mock_seeded
        rec = store.create_record(
            project_id=pid,
            session_id="sess_test",
            kind="reference",
            title="sqlite-vec docs",
            body="Virtual table API for vector search.",
        )
        assert rec["kind"] == "reference"

    def test_create_episode(self, mock_seeded):
        store, pid = mock_seeded
        rec = _make_episode(store, pid)
        assert rec["kind"] == "episode"
        assert rec["user_intent"] == "Fix the flaky test"
        assert rec["what_happened"] == "Identified race condition in worker pool"

    def test_create_generic_scoped_record_without_project(self, mock_store):
        scope = resolve_scope_identity(scope_type="domain", scope="support")
        mock_store.upsert_session(
            project_id=None,
            session_id="sess_generic",
            agent_type="generic-agent",
            source_trace_ref="generic.jsonl",
            repo_path=None,
            cwd=None,
            started_at=None,
            model_name="test-model",
            instructions_text=None,
            prompt_text=None,
            scope_identity=scope,
            source_name="customer-bot",
            source_profile="support",
        )

        rec = mock_store.create_record(
            project_id=None,
            session_id="sess_generic",
            kind="fact",
            title="Escalations need account IDs",
            body="Support escalations should preserve the account identifier.",
            scope_identity=scope,
            source_name="customer-bot",
            source_profile="support",
        )

        assert rec["project_id"] is None
        assert rec["scope_type"] == "domain"
        assert rec["scope_id"] == scope.scope_id
        assert rec["source_name"] == "customer-bot"
        assert rec["versions"][0]["scope_id"] == scope.scope_id

    def test_create_archived_record_sets_valid_until(self, mock_seeded):
        store, pid = mock_seeded
        rec = _make_episode(store, pid, status="archived")
        assert rec["status"] == "archived"
        assert rec["valid_until"] == rec["updated_at"]

    def test_fts_failure_does_not_roll_back_canonical_create(
        self, mock_seeded, monkeypatch
    ):
        store, pid = mock_seeded

        def fail_fts(*_args, **_kwargs):
            raise RuntimeError("fts failed")

        monkeypatch.setattr(store, "_upsert_fts", fail_fts)
        rec = store.create_record(
            project_id=pid,
            session_id="sess_test",
            kind="fact",
            title="Canonical fact",
            body="Canonical rows should persist without FTS.",
        )

        with store.connect() as conn:
            record_count = conn.execute(
                "SELECT COUNT(*) FROM records WHERE title = ?",
                ("Canonical fact",),
            ).fetchone()[0]
            version_count = conn.execute(
                "SELECT COUNT(*) FROM record_versions WHERE title = ?",
                ("Canonical fact",),
            ).fetchone()[0]
        assert rec["title"] == "Canonical fact"
        assert record_count == 1
        assert version_count == 1

    def test_embedding_failure_does_not_roll_back_canonical_create(
        self, mock_seeded, monkeypatch
    ):
        store, pid = mock_seeded

        def fail_embedding(*_args, **_kwargs):
            raise RuntimeError("embedding failed")

        monkeypatch.setattr(store, "_upsert_embedding", fail_embedding)

        rec = store.create_record(
            project_id=pid,
            session_id="sess_test",
            kind="fact",
            title="Canonical fact",
            body="Canonical rows should persist without embeddings.",
        )

        fetched = store.fetch_record(rec["record_id"], project_ids=[pid], include_versions=True)
        assert fetched is not None
        assert fetched["title"] == "Canonical fact"
        assert len(fetched["versions"]) == 1

    def test_create_normalizes_stored_timestamps_to_utc(self, mock_seeded):
        store, pid = mock_seeded
        rec = store.create_record(
            project_id=pid,
            session_id="sess_test",
            kind="fact",
            title="Timestamp fact",
            body="Stored timestamps should use one UTC ISO representation.",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T03:00:00+03:00",
            valid_from="2026-01-01T04:30:00+03:00",
            valid_until="2026-01-01T03:00:00Z",
        )
        assert rec["created_at"] == "2026-01-01T00:00:00+00:00"
        assert rec["updated_at"] == "2026-01-01T00:00:00+00:00"
        assert rec["valid_from"] == "2026-01-01T01:30:00+00:00"
        assert rec["valid_until"] == "2026-01-01T03:00:00+00:00"

    def test_invalid_kind(self, mock_seeded):
        store, pid = mock_seeded
        with pytest.raises(ValueError, match="invalid_kind"):
            store.create_record(
                project_id=pid,
                session_id="sess_test",
                kind="bad",
                title="T",
                body="B",
            )

    def test_invalid_status(self, mock_seeded):
        store, pid = mock_seeded
        with pytest.raises(ValueError, match="invalid_status"):
            store.create_record(
                project_id=pid,
                session_id="sess_test",
                kind="fact",
                title="T",
                body="B",
                status="pending",
            )

    def test_title_required(self, mock_seeded):
        store, pid = mock_seeded
        with pytest.raises(ValueError, match="title_required"):
            store.create_record(
                project_id=pid,
                session_id="sess_test",
                kind="fact",
                title="",
                body="B",
            )

    def test_body_required(self, mock_seeded):
        store, pid = mock_seeded
        with pytest.raises(ValueError, match="body_required"):
            store.create_record(
                project_id=pid,
                session_id="sess_test",
                kind="fact",
                title="T",
                body="  ",
            )

    def test_title_too_long(self, mock_seeded):
        store, pid = mock_seeded
        with pytest.raises(ValueError, match="title_too_long"):
            store.create_record(
                project_id=pid,
                session_id="sess_test",
                kind="fact",
                title="x" * (MAX_RECORD_TITLE_CHARS + 1),
                body="B",
            )

    def test_episode_body_too_long(self, mock_seeded):
        store, pid = mock_seeded
        with pytest.raises(ValueError, match="episode_body_too_long"):
            store.create_record(
                project_id=pid,
                session_id="sess_test",
                kind="episode",
                title="T",
                body="b" * (MAX_EPISODE_BODY_CHARS + 1),
                user_intent="i",
                what_happened="h",
            )

    def test_durable_body_too_long(self, mock_seeded):
        store, pid = mock_seeded
        with pytest.raises(ValueError, match="record_body_too_long"):
            store.create_record(
                project_id=pid,
                session_id="sess_test",
                kind="fact",
                title="T",
                body="b" * (MAX_DURABLE_BODY_CHARS + 1),
            )

    def test_episode_user_intent_too_long(self, mock_seeded):
        store, pid = mock_seeded
        with pytest.raises(ValueError, match="episode_user_intent_too_long"):
            store.create_record(
                project_id=pid,
                session_id="sess_test",
                kind="episode",
                title="T",
                body="B",
                user_intent="u" * (MAX_EPISODE_USER_INTENT_CHARS + 1),
                what_happened="h",
            )

    def test_episode_what_happened_too_long(self, mock_seeded):
        store, pid = mock_seeded
        with pytest.raises(ValueError, match="episode_what_happened_too_long"):
            store.create_record(
                project_id=pid,
                session_id="sess_test",
                kind="episode",
                title="T",
                body="B",
                user_intent="i",
                what_happened="w" * (MAX_EPISODE_WHAT_HAPPENED_CHARS + 1),
            )

    def test_episode_outcomes_too_long(self, mock_seeded):
        store, pid = mock_seeded
        with pytest.raises(ValueError, match="episode_outcomes_too_long"):
            store.create_record(
                project_id=pid,
                session_id="sess_test",
                kind="episode",
                title="T",
                body="B",
                user_intent="i",
                what_happened="h",
                outcomes="o" * (MAX_EPISODE_OUTCOMES_CHARS + 1),
            )

    def test_decision_requires_decision_and_why(self, mock_seeded):
        store, pid = mock_seeded
        with pytest.raises(ValueError, match="decision_requires_decision_and_why"):
            store.create_record(
                project_id=pid,
                session_id="sess_test",
                kind="decision",
                title="T",
                body="B",
                decision="yes",
                why=None,
            )

    def test_decision_requires_both_decision_and_why(self, mock_seeded):
        store, pid = mock_seeded
        with pytest.raises(ValueError, match="decision_requires_decision_and_why"):
            store.create_record(
                project_id=pid,
                session_id="sess_test",
                kind="decision",
                title="T",
                body="B",
                decision=None,
                why="reason",
            )

    def test_episode_requires_user_intent_and_what_happened(self, mock_seeded):
        store, pid = mock_seeded
        with pytest.raises(
            ValueError, match="episode_requires_user_intent_and_what_happened"
        ):
            store.create_record(
                project_id=pid,
                session_id="sess_test",
                kind="episode",
                title="T",
                body="B",
                user_intent=None,
                what_happened=None,
            )

    def test_episode_requires_session_id(self, mock_seeded):
        store, pid = mock_seeded
        with pytest.raises(ValueError, match="episode_requires_session_id"):
            store.create_record(
                project_id=pid,
                session_id=None,
                kind="episode",
                title="T",
                body="B",
                user_intent="i",
                what_happened="h",
            )

    def test_duplicate_episode_for_session(self, mock_seeded):
        store, pid = mock_seeded
        _make_episode(store, pid)
        with pytest.raises(ValueError, match="duplicate_episode_for_session"):
            _make_episode(store, pid)

    def test_creates_first_version(self, mock_seeded):
        store, pid = mock_seeded
        rec = _make_decision(store, pid)
        assert "versions" in rec
        assert len(rec["versions"]) == 1
        assert rec["versions"][0]["version_no"] == 1
        assert rec["versions"][0]["change_kind"] == "create"

    def test_custom_record_id(self, mock_seeded):
        store, pid = mock_seeded
        rec = store.create_record(
            project_id=pid,
            session_id="sess_test",
            kind="fact",
            title="T",
            body="B",
            record_id="rec_custom_123",
        )
        assert rec["record_id"] == "rec_custom_123"

    def test_case_insensitive_kind_and_status(self, mock_seeded):
        store, pid = mock_seeded
        rec = store.create_record(
            project_id=pid,
            session_id="sess_test",
            kind="DECISION",
            title="T",
            body="B",
            status="ACTIVE",
            decision="D",
            why="W",
        )
        assert rec["kind"] == "decision"
        assert rec["status"] == "active"


class TestUpdateRecord:
    def test_partial_title_update(self, mock_seeded):
        store, pid = mock_seeded
        rec = _make_decision(store, pid)
        updated = store.update_record(
            record_id=rec["record_id"],
            session_id="sess_test",
            project_ids=[pid],
            changes={"title": "Updated title"},
        )
        assert updated["title"] == "Updated title"
        assert updated["body"] == rec["body"]

    def test_appends_version(self, mock_seeded):
        store, pid = mock_seeded
        rec = _make_decision(store, pid)
        updated = store.update_record(
            record_id=rec["record_id"],
            session_id="sess_test",
            project_ids=[pid],
            changes={"title": "V2 title"},
        )
        assert len(updated["versions"]) == 2
        vs = sorted(updated["versions"], key=lambda v: v["version_no"])
        assert vs[0]["version_no"] == 1
        assert vs[0]["change_kind"] == "create"
        assert vs[1]["version_no"] == 2
        assert vs[1]["change_kind"] == "update"
        assert vs[1]["title"] == "V2 title"

    def test_not_found_raises(self, mock_seeded):
        store, pid = mock_seeded
        with pytest.raises(ValueError, match="record_not_found"):
            store.update_record(
                record_id="rec_nonexistent",
                session_id=None,
                project_ids=[pid],
                changes={"title": "X"},
            )

    def test_out_of_scope_raises(self, mock_seeded):
        store, pid = mock_seeded
        rec = _make_decision(store, pid)
        with pytest.raises(ValueError, match="record_out_of_scope"):
            store.update_record(
                record_id=rec["record_id"],
                session_id=None,
                project_ids=["proj_other"],
                changes={"title": "X"},
            )

    def test_empty_project_ids_fail_closed(self, mock_seeded):
        store, pid = mock_seeded
        rec = _make_decision(store, pid)
        with pytest.raises(ValueError, match="record_out_of_scope"):
            store.update_record(
                record_id=rec["record_id"],
                session_id=None,
                project_ids=[],
                changes={"title": "Blocked title"},
            )
        fetched = store.fetch_record(rec["record_id"], project_ids=[pid], include_versions=True)
        assert fetched is not None
        assert fetched["title"] == rec["title"]
        assert len(fetched["versions"]) == 1

    def test_none_project_ids_skips_scope_check(self, mock_seeded):
        store, pid = mock_seeded
        rec = _make_decision(store, pid)
        updated = store.update_record(
            record_id=rec["record_id"],
            session_id="sess_test",
            project_ids=None,
            changes={"title": "New title"},
        )
        assert updated["title"] == "New title"

    def test_change_reason_recorded(self, mock_seeded):
        store, pid = mock_seeded
        rec = _make_decision(store, pid)
        updated = store.update_record(
            record_id=rec["record_id"],
            session_id="sess_test",
            project_ids=[pid],
            changes={"title": "V2"},
            change_reason="correcting typo",
        )
        v2 = [v for v in updated["versions"] if v["version_no"] == 2][0]
        assert v2["change_reason"] == "correcting typo"

    def test_empty_changes_raise_no_changes(self, mock_seeded):
        store, pid = mock_seeded
        rec = _make_decision(store, pid)
        with pytest.raises(ValueError, match="no_changes"):
            store.update_record(
                record_id=rec["record_id"],
                session_id="sess_test",
                project_ids=[pid],
                changes={},
            )
        fetched = store.fetch_record(rec["record_id"], project_ids=[pid], include_versions=True)
        assert fetched is not None
        assert len(fetched["versions"]) == 1

    def test_effectively_unchanged_fields_raise_no_changes(self, mock_seeded):
        store, pid = mock_seeded
        rec = _make_decision(store, pid)
        with pytest.raises(ValueError, match="no_changes"):
            store.update_record(
                record_id=rec["record_id"],
                session_id="sess_test",
                project_ids=[pid],
                changes={"title": rec["title"], "updated_at": "2026-02-01T00:00:00Z"},
            )
        fetched = store.fetch_record(rec["record_id"], project_ids=[pid], include_versions=True)
        assert fetched is not None
        assert fetched["updated_at"] == rec["updated_at"]
        assert len(fetched["versions"]) == 1

    def test_fts_failure_does_not_roll_back_canonical_update(
        self, mock_seeded, monkeypatch
    ):
        store, pid = mock_seeded
        rec = _make_decision(store, pid)

        def fail_fts(*_args, **_kwargs):
            raise RuntimeError("fts failed")

        monkeypatch.setattr(store, "_upsert_fts", fail_fts)
        updated = store.update_record(
            record_id=rec["record_id"],
            session_id="sess_test",
            project_ids=[pid],
            changes={"title": "Canonical FTS-independent title"},
        )

        fetched = store.fetch_record(rec["record_id"], project_ids=[pid], include_versions=True)
        health = store.index_health(project_ids=[pid])
        assert updated["title"] == "Canonical FTS-independent title"
        assert fetched is not None
        assert fetched["title"] == "Canonical FTS-independent title"
        assert len(fetched["versions"]) == 2
        assert health["stale_fts_count"] == 1

    def test_embedding_failure_does_not_roll_back_canonical_update(
        self, mock_seeded, monkeypatch
    ):
        store, pid = mock_seeded
        rec = _make_decision(store, pid)

        def fail_embedding(*_args, **_kwargs):
            raise RuntimeError("embedding failed")

        monkeypatch.setattr(store, "_upsert_embedding", fail_embedding)

        updated = store.update_record(
            record_id=rec["record_id"],
            session_id="sess_test",
            project_ids=[pid],
            changes={"title": "Canonical update"},
        )

        fetched = store.fetch_record(rec["record_id"], project_ids=[pid], include_versions=True)
        health = store.index_health(project_ids=[pid])
        assert updated["title"] == "Canonical update"
        assert fetched is not None
        assert fetched["title"] == "Canonical update"
        assert len(fetched["versions"]) == 2
        assert health["stale_embedding_count"] == 1


class TestArchiveRecord:
    def test_recent_active_non_episode_raises(self, mock_seeded):
        store, pid = mock_seeded
        rec = _make_decision(store, pid)
        with pytest.raises(ValueError, match="refuse_archive_recent_active_record"):
            store.archive_record(
                record_id=rec["record_id"],
                session_id=None,
                project_ids=[pid],
            )

    def test_episode_exempt_from_fresh_protection(self, mock_seeded):
        store, pid = mock_seeded
        rec = _make_episode(store, pid)
        result = store.archive_record(
            record_id=rec["record_id"],
            session_id=None,
            project_ids=[pid],
        )
        assert result["status"] == "archived"

    def test_superseded_exempt_from_fresh_protection(self, mock_seeded):
        store, pid = mock_seeded
        rec_a = _make_decision(store, pid)
        rec_b = store.create_record(
            project_id=pid,
            session_id="sess_test",
            kind="fact",
            title="Replacement",
            body="B",
        )
        store.supersede_record(
            record_id=rec_a["record_id"],
            session_id=None,
            project_ids=[pid],
            replacement_record_id=rec_b["record_id"],
        )
        result = store.archive_record(
            record_id=rec_a["record_id"],
            session_id=None,
            project_ids=[pid],
        )
        assert result["status"] == "archived"

    def test_not_found_raises(self, mock_seeded):
        store, pid = mock_seeded
        with pytest.raises(ValueError, match="record_not_found"):
            store.archive_record(
                record_id="rec_missing",
                session_id=None,
                project_ids=[pid],
            )

    def test_empty_project_ids_fail_closed(self, mock_seeded):
        store, pid = mock_seeded
        rec = _make_episode(store, pid)
        with pytest.raises(ValueError, match="record_out_of_scope"):
            store.archive_record(
                record_id=rec["record_id"],
                session_id=None,
                project_ids=[],
            )
        fetched = store.fetch_record(rec["record_id"], project_ids=[pid], include_versions=True)
        assert fetched is not None
        assert fetched["status"] == "active"
        assert len(fetched["versions"]) == 1

    def test_old_record_archives_ok(self, mock_seeded):
        store, pid = mock_seeded
        rec = _make_decision(store, pid)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        with store.connect() as conn:
            conn.execute(
                "UPDATE records SET created_at=? WHERE record_id=?",
                (old_ts, rec["record_id"]),
            )
        result = store.archive_record(
            record_id=rec["record_id"],
            session_id=None,
            project_ids=[pid],
        )
        assert result["status"] == "archived"
        assert result["valid_until"] is not None


class TestSupersedeRecord:
    def test_happy_path(self, mock_seeded):
        store, pid = mock_seeded
        rec_a = _make_decision(store, pid)
        rec_b = store.create_record(
            project_id=pid,
            session_id="sess_test",
            kind="decision",
            title="New decision",
            body="B",
            decision="D",
            why="W",
        )
        old_ts = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        with store.connect() as conn:
            conn.execute(
                "UPDATE records SET created_at=? WHERE record_id=?",
                (old_ts, rec_a["record_id"]),
            )
        result = store.supersede_record(
            record_id=rec_a["record_id"],
            session_id=None,
            project_ids=[pid],
            replacement_record_id=rec_b["record_id"],
        )
        assert result["superseded_by_record_id"] == rec_b["record_id"]
        assert result["valid_until"] is not None

    def test_replacement_not_found_raises(self, mock_seeded):
        store, pid = mock_seeded
        rec = _make_decision(store, pid)
        with pytest.raises(ValueError, match="replacement_record_not_found"):
            store.supersede_record(
                record_id=rec["record_id"],
                session_id=None,
                project_ids=[pid],
                replacement_record_id="rec_ghost",
            )

    def test_empty_project_ids_fail_closed(self, mock_seeded):
        store, pid = mock_seeded
        old = _make_decision(store, pid)
        replacement = store.create_record(
            project_id=pid,
            session_id="sess_test",
            kind="fact",
            title="Replacement fact",
            body="Replacement body.",
        )
        with pytest.raises(ValueError, match="record_out_of_scope"):
            store.supersede_record(
                record_id=old["record_id"],
                session_id=None,
                project_ids=[],
                replacement_record_id=replacement["record_id"],
            )
        fetched = store.fetch_record(old["record_id"], project_ids=[pid], include_versions=True)
        assert fetched is not None
        assert fetched["superseded_by_record_id"] is None
        assert len(fetched["versions"]) == 1


class TestFetchRecord:
    def test_found(self, mock_seeded):
        store, pid = mock_seeded
        rec = _make_decision(store, pid)
        fetched = store.fetch_record(rec["record_id"])
        assert fetched is not None
        assert fetched["record_id"] == rec["record_id"]

    def test_not_found(self, mock_seeded):
        store, pid = mock_seeded
        assert store.fetch_record("rec_missing") is None

    def test_with_versions(self, mock_seeded):
        store, pid = mock_seeded
        rec = _make_decision(store, pid)
        fetched = store.fetch_record(rec["record_id"], include_versions=True)
        assert "versions" in fetched
        assert len(fetched["versions"]) >= 1

    def test_without_versions(self, mock_seeded):
        store, pid = mock_seeded
        rec = _make_decision(store, pid)
        fetched = store.fetch_record(rec["record_id"], include_versions=False)
        assert "versions" not in fetched

    def test_scope_filter(self, mock_seeded):
        store, pid = mock_seeded
        rec = _make_decision(store, pid)
        assert store.fetch_record(rec["record_id"], project_ids=[pid]) is not None
        assert store.fetch_record(rec["record_id"], project_ids=["proj_other"]) is None


class TestQuery:
    def test_records_list(self, mock_seeded):
        store, pid = mock_seeded
        _make_decision(store, pid)
        result = store.query(entity="records", mode="list", project_ids=[pid])
        assert result["entity"] == "records"
        assert result["mode"] == "list"
        assert result["count"] >= 1
        assert len(result["rows"]) >= 1

    def test_records_count(self, mock_seeded):
        store, pid = mock_seeded
        _make_decision(store, pid)
        result = store.query(entity="records", mode="count", project_ids=[pid])
        assert result["entity"] == "records"
        assert result["mode"] == "count"
        assert result["count"] >= 1

    def test_empty_project_ids_match_no_query_rows(self, mock_seeded):
        store, pid = mock_seeded
        _make_decision(store, pid)

        records = store.query(entity="records", mode="list", project_ids=[])
        record_count = store.query(entity="records", mode="count", project_ids=[])
        sessions = store.query(entity="sessions", mode="count", project_ids=[])
        versions = store.query(entity="versions", mode="count", project_ids=[])

        assert records["rows"] == []
        assert record_count["count"] == 0
        assert sessions["count"] == 0
        assert versions["count"] == 0

    def test_invalid_entity_raises(self, mock_seeded):
        store, pid = mock_seeded
        with pytest.raises(ValueError, match="invalid_query_entity"):
            store.query(entity="bogus", mode="list", project_ids=[pid])

    def test_invalid_mode_raises(self, mock_seeded):
        store, pid = mock_seeded
        with pytest.raises(ValueError, match="invalid_query_mode"):
            store.query(entity="records", mode="bogus", project_ids=[pid])

    def test_invalid_order_raises(self, mock_seeded):
        store, pid = mock_seeded
        with pytest.raises(ValueError, match="invalid_query_order"):
            store.query(
                entity="records", mode="list", project_ids=[pid], order_by="bad"
            )

    @pytest.mark.parametrize("order_by", ["updated_at", "valid_from"])
    def test_sessions_reject_non_created_at_order(self, mock_seeded, order_by):
        store, pid = mock_seeded
        with pytest.raises(ValueError, match="invalid_query_order:sessions"):
            store.query(
                entity="sessions",
                mode="list",
                project_ids=[pid],
                order_by=order_by,
            )

    def test_sessions_list(self, mock_seeded):
        store, pid = mock_seeded
        result = store.query(entity="sessions", mode="list", project_ids=[pid])
        assert result["entity"] == "sessions"
        assert len(result["rows"]) >= 1

    def test_versions_list(self, mock_seeded):
        store, pid = mock_seeded
        _make_decision(store, pid)
        result = store.query(entity="versions", mode="list", project_ids=[pid])
        assert result["entity"] == "versions"
        assert result["count"] >= 1

    def test_pagination(self, mock_seeded):
        store, pid = mock_seeded
        for i in range(5):
            store.create_record(
                project_id=pid,
                session_id="sess_test",
                kind="fact",
                title=f"Fact {i}",
                body=f"Body {i}",
            )
        p1 = store.query(
            entity="records", mode="list", project_ids=[pid], limit=2, offset=0
        )
        p2 = store.query(
            entity="records", mode="list", project_ids=[pid], limit=2, offset=2
        )
        assert len(p1["rows"]) == 2
        assert len(p2["rows"]) == 2
        assert {r["record_id"] for r in p1["rows"]}.isdisjoint(
            {r["record_id"] for r in p2["rows"]}
        )

    def test_kind_filter(self, mock_seeded):
        store, pid = mock_seeded
        _make_decision(store, pid)
        store.create_record(
            project_id=pid,
            session_id="sess_test",
            kind="fact",
            title="A fact",
            body="B",
        )
        result = store.query(
            entity="records", mode="list", project_ids=[pid], kind="decision"
        )
        assert all(r["kind"] == "decision" for r in result["rows"])

    def test_status_filter(self, mock_seeded):
        store, pid = mock_seeded
        rec = _make_decision(store, pid)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        with store.connect() as conn:
            conn.execute(
                "UPDATE records SET created_at=? WHERE record_id=?",
                (old_ts, rec["record_id"]),
            )
        store.archive_record(
            record_id=rec["record_id"], session_id=None, project_ids=[pid]
        )
        result = store.query(
            entity="records", mode="list", project_ids=[pid], status="archived"
        )
        assert all(r["status"] == "archived" for r in result["rows"])

    def test_include_total(self, mock_seeded):
        store, pid = mock_seeded
        _make_decision(store, pid)
        result = store.query(
            entity="records",
            mode="list",
            project_ids=[pid],
            include_total=True,
        )
        assert result["total"] is not None
        assert result["total"] >= 1

    def test_sessions_count(self, mock_seeded):
        store, pid = mock_seeded
        result = store.query(entity="sessions", mode="count", project_ids=[pid])
        assert result["entity"] == "sessions"
        assert result["count"] >= 1

    def test_sessions_source_session_id_filters_session_id(self, mock_seeded):
        store, pid = mock_seeded
        store.upsert_session(
            project_id=pid,
            session_id="sess_other",
            agent_type="test",
            source_trace_ref="other.jsonl",
            repo_path="/tmp/other",
            cwd="/tmp/other",
            started_at="2026-01-02T00:00:00Z",
            model_name="test-model",
            instructions_text=None,
            prompt_text=None,
        )
        result = store.query(
            entity="sessions",
            mode="list",
            project_ids=[pid],
            source_session_id="sess_other",
        )
        assert [row["session_id"] for row in result["rows"]] == ["sess_other"]

    def test_sessions_date_only_window_includes_same_day_rows(self, mock_seeded):
        store, pid = mock_seeded
        midday = "2026-04-22T12:00:00+00:00"
        with store.connect() as conn:
            conn.execute(
                "UPDATE sessions SET created_at = ? WHERE session_id = ?",
                (midday, "sess_test"),
            )
        listed = store.query(
            entity="sessions",
            mode="list",
            project_ids=[pid],
            created_since="2026-04-22",
            created_until="2026-04-22",
        )
        counted = store.query(
            entity="sessions",
            mode="count",
            project_ids=[pid],
            created_since="2026-04-22",
            created_until="2026-04-22",
        )
        assert listed["count"] == 1
        assert counted["count"] == 1

    @pytest.mark.parametrize(
        ("filter_kwargs", "filter_name"),
        [
            ({"kind": "fact"}, "kind"),
            ({"status": "active"}, "status"),
            ({"updated_since": "2026-04-22"}, "updated_since"),
            ({"updated_until": "2026-04-22"}, "updated_until"),
            ({"valid_at": "2026-04-22"}, "valid_at"),
            ({"include_archived": False}, "include_archived"),
            ({"include_archived": True}, "include_archived"),
        ],
    )
    def test_sessions_reject_unsupported_filters(
        self, mock_seeded, filter_kwargs, filter_name
    ):
        store, pid = mock_seeded
        with pytest.raises(
            ValueError,
            match=f"unsupported_query_filter:sessions:{filter_name}",
        ):
            store.query(
                entity="sessions",
                mode="list",
                project_ids=[pid],
                **filter_kwargs,
            )

    def test_versions_count(self, mock_seeded):
        store, pid = mock_seeded
        _make_decision(store, pid)
        result = store.query(entity="versions", mode="count", project_ids=[pid])
        assert result["entity"] == "versions"
        assert result["count"] >= 1

    @pytest.mark.parametrize("include_archived", [False, True])
    def test_versions_reject_include_archived_filter(
        self, mock_seeded, include_archived
    ):
        store, pid = mock_seeded
        with pytest.raises(
            ValueError,
            match="unsupported_query_filter:versions:include_archived",
        ):
            store.query(
                entity="versions",
                mode="list",
                project_ids=[pid],
                include_archived=include_archived,
            )

    def test_superseded_records_excluded_from_current_query_results(self, mock_seeded):
        store, pid = mock_seeded
        old_record = _make_decision(store, pid)
        replacement = store.create_record(
            project_id=pid,
            session_id="sess_test",
            kind="decision",
            title="Replacement decision",
            body="New durable truth.",
            decision="Use replacement",
            why="The previous one was superseded.",
        )
        store.supersede_record(
            record_id=old_record["record_id"],
            session_id=None,
            project_ids=[pid],
            replacement_record_id=replacement["record_id"],
        )
        listed = store.query(entity="records", mode="list", project_ids=[pid])
        counted = store.query(entity="records", mode="count", project_ids=[pid])
        row_ids = {row["record_id"] for row in listed["rows"]}
        assert old_record["record_id"] not in row_ids
        assert replacement["record_id"] in row_ids
        assert counted["count"] == len(row_ids)

    def test_date_only_valid_at_includes_records_from_later_same_day(self, mock_seeded):
        store, pid = mock_seeded
        record = _make_decision(store, pid)
        midday = "2026-02-15T12:00:00+00:00"
        with store.connect() as conn:
            conn.execute(
                """
                UPDATE records
                SET valid_from = ?, created_at = ?, updated_at = ?
                WHERE record_id = ?
                """,
                (midday, midday, midday, record["record_id"]),
            )
        result = store.query(
            entity="records",
            mode="list",
            project_ids=[pid],
            valid_at="2026-02-15",
        )
        row_ids = {row["record_id"] for row in result["rows"]}
        assert record["record_id"] in row_ids

    def test_valid_at_includes_archived_rows_without_include_archived(self, mock_seeded):
        store, pid = mock_seeded
        record = store.create_record(
            project_id=pid,
            session_id="sess_test",
            record_id="rec_archived_history",
            kind="fact",
            status="archived",
            title="Archived history",
            body="This archived fact was true during its validity interval.",
            valid_from="2026-01-01T00:00:00Z",
            valid_until="2026-02-01T00:00:00Z",
        )
        result = store.query(
            entity="records",
            mode="list",
            project_ids=[pid],
            valid_at="2026-01-15T02:00:00+02:00",
        )
        assert any(row["record_id"] == record["record_id"] for row in result["rows"])

    def test_valid_at_includes_superseded_historical_rows(self, mock_seeded):
        store, pid = mock_seeded
        old = store.create_record(
            project_id=pid,
            session_id="sess_test",
            record_id="rec_superseded_history",
            kind="fact",
            title="Old provider fact",
            body="This fact was true before the replacement.",
            valid_from="2026-01-01T00:00:00Z",
        )
        replacement = store.create_record(
            project_id=pid,
            session_id="sess_test",
            kind="fact",
            title="New provider fact",
            body="This fact replaced the old one.",
        )
        store.supersede_record(
            record_id=old["record_id"],
            session_id=None,
            project_ids=[pid],
            replacement_record_id=replacement["record_id"],
            valid_until="2026-02-01T00:00:00Z",
        )
        result = store.query(
            entity="records",
            mode="list",
            project_ids=[pid],
            valid_at="2026-01-15T00:00:00Z",
        )
        assert any(row["record_id"] == old["record_id"] for row in result["rows"])

    def test_offset_created_filter_matches_normalized_stored_timestamps(self, mock_seeded):
        store, pid = mock_seeded
        record = store.create_record(
            project_id=pid,
            session_id="sess_test",
            kind="fact",
            title="Offset filter fact",
            body="Offset filters should compare against normalized UTC text.",
            created_at="2026-01-01T03:00:00+03:00",
        )
        result = store.query(
            entity="records",
            mode="list",
            project_ids=[pid],
            created_since="2026-01-01T00:00:00Z",
            created_until="2026-01-01T02:00:00+02:00",
        )
        assert any(row["record_id"] == record["record_id"] for row in result["rows"])


class TestNormalizeRecordPayload:
    def _call(self, **kw):
        defaults = dict(
            kind="fact",
            title="Title",
            body="Body",
            status="active",
            source_session_id=None,
            created_at=_utc_now(),
            updated_at=_utc_now(),
            valid_from=_utc_now(),
            valid_until=None,
            superseded_by_record_id=None,
            decision=None,
            why=None,
            alternatives=None,
            consequences=None,
            user_intent=None,
            what_happened=None,
            outcomes=None,
        )
        defaults.update(kw)
        store = ContextStore("/dev/null")
        return store._normalize_record_payload(**defaults)

    def test_valid_decision(self):
        p = self._call(
            kind="decision", decision="D", why="W", alternatives="A", consequences="C"
        )
        assert p["kind"] == "decision"
        assert p["decision"] == "D"
        assert p["why"] == "W"
        assert p["alternatives"] == "A"
        assert p["consequences"] == "C"
        assert p["user_intent"] is None

    def test_valid_episode(self):
        p = self._call(
            kind="episode",
            source_session_id="sess_1",
            user_intent="intent",
            what_happened="happened",
            outcomes="result",
        )
        assert p["kind"] == "episode"
        assert p["user_intent"] == "intent"
        assert p["what_happened"] == "happened"
        assert p["outcomes"] == "result"
        assert p["decision"] is None

    def test_valid_non_episode_non_decision(self):
        p = self._call(kind="fact")
        assert p["decision"] is None
        assert p["why"] is None
        assert p["alternatives"] is None
        assert p["consequences"] is None
        assert p["user_intent"] is None
        assert p["what_happened"] is None
        assert p["outcomes"] is None

    def test_invalid_kind(self):
        with pytest.raises(ValueError, match="invalid_kind"):
            self._call(kind="bogus")

    def test_invalid_status(self):
        with pytest.raises(ValueError, match="invalid_status"):
            self._call(status="pending")

    def test_title_required(self):
        with pytest.raises(ValueError, match="title_required"):
            self._call(title="")

    def test_body_required(self):
        with pytest.raises(ValueError, match="body_required"):
            self._call(body="  ")

    def test_title_too_long(self):
        with pytest.raises(ValueError, match="title_too_long"):
            self._call(title="x" * (MAX_RECORD_TITLE_CHARS + 1))

    def test_durable_body_too_long(self):
        with pytest.raises(ValueError, match="record_body_too_long"):
            self._call(kind="fact", body="b" * (MAX_DURABLE_BODY_CHARS + 1))

    def test_episode_body_too_long(self):
        with pytest.raises(ValueError, match="episode_body_too_long"):
            self._call(kind="episode", body="b" * (MAX_EPISODE_BODY_CHARS + 1))

    def test_episode_user_intent_too_long(self):
        with pytest.raises(ValueError, match="episode_user_intent_too_long"):
            self._call(
                kind="episode",
                user_intent="u" * (MAX_EPISODE_USER_INTENT_CHARS + 1),
                what_happened="h",
            )

    def test_episode_what_happened_too_long(self):
        with pytest.raises(ValueError, match="episode_what_happened_too_long"):
            self._call(
                kind="episode",
                user_intent="i",
                what_happened="w" * (MAX_EPISODE_WHAT_HAPPENED_CHARS + 1),
            )

    def test_episode_outcomes_too_long(self):
        with pytest.raises(ValueError, match="episode_outcomes_too_long"):
            self._call(
                kind="episode",
                user_intent="i",
                what_happened="h",
                outcomes="o" * (MAX_EPISODE_OUTCOMES_CHARS + 1),
            )

    def test_decision_missing_why(self):
        with pytest.raises(ValueError, match="decision_requires_decision_and_why"):
            self._call(kind="decision", decision="D", why=None)

    def test_decision_missing_decision(self):
        with pytest.raises(ValueError, match="decision_requires_decision_and_why"):
            self._call(kind="decision", decision=None, why="W")

    def test_episode_missing_session_id(self):
        with pytest.raises(ValueError, match="episode_requires_session_id"):
            self._call(
                kind="episode",
                source_session_id=None,
                user_intent="i",
                what_happened="h",
            )

    def test_episode_missing_user_intent(self):
        with pytest.raises(
            ValueError, match="episode_requires_user_intent_and_what_happened"
        ):
            self._call(
                kind="episode",
                source_session_id="sess_1",
                user_intent=None,
                what_happened="h",
            )

    def test_episode_missing_what_happened(self):
        with pytest.raises(
            ValueError, match="episode_requires_user_intent_and_what_happened"
        ):
            self._call(
                kind="episode",
                source_session_id="sess_1",
                user_intent="i",
                what_happened=None,
            )

    def test_non_decision_clears_decision_fields(self):
        p = self._call(
            kind="fact", decision="D", why="W", alternatives="A", consequences="C"
        )
        assert p["decision"] is None
        assert p["why"] is None
        assert p["alternatives"] is None
        assert p["consequences"] is None

    def test_non_episode_clears_episode_fields(self):
        p = self._call(kind="fact", user_intent="U", what_happened="W", outcomes="O")
        assert p["user_intent"] is None
        assert p["what_happened"] is None
        assert p["outcomes"] is None

    def test_whitespace_fields_normalized(self):
        p = self._call(title="  Title  ", body="  Body  ")
        assert p["title"] == "Title"
        assert p["body"] == "Body"


class TestEnsureEpisodeUniqueness:
    def test_non_episode_passes(self, mock_store):
        with mock_store.connect() as conn:
            mock_store._ensure_episode_uniqueness(
                conn,
                project_id="proj_x",
                scope_type="project",
                scope_id="proj_x",
                kind="fact",
                session_id="sess_1",
                exclude_record_id=None,
            )

    def test_none_session_skips_check(self, mock_store):
        with mock_store.connect() as conn:
            mock_store._ensure_episode_uniqueness(
                conn,
                project_id="proj_x",
                scope_type="project",
                scope_id="proj_x",
                kind="episode",
                session_id=None,
                exclude_record_id=None,
            )

    def test_blocks_duplicate(self, mock_seeded):
        store, pid = mock_seeded
        _make_episode(store, pid)
        with store.connect() as conn:
            with pytest.raises(ValueError, match="duplicate_episode_for_session"):
                store._ensure_episode_uniqueness(
                    conn,
                    project_id=pid,
                    scope_type="project",
                    scope_id=pid,
                    kind="episode",
                    session_id="sess_test",
                    exclude_record_id=None,
                )

    def test_allows_update_with_exclude(self, mock_seeded):
        store, pid = mock_seeded
        rec = _make_episode(store, pid)
        with store.connect() as conn:
            store._ensure_episode_uniqueness(
                conn,
                project_id=pid,
                scope_type="project",
                scope_id=pid,
                kind="episode",
                session_id="sess_test",
                exclude_record_id=rec["record_id"],
            )


class TestSearchText:
    def test_decision_includes_all_fields(self):
        store = ContextStore("/dev/null")
        result = store._search_text(
            {
                "kind": "decision",
                "title": "Use SQLite",
                "body": "One DB",
                "decision": "Use SQLite",
                "why": "Simple",
                "alternatives": "JSON",
                "consequences": "Migration",
                "user_intent": None,
                "what_happened": None,
                "outcomes": None,
            }
        )
        assert "kind: decision" in result
        assert "title: Use SQLite" in result
        assert "decision: Use SQLite" in result
        assert "alternatives: JSON" in result

    def test_episode_includes_episode_fields(self):
        store = ContextStore("/dev/null")
        result = store._search_text(
            {
                "kind": "episode",
                "title": "Debug",
                "body": "Fix",
                "decision": None,
                "why": None,
                "alternatives": None,
                "consequences": None,
                "user_intent": "Fix bug",
                "what_happened": "Found race",
                "outcomes": "Test passes",
            }
        )
        assert "user_intent: Fix bug" in result
        assert "what_happened: Found race" in result
        assert "outcomes: Test passes" in result

    def test_plain_record_minimal(self):
        store = ContextStore("/dev/null")
        result = store._search_text(
            {
                "kind": "fact",
                "title": "Python 3.12",
                "body": "Required",
                "decision": None,
                "why": None,
                "alternatives": None,
                "consequences": None,
                "user_intent": None,
                "what_happened": None,
                "outcomes": None,
            }
        )
        assert result.startswith("kind: fact")
        assert "title: Python 3.12" in result


class TestBuildRecordFilterSql:
    def _call(self, **kw):
        store = ContextStore("/dev/null")
        defaults = dict(
            project_ids=None,
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
        defaults.update(kw)
        return store._build_record_filter_sql(**defaults)

    def test_no_filters(self):
        sql, params = self._call()
        assert sql == "1=1"
        assert params == []

    def test_project_ids(self):
        sql, params = self._call(project_ids=["p1", "p2"])
        assert "project_id IN (?, ?)" in sql
        assert params == ["p1", "p2"]

    def test_kind_filters(self):
        sql, params = self._call(kind_filters=["decision", "fact"])
        assert "kind IN (?, ?)" in sql
        assert params == ["decision", "fact"]

    def test_statuses(self):
        sql, params = self._call(statuses=["active"])
        assert "status IN (?)" in sql

    def test_include_archived_false(self):
        sql, params = self._call(include_archived=False)
        assert "status = 'active'" in sql
        assert "valid_from <= ?" in sql
        assert "valid_until IS NULL OR" in sql
        assert len(params) == 2

    def test_include_archived_true_no_status_clause(self):
        sql, params = self._call(include_archived=True)
        assert "status" not in sql
        assert "valid_from <= ?" not in sql

    def test_source_session_id(self):
        sql, params = self._call(source_session_id="sess_1")
        assert "source_session_id = ?" in sql
        assert "sess_1" in params

    def test_date_range_filters(self):
        sql, params = self._call(
            created_since="2026-01-01",
            created_until="2026-06-01",
            updated_since="2026-02-01",
            updated_until="2026-05-01",
        )
        assert "created_at >= ?" in sql
        assert "created_at <= ?" in sql
        assert "updated_at >= ?" in sql
        assert "updated_at <= ?" in sql
        assert len(params) == 4

    def test_date_only_filters_expand_to_full_day_bounds(self):
        sql, params = self._call(
            created_since="2026-01-01",
            created_until="2026-01-01",
            updated_since="2026-02-02",
            updated_until="2026-02-02",
        )
        assert "created_at >= ?" in sql
        assert "created_at <= ?" in sql
        assert "updated_at >= ?" in sql
        assert "updated_at <= ?" in sql
        assert params[0] == "2026-01-01T00:00:00+00:00"
        assert params[1].startswith("2026-01-01T23:59:59")
        assert params[2] == "2026-02-02T00:00:00+00:00"
        assert params[3].startswith("2026-02-02T23:59:59")

    def test_valid_at(self):
        sql, params = self._call(valid_at="2026-03-15")
        assert "valid_from <= ?" in sql
        assert "valid_until IS NULL OR" in sql
        assert params.count("2026-03-15T23:59:59.999999+00:00") == 2

    def test_status_active_applies_current_validity_window(self):
        sql, params = self._call(statuses=["active"], include_archived=True)
        assert "status IN (?)" in sql
        assert "valid_from <= ?" in sql
        assert "valid_until IS NULL OR" in sql
        assert params[0] == "active"
        assert len(params) == 3

    def test_archived_status_does_not_force_current_validity_window(self):
        sql, params = self._call(statuses=["archived"], include_archived=False)
        assert "status IN (?)" in sql
        assert "valid_from <= ?" not in sql
        assert params == ["archived"]

    def test_table_alias(self):
        sql, params = self._call(table_alias="r", project_ids=["p1"])
        assert "r.project_id" in sql
