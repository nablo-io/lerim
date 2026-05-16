"""Comprehensive unit tests for lerim.sessions.catalog module."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lerim.adapters.base import SessionRecord
from lerim.sessions.catalog import (
    _connect,
    claim_session_jobs,
    clear_local_running_job,
    complete_session_job,
    count_fts_indexed,
    count_session_jobs_by_status,
    count_unscoped_sessions_by_agent,
    enqueue_session_job,
    fail_session_job,
    fetch_session_doc,
    get_indexed_run_ids,
    index_session_for_fts,
    init_sessions_db,
    latest_service_run,
    list_queue_jobs,
    list_service_runs,
    list_sessions_window,
    list_unscoped_sessions,
    note_local_running_job,
    queue_health_snapshot,
    reap_stale_running_jobs,
    record_service_run,
    resolve_run_id_prefix,
    retry_all_dead_letter_jobs,
    retry_project_jobs,
    retry_session_job,
    skip_all_dead_letter_jobs,
    skip_project_jobs,
    skip_session_job,
    update_session_extract_fields,
)


@pytest.fixture(autouse=True)
def _reset_init_flag(monkeypatch):
    monkeypatch.setattr("lerim.sessions.catalog._DB_INITIALIZED_PATH", None)
    monkeypatch.setattr("lerim.sessions.catalog._LOCAL_RUNNING_LEASES", {})


def _db(sessions_db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(sessions_db))
    conn.row_factory = sqlite3.Row
    return conn


def _seed(
    run_id: str,
    agent: str = "claude",
    content: str = "test content",
    repo_path: str | None = None,
    repo_name: str | None = None,
    start_time: str = "2026-04-01T10:00:00+00:00",
    **kw: object,
) -> bool:
    return index_session_for_fts(
        run_id=run_id,
        agent_type=agent,
        content=content,
        repo_path=repo_path,
        repo_name=repo_name,
        start_time=start_time,
        **kw,
    )


def _seed_and_enqueue(
    run_id: str,
    repo_path: str = "/tmp/test-project",
    start_time: str = "2026-04-01T10:00:00+00:00",
) -> bool:
    _seed(run_id, start_time=start_time)
    return enqueue_session_job(
        run_id,
        repo_path=repo_path,
        start_time=start_time,
        session_path=f"/tmp/{run_id}.jsonl",
    )


def _set_job_status(run_id: str, status: str, available_at: str | None = None) -> None:
    with _connect() as conn:
        if available_at:
            conn.execute(
                "UPDATE session_jobs SET status = ?, available_at = ? WHERE run_id = ?",
                (status, available_at, run_id),
            )
        else:
            conn.execute(
                "UPDATE session_jobs SET status = ? WHERE run_id = ?",
                (status, run_id),
            )
        conn.commit()


def _make_available_now(run_id: str) -> None:
    past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    with _connect() as conn:
        conn.execute(
            "UPDATE session_jobs SET available_at = ? WHERE run_id = ?",
            (past, run_id),
        )
        conn.commit()


class TestInitAndSchema:
    def test_tables_created(self, sessions_db):
        conn = _db(sessions_db)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "session_docs" in tables
        assert "session_jobs" in tables
        assert "service_runs" in tables
        conn.close()

    def test_connect_sets_busy_timeout(self, sessions_db):
        """Catalog connections wait for busy writers before failing."""
        with _connect() as conn:
            row = conn.execute("PRAGMA busy_timeout").fetchone()

        assert int(row.get("timeout") or 0) == 60000

    def test_fts_virtual_table_created(self, sessions_db):
        conn = _db(sessions_db)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "sessions_fts" in tables
        conn.close()

    def test_triggers_created(self, sessions_db):
        conn = _db(sessions_db)
        triggers = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }
        assert "session_docs_ai" in triggers
        assert "session_docs_ad" in triggers
        assert "session_docs_au" in triggers
        conn.close()

    def test_indexes_created(self, sessions_db):
        conn = _db(sessions_db)
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "idx_session_docs_run" in indexes
        assert "idx_session_docs_agent" in indexes
        assert "idx_session_docs_time" in indexes
        assert "idx_session_jobs_status_available" in indexes
        assert "idx_session_jobs_updated" in indexes
        assert "idx_session_jobs_repo" in indexes
        assert "idx_service_runs_job" in indexes
        assert "idx_service_runs_started" in indexes
        conn.close()

    def test_session_docs_unique_run_id(self, sessions_db):
        _seed("unique-1")
        with _connect() as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO session_docs (run_id, agent_type, content, indexed_at) "
                    "VALUES ('unique-1', 'x', 'y', '2026-01-01T00:00:00Z')"
                )

    def test_session_jobs_unique_run_id_job_type(self, sessions_db):
        _seed_and_enqueue("uniq-job")
        with _connect() as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO session_jobs (run_id, job_type, status, available_at, created_at, updated_at) "
                    "VALUES ('uniq-job', 'extract', 'pending', '2026-01-01', '2026-01-01', '2026-01-01')"
                )

    def test_idempotent_init(self, sessions_db):
        init_sessions_db()
        init_sessions_db()
        conn = _db(sessions_db)
        count = conn.execute("SELECT COUNT(*) FROM session_docs").fetchone()[0]
        assert count == 0
        conn.close()

    def test_concurrent_init_safety(self, tmp_path, monkeypatch):
        """Multiple threads calling init_sessions_db don't crash."""
        import time

        db_file = tmp_path / "concurrent.sqlite3"
        monkeypatch.setattr("lerim.sessions.catalog._db_path", lambda: db_file)
        errors: list[Exception] = []

        def _init():
            for attempt in range(3):
                try:
                    init_sessions_db()
                    return
                except Exception as exc:
                    if attempt == 2:
                        errors.append(exc)
                    time.sleep(0.05)

        threads = [threading.Thread(target=_init) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []

    def test_init_sessions_db_rejects_invalid_sqlite(self, tmp_path, monkeypatch):
        """Invalid SQLite files now fail fast."""
        db_file = tmp_path / "broken.sqlite3"
        db_file.write_text("not a sqlite database", encoding="utf-8")
        monkeypatch.setattr("lerim.sessions.catalog._db_path", lambda: db_file)

        with pytest.raises(sqlite3.DatabaseError):
            init_sessions_db()


class TestFtsIndexing:
    def test_index_returns_true(self, sessions_db):
        assert _seed("fts-1") is True

    def test_index_empty_run_id_returns_false(self, sessions_db):
        assert (
            index_session_for_fts(run_id="", agent_type="claude", content="x") is False
        )

    def test_index_empty_agent_type_returns_false(self, sessions_db):
        assert index_session_for_fts(run_id="abc", agent_type="", content="x") is False

    def test_index_and_count(self, sessions_db):
        _seed("cnt-1")
        _seed("cnt-2")
        assert count_fts_indexed() == 2

    def test_index_replaces_existing(self, sessions_db):
        _seed("rep-1", content="original")
        _seed("rep-1", content="replaced")
        doc = fetch_session_doc("rep-1")
        assert doc is not None
        assert doc["content"] == "replaced"
        assert count_fts_indexed() == 1

    def test_get_indexed_run_ids(self, sessions_db):
        _seed("rid-1")
        _seed("rid-2")
        ids = get_indexed_run_ids()
        assert ids == {"rid-1", "rid-2"}

    def test_get_indexed_run_ids_empty(self, sessions_db):
        assert get_indexed_run_ids() == set()

    def test_fetch_session_doc_found(self, sessions_db):
        _seed("fetch-1", agent="codex", repo_name="myrepo")
        doc = fetch_session_doc("fetch-1")
        assert doc is not None
        assert doc["run_id"] == "fetch-1"
        assert doc["agent_type"] == "codex"
        assert doc["repo_name"] == "myrepo"

    def test_fetch_session_doc_not_found(self, sessions_db):
        assert fetch_session_doc("nonexistent") is None

    def test_fetch_session_doc_empty_run_id(self, sessions_db):
        assert fetch_session_doc("") is None

    def test_fts_trigger_insert(self, sessions_db):
        _seed("fts-ins", content="hello world")
        conn = _db(sessions_db)
        row = conn.execute(
            "SELECT * FROM sessions_fts WHERE run_id = 'fts-ins'"
        ).fetchone()
        assert row is not None
        conn.close()

    def test_fts_trigger_delete(self, sessions_db):
        _seed("fts-del", content="to delete")
        with _connect() as conn:
            conn.execute("DELETE FROM session_docs WHERE run_id = 'fts-del'")
            conn.commit()
        conn = _db(sessions_db)
        row = conn.execute(
            "SELECT * FROM sessions_fts WHERE run_id = 'fts-del'"
        ).fetchone()
        assert row is None
        conn.close()

    def test_fts_trigger_update(self, sessions_db):
        _seed("fts-upd", content="old content", repo_name="old-repo")
        _seed("fts-upd", content="new content", repo_name="new-repo")
        conn = _db(sessions_db)
        rows = conn.execute(
            "SELECT * FROM sessions_fts WHERE run_id = 'fts-upd'"
        ).fetchall()
        assert len(rows) == 1
        conn.close()

    def test_index_with_all_fields(self, sessions_db):
        ok = index_session_for_fts(
            run_id="full-1",
            agent_type="claude",
            content="full content",
            repo_path="/tmp/proj",
            repo_name="proj",
            start_time="2026-04-01T10:00:00+00:00",
            status="completed",
            duration_ms=5000,
            message_count=10,
            tool_call_count=3,
            error_count=0,
            total_tokens=1500,
            summaries='["summary line"]',
            session_path="/tmp/full-1.jsonl",
            content_hash="abc123",
        )
        assert ok is True
        doc = fetch_session_doc("full-1")
        assert doc["duration_ms"] == 5000
        assert doc["message_count"] == 10
        assert doc["tool_call_count"] == 3
        assert doc["total_tokens"] == 1500
        assert doc["content_hash"] == "abc123"

    def test_index_summaries_json_to_summary_text(self, sessions_db):
        index_session_for_fts(
            run_id="sum-1",
            agent_type="claude",
            content="x",
            summaries='["line one", "line two"]',
        )
        doc = fetch_session_doc("sum-1")
        assert doc["summary_text"] == "line one\nline two"

    def test_index_invalid_summaries_json(self, sessions_db):
        index_session_for_fts(
            run_id="sum-2",
            agent_type="claude",
            content="x",
            summaries="not valid json",
        )
        doc = fetch_session_doc("sum-2")
        assert doc["summary_text"] == ""

    def test_index_new_sessions_skips_known_same_hash(
        self, sessions_db, monkeypatch, tmp_path
    ):
        from lerim.sessions.catalog import index_new_sessions

        index_session_for_fts(
            run_id="known-same",
            agent_type="claude",
            content="old content",
            summary_text="old summary",
            session_path="/tmp/known-same.jsonl",
            content_hash="same-hash",
        )
        with _connect() as conn:
            conn.execute(
                "UPDATE session_docs SET indexed_at = ? WHERE run_id = ?",
                ("2000-01-01T00:00:00+00:00", "known-same"),
            )
            conn.commit()

        class FakeAdapter:
            def iter_sessions(self, **kwargs):
                return [
                    SessionRecord(
                        run_id="known-same",
                        agent_type="claude",
                        session_path="/tmp/known-same.jsonl",
                        summaries=["new summary should not index"],
                        content_hash="same-hash",
                    )
                ]

        monkeypatch.setattr(
            "lerim.sessions.catalog.adapter_registry.get_connected_platform_paths",
            lambda path: {"claude": tmp_path},
        )
        monkeypatch.setattr(
            "lerim.sessions.catalog.adapter_registry.get_adapter",
            lambda name: FakeAdapter(),
        )

        details = index_new_sessions(agents=["claude"], return_details=True)

        assert details == []
        doc = fetch_session_doc("known-same")
        assert doc["indexed_at"] == "2000-01-01T00:00:00+00:00"
        assert doc["summary_text"] == "old summary"

    def test_index_new_sessions_marks_known_missing_hash_changed(
        self, sessions_db, monkeypatch, tmp_path
    ):
        from lerim.sessions.catalog import index_new_sessions

        index_session_for_fts(
            run_id="known-missing",
            agent_type="claude",
            content="old content",
            session_path="/tmp/known-missing.jsonl",
            content_hash=None,
        )

        class FakeAdapter:
            def iter_sessions(self, **kwargs):
                return [
                    SessionRecord(
                        run_id="known-missing",
                        agent_type="claude",
                        session_path="/tmp/known-missing.jsonl",
                        summaries=["backfill summary"],
                        content_hash="new-hash",
                    )
                ]

        monkeypatch.setattr(
            "lerim.sessions.catalog.adapter_registry.get_connected_platform_paths",
            lambda path: {"claude": tmp_path},
        )
        monkeypatch.setattr(
            "lerim.sessions.catalog.adapter_registry.get_adapter",
            lambda name: FakeAdapter(),
        )

        details = index_new_sessions(agents=["claude"], return_details=True)

        assert len(details) == 1
        assert details[0].run_id == "known-missing"
        assert details[0].changed is True
        assert fetch_session_doc("known-missing")["content_hash"] == "new-hash"

    def test_update_session_extract_fields(self, sessions_db):
        _seed("upd-1")
        assert (
            update_session_extract_fields(
                "upd-1",
                summary_text="new summary",
                tags='["bug","fix"]',
                outcome="partially_achieved",
            )
            is True
        )
        doc = fetch_session_doc("upd-1")
        assert doc["summary_text"] == "new summary"
        assert doc["tags"] == '["bug","fix"]'
        assert doc["outcome"] == "partially_achieved"

    def test_update_session_extract_fields_partial(self, sessions_db):
        _seed("upd-2")
        assert update_session_extract_fields("upd-2", tags='["only-tags"]') is True
        doc = fetch_session_doc("upd-2")
        assert doc["tags"] == '["only-tags"]'
        assert doc["summary_text"] is None

    def test_update_session_extract_fields_empty_run_id(self, sessions_db):
        assert update_session_extract_fields("", tags="x") is False

    def test_update_session_extract_fields_no_updates(self, sessions_db):
        assert update_session_extract_fields("any-id") is False

    def test_update_session_extract_fields_nonexistent(self, sessions_db):
        assert update_session_extract_fields("nope", summary_text="x") is False


class TestSessionWindow:
    def test_basic_window(self, sessions_db):
        for i in range(5):
            _seed(f"win-{i}", start_time=f"2026-04-0{i + 1}T10:00:00+00:00")
        rows, total = list_sessions_window(limit=3, offset=0)
        assert len(rows) == 3
        assert total == 5

    def test_window_agent_filter(self, sessions_db):
        _seed("wa-1", agent="claude")
        _seed("wa-2", agent="codex")
        rows, total = list_sessions_window(agent_types=["claude"])
        assert len(rows) == 1
        assert rows[0]["agent_type"] == "claude"
        assert total == 1

    def test_window_agent_filter_multiple(self, sessions_db):
        _seed("wb-1", agent="claude")
        _seed("wb-2", agent="codex")
        _seed("wb-3", agent="opencode")
        rows, total = list_sessions_window(agent_types=["claude", "codex"])
        assert total == 2

    def test_window_since_filter(self, sessions_db):
        _seed("ws-1", start_time="2026-01-01T00:00:00+00:00")
        _seed("ws-2", start_time="2026-06-01T00:00:00+00:00")
        rows, total = list_sessions_window(
            since=datetime(2026, 4, 1, tzinfo=timezone.utc)
        )
        run_ids = {r["run_id"] for r in rows}
        assert "ws-2" in run_ids
        assert "ws-1" not in run_ids

    def test_window_until_filter(self, sessions_db):
        _seed("wu-1", start_time="2026-01-01T00:00:00+00:00")
        _seed("wu-2", start_time="2026-06-01T00:00:00+00:00")
        rows, total = list_sessions_window(
            until=datetime(2026, 3, 1, tzinfo=timezone.utc)
        )
        run_ids = {r["run_id"] for r in rows}
        assert "wu-1" in run_ids
        assert "wu-2" not in run_ids

    def test_window_since_and_until(self, sessions_db):
        _seed("wboth-1", start_time="2026-01-01T00:00:00+00:00")
        _seed("wboth-2", start_time="2026-04-01T00:00:00+00:00")
        _seed("wboth-3", start_time="2026-08-01T00:00:00+00:00")
        rows, total = list_sessions_window(
            since=datetime(2026, 2, 1, tzinfo=timezone.utc),
            until=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )
        run_ids = {r["run_id"] for r in rows}
        assert run_ids == {"wboth-2"}

    def test_window_pagination_offset(self, sessions_db):
        for i in range(6):
            _seed(f"wp-{i:02d}", start_time=f"2026-04-{i + 1:02d}T10:00:00+00:00")
        p1, _ = list_sessions_window(limit=2, offset=0)
        p2, _ = list_sessions_window(limit=2, offset=2)
        p3, _ = list_sessions_window(limit=2, offset=4)
        ids1 = {r["run_id"] for r in p1}
        ids2 = {r["run_id"] for r in p2}
        ids3 = {r["run_id"] for r in p3}
        assert ids1.isdisjoint(ids2)
        assert ids2.isdisjoint(ids3)
        assert len(p3) == 2

    def test_window_ties_have_stable_id_order(self, sessions_db):
        _seed("tie-a", start_time="2026-04-01T10:00:00+00:00")
        _seed("tie-b", start_time="2026-04-01T10:00:00+00:00")
        rows, total = list_sessions_window(limit=2)
        assert total == 2
        assert [row["run_id"] for row in rows] == ["tie-b", "tie-a"]

    def test_window_empty_db(self, sessions_db):
        rows, total = list_sessions_window()
        assert rows == []
        assert total == 0

    def test_window_ordering_desc(self, sessions_db):
        _seed("wo-1", start_time="2026-01-01T00:00:00+00:00")
        _seed("wo-2", start_time="2026-06-01T00:00:00+00:00")
        rows, _ = list_sessions_window()
        assert rows[0]["run_id"] == "wo-2"
        assert rows[1]["run_id"] == "wo-1"


class TestJobQueueLifecycle:
    def test_enqueue_creates_job(self, sessions_db):
        _seed_and_enqueue("jq-1")
        conn = _db(sessions_db)
        row = conn.execute(
            "SELECT * FROM session_jobs WHERE run_id = 'jq-1'"
        ).fetchone()
        assert row is not None
        assert row["status"] == "pending"
        assert row["job_type"] == "extract"
        conn.close()

    def test_enqueue_empty_run_id_returns_false(self, sessions_db):
        assert enqueue_session_job("", repo_path="/tmp") is False

    def test_enqueue_empty_repo_path_returns_false(self, sessions_db):
        _seed("jq-norepo")
        assert enqueue_session_job("jq-norepo") is False

    def test_enqueue_without_force_when_pending(self, sessions_db):
        _seed_and_enqueue("jq-dup")
        assert enqueue_session_job("jq-dup", repo_path="/tmp") is False

    def test_enqueue_with_force_resets_pending(self, sessions_db):
        _seed_and_enqueue("jq-force")
        assert enqueue_session_job("jq-force", force=True, repo_path="/tmp") is True

    def test_complete_session_job(self, sessions_db):
        _seed_and_enqueue("jq-comp")
        claim_session_jobs(limit=1, run_ids=["jq-comp"])
        assert complete_session_job("jq-comp") is True
        conn = _db(sessions_db)
        row = conn.execute(
            "SELECT status FROM session_jobs WHERE run_id = 'jq-comp'"
        ).fetchone()
        assert row["status"] == "done"
        conn.close()

    def test_complete_empty_run_id(self, sessions_db):
        assert complete_session_job("") is False

    def test_complete_nonexistent(self, sessions_db):
        assert complete_session_job("nope") is False

    def test_fail_session_job_under_max_attempts(self, sessions_db):
        _seed_and_enqueue("jq-fail")
        claim_session_jobs(limit=1, run_ids=["jq-fail"])
        assert fail_session_job("jq-fail", error="test error") is True
        conn = _db(sessions_db)
        row = conn.execute(
            "SELECT status, error FROM session_jobs WHERE run_id = 'jq-fail'"
        ).fetchone()
        assert row["status"] == "failed"
        assert row["error"] == "test error"
        conn.close()

    def test_fail_session_job_exhausted_to_dead_letter(self, sessions_db):
        _seed_and_enqueue("jq-dl")
        with _connect() as conn:
            conn.execute("UPDATE session_jobs SET attempts = 3 WHERE run_id = 'jq-dl'")
            conn.commit()
        fail_session_job("jq-dl", error="exhausted")
        conn = _db(sessions_db)
        row = conn.execute(
            "SELECT status, completed_at FROM session_jobs WHERE run_id = 'jq-dl'"
        ).fetchone()
        assert row["status"] == "dead_letter"
        assert row["completed_at"] is not None
        conn.close()

    def test_fail_session_job_empty_run_id(self, sessions_db):
        assert fail_session_job("", error="x") is False

    def test_fail_session_job_nonexistent(self, sessions_db):
        assert fail_session_job("nope", error="x") is False

    def test_fail_with_require_status(self, sessions_db):
        _seed_and_enqueue("jq-req")
        claim_session_jobs(limit=1, run_ids=["jq-req"])
        assert fail_session_job("jq-req", error="x", require_status="pending") is False
        assert fail_session_job("jq-req", error="x", require_status="running") is True

    def test_enqueue_max_attempts(self, sessions_db):
        _seed("jq-ma")
        enqueue_session_job("jq-ma", repo_path="/tmp", max_attempts=5)
        conn = _db(sessions_db)
        row = conn.execute(
            "SELECT max_attempts FROM session_jobs WHERE run_id = 'jq-ma'"
        ).fetchone()
        assert row["max_attempts"] == 5
        conn.close()

    def test_enqueue_sets_trigger(self, sessions_db):
        _seed("jq-trig")
        enqueue_session_job("jq-trig", repo_path="/tmp", trigger="manual")
        conn = _db(sessions_db)
        row = conn.execute(
            "SELECT trigger FROM session_jobs WHERE run_id = 'jq-trig'"
        ).fetchone()
        assert row["trigger"] == "manual"
        conn.close()

    def test_enqueue_done_without_force_rejected(self, sessions_db):
        _seed_and_enqueue("jq-done")
        claim_session_jobs(limit=1, run_ids=["jq-done"])
        complete_session_job("jq-done")
        assert enqueue_session_job("jq-done", repo_path="/tmp") is False

    def test_enqueue_done_with_force_accepted(self, sessions_db):
        _seed_and_enqueue("jq-requeue")
        claim_session_jobs(limit=1, run_ids=["jq-requeue"])
        complete_session_job("jq-requeue")
        assert enqueue_session_job("jq-requeue", force=True, repo_path="/tmp") is True


class TestJobQueueClaim:
    def test_claim_marks_running(self, sessions_db):
        _seed_and_enqueue("cl-1")
        jobs = claim_session_jobs(limit=10)
        assert len(jobs) == 1
        assert jobs[0]["status"] == "running"
        assert jobs[0]["attempts"] == 1

    def test_claim_increments_attempts(self, sessions_db):
        _seed_and_enqueue("cl-att")
        _set_job_status("cl-att", "failed")
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with _connect() as conn:
            conn.execute(
                "UPDATE session_jobs SET available_at = ?, attempts = 2 WHERE run_id = ?",
                (past, "cl-att"),
            )
            conn.commit()
        jobs = claim_session_jobs(limit=10)
        matched = [j for j in jobs if j["run_id"] == "cl-att"]
        assert len(matched) == 1
        assert matched[0]["attempts"] == 3

    def test_claim_with_run_ids_filter(self, sessions_db):
        _seed_and_enqueue("cl-rid-1")
        _seed_and_enqueue("cl-rid-2")
        jobs = claim_session_jobs(limit=10, run_ids=["cl-rid-1"])
        claimed_ids = {j["run_id"] for j in jobs}
        assert "cl-rid-1" in claimed_ids
        assert "cl-rid-2" not in claimed_ids

    def test_claim_run_ids_filter_applies_before_project_ranking(self, sessions_db):
        """A targeted older job remains claimable behind a newer pending project job."""
        _seed_and_enqueue(
            "cl-target-old",
            repo_path="/tmp/target-rank-proj",
            start_time="2026-04-01T08:00:00+00:00",
        )
        _seed_and_enqueue(
            "cl-target-new",
            repo_path="/tmp/target-rank-proj",
            start_time="2026-04-01T12:00:00+00:00",
        )

        jobs = claim_session_jobs(limit=10, run_ids=["cl-target-old"])

        assert [job["run_id"] for job in jobs] == ["cl-target-old"]

    def test_claim_limit_respected(self, sessions_db):
        for i in range(10):
            _seed_and_enqueue(
                f"cl-lim-{i}",
                repo_path=f"/tmp/proj-lim-{i}",
                start_time=f"2026-04-01T{i + 1:02d}:00:00+00:00",
            )
        jobs = claim_session_jobs(limit=3)
        assert len(jobs) == 3

    def test_claim_default_backlog_order_is_newest_per_project(self, sessions_db):
        _seed_and_enqueue(
            "cl-newest-old",
            repo_path="/tmp/newest-proj",
            start_time="2026-04-01T08:00:00+00:00",
        )
        _seed_and_enqueue(
            "cl-newest-mid",
            repo_path="/tmp/newest-proj",
            start_time="2026-04-01T10:00:00+00:00",
        )
        _seed_and_enqueue(
            "cl-newest-new",
            repo_path="/tmp/newest-proj",
            start_time="2026-04-01T12:00:00+00:00",
        )

        jobs = claim_session_jobs(limit=10)

        assert len(jobs) == 1
        assert jobs[0]["run_id"] == "cl-newest-new"

    def test_claim_chronological_replay_order_is_explicit(self, sessions_db):
        _seed_and_enqueue(
            "cl-replay-old",
            repo_path="/tmp/replay-proj",
            start_time="2026-04-01T08:00:00+00:00",
        )
        _seed_and_enqueue(
            "cl-replay-new",
            repo_path="/tmp/replay-proj",
            start_time="2026-04-01T12:00:00+00:00",
        )

        jobs = claim_session_jobs(limit=10, claim_order="oldest")

        assert len(jobs) == 1
        assert jobs[0]["run_id"] == "cl-replay-old"

    def test_claim_future_job_does_not_block_available_project_job(self, sessions_db):
        """A later unavailable job must not hide an available project job."""
        _seed_and_enqueue(
            "cl-available",
            repo_path="/tmp/available-proj",
            start_time="2026-04-01T08:00:00+00:00",
        )
        _seed_and_enqueue(
            "cl-future",
            repo_path="/tmp/available-proj",
            start_time="2026-04-01T12:00:00+00:00",
        )
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with _connect() as conn:
            conn.execute(
                "UPDATE session_jobs SET available_at = ? WHERE run_id = ?",
                (future, "cl-future"),
            )
            conn.commit()

        jobs = claim_session_jobs(limit=10)

        assert len(jobs) == 1
        assert jobs[0]["run_id"] == "cl-available"

    def test_claim_run_id_filter_not_blocked_by_unavailable_neighbor(self, sessions_db):
        """Targeted claims find available jobs behind future project rows."""
        _seed_and_enqueue(
            "cl-filter-available",
            repo_path="/tmp/filter-available-proj",
            start_time="2026-04-01T08:00:00+00:00",
        )
        _seed_and_enqueue(
            "cl-filter-future",
            repo_path="/tmp/filter-available-proj",
            start_time="2026-04-01T12:00:00+00:00",
        )
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        with _connect() as conn:
            conn.execute(
                "UPDATE session_jobs SET available_at = ? WHERE run_id = ?",
                (future, "cl-filter-future"),
            )
            conn.commit()

        jobs = claim_session_jobs(limit=10, run_ids=["cl-filter-available"])

        assert len(jobs) == 1
        assert jobs[0]["run_id"] == "cl-filter-available"

    def test_claim_empty_db(self, sessions_db):
        assert claim_session_jobs(limit=10) == []

    def test_claim_skips_done_jobs(self, sessions_db):
        _seed_and_enqueue("cl-done")
        claim_session_jobs(limit=1, run_ids=["cl-done"])
        complete_session_job("cl-done")
        _seed_and_enqueue("cl-skip-other", repo_path="/tmp/other-proj")
        jobs = claim_session_jobs(limit=10)
        run_ids = {j["run_id"] for j in jobs}
        assert "cl-done" not in run_ids

    def test_claim_per_project_returns_one_job_per_project(self, sessions_db):
        _seed_and_enqueue(
            "cl-po-a-old",
            repo_path="/tmp/pp-proj",
            start_time="2026-04-01T11:00:00+00:00",
        )
        _seed_and_enqueue(
            "cl-po-a-new",
            repo_path="/tmp/pp-proj",
            start_time="2026-04-01T12:00:00+00:00",
        )
        _seed_and_enqueue(
            "cl-po-b-old",
            repo_path="/tmp/pp-other",
            start_time="2026-04-01T07:00:00+00:00",
        )
        _seed_and_enqueue(
            "cl-po-b-new",
            repo_path="/tmp/pp-other",
            start_time="2026-04-01T09:00:00+00:00",
        )
        jobs = claim_session_jobs(limit=2)
        claimed_ids = {job["run_id"] for job in jobs}
        assert claimed_ids == {"cl-po-a-new", "cl-po-b-new"}

    def test_invalid_claim_order_rejected(self, sessions_db):
        _seed_and_enqueue("cl-bad-order")
        with pytest.raises(ValueError, match="claim_order"):
            claim_session_jobs(limit=10, claim_order="sideways")


class TestJobQueueDeadLetter:
    def test_retry_dead_letter(self, sessions_db):
        _seed_and_enqueue("dl-1")
        _set_job_status("dl-1", "dead_letter")
        assert retry_session_job("dl-1") is True
        conn = _db(sessions_db)
        row = conn.execute(
            "SELECT status, attempts FROM session_jobs WHERE run_id = 'dl-1'"
        ).fetchone()
        assert row["status"] == "pending"
        assert row["attempts"] == 0
        conn.close()

    def test_retry_empty_run_id(self, sessions_db):
        assert retry_session_job("") is False

    def test_retry_non_dead_letter(self, sessions_db):
        _seed_and_enqueue("dl-nope")
        assert retry_session_job("dl-nope") is False

    def test_skip_dead_letter(self, sessions_db):
        _seed_and_enqueue("dl-skip")
        _set_job_status("dl-skip", "dead_letter")
        assert skip_session_job("dl-skip") is True
        conn = _db(sessions_db)
        row = conn.execute(
            "SELECT status FROM session_jobs WHERE run_id = 'dl-skip'"
        ).fetchone()
        assert row["status"] == "done"
        conn.close()

    def test_skip_empty_run_id(self, sessions_db):
        assert skip_session_job("") is False

    def test_skip_non_dead_letter(self, sessions_db):
        _seed_and_enqueue("dl-skipnope")
        assert skip_session_job("dl-skipnope") is False

    def test_retry_project_jobs(self, sessions_db):
        _seed_and_enqueue("dl-rp1", repo_path="/tmp/rp-proj")
        _seed_and_enqueue("dl-rp2", repo_path="/tmp/rp-proj")
        _set_job_status("dl-rp1", "dead_letter")
        _set_job_status("dl-rp2", "dead_letter")
        assert retry_project_jobs("/tmp/rp-proj") == 2
        conn = _db(sessions_db)
        rows = conn.execute(
            "SELECT status FROM session_jobs WHERE repo_path = '/tmp/rp-proj'"
        ).fetchall()
        assert all(r["status"] == "pending" for r in rows)
        conn.close()

    def test_retry_project_jobs_empty_path(self, sessions_db):
        assert retry_project_jobs("") == 0

    def test_skip_project_jobs(self, sessions_db):
        _seed_and_enqueue("dl-sp1", repo_path="/tmp/sp-proj")
        _seed_and_enqueue("dl-sp2", repo_path="/tmp/sp-proj")
        _set_job_status("dl-sp1", "dead_letter")
        _set_job_status("dl-sp2", "dead_letter")
        assert skip_project_jobs("/tmp/sp-proj") == 2
        conn = _db(sessions_db)
        rows = conn.execute(
            "SELECT status FROM session_jobs WHERE repo_path = '/tmp/sp-proj'"
        ).fetchall()
        assert all(r["status"] == "done" for r in rows)
        conn.close()

    def test_skip_project_jobs_empty_path(self, sessions_db):
        assert skip_project_jobs("") == 0

    def test_retry_all_dead_letter_jobs_not_limited_to_default_page(self, sessions_db):
        """Retry-all transitions every dead-letter row, including rows past 50."""
        for idx in range(55):
            run_id = f"dl-retry-all-{idx:02d}"
            _seed_and_enqueue(run_id, repo_path=f"/tmp/retry-all-{idx}")
            _set_job_status(run_id, "dead_letter")

        assert retry_all_dead_letter_jobs() == 55
        with _connect() as conn:
            row = conn.execute(
                "SELECT COUNT(1) AS total FROM session_jobs WHERE status = ?",
                ("pending",),
            ).fetchone()

        assert row["total"] == 55

    def test_skip_all_dead_letter_jobs_not_limited_to_default_page(self, sessions_db):
        """Skip-all transitions every dead-letter row, including rows past 50."""
        for idx in range(55):
            run_id = f"dl-skip-all-{idx:02d}"
            _seed_and_enqueue(run_id, repo_path=f"/tmp/skip-all-{idx}")
            _set_job_status(run_id, "dead_letter")

        assert skip_all_dead_letter_jobs() == 55
        with _connect() as conn:
            row = conn.execute(
                "SELECT COUNT(1) AS total FROM session_jobs WHERE status = ?",
                ("done",),
            ).fetchone()

        assert row["total"] == 55

    def test_dead_letter_blocks_project(self, sessions_db):
        _seed_and_enqueue(
            "dl-block", repo_path="/tmp/bl-proj", start_time="2026-04-01T08:00:00+00:00"
        )
        _seed_and_enqueue(
            "dl-blocked",
            repo_path="/tmp/bl-proj",
            start_time="2026-04-01T10:00:00+00:00",
        )
        _set_job_status("dl-block", "dead_letter")
        jobs = claim_session_jobs(limit=10)
        claimed_ids = {j["run_id"] for j in jobs}
        assert "dl-blocked" not in claimed_ids

    def test_targeted_claim_bypasses_unrelated_dead_letter(self, sessions_db):
        _seed_and_enqueue(
            "dl-target",
            repo_path="/tmp/bl-target-proj",
            start_time="2026-04-01T08:00:00+00:00",
        )
        _seed_and_enqueue(
            "dl-newer-blocker",
            repo_path="/tmp/bl-target-proj",
            start_time="2026-04-01T12:00:00+00:00",
        )
        _set_job_status("dl-newer-blocker", "dead_letter")

        jobs = claim_session_jobs(limit=10, run_ids=["dl-target"])

        assert [job["run_id"] for job in jobs] == ["dl-target"]

    def test_retry_unblocks_project(self, sessions_db):
        _seed_and_enqueue(
            "dl-ub", repo_path="/tmp/ub-proj", start_time="2026-04-01T08:00:00+00:00"
        )
        _seed_and_enqueue(
            "dl-ub2", repo_path="/tmp/ub-proj", start_time="2026-04-01T10:00:00+00:00"
        )
        _set_job_status("dl-ub", "dead_letter")
        retry_session_job("dl-ub")
        jobs = claim_session_jobs(limit=10)
        claimed_ids = {j["run_id"] for j in jobs}
        assert "dl-ub2" in claimed_ids

    def test_skip_unblocks_project(self, sessions_db):
        _seed_and_enqueue(
            "dl-sub", repo_path="/tmp/sub-proj", start_time="2026-04-01T08:00:00+00:00"
        )
        _seed_and_enqueue(
            "dl-sub2", repo_path="/tmp/sub-proj", start_time="2026-04-01T10:00:00+00:00"
        )
        _set_job_status("dl-sub", "dead_letter")
        skip_session_job("dl-sub")
        jobs = claim_session_jobs(limit=10)
        claimed_ids = {j["run_id"] for j in jobs}
        assert "dl-sub2" in claimed_ids


class TestQueueHealth:
    def test_healthy_queue(self, sessions_db):
        _seed_and_enqueue("qh-ok")
        health = queue_health_snapshot()
        assert health["degraded"] is False
        assert health["stale_running_count"] == 0
        assert health["dead_letter_count"] == 0
        assert health["oldest_running_age_seconds"] is None
        assert health["oldest_dead_letter_age_seconds"] is None
        assert health["advice"] == ""

    def test_degraded_with_dead_letter(self, sessions_db):
        _seed_and_enqueue("qh-dl")
        _set_job_status("qh-dl", "dead_letter")
        health = queue_health_snapshot()
        assert health["degraded"] is True
        assert health["dead_letter_count"] >= 1
        assert "lerim queue --failed" in health["advice"]

    def test_degraded_with_stale_running(self, sessions_db):
        _seed_and_enqueue("qh-stale")
        claim_session_jobs(limit=1, run_ids=["qh-stale"])
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with _connect() as conn:
            conn.execute(
                """
                UPDATE session_jobs
                SET claimed_at = ?, heartbeat_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (old, old, old, "qh-stale"),
            )
            conn.commit()
        health = queue_health_snapshot(lease_seconds=60)
        assert health["degraded"] is True
        assert health["stale_running_count"] >= 1
        assert isinstance(health["oldest_running_age_seconds"], int)
        assert "lerim ingest" in health["advice"]

    def test_fresh_heartbeat_keeps_queue_healthy(self, sessions_db):
        _seed_and_enqueue("qh-heartbeat")
        claim_session_jobs(limit=1, run_ids=["qh-heartbeat"])
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        fresh = datetime.now(timezone.utc).isoformat()
        with _connect() as conn:
            conn.execute(
                """
                UPDATE session_jobs
                SET claimed_at = ?, heartbeat_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (old, fresh, fresh, "qh-heartbeat"),
            )
            conn.commit()

        health = queue_health_snapshot(lease_seconds=60)

        assert health["degraded"] is False
        assert health["stale_running_count"] == 0
        assert isinstance(health["oldest_running_age_seconds"], int)


class TestPrefixResolution:
    def test_resolve_unique_prefix(self, sessions_db):
        _seed_and_enqueue("abcdefghij")
        assert resolve_run_id_prefix("abcdef") == "abcdefghij"

    def test_resolve_ambiguous_prefix(self, sessions_db):
        _seed_and_enqueue("abcdef-first", repo_path="/tmp/p1")
        _seed_and_enqueue("abcdef-second", repo_path="/tmp/p2")
        assert resolve_run_id_prefix("abcdef") is None

    def test_resolve_too_short(self, sessions_db):
        _seed_and_enqueue("xyz789full")
        assert resolve_run_id_prefix("xyz78") is None

    def test_resolve_empty(self, sessions_db):
        assert resolve_run_id_prefix("") is None

    def test_resolve_no_match(self, sessions_db):
        _seed_and_enqueue("nomatch1234")
        assert resolve_run_id_prefix("zzzzzz") is None


class TestServiceRuns:
    def test_record_and_latest(self, sessions_db):
        rid = record_service_run(
            job_type="extract",
            status="completed",
            started_at="2026-04-01T10:00:00+00:00",
            completed_at="2026-04-01T10:01:00+00:00",
            trigger="manual",
            details={"count": 5},
        )
        assert rid > 0
        latest = latest_service_run("extract")
        assert latest is not None
        assert latest["status"] == "completed"
        assert latest["trigger"] == "manual"
        assert latest["details"] == {"count": 5}

    def test_latest_empty_job_type(self, sessions_db):
        assert latest_service_run("") is None

    def test_latest_nonexistent_type(self, sessions_db):
        assert latest_service_run("nonexistent") is None

    def test_latest_returns_most_recent(self, sessions_db):
        record_service_run(
            job_type="extract",
            status="completed",
            started_at="2026-04-01T10:00:00+00:00",
            completed_at=None,
            trigger="auto",
            details=None,
        )
        record_service_run(
            job_type="extract",
            status="completed",
            started_at="2026-04-02T10:00:00+00:00",
            completed_at=None,
            trigger="manual",
            details=None,
        )
        latest = latest_service_run("extract")
        assert latest["trigger"] == "manual"

    def test_list_service_runs(self, sessions_db):
        for i in range(3):
            record_service_run(
                job_type="extract",
                status="completed",
                started_at=f"2026-04-0{i + 1}T10:00:00+00:00",
                completed_at=None,
                trigger=None,
                details=None,
            )
        runs = list_service_runs(limit=10)
        assert len(runs) == 3

    def test_list_service_runs_limit(self, sessions_db):
        for i in range(5):
            record_service_run(
                job_type="extract",
                status="completed",
                started_at=f"2026-04-0{i + 1}T10:00:00+00:00",
                completed_at=None,
                trigger=None,
                details=None,
            )
        runs = list_service_runs(limit=2)
        assert len(runs) == 2

    def test_list_service_runs_empty(self, sessions_db):
        assert list_service_runs() == []

    def test_record_with_null_details(self, sessions_db):
        rid = record_service_run(
            job_type="extract",
            status="running",
            started_at="2026-04-01T10:00:00+00:00",
            completed_at=None,
            trigger=None,
            details=None,
        )
        assert rid > 0
        run = latest_service_run("extract")
        assert run["details"] == {}

    def test_invalid_details_json_handled(self, sessions_db):
        with _connect() as conn:
            conn.execute(
                "INSERT INTO service_runs (job_type, status, started_at, details_json) "
                "VALUES ('extract', 'completed', '2026-04-01T10:00:00', 'not-json')"
            )
            conn.commit()
        run = latest_service_run("extract")
        assert run["details"] == {}


class TestQueueJobs:
    def test_count_by_status(self, sessions_db):
        _seed_and_enqueue("cj-pend", repo_path="/tmp/cj1")
        _seed_and_enqueue("cj-done", repo_path="/tmp/cj2")
        claim_session_jobs(limit=1, run_ids=["cj-done"])
        complete_session_job("cj-done")
        counts = count_session_jobs_by_status()
        assert counts["pending"] >= 1
        assert counts["done"] >= 1
        assert counts["running"] == 0
        assert counts["failed"] == 0
        assert counts["dead_letter"] == 0

    def test_count_by_status_zero_filled(self, sessions_db):
        counts = count_session_jobs_by_status()
        for status in ("pending", "running", "done", "failed", "dead_letter"):
            assert status in counts
            assert counts[status] == 0

    def test_list_queue_jobs_default_excludes_done(self, sessions_db):
        _seed_and_enqueue("lq-pend", repo_path="/tmp/lq1")
        _seed_and_enqueue("lq-done", repo_path="/tmp/lq2")
        claim_session_jobs(limit=1, run_ids=["lq-done"])
        complete_session_job("lq-done")
        rows = list_queue_jobs()
        run_ids = {r["run_id"] for r in rows}
        assert "lq-pend" in run_ids
        assert "lq-done" not in run_ids

    def test_list_queue_jobs_status_filter(self, sessions_db):
        _seed_and_enqueue("lq-sf", repo_path="/tmp/lq-sf")
        _set_job_status("lq-sf", "failed")
        rows = list_queue_jobs(status_filter="failed")
        assert len(rows) >= 1
        assert rows[0]["status"] == "failed"

    def test_list_queue_jobs_failed_only(self, sessions_db):
        _seed_and_enqueue("lq-fo-pend", repo_path="/tmp/lq-fo1")
        _seed_and_enqueue("lq-fo-fail", repo_path="/tmp/lq-fo2")
        _seed_and_enqueue("lq-fo-dl", repo_path="/tmp/lq-fo3")
        _set_job_status("lq-fo-fail", "failed")
        _set_job_status("lq-fo-dl", "dead_letter")
        rows = list_queue_jobs(failed_only=True)
        run_ids = {r["run_id"] for r in rows}
        assert "lq-fo-fail" in run_ids
        assert "lq-fo-dl" in run_ids
        assert "lq-fo-pend" not in run_ids

    def test_list_queue_jobs_project_filter_like(self, sessions_db):
        _seed_and_enqueue("lq-pf-1", repo_path="/tmp/my-special-proj")
        _seed_and_enqueue("lq-pf-2", repo_path="/tmp/other-proj")
        rows = list_queue_jobs(project_filter="special")
        run_ids = {r["run_id"] for r in rows}
        assert "lq-pf-1" in run_ids
        assert "lq-pf-2" not in run_ids

    def test_list_queue_jobs_project_exact(self, sessions_db):
        _seed_and_enqueue("lq-pe-1", repo_path="/tmp/exact")
        _seed_and_enqueue("lq-pe-2", repo_path="/tmp/exact-sub")
        rows = list_queue_jobs(project_filter="/tmp/exact", project_exact=True)
        run_ids = {r["run_id"] for r in rows}
        assert "lq-pe-1" in run_ids
        assert "lq-pe-2" not in run_ids

    def test_list_queue_jobs_limit(self, sessions_db):
        for i in range(10):
            _seed_and_enqueue(
                f"lq-lim-{i}",
                repo_path=f"/tmp/lqlim-{i}",
                start_time=f"2026-04-{i + 1:02d}T10:00:00+00:00",
            )
        rows = list_queue_jobs(limit=3)
        assert len(rows) == 3

    def test_count_unscoped_sessions_by_agent(self, sessions_db):
        _seed("un-1", agent="claude", repo_path="/tmp/unscoped")
        _seed("un-2", agent="codex", repo_path="/tmp/unscoped2")
        counts = count_unscoped_sessions_by_agent(
            projects={"myproj": "/tmp/somewhere-else"}
        )
        assert counts.get("claude") == 1
        assert counts.get("codex") == 1

    def test_count_unscoped_sessions_excludes_matched(self, sessions_db):
        _seed("un-3", agent="claude", repo_path="/tmp/my-project")
        counts = count_unscoped_sessions_by_agent(
            projects={"myproj": "/tmp/my-project"}
        )
        assert counts == {}

    def test_list_unscoped_sessions(self, sessions_db):
        _seed("unl-1", repo_path="/tmp/unscoped-dir")
        items = list_unscoped_sessions(projects={"proj": "/tmp/other"})
        assert len(items) >= 1
        assert items[0]["run_id"] == "unl-1"

    def test_list_unscoped_sessions_excludes_matched(self, sessions_db):
        _seed("unl-2", repo_path="/tmp/my-real-project")
        items = list_unscoped_sessions(projects={"proj": "/tmp/my-real-project"})
        assert len(items) == 0

    def test_list_unscoped_sessions_limit(self, sessions_db):
        for i in range(10):
            _seed(f"unl-lim-{i}", repo_path=f"/tmp/unscoped-{i}")
        items = list_unscoped_sessions(projects={"p": "/tmp/other"}, limit=3)
        assert len(items) == 3


class TestJobQueueAdvanced:
    def test_reap_stale_running_job_marks_failed(self, sessions_db):
        """Stale running job is recovered through fail path (status -> failed)."""
        _seed_and_enqueue(
            "stale-1", repo_path="/tmp/proj-stale", start_time="2026-03-01T10:00:00Z"
        )
        claimed = claim_session_jobs(limit=1, run_ids=["stale-1"])
        assert len(claimed) == 1
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with _connect() as conn:
            conn.execute(
                """
                UPDATE session_jobs
                SET claimed_at = ?, heartbeat_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (old, old, old, "stale-1"),
            )
            conn.commit()
        recovered = reap_stale_running_jobs(
            lease_seconds=60,
            retry_backoff_fn=lambda attempts: 1 if attempts >= 1 else 1,
        )
        assert recovered == 1
        with _connect() as conn:
            row = conn.execute(
                "SELECT status, error FROM session_jobs WHERE run_id = ?",
                ("stale-1",),
            ).fetchone()
        assert row["status"] == "failed"
        assert "stale running lease expired" in str(row["error"] or "")

    def test_reap_running_job_uses_heartbeat_when_present(self, sessions_db):
        """A fresh heartbeat keeps an old claimed job from being reaped."""
        _seed_and_enqueue(
            "alive-1", repo_path="/tmp/proj-alive", start_time="2026-03-01T10:00:00Z"
        )
        claimed = claim_session_jobs(limit=1, run_ids=["alive-1"])
        assert len(claimed) == 1
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        fresh = datetime.now(timezone.utc).isoformat()
        with _connect() as conn:
            conn.execute(
                """
                UPDATE session_jobs
                SET claimed_at = ?, heartbeat_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (old, fresh, fresh, "alive-1"),
            )
            conn.commit()
        assert reap_stale_running_jobs(lease_seconds=60) == 0
        with _connect() as conn:
            row = conn.execute(
                "SELECT status FROM session_jobs WHERE run_id = ?",
                ("alive-1",),
            ).fetchone()
        assert row["status"] == "running"

    def test_local_running_lease_masks_transient_heartbeat_write_failure(
        self, sessions_db
    ):
        """A live in-process job is not stale just because DB heartbeat writes fail."""
        _seed_and_enqueue(
            "alive-local",
            repo_path="/tmp/proj-alive-local",
            start_time="2026-03-01T10:00:00Z",
        )
        claimed = claim_session_jobs(limit=1, run_ids=["alive-local"])
        assert len(claimed) == 1
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with _connect() as conn:
            conn.execute(
                """
                UPDATE session_jobs
                SET claimed_at = ?, heartbeat_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (old, old, old, "alive-local"),
            )
            conn.commit()

        note_local_running_job("alive-local")
        try:
            health = queue_health_snapshot(lease_seconds=60)
            assert health["degraded"] is False
            assert health["stale_running_count"] == 0
            assert reap_stale_running_jobs(lease_seconds=60) == 0
        finally:
            clear_local_running_job("alive-local")

        with _connect() as conn:
            row = conn.execute(
                "SELECT status FROM session_jobs WHERE run_id = ?",
                ("alive-local",),
            ).fetchone()
        assert row["status"] == "running"

    def test_reap_stale_running_job_to_dead_letter_when_attempts_exhausted(
        self, sessions_db
    ):
        """Stale running job dead-letters when max_attempts already exhausted."""
        _seed_and_enqueue(
            "stale-dl", repo_path="/tmp/proj-stale", start_time="2026-03-01T10:00:00Z"
        )
        with _connect() as conn:
            conn.execute(
                "UPDATE session_jobs SET max_attempts = 1 WHERE run_id = ?",
                ("stale-dl",),
            )
            conn.commit()
        claimed = claim_session_jobs(limit=1, run_ids=["stale-dl"])
        assert len(claimed) == 1
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with _connect() as conn:
            conn.execute(
                """
                UPDATE session_jobs
                SET claimed_at = ?, heartbeat_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (old, old, old, "stale-dl"),
            )
            conn.commit()
        recovered = reap_stale_running_jobs(lease_seconds=60)
        assert recovered == 1
        with _connect() as conn:
            row = conn.execute(
                "SELECT status, completed_at FROM session_jobs WHERE run_id = ?",
                ("stale-dl",),
            ).fetchone()
        assert row["status"] == "dead_letter"
        assert row["completed_at"] is not None

    def test_three_projects_one_blocked_two_proceed(self, sessions_db):
        """3 projects, each with 3 jobs. Project A has dead_letter -- only B and C get jobs."""
        for suffix in ("j1", "j2", "j3"):
            _seed_and_enqueue(
                f"pa-{suffix}",
                "/tmp/proj-pa",
                start_time=f"2026-03-01T0{int(suffix[1]) + 6}:00:00Z",
            )
            _seed_and_enqueue(
                f"pb-{suffix}",
                "/tmp/proj-pb",
                start_time=f"2026-03-01T0{int(suffix[1]) + 6}:00:00Z",
            )
            _seed_and_enqueue(
                f"pc-{suffix}",
                "/tmp/proj-pc",
                start_time=f"2026-03-01T0{int(suffix[1]) + 6}:00:00Z",
            )
        _set_job_status("pa-j1", "dead_letter")
        jobs = claim_session_jobs(limit=10)
        claimed_ids = {j["run_id"] for j in jobs}
        assert not any(rid.startswith("pa-") for rid in claimed_ids)
        assert "pb-j3" in claimed_ids
        assert "pc-j3" in claimed_ids
        assert len(claimed_ids) == 2

    def test_mixed_statuses_across_projects(self, sessions_db):
        """Project A: pending+done. Project B: failed+pending. Project C: dead_letter+pending."""
        _seed_and_enqueue("ma-pend", "/tmp/proj-ma", start_time="2026-03-01T08:00:00Z")
        _seed_and_enqueue("ma-done", "/tmp/proj-ma", start_time="2026-03-01T09:00:00Z")
        claim_session_jobs(limit=1, run_ids=["ma-done"])
        complete_session_job("ma-done")

        _seed_and_enqueue("mb-fail", "/tmp/proj-mb", start_time="2026-03-01T08:00:00Z")
        _seed_and_enqueue("mb-pend", "/tmp/proj-mb", start_time="2026-03-01T10:00:00Z")
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        _set_job_status("mb-fail", "failed", available_at=past)

        _seed_and_enqueue("mc-dead", "/tmp/proj-mc", start_time="2026-03-01T08:00:00Z")
        _seed_and_enqueue("mc-pend", "/tmp/proj-mc", start_time="2026-03-01T10:00:00Z")
        _set_job_status("mc-dead", "dead_letter")

        jobs = claim_session_jobs(limit=10)
        claimed_ids = {j["run_id"] for j in jobs}
        assert "ma-pend" in claimed_ids
        assert "mb-pend" in claimed_ids
        assert "mb-fail" not in claimed_ids
        assert "mc-dead" not in claimed_ids
        assert "mc-pend" not in claimed_ids

    def test_full_lifecycle_claim_fail_dead_letter_retry_reclaim(self, sessions_db):
        """Enqueue -> claim -> fail 3x -> dead_letter -> blocks -> retry -> claim again."""
        _seed_and_enqueue("lc-job", "/tmp/proj-lc", start_time="2026-03-01T08:00:00Z")
        _seed_and_enqueue("lc-next", "/tmp/proj-lc", start_time="2026-03-01T10:00:00Z")

        for attempt in range(1, 4):
            jobs = claim_session_jobs(limit=10, claim_order="oldest")
            matched = [j for j in jobs if j["run_id"] == "lc-job"]
            assert len(matched) == 1, f"attempt {attempt}: job should be claimable"
            assert matched[0]["status"] == "running"

            fail_session_job(
                "lc-job", error=f"error-{attempt}", retry_backoff_seconds=0
            )

            with _connect() as conn:
                row = conn.execute(
                    "SELECT status, attempts FROM session_jobs WHERE run_id = ?",
                    ("lc-job",),
                ).fetchone()

            if attempt < 3:
                assert row["status"] == "failed", f"attempt {attempt}: should be failed"
                _make_available_now("lc-job")
            else:
                assert row["status"] == "dead_letter", (
                    "after 3 attempts: should be dead_letter"
                )

        jobs_blocked = claim_session_jobs(limit=10, claim_order="oldest")
        blocked_ids = {j["run_id"] for j in jobs_blocked}
        assert "lc-job" not in blocked_ids
        assert "lc-next" not in blocked_ids

        assert retry_session_job("lc-job") is True
        with _connect() as conn:
            row = conn.execute(
                "SELECT status, attempts FROM session_jobs WHERE run_id = ?",
                ("lc-job",),
            ).fetchone()
        assert row["status"] == "pending"
        assert row["attempts"] == 0

        jobs_after = claim_session_jobs(limit=10, claim_order="oldest")
        after_ids = {j["run_id"] for j in jobs_after}
        assert "lc-job" in after_ids

    def test_full_lifecycle_skip_then_next_job(self, sessions_db):
        """2 jobs in project. Oldest dead_letters -> skip it -> next becomes claimable."""
        _seed_and_enqueue("sk-old", "/tmp/proj-sk", start_time="2026-03-01T08:00:00Z")
        _seed_and_enqueue("sk-new", "/tmp/proj-sk", start_time="2026-03-01T10:00:00Z")

        for i in range(3):
            claim_session_jobs(limit=10, claim_order="oldest")
            fail_session_job("sk-old", error="boom", retry_backoff_seconds=0)
            if i < 2:
                _make_available_now("sk-old")

        with _connect() as conn:
            row = conn.execute(
                "SELECT status FROM session_jobs WHERE run_id = ?", ("sk-old",)
            ).fetchone()
        assert row["status"] == "dead_letter"

        jobs = claim_session_jobs(limit=10, claim_order="oldest")
        assert all(j["run_id"] not in ("sk-old", "sk-new") for j in jobs)

        assert skip_session_job("sk-old") is True

        jobs2 = claim_session_jobs(limit=10, claim_order="oldest")
        claimed_ids = {j["run_id"] for j in jobs2}
        assert "sk-new" in claimed_ids

    def test_claim_returns_no_rn_column(self, sessions_db):
        """CTE adds an `rn` column. Verify claimed job dicts do not expose it."""
        _seed_and_enqueue("rn-check", "/tmp/proj-rn", start_time="2026-03-01T10:00:00Z")

        jobs = claim_session_jobs(limit=10)
        assert len(jobs) >= 1
        job = next(j for j in jobs if j["run_id"] == "rn-check")

        assert "rn" not in job
        for key in ("run_id", "status", "repo_path", "attempts"):
            assert key in job
