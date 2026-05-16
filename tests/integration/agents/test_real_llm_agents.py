"""Focused real-LLM integration coverage for each current Lerim agent."""

from __future__ import annotations

import json

import pytest

from lerim.agents.context_answerer import run_context_answerer
from lerim.agents.context_brief import compile_context_brief
from lerim.agents.context_curator import run_context_curator
from lerim.agents.trace_ingestion import run_trace_ingestion
from lerim.context import ContextStore, resolve_project_identity
from lerim.context_brief import included_record_ids, validate_draft
from tests.integration.common_helpers import retry_on_overload, seed_session


pytestmark = [pytest.mark.integration, pytest.mark.llm, pytest.mark.agent]


def _store_and_identity(live_config, live_repo_root):
    store = ContextStore(live_config.context_db_path)
    store.initialize()
    identity = resolve_project_identity(live_repo_root)
    store.register_project(identity)
    return store, identity


def test_trace_ingestion_agent_real_llm_writes_scoped_context(
    live_config,
    live_repo_root,
) -> None:
    """Trace ingestion should resolve scope, filter signal, and persist records."""
    store, identity = _store_and_identity(live_config, live_repo_root)
    session_id = "integration-agent-trace-ingestion"
    trace_path = live_repo_root / "support-agent-trace.jsonl"
    trace_path.write_text(
        "\n".join(
            json.dumps(item, ensure_ascii=True)
            for item in [
                {
                    "role": "user",
                    "content": (
                        "Review this support-agent handoff. Keep durable context only. "
                        "The durable policy is that refund escalations over EUR 500 must include "
                        "the customer-visible reason and the internal approval reference."
                    ),
                },
                {
                    "role": "assistant",
                    "content": (
                        "I checked the billing workflow. Temporary ticket labels, local notes, "
                        "and draft wording are not reusable. The durable context is the refund "
                        "escalation policy and its required evidence fields."
                    ),
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    seed_session(
        store,
        project_id=identity.project_id,
        session_id=session_id,
        repo_root=live_repo_root,
        agent_type="integration-trace-ingestion",
        source_trace_ref=str(trace_path),
    )

    result, details = retry_on_overload(
        lambda: run_trace_ingestion(
            context_db_path=live_config.context_db_path,
            project_identity=identity,
            session_id=session_id,
            trace_path=trace_path,
            config=live_config,
            session_started_at="2026-05-15T00:00:00+00:00",
            source_name="support-agent",
            source_profile="support handoff trace",
            max_llm_calls=5,
            return_details=True,
        )
    )

    actions = [event.action for event in details.events]
    assert result.completion_summary.strip()
    for expected in (
        "resolve_scope",
        "read_window",
        "scan_window",
        "filter_signals",
        "synthesize_records",
        "save_context",
    ):
        assert expected in actions
    assert details.scope_type == "project"
    rows = store.query(
        entity="records",
        mode="list",
        project_ids=[identity.project_id],
        source_session_id=session_id,
        order_by="created_at",
        limit=10,
        include_archived=True,
    )["rows"]
    assert any(row["kind"] == "episode" for row in rows)
    assert any(row["kind"] != "episode" for row in rows)


def test_context_curator_agent_real_llm_reviews_seeded_records(
    live_config,
    live_repo_root,
) -> None:
    """Context curator should inventory, review, and finish on real model output."""
    store, identity = _store_and_identity(live_config, live_repo_root)
    seed_session_id = "integration-agent-curator-seed"
    session_id = "integration-agent-context-curator"
    seed_session(
        store,
        project_id=identity.project_id,
        session_id=seed_session_id,
        repo_root=live_repo_root,
        agent_type="integration-context-curator",
        source_trace_ref="context-curator-seed",
    )
    seed_session(
        store,
        project_id=identity.project_id,
        session_id=session_id,
        repo_root=live_repo_root,
        agent_type="integration-context-curator",
        source_trace_ref="context-curator-run",
    )
    for record_id, title, body in (
        (
            "curator_refund_policy_weak",
            "Refund escalation policy",
            "Refund escalations over EUR 500 need customer reason and approval reference.",
        ),
        (
            "curator_refund_policy_strong",
            "Refund escalations over EUR 500 need evidence fields",
            "Refund escalations over EUR 500 must include the customer-visible reason and internal approval reference.",
        ),
    ):
        store.create_record(
            project_id=identity.project_id,
            session_id=seed_session_id,
            record_id=record_id,
            kind="fact",
            title=title,
            body=body,
            change_reason="integration_seed",
        )

    result, details = retry_on_overload(
        lambda: run_context_curator(
            context_db_path=live_config.context_db_path,
            project_identity=identity,
            session_id=session_id,
            config=live_config,
            max_llm_calls=6,
            return_details=True,
        )
    )

    actions = [event.action for event in details.events]
    assert result.completion_summary.strip()
    assert "load_inventory" in actions
    assert "build_similarity_clusters" in actions
    assert "final_result" in actions
    assert any(action in actions for action in ("review_cluster", "review_health_batch"))


def test_context_answerer_agent_real_llm_answers_from_seeded_context(
    live_config,
    live_repo_root,
) -> None:
    """Context answerer should plan retrieval and synthesize from returned records."""
    store, identity = _store_and_identity(live_config, live_repo_root)
    seed_session_id = "integration-agent-answerer-seed"
    seed_session(
        store,
        project_id=identity.project_id,
        session_id=seed_session_id,
        repo_root=live_repo_root,
        agent_type="integration-context-answerer",
        source_trace_ref="context-answerer-seed",
    )
    record = store.create_record(
        project_id=identity.project_id,
        session_id=seed_session_id,
        record_id="answerer_refund_policy",
        kind="fact",
        title="Refund escalations require two evidence fields",
        body=(
            "Refund escalations over EUR 500 must include the customer-visible reason "
            "and the internal approval reference."
        ),
        change_reason="integration_seed",
    )

    result, events = retry_on_overload(
        lambda: run_context_answerer(
            context_db_path=live_config.context_db_path,
            project_identity=identity,
            project_ids=[identity.project_id],
            session_id="integration-agent-context-answerer",
            question="What evidence is required for refund escalations over EUR 500?",
            config=live_config,
            return_messages=True,
        )
    )

    functions = [str(event.get("function") or "") for event in events]
    retrievals = [event for event in events if event.get("kind") == "retrieval"]
    answer = result.answer.lower()
    assert "PlanContextRetrieval" in functions
    assert "AnswerFromContext" in functions
    assert retrievals
    assert "customer" in answer
    assert "approval" in answer
    assert str(record["record_id"]) in json.dumps(events, ensure_ascii=True)


def test_context_brief_compiler_agent_real_llm_returns_cited_lines(
    live_config,
) -> None:
    """Context brief compiler should produce cited startup context from candidates."""
    candidates = [
        {
            "record_id": "brief_refund_policy",
            "kind": "fact",
            "title": "Refund escalations require two evidence fields",
            "body": (
                "Refund escalations over EUR 500 must include the customer-visible reason "
                "and the internal approval reference."
            ),
            "updated_at": "2026-05-15T00:00:00+00:00",
        },
        {
            "record_id": "brief_channel_preference",
            "kind": "preference",
            "title": "Keep handoffs evidence-first",
            "body": "Operational handoffs should cite the supporting context record before recommending action.",
            "updated_at": "2026-05-15T00:00:00+00:00",
        },
    ]

    draft, events = retry_on_overload(
        lambda: compile_context_brief(
            config=live_config,
            candidates=candidates,
            return_messages=True,
        )
    )

    validate_draft(
        draft,
        allowed_record_ids={str(record["record_id"]) for record in candidates},
        record_kinds={
            str(record["record_id"]): str(record["kind"]) for record in candidates
        },
    )
    cited_ids = included_record_ids(draft)
    assert cited_ids
    assert draft.decisions == ()
    assert any("brief_refund_policy" in line.record_ids for line in draft.project_facts)
    assert events == [
        {
            "kind": "baml_call",
            "function": "CompileContextBrief",
            "candidate_count": 2,
        }
    ]
