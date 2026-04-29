"""Unit tests for the OpenCode session adapter using temporary SQLite databases."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from lerim.adapters.base import ViewerMessage, ViewerSession
from lerim.adapters.opencode import (
	_export_session_jsonl,
	_json_col,
	_read_session_db,
	_resolve_db_path,
	compact_trace,
	count_sessions,
	default_path,
	iter_sessions,
	validate_connection,
)


def _make_opencode_db(db_path: Path) -> None:
	"""Create a minimal OpenCode SQLite DB with one session, two messages, and parts."""
	conn = sqlite3.connect(db_path)
	conn.execute("""CREATE TABLE session (
		id TEXT PRIMARY KEY,
		directory TEXT,
		version TEXT,
		title TEXT,
		time_created INTEGER
	)""")
	conn.execute("""CREATE TABLE message (
		id TEXT PRIMARY KEY,
		session_id TEXT,
		data TEXT,
		time_created INTEGER
	)""")
	conn.execute("""CREATE TABLE part (
		id TEXT PRIMARY KEY,
		message_id TEXT,
		data TEXT,
		time_created INTEGER
	)""")
	# Insert a session
	conn.execute(
		"INSERT INTO session VALUES (?, ?, ?, ?, ?)",
		("sess-1", "/tmp/project", "1.0", "Test Session", 1708000000000),
	)
	# Insert messages
	conn.execute(
		"INSERT INTO message VALUES (?, ?, ?, ?)",
		(
			"msg-1",
			"sess-1",
			json.dumps({
				"role": "user",
				"tokens": {"input": 10, "output": 0},
				"time": {"created": 1708000001000},
			}),
			1708000001000,
		),
	)
	conn.execute(
		"INSERT INTO message VALUES (?, ?, ?, ?)",
		(
			"msg-2",
			"sess-1",
			json.dumps({
				"role": "assistant",
				"tokens": {"input": 0, "output": 50, "reasoning": 20},
				"modelID": "gpt-4",
				"time": {"created": 1708000002000},
			}),
			1708000002000,
		),
	)
	# Insert parts
	conn.execute(
		"INSERT INTO part VALUES (?, ?, ?, ?)",
		(
			"part-1",
			"msg-1",
			json.dumps({"type": "text", "text": "User question here"}),
			1708000001000,
		),
	)
	conn.execute(
		"INSERT INTO part VALUES (?, ?, ?, ?)",
		(
			"part-2",
			"msg-2",
			json.dumps({"type": "text", "text": "Assistant answer here"}),
			1708000002000,
		),
	)
	conn.commit()
	conn.close()


def _make_opencode_db_with_tools(db_path: Path) -> None:
	"""Create an OpenCode DB with a tool part to test tool message extraction."""
	_make_opencode_db(db_path)
	conn = sqlite3.connect(db_path)
	# Add a message with a tool part
	conn.execute(
		"INSERT INTO message VALUES (?, ?, ?, ?)",
		(
			"msg-3",
			"sess-1",
			json.dumps({
				"role": "assistant",
				"tokens": {"input": 5, "output": 10},
				"time": {"created": 1708000003000},
			}),
			1708000003000,
		),
	)
	conn.execute(
		"INSERT INTO part VALUES (?, ?, ?, ?)",
		(
			"part-3",
			"msg-3",
			json.dumps({
				"type": "tool",
				"tool": "bash",
				"state": {
					"input": "ls -la",
					"output": "file1\nfile2",
					"time": {"start": 1708000003500},
				},
			}),
			1708000003000,
		),
	)
	conn.commit()
	conn.close()


def _make_multi_session_db(db_path: Path) -> None:
	"""Create an OpenCode DB with multiple sessions for iteration tests."""
	conn = sqlite3.connect(db_path)
	conn.execute("""CREATE TABLE session (
		id TEXT PRIMARY KEY,
		directory TEXT,
		version TEXT,
		title TEXT,
		time_created INTEGER
	)""")
	conn.execute("""CREATE TABLE message (
		id TEXT PRIMARY KEY,
		session_id TEXT,
		data TEXT,
		time_created INTEGER
	)""")
	conn.execute("""CREATE TABLE part (
		id TEXT PRIMARY KEY,
		message_id TEXT,
		data TEXT,
		time_created INTEGER
	)""")
	# Session 1 (earlier)
	conn.execute(
		"INSERT INTO session VALUES (?, ?, ?, ?, ?)",
		("s1", "/project-a", "1.0", "First Session", 1708000000000),
	)
	conn.execute(
		"INSERT INTO message VALUES (?, ?, ?, ?)",
		("m1", "s1", json.dumps({"role": "user", "tokens": {}, "time": {}}), 1708000001000),
	)
	conn.execute(
		"INSERT INTO part VALUES (?, ?, ?, ?)",
		("p1", "m1", json.dumps({"type": "text", "text": "Question A"}), 1708000001000),
	)
	conn.execute(
		"INSERT INTO message VALUES (?, ?, ?, ?)",
		("m2", "s1", json.dumps({"role": "assistant", "tokens": {}, "time": {}}), 1708000002000),
	)
	conn.execute(
		"INSERT INTO part VALUES (?, ?, ?, ?)",
		("p2", "m2", json.dumps({"type": "text", "text": "Answer A"}), 1708000002000),
	)
	# Session 2 (later)
	conn.execute(
		"INSERT INTO session VALUES (?, ?, ?, ?, ?)",
		("s2", "/project-b", "1.0", "Second Session", 1708001000000),
	)
	conn.execute(
		"INSERT INTO message VALUES (?, ?, ?, ?)",
		("m3", "s2", json.dumps({"role": "user", "tokens": {}, "time": {}}), 1708001001000),
	)
	conn.execute(
		"INSERT INTO part VALUES (?, ?, ?, ?)",
		("p3", "m3", json.dumps({"type": "text", "text": "Question B"}), 1708001001000),
	)
	conn.commit()
	conn.close()


# ---------------------------------------------------------------------------
# _json_col tests
# ---------------------------------------------------------------------------


def test_json_col_valid_dict():
	"""Valid JSON dict is parsed correctly."""
	assert _json_col('{"key": "value"}') == {"key": "value"}


def test_json_col_returns_empty_on_none():
	"""None input returns empty dict."""
	assert _json_col(None) == {}


def test_json_col_returns_empty_on_empty_string():
	"""Empty string returns empty dict."""
	assert _json_col("") == {}


def test_json_col_returns_empty_on_invalid_json():
	"""Invalid JSON returns empty dict."""
	assert _json_col("not json {{{") == {}


def test_json_col_returns_empty_on_non_dict():
	"""JSON that parses to non-dict returns empty dict."""
	assert _json_col("[1, 2, 3]") == {}
	assert _json_col('"just a string"') == {}
	assert _json_col("42") == {}


# ---------------------------------------------------------------------------
# _resolve_db_path tests
# ---------------------------------------------------------------------------


def test_resolve_db_path_direct_file(tmp_path):
	"""Direct opencode.db file path is returned."""
	db = tmp_path / "opencode.db"
	db.touch()
	assert _resolve_db_path(db) == db


def test_resolve_db_path_in_directory(tmp_path):
	"""opencode.db is found inside a directory."""
	db = tmp_path / "opencode.db"
	db.touch()
	assert _resolve_db_path(tmp_path) == db


def test_resolve_db_path_not_found(tmp_path):
	"""Returns None when no opencode.db exists."""
	assert _resolve_db_path(tmp_path) is None


def test_resolve_db_path_wrong_filename(tmp_path):
	"""Returns None when a direct file has wrong name."""
	other = tmp_path / "other.db"
	other.touch()
	assert _resolve_db_path(other) is None


# ---------------------------------------------------------------------------
# default_path test
# ---------------------------------------------------------------------------


def test_default_path_returns_path():
	"""default_path returns a Path object."""
	result = default_path()
	assert result is not None
	assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# validate_connection tests
# ---------------------------------------------------------------------------


def test_validate_connection_valid(tmp_path):
	"""validate_connection passes on well-formed DB."""
	db_path = tmp_path / "opencode.db"
	_make_opencode_db(db_path)
	result = validate_connection(tmp_path)
	assert result["ok"] is True
	assert result["sessions"] == 1
	assert result["messages"] == 2


def test_validate_connection_missing_db(tmp_path):
	"""validate_connection fails on missing DB."""
	result = validate_connection(tmp_path)
	assert result["ok"] is False
	assert "error" in result


def test_validate_connection_missing_table(tmp_path):
	"""validate_connection fails when a required table is absent."""
	db_path = tmp_path / "opencode.db"
	conn = sqlite3.connect(db_path)
	conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY)")
	conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY)")
	# No 'part' table
	conn.commit()
	conn.close()
	result = validate_connection(tmp_path)
	assert result["ok"] is False
	assert "part" in result["error"]


def test_validate_connection_corrupt_db(tmp_path):
	"""validate_connection handles corrupt DB file."""
	db_path = tmp_path / "opencode.db"
	db_path.write_bytes(b"this is not a sqlite database at all")
	result = validate_connection(tmp_path)
	assert result["ok"] is False
	assert "error" in result


# ---------------------------------------------------------------------------
# count_sessions tests
# ---------------------------------------------------------------------------


def test_count_sessions_basic(tmp_path):
	"""count_sessions on mock DB returns correct count."""
	db_path = tmp_path / "opencode.db"
	_make_opencode_db(db_path)
	assert count_sessions(tmp_path) == 1


def test_count_sessions_nonexistent_path(tmp_path):
	"""Nonexistent path returns 0."""
	assert count_sessions(tmp_path / "nope") == 0


def test_count_sessions_no_db(tmp_path):
	"""Directory without opencode.db returns 0."""
	assert count_sessions(tmp_path) == 0


def test_count_sessions_multiple(tmp_path):
	"""count_sessions reports correct count for multiple sessions."""
	db_path = tmp_path / "opencode.db"
	_make_multi_session_db(db_path)
	assert count_sessions(tmp_path) == 2


# ---------------------------------------------------------------------------
# _read_session_db tests
# ---------------------------------------------------------------------------


def test_read_session_db_basic(tmp_path):
	"""_read_session_db returns a ViewerSession with correct structure."""
	db_path = tmp_path / "opencode.db"
	_make_opencode_db(db_path)
	session = _read_session_db(db_path, "sess-1")
	assert session is not None
	assert session.session_id == "sess-1"
	assert session.cwd == "/tmp/project"
	assert session.meta["version"] == "1.0"
	assert session.meta["title"] == "Test Session"
	user_msgs = [m for m in session.messages if m.role == "user"]
	asst_msgs = [m for m in session.messages if m.role == "assistant"]
	assert len(user_msgs) >= 1
	assert len(asst_msgs) >= 1
	assert "User question" in user_msgs[0].content


def test_read_session_db_with_tools(tmp_path):
	"""_read_session_db extracts tool parts as tool messages."""
	db_path = tmp_path / "opencode.db"
	_make_opencode_db_with_tools(db_path)
	session = _read_session_db(db_path, "sess-1")
	assert session is not None
	tool_msgs = [m for m in session.messages if m.role == "tool"]
	assert len(tool_msgs) == 1
	assert tool_msgs[0].tool_name == "bash"
	assert tool_msgs[0].tool_input == "ls -la"
	assert tool_msgs[0].tool_output == "file1\nfile2"


def test_read_session_db_token_counting(tmp_path):
	"""_read_session_db accumulates token counts including reasoning tokens."""
	db_path = tmp_path / "opencode.db"
	_make_opencode_db(db_path)
	session = _read_session_db(db_path, "sess-1")
	assert session is not None
	assert session.total_input_tokens == 10
	# output (50) + reasoning (20) = 70
	assert session.total_output_tokens == 70


def test_read_session_db_model_id(tmp_path):
	"""_read_session_db attaches model to assistant messages."""
	db_path = tmp_path / "opencode.db"
	_make_opencode_db(db_path)
	session = _read_session_db(db_path, "sess-1")
	assert session is not None
	asst_msgs = [m for m in session.messages if m.role == "assistant"]
	assert asst_msgs[0].model == "gpt-4"


def test_read_session_db_nonexistent_session(tmp_path):
	"""_read_session_db returns None for a session ID not in the DB."""
	db_path = tmp_path / "opencode.db"
	_make_opencode_db(db_path)
	assert _read_session_db(db_path, "nonexistent") is None


def test_read_session_db_timestamps(tmp_path):
	"""_read_session_db attaches ISO timestamps to messages."""
	db_path = tmp_path / "opencode.db"
	_make_opencode_db(db_path)
	session = _read_session_db(db_path, "sess-1")
	assert session is not None
	for msg in session.messages:
		if msg.role in {"user", "assistant"}:
			assert msg.timestamp is not None
			assert "2024" in msg.timestamp  # epoch 1708000001000 -> Feb 2024


def test_read_session_db_empty_text_parts_skipped(tmp_path):
	"""_read_session_db skips text parts with empty or whitespace content."""
	db_path = tmp_path / "opencode.db"
	conn = sqlite3.connect(db_path)
	conn.execute("""CREATE TABLE session (
		id TEXT PRIMARY KEY, directory TEXT, version TEXT, title TEXT, time_created INTEGER
	)""")
	conn.execute("""CREATE TABLE message (
		id TEXT PRIMARY KEY, session_id TEXT, data TEXT, time_created INTEGER
	)""")
	conn.execute("""CREATE TABLE part (
		id TEXT PRIMARY KEY, message_id TEXT, data TEXT, time_created INTEGER
	)""")
	conn.execute(
		"INSERT INTO session VALUES (?, ?, ?, ?, ?)",
		("s", "/d", "1.0", "T", 1708000000000),
	)
	conn.execute(
		"INSERT INTO message VALUES (?, ?, ?, ?)",
		("m", "s", json.dumps({"role": "user", "tokens": {}, "time": {}}), 1708000001000),
	)
	conn.execute(
		"INSERT INTO part VALUES (?, ?, ?, ?)",
		("p1", "m", json.dumps({"type": "text", "text": ""}), 1708000001000),
	)
	conn.execute(
		"INSERT INTO part VALUES (?, ?, ?, ?)",
		("p2", "m", json.dumps({"type": "text", "text": "   "}), 1708000001000),
	)
	conn.execute(
		"INSERT INTO part VALUES (?, ?, ?, ?)",
		("p3", "m", json.dumps({"type": "text", "text": "real content"}), 1708000001000),
	)
	conn.commit()
	conn.close()
	session = _read_session_db(db_path, "s")
	assert session is not None
	assert len(session.messages) == 1
	assert session.messages[0].content == "real content"


def test_read_session_db_message_with_no_text_parts(tmp_path):
	"""_read_session_db skips messages where all parts are non-text or empty."""
	db_path = tmp_path / "opencode.db"
	conn = sqlite3.connect(db_path)
	conn.execute("""CREATE TABLE session (
		id TEXT PRIMARY KEY, directory TEXT, version TEXT, title TEXT, time_created INTEGER
	)""")
	conn.execute("""CREATE TABLE message (
		id TEXT PRIMARY KEY, session_id TEXT, data TEXT, time_created INTEGER
	)""")
	conn.execute("""CREATE TABLE part (
		id TEXT PRIMARY KEY, message_id TEXT, data TEXT, time_created INTEGER
	)""")
	conn.execute(
		"INSERT INTO session VALUES (?, ?, ?, ?, ?)",
		("s", "/d", "1.0", "T", 1708000000000),
	)
	conn.execute(
		"INSERT INTO message VALUES (?, ?, ?, ?)",
		("m1", "s", json.dumps({"role": "assistant", "tokens": {}, "time": {}}), 1708000001000),
	)
	# Only a tool part, no text part -- so no text-role message for this msg
	conn.execute(
		"INSERT INTO part VALUES (?, ?, ?, ?)",
		(
			"p1", "m1",
			json.dumps({"type": "tool", "tool": "grep", "state": {"input": "q", "output": "r"}}),
			1708000001000,
		),
	)
	conn.commit()
	conn.close()
	session = _read_session_db(db_path, "s")
	assert session is not None
	# Should have the tool message but no text message
	assert len(session.messages) == 1
	assert session.messages[0].role == "tool"


def test_read_session_db_default_role(tmp_path):
	"""_read_session_db defaults to 'assistant' when role is missing from message data."""
	db_path = tmp_path / "opencode.db"
	conn = sqlite3.connect(db_path)
	conn.execute("""CREATE TABLE session (
		id TEXT PRIMARY KEY, directory TEXT, version TEXT, title TEXT, time_created INTEGER
	)""")
	conn.execute("""CREATE TABLE message (
		id TEXT PRIMARY KEY, session_id TEXT, data TEXT, time_created INTEGER
	)""")
	conn.execute("""CREATE TABLE part (
		id TEXT PRIMARY KEY, message_id TEXT, data TEXT, time_created INTEGER
	)""")
	conn.execute(
		"INSERT INTO session VALUES (?, ?, ?, ?, ?)",
		("s", "/d", "1.0", "T", 1708000000000),
	)
	conn.execute(
		"INSERT INTO message VALUES (?, ?, ?, ?)",
		("m", "s", json.dumps({"tokens": {}, "time": {}}), 1708000001000),
	)
	conn.execute(
		"INSERT INTO part VALUES (?, ?, ?, ?)",
		("p", "m", json.dumps({"type": "text", "text": "no role set"}), 1708000001000),
	)
	conn.commit()
	conn.close()
	session = _read_session_db(db_path, "s")
	assert session is not None
	assert session.messages[0].role == "assistant"


# ---------------------------------------------------------------------------
# _export_session_jsonl + roundtrip tests
# ---------------------------------------------------------------------------


def test_jsonl_export_roundtrip(tmp_path):
	"""Export ViewerSession to JSONL and verify canonical line schema."""
	session = ViewerSession(
		session_id="roundtrip-test",
		cwd="/tmp",
		messages=[
			ViewerMessage(role="user", content="Hello"),
			ViewerMessage(role="assistant", content="World", model="gpt-4"),
			ViewerMessage(role="tool", tool_name="bash", tool_input="ls", tool_output="files", timestamp="2024-01-01T00:00:00"),
		],
		total_input_tokens=100,
		total_output_tokens=200,
		meta={"version": "2.0"},
	)
	jsonl_path = _export_session_jsonl(session, tmp_path)
	assert jsonl_path.is_file()

	# Verify JSONL lines are canonical format (no metadata line)
	raw_lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
	for line in raw_lines:
		obj = json.loads(line)
		assert "type" in obj
		assert "message" in obj
		assert "timestamp" in obj

	user_count = 0
	assistant_count = 0
	tool_result_entries = 0
	for line in raw_lines:
		obj = json.loads(line)
		assert obj["type"] in ("user", "assistant")
		msg = obj["message"]
		assert msg["role"] in ("user", "assistant")
		content = msg["content"]
		if isinstance(content, str):
			if msg["role"] == "user":
				user_count += 1
			else:
				assistant_count += 1
		elif isinstance(content, list):
			for block in content:
				if block.get("type") == "tool_result":
					tool_result_entries += 1
					assert str(block.get("content", "")).startswith("[cleared:")

	assert user_count == 1
	assert assistant_count >= 1
	assert tool_result_entries == 1


def test_export_session_jsonl_empty_messages(tmp_path):
	"""Exporting a session with no messages produces an empty JSONL file."""
	session = ViewerSession(session_id="empty", cwd="/")
	jsonl_path = _export_session_jsonl(session, tmp_path)
	content = jsonl_path.read_text(encoding="utf-8").strip()
	assert content == ""


# ---------------------------------------------------------------------------
# iter_sessions tests
# ---------------------------------------------------------------------------


def test_iter_sessions_basic(tmp_path):
	"""iter_sessions exports sessions and returns SessionRecords."""
	db_path = tmp_path / "opencode.db"
	_make_opencode_db(db_path)
	cache_dir = tmp_path / "cache"
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir)
	assert len(records) == 1
	rec = records[0]
	assert rec.run_id == "sess-1"
	assert rec.agent_type == "opencode"
	assert rec.repo_path == "/tmp/project"
	assert rec.message_count >= 2
	assert Path(rec.session_path).is_file()


def test_iter_sessions_skips_known_ids(tmp_path):
	"""iter_sessions skips sessions whose run_id is already known."""
	db_path = tmp_path / "opencode.db"
	_make_opencode_db(db_path)
	cache_dir = tmp_path / "cache"
	records = iter_sessions(
		traces_dir=tmp_path,
		cache_dir=cache_dir,
		known_run_ids={"sess-1"},
	)
	assert len(records) == 0


def test_iter_sessions_nonexistent_dir(tmp_path):
	"""Nonexistent traces_dir returns empty list."""
	records = iter_sessions(traces_dir=tmp_path / "nope")
	assert records == []


def test_iter_sessions_no_db(tmp_path):
	"""Directory without opencode.db returns empty list."""
	records = iter_sessions(traces_dir=tmp_path)
	assert records == []


def test_iter_sessions_multiple_sessions(tmp_path):
	"""iter_sessions handles multiple sessions correctly."""
	db_path = tmp_path / "opencode.db"
	_make_multi_session_db(db_path)
	cache_dir = tmp_path / "cache"
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir)
	assert len(records) == 2
	ids = {r.run_id for r in records}
	assert ids == {"s1", "s2"}


def test_iter_sessions_summaries(tmp_path):
	"""iter_sessions collects message summaries (up to 5, truncated to 140 chars)."""
	db_path = tmp_path / "opencode.db"
	_make_opencode_db(db_path)
	cache_dir = tmp_path / "cache"
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir)
	assert len(records) == 1
	# Should have summaries from user and assistant messages
	assert len(records[0].summaries) >= 1
	assert any("User question" in s for s in records[0].summaries)


def test_iter_sessions_tool_call_count(tmp_path):
	"""iter_sessions counts tool messages separately."""
	db_path = tmp_path / "opencode.db"
	_make_opencode_db_with_tools(db_path)
	cache_dir = tmp_path / "cache"
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir)
	assert len(records) == 1
	assert records[0].tool_call_count >= 1


def test_iter_sessions_total_tokens(tmp_path):
	"""iter_sessions calculates total_tokens from input + output tokens."""
	db_path = tmp_path / "opencode.db"
	_make_opencode_db(db_path)
	cache_dir = tmp_path / "cache"
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir)
	assert len(records) == 1
	# 10 input + 50 output + 20 reasoning = 80
	assert records[0].total_tokens == 80


def test_iter_sessions_jsonl_cache_created(tmp_path):
	"""iter_sessions creates JSONL cache files."""
	db_path = tmp_path / "opencode.db"
	_make_opencode_db(db_path)
	cache_dir = tmp_path / "cache"
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir)
	for rec in records:
		p = Path(rec.session_path)
		assert p.is_file()
		assert p.suffix == ".jsonl"
		assert p.parent == cache_dir


def test_iter_sessions_partial_skip(tmp_path):
	"""iter_sessions skips only known IDs, keeping the rest."""
	db_path = tmp_path / "opencode.db"
	_make_multi_session_db(db_path)
	cache_dir = tmp_path / "cache"
	records = iter_sessions(
		traces_dir=tmp_path,
		cache_dir=cache_dir,
		known_run_ids={"s1"},
	)
	assert len(records) == 1
	assert records[0].run_id == "s2"


# --- compact_trace tests ---


def test_compact_trace_passes_canonical_entries():
	"""compact_trace passes through canonical user/assistant entries unchanged."""
	lines = [
		json.dumps({"type": "user", "message": {"role": "user", "content": "hello"}, "timestamp": "2024-02-15T12:00:00Z"}),
		json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "world"}, "timestamp": "2024-02-15T12:00:01Z"}),
	]
	result = compact_trace("\n".join(lines) + "\n")
	parsed = [json.loads(line) for line in result.strip().split("\n")]
	assert len(parsed) == 2
	assert parsed[0]["message"]["content"] == "hello"
	assert parsed[1]["message"]["content"] == "world"


def test_compact_trace_clears_tool_result_in_canonical():
	"""compact_trace clears uncleaned tool_result content in canonical entries."""
	entry = {
		"type": "assistant",
		"message": {
			"role": "assistant",
			"content": [
				{"type": "tool_use", "name": "bash", "input": "ls -la"},
				{"type": "tool_result", "content": "x" * 8000},
			],
		},
		"timestamp": "2024-02-15T12:00:00Z",
	}
	result = compact_trace(json.dumps(entry) + "\n")
	parsed = json.loads(result.strip())
	assert parsed["message"]["content"][0]["name"] == "bash"
	assert parsed["message"]["content"][1]["content"] == "[cleared: 8000 chars]"


def test_compact_trace_preserves_already_cleared():
	"""compact_trace preserves already-cleared tool_result descriptors."""
	entry = {
		"type": "assistant",
		"message": {
			"role": "assistant",
			"content": [
				{"type": "tool_use", "name": "bash", "input": "ls"},
				{"type": "tool_result", "content": "[cleared: 500 chars]"},
			],
		},
		"timestamp": "2024-02-15T12:00:00Z",
	}
	result = compact_trace(json.dumps(entry) + "\n")
	parsed = json.loads(result.strip())
	assert parsed["message"]["content"][1]["content"] == "[cleared: 500 chars]"


def test_compact_trace_drops_non_canonical_entries():
	"""compact_trace drops entries that don't match canonical schema."""
	lines = [
		json.dumps({"session_id": "s1", "cwd": "/tmp"}),
		json.dumps({"role": "user", "content": "old format"}),
		json.dumps({"type": "user", "message": {"role": "user", "content": "canonical"}, "timestamp": "2024-02-15T12:00:00Z"}),
	]
	result = compact_trace("\n".join(lines) + "\n")
	parsed = [json.loads(line) for line in result.strip().split("\n")]
	# Only the canonical entry survives
	assert len(parsed) == 1
	assert parsed[0]["message"]["content"] == "canonical"


