"""Unit tests for redaction wiring in lerim.traces.envelope.write_compact_trace.

The trajectory library does not redact, and the generic-import path does not
go through it at all, so this writer has to scrub secrets itself before a trace
lands in the cache that feeds search, the dashboard, and cloud submission.
These tests exercise that wiring rather than the redaction patterns themselves
(covered in tests/unit/test_redaction.py).
"""

from __future__ import annotations

import json

from lerim.traces.envelope import NormalizedTrace, write_compact_trace


def _trace_with_content(content: str) -> NormalizedTrace:
    """Build a minimal trajectory-v1 trace carrying the given user content."""
    records = (
        {"role": "meta", "source": "generic"},
        {"role": "user", "content": content, "timestamp": "2026-01-01T00:00:00Z"},
    )
    return NormalizedTrace(
        trace_id="trace_test",
        records=records,
        started_at="2026-01-01T00:00:00Z",
        message_count=1,
        content_hash="deadbeefcafefeed",
    )


def test_write_compact_trace_redacts_email(tmp_path):
    """An email address embedded in record content is redacted on write."""
    trace = _trace_with_content("please email ops@example.com about this")
    destination = tmp_path / "normalized" / "trace.jsonl"

    write_compact_trace(trace, destination)

    text = destination.read_text(encoding="utf-8")
    assert "ops@example.com" not in text
    assert "[REDACTED:email]" in text


def test_write_compact_trace_redacts_api_key(tmp_path):
    """An OpenAI-style API key embedded in record content is redacted on write."""
    trace = _trace_with_content("here is my key sk-abcdefghijklmnopqrstuvwx0011")
    destination = tmp_path / "trace.jsonl"

    write_compact_trace(trace, destination)

    text = destination.read_text(encoding="utf-8")
    assert "sk-abcdefghijklmnopqrstuvwx0011" not in text
    assert "[REDACTED:api_key]" in text


def test_write_compact_trace_redacts_private_key_block(tmp_path):
    """A private key PEM block embedded in record content is redacted on write."""
    key_block = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA1234567890abcdefghijklmnop\n"
        "-----END RSA PRIVATE KEY-----"
    )
    trace = _trace_with_content(f"rotate this key:\n{key_block}")
    destination = tmp_path / "trace.jsonl"

    write_compact_trace(trace, destination)

    text = destination.read_text(encoding="utf-8")
    assert "MIIEpAIBAAKCAQEA1234567890abcdefghijklmnop" not in text
    assert "[REDACTED:private_key]" in text


def test_write_compact_trace_redacts_tool_call_arguments(tmp_path):
    """Secrets hide in tool arguments as readily as in prose, so args are scrubbed too.

    ``tool_calls[].args`` is a stringified JSON object, which is exactly where a
    curl command carrying an Authorization header ends up.
    """
    trace = NormalizedTrace(
        trace_id="trace_test",
        records=(
            {"role": "meta", "source": "generic"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "bash",
                        "args": json.dumps(
                            {"command": "curl -H 'Authorization: Bearer sk-abcdef0123456789abcdef'"}
                        ),
                    }
                ],
                "timestamp": "2026-01-01T00:00:00Z",
            },
        ),
        started_at="2026-01-01T00:00:00Z",
        message_count=1,
        content_hash="deadbeefcafefeed",
    )
    destination = tmp_path / "trace.jsonl"

    write_compact_trace(trace, destination)

    text = destination.read_text(encoding="utf-8")
    assert "sk-abcdef0123456789abcdef" not in text
    assert "[REDACTED:" in text


def test_write_compact_trace_preserves_normal_content(tmp_path):
    """Record content with no secrets is written through unchanged as valid JSONL."""
    trace = _trace_with_content("let's ship the release notes tomorrow")
    destination = tmp_path / "trace.jsonl"

    write_compact_trace(trace, destination)

    lines = destination.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["content"] == "let's ship the release notes tomorrow"


def test_write_compact_trace_output_stays_valid_jsonl_after_redaction(
    tmp_path, assert_valid_trace_file
):
    """Redaction placeholders keep the file valid trajectory-v1, one record per line."""
    trace = _trace_with_content(
        "contact jane@example.com or use sk-abcdefghijklmnop0000"
    )
    destination = tmp_path / "trace.jsonl"

    write_compact_trace(trace, destination)

    records = assert_valid_trace_file(destination)
    assert "[REDACTED:email]" in records[1]["content"]
    assert "[REDACTED:api_key]" in records[1]["content"]
