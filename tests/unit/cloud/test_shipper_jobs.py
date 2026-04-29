"""Unit tests for cloud shipper job-status shipping and status-map.

Tests _JOB_STATUS_MAP completeness/values, _ShipperState.jobs_shipped_at
round-trip persistence, and _query_job_statuses against a real temp SQLite DB.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch


from lerim.cloud.shipper import (
	_JOB_STATUS_MAP,
	_ShipperState,
	_query_job_statuses,
	_ship_job_statuses,
)


# ── status mapping tests ────────────────────────────────────────────────


def test_job_status_map_complete():
	"""All 5 local job statuses have cloud mappings."""
	expected_keys = {"pending", "running", "done", "failed", "dead_letter"}
	assert set(_JOB_STATUS_MAP.keys()) == expected_keys


def test_job_status_map_values():
	"""Each local status maps to the correct cloud name."""
	assert _JOB_STATUS_MAP["pending"] == "queued"
	assert _JOB_STATUS_MAP["running"] == "processing"
	assert _JOB_STATUS_MAP["done"] == "processed"
	assert _JOB_STATUS_MAP["failed"] == "failed"
	assert _JOB_STATUS_MAP["dead_letter"] == "blocked"


# ── ShipperState tests ──────────────────────────────────────────────────


def test_shipper_state_has_jobs_shipped_at():
	"""_ShipperState has a jobs_shipped_at field with empty string default."""
	state = _ShipperState()
	assert hasattr(state, "jobs_shipped_at")
	assert state.jobs_shipped_at == ""


def test_shipper_state_load_saves_jobs_watermark(tmp_path, monkeypatch):
	"""Save state with jobs_shipped_at, load it back -- value preserved."""
	state_path = tmp_path / "cloud_shipper_state.json"
	monkeypatch.setattr("lerim.cloud.shipper._STATE_PATH", state_path)

	state = _ShipperState(jobs_shipped_at="2026-03-25T12:00:00Z")
	state.save()

	loaded = _ShipperState.load()
	assert loaded.jobs_shipped_at == "2026-03-25T12:00:00Z"


# ── SQLite helpers ───────────────────────────────────────────────────────


def _create_jobs_table(db_path):
	"""Create a minimal session_jobs table matching the schema used by _query_job_statuses."""
	conn = sqlite3.connect(db_path)
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
			UNIQUE(run_id, job_type)
		)
		"""
	)
	conn.commit()
	conn.close()


def _insert_job(db_path, run_id, status="pending", updated_at=None, error=None, attempts=0):
	"""Insert a single row into session_jobs for testing."""
	now = updated_at or datetime.now(timezone.utc).isoformat()
	conn = sqlite3.connect(db_path)
	conn.execute(
		"""
		INSERT INTO session_jobs (run_id, job_type, status, attempts, error, available_at, created_at, updated_at)
		VALUES (?, 'extract', ?, ?, ?, ?, ?, ?)
		""",
		(run_id, status, attempts, error, now, now, now),
	)
	conn.commit()
	conn.close()


# ── query tests ──────────────────────────────────────────────────────────


def test_query_job_statuses_empty_db(tmp_path):
	"""Empty/missing DB returns empty list."""
	missing = tmp_path / "nonexistent.sqlite3"
	assert _query_job_statuses(missing, "", 100) == []


def test_query_job_statuses_empty_table(tmp_path):
	"""Existing DB with empty session_jobs table returns empty list."""
	db_path = tmp_path / "sessions.sqlite3"
	_create_jobs_table(db_path)
	assert _query_job_statuses(db_path, "", 100) == []


def test_query_job_statuses_returns_recent(tmp_path):
	"""Query with watermark returns only jobs after that timestamp."""
	db_path = tmp_path / "sessions.sqlite3"
	_create_jobs_table(db_path)

	_insert_job(db_path, "old-job", status="done", updated_at="2026-03-01T00:00:00Z")
	_insert_job(db_path, "new-job-1", status="running", updated_at="2026-03-20T10:00:00Z")
	_insert_job(db_path, "new-job-2", status="pending", updated_at="2026-03-20T11:00:00Z")

	watermark = "2026-03-10T00:00:00Z"
	rows = _query_job_statuses(db_path, watermark, 100)

	run_ids = {r["run_id"] for r in rows}
	assert "old-job" not in run_ids
	assert "new-job-1" in run_ids
	assert "new-job-2" in run_ids
	assert len(rows) == 2