# ---------------------------------------------------------------------------
# Time window filtering tests
# ---------------------------------------------------------------------------


def test_iter_sessions_time_window_filter(tmp_path):
	"""iter_sessions filters sessions outside the start/end time window."""
	from datetime import datetime, timezone

	db_path = tmp_path / "opencode.db"
	conn = sqlite3.connect(db_path)
	conn.execute("""CREATE TABLE session (
		id TEXT PRIMARY KEY, directory TEXT, version TEXT, title TEXT, time_created INTEGER
	)""")
	conn.execute("""CREATE TABLE message (
		id TEXT PRIMARY KEY, session_id TEXT, data TEXT, time_created INTEGER
	)""")
	conn.execute("""CREATE TABLE part (
		id TEXT PRIMARY KEY, message_id TEXT, data TEXT, time_created INTEGER
	)""")
	# Old session
	conn.execute(
		"INSERT INTO session VALUES (?, ?, ?, ?, ?)",
		("old", "/d", "1.0", "Old", 1600000000000),
	)
	conn.execute(
		"INSERT INTO message VALUES (?, ?, ?, ?)",
		("m-old", "old", json.dumps({"role": "user", "tokens": {}, "time": {}}), 1600000001000),
	)
	conn.execute(
		"INSERT INTO part VALUES (?, ?, ?, ?)",
		("p-old", "m-old", json.dumps({"type": "text", "text": "old msg"}), 1600000001000),
	)
	# New session
	conn.execute(
		"INSERT INTO session VALUES (?, ?, ?, ?, ?)",
		("new", "/d", "1.0", "New", 1800000000000),
	)
	conn.execute(
		"INSERT INTO message VALUES (?, ?, ?, ?)",
		("m-new", "new", json.dumps({"role": "user", "tokens": {}, "time": {}}), 1800000001000),
	)
	conn.execute(
		"INSERT INTO part VALUES (?, ?, ?, ?)",
		("p-new", "m-new", json.dumps({"type": "text", "text": "new msg"}), 1800000001000),
	)
	conn.commit()
	conn.close()

	cache_dir = tmp_path / "cache"
	start = datetime(2025, 1, 1, tzinfo=timezone.utc)
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir, start=start)
	assert len(records) == 1
	assert records[0].run_id == "new"


