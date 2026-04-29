"""Unit tests for the Codex session adapter."""

from __future__ import annotations

import json
from pathlib import Path

from lerim.adapters.codex import (
    _extract_message_text,
    compact_trace,
    count_sessions,
    iter_sessions,
)


def _write_codex_jsonl(path: Path, entries: list[dict]) -> Path:
    """Write Codex-format JSONL entries to a file."""
    with path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")
    return path


def test_extract_message_text_string():
    """String content -> returned as-is."""
    assert _extract_message_text("hello") == "hello"


def test_extract_message_text_list():
    """List content with text items -> concatenated."""
    content = [{"text": "line1"}, {"text": "line2"}]
    result = _extract_message_text(content)
    assert result is not None
    assert "line1" in result
    assert "line2" in result


def test_iter_sessions_enumeration(tmp_path):
    """iter_sessions returns SessionRecords for all JSONL files."""
    _write_codex_jsonl(
        tmp_path / "a.jsonl",
        [
            {"type": "event_msg", "payload": {"type": "user_message", "message": "hi"}},
        ],
    )
    _write_codex_jsonl(
        tmp_path / "b.jsonl",
        [
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "hello"},
            },
        ],
    )
    records = iter_sessions(traces_dir=tmp_path)
    assert len(records) == 2
    run_ids = {r.run_id for r in records}
    assert "a" in run_ids
    assert "b" in run_ids


def test_count_sessions(tmp_path):
    """count_sessions counts non-empty files."""
    (tmp_path / "a.jsonl").write_text('{"x":1}\n', encoding="utf-8")
    (tmp_path / "empty.jsonl").write_text("", encoding="utf-8")
    assert count_sessions(tmp_path) == 1


def test_iter_sessions_skips_known_ids(tmp_path):
    """iter_sessions skips sessions whose run_id is already known."""
    _write_codex_jsonl(
        tmp_path / "stable.jsonl",
        [{"type": "event_msg", "payload": {"type": "user_message", "message": "hi"}}],
    )
    _write_codex_jsonl(
        tmp_path / "new.jsonl",
        [
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "hello"},
            }
        ],
    )
    # Skip "stable" by providing its ID
    records = iter_sessions(
        traces_dir=tmp_path,
        known_run_ids={"stable"},
    )
    assert len(records) == 1
    assert records[0].run_id == "new"


# --- compact_trace tests (canonical schema) ---


def test_compact_trace_drops_turn_context():
    """compact_trace drops turn_context lines entirely."""
    lines = [
        json.dumps({"type": "turn_context", "payload": {"files": ["a.py"]}}),
        json.dumps(
            {
                "type": "response_item",
                "timestamp": "2026-03-20T10:00:00Z",
                "payload": {"type": "message", "role": "user", "content": "hi"},
            }
        ),
    ]
    result = compact_trace("\n".join(lines) + "\n")
    parsed = [json.loads(line) for line in result.strip().split("\n")]
    assert len(parsed) == 1
    assert parsed[0]["type"] == "user"


def test_compact_trace_drops_session_meta():
    """compact_trace drops session_meta lines entirely."""
    entry = {
        "type": "session_meta",
        "payload": {"id": "s1", "cwd": "/tmp", "base_instructions": "x" * 10_000},
    }
    result = compact_trace(json.dumps(entry) + "\n")
    assert result.strip() == ""


def test_compact_trace_preserves_user_event_msg():
    """compact_trace preserves structured user event messages."""
    entry = {
        "type": "event_msg",
        "timestamp": "2026-03-20T10:00:00Z",
        "payload": {"type": "user_message", "message": "hi"},
    }
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    assert parsed["type"] == "user"
    assert parsed["message"]["role"] == "user"
    assert parsed["message"]["content"] == "hi"
    assert parsed["timestamp"] == "2026-03-20T10:00:00Z"


def test_compact_trace_preserves_agent_event_msg():
    """compact_trace preserves structured agent event messages."""
    entry = {
        "type": "event_msg",
        "payload": {"type": "agent_message", "message": "done"},
    }
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    assert parsed["type"] == "assistant"
    assert parsed["message"]["role"] == "assistant"
    assert parsed["message"]["content"] == "done"


def test_compact_trace_clears_function_call_output():
    """compact_trace produces canonical entry with cleared tool_result content."""
    entry = {
        "type": "response_item",
        "timestamp": "2026-03-20T10:00:00Z",
        "payload": {"type": "function_call_output", "output": "x" * 50_000},
    }
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    assert parsed["type"] == "assistant"
    assert parsed["message"]["role"] == "assistant"
    content = parsed["message"]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "tool_result"
    assert content[0]["content"] == "[cleared: 50000 chars]"