def test_query_job_statuses_no_watermark(tmp_path):
	"""Query with empty watermark returns all rows."""
	db_path = tmp_path / "sessions.sqlite3"
	_create_jobs_table(db_path)

	for i in range(5):
		_insert_job(db_path, f"job-{i}", updated_at=f"2026-03-{10+i:02d}T00:00:00Z")

	rows = _query_job_statuses(db_path, "", 100)
	assert len(rows) == 5


def test_query_job_statuses_limit(tmp_path):
	"""Limit parameter caps the number of returned rows."""
	db_path = tmp_path / "sessions.sqlite3"
	_create_jobs_table(db_path)

	for i in range(10):
		_insert_job(db_path, f"job-{i}", updated_at=f"2026-03-{10+i:02d}T00:00:00Z")

	rows = _query_job_statuses(db_path, "", 3)
	assert len(rows) == 3


def test_query_job_statuses_ordered_by_updated_at(tmp_path):
	"""Rows are returned in ascending updated_at order."""
	db_path = tmp_path / "sessions.sqlite3"
	_create_jobs_table(db_path)

	# Insert in reverse order
	_insert_job(db_path, "c", updated_at="2026-03-15T00:00:00Z")
	_insert_job(db_path, "a", updated_at="2026-03-10T00:00:00Z")
	_insert_job(db_path, "b", updated_at="2026-03-12T00:00:00Z")

	rows = _query_job_statuses(db_path, "", 100)
	run_ids = [r["run_id"] for r in rows]
	assert run_ids == ["a", "b", "c"]


# ── _ship_job_statuses tests ────────────────────────────────────────────


def test_ship_job_statuses_maps_and_advances_watermark(tmp_path):
	"""_ship_job_statuses maps statuses, ships payload, and advances state watermark."""
	db_path = tmp_path / "sessions.sqlite3"
	_create_jobs_table(db_path)

	_insert_job(db_path, "j1", status="pending", updated_at="2026-03-20T01:00:00Z")
	_insert_job(db_path, "j2", status="done", updated_at="2026-03-20T02:00:00Z")
	_insert_job(db_path, "j3", status="dead_letter", updated_at="2026-03-20T03:00:00Z")

	state = _ShipperState(jobs_shipped_at="")

	captured_payloads = []

	async def mock_post_batch(endpoint, path, token, payload):
		captured_payloads.append(payload)
		return True

	with patch("lerim.cloud.shipper._post_batch", side_effect=mock_post_batch):
		shipped = asyncio.run(
			_ship_job_statuses("http://test", "tok", state, db_path)
		)

	assert shipped == 3
	assert state.jobs_shipped_at == "2026-03-20T03:00:00Z"

	# Verify status mapping in the payload
	statuses_sent = captured_payloads[0]["statuses"]
	status_by_run = {s["run_id"]: s["processing_status"] for s in statuses_sent}
	assert status_by_run["j1"] == "queued"
	assert status_by_run["j2"] == "processed"
	assert status_by_run["j3"] == "blocked"


def test_ship_job_statuses_no_rows_does_not_advance(tmp_path):
	"""When no rows exist, shipped count is 0 and watermark stays unchanged."""
	db_path = tmp_path / "sessions.sqlite3"
	_create_jobs_table(db_path)

	state = _ShipperState(jobs_shipped_at="2026-03-01T00:00:00Z")

	with patch("lerim.cloud.shipper._post_batch", new_callable=AsyncMock) as mock:
		shipped = asyncio.run(
			_ship_job_statuses("http://test", "tok", state, db_path)
		)

	assert shipped == 0
	assert state.jobs_shipped_at == "2026-03-01T00:00:00Z"
	mock.assert_not_called()


def test_ship_job_statuses_stops_on_post_failure(tmp_path):
	"""When _post_batch returns False, shipping stops and watermark is not advanced."""
	db_path = tmp_path / "sessions.sqlite3"
	_create_jobs_table(db_path)

	_insert_job(db_path, "j1", status="running", updated_at="2026-03-20T01:00:00Z")

	state = _ShipperState(jobs_shipped_at="")

	async def mock_fail(*args, **kwargs):
		return False

	with patch("lerim.cloud.shipper._post_batch", side_effect=mock_fail):
		shipped = asyncio.run(
			_ship_job_statuses("http://test", "tok", state, db_path)
		)

	assert shipped == 0
	assert state.jobs_shipped_at == ""
