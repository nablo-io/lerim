"""Integration tests for queue / daemon processing behavior."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread
from typing import Any

import pytest

from lerim.config.settings import reload_config
from lerim.server import api, daemon
from lerim.sessions import catalog
from tests.helpers import write_test_config
from tests.integration.queue.helpers import load_queue_expectation


def _iso_now() -> str:
    """Return a fresh UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class QueueCaseEnv:
    """Resolved paths and config for one queue/daemon integration case."""

    repo_root: Path
    config_path: Path
    sessions_db_path: Path
    context_db_path: Path


@pytest.fixture
def queue_case_env(tmp_path, monkeypatch) -> QueueCaseEnv:
    """Create one isolated config + repo for queue/daemon integration tests."""
    repo_root = tmp_path / "queue-project"
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / ".git").mkdir(exist_ok=True)

    config_path = write_test_config(
        tmp_path,
        **{"roles.agent": {"provider": "openrouter", "model": "integration-test"}},
        projects={repo_root.name: str(repo_root)},
        server={"ingest_window_days": 7, "ingest_max_sessions": 10},
    )
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    cfg = reload_config()
    catalog.init_sessions_db()

    yield QueueCaseEnv(
        repo_root=repo_root,
        config_path=config_path,
        sessions_db_path=cfg.sessions_db_path,
        context_db_path=cfg.context_db_path,
    )

    reload_config()


def _index_session(case_env: QueueCaseEnv, *, run_id: str, summary: str = "integration queue session") -> str:
    """Insert one indexed session row and return its session-path string."""
    session_path = case_env.repo_root / "sessions" / f"{run_id}.jsonl"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text('{"run_id": "%s"}\n' % run_id, encoding="utf-8")
    ok = catalog.index_session_for_fts(
        run_id=run_id,
        agent_type="codex",
        repo_path=str(case_env.repo_root),
        repo_name=case_env.repo_root.name,
        start_time=_iso_now(),
        content=summary,
        summary_text=summary,
        session_path=str(session_path),
    )
    assert ok is True
    return str(session_path)


