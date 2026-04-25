"""Targeted real-LLM integration cases for the ask agent."""

from __future__ import annotations

import pytest

from tests.integration.ask.helpers import load_ask_expectation, run_ask_case
from tests.live_helpers import ASK_TOOL_NAMES, FRAMEWORK_TOOL_NAMES


def _find_first_tool_call(tool_calls: list[dict[str, object]], tool_name: str) -> dict[str, object]:
    """Return the first call for one tool name."""
    return next(call for call in tool_calls if call["tool_name"] == tool_name)


def _find_all_tool_calls(tool_calls: list[dict[str, object]], tool_name: str) -> list[dict[str, object]]:
    """Return all calls for one tool name."""
    return [call for call in tool_calls if call["tool_name"] == tool_name]


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_ask_distinguishes_current_truth_from_historical_truth(
    live_config,
    live_repo_root,
) -> None:
    """Current-vs-historical questions should include archived support and label the difference."""
    case = load_ask_expectation("current_truth_vs_historical_truth")
    expectation = case["expected"]
    outcome = run_ask_case(
        case_name="current_truth_vs_historical_truth",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    answer = outcome.result.answer.strip().lower()
    tool_names = outcome.tool_names

    assert outcome.result.answer.strip()
    assert set(tool_names).issubset(ASK_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)

    list_calls = _find_all_tool_calls(outcome.tool_calls, "list_context")
    context_calls = _find_all_tool_calls(outcome.tool_calls, "count_context")
    search_calls = _find_all_tool_calls(outcome.tool_calls, "search_context")
    assert list_calls or context_calls or search_calls
    if search_calls:
        first_search_args = search_calls[0]["args"] or {}
        assert bool(first_search_args.get("include_archived")) is True

    fetch_calls = _find_all_tool_calls(outcome.tool_calls, "get_context")
    assert fetch_calls, "expected current and historical supporting records to be fetched"
    if fetch_calls:
        fetched_ids: set[str] = set()
        for call in fetch_calls:
            fetch_args = call["args"] or {}
            fetched_ids.update(str(record_id) for record_id in (fetch_args.get("record_ids") or []))
        assert set(expectation["required_record_ids"]).issubset(fetched_ids)
    if list_calls:
        assert any(
            bool((call["args"] or {}).get("include_archived"))
            for call in list_calls
        ) or context_calls, "expected an archived-capable retrieval path before synthesis"
    elif context_calls:
        context_args = context_calls[0]["args"] or {}
        assert str(context_args.get("mode") or "").strip().lower() == "list"

    for token in expectation["answer_must_include_all"]:
        assert token in answer


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_ask_durable_support_beats_episode_support(
    live_config,
    live_repo_root,
) -> None:
    """When durable and episodic support coexist, ask should prefer the durable record."""
    case = load_ask_expectation("ask_durable_support_beats_episode_support")
    expectation = case["expected"]
    outcome = run_ask_case(
        case_name="ask_durable_support_beats_episode_support",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    answer = outcome.result.answer.strip().lower()
    tool_names = outcome.tool_names

    assert outcome.result.answer.strip()
    assert set(tool_names).issubset(ASK_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for token in expectation["answer_must_include_all"]:
        assert token in answer
    for token in expectation["answer_must_not_include"]:
        assert token not in answer


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_ask_as_of_date_uses_valid_at(
    live_config,
    live_repo_root,
) -> None:
    """As-of questions should use validity-time retrieval instead of latest truth."""
    case = load_ask_expectation("ask_as_of_date_uses_valid_at")
    expectation = case["expected"]
    outcome = run_ask_case(
        case_name="ask_as_of_date_uses_valid_at",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    answer = outcome.result.answer.strip().lower()
    tool_names = outcome.tool_names

    assert outcome.result.answer.strip()
    assert set(tool_names).issubset(ASK_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    list_calls = _find_all_tool_calls(outcome.tool_calls, "list_context")
    context_calls = _find_all_tool_calls(outcome.tool_calls, "count_context")
    search_calls = _find_all_tool_calls(outcome.tool_calls, "search_context")
    assert list_calls or context_calls or search_calls
    first_call = list_calls[0] if list_calls else context_calls[0] if context_calls else search_calls[0]
    args = first_call["args"] or {}
    if search_calls and first_call is search_calls[0]:
        assert str(args.get("valid_at") or "").startswith("2026-02-15")
        if list_calls:
            list_args = list_calls[0]["args"] or {}
            assert str(list_args.get("valid_at") or "").startswith("2026-02-15")
    else:
        assert str(args.get("valid_at") or "").startswith("2026-02-15")
    if list_calls:
        list_args = list_calls[0]["args"] or {}
        assert str(list_args.get("valid_at") or "").startswith("2026-02-15")
    elif context_calls:
        context_args = context_calls[0]["args"] or {}
        assert str(context_args.get("valid_at") or "").startswith("2026-02-15")
    for token in expectation["answer_must_include_all"]:
        assert token in answer
    for token in expectation["answer_must_not_include"]:
        assert token not in answer


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_ask_near_miss_topic_returns_negative(
    live_config,
    live_repo_root,
) -> None:
    """Near-miss semantic neighbors should not be stretched into unsupported claims."""
    case = load_ask_expectation("ask_near_miss_topic_returns_negative")
    expectation = case["expected"]
    outcome = run_ask_case(
        case_name="ask_near_miss_topic_returns_negative",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    answer = outcome.result.answer.strip().lower()
    tool_names = outcome.tool_names

    assert outcome.result.answer.strip()
    assert set(tool_names).issubset(ASK_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    assert any(token in answer for token in expectation["answer_must_include_any"])
    for token in expectation["answer_must_not_include"]:
        assert token not in answer
    assert any(token in answer for token in expectation["answer_must_include_any"])

@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_ask_calls_out_when_support_is_only_episodic(
    live_config,
    live_repo_root,
) -> None:
    """Ask should clearly say when a topic is supported only by episode records."""
    case = load_ask_expectation("episodic_only_support_is_called_out")
    expectation = case["expected"]
    outcome = run_ask_case(
        case_name="episodic_only_support_is_called_out",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    answer = outcome.result.answer.strip().lower()
    tool_names = outcome.tool_names

    assert outcome.result.answer.strip()
    assert set(tool_names).issubset(ASK_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names

    assert "get_context" in tool_names
    assert any(token in answer for token in expectation["answer_must_include_any"])
    assert any(token in answer for token in expectation["answer_must_include_any_missing_durable"])

@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_ask_no_relevant_records(
    live_config,
    live_repo_root,
) -> None:
    """Ask should answer honestly when no relevant records exist."""
    case = load_ask_expectation("ask_no_relevant_records")
    expectation = case["expected"]
    outcome = run_ask_case(
        case_name="ask_no_relevant_records",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    answer = outcome.result.answer.strip().lower()
    tool_names = outcome.tool_names

    assert outcome.result.answer.strip()
    assert set(tool_names).issubset(ASK_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    assert any(token in answer for token in expectation["answer_must_include_any"])

@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_ask_multi_record_synthesis(
    live_config,
    live_repo_root,
) -> None:
    """Ask should combine several durable records into one coherent answer."""
    case = load_ask_expectation("ask_multi_record_synthesis")
    expectation = case["expected"]
    outcome = run_ask_case(
        case_name="ask_multi_record_synthesis",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    answer = outcome.result.answer.strip().lower()
    tool_names = outcome.tool_names

    assert outcome.result.answer.strip()
    assert set(tool_names).issubset(ASK_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    fetch_calls = _find_all_tool_calls(outcome.tool_calls, "get_context")
    assert fetch_calls, "expected fetched records before synthesis"
    fetched_ids: set[str] = set()
    for call in fetch_calls:
        fetch_args = call["args"] or {}
        fetched_ids.update(str(record_id) for record_id in (fetch_args.get("record_ids") or []))
    assert len(fetched_ids) >= 2

    for token in expectation["answer_must_include_all"]:
        assert token in answer
