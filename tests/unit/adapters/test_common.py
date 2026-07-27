"""Unit tests for the shared adapter helpers in ``lerim.adapters.common``.

The module is small on purpose after the trajectory migration: timestamp
parsing, the one JSONL writer every trace file goes through, window filtering,
hashing, and the tool-failure classifier. The writer gets the most attention
here because the one-record-per-line layout it produces is what makes
``line:<N>`` evidence citations addressable.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lerim.adapters.common import (
    TOOL_ERROR_WRAPPER,
    TOOL_FAILURE_PREFIXES,
    compute_file_hash,
    in_window,
    is_failed_tool_result_text,
    load_jsonl_dict_lines,
    normalize_timestamp_iso,
    parse_timestamp,
    write_trajectory_jsonl,
)

# The three characters `json.dumps(ensure_ascii=False)` emits raw and
# `str.splitlines()` treats as line breaks.
LINE_SEPARATORS = ("\u2028", "\u2029", "\u0085")


# ---------------------------------------------------------------------------
# write_trajectory_jsonl — the load-bearing writer
# ---------------------------------------------------------------------------


def test_writer_emits_one_compact_record_per_line(tmp_path):
    """Records land one per line with no pretty-printing whitespace."""
    path = write_trajectory_jsonl(
        tmp_path / "trace.jsonl",
        [
            {"role": "meta", "source": "generic"},
            {"role": "user", "content": "hello", "timestamp": "2026-05-16T09:00:00Z"},
        ],
    )

    lines = path.read_text(encoding="utf-8").split("\n")
    assert lines == [
        '{"role":"meta","source":"generic"}',
        '{"role":"user","content":"hello","timestamp":"2026-05-16T09:00:00Z"}',
        "",
    ]


@pytest.mark.parametrize("separator", LINE_SEPARATORS)
def test_writer_escapes_characters_that_would_split_a_record(tmp_path, separator):
    """A record carrying a Unicode line separator still occupies exactly one line.

    Both readers must agree: ``for line in handle`` splits on ``\\n`` only,
    while ``str.splitlines()`` also splits on U+2028/U+2029/U+0085. An
    unescaped separator makes them disagree and silently rebinds every later
    ``line:<N>`` citation in the file.
    """
    path = write_trajectory_jsonl(
        tmp_path / "trace.jsonl",
        [
            {"role": "meta", "source": "generic"},
            {"role": "user", "content": f"var a=1;{separator}var b=2;"},
            {"role": "assistant", "content": "ok"},
        ],
    )

    raw = path.read_text(encoding="utf-8")
    assert separator not in raw
    assert len(raw.splitlines()) == 3
    with path.open("r", encoding="utf-8") as handle:
        assert len(list(handle)) == 3
    assert json.loads(raw.splitlines()[1])["content"] == f"var a=1;{separator}var b=2;"


def test_writer_keeps_non_ascii_text_readable(tmp_path):
    """Ordinary non-ASCII content is written as itself, not as escape sequences."""
    path = write_trajectory_jsonl(
        tmp_path / "trace.jsonl", [{"role": "meta", "source": "generic", "cwd": "/tmp/naïve"}]
    )

    raw = path.read_text(encoding="utf-8")
    assert "naïve" in raw
    assert json.loads(raw)["cwd"] == "/tmp/naïve"


def test_writer_creates_missing_parent_directories(tmp_path):
    """A first write into a new agent's cache directory succeeds."""
    destination = tmp_path / "cache" / "traces" / "claude" / "run.jsonl"

    path = write_trajectory_jsonl(destination, [{"role": "meta", "source": "claude-code"}])

    assert path == destination
    assert destination.is_file()


def test_writer_replaces_previous_content(tmp_path):
    """Re-normalizing a session overwrites its cache rather than appending to it."""
    destination = tmp_path / "trace.jsonl"
    write_trajectory_jsonl(destination, [{"role": "meta", "source": "a"}])

    write_trajectory_jsonl(destination, [{"role": "meta", "source": "b"}])

    assert destination.read_text(encoding="utf-8") == '{"role":"meta","source":"b"}\n'


def test_writing_no_records_produces_an_empty_file(tmp_path):
    """An empty record list is written as an empty file, not a malformed one."""
    path = write_trajectory_jsonl(tmp_path / "trace.jsonl", [])

    assert path.read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# is_failed_tool_result_text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", TOOL_FAILURE_PREFIXES)
def test_every_declared_failure_prefix_is_detected(prefix):
    """Each marker in the table classifies the result it was added for."""
    assert is_failed_tool_result_text(f"{prefix} while running the command")


def test_the_error_wrapper_anywhere_in_the_head_marks_a_failure():
    """Harness-wrapped errors are detected even behind a short preamble."""
    assert is_failed_tool_result_text(f"tool output {TOOL_ERROR_WRAPPER} bad path")


def test_a_nonzero_exit_code_is_a_failure_and_zero_is_not():
    """`Exit code 0` is success even though it shares the prefix with failures."""
    assert is_failed_tool_result_text("Exit code 1\nplan.md")
    assert not is_failed_tool_result_text("Exit code 0\nplan.md")


def test_classification_ignores_case_and_leading_whitespace():
    """Markers are matched on normalized text, not on exact harness formatting."""
    assert is_failed_tool_result_text("   ERROR: no such file")


def test_ordinary_output_and_empty_results_are_not_failures():
    """A successful result is never counted, and neither is a blank one."""
    assert not is_failed_tool_result_text("1\tdef retry():")
    assert not is_failed_tool_result_text("")
    assert not is_failed_tool_result_text("   \n  ")


