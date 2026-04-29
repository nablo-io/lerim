"""Unit tests for shared adapter helpers in lerim.adapters.common.

Covers parse_timestamp, load_jsonl_dict_lines, count_non_empty_files,
in_window, compute_file_hash, compact_jsonl, readonly_connect, and
write_session_cache.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from lerim.adapters.common import (
	compact_jsonl,
	compute_file_hash,
	count_non_empty_files,
	in_window,
	load_jsonl_dict_lines,
	parse_timestamp,
	readonly_connect,
	write_session_cache,
)


# ---------------------------------------------------------------------------
# parse_timestamp
# ---------------------------------------------------------------------------


def test_parse_timestamp_iso():
	"""ISO 8601 string -> datetime."""
	result = parse_timestamp("2026-02-20T10:00:00+00:00")
	assert isinstance(result, datetime)
	assert result.year == 2026
	assert result.tzinfo is not None


def test_parse_timestamp_epoch_ms():
	"""Millisecond epoch int -> datetime."""
	result = parse_timestamp(1_706_000_000_000)
	assert isinstance(result, datetime)
	assert result.tzinfo is not None


def test_parse_timestamp_epoch_s():
	"""Second epoch int -> datetime."""
	result = parse_timestamp(1_706_000_000)
	assert isinstance(result, datetime)
	assert result.tzinfo is not None


def test_parse_timestamp_invalid():
	"""Invalid input -> None (no crash)."""
	assert parse_timestamp("not-a-date") is None
	assert parse_timestamp(None) is None
	assert parse_timestamp("") is None
	assert parse_timestamp([1, 2, 3]) is None


def test_parse_timestamp_datetime_passthrough():
	"""A datetime object is returned as-is (with tz)."""
	dt = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
	result = parse_timestamp(dt)
	assert result == dt
	assert result.tzinfo is not None


def test_parse_timestamp_naive_datetime():
	"""A naive datetime gets UTC attached."""
	naive = datetime(2026, 3, 15, 12, 0, 0)
	result = parse_timestamp(naive)
	assert result.tzinfo == timezone.utc
	assert result.year == 2026


def test_parse_timestamp_iso_with_z_suffix():
	"""ISO string ending in Z is parsed correctly."""
	result = parse_timestamp("2026-03-15T12:00:00Z")
	assert isinstance(result, datetime)
	assert result.tzinfo is not None


def test_parse_timestamp_negative_epoch():
	"""Large-magnitude negative epoch (pre-1970) returns None on OSError."""
	# A very large negative number may cause OSError on some platforms
	result = parse_timestamp(-1e18)
	assert result is None or isinstance(result, datetime)


# ---------------------------------------------------------------------------
# load_jsonl_dict_lines
# ---------------------------------------------------------------------------


def test_load_jsonl_dict_lines_valid(tmp_path):
	"""File with valid JSON dict lines -> list of dicts."""
	f = tmp_path / "valid.jsonl"
	f.write_text('{"a":1}\n{"b":2}\n', encoding="utf-8")
	rows = load_jsonl_dict_lines(f)
	assert rows == [{"a": 1}, {"b": 2}]


def test_load_jsonl_dict_lines_mixed(tmp_path):
	"""File with dicts + arrays + invalid -> only dicts returned."""
	f = tmp_path / "mixed.jsonl"
	f.write_text('{"a":1}\n[1,2,3]\nnot-json\n{"b":2}\n', encoding="utf-8")
	rows = load_jsonl_dict_lines(f)
	assert rows == [{"a": 1}, {"b": 2}]


def test_load_jsonl_dict_lines_empty(tmp_path):
	"""Empty file -> empty list."""
	f = tmp_path / "empty.jsonl"
	f.write_text("", encoding="utf-8")
	assert load_jsonl_dict_lines(f) == []


def test_load_jsonl_dict_lines_missing_file(tmp_path):
	"""Non-existent file returns empty list (OSError caught)."""
	missing = tmp_path / "does-not-exist.jsonl"
	assert load_jsonl_dict_lines(missing) == []


def test_load_jsonl_dict_lines_blank_lines(tmp_path):
	"""Blank lines are skipped."""
	f = tmp_path / "blanks.jsonl"
	f.write_text('\n\n{"a":1}\n\n', encoding="utf-8")
	rows = load_jsonl_dict_lines(f)
	assert rows == [{"a": 1}]


# ---------------------------------------------------------------------------
# count_non_empty_files
# ---------------------------------------------------------------------------


def test_count_non_empty_files(tmp_path):
	"""Count files matching glob that have content."""
	(tmp_path / "a.jsonl").write_text('{"x":1}', encoding="utf-8")
	(tmp_path / "b.jsonl").write_text("", encoding="utf-8")  # empty
	(tmp_path / "c.txt").write_text("data", encoding="utf-8")  # wrong ext
	assert count_non_empty_files(tmp_path, "*.jsonl") == 1


def test_count_non_empty_files_missing_dir(tmp_path):
	"""Non-existent directory returns 0."""
	assert count_non_empty_files(tmp_path / "nope", "*.jsonl") == 0


# ---------------------------------------------------------------------------
# in_window
# ---------------------------------------------------------------------------


def test_in_window_inside():
	"""Datetime within start-end -> True."""
	now = datetime(2026, 2, 20, 12, 0, 0, tzinfo=timezone.utc)
	start = datetime(2026, 2, 20, 0, 0, 0, tzinfo=timezone.utc)
	end = datetime(2026, 2, 21, 0, 0, 0, tzinfo=timezone.utc)
	assert in_window(now, start, end) is True


def test_in_window_outside():
	"""Datetime outside range -> False."""
	now = datetime(2026, 2, 22, 12, 0, 0, tzinfo=timezone.utc)
	start = datetime(2026, 2, 20, 0, 0, 0, tzinfo=timezone.utc)
	end = datetime(2026, 2, 21, 0, 0, 0, tzinfo=timezone.utc)
	assert in_window(now, start, end) is False


def test_in_window_none_bounds():
	"""None start or end means unbounded."""
	now = datetime(2026, 2, 20, 12, 0, 0, tzinfo=timezone.utc)
	assert in_window(now, None, None) is True
	assert in_window(now, None, datetime(2027, 1, 1, tzinfo=timezone.utc)) is True
	assert in_window(now, datetime(2025, 1, 1, tzinfo=timezone.utc), None) is True


def test_in_window_none_value_no_bounds():
	"""None value with no bounds returns True."""
	assert in_window(None, None, None) is True


def test_in_window_none_value_with_bounds():
	"""None value with actual bounds returns False."""
	start = datetime(2026, 1, 1, tzinfo=timezone.utc)
	assert in_window(None, start, None) is False


def test_in_window_exact_boundary():
	"""Value equal to start or end is inclusive."""
	dt = datetime(2026, 2, 20, 0, 0, 0, tzinfo=timezone.utc)
	assert in_window(dt, dt, dt) is True


def test_in_window_before_start():
	"""Value before start returns False."""
	value = datetime(2026, 1, 1, tzinfo=timezone.utc)
	start = datetime(2026, 2, 1, tzinfo=timezone.utc)
	assert in_window(value, start, None) is False


# ---------------------------------------------------------------------------
# compute_file_hash
# ---------------------------------------------------------------------------


def test_compute_file_hash_deterministic(tmp_path):
	"""Same file content produces same hash."""
	f = tmp_path / "test.txt"
	f.write_text("hello world", encoding="utf-8")
	h1 = compute_file_hash(f)
	h2 = compute_file_hash(f)
	assert h1 == h2
	assert len(h1) == 64  # SHA-256 hex digest


def test_compute_file_hash_changes_on_content(tmp_path):
	"""Different content produces different hash."""
	f = tmp_path / "test.txt"
	f.write_text("content A", encoding="utf-8")
	h1 = compute_file_hash(f)
	f.write_text("content B", encoding="utf-8")
	h2 = compute_file_hash(f)
	assert h1 != h2


def test_compute_file_hash_empty_file(tmp_path):
	"""Empty file produces consistent hash (SHA-256 of empty)."""
	f = tmp_path / "empty.txt"
	f.write_text("", encoding="utf-8")
	h = compute_file_hash(f)
	assert len(h) == 64


# ---------------------------------------------------------------------------
# compact_jsonl
# ---------------------------------------------------------------------------


def test_compact_jsonl_identity_cleaner():
	"""Cleaner that returns input unchanged preserves all lines."""
	raw = '{"a":1}\n{"b":2}\n'
	result = compact_jsonl(raw, lambda obj: obj)
	lines = [line for line in result.strip().split("\n") if line]
	assert len(lines) == 2
	assert json.loads(lines[0]) == {"a": 1}
	assert json.loads(lines[1]) == {"b": 2}


def test_compact_jsonl_drop_lines():
	"""Cleaner returning None drops lines."""
	raw = '{"keep":true}\n{"drop":true}\n'

	def cleaner(obj: dict[str, Any]) -> dict[str, Any] | None:
		"""Keep only dicts where keep is true."""
		if obj.get("drop"):
			return None
		return obj

	result = compact_jsonl(raw, cleaner)
	lines = [line for line in result.strip().split("\n") if line]
	assert len(lines) == 1
	assert json.loads(lines[0]) == {"keep": True}


def test_compact_jsonl_non_json_lines_kept():
	"""Non-JSON lines pass through unchanged."""
	raw = 'not-json\n{"a":1}\n'
	result = compact_jsonl(raw, lambda obj: obj)
	lines = [line for line in result.strip().split("\n") if line]
	assert lines[0] == "not-json"
	assert json.loads(lines[1]) == {"a": 1}


def test_compact_jsonl_empty_lines_skipped():
	"""Empty lines are dropped from output."""
	raw = '\n\n{"a":1}\n\n'
	result = compact_jsonl(raw, lambda obj: obj)
	lines = [line for line in result.strip().split("\n") if line]
	assert len(lines) == 1


def test_compact_jsonl_cleaner_transforms():
	"""Cleaner can transform the dict content."""
	raw = '{"msg":"hello","extra":"remove"}\n'

	def strip_extra(obj: dict[str, Any]) -> dict[str, Any]:
		"""Remove extra key from dict."""
		obj.pop("extra", None)
		return obj

	result = compact_jsonl(raw, strip_extra)
	parsed = json.loads(result.strip())
	assert parsed == {"msg": "hello"}
	assert "extra" not in parsed


# ---------------------------------------------------------------------------
# readonly_connect
# ---------------------------------------------------------------------------


def test_readonly_connect_row_factory(tmp_path):
	"""readonly_connect returns connection with Row factory."""
	db_path = tmp_path / "test.db"
	# Create a table to query
	conn = sqlite3.connect(str(db_path))
	conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
	conn.execute("INSERT INTO t VALUES (1, 'alice')")
	conn.commit()
	conn.close()

	ro = readonly_connect(db_path)
	row = ro.execute("SELECT * FROM t").fetchone()
	# Row factory means we can access by column name
	assert row["id"] == 1
	assert row["name"] == "alice"
	ro.close()


def test_readonly_connect_blocks_writes(tmp_path):
	"""readonly_connect with PRAGMA query_only=ON blocks INSERT/UPDATE."""
	db_path = tmp_path / "test.db"
	conn = sqlite3.connect(str(db_path))
	conn.execute("CREATE TABLE t (id INTEGER)")
	conn.commit()
	conn.close()

	ro = readonly_connect(db_path)
	try:
		ro.execute("INSERT INTO t VALUES (1)")
		ro.commit()
		# Some SQLite versions raise on execute, others on commit
		assert False, "Write should have been blocked"
	except sqlite3.OperationalError:
		pass
	finally:
		ro.close()


# ---------------------------------------------------------------------------
# write_session_cache
# ---------------------------------------------------------------------------


def test_write_session_cache_basic(tmp_path):
	"""write_session_cache writes compacted JSONL and returns correct path."""
	cache_dir = tmp_path / "cache"
	lines = ['{"a":1}', '{"b":2}']

	def identity_compact(raw: str) -> str:
		"""Identity compactor -- returns input unchanged."""
		return raw

	result_path = write_session_cache(cache_dir, "run-001", lines, identity_compact)

	assert result_path == cache_dir / "run-001.jsonl"
	assert result_path.exists()
	content = result_path.read_text(encoding="utf-8")
	assert '{"a":1}' in content
	assert '{"b":2}' in content


def test_write_session_cache_creates_dir(tmp_path):
	"""write_session_cache creates cache_dir if it does not exist."""
	cache_dir = tmp_path / "deep" / "nested" / "cache"
	assert not cache_dir.exists()

	write_session_cache(cache_dir, "run-002", ["line1"], lambda r: r)

	assert cache_dir.exists()
	assert (cache_dir / "run-002.jsonl").exists()


def test_write_session_cache_applies_compact_fn(tmp_path):
	"""write_session_cache passes raw content through compact_fn."""
	cache_dir = tmp_path / "cache"

	def upper_compact(raw: str) -> str:
		"""Convert to uppercase."""
		return raw.upper()

	write_session_cache(cache_dir, "run-003", ["hello"], upper_compact)
	content = (cache_dir / "run-003.jsonl").read_text(encoding="utf-8")
	assert content == "HELLO\n"
