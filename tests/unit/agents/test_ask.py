"""Tests for the ask agent module."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

from lerim.agents.ask import (
    ASK_SYSTEM_PROMPT,
    AskResult,
    run_ask,
)


class TestAskResult:
    """Tests for AskResult model."""

    def test_model_dump(self):
        result = AskResult(answer="some answer text")
        data = result.model_dump()
        assert data["answer"] == "some answer text"


class TestAskSystemPrompt:
    """Tests for ASK_SYSTEM_PROMPT."""

    def test_non_empty(self):
        assert isinstance(ASK_SYSTEM_PROMPT, str)
        assert len(ASK_SYSTEM_PROMPT.strip()) > 0

    def test_keeps_policy_outside_tool_catalog(self):
        assert "<tools>" not in ASK_SYSTEM_PROMPT
        assert "deterministic counting" in ASK_SYSTEM_PROMPT
        assert "semantic search" in ASK_SYSTEM_PROMPT
        assert "fetch the full current durable records" in ASK_SYSTEM_PROMPT

    def test_mentions_currentness_safety_rules(self):
        assert "current-state" in ASK_SYSTEM_PROMPT
        assert "updated_at" in ASK_SYSTEM_PROMPT
        assert "newer direct active durable support" in ASK_SYSTEM_PROMPT


class TestRunAskSignature:
    """Tests for run_ask function signature."""

    def test_accepts_expected_kwargs(self):
        params = inspect.signature(run_ask).parameters
        expected = {
            "context_db_path",
            "project_identity",
            "project_ids",
            "session_id",
            "model",
            "question",
            "hints",
            "request_limit",
            "return_messages",
        }
        assert set(params.keys()) == expected

    def test_runs_agent_sync(self, tmp_path, monkeypatch):
        model = MagicMock()
        mock_result = MagicMock()
        mock_result.output = AskResult(answer="answer text")
        mock_result.all_messages.return_value = []

        mock_agent = MagicMock()
        mock_agent.run_sync.return_value = mock_result
        monkeypatch.setattr(
            "lerim.agents.ask.build_ask_agent",
            lambda _m: mock_agent,
        )

        from lerim.context.project_identity import ProjectIdentity

        identity = ProjectIdentity(
            project_id="proj_abc",
            project_slug="test",
            repo_path=tmp_path,
        )
        result = run_ask(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=identity,
            project_ids=["proj_abc"],
            session_id="sess_1",
            model=model,
            question="what do we know about X?",
        )
        assert result.answer == "answer text"
