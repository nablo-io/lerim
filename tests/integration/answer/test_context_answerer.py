"""Targeted real-LLM integration cases for the context answerer."""

from __future__ import annotations

import pytest

from tests.integration.answer.helpers import (
    load_answer_expectation,
    run_answer_case,
)


pytestmark = [pytest.mark.integration, pytest.mark.llm, pytest.mark.agent]


def _normalize(text: str) -> str:
    return text.lower().replace("\u2013", "-").replace("\u2014", "-")


def _event_functions(messages: list[dict[str, object]]) -> list[str]:
    return [str(item.get("function") or "") for item in messages]


def _retrieval_actions(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    return [item for item in messages if item.get("kind") == "retrieval"]


def test_context_answerer_count_question_uses_exact_count(
    live_config,
    live_repo_root,
) -> None:
    """Count questions should use the count retrieval action."""
    expectation = load_answer_expectation("count_question_uses_count_context")["expected"]
    outcome = run_answer_case(
        case_name="count_question_uses_count_context",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    answer = _normalize(outcome.result.answer)
    actions = _retrieval_actions(outcome.messages)

    assert outcome.result.answer.strip()
    assert _event_functions(outcome.messages) == [
        "PlanContextRetrieval",
        "",
        "AnswerFromContext",
    ]
    assert [action["action_type"] for action in actions] == ["count"]
    assert actions[0]["result_count"] == 2
    answer_events = [
        event
        for event in outcome.messages
        if event.get("function") == "AnswerFromContext"
    ]
    assert answer_events[-1]["supporting_record_ids"] == []
    assert any(token in answer for token in expectation["answer_must_include_any"])


def test_context_answerer_semantic_topic_uses_search_or_list_support(
    live_config,
    live_repo_root,
) -> None:
    """Topic questions should retrieve supporting records before answering."""
    expectation = load_answer_expectation("semantic_topic_uses_search_then_fetch")["expected"]
    outcome = run_answer_case(
        case_name="semantic_topic_uses_search_then_fetch",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    answer = _normalize(outcome.result.answer)
    actions = _retrieval_actions(outcome.messages)

    assert outcome.result.answer.strip()
    assert "PlanContextRetrieval" in _event_functions(outcome.messages)
    assert "AnswerFromContext" in _event_functions(outcome.messages)
    assert actions
    assert any(action["action_type"] in {"search", "list"} for action in actions)
    assert any(int(action.get("result_count") or 0) > 0 for action in actions)
    for token in expectation["answer_must_include_all"]:
        assert token in answer


def test_context_answerer_can_compare_current_and_historical_records(
    live_config,
    live_repo_root,
) -> None:
    """Current-vs-historical questions should surface both seeded truths."""
    expectation = load_answer_expectation("current_truth_vs_historical_truth")["expected"]
    outcome = run_answer_case(
        case_name="current_truth_vs_historical_truth",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    answer = _normalize(outcome.result.answer)
    actions = _retrieval_actions(outcome.messages)

    assert outcome.result.answer.strip()
    assert actions
    for record_id in expectation["required_record_ids"]:
        assert record_id in str(outcome.messages)
    for token in expectation["answer_must_include_all"]:
        assert token in answer
    assert any(token in answer for token in expectation["answer_must_include_any"])
