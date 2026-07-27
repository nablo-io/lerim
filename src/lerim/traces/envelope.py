"""Map generic agent traces into Lerim's trajectory-v1 record format.

Native harness sessions are normalized by the trajectory library. Generic and
custom traces are not harness formats, so Lerim maps them here into the same
records: a leading ``meta`` record followed by ``user`` / ``reasoning`` /
``assistant`` / ``tool`` records carrying ISO-8601 timestamps. Records are
written one compact JSON object per line, because extracted context records
cite trace evidence as ``line:<N>``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from lerim.adapters.common import normalize_timestamp_iso, write_trajectory_jsonl
from lerim.redaction import redact_text

GENERIC_SOURCE = "generic"
# Mirrors the trajectory library's synthesis anchor so Lerim-mapped traces and
# library-normalized traces agree when a source carries no timestamps at all.
SYNTHETIC_TIMESTAMP_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
SYNTHETIC_TIMESTAMP_STEP = timedelta(seconds=15)

_EVENT_LIST_FIELDS = ("events", "messages", "trace", "steps", "items")
_CONTENT_FIELDS = ("content", "text", "message", "summary", "observation")
_TIMESTAMP_FIELDS = ("timestamp", "time", "created_at", "started_at", "date")
_SESSION_ID_FIELDS = ("session_id", "sessionId", "id", "run_id", "runId")
_TOOL_CALL_ID_FIELDS = ("tool_call_id", "toolCallId", "tool_use_id", "call_id")
_USER_ROLES = frozenset({"user", "human", "customer"})
_REASONING_ROLES = frozenset({"reasoning", "thinking", "thought"})
_TOOL_ROLES = frozenset({"tool", "tool_result", "function_result", "tool_output"})
_METADATA_FIELDS = (
    "metadata",
    "source",
    "source_name",
    "source_profile",
    "session_id",
    "sessionId",
    "id",
    "run_id",
    "runId",
    "started_at",
    "created_at",
    "cwd",
    "repo_path",
    "scope",
    "scope_type",
    "scope_label",
)


@dataclass(frozen=True)
class NormalizedTrace:
    """One generic trace mapped into trajectory-v1 records."""

    trace_id: str
    records: tuple[dict[str, Any], ...]
    started_at: str | None
    message_count: int
    content_hash: str
    session_id: str | None = None
    metadata: dict[str, Any] | None = None


def load_generic_trace(path: Path, *, source_name: str | None = None) -> NormalizedTrace:
    """Load a JSON/JSONL/text trace file and return trajectory-v1 records.

    A trace that is already trajectory-v1 — every file Lerim writes, and the
    shipped examples — round-trips: its leading ``meta`` record is read as
    metadata rather than mapped into the conversation, so re-importing one does
    not prepend a junk record and shift every ``line:<N>`` citation by one.
    """
    source = path.expanduser().resolve()
    raw_events, metadata = _load_raw_trace(source)
    conversation: list[dict[str, Any]] = []
    anchors: list[str | None] = []
    for event in raw_events:
        if _is_meta_event(event):
            declared = _compact_payload(event)
            declared.pop("role", None)
            metadata.update(declared)
            continue
        for record in _conversation_records(event):
            conversation.append(record)
            anchors.append(_event_timestamp(event))
    if not conversation:
        conversation = [
            {
                "role": "user",
                "content": source.read_text(encoding="utf-8", errors="replace")[:8000],
            }
        ]
        anchors = [None]
    _apply_timestamps(conversation, anchors)
    records = [_meta_record(metadata, source_name), *conversation]
    content_hash = _content_hash(records)
    return NormalizedTrace(
        trace_id=f"trace_{content_hash[:12]}",
        records=tuple(records),
        started_at=conversation[0]["timestamp"],
        message_count=len(conversation),
        content_hash=content_hash,
        session_id=_metadata_session_id(metadata),
        metadata=metadata,
    )


def write_compact_trace(trace: NormalizedTrace, destination: Path) -> Path:
    """Write one redacted trajectory-v1 record per line, compact and unindented."""
    return write_trajectory_jsonl(
        destination, [_redacted(record) for record in trace.records]
    )


def _redacted(value: Any) -> Any:
    """Scrub secrets from every string in a record without breaking its structure."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {key: _redacted(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redacted(item) for item in value]
    return value


