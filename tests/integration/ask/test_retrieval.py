"""Targeted real-LLM integration cases for the ask agent."""

from __future__ import annotations

import re

import pytest

from tests.integration.ask.helpers import load_ask_expectation, run_ask_case
from tests.live_helpers import ASK_TOOL_NAMES, FRAMEWORK_TOOL_NAMES, assert_no_legacy_tools


def _normalize_answer_text(text: str) -> str:
    """Normalize common formatting variants before substring assertions."""
    return (
        text.lower()
        .replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("**", "")
    )


def _find_first_tool_call(tool_calls: list[dict[str, object]], tool_name: str) -> dict[str, object]:
    """Return the first call for one tool name."""
    return next(call for call in tool_calls if call["tool_name"] == tool_name)


def _find_all_tool_calls(tool_calls: list[dict[str, object]], tool_name: str) -> list[dict[str, object]]:
    """Return all calls for one tool name."""
    return [call for call in tool_calls if call["tool_name"] == tool_name]


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_ask_count_question_uses_context_query(
    live_config,
    live_repo_root,
) -> None:
    """Count questions should use deterministic querying, not semantic retrieval."""
    case = load_ask_expectation("count_question_uses_context_query")
    expectation = case["expected"]
    outcome = run_ask_case(
        case_name="count_question_uses_context_query",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    answer = _normalize_answer_text(outcome.result.answer.strip())
    tool_names = outcome.tool_names

    assert outcome.result.answer.strip()
    assert set(tool_names).issubset(ASK_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    assert_no_legacy_tools(tool_names)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    count_call = _find_first_tool_call(outcome.tool_calls, "context_query")
    args = count_call["args"] or {}
    assert str(args.get("mode") or "").strip().lower() == expectation["context_query"]["mode"]
    assert str(args.get("entity") or "").strip().lower() in expectation["context_query"]["entity_any_of"]

    assert any(token in answer for token in expectation["answer_must_include_any"])

@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_ask_semantic_topic_uses_search_then_fetch(
    live_config,
    live_repo_root,
) -> None:
    """Topic questions should retrieve semantically, then fetch the matched records."""
    case = load_ask_expectation("semantic_topic_uses_search_then_fetch")
    expectation = case["expected"]
    outcome = run_ask_case(
        case_name="semantic_topic_uses_search_then_fetch",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    answer = _normalize_answer_text(outcome.result.answer.strip())
    tool_names = outcome.tool_names

    assert outcome.result.answer.strip()
    assert set(tool_names).issubset(ASK_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    assert_no_legacy_tools(tool_names)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    search_index = tool_names.index("search_records")
    fetch_index = tool_names.index("fetch_records")
    assert search_index < fetch_index

    for token in expectation["answer_must_include_all"]:
        assert token in answer

@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_ask_latest_question_prefers_exact_listing(
    live_config,
    live_repo_root,
) -> None:
    """Latest questions should prefer exact listing over semantic retrieval."""
    case = load_ask_expectation("ask_latest_question_prefers_exact_listing")
    expectation = case["expected"]
    outcome = run_ask_case(
        case_name="ask_latest_question_prefers_exact_listing",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    answer = _normalize_answer_text(outcome.result.answer.strip())
    tool_names = outcome.tool_names

    assert outcome.result.answer.strip()
    assert set(tool_names).issubset(ASK_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    assert_no_legacy_tools(tool_names)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    list_calls = _find_all_tool_calls(outcome.tool_calls, "list_records")
    context_calls = _find_all_tool_calls(outcome.tool_calls, "context_query")
    assert list_calls or context_calls, "expected exact listing/querying for latest question"
    latest_call = list_calls[0] if list_calls else context_calls[0]
    args = latest_call["args"] or {}
    effective_order_by = str(args.get("order_by") or "updated_at").strip().lower()
    assert effective_order_by in expectation["list_records"]["order_by_any_of"]
    kind_filters = args.get("kind_filters") or []
    if isinstance(kind_filters, str):
        kind_filters = [kind_filters]
    effective_kind = str(args.get("kind") or "").strip().lower()
    assert effective_kind == "decision" or kind_filters == ["decision"]
    assert "search_records" not in tool_names

    for token in expectation["answer_must_include_all"]:
        assert token in answer

@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_ask_time_window_question_narrows_before_synthesis(
    live_config,
    live_repo_root,
) -> None:
    """Time-window questions should narrow by exact filters before synthesis."""
    case = load_ask_expectation("ask_time_window_question_narrows_before_synthesis")
    expectation = case["expected"]
    outcome = run_ask_case(
        case_name="ask_time_window_question_narrows_before_synthesis",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    answer = _normalize_answer_text(outcome.result.answer.strip())
    tool_names = outcome.tool_names

    assert outcome.result.answer.strip()
    assert set(tool_names).issubset(ASK_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    assert_no_legacy_tools(tool_names)
    assert any(tool_name in tool_names for tool_name in expectation["must_use_any_tools"])
    for tool_name in expectation.get("must_not_use_tools", []):
        assert tool_name not in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    if "list_records" in tool_names:
        list_call = _find_first_tool_call(outcome.tool_calls, "list_records")
        args = list_call["args"] or {}
    else:
        query_call = _find_first_tool_call(outcome.tool_calls, "context_query")
        args = query_call["args"] or {}
    assert str(args.get("created_since") or "").strip()
    assert str(args.get("created_until") or "").strip()
    kind_filters = args.get("kind_filters") or []
    if isinstance(kind_filters, str):
        kind_filters = [kind_filters]
    effective_kind = str(args.get("kind") or "").strip().lower()
    assert effective_kind == "decision" or kind_filters == ["decision"]
    assert "search_records" not in tool_names

    fetch_calls = _find_all_tool_calls(outcome.tool_calls, "fetch_records")
    if fetch_calls:
        fetch_index = tool_names.index("fetch_records")
        if "list_records" in tool_names:
            exact_index = tool_names.index("list_records")
        else:
            exact_index = tool_names.index("context_query")
        assert exact_index < fetch_index

    for token in expectation["answer_must_include_all"]:
        assert token in answer
    for token in expectation["answer_must_not_include"]:
        assert token not in answer


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_ask_time_window_zero_results_do_not_expand_scope(
    live_config,
    live_repo_root,
) -> None:
    """Empty time-window queries should stay negative instead of widening the scope."""
    case = load_ask_expectation("ask_time_window_zero_results_do_not_expand_scope")
    expectation = case["expected"]
    outcome = run_ask_case(
        case_name="ask_time_window_zero_results_do_not_expand_scope",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    answer = _normalize_answer_text(outcome.result.answer.strip())
    tool_names = outcome.tool_names

    assert outcome.result.answer.strip()
    assert set(tool_names).issubset(ASK_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    assert_no_legacy_tools(tool_names)
    assert any(tool_name in tool_names for tool_name in expectation["must_use_any_tools"])
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    if "list_records" in tool_names:
        first_exact = _find_first_tool_call(outcome.tool_calls, "list_records")
    else:
        first_exact = _find_first_tool_call(outcome.tool_calls, "context_query")
    args = first_exact["args"] or {}
    has_created_window = bool(str(args.get("created_since") or "").strip()) and bool(
        str(args.get("created_until") or "").strip()
    )
    has_updated_window = bool(str(args.get("updated_since") or "").strip()) and bool(
        str(args.get("updated_until") or "").strip()
    )
    assert has_created_window or has_updated_window

    for token in expectation["answer_must_include_any"]:
        if token in answer:
            break
    else:
        has_time_anchor = any(token in answer for token in ("2026-04-20", "yesterday", "time window"))
        has_negative_signal = any(
            token in answer
            for token in (
                "nothing",
                "no records",
                "no evidence",
                "no decisions",
                "not found",
                "not recorded",
            )
        )
        if not (has_time_anchor and has_negative_signal):
            raise AssertionError(f"answer missing expected negative phrasing: {answer}")
    for token in expectation["answer_must_not_include"]:
        assert token not in answer

@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_ask_mixed_question_strategy(
    live_config,
    live_repo_root,
) -> None:
    """Mixed questions should narrow first, then retrieve, then answer."""
    case = load_ask_expectation("ask_mixed_question_strategy")
    expectation = case["expected"]
    outcome = run_ask_case(
        case_name="ask_mixed_question_strategy",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    answer = _normalize_answer_text(outcome.result.answer.strip())
    tool_names = outcome.tool_names

    assert outcome.result.answer.strip()
    assert set(tool_names).issubset(ASK_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    assert_no_legacy_tools(tool_names)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names

    narrow_index = min(
        tool_names.index(tool_name)
        for tool_name in ("list_records", "context_query")
        if tool_name in tool_names
    )
    fetch_index = tool_names.index("fetch_records")
    assert narrow_index < fetch_index
    if "search_records" in tool_names:
        search_index = tool_names.index("search_records")
        assert narrow_index < search_index < fetch_index

    for token in expectation["answer_must_include_all"]:
        assert token in answer
    for token in expectation.get("answer_must_not_include", []):
        assert token not in answer


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_ask_mixed_time_topic_no_in_window_match_stays_negative(
    live_config,
    live_repo_root,
) -> None:
    """Mixed time-plus-topic questions should not widen to older topical matches."""
    case = load_ask_expectation("ask_mixed_time_topic_no_in_window_match_stays_negative")
    expectation = case["expected"]
    outcome = run_ask_case(
        case_name="ask_mixed_time_topic_no_in_window_match_stays_negative",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    answer = _normalize_answer_text(outcome.result.answer.strip())
    tool_names = outcome.tool_names

    assert outcome.result.answer.strip()
    assert set(tool_names).issubset(ASK_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    assert_no_legacy_tools(tool_names)
    assert any(tool_name in tool_names for tool_name in expectation["must_use_any_tools"])

    if "list_records" in tool_names:
        first_exact = _find_first_tool_call(outcome.tool_calls, "list_records")
    else:
        first_exact = _find_first_tool_call(outcome.tool_calls, "context_query")
    args = first_exact["args"] or {}
    has_created_window = bool(str(args.get("created_since") or "").strip()) and bool(
        str(args.get("created_until") or "").strip()
    )
    has_updated_window = bool(str(args.get("updated_since") or "").strip()) and bool(
        str(args.get("updated_until") or "").strip()
    )
    assert has_created_window or has_updated_window
    if "search_records" in tool_names:
        search_index = tool_names.index("search_records")
        exact_index = min(
            tool_names.index(tool_name)
            for tool_name in ("list_records", "context_query")
            if tool_name in tool_names
        )
        assert exact_index < search_index

    for token in expectation["answer_must_include_any"]:
        if token in answer:
            break
    else:
        has_time_anchor = any(token in answer for token in ("yesterday", "time window")) or bool(
            re.search(r"\b20\d{2}-\d{2}-\d{2}\b", answer)
        )
        has_negative_signal = any(token in answer for token in ("nothing", "no records", "no evidence"))
        if not (has_time_anchor and has_negative_signal):
            raise AssertionError(f"answer missing expected negative phrasing: {answer}")
    for token in expectation["answer_must_not_include"]:
        assert token not in answer
