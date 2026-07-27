"""Custom trace-folder scanner.

Custom projects are folders of already-clean trajectory-v1 JSONL traces, one
file per session. They are not harness sources: Lerim does not compact,
rewrite, or normalize these files, so a folder is only ingested when every
record in it already matches the format the trajectory normalizer emits.

:func:`_validate_record` mirrors ``trajectory-v1.schema.json`` as shipped by the
pinned ``@letta-ai/trajectory`` package (``schema/trajectory-v1.schema.json``,
also reachable through the package's ``./schema`` export). It is written out
rather than fed to a JSON Schema engine because neither ``jsonschema`` nor
``fastjsonschema`` is a declared Lerim dependency — both arrive transitively —
and the schema is a closed union of five record shapes, small enough that
restating it costs less than depending on something undeclared. Restating it
also buys the per-line, per-field error messages a folder owner needs to fix a
broken cleaner script. Any upstream schema change must be mirrored here.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from lerim.adapters.common import compute_file_hash, parse_timestamp
from lerim.sessions.types import SessionRecord

CUSTOM_AGENT_TYPE = "custom"

# Catalog summaries feed full-text search. Native sessions summarize as their
# opening user turn; custom traces follow the same rule so search behaves the
# same for both.
SUMMARY_MAX_CHARACTERS = 300

_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})$"
)
_CONVERSATION_ROLES = frozenset({"user", "assistant"})
_TEXT_RECORD_KEYS = frozenset({"role", "content", "timestamp"})
_META_KEYS = frozenset({"role", "source", "cwd", "git_branch", "model"})
_ASSISTANT_KEYS = frozenset({"role", "content", "timestamp", "tool_calls"})
_TOOL_KEYS = frozenset({"role", "tool_call_id", "content", "timestamp"})
_TOOL_CALL_KEYS = frozenset({"id", "name", "args"})


def iter_custom_trace_sessions(
    *,
    project_name: str,
    project_path: Path,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[SessionRecord]:
    """Return custom trace sessions from one registered custom project folder."""
    root = project_path.expanduser().resolve()
    if not root.is_dir():
        return []

    sessions: list[SessionRecord] = []
    for trace_path in sorted(root.rglob("*.jsonl")):
        if not trace_path.is_file():
            continue
        try:
            records = _load_trajectory(trace_path)
        except (ValueError, OSError) as exc:
            logger.warning(
                "custom trace skipped | project={} path={} error={}",
                project_name,
                trace_path,
                str(exc),
            )
            continue

        started_at = _first_timestamp(records)
        parsed_started_at = parse_timestamp(started_at)
        if not _in_window(parsed_started_at, start=start, end=end):
            continue

        rel_path = trace_path.relative_to(root)
        run_id = _custom_run_id(root=root, relative_path=rel_path)
        sessions.append(
            SessionRecord(
                run_id=run_id,
                agent_type=CUSTOM_AGENT_TYPE,
                session_path=str(trace_path),
                start_time=started_at,
                repo_path=str(root),
                repo_name=project_name,
                status="completed",
                message_count=sum(
                    1 for record in records if record["role"] in _CONVERSATION_ROLES
                ),
                tool_call_count=sum(
                    len(record.get("tool_calls") or ()) for record in records
                ),
                summaries=_summaries(records),
                content_hash=compute_file_hash(trace_path),
            )
        )
    return sessions


def _load_trajectory(path: Path) -> list[dict[str, Any]]:
    """Load one trajectory-v1 JSONL file, rejecting anything off-format."""
    records: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_number} is not JSON") from exc
        if not isinstance(record, dict):
            raise ValueError(f"line {line_number} is not a JSON object")
        problem = _validate_record(record, is_first=not records)
        if problem is not None:
            raise ValueError(f"line {line_number} {problem}")
        records.append(record)
    if not records:
        raise ValueError("trace file is empty")
    return records


def _validate_record(record: dict[str, Any], *, is_first: bool) -> str | None:
    """Return why a record is not valid trajectory-v1, or ``None`` if it is.

    ``is_first`` enforces the one rule the format documents but the schema
    cannot express per record: record 0 is the only ``meta`` record.
    """
    role = record.get("role")
    if role == "meta":
        if not is_first:
            return "has a meta record after record 0"
        return _validate_meta(record)
    if is_first:
        return f"is role {role!r}; record 0 must be meta"
    if role in {"user", "reasoning"}:
        return _validate_text_record(record, _TEXT_RECORD_KEYS)
    if role == "assistant":
        return _validate_assistant(record)
    if role == "tool":
        return _validate_tool(record)
    return f"has unknown role {role!r}"


def _validate_meta(record: dict[str, Any]) -> str | None:
    """Validate a ``meta`` record."""
    extra = _unknown_keys(record, _META_KEYS)
    if extra:
        return extra
    if not _is_non_empty_string(record.get("source")):
        return "meta needs a non-empty string source"
    for key in ("cwd", "git_branch", "model"):
        if key in record and not isinstance(record[key], str):
            return f"meta {key} must be a string"
    return None


def _validate_text_record(record: dict[str, Any], allowed: frozenset[str]) -> str | None:
    """Validate a record whose payload is a plain string plus a timestamp."""
    extra = _unknown_keys(record, allowed)
    if extra:
        return extra
    if not isinstance(record.get("content"), str):
        return f"{record['role']} content must be a string"
    return _validate_timestamp(record)


def _validate_assistant(record: dict[str, Any]) -> str | None:
    """Validate an ``assistant`` record and its optional tool calls."""
    extra = _unknown_keys(record, _ASSISTANT_KEYS)
    if extra:
        return extra
    timestamp_problem = _validate_timestamp(record)
    if timestamp_problem is not None:
        return timestamp_problem
    content = record.get("content")
    if "tool_calls" not in record:
        if not _is_non_empty_string(content):
            return "assistant without tool_calls needs non-empty string content"
        return None
    if content is not None:
        return "assistant with tool_calls must have null content"
    calls = record["tool_calls"]
    if not isinstance(calls, list) or not calls:
        return "assistant tool_calls must be a non-empty array"
    for call in calls:
        if not isinstance(call, dict):
            return "each tool_calls entry must be an object"
        extra = _unknown_keys(call, _TOOL_CALL_KEYS, label="tool_calls entry")
        if extra:
            return extra
        if not _is_non_empty_string(call.get("id")):
            return "each tool_calls entry needs a non-empty id"
        if not _is_non_empty_string(call.get("name")):
            return "each tool_calls entry needs a non-empty name"
        if not isinstance(call.get("args"), str):
            return "each tool_calls entry needs args as a JSON string, not an object"
    return None


def _validate_tool(record: dict[str, Any]) -> str | None:
    """Validate a ``tool`` result record."""
    extra = _unknown_keys(record, _TOOL_KEYS)
    if extra:
        return extra
    if not _is_non_empty_string(record.get("tool_call_id")):
        return "tool needs a non-empty tool_call_id"
    if not isinstance(record.get("content"), str):
        return "tool content must be a string"
    return _validate_timestamp(record)


def _validate_timestamp(record: dict[str, Any]) -> str | None:
    """Validate the ISO-8601 timestamp every non-meta record must carry."""
    timestamp = record.get("timestamp")
    if not isinstance(timestamp, str) or not _TIMESTAMP_RE.match(timestamp):
        return (
            f"{record['role']} needs an ISO-8601 timestamp "
            "like 2026-05-16T09:00:00Z"
        )
    return None


def _unknown_keys(
    record: dict[str, Any], allowed: frozenset[str], *, label: str = "record"
) -> str | None:
    """Return a message naming keys the schema does not allow, if any."""
    extra = sorted(set(record) - allowed)
    if not extra:
        return None
    return f"{label} has unsupported keys: {', '.join(extra)}"


def _is_non_empty_string(value: Any) -> bool:
    """Return whether a value is a string with at least one character."""
    return isinstance(value, str) and bool(value)


def _first_timestamp(records: list[dict[str, Any]]) -> str | None:
    """Return the first timestamp in the trace, skipping the meta record."""
    for record in records:
        timestamp = record.get("timestamp")
        if isinstance(timestamp, str):
            return timestamp
    return None


def _in_window(
    started_at: datetime | None,
    *,
    start: datetime | None,
    end: datetime | None,
) -> bool:
    """Return whether a session timestamp overlaps the ingest window."""
    if started_at is None:
        return True
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    if start is not None and started_at < start:
        return False
    if end is not None and started_at > end:
        return False
    return True


def _custom_run_id(*, root: Path, relative_path: Path) -> str:
    """Return a stable custom-session id from project root and relative file path."""
    digest = hashlib.sha1(
        f"{root}\0{relative_path.as_posix()}".encode("utf-8")
    ).hexdigest()[:20]
    return f"custom_{digest}"


def _summaries(records: list[dict[str, Any]]) -> list[str]:
    """Return the opening user turn as the session's catalog summary."""
    for record in records:
        if record["role"] != "user":
            continue
        text = " ".join(record["content"].split())
        if text:
            return [text[:SUMMARY_MAX_CHARACTERS]]
    return []


if __name__ == "__main__":
    """Validate the shipped example traces against this module's schema check."""
    examples = sorted(
        (Path(__file__).resolve().parents[3] / "docs" / "examples" / "traces").glob(
            "*.jsonl"
        )
    )
    assert examples, "no example traces found"
    for example in examples:
        records = _load_trajectory(example)
        print(f"{example.name}: {len(records)} records, summary={_summaries(records)}")

    bad = {"role": "assistant", "content": "hi", "tool_calls": [], "timestamp": "x"}
    assert _validate_record(bad, is_first=False) is not None
    assert _validate_record({"role": "meta"}, is_first=True) is not None
    assert _validate_record({"role": "meta", "source": "x"}, is_first=True) is None