def test_compact_trace_function_call_output_idempotent():
    """Already-cleared function_call_output is not re-measured."""
    entry = {
        "type": "response_item",
        "timestamp": "2026-03-20T10:00:00Z",
        "payload": {"type": "function_call_output", "output": "[cleared: 999 chars]"},
    }
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    content = parsed["message"]["content"]
    assert content[0]["content"] == "[cleared: 999 chars]"


def test_compact_trace_drops_reasoning():
    """compact_trace drops reasoning entries entirely."""
    entry = {
        "type": "response_item",
        "payload": {
            "type": "reasoning",
            "content": [{"type": "text", "text": "y" * 8000}],
        },
    }
    result = compact_trace(json.dumps(entry) + "\n")
    assert result.strip() == ""


def test_compact_trace_drops_agent_reasoning():
    """compact_trace drops agent_reasoning event_msg entirely."""
    entry = {
        "type": "event_msg",
        "payload": {"type": "agent_reasoning", "message": "z" * 3000},
    }
    result = compact_trace(json.dumps(entry) + "\n")
    assert result.strip() == ""


def test_compact_trace_transforms_function_call():
    """compact_trace transforms function_call into canonical tool_use entry."""
    entry = {
        "type": "response_item",
        "timestamp": "2026-03-20T10:00:00Z",
        "payload": {
            "type": "function_call",
            "name": "read_file",
            "arguments": '{"path": "/tmp/x.py"}',
        },
    }
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    assert parsed["type"] == "assistant"
    assert parsed["message"]["role"] == "assistant"
    content = parsed["message"]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "tool_use"
    assert content[0]["name"] == "read_file"
    assert content[0]["input"] == '{"path": "/tmp/x.py"}'


def test_compact_trace_transforms_user_message():
    """compact_trace transforms user message into canonical user entry."""
    entry = {
        "type": "response_item",
        "timestamp": "2026-03-20T10:00:00Z",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Hello world"}],
        },
    }
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    assert parsed["type"] == "user"
    assert parsed["message"]["role"] == "user"
    assert parsed["message"]["content"] == "Hello world"
    assert parsed["timestamp"] == "2026-03-20T10:00:00Z"


def test_compact_trace_transforms_assistant_message():
    """compact_trace transforms assistant message and strips think blocks."""
    entry = {
        "type": "response_item",
        "timestamp": "2026-03-20T10:00:00Z",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "output_text", "text": "before <think>secret</think> after"}
            ],
        },
    }
    result = compact_trace(json.dumps(entry) + "\n")
    parsed = json.loads(result.strip())
    assert parsed["type"] == "assistant"
    assert parsed["message"]["role"] == "assistant"
    assert "<think>" not in parsed["message"]["content"]
    assert "[thinking cleared]" in parsed["message"]["content"]
    assert "before" in parsed["message"]["content"]
    assert "after" in parsed["message"]["content"]


def test_compact_trace_drops_developer_message():
    """compact_trace drops developer (system prompt) messages."""
    entry = {
        "type": "response_item",
        "timestamp": "2026-03-20T10:00:00Z",
        "payload": {
            "type": "message",
            "role": "developer",
            "content": "System prompt text",
        },
    }
    result = compact_trace(json.dumps(entry) + "\n")
    assert result.strip() == ""


# --- _extract_message_text edge cases ---


def test_extract_message_text_none():
    """None content returns None."""
    assert _extract_message_text(None) is None


def test_extract_message_text_integer():
    """Integer content returns None (not str or list)."""
    assert _extract_message_text(42) is None


def test_extract_message_text_empty_list():
    """Empty list content returns None."""
    assert _extract_message_text([]) is None


def test_extract_message_text_list_no_text_keys():
    """List of dicts without 'text' keys returns None."""
    content = [{"type": "image", "url": "http://example.com"}]
    assert _extract_message_text(content) is None


def test_extract_message_text_list_mixed():
    """List with mix of dict-with-text and dict-without-text extracts text only."""
    content = [
        {"type": "image", "url": "http://example.com"},
        {"text": "visible text"},
        {"type": "code", "code": "x=1"},
    ]
    result = _extract_message_text(content)
    assert result == "visible text"


def test_extract_message_text_list_with_non_string_text():
    """List entries where text is not a string are skipped."""
    content = [{"text": 123}, {"text": "ok"}]
    result = _extract_message_text(content)
    assert result == "ok"


# --- iter_sessions edge cases ---


def test_iter_sessions_empty_dir(tmp_path):
    """Empty directory returns empty list."""
    records = iter_sessions(traces_dir=tmp_path)
    assert records == []


