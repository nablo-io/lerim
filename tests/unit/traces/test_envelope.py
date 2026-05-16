"""Tests for generic trace envelope normalization."""

from __future__ import annotations

import json

from lerim.traces.envelope import load_generic_trace, write_compact_trace


def test_load_generic_trace_reads_jsonl_events(tmp_path):
    """JSONL events become canonical compact trace entries."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "role": "customer",
                        "content": "I need help with billing.",
                        "timestamp": "2026-05-15T10:00:00Z",
                    }
                ),
                json.dumps(
                    {
                        "role": "agent",
                        "content": "I checked the invoice.",
                        "timestamp": "2026-05-15T10:01:00Z",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    trace = load_generic_trace(trace_path)

    assert trace.trace_id.startswith("trace_")
    assert trace.started_at == "2026-05-15T10:00:00Z"
    assert trace.message_count == 2
    assert trace.events[0]["type"] == "user"
    assert trace.events[1]["type"] == "assistant"


def test_load_generic_trace_reads_json_object_messages(tmp_path):
    """A JSON object with a messages list is normalized as events."""
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "message": {"content": "find flights"}},
                    {"role": "assistant", "message": {"content": "found options"}},
                ]
            }
        ),
        encoding="utf-8",
    )

    trace = load_generic_trace(trace_path)

    assert [event["message"]["content"] for event in trace.events] == [
        "find flights",
        "found options",
    ]


def test_load_generic_trace_wraps_raw_text(tmp_path):
    """Raw text traces are preserved as one user message."""
    trace_path = tmp_path / "trace.txt"
    trace_path.write_text("raw transcript text", encoding="utf-8")

    trace = load_generic_trace(trace_path)

    assert trace.message_count == 1
    assert trace.events[0]["type"] == "user"
    assert trace.events[0]["message"]["content"] == "raw transcript text"


def test_write_compact_trace_outputs_jsonl(tmp_path):
    """Normalized traces are written as newline-delimited canonical JSON."""
    source = tmp_path / "trace.jsonl"
    source.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")
    trace = load_generic_trace(source)
    destination = tmp_path / "normalized" / "trace.jsonl"

    write_compact_trace(trace, destination)

    lines = destination.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["message"]["content"] == "hello"
