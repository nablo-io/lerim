"""Tests for adapter base data models and protocol."""

from __future__ import annotations

import dataclasses
from datetime import datetime
from pathlib import Path

import pytest

from lerim.adapters.base import SessionRecord, ViewerMessage, ViewerSession


class TestViewerMessage:
    """Tests for ViewerMessage dataclass."""

    def test_construction_with_defaults(self):
        msg = ViewerMessage(role="assistant")
        assert msg.role == "assistant"
        assert msg.content is None
        assert msg.timestamp is None
        assert msg.model is None
        assert msg.tool_name is None
        assert msg.tool_input is None
        assert msg.tool_output is None
        assert msg.meta == {}

    def test_construction_with_all_fields(self):
        msg = ViewerMessage(
            role="tool",
            content="result",
            timestamp="2026-01-01T00:00:00Z",
            model="gpt-4",
            tool_name="read_file",
            tool_input={"path": "/tmp/f"},
            tool_output="file contents",
            meta={"token_count": 50},
        )
        assert msg.role == "tool"
        assert msg.content == "result"
        assert msg.timestamp == "2026-01-01T00:00:00Z"
        assert msg.model == "gpt-4"
        assert msg.tool_name == "read_file"
        assert msg.tool_input == {"path": "/tmp/f"}
        assert msg.tool_output == "file contents"
        assert msg.meta == {"token_count": 50}


class TestViewerSession:
    """Tests for ViewerSession dataclass."""

    def test_construction_with_defaults(self):
        session = ViewerSession(session_id="abc")
        assert session.session_id == "abc"
        assert session.cwd is None
        assert session.git_branch is None
        assert session.messages == []
        assert session.total_input_tokens == 0
        assert session.total_output_tokens == 0
        assert session.meta == {}

    def test_construction_with_messages(self):
        msgs = [
            ViewerMessage(role="user", content="hello"),
            ViewerMessage(role="assistant", content="hi"),
        ]
        session = ViewerSession(
            session_id="abc",
            cwd="/tmp",
            git_branch="main",
            messages=msgs,
            total_input_tokens=100,
            total_output_tokens=50,
        )
        assert len(session.messages) == 2
        assert session.total_input_tokens == 100


class TestSessionRecord:
    """Tests for SessionRecord frozen dataclass."""

    def test_frozen_raises_on_set(self):
        record = SessionRecord(
            run_id="run1",
            agent_type="codex",
            session_path="/tmp/run1.json",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.run_id = "run2"

    def test_default_status_completed(self):
        record = SessionRecord(
            run_id="run1",
            agent_type="codex",
            session_path="/tmp/run1.json",
        )
        assert record.status == "completed"

    def test_default_counts_zero(self):
        record = SessionRecord(
            run_id="run1",
            agent_type="codex",
            session_path="/tmp/run1.json",
        )
        assert record.duration_ms == 0
        assert record.message_count == 0
        assert record.tool_call_count == 0
        assert record.error_count == 0
        assert record.total_tokens == 0

    def test_optional_fields_default_none(self):
        record = SessionRecord(
            run_id="run1",
            agent_type="codex",
            session_path="/tmp/run1.json",
        )
        assert record.start_time is None
        assert record.repo_path is None
        assert record.repo_name is None
        assert record.content_hash is None

    def test_summaries_default_empty_list(self):
        record = SessionRecord(
            run_id="run1",
            agent_type="codex",
            session_path="/tmp/run1.json",
        )
        assert record.summaries == []


class TestAdapterProtocol:
    """Tests for Adapter protocol duck-typing."""

    def test_duck_typing_compatible(self):
        class MyAdapter:
            def default_path(self) -> Path | None:
                return Path("/tmp/traces")

            def count_sessions(self, path: Path) -> int:
                return 42

            def iter_sessions(
                self,
                traces_dir: Path | None = None,
                start: datetime | None = None,
                end: datetime | None = None,
                known_run_ids: set[str] | None = None,
            ) -> list[SessionRecord]:
                return []

        adapter = MyAdapter()
        assert adapter.default_path() == Path("/tmp/traces")
        assert adapter.count_sessions(Path("/tmp")) == 42
        assert adapter.iter_sessions() == []
