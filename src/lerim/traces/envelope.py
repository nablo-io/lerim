"""Normalize generic agent traces into Lerim's compact JSONL format."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lerim.adapters.common import make_canonical_entry, normalize_timestamp_iso

_EVENT_LIST_FIELDS = ("events", "messages", "trace", "steps", "items")
_CONTENT_FIELDS = ("content", "text", "message", "summary", "observation")
_TIMESTAMP_FIELDS = ("timestamp", "time", "created_at", "started_at", "date")


@dataclass(frozen=True)
class NormalizedTrace:
    """Normalized compact trace data ready for extraction."""

    trace_id: str
    events: tuple[dict[str, Any], ...]
    started_at: str | None
    message_count: int


def load_generic_trace(path: Path) -> NormalizedTrace:
    """Load a JSON/JSONL trace file and return canonical compact events."""
    source = path.expanduser().resolve()
    raw_events = _load_raw_events(source)
    canonical: list[dict[str, Any]] = []
    started_at: str | None = None
    for item in raw_events:
        entry = _canonical_entry(item)
        canonical.append(entry)
        started_at = started_at or entry.get("timestamp")
    if not canonical:
        canonical.append(
            make_canonical_entry(
                "user",
                "user",
                source.read_text(encoding="utf-8", errors="replace")[:8000],
                None,
            )
        )
    trace_id = _trace_id(source, canonical)
    return NormalizedTrace(
        trace_id=trace_id,
        events=tuple(canonical),
        started_at=started_at,
        message_count=len(canonical),
    )


def write_compact_trace(trace: NormalizedTrace, destination: Path) -> Path:
    """Write canonical compact events to destination JSONL."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(event, ensure_ascii=False) for event in trace.events]
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination


def _load_raw_events(path: Path) -> list[dict[str, Any]]:
    """Load structured events from JSON, JSONL, or raw text."""
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        jsonl_events = _load_jsonl_events(text)
        if jsonl_events:
            return jsonl_events
        return [{"role": "user", "content": text}]
    return _events_from_json(parsed)


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


def _event_dict(value: Any) -> dict[str, Any]:
    """Return a dict event, wrapping primitives as content."""
    if isinstance(value, dict):
        return value
    return {"role": "assistant", "content": value}


def _canonical_entry(event: dict[str, Any]) -> dict[str, Any]:
    """Convert one generic event into Lerim's canonical compact shape."""
    role = _canonical_role(event)
    content = _canonical_content(event)
    timestamp = _canonical_timestamp(event)
    return make_canonical_entry(role, role, content, timestamp)


def _canonical_role(event: dict[str, Any]) -> str:
    """Map structured event roles into user/assistant canonical roles."""
    raw = str(event.get("role") or event.get("type") or event.get("actor") or "").strip().lower()
    return "user" if raw in {"user", "human", "customer"} else "assistant"


def _canonical_content(event: dict[str, Any]) -> str | list[dict[str, Any]]:
    """Return compact content text or a structured event block."""
    message = event.get("message")
    if isinstance(message, dict):
        for field_name in _CONTENT_FIELDS:
            value = message.get(field_name)
            if value not in (None, ""):
                return _content_value(value)
    for field_name in _CONTENT_FIELDS:
        value = event.get(field_name)
        if value not in (None, ""):
            return _content_value(value)
    return [{"type": "generic_event", "payload": _compact_payload(event)}]


def _content_value(value: Any) -> str | list[dict[str, Any]]:
    """Normalize content values while preserving structured payloads."""
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return value
    return [{"type": "generic_event", "payload": value}]


def _canonical_timestamp(event: dict[str, Any]) -> str | None:
    """Return the first parseable timestamp from known structured fields."""
    for field_name in _TIMESTAMP_FIELDS:
        value = event.get(field_name)
        parsed = normalize_timestamp_iso(value)
        if parsed:
            return parsed
    return None


def _compact_payload(event: dict[str, Any]) -> dict[str, Any]:
    """Drop empty values from an event payload for compact trace blocks."""
    return {key: value for key, value in event.items() if value not in (None, "", [], {})}


def _trace_id(path: Path, events: list[dict[str, Any]]) -> str:
    """Return a deterministic id from path and normalized event content."""
    import hashlib

    digest = hashlib.sha256()
    digest.update(str(path).encode("utf-8"))
    for event in events:
        digest.update(json.dumps(event, sort_keys=True, ensure_ascii=True).encode("utf-8"))
    return f"trace_{digest.hexdigest()[:12]}"


if __name__ == "__main__":
    """Run a tiny parser smoke check."""
    event = _canonical_entry({"role": "customer", "content": "hello"})
    assert event["type"] == "user"
    assert event["message"]["content"] == "hello"
