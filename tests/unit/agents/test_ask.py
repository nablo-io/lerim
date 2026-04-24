"""Tests for the ask agent module."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

from lerim.agents.ask import (
    ASK_SYSTEM_PROMPT,
    AskResult,
    format_ask_hints,
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

    def test_mentions_key_tools(self):
        assert "context_query" in ASK_SYSTEM_PROMPT
        assert "search_records" in ASK_SYSTEM_PROMPT
        assert "fetch_records" in ASK_SYSTEM_PROMPT

    def test_mentions_currentness_safety_rules(self):
        assert "current-state" in ASK_SYSTEM_PROMPT
        assert "updated_at" in ASK_SYSTEM_PROMPT
        assert "newer direct active durable support" in ASK_SYSTEM_PROMPT


class TestFormatAskHints:
    """Tests for format_ask_hints helper."""

    def test_empty_hits_returns_placeholder(self):
        assert format_ask_hints([], []) == "(no pre-fetched hints)"

    def test_single_hit(self):
        hits = [{"kind": "fact", "title": "X depends on Y", "body_preview": "preview"}]
        result = format_ask_hints(hits, [])
        assert "[fact] X depends on Y: preview" in result

    def test_multiple_hits(self):
        hits = [
            {"kind": "decision", "title": "A", "body_preview": "p1"},
            {"kind": "preference", "title": "B", "body_preview": "p2"},
        ]
        result = format_ask_hints(hits, [])
        lines = result.strip().split("\n")
        assert len(lines) == 2

    def test_missing_kind_defaults(self):
        hits = [{"title": "No kind", "body_preview": "preview"}]
        result = format_ask_hints(hits, [])
        assert "[?]" in result

    def test_context_docs_ignored(self):
        hits = [{"kind": "fact", "title": "T", "body_preview": "B"}]
        result = format_ask_hints(hits, [{"some": "doc"}])
        assert "[fact] T: B" in result

    def test_hints_include_currentness_metadata(self):
        hits = [
            {
                "kind": "fact",
                "title": "Current risk",
                "body_preview": "preview",
                "status": "archived",
                "updated_at": "2026-04-23T15:00:00+00:00",
                "superseded_by_record_id": "rec_new",
            }
        ]
        result = format_ask_hints(hits, [])
        assert "status=archived" in result
        assert "updated_at=2026-04-23T15:00:00+00:00" in result
        assert "superseded_by=rec_new" in result


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
