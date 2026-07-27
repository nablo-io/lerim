"""Shared helpers for timestamps, trace JSONL I/O, window filtering, and hashing."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

# ``json.dumps`` escapes every C0 control character, but with
# ``ensure_ascii=False`` it emits U+2028, U+2029 and U+0085 raw — and
# ``str.splitlines()`` treats all three as line breaks. Measured on real data:
# 2 of 19 codex sessions carry U+2028. Escaping them keeps one record on one
# line for every reader, and the escape decodes back to the same string.
_LINE_BREAK_ESCAPES = str.maketrans(
    {"\u2028": "\\u2028", "\u2029": "\\u2029", "\u0085": "\\u0085"}
)

# trajectory-v1 carries no `is_error` flag: the normalizer drops it, so a failed
# tool call is only recognizable from the head of its result text. These markers
# are anchored at the head and taken from observed harness failure output, so
# the rule under-reports rather than misclassifying successful output. Measured
# by joining raw claude `is_error` blocks to their normalized records by
# tool_call_id across 150 local sessions: 81 of 129 real failures detected,
# 10 of 4,153 successful results flagged (all of them error text the harness
# itself did not flag).
TOOL_ERROR_WRAPPER = "<tool_use_error>"
TOOL_FAILURE_PREFIXES = (
    "error",
    "exception",
    "traceback (most recent call last)",
    "permission denied",
    "command not found",
    "file does not exist",
    "file has not been read yet",
    "file content (",
    "the user doesn't want to proceed",
    "[request interrupted",
)


def is_failed_tool_result_text(content: str) -> bool:
    """Return whether a trajectory-v1 tool result's text reports a failed call."""
    head = " ".join(content.split()).strip()[:200].lower()
    if not head:
        return False
    if TOOL_ERROR_WRAPPER in head:
        return True
    if head.startswith("exit code ") and not head.startswith("exit code 0"):
        return True
    return head.startswith(TOOL_FAILURE_PREFIXES)


def parse_timestamp(value: Any) -> datetime | None:
    """Parse many timestamp shapes into a timezone-aware UTC datetime."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        timestamp = float(value)
        if abs(timestamp) > 1e10:
            timestamp /= 1000.0
        try:
            parsed = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (ValueError, OSError):
            return None
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def normalize_timestamp_iso(value: Any) -> str | None:
    """Parse any timestamp shape and return ISO 8601 UTC string, or None."""
    dt = parse_timestamp(value)
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def load_jsonl_dict_lines(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file and return only dict payload rows."""
    entries: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    entries.append(payload)
    except OSError:
        return []
    return entries


def write_trajectory_jsonl(
    path: Path, records: Sequence[Mapping[str, Any]]
) -> Path:
    """Write trajectory-v1 records as one compact JSON object per line.

    Every writer of a trace file goes through here, because the layout is
    load-bearing: extracted context cites evidence as ``line:<N>`` into these
    files, so a record that spans two lines silently rebinds every later
    citation. Records must already be redacted — this only serializes.
    """
    lines = [
        json.dumps(record, ensure_ascii=False, separators=(",", ":")).translate(
            _LINE_BREAK_ESCAPES
        )
        for record in records
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return path


def in_window(
    value: datetime | None, start: datetime | None, end: datetime | None
) -> bool:
    """Return whether ``value`` is inside the inclusive ``start``/``end`` window."""
    if value is None:
        return start is None and end is None
    if start and value < start:
        return False
    if end and value > end:
        return False
    return True


def compute_file_hash(path: Path) -> str:
    """Compute SHA-256 hex digest of a file's raw bytes."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


if __name__ == "__main__":
    """Run a real-path smoke test for timestamp parsing and JSONL reading."""
    assert parse_timestamp("2026-02-19T10:00:00+00:00") is not None
    assert parse_timestamp(1_706_000_000) is not None
    assert parse_timestamp("not-a-date") is None
    assert normalize_timestamp_iso("2026-02-19T10:00:00Z") == "2026-02-19T10:00:00Z"

    with TemporaryDirectory() as tmp_dir:
        sample = Path(tmp_dir) / "sample.jsonl"
        sample.write_text('{"a":1}\n{"b":2}\nnot-json\n[1,2,3]\n', encoding="utf-8")
        rows = load_jsonl_dict_lines(sample)
        assert rows == [{"a": 1}, {"b": 2}]

        h1 = compute_file_hash(sample)
        assert len(h1) == 64, "SHA-256 hex digest should be 64 chars"
        h2 = compute_file_hash(sample)
        assert h1 == h2, "Hash should be deterministic"
        sample.write_text('{"c":3}\n', encoding="utf-8")
        h3 = compute_file_hash(sample)
        assert h3 != h1, "Changed file should produce different hash"

        # A record carrying U+2028 still occupies exactly one line, for both
        # `open()` iteration and `str.splitlines()`.
        trace = Path(tmp_dir) / "nested" / "trace.jsonl"
        separator = "\u2028"
        write_trajectory_jsonl(
            trace,
            [
                {"role": "meta", "source": "generic"},
                {"role": "user", "content": f"var a=1;{separator}var b=2;"},
                {"role": "assistant", "content": "ok"},
            ],
        )
        raw = trace.read_text(encoding="utf-8")
        assert len(raw.splitlines()) == 3, raw.splitlines()
        assert separator not in raw
        assert json.loads(raw.splitlines()[1])["content"] == f"var a=1;{separator}var b=2;"

    assert is_failed_tool_result_text("Error: command not found")
    assert is_failed_tool_result_text("Exit code 1\nplan.md")
    assert not is_failed_tool_result_text("Exit code 0\nplan.md")
    assert not is_failed_tool_result_text("")

    now = datetime.now(timezone.utc)
    assert in_window(now, now, now)