def test_iter_sessions_nonexistent_dir():
    """Non-existent directory returns empty list."""
    from pathlib import Path

    records = iter_sessions(traces_dir=Path("/tmp/nonexistent_codex_dir_abc"))
    assert records == []


def test_iter_sessions_skips_empty_jsonl(tmp_path):
    """JSONL file with no valid entries is skipped."""
    (tmp_path / "empty.jsonl").write_text("", encoding="utf-8")
    records = iter_sessions(traces_dir=tmp_path)
    assert records == []


def test_iter_sessions_extracts_metadata(tmp_path):
    """iter_sessions extracts repo metadata from session_meta entries."""
    _write_codex_jsonl(
        tmp_path / "sess1.jsonl",
        [
            {
                "type": "session_meta",
                "timestamp": "2026-03-20T10:00:00Z",
                "payload": {
                    "cwd": "/home/user/projects/myrepo",
                    "git": {"branch": "main"},
                },
            },
            {
                "type": "event_msg",
                "timestamp": "2026-03-20T10:00:01Z",
                "payload": {"type": "user_message", "message": "hello"},
            },
        ],
    )
    records = iter_sessions(traces_dir=tmp_path)
    assert len(records) == 1
    assert records[0].repo_path == "/home/user/projects/myrepo"
    assert records[0].repo_name == "main"
    assert records[0].message_count == 1


def test_iter_sessions_counts_tokens(tmp_path):
    """iter_sessions accumulates token counts from token_count events."""
    _write_codex_jsonl(
        tmp_path / "tok.jsonl",
        [
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "hi"},
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 100,
                            "output_tokens": 50,
                            "reasoning_output_tokens": 20,
                        }
                    },
                },
            },
        ],
    )
    records = iter_sessions(traces_dir=tmp_path)
    assert len(records) == 1
    assert records[0].total_tokens == 170


def test_iter_sessions_counts_tool_calls_and_structured_errors(tmp_path):
    """iter_sessions counts tool calls and explicit structured error fields."""
    _write_codex_jsonl(
        tmp_path / "tools.jsonl",
        [
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "do it"},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "read_file",
                    "arguments": "{}",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "output": "Error: file not found",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "output": "tool failed",
                    "is_error": True,
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "write_file",
                    "input": "{}",
                },
            },
        ],
    )
    records = iter_sessions(traces_dir=tmp_path)
    assert len(records) == 1
    assert records[0].tool_call_count == 2
    assert records[0].error_count == 1


def test_iter_sessions_does_not_infer_errors_from_output_text(tmp_path):
    """Plain output text containing error-like words does not affect error_count."""
    _write_codex_jsonl(
        tmp_path / "text-error.jsonl",
        [
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "do it"},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "output": "Error: file not found",
                },
            },
        ],
    )
    records = iter_sessions(traces_dir=tmp_path)
    assert len(records) == 1
    assert records[0].error_count == 0


# --- compact_trace / _clean_entry edge cases ---


def test_compact_trace_no_payload():
    """response_item without valid payload dict is dropped."""
    entry = {"type": "response_item", "payload": "not-a-dict"}
    result = compact_trace(json.dumps(entry) + "\n")
    assert result.strip() == ""


def test_compact_trace_unknown_type_dropped():
    """Entry with unknown type is dropped entirely."""
    entry = {"type": "unknown_type", "data": "something"}
    result = compact_trace(json.dumps(entry) + "\n")
    assert result.strip() == ""


def test_compact_trace_reasoning_dropped():
    """Reasoning with non-list content is dropped entirely."""
    entry = {
        "type": "response_item",
        "payload": {
            "type": "reasoning",
            "content": "plain string reasoning content",
        },
    }
    result = compact_trace(json.dumps(entry) + "\n")
    assert result.strip() == ""


def test_compact_trace_multiple_lines():
    """compact_trace processes multiple JSONL lines: drops non-conversation, keeps canonical."""
    lines = [
        json.dumps({"type": "turn_context", "payload": {}}),
        json.dumps({"type": "session_meta", "payload": {"id": "s1", "base_instructions": "long"}}),
        json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "hi"}}),
        json.dumps({
            "type": "response_item",
            "timestamp": "2026-03-20T10:00:00Z",
            "payload": {"type": "message", "role": "user", "content": "hello"},
        }),
    ]
    result = compact_trace("\n".join(lines) + "\n")
    parsed = [json.loads(line) for line in result.strip().split("\n")]
    # turn_context/session_meta are dropped; structured event and response messages remain.
    assert len(parsed) == 2
    assert parsed[0]["type"] == "user"
    assert parsed[0]["message"]["content"] == "hi"
    assert parsed[1]["type"] == "user"
    assert parsed[1]["message"]["content"] == "hello"