def _meta_record(metadata: dict[str, Any], source_name: str | None) -> dict[str, Any]:
    """Build the leading meta record that opens every trajectory-v1 trace."""
    record: dict[str, Any] = {
        "role": "meta",
        "source": _text(source_name)
        or _text(metadata.get("source"))
        or _text(metadata.get("source_name"))
        or GENERIC_SOURCE,
    }
    cwd = _text(metadata.get("cwd")) or _text(metadata.get("repo_path"))
    if cwd:
        record["cwd"] = cwd
    for field in ("git_branch", "model"):
        value = _text(metadata.get(field))
        if value:
            record[field] = value
    return record


def _is_meta_event(event: dict[str, Any]) -> bool:
    """Return whether an event is a trajectory-v1 meta record, not a message."""
    return str(event.get("role") or "").strip().lower() == "meta"


def _conversation_records(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Map one generic event onto its user/reasoning/assistant/tool records.

    An event that carries both prose and tool calls becomes two records, the
    way the trajectory library splits them, because trajectory-v1 requires
    ``content: null`` on a tool-call record. Returning one record here would
    delete the sentence explaining why the call was made.
    """
    raw_role = str(event.get("role") or event.get("type") or event.get("actor") or "")
    raw_role = raw_role.strip().lower()
    tool_calls = _tool_calls(event)
    if tool_calls:
        # Only text the event actually declares: the whole-payload fallback
        # would turn every bare tool call into a JSON blob of itself.
        prose = _declared_content_text(event)
        return [
            *([{"role": "assistant", "content": prose}] if prose else []),
            {"role": "assistant", "content": None, "tool_calls": tool_calls},
        ]
    content = _content_text(event)
    if raw_role in _USER_ROLES:
        return [{"role": "user", "content": content}]
    if raw_role in _REASONING_ROLES:
        return [{"role": "reasoning", "content": content}]
    tool_call_id = _tool_call_id(event)
    if raw_role in _TOOL_ROLES and tool_call_id:
        return [{"role": "tool", "tool_call_id": tool_call_id, "content": content}]
    # An assistant record without tool calls must carry non-empty content.
    return [{"role": "assistant", "content": content}] if content else []


def _tool_calls(event: dict[str, Any]) -> list[dict[str, str]]:
    """Normalize declared tool calls into trajectory-v1 id/name/args triples."""
    raw_calls = event.get("tool_calls") or event.get("toolCalls")
    if not isinstance(raw_calls, list):
        return []
    calls: list[dict[str, str]] = []
    for index, raw_call in enumerate(raw_calls):
        if not isinstance(raw_call, dict):
            continue
        function = raw_call.get("function")
        function = function if isinstance(function, dict) else {}
        name = _text(raw_call.get("name")) or _text(function.get("name"))
        if not name:
            continue
        args = raw_call.get("args")
        if args is None:
            args = raw_call.get("arguments", function.get("arguments"))
        calls.append(
            {
                "id": _text(raw_call.get("id")) or f"call_{index}",
                "name": name,
                "args": args if isinstance(args, str) else _json_text(args or {}),
            }
        )
    return calls


def _tool_call_id(event: dict[str, Any]) -> str:
    """Return the tool-call id a tool result links back to."""
    for field in _TOOL_CALL_ID_FIELDS:
        value = _text(event.get(field))
        if value:
            return value
    return ""


def _content_text(event: dict[str, Any]) -> str:
    """Return record content as text, serializing structured payloads compactly."""
    declared = _declared_content_text(event)
    if declared:
        return declared
    payload = _compact_payload(event)
    return _json_text(payload) if payload else ""


def _declared_content_text(event: dict[str, Any]) -> str:
    """Return the text an event states in a content field, or empty when it states none."""
    message = event.get("message")
    if isinstance(message, dict):
        for field in _CONTENT_FIELDS:
            value = message.get(field)
            if value not in (None, ""):
                return value if isinstance(value, str) else _json_text(value)
    for field in _CONTENT_FIELDS:
        value = event.get(field)
        if value not in (None, ""):
            return value if isinstance(value, str) else _json_text(value)
    return ""


def _apply_timestamps(records: list[dict[str, Any]], anchors: list[str | None]) -> None:
    """Stamp every record, carrying forward source timestamps where they exist."""
    known = [anchor for anchor in anchors[: len(records)] if anchor]
    if not known:
        for index, record in enumerate(records):
            moment = SYNTHETIC_TIMESTAMP_BASE + SYNTHETIC_TIMESTAMP_STEP * index
            record["timestamp"] = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
        return
    current = known[0]
    for index, record in enumerate(records):
        anchor = anchors[index] if index < len(anchors) else None
        current = anchor or current
        record["timestamp"] = current


def _event_timestamp(event: dict[str, Any]) -> str | None:
    """Return the first parseable timestamp from known structured fields."""
    for field in _TIMESTAMP_FIELDS:
        parsed = normalize_timestamp_iso(event.get(field))
        if parsed:
            return parsed
    return None


def _load_raw_trace(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load structured events and wrapper metadata from JSON, JSONL, or text."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        raise ValueError(f"trace file is empty: {path}")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        jsonl_events = _load_jsonl_events(text)
        if jsonl_events:
            return jsonl_events, {}
        return [{"role": "user", "content": text}], {}
    return _events_from_json(parsed), _metadata_from_json(parsed)


def _load_jsonl_events(text: str) -> list[dict[str, Any]]:
    """Parse line-delimited JSON objects when every non-empty line is JSON."""
    rows: list[dict[str, Any]] = []
    saw_line = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        saw_line = True
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return []
        if isinstance(payload, dict):
            rows.append(payload)
        else:
            rows.append({"role": "assistant", "content": payload})
    return rows if saw_line else []


def _events_from_json(value: Any) -> list[dict[str, Any]]:
    """Extract event dictionaries from a JSON value."""
    if isinstance(value, list):
        return [_event_dict(item) for item in value]
    if isinstance(value, dict):
        for field_name in _EVENT_LIST_FIELDS:
            events = value.get(field_name)
            if isinstance(events, list):
                return [_event_dict(item) for item in events]
        return [_event_dict(value)]
    return [{"role": "assistant", "content": value}]


def _metadata_from_json(value: Any) -> dict[str, Any]:
    """Extract top-level wrapper metadata from a JSON trace payload."""
    if not isinstance(value, dict):
        return {}
    metadata: dict[str, Any] = {}
    nested = value.get("metadata")
    if isinstance(nested, dict):
        metadata.update(_compact_payload(nested))
    for field_name in _METADATA_FIELDS:
        if field_name == "metadata":
            continue
        field_value = value.get(field_name)
        if field_value not in (None, "", [], {}):
            metadata[field_name] = field_value
    return metadata


def _metadata_session_id(metadata: dict[str, Any]) -> str | None:
    """Return a stable session id declared by trace wrapper metadata."""
    for field_name in _SESSION_ID_FIELDS:
        text = _text(metadata.get(field_name))
        if text:
            return text
    return None


def _event_dict(value: Any) -> dict[str, Any]:
    """Return a dict event, wrapping primitives as content."""
    if isinstance(value, dict):
        return value
    return {"role": "assistant", "content": value}


def _compact_payload(event: dict[str, Any]) -> dict[str, Any]:
    """Drop empty values from an event payload for compact trace content."""
    return {key: value for key, value in event.items() if value not in (None, "", [], {})}


def _json_text(value: Any) -> str:
    """Serialize a structured payload as deterministic single-line JSON text."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _text(value: Any) -> str:
    """Return a stripped string for an optional scalar value."""
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return ""
    return str(value).strip()


def _content_hash(records: list[dict[str, Any]]) -> str:
    """Return a deterministic hash over the mapped records."""
    digest = hashlib.sha256()
    for record in records:
        digest.update(json.dumps(record, sort_keys=True, ensure_ascii=True).encode("utf-8"))
    return digest.hexdigest()


if __name__ == "__main__":
    """Run a tiny parser smoke check."""
    assert _conversation_records({"role": "customer", "content": "hello"}) == [
        {"role": "user", "content": "hello"}
    ]
    bare_call = _conversation_records(
        {"role": "assistant", "tool_calls": [{"id": "c1", "name": "Bash", "args": {"cmd": "ls"}}]}
    )
    assert len(bare_call) == 1, bare_call
    assert bare_call[0]["content"] is None
    assert bare_call[0]["tool_calls"][0]["args"] == '{"cmd":"ls"}'

    # Prose alongside a tool call becomes two records, never a dropped sentence.
    narrated = _conversation_records(
        {
            "role": "assistant",
            "content": "I'll grep the log for you.",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "bash"}}],
        }
    )
    assert [record["content"] for record in narrated] == ["I'll grep the log for you.", None]
    assert _is_meta_event({"role": "meta", "source": "support-agent"})