def _job_row(case_env: QueueCaseEnv, run_id: str) -> dict[str, Any]:
    """Fetch one queue row directly from the sessions DB."""
    with sqlite3.connect(case_env.sessions_db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM session_jobs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    assert row is not None
    return dict(row)


def _service_run_rows(case_env: QueueCaseEnv, job_type: str) -> list[dict[str, Any]]:
    """Return service-run rows for one job type."""
    with sqlite3.connect(case_env.sessions_db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM service_runs WHERE job_type = ? ORDER BY id ASC",
            (job_type,),
        ).fetchall()
    return [dict(row) for row in rows]


def _set_job_fields(case_env: QueueCaseEnv, run_id: str, **fields: Any) -> None:
    """Update one session job row directly for integration setup."""
    assignments = ", ".join(f"{name} = ?" for name in fields)
    params = [*fields.values(), run_id]
    with sqlite3.connect(case_env.sessions_db_path) as conn:
        conn.execute(
            f"UPDATE session_jobs SET {assignments} WHERE run_id = ?",
            params,
        )
        conn.commit()


def _patch_fake_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ingest_behaviors: list[Any] | None = None,
    curate_behaviors: list[Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Replace daemon runtime with one scripted fake runtime."""
    calls: dict[str, list[dict[str, Any]]] = {"ingest": [], "curate": []}
    ingest_queue = list(ingest_behaviors or [])
    curate_queue = list(curate_behaviors or [])

    class FakeRuntime:
        def __init__(self, default_cwd: str | None = None, config: Any | None = None) -> None:
            self.default_cwd = default_cwd
            self.config = config

        def ingest(self, session_path: Path, **kwargs: Any) -> dict[str, Any]:
            calls["ingest"].append({"default_cwd": self.default_cwd, "session_path": str(session_path), **kwargs})
            behavior = ingest_queue.pop(0) if ingest_queue else {
                "records_created": 1,
                "records_updated": 0,
                "records_archived": 0,
                "cost_usd": 0.0,
            }
            if isinstance(behavior, Exception):
                raise behavior
            return dict(behavior)

        def curate(self, repo_root: Path | None = None, session_id: str | None = None) -> dict[str, Any]:
            calls["curate"].append(
                {
                    "default_cwd": self.default_cwd,
                    "repo_root": str(repo_root) if repo_root else None,
                    "session_id": session_id,
                }
            )
            behavior = curate_queue.pop(0) if curate_queue else {
                "records_created": 0,
                "records_updated": 0,
                "records_archived": 0,
                "cost_usd": 0.0,
            }
            if isinstance(behavior, Exception):
                raise behavior
            return dict(behavior)

    monkeypatch.setattr(daemon, "LerimRuntime", FakeRuntime)
    return calls


@pytest.mark.integration
def test_pending_session_completes(queue_case_env: QueueCaseEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    """One targeted run_id should enqueue, claim, and complete successfully."""
    expectation = load_queue_expectation("pending_session_completes")["expected"]
    run_id = "run-pending-completes"
    session_path = _index_session(queue_case_env, run_id=run_id, summary="pending session ready for extraction")
    calls = _patch_fake_runtime(monkeypatch)

    code, summary = daemon.run_ingest_once(
        run_id=run_id,
        agent_filter=None,
        no_extract=False,
        force=False,
        max_sessions=5,
        dry_run=False,
        ignore_lock=True,
        trigger="manual",
    )

    job = _job_row(queue_case_env, run_id)

    assert code == daemon.EXIT_OK
    assert summary.extracted_sessions == int(expectation["extracted_sessions"])
    assert summary.failed_sessions == 0
    assert summary.skipped_sessions == 0
    assert job["status"] == catalog.JOB_STATUS_DONE == expectation["final_status"]
    assert int(job["attempts"] or 0) == int(expectation["attempts"])
    assert len(calls["ingest"]) == 1
    ingest_call = calls["ingest"][0]
    assert ingest_call["default_cwd"] == str(queue_case_env.repo_root)
    assert ingest_call["session_path"] == session_path
    assert ingest_call["session_id"] == run_id
    assert ingest_call["agent_type"] == "codex"
    assert ingest_call["session_meta"]["cwd"] == str(queue_case_env.repo_root)
    assert ingest_call["session_meta"]["started_at"]


@pytest.mark.integration
def test_failed_job_retry_path_completes_on_second_run(
    queue_case_env: QueueCaseEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed available job should retry and complete on the next ingest run."""
    expectation = load_queue_expectation("failed_job_retry_path_completes_on_second_run")["expected"]
    run_id = "run-retry-after-failure"
    _index_session(queue_case_env, run_id=run_id, summary="session that should fail once then retry")
    queued = catalog.enqueue_session_job(
        run_id,
        agent_type="codex",
        session_path=str(queue_case_env.repo_root / "sessions" / f"{run_id}.jsonl"),
        start_time=_iso_now(),
        trigger="integration",
        force=False,
        repo_path=str(queue_case_env.repo_root),
    )
    assert queued is True

    monkeypatch.setattr(daemon, "index_new_sessions", lambda **kwargs: [])
    calls = _patch_fake_runtime(
        monkeypatch,
        ingest_behaviors=[
            RuntimeError("transient extraction failure"),
            {"records_created": 1, "records_updated": 0, "records_archived": 0, "cost_usd": 0.0},
        ],
    )

    first_code, first_summary = daemon.run_ingest_once(
        run_id=None,
        agent_filter=None,
        no_extract=False,
        force=False,
        max_sessions=5,
        dry_run=False,
        ignore_lock=True,
        trigger="manual",
    )
    first_job = _job_row(queue_case_env, run_id)
    assert first_code == daemon.EXIT_FATAL
    assert first_summary.failed_sessions == 1
    assert first_summary.extracted_sessions == 0
    assert first_job["status"] == catalog.JOB_STATUS_FAILED == expectation["first_status"]
    assert int(first_job["attempts"] or 0) == 1

    _set_job_fields(
        queue_case_env,
        run_id,
        available_at=(_iso_now()),
    )

    second_code, second_summary = daemon.run_ingest_once(
        run_id=None,
        agent_filter=None,
        no_extract=False,
        force=False,
        max_sessions=5,
        dry_run=False,
        ignore_lock=True,
        trigger="manual",
    )
    second_job = _job_row(queue_case_env, run_id)

    assert second_code == daemon.EXIT_OK
    assert second_summary.extracted_sessions == 1
    assert second_summary.failed_sessions == 0
    assert second_job["status"] == catalog.JOB_STATUS_DONE == expectation["second_status"]
    assert int(second_job["attempts"] or 0) == int(expectation["attempts"])
    assert len(calls["ingest"]) == 2


@pytest.mark.integration
def test_skip_leaves_other_jobs_untouched(
    queue_case_env: QueueCaseEnv,
) -> None:
    """Skipping one dead-letter job should not mutate other queue rows."""
    expectation = load_queue_expectation("skip_leaves_other_jobs_untouched")["expected"]
    dead_run = "run-dead-skip-target"
    other_dead_run = "run-dead-untouched"
    pending_run = "run-pending-untouched"

    for run_id in (dead_run, other_dead_run, pending_run):
        _index_session(queue_case_env, run_id=run_id, summary=f"queue row for {run_id}")
        queued = catalog.enqueue_session_job(
            run_id,
            agent_type="codex",
            session_path=str(queue_case_env.repo_root / "sessions" / f"{run_id}.jsonl"),
            start_time=_iso_now(),
            trigger="integration",
            force=False,
            repo_path=str(queue_case_env.repo_root),
        )
        assert queued is True

    _set_job_fields(
        queue_case_env,
        dead_run,
        status=catalog.JOB_STATUS_DEAD_LETTER,
        attempts=3,
        max_attempts=3,
        completed_at=_iso_now(),
        error="stuck",
    )
    _set_job_fields(
        queue_case_env,
        other_dead_run,
        status=catalog.JOB_STATUS_DEAD_LETTER,
        attempts=3,
        max_attempts=3,
        completed_at=_iso_now(),
        error="other blocker",
    )

    result = api.api_skip_job(dead_run)

    target = _job_row(queue_case_env, dead_run)
    other_dead = _job_row(queue_case_env, other_dead_run)
    pending = _job_row(queue_case_env, pending_run)

    assert result["skipped"] is True
    assert target["status"] == catalog.JOB_STATUS_DONE == expectation["skipped_status"]
    assert other_dead["status"] == catalog.JOB_STATUS_DEAD_LETTER == expectation["untouched_dead_status"]
    assert pending["status"] == catalog.JOB_STATUS_PENDING == expectation["untouched_pending_status"]
    assert result["queue"][catalog.JOB_STATUS_DEAD_LETTER] == 1
    assert result["queue"][catalog.JOB_STATUS_DONE] == 1
    assert result["queue"][catalog.JOB_STATUS_PENDING] == 1


@pytest.mark.integration
def test_degraded_queue_reports_cleanly_via_api_ingest(queue_case_env: QueueCaseEnv) -> None:
    """API ingest should surface real degraded queue state and human advice."""
    expectation = load_queue_expectation("degraded_queue_reports_cleanly_via_api_ingest")["expected"]
    run_id = "run-degraded-dead-letter"
    _index_session(queue_case_env, run_id=run_id, summary="dead letter should degrade queue")
    queued = catalog.enqueue_session_job(
        run_id,
        agent_type="codex",
        session_path=str(queue_case_env.repo_root / "sessions" / f"{run_id}.jsonl"),
        start_time=_iso_now(),
        trigger="integration",
        force=False,
        repo_path=str(queue_case_env.repo_root),
    )
    assert queued is True
    _set_job_fields(
        queue_case_env,
        run_id,
        status=catalog.JOB_STATUS_DEAD_LETTER,
        attempts=3,
        max_attempts=3,
        completed_at=_iso_now(),
        error="permanent failure",
    )

    result = api.api_ingest(dry_run=True)

    assert result["code"] == daemon.EXIT_OK
    assert result["queue_health"]["degraded"] is bool(expectation["degraded"])
    assert result["queue_health"]["dead_letter_count"] == int(expectation["dead_letter_count"])
    assert "warning" in result
    assert "Queue degraded." in result["warning"]
    assert "queue --failed" in result["warning"]


@pytest.mark.integration
def test_curate_once_dry_run_short_circuits(
    queue_case_env: QueueCaseEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dry-run should win over force and skip runtime work entirely."""
    expectation = load_queue_expectation("curate_once_dry_run_short_circuits_even_with_force")["expected"]
    calls = _patch_fake_runtime(monkeypatch)

    code, payload = daemon.run_curate_once(dry_run=True, trigger="manual")

    service_runs = _service_run_rows(queue_case_env, "curate")

    assert code == daemon.EXIT_OK
    assert payload == {"dry_run": bool(expectation["dry_run"])}
    assert calls["curate"] == []
    assert len(service_runs) == 1
    assert service_runs[0]["status"] == expectation["service_run_status"]
    assert '"dry_run": true' in str(service_runs[0]["details_json"]).lower()


@pytest.mark.integration
def test_api_job_status_for_long_running_ingest(
    queue_case_env: QueueCaseEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API status should surface running ingest work and later the completed result."""
    expectation = load_queue_expectation("api_job_status_for_long_running_ingest")["expected"]
    run_id = "run-api-status-ingest"
    _index_session(queue_case_env, run_id=run_id, summary="session that stays running briefly")
    queued = catalog.enqueue_session_job(
        run_id,
        agent_type="codex",
        session_path=str(queue_case_env.repo_root / "sessions" / f"{run_id}.jsonl"),
        start_time=_iso_now(),
        trigger="integration",
        force=False,
        repo_path=str(queue_case_env.repo_root),
    )
    assert queued is True

    started = Event()
    release = Event()

    class BlockingRuntime:
        def __init__(self, default_cwd: str | None = None, config: Any | None = None) -> None:
            self.default_cwd = default_cwd
            self.config = config

        def ingest(self, session_path: Path, **kwargs: Any) -> dict[str, Any]:
            started.set()
            release.wait(timeout=10)
            return {
                "records_created": 1,
                "records_updated": 0,
                "records_archived": 0,
                "cost_usd": 0.0,
            }

        def curate(self, repo_root: Path | None = None, session_id: str | None = None) -> dict[str, Any]:
            raise AssertionError("curate should not run in this case")

    monkeypatch.setattr(daemon, "LerimRuntime", BlockingRuntime)
    monkeypatch.setattr(api, "get_config", reload_config)

    result_box: dict[str, Any] = {}

    def _run_ingest() -> None:
        result_box["payload"] = api.api_ingest(
            run_id=run_id,
            max_sessions=5,
            ignore_lock=True,
        )

    thread = Thread(target=_run_ingest, daemon=True)
    thread.start()
    assert started.wait(timeout=10)

    running_status = api.api_status(scope="project", project=queue_case_env.repo_root.name)
    assert running_status["recent_activity"][0]["status"] == expectation["running_status"]
    assert running_status["recent_activity"][0]["op_type"] == expectation["op_type"]
    assert int(running_status["queue"]["running"] or 0) == expectation["queue_running"]

    release.set()
    thread.join(timeout=10)
    assert "payload" in result_box
    assert int(result_box["payload"]["code"]) == daemon.EXIT_OK

    completed_status = api.api_status(scope="project", project=queue_case_env.repo_root.name)
    latest_ingest = completed_status["latest_ingest"]
    assert latest_ingest is not None
    assert latest_ingest["status"] == expectation["completed_status"]
    assert int(completed_status["queue"]["done"] or 0) == expectation["queue_done"]
