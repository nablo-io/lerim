"""Targeted real-LLM integration cases for the ask agent."""

from __future__ import annotations

import re

import pytest

from tests.integration.ask.helpers import load_ask_expectation, run_ask_case
from tests.live_helpers import ASK_TOOL_NAMES, FRAMEWORK_TOOL_NAMES


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


def _has_time_anchor(answer: str) -> bool:
    """Return whether an answer anchors a date-window response."""
    if any(token in answer for token in ("yesterday", "time window")):
        return True
    if re.search(r"\b20\d{2}-\d{2}-\d{2}\b", answer):
        return True
    return bool(
        re.search(
            r"\b(?:january|february|march|april|may|june|july|august|"
            r"september|october|november|december)\s+\d{1,2},\s+20\d{2}\b",
            answer,
        )
    )


def _find_first_tool_call(tool_calls: list[dict[str, object]], tool_name: str) -> dict[str, object]:
    """Return the first call for one tool name."""
    return next(call for call in tool_calls if call["tool_name"] == tool_name)


def _find_all_tool_calls(tool_calls: list[dict[str, object]], tool_name: str) -> list[dict[str, object]]:
    """Return all calls for one tool name."""
    return [call for call in tool_calls if call["tool_name"] == tool_name]


def _tool_call_index(tool_calls: list[dict[str, object]], target: dict[str, object]) -> int:
    """Return the zero-based position of a tool call in the trace."""
    for index, call in enumerate(tool_calls):
        if call is target:
            return index
    raise AssertionError(f"tool call not found in trace: {target}")


def _has_time_window(args: dict[str, object]) -> bool:
    """Return whether args contain an exact created or updated time window."""
    filters = args.get("filters") if isinstance(args.get("filters"), dict) else {}
    effective_args = filters or args
    has_created_window = bool(str(effective_args.get("created_since") or "").strip()) and bool(
        str(effective_args.get("created_until") or "").strip()
    )
    has_updated_window = bool(str(effective_args.get("updated_since") or "").strip()) and bool(
        str(effective_args.get("updated_until") or "").strip()
    )
    return has_created_window or has_updated_window


def _has_kind_filter(args: dict[str, object], kind: str) -> bool:
    """Return whether args restrict records to the expected kind."""
    expected = kind.strip().lower()
    filters = args.get("filters") if isinstance(args.get("filters"), dict) else {}
    effective_args = filters or args
    effective_kind = str(effective_args.get("kind") or "").strip().lower()
    if effective_kind == expected:
        return True
    kind_filters = effective_args.get("kind_filters") or []
    if isinstance(kind_filters, str):
        kind_filters = [kind_filters]
    return expected in {str(value).strip().lower() for value in kind_filters}


def _find_exact_time_window_call(
    tool_calls: list[dict[str, object]],
    *,
    kind: str | None = None,
) -> dict[str, object]:
    """Return the earliest exact retrieval call carrying a time-window filter."""
    for call in tool_calls:
        if call["tool_name"] not in {"list_context", "count_context"}:
            continue
        args = call["args"] or {}
        assert isinstance(args, dict)
        if not _has_time_window(args):
            continue
        if kind is not None and not _has_kind_filter(args, kind):
            continue
        return call
    label = f" for kind {kind!r}" if kind else ""
    raise AssertionError(f"expected exact time-window retrieval call{label}")


def _tool_return_for_call(
    tool_returns: list[dict[str, object]],
    call: dict[str, object],
) -> dict[str, object]:
    """Return the tool result paired with a call by tool_call_id."""
    call_id = call.get("tool_call_id")
    for result in tool_returns:
        if result.get("tool_call_id") == call_id:
            return result
    raise AssertionError(f"expected tool return for {call['tool_name']} call {call_id}")


