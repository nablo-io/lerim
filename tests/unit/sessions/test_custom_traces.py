"""Unit tests for custom clean-trace folder discovery."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from lerim.sessions.custom_traces import iter_custom_trace_sessions


def _canonical(role: str, content: str, timestamp: str | None = None) -> str:
    return json.dumps(
        {
            "type": role,
            "message": {"role": role, "content": content},
            "timestamp": timestamp,
        }
    )


def test_iter_custom_trace_sessions_reads_clean_jsonl_without_copying(tmp_path) -> None:
    """A valid custom JSONL file is indexed from its original folder."""
    trace = tmp_path / "run-1.jsonl"
    trace.write_text(
        "\n".join(
            [
                _canonical(
                    "user",
                    "Renewal customer asked for legal approval.",
                    "2026-05-16T09:00:00Z",
                ),
                _canonical(
                    "assistant",
                    "Escalated to legal with the contract note.",
                    "2026-05-16T09:01:00Z",
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    sessions = iter_custom_trace_sessions(project_name="support", project_path=tmp_path)

    assert len(sessions) == 1
    session = sessions[0]
    assert session.agent_type == "custom"
    assert session.repo_path == str(tmp_path.resolve())
    assert session.session_path == str(trace.resolve())
    assert session.run_id.startswith("custom_")
    assert session.message_count == 2
    assert session.content_hash


def test_iter_custom_trace_sessions_skips_invalid_schema(tmp_path) -> None:
    """Custom mode accepts only already-clean canonical JSONL."""
    (tmp_path / "bad.jsonl").write_text(
        '{"role":"user","content":"raw"}\n', encoding="utf-8"
    )

    assert iter_custom_trace_sessions(project_name="support", project_path=tmp_path) == []


def test_iter_custom_trace_sessions_applies_time_window(tmp_path) -> None:
    """The custom scanner respects ingest window bounds."""
    (tmp_path / "old.jsonl").write_text(
        _canonical("user", "old", "2026-05-01T09:00:00Z") + "\n",
        encoding="utf-8",
    )
    (tmp_path / "new.jsonl").write_text(
        _canonical("user", "new", "2026-05-16T09:00:00Z") + "\n",
        encoding="utf-8",
    )

    sessions = iter_custom_trace_sessions(
        project_name="support",
        project_path=tmp_path,
        start=datetime(2026, 5, 10, tzinfo=timezone.utc),
        end=datetime(2026, 5, 17, tzinfo=timezone.utc),
    )

    assert [item.session_path for item in sessions] == [
        str((tmp_path / "new.jsonl").resolve())
    ]
