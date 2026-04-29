"""Unit tests for cloud shipper — local-to-cloud data sync.

Tests ship_once, _pull_records, _ship_sessions, _ship_records,
_ship_logs, _ship_service_runs, HTTP helpers, state persistence,
and error handling. All network calls are mocked.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lerim.cloud.shipper import (
	_ShipperState,
	_get_json_sync,
	_is_cloud_configured,
	_post_batch_sync,
	_pull_records,
	_query_context_records,
	_query_new_sessions,
	_query_service_runs,
	_read_transcript,
	_ship_logs,
	_ship_records,
	_ship_service_runs,
	_ship_sessions,
	ship_once,
)
from lerim.context import ContextStore, resolve_project_identity
from tests.helpers import make_config


def _dated_log_file(root: Path) -> Path:
	path = root / "2026" / "03" / "01" / "lerim.jsonl"
	path.parent.mkdir(parents=True, exist_ok=True)
	return path


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_embeddings(monkeypatch):
	"""Use deterministic in-memory embeddings in cloud shipper tests."""
	provider = MagicMock()
	provider.embedding_dims = 384
	provider.model_id = "test-model"
	provider.embed_document.return_value = [0.1] * 384
	provider.embed_query.return_value = [0.1] * 384
	monkeypatch.setattr("lerim.context.store.get_embedding_provider", lambda: provider)
	monkeypatch.setattr(
		"lerim.context.embedding.get_embedding_provider", lambda: provider
	)


def _create_sessions_table(db_path: Path) -> None:
	"""Create a minimal session_docs table for testing."""
	conn = sqlite3.connect(db_path)
	conn.execute(
		"""
		CREATE TABLE IF NOT EXISTS session_docs (
			run_id TEXT PRIMARY KEY,
			agent_type TEXT,
			repo_path TEXT,
			repo_name TEXT,
			start_time TEXT,
			indexed_at TEXT,
			status TEXT,
			duration_ms INTEGER,
			message_count INTEGER,
			tool_call_count INTEGER,
			error_count INTEGER,
			total_tokens INTEGER,
			summary_text TEXT,
			tags TEXT,
			outcome TEXT,
			session_path TEXT
		)
		"""
	)
	conn.commit()
	conn.close()


def _insert_session(db_path: Path, run_id: str, indexed_at: str, **kwargs) -> None:
	"""Insert a session_docs row for testing."""
	defaults = {
		"agent_type": "claude",
		"repo_path": "/tmp/repo",
		"repo_name": "repo",
		"start_time": indexed_at,
		"status": "complete",
		"duration_ms": 1000,
		"message_count": 5,
		"tool_call_count": 2,
		"error_count": 0,
		"total_tokens": 500,
		"summary_text": "test session",
		"tags": "",
		"outcome": "success",
		"session_path": None,
	}
	defaults.update(kwargs)
	conn = sqlite3.connect(db_path)
	conn.execute(
		"""
		INSERT INTO session_docs (
			run_id, agent_type, repo_path, repo_name, start_time,
			indexed_at, status, duration_ms, message_count,
			tool_call_count, error_count, total_tokens,
			summary_text, tags, outcome, session_path
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		""",
		(
			run_id, defaults["agent_type"], defaults["repo_path"],
			defaults["repo_name"], defaults["start_time"],
			indexed_at, defaults["status"], defaults["duration_ms"],
			defaults["message_count"], defaults["tool_call_count"],
			defaults["error_count"], defaults["total_tokens"],
			defaults["summary_text"], defaults["tags"],
			defaults["outcome"], defaults["session_path"],
		),
	)
	conn.commit()
	conn.close()


def _create_service_runs_table(db_path: Path) -> None:
	"""Create a minimal service_runs table for testing."""
	conn = sqlite3.connect(db_path)
	conn.execute(
		"""
		CREATE TABLE IF NOT EXISTS service_runs (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			job_type TEXT NOT NULL,
			status TEXT NOT NULL,
			started_at TEXT,
			completed_at TEXT,
			trigger TEXT,
			details_json TEXT
		)
		"""
	)
	conn.commit()
	conn.close()


def _insert_context_record(
	context_db_path: Path,
	project_path: Path,
	record_id: str,
	*,
	kind: str = "fact",
	title: str = "Test Record",
	summary: str = "Test summary",
	content: str = "content",
) -> None:
	"""Insert one canonical context record for shipping tests."""
	store = ContextStore(context_db_path)
	store.initialize()
	identity = resolve_project_identity(project_path)
	store.register_project(identity)
	store.create_record(
		project_id=identity.project_id,
		session_id=None,
		record_id=record_id,
		kind=kind,
		title=title,
		body=content or summary,
	)


# ---------------------------------------------------------------------------
# _ShipperState
# ---------------------------------------------------------------------------


class TestShipperState:
	"""Tests for shipper state persistence."""

	def test_defaults(self):
		"""Fresh state has zero offsets and empty watermarks."""
		state = _ShipperState()
		assert state.log_offset_bytes == 0
		assert state.sessions_shipped_at == ""
		assert state.records_shipped_at == ""

	def test_save_and_load(self, tmp_path, monkeypatch):
		"""State survives save/load round-trip."""
		state_path = tmp_path / "state.json"
		monkeypatch.setattr("lerim.cloud.shipper._STATE_PATH", state_path)

		state = _ShipperState(
			log_offset_bytes=1024,
			sessions_shipped_at="2026-03-20T00:00:00Z",
			records_shipped_at="2026-03-21T00:00:00Z",
		)
		state.save()

		loaded = _ShipperState.load()
		assert loaded.log_offset_bytes == 1024
		assert loaded.sessions_shipped_at == "2026-03-20T00:00:00Z"
		assert loaded.records_shipped_at == "2026-03-21T00:00:00Z"

	def test_load_missing_file(self, tmp_path, monkeypatch):
		"""Load from non-existent file returns defaults."""
		monkeypatch.setattr(
			"lerim.cloud.shipper._STATE_PATH", tmp_path / "missing.json"
		)
		state = _ShipperState.load()
		assert state.log_offset_bytes == 0

	def test_load_corrupt_file(self, tmp_path, monkeypatch):
		"""Load from corrupt file returns defaults."""
		state_path = tmp_path / "state.json"
		state_path.write_text("not json", encoding="utf-8")
		monkeypatch.setattr("lerim.cloud.shipper._STATE_PATH", state_path)
		state = _ShipperState.load()
		assert state.log_offset_bytes == 0

	def test_load_non_dict_json(self, tmp_path, monkeypatch):
		"""Load from JSON that is not a dict returns defaults."""
		state_path = tmp_path / "state.json"
		state_path.write_text("[1,2,3]", encoding="utf-8")
		monkeypatch.setattr("lerim.cloud.shipper._STATE_PATH", state_path)
		state = _ShipperState.load()
		assert state.log_offset_bytes == 0


# ---------------------------------------------------------------------------
# _is_cloud_configured
# ---------------------------------------------------------------------------


class TestIsCloudConfigured:
	"""Tests for cloud configuration check."""

	def test_not_configured_no_token(self, tmp_path):
		"""Returns False when cloud_token is None."""
		cfg = make_config(tmp_path)
		assert cfg.cloud_token is None
		assert not _is_cloud_configured(cfg)

	def test_configured(self, tmp_path):
		"""Returns True when both token and endpoint are set."""
		cfg = replace(
			make_config(tmp_path),
			cloud_token="tok-123",
			cloud_endpoint="https://api.lerim.dev",
		)
		assert _is_cloud_configured(cfg)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


class TestPostBatchSync:
	"""Tests for synchronous HTTP POST helper."""

	def test_success(self):
		"""Successful 200 POST returns True."""
		mock_resp = MagicMock()
		mock_resp.status = 200
		mock_resp.__enter__ = MagicMock(return_value=mock_resp)
		mock_resp.__exit__ = MagicMock(return_value=False)

		with patch("lerim.cloud.shipper.urllib.request.urlopen", return_value=mock_resp):
			result = _post_batch_sync(
				"https://api.test", "/api/v1/ingest/logs", "tok", {"entries": []}
			)
		assert result is True

	def test_http_error(self):
		"""HTTPError returns False."""
		import urllib.error

		exc = urllib.error.HTTPError(
			"https://api.test/path", 500, "Server Error", {}, None
		)
		with patch("lerim.cloud.shipper.urllib.request.urlopen", side_effect=exc):
			result = _post_batch_sync(
				"https://api.test", "/path", "tok", {}
			)
		assert result is False

	def test_url_error(self):
		"""URLError returns False."""
		import urllib.error

		exc = urllib.error.URLError("Connection refused")
		with patch("lerim.cloud.shipper.urllib.request.urlopen", side_effect=exc):
			result = _post_batch_sync(
				"https://api.test", "/path", "tok", {}
			)
		assert result is False


class TestGetJsonSync:
	"""Tests for synchronous HTTP GET helper."""

	def test_success(self):
		"""Successful GET returns parsed JSON."""
		mock_resp = MagicMock()
		mock_resp.read.return_value = b'{"ok": true}'
		mock_resp.__enter__ = MagicMock(return_value=mock_resp)
		mock_resp.__exit__ = MagicMock(return_value=False)

		with patch("lerim.cloud.shipper.urllib.request.urlopen", return_value=mock_resp):
			result = _get_json_sync(
				"https://api.test", "/api/v1/data", "tok", {"limit": "10"}
			)
		assert result == {"ok": True}

	def test_failure(self):
		"""Failed GET returns None."""
		import urllib.error

		exc = urllib.error.URLError("timeout")
		with patch("lerim.cloud.shipper.urllib.request.urlopen", side_effect=exc):
			result = _get_json_sync("https://api.test", "/path", "tok", {})
		assert result is None


# ---------------------------------------------------------------------------
# _read_transcript
# ---------------------------------------------------------------------------


class TestReadTranscript:
	"""Tests for transcript file reading."""

	def test_reads_file(self, tmp_path):
		"""Valid file path returns contents."""
		p = tmp_path / "transcript.jsonl"
		p.write_text('{"msg":"hi"}\n', encoding="utf-8")
		assert _read_transcript(str(p)) is not None

	def test_none_path(self):
		"""None session_path returns None."""
		assert _read_transcript(None) is None

	def test_empty_path(self):
		"""Empty string path returns None."""
		assert _read_transcript("") is None

	def test_missing_file(self, tmp_path):
		"""Non-existent file returns None."""
		assert _read_transcript(str(tmp_path / "missing.jsonl")) is None


# ---------------------------------------------------------------------------
# _query_new_sessions
# ---------------------------------------------------------------------------


class TestQueryNewSessions:
	"""Tests for session query helper."""

	def test_missing_db(self, tmp_path):
		"""Missing DB file returns empty list."""
		assert _query_new_sessions(tmp_path / "missing.db", "", 100) == []

	def test_empty_table(self, tmp_path):
		"""Empty table returns empty list."""
		db_path = tmp_path / "sessions.sqlite3"
		_create_sessions_table(db_path)
		assert _query_new_sessions(db_path, "", 100) == []

	def test_returns_rows(self, tmp_path):
		"""Rows matching watermark are returned."""
		db_path = tmp_path / "sessions.sqlite3"
		_create_sessions_table(db_path)
		_insert_session(db_path, "s1", "2026-03-01T00:00:00Z")
		_insert_session(db_path, "s2", "2026-03-20T00:00:00Z")

		rows = _query_new_sessions(db_path, "2026-03-10T00:00:00Z", 100)
		assert len(rows) == 1
		assert rows[0]["run_id"] == "s2"

	def test_no_watermark_returns_all(self, tmp_path):
		"""Empty watermark returns all rows."""
		db_path = tmp_path / "sessions.sqlite3"
		_create_sessions_table(db_path)
		_insert_session(db_path, "s1", "2026-03-01T00:00:00Z")
		_insert_session(db_path, "s2", "2026-03-20T00:00:00Z")

		rows = _query_new_sessions(db_path, "", 100)
		assert len(rows) == 2


# ---------------------------------------------------------------------------
# _ship_sessions
# ---------------------------------------------------------------------------


class TestShipSessions:
	"""Tests for session shipping."""

	def test_ships_and_advances_watermark(self, tmp_path):
		"""Successful shipping advances the state watermark."""
		db_path = tmp_path / "sessions.sqlite3"
		_create_sessions_table(db_path)
		_insert_session(db_path, "s1", "2026-03-20T01:00:00Z")
		_insert_session(db_path, "s2", "2026-03-20T02:00:00Z")

		state = _ShipperState()

		async def mock_post(*args, **kwargs):
			"""Always succeed."""
			return True

		with patch("lerim.cloud.shipper._post_batch", side_effect=mock_post):
			shipped = asyncio.run(
				_ship_sessions("https://api.test", "tok", state, db_path)
			)

		assert shipped == 2
		assert state.sessions_shipped_at == "2026-03-20T02:00:00Z"

	def test_stops_on_failure(self, tmp_path):
		"""Post failure stops shipping and does not advance watermark."""
		db_path = tmp_path / "sessions.sqlite3"
		_create_sessions_table(db_path)
		_insert_session(db_path, "s1", "2026-03-20T01:00:00Z")

		state = _ShipperState()

		async def mock_fail(*args, **kwargs):
			"""Always fail."""
			return False

		with patch("lerim.cloud.shipper._post_batch", side_effect=mock_fail):
			shipped = asyncio.run(
				_ship_sessions("https://api.test", "tok", state, db_path)
			)

		assert shipped == 0
		assert state.sessions_shipped_at == ""

	def test_includes_transcript(self, tmp_path):
		"""Transcript is included when session_path points to valid file."""
		db_path = tmp_path / "sessions.sqlite3"
		_create_sessions_table(db_path)

		transcript_path = tmp_path / "transcript.jsonl"
		transcript_path.write_text('{"msg":"hello"}\n', encoding="utf-8")

		_insert_session(
			db_path, "s1", "2026-03-20T01:00:00Z",
			session_path=str(transcript_path),
		)

		state = _ShipperState()
		captured = []

		async def mock_post(endpoint, path, token, payload):
			"""Capture payload."""
			captured.append(payload)
			return True

		with patch("lerim.cloud.shipper._post_batch", side_effect=mock_post):
			asyncio.run(
				_ship_sessions("https://api.test", "tok", state, db_path)
			)

		assert len(captured) == 1
		sessions = captured[0]["sessions"]
		assert any("transcript_jsonl" in s for s in sessions)

	def test_includes_project_for_known_repo_path(self, tmp_path):
		"""Session payload includes configured project resolved from repo_path."""
		db_path = tmp_path / "sessions.sqlite3"
		_create_sessions_table(db_path)
		project_dir = tmp_path / "alpha"
		nested_dir = project_dir / "pkg"
		nested_dir.mkdir(parents=True)
		other_project = tmp_path / "beta"
		other_project.mkdir()
		_insert_session(
			db_path,
			"s-project",
			"2026-03-20T01:00:00Z",
			repo_path=str(nested_dir),
			repo_name="alpha",
		)

		state = _ShipperState()
		captured = []

		async def mock_post(endpoint, path, token, payload):
			"""Capture payload."""
			captured.append(payload)
			return True

		with patch("lerim.cloud.shipper._post_batch", side_effect=mock_post):
			shipped = asyncio.run(
				_ship_sessions(
					"https://api.test",
					"tok",
					state,
					db_path,
					{"alpha": str(project_dir), "beta": str(other_project)},
				)
			)

		assert shipped == 1
		assert captured[0]["sessions"][0]["project"] == "alpha"

	def test_unresolved_repo_path_does_not_fall_back_to_first_project(self, tmp_path):
		"""Unmatched session repo_path ships without bogus project attribution."""
		db_path = tmp_path / "sessions.sqlite3"
		_create_sessions_table(db_path)
		alpha_dir = tmp_path / "alpha"
		beta_dir = tmp_path / "beta"
		unmatched_dir = tmp_path / "outside"
		alpha_dir.mkdir()
		beta_dir.mkdir()
		unmatched_dir.mkdir()
		_insert_session(
			db_path,
			"s-unresolved",
			"2026-03-20T01:00:00Z",
			repo_path=str(unmatched_dir),
			repo_name="outside",
		)

		state = _ShipperState()
		captured = []

		async def mock_post(endpoint, path, token, payload):
			"""Capture payload."""
			captured.append(payload)
			return True

		with patch("lerim.cloud.shipper._post_batch", side_effect=mock_post):
			shipped = asyncio.run(
				_ship_sessions(
					"https://api.test",
					"tok",
					state,
					db_path,
					{"alpha": str(alpha_dir), "beta": str(beta_dir)},
				)
			)

		assert shipped == 1
		assert "project" not in captured[0]["sessions"][0]
		assert captured[0]["sessions"][0]["repo_name"] == "outside"


# ---------------------------------------------------------------------------
# _ship_logs
# ---------------------------------------------------------------------------


class TestShipLogs:
	"""Tests for log file shipping."""

	def test_no_log_file(self, tmp_path, monkeypatch):
		"""No log file returns 0 shipped."""
		monkeypatch.setattr("lerim.cloud.shipper.LOG_DIR", tmp_path)
		state = _ShipperState(log_file="lerim.jsonl")
		shipped = asyncio.run(_ship_logs("https://api.test", "tok", state))
		assert shipped == 0

	def test_ships_log_entries(self, tmp_path, monkeypatch):
		"""Log entries are shipped and offset is advanced."""
		monkeypatch.setattr("lerim.cloud.shipper.LOG_DIR", tmp_path)
		log_file = _dated_log_file(tmp_path)
		entries = [
			json.dumps({"level": "info", "msg": f"line {i}"})
			for i in range(3)
		]
		log_file.write_text("\n".join(entries) + "\n", encoding="utf-8")

		state = _ShipperState(log_file="lerim.jsonl", log_offset_bytes=0)

		async def mock_post(*args, **kwargs):
			"""Always succeed."""
			return True

		with patch("lerim.cloud.shipper._post_batch", side_effect=mock_post):
			shipped = asyncio.run(
				_ship_logs("https://api.test", "tok", state)
			)

		assert shipped == 3
		assert state.log_offset_bytes > 0

	def test_detects_log_rotation(self, tmp_path, monkeypatch):
		"""Offset reset when file is smaller than stored offset."""
		monkeypatch.setattr("lerim.cloud.shipper.LOG_DIR", tmp_path)
		log_file = _dated_log_file(tmp_path)
		log_file.write_text('{"msg":"new"}\n', encoding="utf-8")

		state = _ShipperState(
			log_file="lerim.jsonl",
			log_offset_bytes=99999,
		)

		async def mock_post(*args, **kwargs):
			"""Always succeed."""
			return True

		with patch("lerim.cloud.shipper._post_batch", side_effect=mock_post):
			shipped = asyncio.run(
				_ship_logs("https://api.test", "tok", state)
			)

		assert shipped == 1

	def test_stops_on_batch_failure(self, tmp_path, monkeypatch):
		"""Log shipping stops on first batch failure."""
		monkeypatch.setattr("lerim.cloud.shipper.LOG_DIR", tmp_path)
		log_file = _dated_log_file(tmp_path)
		entries = [json.dumps({"msg": f"line {i}"}) for i in range(3)]
		log_file.write_text("\n".join(entries) + "\n", encoding="utf-8")

		state = _ShipperState(log_file="lerim.jsonl", log_offset_bytes=0)

		async def mock_fail(*args, **kwargs):
			"""Always fail."""
			return False

		with patch("lerim.cloud.shipper._post_batch", side_effect=mock_fail):
			shipped = asyncio.run(
				_ship_logs("https://api.test", "tok", state)
			)

		# Partial batch sent, all fail
		assert shipped == 0
		assert state.log_offset_bytes == 0

	def test_keeps_offset_at_failed_batch_start(self, tmp_path, monkeypatch):
		"""Successful batches advance, but a later failed batch is retried."""
		monkeypatch.setattr("lerim.cloud.shipper.LOG_DIR", tmp_path)
		log_file = _dated_log_file(tmp_path)
		entries = [json.dumps({"msg": f"line {i}"}) for i in range(501)]
		log_file.write_text("\n".join(entries) + "\n", encoding="utf-8")

		first_batch_size = len(("\n".join(entries[:500]) + "\n").encode("utf-8"))
		state = _ShipperState(log_file="lerim.jsonl", log_offset_bytes=0)
		calls = 0

		async def mock_post(*args, **kwargs):
			"""Succeed once, then fail."""
			nonlocal calls
			calls += 1
			return calls == 1

		with patch("lerim.cloud.shipper._post_batch", side_effect=mock_post):
			shipped = asyncio.run(
				_ship_logs("https://api.test", "tok", state)
			)

		assert shipped == 500
		assert state.log_offset_bytes == first_batch_size


# ---------------------------------------------------------------------------
# _query_context_records
# ---------------------------------------------------------------------------


class TestQueryContextRecords:
	"""Tests for context-DB record scanning."""

	def test_queries_registered_project_records(self, tmp_path):
		"""Query returns durable records for the selected projects."""
		project_dir = tmp_path / "project_a"
		project_dir.mkdir()
		context_db_path = tmp_path / "context.sqlite3"
		_insert_context_record(context_db_path, project_dir, "rec-001")

		results = _query_context_records(
			context_db_path, {"project_a": str(project_dir)}, "", 100
		)
		assert len(results) == 1
		assert results[0]["project"] == "project_a"
		assert results[0]["record_id"] == "rec-001"
		assert results[0]["created_at"]
		assert results[0]["valid_from"]

	def test_excludes_episode_records(self, tmp_path):
		"""Episode rows are not shipped through cloud record sync."""
		project_dir = tmp_path / "project_a"
		project_dir.mkdir()
		context_db_path = tmp_path / "context.sqlite3"
		store = ContextStore(context_db_path)
		store.initialize()
		identity = resolve_project_identity(project_dir)
		store.register_project(identity)
		store.upsert_session(
			project_id=identity.project_id,
			session_id="sess_episode",
			agent_type="test",
			source_trace_ref="seed:episode",
			repo_path=str(project_dir),
			cwd=str(project_dir),
			started_at="2026-04-20T00:00:00+00:00",
			model_name="test-model",
			instructions_text=None,
			prompt_text=None,
			metadata={},
		)
		store.create_record(
			project_id=identity.project_id,
			session_id="sess_episode",
			record_id="ep-001",
			kind="episode",
			title="Episode title",
			body="Episode body",
			user_intent="Fix bug",
			what_happened="Fixed bug",
		)

		results = _query_context_records(
			context_db_path, {"project_a": str(project_dir)}, "", 100
		)
		assert results == []

	def test_respects_watermark(self, tmp_path):
		"""Records with updated_at <= watermark are skipped."""
		project_dir = tmp_path / "project_a"
		project_dir.mkdir()
		context_db_path = tmp_path / "context.sqlite3"
		_insert_context_record(context_db_path, project_dir, "rec-old")

		results = _query_context_records(
			context_db_path, {"project_a": str(project_dir)}, "9999-01-01T00:00:00Z", 100
		)
		assert results == []

	def test_missing_context_db_returns_empty(self, tmp_path):
		"""Missing context DB returns empty results."""
		project_dir = tmp_path / "empty_project"
		project_dir.mkdir()
		results = _query_context_records(tmp_path / "missing.sqlite3", {"p": str(project_dir)}, "", 100)
		assert results == []


# ---------------------------------------------------------------------------
# _ship_records
# ---------------------------------------------------------------------------


class TestShipRecords:
	"""Tests for record shipping via the cloud records endpoint."""

	def test_no_projects_returns_zero(self, tmp_path):
		"""No projects configured returns 0 shipped."""
		cfg = replace(make_config(tmp_path), projects={})
		state = _ShipperState()
		shipped = asyncio.run(
			_ship_records("https://api.test", "tok", cfg, state)
		)
		assert shipped == 0

	def test_ships_records(self, tmp_path):
		"""Context records are shipped and watermark advanced."""
		project_dir = tmp_path / "proj"
		project_dir.mkdir()

		cfg = replace(make_config(tmp_path), projects={"proj": str(project_dir)})
		_insert_context_record(cfg.context_db_path, project_dir, "mem-1")
		state = _ShipperState()

		captured = []

		async def mock_post(endpoint, path, token, payload):
			"""Capture payload."""
			captured.append(payload)
			return True

		with patch("lerim.cloud.shipper._post_batch", side_effect=mock_post):
			shipped = asyncio.run(
				_ship_records("https://api.test", "tok", cfg, state)
			)

		assert shipped == 1
		assert len(captured) == 1
		assert "records" in captured[0]
		assert captured[0]["records"][0]["record_id"] == "mem-1"
		assert captured[0]["records"][0]["created_at"]
		assert captured[0]["records"][0]["valid_from"]
		assert "valid_until" in captured[0]["records"][0]


# ---------------------------------------------------------------------------
# _ship_service_runs
# ---------------------------------------------------------------------------


class TestShipServiceRuns:
	"""Tests for service run shipping."""

	def test_ships_runs(self, tmp_path):
		"""Service runs are shipped and watermark advanced."""
		db_path = tmp_path / "sessions.sqlite3"
		_create_service_runs_table(db_path)

		conn = sqlite3.connect(db_path)
		conn.execute(
			"INSERT INTO service_runs (job_type, status, started_at, completed_at, trigger, details_json) "
			"VALUES (?, ?, ?, ?, ?, ?)",
			("sync", "done", "2026-03-20T01:00:00Z", "2026-03-20T01:05:00Z", "daemon", '{"count": 1}'),
		)
		conn.commit()
		conn.close()

		state = _ShipperState()

		async def mock_post(*args, **kwargs):
			"""Always succeed."""
			return True

		with patch("lerim.cloud.shipper._post_batch", side_effect=mock_post):
			shipped = asyncio.run(
				_ship_service_runs("https://api.test", "tok", state, db_path)
			)

		assert shipped == 1
		assert state.service_runs_shipped_at == "2026-03-20T01:00:00Z"

	def test_no_runs_returns_zero(self, tmp_path):
		"""Empty service_runs table ships nothing."""
		db_path = tmp_path / "sessions.sqlite3"
		_create_service_runs_table(db_path)
		state = _ShipperState()

		with patch("lerim.cloud.shipper._post_batch", new_callable=AsyncMock):
			shipped = asyncio.run(
				_ship_service_runs("https://api.test", "tok", state, db_path)
			)

		assert shipped == 0


# ---------------------------------------------------------------------------
# _query_service_runs
# ---------------------------------------------------------------------------


class TestQueryServiceRuns:
	"""Tests for service run query helper."""

	def test_missing_db(self, tmp_path):
		"""Missing DB returns empty list (catches sqlite3.Error)."""
		# _query_service_runs opens the db even if missing (sqlite creates it),
		# but the table won't exist -> OperationalError -> empty list.
		result = _query_service_runs(tmp_path / "missing.db", "", 10)
		assert result == []

	def test_parses_details_json(self, tmp_path):
		"""details_json string is parsed to dict."""
		db_path = tmp_path / "sessions.sqlite3"
		_create_service_runs_table(db_path)

		conn = sqlite3.connect(db_path)
		conn.execute(
			"INSERT INTO service_runs (job_type, status, started_at, details_json) "
			"VALUES (?, ?, ?, ?)",
			("sync", "done", "2026-03-20T00:00:00Z", '{"sessions": 3}'),
		)
		conn.commit()
		conn.close()

		rows = _query_service_runs(db_path, "", 10)
		assert len(rows) == 1
		assert rows[0]["details_json"] == {"sessions": 3}


# ---------------------------------------------------------------------------
# _pull_records
# ---------------------------------------------------------------------------


class TestPullRecords:
	"""Tests for pulling cloud-edited records into the context DB."""

	def test_no_data_returns_zero(self, tmp_path):
		"""Empty response returns 0."""
		cfg = replace(make_config(tmp_path), projects={"proj": str(tmp_path / "proj")})
		state = _ShipperState()

		async def mock_get(*args, **kwargs):
			"""Return None."""
			return None

		with patch("lerim.cloud.shipper.asyncio.to_thread", side_effect=mock_get):
			pulled = asyncio.run(
				_pull_records("https://api.test", "tok", cfg, state)
			)

		assert pulled == 0

	def test_pulls_and_upserts_record(self, tmp_path):
		"""Cloud records are written into the canonical context DB."""
		proj_dir = tmp_path / "proj"
		proj_dir.mkdir()
		cfg = replace(make_config(tmp_path), projects={"proj": str(proj_dir)})
		state = _ShipperState()

		cloud_data = {
			"records": [
				{
					"record_id": "cloud-mem-1",
					"record_kind": "fact",
					"title": "Cloud Record",
					"description": "From dashboard",
					"body": "Edited body text",
					"cloud_edited_at": "2026-04-01T12:00:00Z",
					"project": "proj",
				}
			]
		}

		async def mock_to_thread(fn, *args, **kwargs):
			"""Return cloud data."""
			return cloud_data

		with patch("lerim.cloud.shipper.asyncio.to_thread", side_effect=mock_to_thread):
			pulled = asyncio.run(
				_pull_records("https://api.test", "tok", cfg, state)
			)

		assert pulled == 1
		assert state.records_pulled_at == "2026-04-01T12:00:00Z"
		store = ContextStore(cfg.context_db_path)
		identity = resolve_project_identity(proj_dir)
		with store.connect() as conn:
			row = conn.execute(
				"SELECT title, body FROM records WHERE record_id = ? AND project_id = ?",
				("cloud-mem-1", identity.project_id),
			).fetchone()
		assert row is not None
		assert row["title"] == "Cloud Record"
		assert row["body"] == "Edited body text"

	def test_pull_update_preserves_existing_valid_from(self, tmp_path):
		"""Cloud edits update content without rewriting the validity start."""
		proj_dir = tmp_path / "proj"
		proj_dir.mkdir()
		cfg = replace(make_config(tmp_path), projects={"proj": str(proj_dir)})
		state = _ShipperState()
		store = ContextStore(cfg.context_db_path)
		store.initialize()
		identity = resolve_project_identity(proj_dir)
		store.register_project(identity)
		store.create_record(
			project_id=identity.project_id,
			session_id=None,
			record_id="cloud-existing-validity",
			kind="fact",
			title="Original title",
			body="Original body",
			updated_at="2026-03-02T00:00:00Z",
			valid_from="2026-03-01T00:00:00Z",
		)
		with store.connect() as conn:
			original_created_at = conn.execute(
				"SELECT created_at FROM records WHERE record_id = ?",
				("cloud-existing-validity",),
			).fetchone()["created_at"]

		cloud_data = {
			"records": [
				{
					"record_id": "cloud-existing-validity",
					"record_kind": "fact",
					"title": "Updated title",
					"body": "Updated body",
					"status": "active",
					"project": "proj",
					"cloud_edited_at": "2026-04-01T12:00:00Z",
				}
			]
		}

		async def mock_to_thread(fn, *args, **kwargs):
			return cloud_data

		with patch("lerim.cloud.shipper.asyncio.to_thread", side_effect=mock_to_thread):
			pulled = asyncio.run(
				_pull_records("https://api.test", "tok", cfg, state)
			)

		assert pulled == 1
		with store.connect() as conn:
			row = conn.execute(
				"SELECT title, created_at, updated_at, valid_from FROM records WHERE record_id = ?",
				("cloud-existing-validity",),
			).fetchone()
		assert row["title"] == "Updated title"
		assert row["created_at"] == original_created_at
		assert row["updated_at"].replace("+00:00", "Z") == "2026-04-01T12:00:00Z"
		assert row["valid_from"].replace("+00:00", "Z") == "2026-03-01T00:00:00Z"

	def test_skips_missing_record_id(self, tmp_path):
		"""Records without record_id are skipped."""
		proj_dir = tmp_path / "proj"
		proj_dir.mkdir()
		cfg = replace(make_config(tmp_path), projects={"proj": str(proj_dir)})
		state = _ShipperState()

		cloud_data = {
			"records": [
				{
					"record_id": "",
					"cloud_edited_at": "2026-04-01T12:00:00Z",
					"project": "proj",
				}
			]
		}

		async def mock_to_thread(fn, *args, **kwargs):
			"""Return cloud data with empty record_id."""
			return cloud_data

		with patch("lerim.cloud.shipper.asyncio.to_thread", side_effect=mock_to_thread):
			pulled = asyncio.run(
				_pull_records("https://api.test", "tok", cfg, state)
			)

		assert pulled == 0

	def test_skips_unresolved_project_name(self, tmp_path):
		"""Unknown cloud project names do not fall back to another local project."""
		proj_dir = tmp_path / "alpha"
		proj_dir.mkdir()
		cfg = replace(make_config(tmp_path), projects={"alpha": str(proj_dir)})
		state = _ShipperState()

		cloud_data = {
			"records": [
				{
					"record_id": "cloud-unknown-project",
					"title": "Should be skipped",
					"body": "Unknown project should not be remapped.",
					"cloud_edited_at": "2026-04-01T12:00:00Z",
					"project": "beta",
				}
			]
		}

		async def mock_to_thread(fn, *args, **kwargs):
			return cloud_data

		with patch("lerim.cloud.shipper.asyncio.to_thread", side_effect=mock_to_thread):
			pulled = asyncio.run(
				_pull_records("https://api.test", "tok", cfg, state)
			)

		assert pulled == 0
		store = ContextStore(cfg.context_db_path)
		store.initialize()
		with store.connect() as conn:
			row = conn.execute(
				"SELECT 1 FROM records WHERE record_id = ?",
				("cloud-unknown-project",),
			).fetchone()
		assert row is None

	def test_skip_unknown_project_advances_watermark(self, tmp_path):
		"""Unknown-project rows are permanent skips, so the pull watermark moves."""
		alpha_dir = tmp_path / "alpha"
		alpha_dir.mkdir()
		cfg = replace(make_config(tmp_path), projects={"alpha": str(alpha_dir)})
		state = _ShipperState(records_pulled_at="2026-03-01T00:00:00Z")

		cloud_data = {
			"records": [
				{
					"record_id": "cloud-unknown-project",
					"title": "Should be skipped",
					"body": "Unknown project should not be remapped.",
					"cloud_edited_at": "2026-04-01T12:00:00Z",
					"project": "beta",
				}
			]
		}

		async def mock_to_thread(fn, *args, **kwargs):
			return cloud_data

		with patch("lerim.cloud.shipper.asyncio.to_thread", side_effect=mock_to_thread):
			pulled = asyncio.run(
				_pull_records("https://api.test", "tok", cfg, state)
			)

		assert pulled == 0
		assert state.records_pulled_at == "2026-04-01T12:00:00Z"


# ---------------------------------------------------------------------------
# ship_once
# ---------------------------------------------------------------------------


class TestShipOnce:
	"""Tests for the main ship_once entry point."""

	def test_skip_when_not_configured(self, tmp_path):
		"""Returns empty dict when cloud is not configured."""
		cfg = make_config(tmp_path)
		assert cfg.cloud_token is None
		result = asyncio.run(ship_once(cfg))
		assert result == {}

	def test_runs_all_phases(self, tmp_path, monkeypatch):
		"""ship_once runs pull + push phases when cloud is configured."""
		(tmp_path / "index").mkdir(exist_ok=True)
		db_path = tmp_path / "index" / "sessions.sqlite3"
		_create_sessions_table(db_path)

		cfg = replace(
			make_config(tmp_path),
			cloud_token="tok-test",
			cloud_endpoint="https://api.test",
			projects={},
			sessions_db_path=db_path,
		)

		state_path = tmp_path / "state.json"
		monkeypatch.setattr("lerim.cloud.shipper._STATE_PATH", state_path)
		monkeypatch.setattr("lerim.cloud.shipper.LOG_DIR", tmp_path)

		# Also create session_jobs table (needed by _ship_job_statuses)
		conn = sqlite3.connect(db_path)
		conn.execute(
			"""
			CREATE TABLE IF NOT EXISTS session_jobs (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				run_id TEXT NOT NULL,
				job_type TEXT NOT NULL DEFAULT 'extract',
				status TEXT NOT NULL,
				attempts INTEGER DEFAULT 0,
				error TEXT,
				available_at TEXT NOT NULL,
				created_at TEXT NOT NULL,
				updated_at TEXT NOT NULL,
				UNIQUE(run_id, job_type)
			)
			"""
		)
		conn.commit()
		conn.close()

		# Mock _pull_records to avoid real HTTP
		async def mock_pull(*args, **kwargs):
			"""No-op pull."""
			return 0

		monkeypatch.setattr("lerim.cloud.shipper._pull_records", mock_pull)

		result = asyncio.run(ship_once(cfg))

		assert isinstance(result, dict)
		assert "logs" in result
		assert "sessions" in result
		assert "records" in result
		assert "records_pulled" in result
		assert result["records_pulled"] == 0

	def test_serialization_with_sessions(self, tmp_path, monkeypatch):
		"""Session rows are serialized correctly in POST payload."""
		(tmp_path / "index").mkdir(exist_ok=True)
		db_path = tmp_path / "index" / "sessions.sqlite3"
		_create_sessions_table(db_path)
		_insert_session(db_path, "s-test", "2026-04-01T00:00:00Z")

		cfg = replace(
			make_config(tmp_path),
			cloud_token="tok-test",
			cloud_endpoint="https://api.test",
			projects={},
			sessions_db_path=db_path,
		)

		# Create session_jobs table
		conn = sqlite3.connect(db_path)
		conn.execute(
			"""
			CREATE TABLE IF NOT EXISTS session_jobs (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				run_id TEXT NOT NULL,
				job_type TEXT NOT NULL DEFAULT 'extract',
				status TEXT NOT NULL,
				attempts INTEGER DEFAULT 0,
				error TEXT,
				available_at TEXT NOT NULL,
				created_at TEXT NOT NULL,
				updated_at TEXT NOT NULL,
				UNIQUE(run_id, job_type)
			)
			"""
		)
		conn.commit()
		conn.close()

		state_path = tmp_path / "state.json"
		monkeypatch.setattr("lerim.cloud.shipper._STATE_PATH", state_path)
		monkeypatch.setattr("lerim.cloud.shipper.LOG_DIR", tmp_path)

		async def mock_pull(*args, **kwargs):
			"""No-op pull."""
			return 0

		monkeypatch.setattr("lerim.cloud.shipper._pull_records", mock_pull)

		captured = []

		async def mock_post(endpoint, path, token, payload):
			"""Capture payloads."""
			captured.append({"path": path, "payload": payload})
			return True

		with patch("lerim.cloud.shipper._post_batch", side_effect=mock_post):
			result = asyncio.run(ship_once(cfg))

		assert result["sessions"] == 1
		session_posts = [c for c in captured if "sessions" in c["path"]]
		assert len(session_posts) == 1
