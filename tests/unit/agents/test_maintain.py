"""Tests for the maintain agent module."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

from lerim.agents.maintain import (
    MAINTAIN_SYSTEM_PROMPT,
    MaintainResult,
    run_maintain,
)


class TestMaintainResult:
    """Tests for MaintainResult model."""

    def test_model_dump(self):
        result = MaintainResult(completion_summary="done")
        data = result.model_dump()
        assert "completion_summary" in data
        assert data["completion_summary"] == "done"


class TestMaintainSystemPrompt:
    """Tests for MAINTAIN_SYSTEM_PROMPT."""

    def test_keeps_policy_outside_tool_catalog(self):
        assert "<tools>" not in MAINTAIN_SYSTEM_PROMPT
        assert "Before any archive, revision, or supersession" in MAINTAIN_SYSTEM_PROMPT
        assert "Fetch both rows and supersede" in MAINTAIN_SYSTEM_PROMPT


class TestRunMaintainSignature:
    """Tests for run_maintain function signature."""

    def test_accepts_expected_kwargs(self):
        params = inspect.signature(run_maintain).parameters
        expected = {
            "context_db_path",
            "project_identity",
            "session_id",
            "model",
            "request_limit",
            "return_messages",
        }
        assert set(params.keys()) == expected

    def test_runs_agent_sync(self, tmp_path, monkeypatch):
        model = MagicMock()
        mock_result = MagicMock()
        mock_result.output = MaintainResult(completion_summary="ok")
        mock_result.all_messages.return_value = []

        mock_agent = MagicMock()
        mock_agent.run_sync.return_value = mock_result
        monkeypatch.setattr(
            "lerim.agents.maintain.build_maintain_agent",
            lambda _m: mock_agent,
        )

        from lerim.context.project_identity import ProjectIdentity

        identity = ProjectIdentity(
            project_id="proj_abc",
            project_slug="test",
            repo_path=tmp_path,
        )
        result = run_maintain(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=identity,
            session_id="sess_1",
            model=model,
        )
        assert result.completion_summary == "ok"

    def test_return_messages_true(self, tmp_path, monkeypatch):
        model = MagicMock()
        mock_result = MagicMock()
        mock_result.output = MaintainResult(completion_summary="ok")
        mock_result.all_messages.return_value = ["msg1"]

        mock_agent = MagicMock()
        mock_agent.run_sync.return_value = mock_result
        monkeypatch.setattr(
            "lerim.agents.maintain.build_maintain_agent",
            lambda _m: mock_agent,
        )

        from lerim.context.project_identity import ProjectIdentity

        identity = ProjectIdentity(
            project_id="proj_abc",
            project_slug="test",
            repo_path=tmp_path,
        )
        output, messages = run_maintain(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=identity,
            session_id="sess_1",
            model=model,
            return_messages=True,
        )
        assert output.completion_summary == "ok"
        assert messages == ["msg1"]
