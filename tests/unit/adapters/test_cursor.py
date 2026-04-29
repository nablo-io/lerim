"""Unit tests for the Cursor adapter using temporary SQLite databases."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from lerim.adapters.cursor import (
	_extract_text,
	_normalize_role,
	_parse_json_value,
	_read_session_db,
	_resolve_db_paths,
	compact_trace,
	count_sessions,
	default_path,
	iter_sessions,
	validate_connection,
)


def _make_cursor_db(
	db_path: Path,
	composers: dict[str, dict],
	bubbles: list[tuple[str, str, dict]],
) -> None:
	"""Create a test Cursor DB with given composers and bubbles.

	composers: {composerId: composerData_json_dict}
	bubbles: [(composerId, bubbleId, bubble_json_dict), ...]
	"""
	conn = sqlite3.connect(db_path)
	conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
	for cid, data in composers.items():
		conn.execute(
			"INSERT INTO cursorDiskKV VALUES (?, ?)",
			(f"composerData:{cid}", json.dumps(data)),
		)
	for cid, bid, data in bubbles:
		conn.execute(
			"INSERT INTO cursorDiskKV VALUES (?, ?)",
			(f"bubbleId:{cid}:{bid}", json.dumps(data)),
		)
	conn.commit()
	conn.close()


# ---------------------------------------------------------------------------
# _parse_json_value tests
# ---------------------------------------------------------------------------


def test_parse_json_value_plain_dict():
	"""Normal JSON dict is parsed directly."""
	raw = json.dumps({"key": "value"})
	assert _parse_json_value(raw) == {"key": "value"}


def test_parse_json_value_double_encoded():
	"""Double-encoded JSON string is unwrapped to the inner object."""
	inner = {"nested": True}
	raw = json.dumps(json.dumps(inner))
	assert _parse_json_value(raw) == inner


def test_parse_json_value_plain_string():
	"""A JSON string that is not itself JSON returns the plain string."""
	raw = json.dumps("hello world")
	assert _parse_json_value(raw) == "hello world"


def test_parse_json_value_invalid_json():
	"""Invalid JSON returns None."""
	assert _parse_json_value("not json at all {{{") is None


def test_parse_json_value_integer():
	"""A JSON integer is returned as-is (not a string, no inner parse)."""
	raw = json.dumps(42)
	assert _parse_json_value(raw) == 42


def test_parse_json_value_list():
	"""A JSON list is returned as-is."""
	raw = json.dumps([1, 2, 3])
	assert _parse_json_value(raw) == [1, 2, 3]


# ---------------------------------------------------------------------------
# _extract_text tests
# ---------------------------------------------------------------------------


def test_extract_text_none():
	"""None input returns empty string."""
	assert _extract_text(None) == ""


def test_extract_text_plain_string():
	"""Plain string is returned unchanged."""
	assert _extract_text("hello") == "hello"


def test_extract_text_dict_with_text_key():
	"""Dict with 'text' key extracts recursively."""
	assert _extract_text({"text": "inner"}) == "inner"


def test_extract_text_dict_with_content_key():
	"""Dict with 'content' key extracts recursively."""
	assert _extract_text({"content": "body"}) == "body"


def test_extract_text_dict_with_message_key():
	"""Dict with 'message' key extracts recursively."""
	assert _extract_text({"message": "msg"}) == "msg"


def test_extract_text_dict_with_value_key():
	"""Dict with 'value' key extracts recursively."""
	assert _extract_text({"value": "val"}) == "val"


def test_extract_text_dict_nested():
	"""Nested dict resolves through multiple levels."""
	assert _extract_text({"text": {"content": "deep"}}) == "deep"


def test_extract_text_dict_no_known_key():
	"""Dict without known keys returns str() of the dict."""
	val = {"unknown": "x"}
	assert _extract_text(val) == str(val)


def test_extract_text_list():
	"""List of values joins non-empty extracted parts."""
	result = _extract_text(["hello", {"text": "world"}, None])
	assert result == "hello\nworld"


def test_extract_text_integer():
	"""Non-string, non-dict, non-list falls back to str()."""
	assert _extract_text(42) == "42"


# ---------------------------------------------------------------------------
# _normalize_role tests
# ---------------------------------------------------------------------------


def test_normalize_role_int_user():
	"""Integer 1 maps to 'user'."""
	assert _normalize_role(1) == "user"


def test_normalize_role_int_assistant():
	"""Integer 2 maps to 'assistant'."""
	assert _normalize_role(2) == "assistant"


def test_normalize_role_int_other():
	"""Integer other than 1 or 2 maps to 'tool'."""
	assert _normalize_role(3) == "tool"
	assert _normalize_role(0) == "tool"
	assert _normalize_role(99) == "tool"


def test_normalize_role_string_user_aliases():
	"""String user aliases map to 'user'."""
	for alias in ("user", "human", "human_user", "User", "HUMAN"):
		assert _normalize_role(alias) == "user"


def test_normalize_role_string_assistant_aliases():
	"""String assistant aliases map to 'assistant'."""
	for alias in ("assistant", "ai", "bot", "model", "Assistant", "AI"):
		assert _normalize_role(alias) == "assistant"


def test_normalize_role_string_tool_aliases():
	"""String tool aliases map to 'tool'."""
	for alias in ("tool", "function", "Tool"):
		assert _normalize_role(alias) == "tool"


def test_normalize_role_unknown_string():
	"""Unknown string defaults to 'assistant'."""
	assert _normalize_role("something_else") == "assistant"


def test_normalize_role_none():
	"""None defaults to 'assistant'."""
	assert _normalize_role(None) == "assistant"


# ---------------------------------------------------------------------------
# _resolve_db_paths tests
# ---------------------------------------------------------------------------


def test_resolve_db_paths_file(tmp_path):
	"""Direct file path returns a single-element list."""
	db = tmp_path / "state.vscdb"
	db.touch()
	assert _resolve_db_paths(db) == [db]


def test_resolve_db_paths_dir_with_state(tmp_path):
	"""Directory containing state.vscdb returns it."""
	db = tmp_path / "state.vscdb"
	db.touch()
	assert _resolve_db_paths(tmp_path) == [db]


def test_resolve_db_paths_nested(tmp_path):
	"""Glob finds state.vscdb in subdirectories."""
	sub = tmp_path / "subdir"
	sub.mkdir()
	db = sub / "state.vscdb"
	db.touch()
	result = _resolve_db_paths(tmp_path)
	assert db in result


def test_resolve_db_paths_empty(tmp_path):
	"""Empty directory returns empty list."""
	assert _resolve_db_paths(tmp_path) == []


# ---------------------------------------------------------------------------
# default_path test
# ---------------------------------------------------------------------------


def test_default_path_returns_path():
	"""default_path returns a Path object on all platforms."""
	result = default_path()
	assert result is not None
	assert isinstance(result, Path)


# ---------------------------------------------------------------------------
# validate_connection tests
# ---------------------------------------------------------------------------


def test_validate_connection_passes_on_valid_db():
	"""A valid DB with composerData and bubbleId rows passes validation."""
	with TemporaryDirectory() as tmp:
		db_path = Path(tmp) / "state.vscdb"
		_make_cursor_db(
			db_path,
			composers={"v": {"composerId": "v"}},
			bubbles=[
				("v", "1", {"type": 1, "text": "hi"}),
				("v", "2", {"type": 2, "text": "hello"}),
			],
		)
		result = validate_connection(Path(tmp))
		assert result["ok"] is True
		assert result["sessions"] == 1
		assert result["messages"] == 2


def test_validate_connection_fails_on_missing_table():
	"""A SQLite DB without cursorDiskKV table should fail validation."""
	with TemporaryDirectory() as tmp:
		db_path = Path(tmp) / "state.vscdb"
		conn = sqlite3.connect(db_path)
		conn.execute("CREATE TABLE other_table (id INTEGER)")
		conn.commit()
		conn.close()

		result = validate_connection(Path(tmp))
		assert result["ok"] is False
		assert "cursorDiskKV" in result["error"]


def test_validate_connection_warns_on_empty_conversations():
	"""DB with composerData but no bubbles reports 0 messages/sessions."""
	with TemporaryDirectory() as tmp:
		db_path = Path(tmp) / "state.vscdb"
		_make_cursor_db(
			db_path,
			composers={"empty": {"composerId": "empty"}},
			bubbles=[],
		)
		result = validate_connection(Path(tmp))
		assert result["ok"] is True
		assert result["messages"] == 0
		assert result["sessions"] == 0


def test_validate_connection_no_db_found(tmp_path):
	"""validate_connection reports error when no state.vscdb exists."""
	result = validate_connection(tmp_path)
	assert result["ok"] is False
	assert "No state.vscdb" in result["error"]


def test_validate_connection_multiple_composers(tmp_path):
	"""validate_connection counts distinct composerIds across many bubbles."""
	db_path = tmp_path / "state.vscdb"
	_make_cursor_db(
		db_path,
		composers={},
		bubbles=[
			("c1", "1", {"type": 1, "text": "a"}),
			("c1", "2", {"type": 2, "text": "b"}),
			("c2", "1", {"type": 1, "text": "c"}),
		],
	)
	result = validate_connection(tmp_path)
	assert result["ok"] is True
	assert result["sessions"] == 2
	assert result["messages"] == 3


def test_validate_connection_malformed_bubbleid_key(tmp_path):
	"""Bubble keys without enough ':' parts are ignored in the count."""
	db_path = tmp_path / "state.vscdb"
	conn = sqlite3.connect(db_path)
	conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
	conn.execute(
		"INSERT INTO cursorDiskKV VALUES (?, ?)",
		("bubbleId:onlyonepart", json.dumps({"type": 1})),
	)
	conn.commit()
	conn.close()
	result = validate_connection(tmp_path)
	assert result["ok"] is True
	assert result["sessions"] == 0
	assert result["messages"] == 0


# ---------------------------------------------------------------------------
# count_sessions tests
# ---------------------------------------------------------------------------


def test_count_sessions_counts_composers_with_messages():
	"""Only composers that have bubbleId rows are counted."""
	with TemporaryDirectory() as tmp:
		db_path = Path(tmp) / "state.vscdb"
		_make_cursor_db(
			db_path,
			composers={
				"a": {"composerId": "a"},
				"b": {"composerId": "b"},
				"c": {"composerId": "c"},
			},
			bubbles=[
				("a", "1", {"type": 1, "text": "hi"}),
				("b", "1", {"type": 2, "text": "hey"}),
			],
		)
		assert count_sessions(Path(tmp)) == 2


def test_count_sessions_nonexistent_path(tmp_path):
	"""Nonexistent path returns 0."""
	assert count_sessions(tmp_path / "nope") == 0


def test_count_sessions_empty_db(tmp_path):
	"""DB with no bubbles returns 0."""
	db_path = tmp_path / "state.vscdb"
	_make_cursor_db(db_path, composers={"a": {}}, bubbles=[])
	assert count_sessions(tmp_path) == 0


# ---------------------------------------------------------------------------
# iter_sessions tests
# ---------------------------------------------------------------------------


def test_iter_sessions_groups_bubbles_by_composer():
	"""Two composers with 3 and 2 bubbles produce 2 SessionRecords."""
	with TemporaryDirectory() as tmp:
		db_path = Path(tmp) / "state.vscdb"
		cache_dir = Path(tmp) / "cache"
		_make_cursor_db(
			db_path,
			composers={
				"aaa": {"composerId": "aaa", "createdAt": 1700000000000},
				"bbb": {"composerId": "bbb", "createdAt": 1700001000000},
			},
			bubbles=[
				("aaa", "1", {"type": 1, "text": "hello from user"}),
				("aaa", "2", {"type": 2, "text": "hello from assistant"}),
				("aaa", "3", {"type": 1, "text": "follow-up"}),
				("bbb", "1", {"type": 1, "text": "user msg"}),
				("bbb", "2", {"type": 2, "text": "bot reply"}),
			],
		)
		records = iter_sessions(traces_dir=Path(tmp), cache_dir=cache_dir)
		assert len(records) == 2

		by_id = {r.run_id: r for r in records}
		assert by_id["aaa"].message_count == 3
		assert by_id["bbb"].message_count == 2

		for rec in records:
			p = Path(rec.session_path)
			assert p.is_file()
			assert p.suffix == ".jsonl"


def test_iter_sessions_skips_composers_without_bubbles():
	"""A composer with zero bubbles should not appear in results."""
	with TemporaryDirectory() as tmp:
		db_path = Path(tmp) / "state.vscdb"
		cache_dir = Path(tmp) / "cache"
		_make_cursor_db(
			db_path,
			composers={"lonely": {"composerId": "lonely", "createdAt": 1700000000000}},
			bubbles=[],
		)
		records = iter_sessions(traces_dir=Path(tmp), cache_dir=cache_dir)
		assert records == []


def test_iter_sessions_skips_known_ids():
	"""iter_sessions skips sessions whose run_id is already known."""
	with TemporaryDirectory() as tmp:
		db_path = Path(tmp) / "state.vscdb"
		cache_dir = Path(tmp) / "cache"
		_make_cursor_db(
			db_path,
			composers={
				"known": {"composerId": "known", "createdAt": 1700000000000},
				"new": {"composerId": "new", "createdAt": 1700001000000},
			},
			bubbles=[
				("known", "1", {"type": 1, "text": "hi"}),
				("new", "1", {"type": 1, "text": "hello"}),
			],
		)
		# Skip "known" by providing its ID
		records = iter_sessions(
			traces_dir=Path(tmp),
			cache_dir=cache_dir,
			known_run_ids={"known"},
		)
		assert len(records) == 1
		assert records[0].run_id == "new"


def test_iter_sessions_nonexistent_dir(tmp_path):
	"""Nonexistent traces_dir returns empty list."""
	records = iter_sessions(traces_dir=tmp_path / "nope")
	assert records == []


def test_iter_sessions_summaries_collected(tmp_path):
	"""User bubble text is collected as summaries (up to 5, truncated to 140 chars)."""
	db_path = tmp_path / "state.vscdb"
	cache_dir = tmp_path / "cache"
	_make_cursor_db(
		db_path,
		composers={"s": {"composerId": "s", "createdAt": 1700000000000}},
		bubbles=[
			("s", str(i), {"type": 1, "text": f"user message {i}"})
			for i in range(7)
		],
	)
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir)
	assert len(records) == 1
	# Should collect at most 5 summaries
	assert len(records[0].summaries) == 5
	assert records[0].summaries[0] == "user message 0"


def test_iter_sessions_tool_count(tmp_path):
	"""Bubbles with type not in (1, 2) are counted as tool calls."""
	db_path = tmp_path / "state.vscdb"
	cache_dir = tmp_path / "cache"
	_make_cursor_db(
		db_path,
		composers={"t": {"composerId": "t", "createdAt": 1700000000000}},
		bubbles=[
			("t", "1", {"type": 1, "text": "user"}),
			("t", "2", {"type": 2, "text": "assistant"}),
			("t", "3", {"type": 3, "text": "tool result"}),
			("t", "4", {"type": 4, "text": "another tool"}),
		],
	)
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir)
	assert len(records) == 1
	assert records[0].message_count == 2
	assert records[0].tool_call_count == 2


def test_iter_sessions_agent_type_is_cursor(tmp_path):
	"""SessionRecord agent_type is 'cursor'."""
	db_path = tmp_path / "state.vscdb"
	cache_dir = tmp_path / "cache"
	_make_cursor_db(
		db_path,
		composers={"x": {"composerId": "x", "createdAt": 1700000000000}},
		bubbles=[("x", "1", {"type": 1, "text": "hi"})],
	)
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir)
	assert records[0].agent_type == "cursor"


def test_iter_sessions_sorted_by_start_time(tmp_path):
	"""Records are sorted by start_time."""
	db_path = tmp_path / "state.vscdb"
	cache_dir = tmp_path / "cache"
	_make_cursor_db(
		db_path,
		composers={
			"late": {"composerId": "late", "createdAt": 1700002000000},
			"early": {"composerId": "early", "createdAt": 1700000000000},
		},
		bubbles=[
			("late", "1", {"type": 1, "text": "late"}),
			("early", "1", {"type": 1, "text": "early"}),
		],
	)
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir)
	assert records[0].run_id == "early"
	assert records[1].run_id == "late"


def test_iter_sessions_malformed_bubble_key_skipped(tmp_path):
	"""Bubble keys with fewer than 3 ':' parts are silently skipped."""
	db_path = tmp_path / "state.vscdb"
	cache_dir = tmp_path / "cache"
	conn = sqlite3.connect(db_path)
	conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
	conn.execute(
		"INSERT INTO cursorDiskKV VALUES (?, ?)",
		("bubbleId:badkey", json.dumps({"type": 1, "text": "orphan"})),
	)
	conn.execute(
		"INSERT INTO cursorDiskKV VALUES (?, ?)",
		("bubbleId:good:1", json.dumps({"type": 1, "text": "ok"})),
	)
	conn.commit()
	conn.close()
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir)
	assert len(records) == 1
	assert records[0].run_id == "good"


def test_iter_sessions_double_encoded_values(tmp_path):
	"""Double-encoded JSON values in DB are parsed correctly."""
	db_path = tmp_path / "state.vscdb"
	cache_dir = tmp_path / "cache"
	composer_data = {"composerId": "denc", "createdAt": 1700000000000}
	bubble_data = {"type": 1, "text": "double encoded"}
	conn = sqlite3.connect(db_path)
	conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
	# Double-encode: json.dumps wraps the already-dumped string
	conn.execute(
		"INSERT INTO cursorDiskKV VALUES (?, ?)",
		("composerData:denc", json.dumps(json.dumps(composer_data))),
	)
	conn.execute(
		"INSERT INTO cursorDiskKV VALUES (?, ?)",
		("bubbleId:denc:1", json.dumps(json.dumps(bubble_data))),
	)
	conn.commit()
	conn.close()
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir)
	assert len(records) == 1
	assert records[0].run_id == "denc"


# ---------------------------------------------------------------------------
# _read_session_db tests
# ---------------------------------------------------------------------------


def test_read_session_db_returns_viewer_session(tmp_path):
	"""_read_session_db returns a ViewerSession with correct messages."""
	db_path = tmp_path / "state.vscdb"
	_make_cursor_db(
		db_path,
		composers={"sid": {"composerId": "sid"}},
		bubbles=[
			("sid", "1", {"type": 1, "text": "user text"}),
			("sid", "2", {"type": 2, "text": "bot text"}),
			("sid", "3", {"type": 3, "text": "tool output"}),
		],
	)
	session = _read_session_db(db_path, "sid")
	assert session is not None
	assert session.session_id == "sid"
	assert len(session.messages) == 3
	assert session.messages[0].role == "user"
	assert session.messages[1].role == "assistant"
	assert session.messages[2].role == "tool"


def test_read_session_db_no_matching_bubbles(tmp_path):
	"""_read_session_db returns None when no bubbles match session_id."""
	db_path = tmp_path / "state.vscdb"
	_make_cursor_db(db_path, composers={}, bubbles=[])
	assert _read_session_db(db_path, "nonexistent") is None


def test_read_session_db_skips_empty_text(tmp_path):
	"""_read_session_db skips bubbles with empty text."""
	db_path = tmp_path / "state.vscdb"
	_make_cursor_db(
		db_path,
		composers={"e": {"composerId": "e"}},
		bubbles=[
			("e", "1", {"type": 1, "text": "has text"}),
			("e", "2", {"type": 2, "text": ""}),
			("e", "3", {"type": 2, "text": "   "}),
		],
	)
	session = _read_session_db(db_path, "e")
	assert session is not None
	assert len(session.messages) == 1


def test_read_session_db_non_dict_bubble_skipped(tmp_path):
	"""_read_session_db skips bubble values that parse to non-dict."""
	db_path = tmp_path / "state.vscdb"
	conn = sqlite3.connect(db_path)
	conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
	conn.execute(
		"INSERT INTO cursorDiskKV VALUES (?, ?)",
		("bubbleId:s:1", json.dumps("just a string")),
	)
	conn.execute(
		"INSERT INTO cursorDiskKV VALUES (?, ?)",
		("bubbleId:s:2", json.dumps({"type": 1, "text": "real"})),
	)
	conn.commit()
	conn.close()
	session = _read_session_db(db_path, "s")
	assert session is not None
	assert len(session.messages) == 1


# ---------------------------------------------------------------------------
# exported_jsonl tests
# ---------------------------------------------------------------------------


def test_exported_jsonl_contains_compacted_data():
	"""Exported JSONL uses canonical schema after compaction."""
	with TemporaryDirectory() as tmp:
		db_path = Path(tmp) / "state.vscdb"
		cache_dir = Path(tmp) / "cache"
		composer_data = {
			"composerId": "raw",
			"createdAt": 1700000000000,
			"status": "active",
		}
		bubble_data = {
			"type": 1,
			"text": "raw message",
			"createdAt": 1700000000000,
			"_v": 3,
			"lints": [{"code": "x"}],
			"extra_field": True,
		}
		_make_cursor_db(
			db_path,
			composers={"raw": composer_data},
			bubbles=[("raw", "b1", bubble_data)],
		)
		iter_sessions(traces_dir=Path(tmp), cache_dir=cache_dir)

		jsonl_path = cache_dir / "raw.jsonl"
		assert jsonl_path.is_file()
		lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
		# Header row is dropped, only the user bubble remains
		assert len(lines) == 1

		entry = json.loads(lines[0])
		assert entry["type"] == "user"
		assert entry["message"]["role"] == "user"
		assert entry["message"]["content"] == "raw message"
		assert "timestamp" in entry


# --- compact_trace tests ---


def test_compact_trace_clears_tool_results():
	"""compact_trace converts tool calls to canonical schema with cleared results."""
	bubble = {
		"type": 2,
		"capabilityType": 15,
		"createdAt": 1700000000000,
		"toolFormerData": [
			{
				"name": "run_terminal_command_v2",
				"params": {"cmd": "ls"},
				"result": "x" * 5000,
				"status": "done",
			},
		],
	}
	result = compact_trace(json.dumps(bubble) + "\n")
	parsed = json.loads(result.strip())
	assert parsed["type"] == "assistant"
	assert parsed["message"]["role"] == "assistant"
	blocks = parsed["message"]["content"]
	assert blocks[0]["type"] == "tool_use"
	assert blocks[0]["name"] == "run_terminal_command_v2"
	assert blocks[0]["input"] == {"cmd": "ls"}
	assert blocks[1]["type"] == "tool_result"
	assert blocks[1]["content"] == "[cleared: 5000 chars]"


def test_compact_trace_clears_thinking_blocks():
	"""compact_trace drops thinking blocks (capabilityType 30) entirely."""
	bubble = {
		"type": 2,
		"capabilityType": 30,
		"thinking": {"text": "y" * 10000, "signature": "sig123"},
		"text": "",
	}
	result = compact_trace(json.dumps(bubble) + "\n")
	# Thinking blocks are dropped entirely, so result should be empty
	assert result.strip() == ""


def test_compact_trace_strips_empty_fields():
	"""compact_trace converts to canonical schema, stripping all raw fields."""
	bubble = {
		"type": 1,
		"text": "hello",
		"createdAt": 1700000000000,
		"lints": [],
		"commits": [],
		"attachments": [],
		"extra": None,
	}
	result = compact_trace(json.dumps(bubble) + "\n")
	parsed = json.loads(result.strip())
	assert parsed["type"] == "user"
	assert parsed["message"]["content"] == "hello"
	# Raw fields are not present in canonical output
	assert "lints" not in parsed
	assert "commits" not in parsed
	assert "attachments" not in parsed
	assert "extra" not in parsed


def test_compact_trace_preserves_user_assistant_text():
	"""compact_trace preserves text content in canonical message.content."""
	lines = [
		json.dumps({"type": 1, "text": "user message"}),
		json.dumps({"type": 2, "text": "assistant reply"}),
	]
	result = compact_trace("\n".join(lines) + "\n")
	parsed = [json.loads(line) for line in result.strip().split("\n")]
	assert parsed[0]["message"]["content"] == "user message"
	assert parsed[0]["type"] == "user"
	assert parsed[1]["message"]["content"] == "assistant reply"
	assert parsed[1]["type"] == "assistant"


def test_compact_trace_preserves_false_and_zero():
	"""compact_trace canonical schema does not carry over raw bubble fields."""
	bubble = {
		"type": 1,
		"text": "msg",
		"createdAt": 1700000000000,
		"enabled": False,
		"count": 0,
	}
	result = compact_trace(json.dumps(bubble) + "\n")
	parsed = json.loads(result.strip())
	# Canonical schema only has type, message, timestamp
	assert parsed["type"] == "user"
	assert parsed["message"]["content"] == "msg"
	assert "enabled" not in parsed
	assert "count" not in parsed


def test_compact_trace_non_dict_tool_former_data_entry():
	"""compact_trace skips non-dict entries in toolFormerData tool list."""
	bubble = {
		"type": 2,
		"capabilityType": 15,
		"createdAt": 1700000000000,
		"toolFormerData": [
			"not a dict",
			{"name": "tool", "result": "data"},
		],
	}
	result = compact_trace(json.dumps(bubble) + "\n")
	parsed = json.loads(result.strip())
	assert parsed["type"] == "assistant"
	blocks = parsed["message"]["content"]
	# Only the dict entry produces a tool_use block; "not a dict" is skipped
	assert blocks[0]["type"] == "tool_use"
	assert blocks[0]["name"] == "tool"
	assert blocks[1]["type"] == "tool_result"
	assert blocks[1]["content"] == "[cleared: 4 chars]"


# ---------------------------------------------------------------------------
# Time window filtering tests
# ---------------------------------------------------------------------------


def test_iter_sessions_time_window_filter(tmp_path):
	"""iter_sessions filters sessions outside the start/end time window."""
	from datetime import datetime, timezone

	db_path = tmp_path / "state.vscdb"
	cache_dir = tmp_path / "cache"
	_make_cursor_db(
		db_path,
		composers={
			"old": {"composerId": "old", "createdAt": 1600000000000},
			"new": {"composerId": "new", "createdAt": 1800000000000},
		},
		bubbles=[
			("old", "1", {"type": 1, "text": "old msg"}),
			("new", "1", {"type": 1, "text": "new msg"}),
		],
	)
	# Window that only includes the "new" session
	start = datetime(2025, 1, 1, tzinfo=timezone.utc)
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir, start=start)
	assert len(records) == 1
	assert records[0].run_id == "new"


def test_iter_sessions_empty_text_not_in_summaries(tmp_path):
	"""Bubbles with empty text after stripping are not added to summaries."""
	db_path = tmp_path / "state.vscdb"
	cache_dir = tmp_path / "cache"
	_make_cursor_db(
		db_path,
		composers={"es": {"composerId": "es", "createdAt": 1700000000000}},
		bubbles=[
			("es", "1", {"type": 1, "text": "   "}),
			("es", "2", {"type": 1, "text": "real text"}),
		],
	)
	records = iter_sessions(traces_dir=tmp_path, cache_dir=cache_dir)
	assert len(records) == 1
	# Only "real text" should appear (empty stripped text is skipped)
	assert records[0].summaries == ["real text"]


# ---------------------------------------------------------------------------
# SQLite error handling tests
# ---------------------------------------------------------------------------


def test_validate_connection_sqlite_error(tmp_path):
	"""validate_connection returns error on corrupt DB."""
	db_path = tmp_path / "state.vscdb"
	db_path.write_bytes(b"this is not a sqlite database")
	result = validate_connection(tmp_path)
	assert result["ok"] is False
	assert "error" in result


def test_count_sessions_sqlite_error(tmp_path):
	"""count_sessions returns 0 on corrupt DB."""
	db_path = tmp_path / "state.vscdb"
	# Create a valid DB then corrupt it by overwriting with bad data
	_make_cursor_db(db_path, composers={}, bubbles=[])
	# Corrupt the table
	conn = sqlite3.connect(db_path)
	conn.execute("DROP TABLE cursorDiskKV")
	conn.execute("CREATE TABLE cursorDiskKV (key INTEGER)")
	conn.commit()
	conn.close()
	# count_sessions tries SELECT key FROM cursorDiskKV WHERE key LIKE ... which will fail on split
	# But it won't raise sqlite3.Error. Let's use a truly corrupt DB approach.
	db_path.unlink()
	db_path.write_bytes(b"not a database at all")
	assert count_sessions(tmp_path) == 0


def test_iter_sessions_sqlite_error(tmp_path):
	"""iter_sessions returns empty list on corrupt DB."""
	db_path = tmp_path / "state.vscdb"
	db_path.write_bytes(b"corrupt sqlite data")
	records = iter_sessions(traces_dir=tmp_path, cache_dir=tmp_path / "cache")
	assert records == []


def test_read_session_db_sqlite_error(tmp_path):
	"""_read_session_db returns None on SQLite error."""
	db_path = tmp_path / "state.vscdb"
	db_path.write_bytes(b"corrupt data")
	assert _read_session_db(db_path, "sid") is None
