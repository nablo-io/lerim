"""Tests for the context-answerer agent module."""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest

from lerim.agents.context_answerer import (
    CONTEXT_ANSWERER_SYSTEM_PROMPT,
    ContextAnswerResult,
    run_context_answerer,
)
from lerim.context import ContextStore
from lerim.context.project_identity import ProjectIdentity


def _pipeline_steps(steps):
    """Return fake model steps keyed by ContextAnswerPipeline phase."""
    return {
        "plan": steps.PlanContextRetrieval,
        "answer": steps.AnswerFromContext,
    }


class TestContextAnswerResult:
    """Tests for ContextAnswerResult model."""

    def test_model_dump(self):
        result = ContextAnswerResult(answer="some answer text")
        data = result.model_dump()
        assert data["answer"] == "some answer text"
        assert data["supporting_record_ids"] == []


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
            "steps",
        }
        assert set(params.keys()) == expected

    def test_runs_model_planned_retrieval(self, tmp_path):
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
            title="Use model-planned answer",
            body="Answer retrieves context through a structured model plan.",
            status="active",
            decision="Use model-planned answer retrieval.",
            why="Answer should retrieve through structured store reads before synthesis.",
            change_reason="test",
        )

        class FakeModelSteps:
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

        steps = FakeModelSteps()

        result = run_context_answerer(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=identity,
            project_ids=["proj_abc"],
            session_id="sess_1",
            question="what do we know about X?",
            steps=_pipeline_steps(steps),
        )
        assert result.answer == "There is 1 active decision."
        assert result.supporting_record_ids == []

    def test_retries_search_plan_without_query(self, tmp_path):
        identity = ProjectIdentity(
            project_id="proj_abc",
            project_slug="test",
            repo_path=tmp_path,
        )
        store = ContextStore(tmp_path / "context.sqlite3")
        store.initialize()
        store.register_project(identity)
        plan_instructions: list[str] = []

        class FakeModelSteps:
            def PlanContextRetrieval(self, **kwargs):
                plan_instructions.append(kwargs["run_instruction"])
                if len(plan_instructions) == 1:
                    return {
                        "actions": [
                            {
                                "action_type": "search",
                                "rationale": "Search without a query.",
                            }
                        ],
                        "rationale": "Invalid plan.",
                    }
                assert "Previous structured output was unsafe" in kwargs["run_instruction"]
                return {
                    "actions": [
                        {
                            "action_type": "count",
                            "kind": "fact",
                            "status": "active",
                            "rationale": "Count active facts.",
                        }
                    ],
                    "rationale": "Use exact count instead.",
                }

            def AnswerFromContext(self, **kwargs):
                retrieval = json.loads(kwargs["retrieval_json"])
                count = retrieval["results"][0]["count"]
                return {"answer": f"There are {count} active facts."}

        steps = FakeModelSteps()

        result, events = run_context_answerer(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=identity,
            project_ids=["proj_abc"],
            session_id="sess_1",
            question="compare retrieval design",
            return_messages=True,
            steps=_pipeline_steps(steps),
        )
        assert result.answer == "There are 0 active facts."
        assert len(plan_instructions) == 2
        assert events[0]["kind"] == "model_retry"
        assert events[1]["stage"] == "plan_retrieval"
        assert events[1]["attempts"] == 2

    def test_returns_valid_supporting_record_ids(self, tmp_path):
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
        created = store.create_record(
            project_id=identity.project_id,
            session_id="seed",
            kind="fact",
            title="Refund checks",
            body="Refund agents must verify entitlement and latest invoice.",
            status="active",
            change_reason="test",
        )

        class FakeModelSteps:
            def PlanContextRetrieval(self, **_kwargs):
                return {
                    "actions": [
                        {
                            "action_type": "list",
                            "kind": "fact",
                            "status": "active",
                            "rationale": "List active facts.",
                        }
                    ],
                    "rationale": "Use exact list.",
                }

            def AnswerFromContext(self, **_kwargs):
                return {
                    "answer": "Verify entitlement and latest invoice.",
                    "supporting_record_ids": [created["record_id"], "not_a_real_record"],
                }

        steps = FakeModelSteps()

        result = run_context_answerer(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=identity,
            project_ids=["proj_abc"],
            session_id="sess_1",
            question="what should refund agents check?",
            steps=_pipeline_steps(steps),
        )
        assert result.answer == "Verify entitlement and latest invoice."
        assert result.supporting_record_ids == [created["record_id"]]

    def test_retries_placeholder_only_answers(self, tmp_path):
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
        created = store.create_record(
            project_id=identity.project_id,
            session_id="seed",
            kind="fact",
            title="Timeout contract",
            body="Answer requests use a five minute server deadline.",
            status="active",
            change_reason="test",
        )
        answer_instructions: list[str] = []

        class FakeModelSteps:
            def PlanContextRetrieval(self, **_kwargs):
                return {
                    "actions": [
                        {
                            "action_type": "list",
                            "kind": "fact",
                            "status": "active",
                            "rationale": "List active facts.",
                        }
                    ],
                    "rationale": "Use exact list.",
                }

            def AnswerFromContext(self, **kwargs):
                answer_instructions.append(kwargs["run_instruction"])
                if len(answer_instructions) == 1:
                    return {
                        "answer": "...",
                        "supporting_record_ids": ["rec_...", "..."],
                    }
                assert "Previous structured output was unsafe" in kwargs["run_instruction"]
                return {
                    "answer": "Answer requests use a five minute server deadline.",
                    "supporting_record_ids": [created["record_id"]],
                }

        steps = FakeModelSteps()

        result, events = run_context_answerer(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=identity,
            project_ids=["proj_abc"],
            session_id="sess_1",
            question="what is the answer timeout?",
            return_messages=True,
            steps=_pipeline_steps(steps),
        )
        assert result.answer == "Answer requests use a five minute server deadline."
        assert result.supporting_record_ids == [created["record_id"]]
        assert len(answer_instructions) == 2
        assert events[-2]["kind"] == "model_retry"
        assert events[-1]["attempts"] == 2

    def test_rejects_repeated_placeholder_only_answers(self, tmp_path):
        identity = ProjectIdentity(
            project_id="proj_abc",
            project_slug="test",
            repo_path=tmp_path,
        )
        store = ContextStore(tmp_path / "context.sqlite3")
        store.initialize()
        store.register_project(identity)

        class FakeModelSteps:
            def PlanContextRetrieval(self, **_kwargs):
                return {
                    "actions": [
                        {
                            "action_type": "count",
                            "kind": "fact",
                            "status": "active",
                            "rationale": "Count active facts.",
                        }
                    ],
                    "rationale": "Use exact count.",
                }

            def AnswerFromContext(self, **_kwargs):
                return {"answer": "...", "supporting_record_ids": []}

        steps = FakeModelSteps()

        with pytest.raises(RuntimeError, match="invalid answer_from_context output"):
            run_context_answerer(
                context_db_path=tmp_path / "context.sqlite3",
                project_identity=identity,
                project_ids=["proj_abc"],
                session_id="sess_1",
                question="what is the answer timeout?",
                steps=_pipeline_steps(steps),
            )

    def test_can_return_debug_events(self, tmp_path):
        identity = ProjectIdentity(
            project_id="proj_abc",
            project_slug="test",
            repo_path=tmp_path,
        )
        store = ContextStore(tmp_path / "context.sqlite3")
        store.initialize()
        store.register_project(identity)

        class FakeModelSteps:
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
                return {
                    "answer": "No facts found.",
                    "supporting_record_ids": ["fabricated_count_result"],
                }

        steps = FakeModelSteps()

        result, events = run_context_answerer(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=identity,
            project_ids=["proj_abc"],
            session_id="sess_1",
            question="what facts exist?",
            return_messages=True,
            steps=_pipeline_steps(steps),
        )
        assert result.answer == "No facts found."
        assert [event["kind"] for event in events] == [
            "model_step",
            "retrieval",
            "model_step",
        ]
        assert events[1]["action_type"] == "list"
        assert events[-1]["supporting_record_ids"] == []
