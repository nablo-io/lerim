"""Tests for the context-answerer agent module."""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

from lerim.agents.context_answerer import (
    CONTEXT_ANSWERER_SYSTEM_PROMPT,
    ContextAnswerResult,
    run_context_answerer,
)
from lerim.context import ContextStore
from lerim.context.project_identity import ProjectIdentity


class TestContextAnswerResult:
    """Tests for ContextAnswerResult model."""

    def test_model_dump(self):
        result = ContextAnswerResult(answer="some answer text")
        data = result.model_dump()
        assert data["answer"] == "some answer text"


class TestAnswerSystemPrompt:
    """Tests for CONTEXT_ANSWERER_SYSTEM_PROMPT."""

    def test_non_empty(self):
        assert isinstance(CONTEXT_ANSWERER_SYSTEM_PROMPT, str)
        assert len(CONTEXT_ANSWERER_SYSTEM_PROMPT.strip()) > 0

    def test_keeps_policy_outside_tool_catalog(self):
        assert "<tools>" not in CONTEXT_ANSWERER_SYSTEM_PROMPT
        assert "plans retrieval" in CONTEXT_ANSWERER_SYSTEM_PROMPT
        assert "retrieved records only" in CONTEXT_ANSWERER_SYSTEM_PROMPT


class TestRunAnswerSignature:
    """Tests for run_context_answerer function signature."""

    def test_accepts_expected_kwargs(self):
        params = inspect.signature(run_context_answerer).parameters
        expected = {
            "context_db_path",
            "project_identity",
            "project_ids",
            "session_id",
            "question",
            "config",
            "hints",
            "return_messages",
        }
        assert set(params.keys()) == expected

    def test_runs_baml_planned_retrieval(self, tmp_path, monkeypatch):
        identity = ProjectIdentity(
            project_id="proj_abc",
            project_slug="test",
            repo_path=tmp_path,
        )
        store = ContextStore(tmp_path / "context.sqlite3")
        store.initialize()
        store.register_project(identity)
        store.upsert_session(
            project_id=identity.project_id,
            session_id="seed",
            agent_type="test",
            source_trace_ref="test",
            repo_path=str(tmp_path),
            cwd=str(tmp_path),
            started_at="2026-05-15T00:00:00+00:00",
            model_name="test/model",
            instructions_text=None,
            prompt_text=None,
            metadata={},
        )
        store.create_record(
            project_id=identity.project_id,
            session_id="seed",
            kind="decision",
            title="Use BAML-planned answer",
            body="Answer retrieves context through a structured BAML plan.",
            status="active",
            decision="Use BAML-planned answer retrieval.",
            why="Answer should retrieve through structured store reads before synthesis.",
            change_reason="test",
        )

        class FakeBamlRuntime:
            def PlanContextRetrieval(self, **_kwargs):
                return {
                    "actions": [
                        {
                            "action_type": "count",
                            "kind": "decision",
                            "status": "active",
                            "rationale": "Count active decisions.",
                        }
                    ],
                    "rationale": "Use exact count.",
                }

            def AnswerFromContext(self, **kwargs):
                retrieval = json.loads(kwargs["retrieval_json"])
                count = retrieval["results"][0]["count"]
                return {"answer": f"There is {count} active decision."}

        monkeypatch.setattr(
            "lerim.agents.context_answerer.graph.build_baml_client_for_role",
            lambda **_kwargs: FakeBamlRuntime(),
        )

        result = run_context_answerer(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=identity,
            project_ids=["proj_abc"],
            session_id="sess_1",
            question="what do we know about X?",
        )
        assert result.answer == "There is 1 active decision."

    def test_can_return_debug_events(self, tmp_path, monkeypatch):
        identity = ProjectIdentity(
            project_id="proj_abc",
            project_slug="test",
            repo_path=tmp_path,
        )
        store = ContextStore(tmp_path / "context.sqlite3")
        store.initialize()
        store.register_project(identity)

        class FakeBamlRuntime:
            def PlanContextRetrieval(self, **_kwargs):
                return SimpleNamespace(
                    actions=[
                        {
                            "action_type": "list",
                            "kind": "fact",
                            "limit": 1,
                            "rationale": "List recent facts.",
                        }
                    ],
                    rationale="Use exact listing.",
                )

            def AnswerFromContext(self, **_kwargs):
                return SimpleNamespace(
                    answer="No facts found.",
                    supporting_record_ids=["fabricated_count_result"],
                )

        monkeypatch.setattr(
            "lerim.agents.context_answerer.graph.build_baml_client_for_role",
            lambda **_kwargs: FakeBamlRuntime(),
        )

        result, events = run_context_answerer(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=identity,
            project_ids=["proj_abc"],
            session_id="sess_1",
            question="what facts exist?",
            return_messages=True,
        )
        assert result.answer == "No facts found."
        assert [event["kind"] for event in events] == [
            "baml_call",
            "retrieval",
            "baml_call",
        ]
        assert events[1]["action_type"] == "list"
        assert events[-1]["supporting_record_ids"] == []