def test_iter_sessions_skips_session_when_read_returns_none(tmp_path, monkeypatch):
	"""iter_sessions skips sessions where _read_session_db returns None."""
	db_path = tmp_path / "opencode.db"
	_make_opencode_db(db_path)
	cache_dir = tmp_path / "cache"

	# Monkeypatch _read_session_db to return None for the session
	monkeypatch.setattr(
		"lerim.adapters.opencode._read_session_db", lambda *a, **kw: None
	)
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir)
	assert records == []


def test_iter_sessions_directory_as_repo_name(tmp_path):
	"""iter_sessions uses session directory for both repo_path and repo_name."""
	db_path = tmp_path / "opencode.db"
	_make_opencode_db(db_path)
	cache_dir = tmp_path / "cache"
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir)
	assert records[0].repo_path == "/tmp/project"
	assert records[0].repo_name == "/tmp/project"


def test_iter_sessions_summaries_capped_at_five(tmp_path):
	"""iter_sessions caps summaries at 5 entries."""
	db_path = tmp_path / "opencode.db"
	conn = sqlite3.connect(db_path)
	conn.execute("""CREATE TABLE session (
		id TEXT PRIMARY KEY, directory TEXT, version TEXT, title TEXT, time_created INTEGER
	)""")
	conn.execute("""CREATE TABLE message (
		id TEXT PRIMARY KEY, session_id TEXT, data TEXT, time_created INTEGER
	)""")
	conn.execute("""CREATE TABLE part (
		id TEXT PRIMARY KEY, message_id TEXT, data TEXT, time_created INTEGER
	)""")
	conn.execute(
		"INSERT INTO session VALUES (?, ?, ?, ?, ?)",
		("s", "/d", "1.0", "T", 1708000000000),
	)
	# Create 8 user+assistant message pairs (16 messages, each with a text part)
	for i in range(8):
		mid = f"m{i}"
		role = "user" if i % 2 == 0 else "assistant"
		conn.execute(
			"INSERT INTO message VALUES (?, ?, ?, ?)",
			(mid, "s", json.dumps({"role": role, "tokens": {}, "time": {}}), 1708000001000 + i * 1000),
		)
		conn.execute(
			"INSERT INTO part VALUES (?, ?, ?, ?)",
			(f"p{i}", mid, json.dumps({"type": "text", "text": f"msg {i}"}), 1708000001000 + i * 1000),
		)
	conn.commit()
	conn.close()

	cache_dir = tmp_path / "cache"
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir)
	assert len(records) == 1
	assert len(records[0].summaries) == 5


# ---------------------------------------------------------------------------
# SQLite error handling tests
# ---------------------------------------------------------------------------


def test_count_sessions_sqlite_error(tmp_path):
	"""count_sessions returns 0 on corrupt DB."""
	db_path = tmp_path / "opencode.db"
	db_path.write_bytes(b"corrupt data not a database")
	assert count_sessions(tmp_path) == 0


def test_read_session_db_sqlite_error(tmp_path):
	"""_read_session_db returns None on SQLite error."""
	db_path = tmp_path / "opencode.db"
	db_path.write_bytes(b"corrupt data")
	assert _read_session_db(db_path, "sid") is None


def test_iter_sessions_sqlite_error(tmp_path):
	"""iter_sessions returns empty list on DB query error."""
	db_path = tmp_path / "opencode.db"
	db_path.write_bytes(b"corrupt sqlite data")
	records = iter_sessions(traces_dir=tmp_path, cache_dir=tmp_path / "cache")
	assert records == []
