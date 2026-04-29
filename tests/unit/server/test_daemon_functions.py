"""Unit tests for daemon.py pure logic: resolve_window_bounds, lock state,
OperationResult, SyncSummary, ServiceLock, log_activity, and helper functions.

Focuses on testable functions without requiring a real runtime or database.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lerim.server import daemon
from lerim.server.daemon import (
    EXIT_FATAL,
    EXIT_LOCK_BUSY,
    EXIT_OK,
    EXIT_PARTIAL,
    LockBusyError,
    OperationResult,
    ServiceLock,
    SyncSummary,
    _is_stale,
    _now_iso,
    _parse_iso,
    _pid_alive,
    _retry_backoff_seconds,
    active_lock_state,
    log_activity,
    read_json_file,
    resolve_window_bounds,
)
from tests.helpers import make_config


# ---------------------------------------------------------------------------
# _parse_iso
# ---------------------------------------------------------------------------


def test_parse_iso_valid() -> None:
    """_parse_iso parses a standard ISO timestamp."""
    result = _parse_iso("2026-03-15T10:30:00+00:00")
    assert result is not None
    assert result.year == 2026
    assert result.month == 3


def test_parse_iso_with_z_suffix() -> None:
    """_parse_iso handles Z suffix."""
    result = _parse_iso("2026-03-15T10:30:00Z")
    assert result is not None
    assert result.tzinfo is not None


def test_parse_iso_empty() -> None:
    """_parse_iso returns None for empty string."""
    assert _parse_iso("") is None
    assert _parse_iso(None) is None


def test_parse_iso_invalid() -> None:
    """_parse_iso returns None for garbage input."""
    assert _parse_iso("not-a-date") is None


# ---------------------------------------------------------------------------
# _retry_backoff_seconds
# ---------------------------------------------------------------------------


def test_retry_backoff_first_attempt() -> None:
    """First attempt backoff is 30 seconds."""
    assert _retry_backoff_seconds(1) == 30


def test_retry_backoff_exponential() -> None:
    """Backoff doubles with each attempt."""
    assert _retry_backoff_seconds(2) == 60
    assert _retry_backoff_seconds(3) == 120
    assert _retry_backoff_seconds(4) == 240


def test_retry_backoff_capped_at_3600() -> None:
    """Backoff is capped at 3600 seconds (1 hour)."""
    assert _retry_backoff_seconds(100) == 3600


def test_retry_backoff_zero_attempts() -> None:
    """Zero attempts is treated as 1 (minimum)."""
    assert _retry_backoff_seconds(0) == 30


def test_retry_backoff_negative_attempts() -> None:
    """Negative attempts treated as 1."""
    assert _retry_backoff_seconds(-5) == 30


# ---------------------------------------------------------------------------
# _now_iso
# ---------------------------------------------------------------------------


def test_now_iso_returns_utc_string() -> None:
    """_now_iso returns a parseable UTC ISO timestamp."""
    result = _now_iso()
    parsed = datetime.fromisoformat(result)
    assert parsed.tzinfo is not None
    # Should be very recent (within 2 seconds)
    diff = abs((datetime.now(timezone.utc) - parsed).total_seconds())
    assert diff < 2


# ---------------------------------------------------------------------------
# _pid_alive
# ---------------------------------------------------------------------------


def test_pid_alive_current_process() -> None:
    """Current process PID should be alive."""
    assert _pid_alive(os.getpid()) is True


def test_pid_alive_invalid_pid() -> None:
    """Invalid PIDs return False."""
    assert _pid_alive(None) is False
    assert _pid_alive(-1) is False
    assert _pid_alive(0) is False


def test_pid_alive_nonexistent_pid() -> None:
    """Very large PID (likely nonexistent) returns False."""
    assert _pid_alive(999999999) is False


# ---------------------------------------------------------------------------
# _is_stale
# ---------------------------------------------------------------------------


def test_is_stale_fresh_heartbeat() -> None:
    """Recent heartbeat is not stale."""
    now = datetime.now(timezone.utc).isoformat()
    assert _is_stale({"heartbeat_at": now}, stale_seconds=60) is False


def test_is_stale_old_heartbeat() -> None:
    """Old heartbeat is stale."""
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert _is_stale({"heartbeat_at": old}, stale_seconds=60) is True


def test_is_stale_missing_heartbeat() -> None:
    """Missing heartbeat is treated as stale."""
    assert _is_stale({}, stale_seconds=60) is True
    assert _is_stale({"heartbeat_at": ""}, stale_seconds=60) is True


def test_is_stale_minimum_threshold() -> None:
    """Stale threshold is at least 1 second."""
    now = datetime.now(timezone.utc).isoformat()
    assert _is_stale({"heartbeat_at": now}, stale_seconds=0) is False


# ---------------------------------------------------------------------------
# read_json_file
# ---------------------------------------------------------------------------


def test_read_json_file_valid(tmp_path) -> None:
    """read_json_file reads a valid JSON object."""
    f = tmp_path / "data.json"
    f.write_text('{"key": "value"}', encoding="utf-8")
    result = read_json_file(f)
    assert result == {"key": "value"}


def test_read_json_file_missing(tmp_path) -> None:
    """read_json_file returns None for missing file."""
    result = read_json_file(tmp_path / "nonexistent.json")
    assert result is None


def test_read_json_file_invalid_json(tmp_path) -> None:
    """read_json_file returns None for invalid JSON."""
    f = tmp_path / "bad.json"
    f.write_text("not json", encoding="utf-8")
    result = read_json_file(f)
    assert result is None


def test_read_json_file_non_dict(tmp_path) -> None:
    """read_json_file returns None when file contains non-dict JSON."""
    f = tmp_path / "array.json"
    f.write_text("[1, 2, 3]", encoding="utf-8")
    result = read_json_file(f)
    assert result is None


# ---------------------------------------------------------------------------
# active_lock_state
# ---------------------------------------------------------------------------


def test_active_lock_state_fresh_and_alive(tmp_path) -> None:
    """active_lock_state returns state when PID is alive and heartbeat fresh."""
    lock_file = tmp_path / "writer.lock"
    state = {
        "pid": os.getpid(),
        "heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "owner": "test",
    }
    lock_file.write_text(json.dumps(state), encoding="utf-8")

    result = active_lock_state(lock_file, stale_seconds=60)
    assert result is not None
    assert result["owner"] == "test"


def test_active_lock_state_stale(tmp_path) -> None:
    """active_lock_state returns None when heartbeat is stale."""
    lock_file = tmp_path / "writer.lock"
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    state = {"pid": os.getpid(), "heartbeat_at": old, "owner": "test"}
    lock_file.write_text(json.dumps(state), encoding="utf-8")

    result = active_lock_state(lock_file, stale_seconds=60)
    assert result is None


def test_active_lock_state_dead_pid(tmp_path) -> None:
    """active_lock_state returns None when PID is dead."""
    lock_file = tmp_path / "writer.lock"
    state = {
        "pid": 999999999,
        "heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "owner": "test",
    }
    lock_file.write_text(json.dumps(state), encoding="utf-8")

    result = active_lock_state(lock_file, stale_seconds=60)
    assert result is None


def test_active_lock_state_missing_file(tmp_path) -> None:
    """active_lock_state returns None for missing lock file."""
    result = active_lock_state(tmp_path / "missing.lock")
    assert result is None


# ---------------------------------------------------------------------------
# ServiceLock
# ---------------------------------------------------------------------------


def test_service_lock_acquire_release(tmp_path) -> None:
    """ServiceLock acquires and releases cleanly."""
    lock_file = tmp_path / "index" / "writer.lock"
    lock = ServiceLock(lock_file, stale_seconds=60)

    state = lock.acquire("test", "lerim test")

    assert lock_file.exists()
    assert state["pid"] == os.getpid()
    assert state["owner"] == "test"

    lock.release()
    assert not lock_file.exists()


def test_service_lock_reclaims_stale(tmp_path) -> None:
    """ServiceLock reclaims a stale lock (dead PID, old heartbeat)."""
    lock_file = tmp_path / "writer.lock"
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    stale_state = {"pid": 999999999, "heartbeat_at": old, "owner": "old-proc"}
    lock_file.write_text(json.dumps(stale_state), encoding="utf-8")

    lock = ServiceLock(lock_file, stale_seconds=60)
    state = lock.acquire("new-owner", "lerim sync")

    assert state["owner"] == "new-owner"
    assert state["pid"] == os.getpid()
    lock.release()


def test_service_lock_reclaims_reused_pid(monkeypatch, tmp_path) -> None:
    """ServiceLock treats a fresh lock with a reused PID as stale."""
    lock_file = tmp_path / "writer.lock"
    fresh = datetime.now(timezone.utc).isoformat()
    old_state = {
        "pid": os.getpid(),
        "heartbeat_at": fresh,
        "owner": "old-container",
        "process_start_ticks": "old-start",
    }
    lock_file.write_text(json.dumps(old_state), encoding="utf-8")
    monkeypatch.setattr(daemon, "_process_start_ticks", lambda _pid: "new-start")

    lock = ServiceLock(lock_file, stale_seconds=60)
    state = lock.acquire("new-container", "lerim sync")

    assert state["owner"] == "new-container"
    assert state["process_start_ticks"] == "new-start"
    lock.release()


def test_service_lock_busy_raises(tmp_path) -> None:
    """ServiceLock raises LockBusyError when lock is actively held."""
    lock_file = tmp_path / "writer.lock"
    fresh = datetime.now(timezone.utc).isoformat()
    active_state = {
        "pid": os.getpid(),
        "heartbeat_at": fresh,
        "owner": "other-service",
    }
    lock_file.write_text(json.dumps(active_state), encoding="utf-8")

    lock = ServiceLock(lock_file, stale_seconds=60)
    with pytest.raises(LockBusyError):
        lock.acquire("conflicting", "lerim maintain")


def test_service_lock_refreshes_heartbeat(tmp_path) -> None:
    """Long-held locks refresh heartbeat so they are not reclaimed as stale."""
    lock_file = tmp_path / "writer.lock"
    lock = ServiceLock(lock_file, stale_seconds=3)
    state = lock.acquire("test", "lerim test")
    first = state["heartbeat_at"]

    time.sleep(1.2)

    updated = json.loads(lock_file.read_text(encoding="utf-8"))
    assert updated["heartbeat_at"] != first
    lock.release()


def test_service_lock_release_without_acquire(tmp_path) -> None:
    """ServiceLock.release is a no-op when not held."""
    lock_file = tmp_path / "writer.lock"
    lock = ServiceLock(lock_file, stale_seconds=60)
    # Should not raise
    lock.release()


def test_service_lock_release_wrong_pid(tmp_path) -> None:
    """ServiceLock does not delete lock file held by different PID."""
    lock_file = tmp_path / "writer.lock"
    lock = ServiceLock(lock_file, stale_seconds=60)

    # Acquire the lock
    lock.acquire("test", "lerim test")

    # Tamper with the file to simulate different PID
    state = json.loads(lock_file.read_text(encoding="utf-8"))
    state["pid"] = 999999999
    lock_file.write_text(json.dumps(state), encoding="utf-8")

    # Release should leave file intact (different PID)
    lock.release()
    assert lock_file.exists()


# ---------------------------------------------------------------------------
# LockBusyError
# ---------------------------------------------------------------------------


def test_lock_busy_error_str_with_state(tmp_path) -> None:
    """LockBusyError.__str__ shows owner and PID when state is present."""
    err = LockBusyError(
        lock_path=tmp_path / "writer.lock",
        state={"owner": "sync", "pid": 12345},
    )
    s = str(err)
    assert "sync" in s
    assert "12345" in s


def test_lock_busy_error_str_without_state(tmp_path) -> None:
    """LockBusyError.__str__ shows path when state is None."""
    err = LockBusyError(lock_path=tmp_path / "writer.lock", state=None)
    s = str(err)
    assert "writer.lock" in s


# ---------------------------------------------------------------------------
# OperationResult
# ---------------------------------------------------------------------------


def test_operation_result_to_details_json_sync() -> None:
    """to_details_json strips zero/empty values and metadata keys."""
    op = OperationResult(
        operation="sync",
        status="completed",
        trigger="manual",
        extracted_sessions=3,
        failed_sessions=0,
        cost_usd=0.005,
    )
    details = op.to_details_json()

    # Stripped: operation, status, trigger (separate columns)
    assert "operation" not in details
    assert "status" not in details
    assert "trigger" not in details

    # Stripped: zero/empty values
    assert "failed_sessions" not in details
    assert "run_ids" not in details  # empty list
    assert "extracted_sessions" not in details

    # Present: non-zero values
    assert details["cost_usd"] == 0.005


def test_operation_result_to_details_json_dry_run() -> None:
    """to_details_json strips dry_run=False but keeps dry_run=True."""
    op_false = OperationResult(
        operation="sync",
        status="completed",
        trigger="api",
        dry_run=False,
    )
    assert "dry_run" not in op_false.to_details_json()

    op_true = OperationResult(
        operation="sync",
        status="completed",
        trigger="api",
        dry_run=True,
    )
    assert op_true.to_details_json()["dry_run"] is True


def test_operation_result_to_span_attrs_sync() -> None:
    """to_span_attrs returns flat attributes for sync operation."""
    op = OperationResult(
        operation="sync",
        status="completed",
        trigger="daemon",
        extracted_sessions=5,
        failed_sessions=1,
        cost_usd=0.01,
        error=None,
    )
    attrs = op.to_span_attrs()

    assert attrs["operation"] == "sync"
    assert attrs["extracted_sessions"] == 5
    assert attrs["cost_usd"] == 0.01
    assert "error" not in attrs  # None -> excluded


def test_operation_result_to_span_attrs_maintain() -> None:
    """to_span_attrs returns projects_count for maintain operation."""
    op = OperationResult(
        operation="maintain",
        status="completed",
        trigger="daemon",
        projects={"proj-a": {}, "proj-b": {}},
    )
    attrs = op.to_span_attrs()

    assert attrs["operation"] == "maintain"
    assert attrs["projects_count"] == 2
    # sync-specific keys absent
    assert "indexed_sessions" not in attrs


def test_operation_result_to_span_attrs_with_error() -> None:
    """to_span_attrs includes error when present."""
    op = OperationResult(
        operation="sync",
        status="failed",
        trigger="api",
        error="something went wrong",
    )
    attrs = op.to_span_attrs()
    assert attrs["error"] == "something went wrong"


# ---------------------------------------------------------------------------
# SyncSummary
# ---------------------------------------------------------------------------


def test_sync_summary_dataclass() -> None:
    """SyncSummary is a frozen dataclass with expected fields."""
    s = SyncSummary(
        indexed_sessions=10,
        extracted_sessions=8,
        skipped_sessions=1,
        failed_sessions=1,
        run_ids=["r1", "r2"],
        cost_usd=0.02,
    )
    assert s.indexed_sessions == 10
    assert s.cost_usd == 0.02
    assert len(s.run_ids) == 2


def test_sync_summary_defaults() -> None:
    """SyncSummary cost_usd defaults to 0.0."""
    s = SyncSummary(
        indexed_sessions=0,
        extracted_sessions=0,
        skipped_sessions=0,
        failed_sessions=0,
        run_ids=[],
    )
    assert s.cost_usd == 0.0


# ---------------------------------------------------------------------------
# resolve_window_bounds
# ---------------------------------------------------------------------------


def _parse_duration(raw: str) -> int:
    """Test helper mirroring parse_duration_to_seconds for resolve_window_bounds."""
    unit = raw[-1]
    amount = int(raw[:-1])
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return amount * multipliers[unit]


def test_resolve_window_bounds_duration(monkeypatch, tmp_path) -> None:
    """Window like '7d' resolves to 7 days before now."""
    cfg = make_config(tmp_path)
    monkeypatch.setattr("lerim.server.daemon.get_config", lambda: cfg)

    start, end = resolve_window_bounds(
        window="7d",
        since_raw=None,
        until_raw=None,
        parse_duration_to_seconds=_parse_duration,
    )

    assert start is not None
    diff = (end - start).total_seconds()
    assert abs(diff - 7 * 86400) < 2


def test_resolve_window_bounds_hours(monkeypatch, tmp_path) -> None:
    """Window like '12h' resolves to 12 hours before now."""
    cfg = make_config(tmp_path)
    monkeypatch.setattr("lerim.server.daemon.get_config", lambda: cfg)

    start, end = resolve_window_bounds(
        window="12h",
        since_raw=None,
        until_raw=None,
        parse_duration_to_seconds=_parse_duration,
    )

    assert start is not None
    diff = (end - start).total_seconds()
    assert abs(diff - 12 * 3600) < 2


def test_resolve_window_bounds_since_until() -> None:
    """Explicit --since/--until overrides window."""
    start, end = resolve_window_bounds(
        window=None,
        since_raw="2026-01-01T00:00:00+00:00",
        until_raw="2026-01-15T00:00:00+00:00",
        parse_duration_to_seconds=_parse_duration,
    )

    assert start is not None
    assert start.month == 1
    assert start.day == 1
    assert end.day == 15


def test_resolve_window_bounds_since_after_until_raises() -> None:
    """--since after --until raises ValueError."""
    with pytest.raises(ValueError, match="--since must be before --until"):
        resolve_window_bounds(
            window=None,
            since_raw="2026-02-01T00:00:00+00:00",
            until_raw="2026-01-01T00:00:00+00:00",
            parse_duration_to_seconds=_parse_duration,
        )


def test_resolve_window_bounds_window_with_since_raises() -> None:
    """--window combined with --since raises ValueError."""
    with pytest.raises(ValueError, match="cannot be combined"):
        resolve_window_bounds(
            window="7d",
            since_raw="2026-01-01T00:00:00+00:00",
            until_raw=None,
            parse_duration_to_seconds=_parse_duration,
        )


def test_resolve_window_bounds_no_window_uses_config(monkeypatch, tmp_path) -> None:
    """No window/since/until falls back to config.sync_window_days."""
    cfg = replace(make_config(tmp_path), sync_window_days=14)
    monkeypatch.setattr("lerim.server.daemon.get_config", lambda: cfg)

    start, end = resolve_window_bounds(
        window=None,
        since_raw=None,
        until_raw=None,
        parse_duration_to_seconds=_parse_duration,
    )

    assert start is not None
    diff = (end - start).total_seconds()
    assert abs(diff - 14 * 86400) < 2


def test_resolve_window_bounds_all_with_db(monkeypatch, tmp_path) -> None:
    """Window 'all' queries database for earliest session."""
    import sqlite3

    db_path = tmp_path / "sessions.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE session_docs (start_time TEXT)")
    conn.execute(
        "INSERT INTO session_docs (start_time) VALUES (?)",
        ("2025-06-15T10:00:00+00:00",),
    )
    conn.commit()
    conn.close()

    cfg = replace(make_config(tmp_path), sessions_db_path=db_path)
    monkeypatch.setattr("lerim.server.daemon.get_config", lambda: cfg)

    start, end = resolve_window_bounds(
        window="all",
        since_raw=None,
        until_raw=None,
        parse_duration_to_seconds=_parse_duration,
    )

    assert start is not None
    assert start.year == 2025
    assert start.month == 6


def test_resolve_window_bounds_all_empty_db(monkeypatch, tmp_path) -> None:
    """Window 'all' with empty database returns None start."""
    import sqlite3

    db_path = tmp_path / "sessions.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE session_docs (start_time TEXT)")
    conn.commit()
    conn.close()

    cfg = replace(make_config(tmp_path), sessions_db_path=db_path)
    monkeypatch.setattr("lerim.server.daemon.get_config", lambda: cfg)

    start, end = resolve_window_bounds(
        window="all",
        since_raw=None,
        until_raw=None,
        parse_duration_to_seconds=_parse_duration,
    )

    assert start is None


def test_resolve_window_bounds_all_no_db(monkeypatch, tmp_path) -> None:
    """Window 'all' with missing database returns None start."""
    cfg = replace(
        make_config(tmp_path),
        sessions_db_path=tmp_path / "nonexistent.sqlite3",
    )
    monkeypatch.setattr("lerim.server.daemon.get_config", lambda: cfg)

    start, end = resolve_window_bounds(
        window="all",
        since_raw=None,
        until_raw=None,
        parse_duration_to_seconds=_parse_duration,
    )

    assert start is None


def test_resolve_window_bounds_since_naive_gets_utc() -> None:
    """Naive --since datetime gets UTC timezone added."""
    start, end = resolve_window_bounds(
        window=None,
        since_raw="2026-01-01T00:00:00",
        until_raw="2026-01-15T00:00:00+00:00",
        parse_duration_to_seconds=_parse_duration,
    )

    assert start is not None
    assert start.tzinfo is not None


# ---------------------------------------------------------------------------
# log_activity
# ---------------------------------------------------------------------------


def test_log_activity_creates_file(tmp_path, monkeypatch) -> None:
    """log_activity creates parent directory and writes log line."""
    log_file = tmp_path / "subdir" / "activity.log"
    monkeypatch.setattr("lerim.server.daemon.ACTIVITY_LOG_PATH", log_file)

    log_activity("sync", "myproject", "2 new", 3.5, cost_usd=0.001)

    assert log_file.exists()
    content = log_file.read_text()
    assert "| sync" in content
    assert "myproject" in content
    assert "$0.0010" in content
    assert "3.5s" in content


def test_log_activity_appends_multiple(tmp_path, monkeypatch) -> None:
    """log_activity appends lines without overwriting."""
    log_file = tmp_path / "activity.log"
    monkeypatch.setattr("lerim.server.daemon.ACTIVITY_LOG_PATH", log_file)

    log_activity("sync", "proj-a", "1 new", 1.0)
    log_activity("maintain", "proj-b", "2 merged", 2.0)

    lines = log_file.read_text().splitlines()
    assert len(lines) == 2


def test_log_activity_cost_formatting(tmp_path, monkeypatch) -> None:
    """log_activity formats cost to 4 decimal places."""
    log_file = tmp_path / "activity.log"
    monkeypatch.setattr("lerim.server.daemon.ACTIVITY_LOG_PATH", log_file)

    log_activity("sync", "proj", "stats", 1.0, cost_usd=0.12345)

    content = log_file.read_text()
    assert "$0.1235" in content  # rounded to 4 decimals


# ---------------------------------------------------------------------------
# _empty_sync_summary
# ---------------------------------------------------------------------------


def test_empty_sync_summary() -> None:
    """_empty_sync_summary returns zeroed-out SyncSummary."""
    from lerim.server.daemon import _empty_sync_summary

    s = _empty_sync_summary()
    assert s.indexed_sessions == 0
    assert s.extracted_sessions == 0
    assert s.run_ids == []
    assert s.cost_usd == 0.0


# ---------------------------------------------------------------------------
# _record_service_event
# ---------------------------------------------------------------------------


def test_record_service_event_calls_fn() -> None:
    """_record_service_event passes correct kwargs to the record function."""
    from lerim.server.daemon import _record_service_event

    captured: list[dict] = []

    def fake_record(**kwargs):
        """Capture service run recording call."""
        captured.append(kwargs)

    _record_service_event(
        fake_record,
        job_type="sync",
        status="completed",
        started_at="2026-01-01T00:00:00+00:00",
        trigger="manual",
        details={"extracted_sessions": 3},
    )

    assert len(captured) == 1
    assert captured[0]["job_type"] == "sync"
    assert captured[0]["status"] == "completed"
    assert captured[0]["trigger"] == "manual"
    assert captured[0]["completed_at"]  # should be set by _now_iso
    assert captured[0]["details"]["extracted_sessions"] == 3


# ---------------------------------------------------------------------------
# lock_path
# ---------------------------------------------------------------------------


def test_lock_path_uses_index_dir(monkeypatch, tmp_path) -> None:
    """lock_path returns path under global_data_dir / index."""
    from lerim.server.daemon import lock_path

    cfg = make_config(tmp_path)
    monkeypatch.setattr("lerim.server.daemon.get_config", lambda: cfg)

    result = lock_path("writer.lock")
    assert result == cfg.global_data_dir / "index" / "writer.lock"


# ---------------------------------------------------------------------------
# _pid_alive edge cases
# ---------------------------------------------------------------------------


def test_pid_alive_permission_error(monkeypatch) -> None:
    """_pid_alive returns True when kill raises PermissionError (process exists)."""

    def mock_kill(pid, sig):
        """Simulate process owned by another user."""
        raise PermissionError("not permitted")

    monkeypatch.setattr(os, "kill", mock_kill)

    result = _pid_alive(12345)
    assert result is True


def test_pid_alive_os_error(monkeypatch) -> None:
    """_pid_alive returns False on generic OSError."""

    def mock_kill(pid, sig):
        """Simulate unexpected OS error."""
        raise OSError("unexpected")

    monkeypatch.setattr(os, "kill", mock_kill)

    result = _pid_alive(12345)
    assert result is False


# ---------------------------------------------------------------------------
# OperationResult edge cases
# ---------------------------------------------------------------------------


def test_operation_result_with_all_fields() -> None:
    """OperationResult with all fields populated serializes correctly."""
    op = OperationResult(
        operation="sync",
        status="partial",
        trigger="daemon",
        indexed_sessions=10,
        queued_sessions=8,
        extracted_sessions=6,
        skipped_sessions=1,
        failed_sessions=3,
        run_ids=["r1", "r2"],
        window_start="2026-01-01T00:00:00+00:00",
        window_end="2026-01-15T00:00:00+00:00",
        projects={},
        cost_usd=0.05,
        error="partial failure",
        dry_run=False,
    )

    details = op.to_details_json()
    assert "extracted_sessions" not in details
    assert details["error"] == "partial failure"
    # empty projects and False dry_run excluded
    assert "projects" not in details
    assert "dry_run" not in details

    attrs = op.to_span_attrs()
    assert attrs["indexed_sessions"] == 10
    assert attrs["error"] == "partial failure"
    assert attrs["cost_usd"] == 0.05


def test_operation_result_maintain_no_cost() -> None:
    """Maintain OperationResult without cost excludes cost_usd from attrs."""
    op = OperationResult(
        operation="maintain",
        status="completed",
        trigger="daemon",
        cost_usd=0.0,
    )
    attrs = op.to_span_attrs()
    assert "cost_usd" not in attrs


# ---------------------------------------------------------------------------
# run_maintain_once dry_run shortcut
# ---------------------------------------------------------------------------


def test_run_maintain_once_dry_run(monkeypatch, tmp_path) -> None:
    """run_maintain_once with dry_run=True returns immediately without lock."""
    from lerim.server import daemon
    from lerim.sessions import catalog

    config_path = Path(tmp_path) / "test_config.toml"
    config_path.write_text(
        f'[data]\ndir = "{tmp_path}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    from lerim.config.settings import reload_config

    reload_config()
    catalog.init_sessions_db()

    code, payload = daemon.run_maintain_once(dry_run=True)
    assert code == EXIT_OK
    assert payload.get("dry_run") is True


# ---------------------------------------------------------------------------
# Exit code constants
# ---------------------------------------------------------------------------


def test_exit_code_values() -> None:
    """Exit code constants have expected values."""
    assert EXIT_OK == 0
    assert EXIT_FATAL == 1
    assert EXIT_PARTIAL == 3
    assert EXIT_LOCK_BUSY == 4
