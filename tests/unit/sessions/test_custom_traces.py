"""Unit tests for custom clean-trace folder discovery.

Custom folders are the one ingest path where Lerim does not normalize anything:
it accepts the user's files as-is. That makes the format check the whole
contract, so these tests hold the module's hand-written schema restatement to
the real ``trajectory-v1.schema.json`` shipped by the pinned npm package.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from lerim.sessions.custom_traces import _validate_record, iter_custom_trace_sessions

META = '{"role":"meta","source":"support-agent","cwd":"/srv/app"}'


def _record(role: str, content: str, timestamp: str = "2026-05-16T09:00:00Z") -> str:
    """Serialize one trajectory-v1 text record the way a clean folder holds it."""
    return json.dumps({"role": role, "content": content, "timestamp": timestamp})


def _write_trace(path, *lines: str) -> None:
    """Write a trajectory-v1 trace file, meta record first."""
    path.write_text("\n".join([META, *lines]) + "\n", encoding="utf-8")


def test_iter_custom_trace_sessions_reads_clean_jsonl_without_copying(tmp_path) -> None:
    """A valid custom JSONL file is indexed from its original folder."""
    trace = tmp_path / "run-1.jsonl"
    _write_trace(
        trace,
        _record(
            "user", "Renewal customer asked for legal approval.", "2026-05-16T09:00:00Z"
        ),
        _record(
            "assistant",
            "Escalated to legal with the contract note.",
            "2026-05-16T09:01:00Z",
        ),
    )

    sessions = iter_custom_trace_sessions(project_name="support", project_path=tmp_path)

    assert len(sessions) == 1
    session = sessions[0]
    assert session.agent_type == "custom"
    assert session.repo_path == str(tmp_path.resolve())
    assert session.session_path == str(trace.resolve())
    assert session.run_id.startswith("custom_")
    assert session.message_count == 2
    assert session.start_time == "2026-05-16T09:00:00Z"
    assert session.summaries == ["Renewal customer asked for legal approval."]
    assert session.content_hash


def test_iter_custom_trace_sessions_counts_tool_calls(tmp_path) -> None:
    """Tool calls are counted from the records, not guessed from the file."""
    _write_trace(
        tmp_path / "run-1.jsonl",
        _record("user", "check the invoice"),
        json.dumps(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_1", "name": "read", "args": '{"path":"inv.txt"}'},
                    {"id": "call_2", "name": "grep", "args": "{}"},
                ],
                "timestamp": "2026-05-16T09:01:00Z",
            }
        ),
        json.dumps(
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "$42.00",
                "timestamp": "2026-05-16T09:01:05Z",
            }
        ),
    )

    [session] = iter_custom_trace_sessions(project_name="support", project_path=tmp_path)

    assert session.tool_call_count == 2
    # Turns are user + assistant records, matching the harness path's rule, so
    # the tool *result* is machine output and does not count as a turn.
    assert session.message_count == 2


def test_iter_custom_trace_sessions_skips_a_pre_trajectory_trace(tmp_path) -> None:
    """A folder still holding the retired {"type","message"} shape is rejected."""
    (tmp_path / "bad.jsonl").write_text(
        json.dumps(
            {
                "type": "user",
                "message": {"role": "user", "content": "raw"},
                "timestamp": "2026-05-16T09:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert iter_custom_trace_sessions(project_name="support", project_path=tmp_path) == []


def test_iter_custom_trace_sessions_requires_a_leading_meta_record(tmp_path) -> None:
    """Without meta first, every ``line:<N>`` citation would be off by one."""
    (tmp_path / "bad.jsonl").write_text(
        _record("user", "no meta record here") + "\n", encoding="utf-8"
    )

    assert iter_custom_trace_sessions(project_name="support", project_path=tmp_path) == []


def test_iter_custom_trace_sessions_reports_why_a_trace_was_skipped(
    tmp_path, warning_log
) -> None:
    """A folder owner fixing a cleaner script needs the line and the reason."""
    _write_trace(
        tmp_path / "bad.jsonl",
        json.dumps({"role": "user", "content": "no timestamp"}),
    )

    assert iter_custom_trace_sessions(project_name="support", project_path=tmp_path) == []
    assert any("line 2" in message and "ISO-8601" in message for message in warning_log)


def test_iter_custom_trace_sessions_keeps_going_after_one_bad_file(tmp_path) -> None:
    """One malformed trace never costs the folder its other sessions."""
    (tmp_path / "a-bad.jsonl").write_text("{not json\n", encoding="utf-8")
    _write_trace(tmp_path / "b-good.jsonl", _record("user", "still indexed"))

    [session] = iter_custom_trace_sessions(project_name="support", project_path=tmp_path)

    assert session.session_path == str((tmp_path / "b-good.jsonl").resolve())


def test_iter_custom_trace_sessions_applies_time_window(tmp_path) -> None:
    """The custom scanner respects ingest window bounds."""
    _write_trace(tmp_path / "old.jsonl", _record("user", "old", "2026-05-01T09:00:00Z"))
    _write_trace(tmp_path / "new.jsonl", _record("user", "new", "2026-05-16T09:00:00Z"))

    sessions = iter_custom_trace_sessions(
        project_name="support",
        project_path=tmp_path,
        start=datetime(2026, 5, 10, tzinfo=timezone.utc),
        end=datetime(2026, 5, 17, tzinfo=timezone.utc),
    )

    assert [item.session_path for item in sessions] == [
        str((tmp_path / "new.jsonl").resolve())
    ]


# One record per shape the format allows, plus the ways a hand-written cleaner
# gets each one wrong. ``valid`` is what the real JSON Schema says, and is
# asserted against both the schema and this module's restatement of it.
SCHEMA_CASES = [
    ("meta", {"role": "meta", "source": "support-agent"}, True),
    (
        "meta-with-optionals",
        {"role": "meta", "source": "codex", "cwd": "/srv", "git_branch": "main"},
        True,
    ),
    ("meta-without-source", {"role": "meta"}, False),
    ("meta-empty-source", {"role": "meta", "source": ""}, False),
    (
        "user",
        {"role": "user", "content": "hello", "timestamp": "2026-05-16T09:00:00Z"},
        True,
    ),
    (
        "reasoning",
        {"role": "reasoning", "content": "thinking", "timestamp": "2026-05-16T09:00:00Z"},
        True,
    ),
    (
        "assistant-text",
        {"role": "assistant", "content": "done", "timestamp": "2026-05-16T09:00:00Z"},
        True,
    ),
    (
        "assistant-empty-content",
        {"role": "assistant", "content": "", "timestamp": "2026-05-16T09:00:00Z"},
        False,
    ),
    (
        "assistant-tool-call",
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c1", "name": "bash", "args": "{}"}],
            "timestamp": "2026-05-16T09:00:00Z",
        },
        True,
    ),
    (
        "assistant-tool-call-with-content",
        {
            "role": "assistant",
            "content": "narration",
            "tool_calls": [{"id": "c1", "name": "bash", "args": "{}"}],
            "timestamp": "2026-05-16T09:00:00Z",
        },
        False,
    ),
    (
        "assistant-tool-call-object-args",
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c1", "name": "bash", "args": {"cmd": "ls"}}],
            "timestamp": "2026-05-16T09:00:00Z",
        },
        False,
    ),
    (
        "assistant-empty-tool-calls",
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [],
            "timestamp": "2026-05-16T09:00:00Z",
        },
        False,
    ),
    (
        "tool",
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "ok",
            "timestamp": "2026-05-16T09:00:00Z",
        },
        True,
    ),
    (
        "tool-without-call-id",
        {"role": "tool", "content": "ok", "timestamp": "2026-05-16T09:00:00Z"},
        False,
    ),
    (
        "unknown-role",
        {"role": "system", "content": "hi", "timestamp": "2026-05-16T09:00:00Z"},
        False,
    ),
    ("user-without-timestamp", {"role": "user", "content": "hi"}, False),
]


@pytest.mark.parametrize(
    ("record", "valid"),
    [pytest.param(record, valid, id=name) for name, record, valid in SCHEMA_CASES],
)
def test_custom_schema_restatement_agrees_with_the_shipped_schema(
    record, valid, trajectory_validator
) -> None:
    """The restated schema must accept and reject exactly what upstream does.

    ``custom_traces`` restates ``trajectory-v1.schema.json`` in Python on
    purpose — no JSON Schema engine is a declared Lerim dependency — so this is
    the test that catches the restatement drifting from the package.
    """
    schema_says_valid = trajectory_validator.is_valid([record])
    problem = _validate_record(record, is_first=record.get("role") == "meta")

    assert schema_says_valid is valid, f"upstream schema disagrees: {record}"
    assert (problem is None) is valid, f"restatement said {problem!r} for {record}"


def test_a_meta_record_after_record_zero_is_rejected(trajectory_validator) -> None:
    """Only record 0 may be meta — a rule the JSON Schema cannot express per record."""
    meta = {"role": "meta", "source": "support-agent"}

    assert trajectory_validator.is_valid([meta])
    assert _validate_record(meta, is_first=False) == "has a meta record after record 0"