def _records_from_payload(payload: object) -> list[dict[str, object]]:
    """Return record-like rows from a parsed tool result payload."""
    if not isinstance(payload, dict):
        return []
    for key in ("records", "rows", "hits"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _payload_count(payload: object) -> int | None:
    """Return a parsed payload count when present."""
    if not isinstance(payload, dict) or "count" not in payload:
        return None
    return int(payload["count"])


def _payload_is_zero(payload: object) -> bool:
    """Return whether a retrieval payload semantically reports zero records."""
    records = _records_from_payload(payload)
    count = _payload_count(payload)
    return not records and count in (None, 0)


def _records_text(records: list[dict[str, object]]) -> str:
    """Flatten returned support records into assertion text."""
    fields = (
        "record_id",
        "kind",
        "title",
        "body",
        "body_preview",
        "decision",
        "why",
        "alternatives",
        "consequences",
        "user_intent",
        "what_happened",
        "outcomes",
    )
    return _normalize_answer_text(
        " ".join(
            str(record.get(field) or "")
            for record in records
            for field in fields
        )
    )


def _returned_records_for_calls(
    tool_returns: list[dict[str, object]],
    calls: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Return all record payloads produced by the selected tool calls."""
    records: list[dict[str, object]] = []
    for call in calls:
        result = _tool_return_for_call(tool_returns, call)
        records.extend(_records_from_payload(result.get("parsed_content")))
    return records


def _returned_records_for_tool(
    tool_calls: list[dict[str, object]],
    tool_returns: list[dict[str, object]],
    tool_name: str,
) -> list[dict[str, object]]:
    """Return record payloads from every call to one tool."""
    return _returned_records_for_calls(
        tool_returns,
        _find_all_tool_calls(tool_calls, tool_name),
    )


def _assert_no_widening_after_zero_result(
    *,
    tool_calls: list[dict[str, object]],
    tool_returns: list[dict[str, object]],
    exact_call: dict[str, object],
) -> None:
    """Assert zero-result time-window retrieval was not followed by broader retrieval."""
    exact_return = _tool_return_for_call(tool_returns, exact_call)
    exact_payload = exact_return.get("parsed_content")
    assert _payload_is_zero(exact_payload), f"expected exact window to return zero records: {exact_payload}"

    exact_index = _tool_call_index(tool_calls, exact_call)
    for call in tool_calls[exact_index + 1:]:
        tool_name = str(call["tool_name"])
        if tool_name not in {"count_context", "get_context", "list_context", "search_context"}:
            continue
        args = call["args"] or {}
        if not isinstance(args, dict) or not _has_time_window(args):
            raise AssertionError(f"{tool_name} widened retrieval after exact zero-result window")
        result = _tool_return_for_call(tool_returns, call)
        payload = result.get("parsed_content")
        assert _payload_is_zero(payload), f"{tool_name} returned support after exact zero-result window: {payload}"


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_ask_count_question_uses_count_context(
    live_config,
    live_repo_root,
) -> None:
    """Count questions should use deterministic querying, not semantic retrieval."""
    case = load_ask_expectation("count_question_uses_count_context")
    expectation = case["expected"]
    outcome = run_ask_case(
        case_name="count_question_uses_count_context",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    answer = _normalize_answer_text(outcome.result.answer.strip())
    tool_names = outcome.tool_names

    assert outcome.result.answer.strip()
    assert set(tool_names).issubset(ASK_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    count_call = _find_first_tool_call(outcome.tool_calls, "count_context")
    args = count_call["args"] or {}
    assert set(args).issubset(
        {
            "kind",
            "status",
            "source_session_id",
            "created_since",
            "created_until",
            "updated_since",
            "updated_until",
            "valid_at",
            "include_archived",
        }
    )

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
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    search_index = tool_names.index("search_context")
    fetch_index = tool_names.index("get_context")
    assert search_index < fetch_index

    fetched_records = _returned_records_for_tool(outcome.tool_calls, outcome.tool_returns, "get_context")
    assert fetched_records, "expected get_context to return support payloads"
    fetched_support = _records_text(fetched_records)
    for token in expectation["answer_must_include_all"]:
        assert token in fetched_support

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
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    list_calls = _find_all_tool_calls(outcome.tool_calls, "list_context")
    context_calls = _find_all_tool_calls(outcome.tool_calls, "count_context")
    assert list_calls or context_calls, "expected exact listing/querying for latest question"
    latest_tool_name = min(
        ("list_context", "count_context"),
        key=lambda name: tool_names.index(name) if name in tool_names else len(tool_names),
    )
    latest_call = _find_first_tool_call(outcome.tool_calls, latest_tool_name)
    args = latest_call["args"] or {}
    effective_order_by = str(args.get("order_by") or "updated_at").strip().lower()
    assert effective_order_by in expectation["list_context"]["order_by_any_of"]
    assert _has_kind_filter(args, "decision")
    if "search_context" in tool_names:
        assert tool_names.index(latest_tool_name) < tool_names.index("search_context")

    for token in expectation["answer_must_include_all"]:
        assert token in answer


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_ask_current_readiness_prefers_newer_support(
    live_config,
    live_repo_root,
) -> None:
    """Current readiness questions should not answer from older contradicted support."""
    case = load_ask_expectation("current_readiness_prefers_newer_support")
    expectation = case["expected"]
    outcome = run_ask_case(
        case_name="current_readiness_prefers_newer_support",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    answer = _normalize_answer_text(outcome.result.answer.strip())
    tool_names = outcome.tool_names

    assert outcome.result.answer.strip()
    assert set(tool_names).issubset(ASK_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)

    exact_calls = _find_all_tool_calls(
        outcome.tool_calls, "list_context"
    ) + _find_all_tool_calls(outcome.tool_calls, "count_context")
    assert exact_calls, "expected exact current-row inspection before synthesis"
    first_exact_name = min(
        ("list_context", "count_context"),
        key=lambda name: tool_names.index(name) if name in tool_names else len(tool_names),
    )
    first_exact = _find_first_tool_call(outcome.tool_calls, first_exact_name)
    first_exact_args = first_exact["args"] or {}
    assert str(first_exact_args.get("order_by") or "updated_at").strip().lower() == "updated_at"
    first_exact_index = tool_names.index(first_exact_name)
    if "search_context" in tool_names:
        assert first_exact_index < tool_names.index("search_context")

    fetch_calls = _find_all_tool_calls(outcome.tool_calls, "get_context")
    assert fetch_calls, "expected full current support to be fetched"
    fetched_records = _returned_records_for_calls(outcome.tool_returns, fetch_calls)
    fetched_ids = {str(record.get("record_id")) for record in fetched_records}
    assert expectation["required_current_record_id"] in fetched_ids
    current_record = next(
        record for record in fetched_records
        if str(record.get("record_id")) == expectation["required_current_record_id"]
    )
    current_support = _records_text([current_record])
    assert "timestamp anchoring" in current_support
    assert "active durable" in current_support

    for token in expectation["answer_must_include_all"]:
        assert token in answer
    assert any(token in answer for token in expectation["answer_must_include_any_provenance"])
    for token in expectation["answer_must_not_include"]:
        assert token not in answer


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
    assert any(tool_name in tool_names for tool_name in expectation["must_use_any_tools"])
    for tool_name in expectation.get("must_not_use_tools", []):
        assert tool_name not in tool_names

    exact_call = _find_exact_time_window_call(outcome.tool_calls, kind="decision")
    args = exact_call["args"] or {}
    assert isinstance(args, dict)
    assert _has_time_window(args)
    assert _has_kind_filter(args, "decision")
    exact_index = _tool_call_index(outcome.tool_calls, exact_call)
    if "search_context" in tool_names:
        assert exact_index < tool_names.index("search_context")

    fetch_calls = _find_all_tool_calls(outcome.tool_calls, "get_context")
    if fetch_calls:
        fetch_index = tool_names.index("get_context")
        assert exact_index < fetch_index

    returned_support = _records_text(
        _returned_records_for_calls(outcome.tool_returns, [exact_call] + fetch_calls)
    )
    for token in expectation["answer_must_include_all"]:
        assert token in returned_support
    for token in expectation["answer_must_not_include"]:
        assert token not in returned_support

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
    assert any(tool_name in tool_names for tool_name in expectation["must_use_any_tools"])
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    exact_call = _find_exact_time_window_call(outcome.tool_calls, kind="decision")
    args = exact_call["args"] or {}
    assert isinstance(args, dict)
    assert _has_time_window(args)
    assert _has_kind_filter(args, "decision")
    if "search_context" in tool_names:
        assert _tool_call_index(outcome.tool_calls, exact_call) < tool_names.index("search_context")
    _assert_no_widening_after_zero_result(
        tool_calls=outcome.tool_calls,
        tool_returns=outcome.tool_returns,
        exact_call=exact_call,
    )

    for token in expectation["answer_must_include_any"]:
        if token in answer:
            break
    else:
        has_time_anchor = _has_time_anchor(answer)
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
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names

    narrow_call = _find_exact_time_window_call(outcome.tool_calls)
    narrow_index = _tool_call_index(outcome.tool_calls, narrow_call)
    fetch_index = tool_names.index("get_context")
    assert narrow_index < fetch_index
    if "search_context" in tool_names:
        search_index = tool_names.index("search_context")
        assert narrow_index < search_index < fetch_index

    fetch_calls = _find_all_tool_calls(outcome.tool_calls, "get_context")
    returned_support = _records_text(_returned_records_for_calls(outcome.tool_returns, fetch_calls))
    for token in expectation["answer_must_include_all"]:
        assert token in returned_support
    for token in expectation.get("answer_must_not_include", []):
        assert token not in returned_support

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
    assert any(tool_name in tool_names for tool_name in expectation["must_use_any_tools"])

    first_exact = _find_exact_time_window_call(outcome.tool_calls)
    args = first_exact["args"] or {}
    assert isinstance(args, dict)
    assert _has_time_window(args)
    if "search_context" in tool_names:
        search_index = tool_names.index("search_context")
        exact_index = _tool_call_index(outcome.tool_calls, first_exact)
        assert exact_index < search_index
    _assert_no_widening_after_zero_result(
        tool_calls=outcome.tool_calls,
        tool_returns=outcome.tool_returns,
        exact_call=first_exact,
    )

    for token in expectation["answer_must_include_any"]:
        if token in answer:
            break
    else:
        has_time_anchor = _has_time_anchor(answer)
        has_negative_signal = any(token in answer for token in ("nothing", "no records", "no evidence"))
        if not (has_time_anchor and has_negative_signal):
            raise AssertionError(f"answer missing expected negative phrasing: {answer}")
    for token in expectation["answer_must_not_include"]:
        assert token not in answer