def test_a_failure_marker_far_into_the_output_is_not_counted():
    """Only the head is inspected, so prose mentioning an error is not a failure."""
    assert not is_failed_tool_result_text("ok\n" * 200 + "Error: too late to matter")


# ---------------------------------------------------------------------------
# parse_timestamp / normalize_timestamp_iso
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "2026-02-20T10:00:00+00:00",
        "2026-03-15T12:00:00Z",
        "2026-03-06T14:15:22.394Z",
        1_706_000_000,
        1_706_000_000_000,
        datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 15, 12, 0),
    ],
)
def test_known_timestamp_shapes_parse_to_aware_datetimes(value):
    """Every shape harnesses emit becomes a timezone-aware UTC datetime."""
    parsed = parse_timestamp(value)

    assert isinstance(parsed, datetime)
    assert parsed.tzinfo is not None


@pytest.mark.parametrize("value", ["not-a-date", None, "", [1, 2, 3], {"a": 1}])
def test_unparseable_timestamps_return_none(value):
    """A timestamp Lerim cannot read is absent, not a crash or a wrong instant."""
    assert parse_timestamp(value) is None


def test_a_datetime_with_a_zone_is_returned_unchanged():
    """An already-aware datetime is not re-interpreted."""
    moment = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
    assert parse_timestamp(moment) == moment


def test_a_naive_datetime_is_read_as_utc():
    """Harnesses that omit a zone are treated as UTC rather than local time."""
    assert parse_timestamp(datetime(2026, 3, 15, 12, 0)).tzinfo == timezone.utc


def test_normalize_timestamp_iso_renders_a_stable_utc_string():
    """The catalog stores one timestamp spelling regardless of input shape."""
    assert normalize_timestamp_iso("2026-02-19T10:00:00Z") == "2026-02-19T10:00:00Z"
    assert normalize_timestamp_iso(1_706_000_000) is not None
    assert normalize_timestamp_iso("not-a-date") is None


# ---------------------------------------------------------------------------
# load_jsonl_dict_lines
# ---------------------------------------------------------------------------


def test_only_json_objects_are_returned_from_a_jsonl_file(tmp_path):
    """Arrays, junk lines and blanks are skipped; objects are kept in order."""
    path = tmp_path / "mixed.jsonl"
    path.write_text('{"a":1}\n\n[1,2,3]\nnot-json\n{"b":2}\n', encoding="utf-8")

    assert load_jsonl_dict_lines(path) == [{"a": 1}, {"b": 2}]


def test_reading_a_missing_or_empty_file_yields_nothing(tmp_path):
    """A cache file that is gone or empty reads as no rows, not an exception."""
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")

    assert load_jsonl_dict_lines(empty) == []
    assert load_jsonl_dict_lines(tmp_path / "missing.jsonl") == []


def test_records_written_by_the_writer_read_back_identically(tmp_path):
    """The writer and the reader agree, including on escaped line separators."""
    records = [
        {"role": "meta", "source": "generic"},
        {"role": "user", "content": "a\u2028b", "timestamp": "2026-05-16T09:00:00Z"},
    ]
    path = write_trajectory_jsonl(tmp_path / "trace.jsonl", records)

    assert load_jsonl_dict_lines(path) == records


# ---------------------------------------------------------------------------
# in_window
# ---------------------------------------------------------------------------


def test_window_bounds_are_inclusive():
    """A session exactly on a bound is inside the window."""
    moment = datetime(2026, 2, 20, tzinfo=timezone.utc)
    assert in_window(moment, moment, moment) is True


def test_values_outside_either_bound_are_excluded():
    """Sessions before the start or after the end are filtered out."""
    start = datetime(2026, 2, 20, tzinfo=timezone.utc)
    end = datetime(2026, 2, 21, tzinfo=timezone.utc)

    assert in_window(datetime(2026, 2, 19, tzinfo=timezone.utc), start, end) is False
    assert in_window(datetime(2026, 2, 22, tzinfo=timezone.utc), start, end) is False


def test_missing_bounds_mean_unbounded():
    """`None` bounds do not narrow the window."""
    moment = datetime(2026, 2, 20, 12, tzinfo=timezone.utc)

    assert in_window(moment, None, None) is True
    assert in_window(moment, None, datetime(2027, 1, 1, tzinfo=timezone.utc)) is True
    assert in_window(moment, datetime(2025, 1, 1, tzinfo=timezone.utc), None) is True


def test_an_unknown_timestamp_only_passes_an_unbounded_window():
    """A session with no readable time is not silently assumed to be in range."""
    assert in_window(None, None, None) is True
    assert in_window(None, datetime(2026, 1, 1, tzinfo=timezone.utc), None) is False


# ---------------------------------------------------------------------------
# compute_file_hash
# ---------------------------------------------------------------------------


def test_the_content_hash_is_deterministic_and_content_addressed(tmp_path):
    """Re-hashing an unchanged file matches; any edit changes the digest."""
    path = tmp_path / "trace.jsonl"
    path.write_text("content A", encoding="utf-8")
    first = compute_file_hash(path)

    assert compute_file_hash(path) == first
    assert len(first) == 64

    path.write_text("content B", encoding="utf-8")
    assert compute_file_hash(path) != first


def test_hashing_an_empty_file_still_produces_a_digest(tmp_path):
    """An empty cache file is hashable, so it can be compared like any other."""
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")

    assert len(compute_file_hash(path)) == 64


def test_hashing_a_missing_file_fails_loudly(tmp_path):
    """A cache file that vanished is an error, not a silently stable hash."""
    with pytest.raises(OSError):
        compute_file_hash(Path(tmp_path / "gone.jsonl"))
