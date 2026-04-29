"""Unit tests for the Claude session adapter."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from lerim.adapters.claude import (
    compact_trace,
    count_sessions,
    default_path,
    iter_sessions,
)

FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures" / "traces" / "unit"


def _write_claude_jsonl(path: Path, entries: list[dict]) -> Path:
    """Write Claude-format JSONL entries to a file."""
    with path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")
    return path


def test_iter_sessions_window_filtering(tmp_path):
    """iter_sessions with start/end window only returns sessions within range."""
    _write_claude_jsonl(
        tmp_path / "early.jsonl",
        [
            {
                "type": "user",
                "message": {"content": "hi"},
                "timestamp": "2026-01-01T10:00:00Z",
            },
        ],
    )
    _write_claude_jsonl(
        tmp_path / "late.jsonl",
        [
            {
                "type": "user",
                "message": {"content": "hi"},
                "timestamp": "2026-03-01T10:00:00Z",
            },
        ],
    )
    start = datetime(2026, 2, 1, tzinfo=timezone.utc)
    end = datetime(2026, 2, 28, tzinfo=timezone.utc)
    records = iter_sessions(traces_dir=tmp_path, start=start, end=end)
    assert len(records) == 0  # both outside Feb range


def test_iter_sessions_skips_known_ids(tmp_path):
    """iter_sessions skips sessions whose run_id is already known."""
    # Sessions need >= 6 conversation turns to pass the min-turn filter
    _turns = [
        {
            "type": "user",
            "message": {"content": f"msg {i}"},
            "timestamp": "2026-02-20T10:00:00Z",
        }
        if i % 2 == 0
        else {
            "type": "assistant",
            "message": {"content": f"reply {i}"},
            "timestamp": "2026-02-20T10:00:00Z",
        }
        for i in range(8)
    ]
    _write_claude_jsonl(tmp_path / "known.jsonl", _turns)
    _write_claude_jsonl(tmp_path / "new.jsonl", _turns)
    # Skip "known" by providing its ID
    records = iter_sessions(
        traces_dir=tmp_path,
        known_run_ids={"known"},
    )
    assert len(records) == 1
    assert records[0].run_id == "new"


def test_count_sessions(tmp_path):
    """count_sessions counts non-empty JSONL files in directory."""
    (tmp_path / "a.jsonl").write_text('{"x":1}\n', encoding="utf-8")
    (tmp_path / "b.jsonl").write_text('{"x":2}\n', encoding="utf-8")
    (tmp_path / "empty.jsonl").write_text("", encoding="utf-8")
    assert count_sessions(tmp_path) == 2


def test_default_path():
    """default_path returns ~/.claude/projects/."""
    result = default_path()
    assert result is not None
    assert str(result).endswith(".claude/projects")


# --- compact_trace tests ---


def test_compact_trace_drops_noise_types():
    """compact_trace drops progress/file-history-snapshot/queue-operation/pr-link lines."""
    lines = [
        json.dumps({"type": "progress", "data": "loading"}),
        json.dumps({"type": "user", "message": {"content": "hi"}, "timestamp": "t1"}),
        json.dumps({"type": "file-history-snapshot", "files": []}),
        json.dumps({"type": "queue-operation", "op": "enqueue"}),
        json.dumps({"type": "pr-link", "url": "https://example.com"}),
        json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "hello"}]},
                "timestamp": "t2",
            }
        ),
    ]
    result = compact_trace("\n".join(lines) + "\n")
    parsed = [json.loads(line) for line in result.strip().split("\n")]
    assert len(parsed) == 2
    assert parsed[0]["type"] == "user"
    assert parsed[1]["type"] == "assistant"


def test_compact_trace_strips_metadata_fields():
    """compact_trace keeps only type/message/timestamp, strips everything else."""
    entry = {
        "type": "user",
        "message": {"content": "hello"},
        "timestamp": "2026-01-01T00:00:00Z",
        "parentUuid": "abc",
        "isSidechain": False,
        "userType": "external",
        "cwd": "/home/user",
        "sessionId": "s1",
        "version": "1.0",
        "gitBranch": "main",
        "slug": "test",
        "uuid": "xyz",
        "requestId": "r1",
        "toolUseResult": "x" * 5_000_000,
        "planContent": "some plan",
        "sourceToolAssistantUUID": "a1",
    }
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    assert set(parsed.keys()) == {"type", "message", "timestamp"}
    assert parsed["message"]["content"] == "hello"


def test_compact_trace_clears_tool_result_string():
    """compact_trace replaces tool_result string content with size descriptor."""
    big_content = "x" * 100_000
    entry = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": big_content,
                }
            ]
        },
        "timestamp": "t1",
    }
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    inner = parsed["message"]["content"][0]["content"]
    assert inner == "[cleared: 100000 chars]"


def test_compact_trace_clears_tool_result_list():
    """compact_trace replaces tool_result list content with size descriptor."""
    entry = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": [
                        {"type": "text", "text": "a" * 40_000},
                        {"type": "text", "text": "b" * 40_000},
                    ],
                }
            ]
        },
        "timestamp": "t1",
    }
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    inner = parsed["message"]["content"][0]["content"]
    assert inner == "[cleared: 80000 chars]"


def test_compact_trace_clears_small_tool_results():
    """compact_trace clears ALL tool_result content regardless of size."""
    small_content = "result data"
    entry = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": small_content,
                }
            ]
        },
        "timestamp": "t1",
    }
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    assert (
        parsed["message"]["content"][0]["content"]
        == f"[cleared: {len(small_content)} chars]"
    )


def test_compact_trace_clears_thinking_blocks():
    """compact_trace replaces thinking block text with size descriptor and drops signature."""
    entry = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "thinking", "thinking": "x" * 5000, "signature": "abc123"},
                {"type": "text", "text": "My conclusion"},
            ]
        },
        "timestamp": "t1",
    }
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    thinking_block = parsed["message"]["content"][0]
    assert thinking_block["thinking"] == "[thinking cleared: 5000 chars]"
    assert "signature" not in thinking_block
    text_block = parsed["message"]["content"][1]
    assert text_block["text"] == "My conclusion"


def test_compact_trace_keeps_malformed_lines():
    """compact_trace preserves non-JSON lines as-is."""
    raw = (
        "not-json\n"
        + json.dumps({"type": "user", "message": {"content": "hi"}, "timestamp": "t"})
        + "\n"
    )
    result = compact_trace(raw)
    lines = [line for line in result.strip().split("\n") if line.strip()]
    assert lines[0] == "not-json"
    assert json.loads(lines[1])["type"] == "user"
