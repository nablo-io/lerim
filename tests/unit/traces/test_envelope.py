"""Tests for generic trace envelope mapping into trajectory-v1.

Generic and custom traces never reach the trajectory library — Lerim maps them
itself. That makes this the second writer of the trace cache format, so these
tests hold it to the same contract as the harness path: valid trajectory-v1,
one compact record per line, meta first.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lerim.traces.envelope import load_generic_trace, write_compact_trace

EXAMPLE_TRACES_DIR = Path(__file__).parents[3] / "docs" / "examples" / "traces"


def test_load_generic_trace_reads_jsonl_events(tmp_path):
    """JSONL events become trajectory-v1 conversation records behind a meta record."""
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
    assert trace.records[0]["role"] == "meta"
    assert [record["role"] for record in trace.records[1:]] == ["user", "assistant"]
    assert trace.records[1]["content"] == "I need help with billing."


def test_load_generic_trace_reads_json_object_messages(tmp_path):
    """A JSON object with a messages list is mapped as conversation records."""
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

    assert [record["content"] for record in trace.records[1:]] == [
        "find flights",
        "found options",
    ]


def test_load_generic_trace_maps_tool_calls_and_results(tmp_path):
    """A narrated tool call splits into prose plus a linked call/result pair."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "role": "assistant",
                        "content": "Let me read the invoice.",
                        "tool_calls": [
                            {"id": "call_7", "name": "read", "args": {"path": "inv.txt"}}
                        ],
                    }
                ),
                json.dumps(
                    {"role": "tool_result", "tool_call_id": "call_7", "content": "$42.00"}
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    trace = load_generic_trace(trace_path)

    prose, call, result = trace.records[1:]
    assert prose == {
        "role": "assistant",
        "content": "Let me read the invoice.",
        "timestamp": prose["timestamp"],
    }
    assert call["content"] is None
    assert call["tool_calls"] == [
        {"id": "call_7", "name": "read", "args": '{"path":"inv.txt"}'}
    ]
    assert result["role"] == "tool"
    assert result["tool_call_id"] == "call_7"


def test_load_generic_trace_uses_content_based_trace_id(tmp_path):
    """Equivalent payloads keep the same trace id across file paths."""
    first = tmp_path / "first.jsonl"
    second = tmp_path / "nested" / "second.jsonl"
    second.parent.mkdir()
    payload = '{"role":"user","content":"stable event"}\n'
    first.write_text(payload, encoding="utf-8")
    second.write_text(payload, encoding="utf-8")

    first_trace = load_generic_trace(first)
    second_trace = load_generic_trace(second)

    assert first_trace.trace_id == second_trace.trace_id
    assert first_trace.content_hash == second_trace.content_hash


def test_load_generic_trace_preserves_wrapper_metadata(tmp_path):
    """Wrapper metadata is preserved separately from message records."""
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(
        json.dumps(
            {
                "session_id": "sess-wrapper",
                "source_name": "support-bot",
                "metadata": {"cwd": "/tmp/repo", "ticket": "T-123"},
                "messages": [{"role": "user", "content": "help"}],
            }
        ),
        encoding="utf-8",
    )

    trace = load_generic_trace(trace_path)

    assert trace.session_id == "sess-wrapper"
    assert trace.metadata == {
        "cwd": "/tmp/repo",
        "ticket": "T-123",
        "session_id": "sess-wrapper",
        "source_name": "support-bot",
    }
    assert trace.records[0] == {
        "role": "meta",
        "source": "support-bot",
        "cwd": "/tmp/repo",
    }


def test_load_generic_trace_round_trips_a_trajectory_v1_file(tmp_path):
    """Re-importing a written trace must not prepend a junk record.

    Every ``line:<N>`` citation in the user's database is an offset into the
    trace cache, so an import that turned the leading meta record into a first
    user message would shift every record down by one.
    """
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        "\n".join(
            [
                '{"role":"meta","source":"support-agent","cwd":"/srv/app"}',
                '{"role":"user","content":"the invoice is wrong"}',
                '{"role":"assistant","content":"I reissued it."}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    trace = load_generic_trace(trace_path)

    assert trace.message_count == 2
    assert trace.records[0] == {
        "role": "meta",
        "source": "support-agent",
        "cwd": "/srv/app",
    }
    assert trace.records[1]["content"] == "the invoice is wrong"


def test_load_generic_trace_wraps_raw_text(tmp_path):
    """Raw text traces are preserved as one user record."""
    trace_path = tmp_path / "trace.txt"
    trace_path.write_text("raw transcript text", encoding="utf-8")

    trace = load_generic_trace(trace_path)

    assert trace.message_count == 1
    assert trace.records[1]["role"] == "user"
    assert trace.records[1]["content"] == "raw transcript text"


def test_load_generic_trace_rejects_empty_file(tmp_path):
    """Empty trace imports fail before the extraction path spends model calls."""
    trace_path = tmp_path / "empty.jsonl"
    trace_path.write_text(" \n\t\n", encoding="utf-8")

    with pytest.raises(ValueError, match="trace file is empty"):
        load_generic_trace(trace_path)


def test_write_compact_trace_outputs_jsonl(tmp_path, assert_valid_trace_file):
    """A generic trace is written as valid, compact, one-record-per-line trajectory-v1."""
    source = tmp_path / "trace.jsonl"
    source.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")
    trace = load_generic_trace(source)
    destination = tmp_path / "normalized" / "trace.jsonl"

    write_compact_trace(trace, destination)

    records = assert_valid_trace_file(destination)
    assert [record["role"] for record in records] == ["meta", "user"]
    assert records[1]["content"] == "hello"


@pytest.mark.parametrize(
    "name",
    [
        "support-agent-run.jsonl",
        "research-analyst-run.jsonl",
        "incident-agent-run.jsonl",
        "compliance-review-run.jsonl",
    ],
)
def test_shipped_example_traces_import_as_valid_trajectory(
    name, tmp_path, assert_valid_trace_file
):
    """The examples in the docs are the ones users import first, so they must pass."""
    write_compact_trace(load_generic_trace(EXAMPLE_TRACES_DIR / name), tmp_path / name)

    assert_valid_trace_file(tmp_path / name)


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085"])
def test_write_compact_trace_keeps_a_line_separator_inside_one_line(
    separator, tmp_path, assert_valid_trace_file
):
    """A Unicode line separator in trace text must not become a second line.

    ``json.dumps(ensure_ascii=False)`` emits U+2028/U+2029/U+0085 raw, and
    ``str.splitlines()`` treats all three as line breaks, so an unescaped one
    would split a record in half and rebind every later ``line:<N>`` citation.
    """
    content = f"before{separator}after"
    source = tmp_path / "trace.jsonl"
    source.write_text(
        json.dumps({"role": "user", "content": content}) + "\n", encoding="utf-8"
    )
    destination = tmp_path / "normalized.jsonl"

    write_compact_trace(load_generic_trace(source), destination)

    records = assert_valid_trace_file(destination)
    assert records[1]["content"] == content
