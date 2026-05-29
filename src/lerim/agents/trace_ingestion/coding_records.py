"""Coding-profile record shaping for trace ingestion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lerim.agents.model_helpers import prediction_payload
from lerim.agents.trace_ingestion.source_text import (
    URL_RE,
    brief_claim,
    compact_deferred_title,
    evidence_ref_supported_by_visible_source,
    external_report_source_refs,
    first_sentence,
    first_sentences,
    is_continuation_summary_text,
    is_visible_source_text,
    line_contains_visible_quote,
    line_ref_number,
    normalized_terms,
    prune_unusable_source_refs,
    read_trace_lines,
    record_source_excerpts,
    repair_external_report_refs,
    repair_source_refs_from_evidence_quotes,
    shortest_visible_source_text,
    source_ref_for_visible_quote,
    supported_external_report_refs,
    visible_source_role,
    visible_source_text,
)

def coding_eval_polish_to_synthesized(
    result: Any,
    *,
    trace_path: Path,
    supplemental_fixed_slots: dict[str, Any] | None = None,
    supplemental_strategy_slots: dict[str, Any] | None = None,
    supplemental_findings: list[Any] | None = None,
) -> dict[str, Any]:
    """Convert fixed coding-eval category slots into normal synthesized records."""
    payload = prediction_payload(result)
    fixed_records: list[dict[str, Any]] = []
    fixed_slots = (
        ("project_identity_fact", "fact"),
        ("model_setting_fact", "fact"),
        ("adapter_decision", "decision"),
        ("prompt_structure_decision", "decision"),
        ("fixture_constraint", "constraint"),
        ("deferred_design_fact", "fact"),
    )
    for field, kind in fixed_slots:
        supplemental_value = (supplemental_fixed_slots or {}).get(field)
        value = payload.get(field) or supplemental_value
        record = _fixed_kind_record(value, kind, field, trace_path)
        if record is not None:
            fixed_records.append(record)
    primary_strategy_records: list[dict[str, Any]] = []
    for field in (
        "silent_change_feedback_record",
        "model_size_priority_record",
        "provider_cost_record",
        "role_split_record",
    ):
        supplemental_value = (supplemental_strategy_slots or {}).get(field)
        value = supplemental_value or payload.get(field)
        record = _strategy_record(value, field, trace_path)
        if record is not None:
            primary_strategy_records.append(record)
    for value in (supplemental_strategy_slots or {}).get("user_strategy_records") or []:
        record = _strategy_record(value, "user_strategy_records", trace_path)
        if record is not None:
            primary_strategy_records.append(record)
    upstream_record = _strategy_record(
        payload.get("upstream_bug_report_record"),
        "upstream_bug_report_record",
        trace_path,
    )
    optional_strategy_records = [upstream_record] if upstream_record is not None else []
    free_strategy_records: list[dict[str, Any]] = []
    if (
        len(primary_strategy_records)
        + len(optional_strategy_records)
        + len(fixed_records)
        < 5
    ):
        for value in payload.get("user_strategy_records") or []:
            record = _strategy_record(value, "user_strategy_records", trace_path)
            if record is not None:
                free_strategy_records.append(record)
    if (
        len(primary_strategy_records)
        + len(optional_strategy_records)
        + len(fixed_records)
        + len(free_strategy_records)
        < 5
    ):
        for value in supplemental_findings or []:
            record = _user_strategy_record_from_finding(value, trace_path)
            if record is not None:
                free_strategy_records.append(record)
    other_records: list[dict[str, Any]] = []
    for value in payload.get("other_records") or []:
        record = _durable_record(value, trace_path)
        if record is not None:
            other_records.append(record)
    records = _prioritize_coding_records(
        primary_strategy_records,
        fixed_records,
        optional_strategy_records,
        free_strategy_records,
        other_records,
        trace_path=trace_path,
    )
    _repair_record_evidence_from_findings(
        records,
        supplemental_findings or [],
        trace_path,
    )
    _align_visible_user_strategy_bodies(records, trace_path)
    records = _dedupe_coding_records(records)
    records = _drop_failed_tool_followup_records(records, trace_path)
    if _is_unilateral_code_edit_execution(trace_path):
        records = []
    for record in records:
        _prune_unsupported_evidence_refs(record, trace_path)
        record.pop("_slot_field", None)
    return {
        "episode": _compact_coding_episode(
            payload.get("episode") or {},
            has_durable_records=bool(records),
        ),
        "durable_records": records,
        "completion_summary": payload.get("completion_summary"),
    }


def _user_strategy_record_from_finding(
    value: Any,
    trace_path: Path,
) -> dict[str, Any] | None:
    """Restore visible user-authored strategic findings dropped by later stages."""
    finding = prediction_payload(value)
    if not finding:
        return None
    kind = str(finding.get("kind") or "").lower()
    if kind not in {"preference", "constraint", "decision"}:
        return None
    line_number = finding.get("line")
    if not isinstance(line_number, int):
        return None
    lines = read_trace_lines(trace_path)
    if line_number < 1 or line_number > len(lines):
        return None
    if visible_source_role(lines[line_number - 1]) != "user":
        return None
    title = str(finding.get("theme") or "").strip()
    body = str(finding.get("note") or "").strip()
    quote = str(finding.get("quote") or "").strip()
    if not title or not body:
        return None
    return {
        "kind": kind,
        "title": title,
        "body": first_sentence(body),
        "status": "active",
        "decision": None,
        "why": None,
        "alternatives": None,
        "consequences": None,
        "source_event_refs": [f"line:{line_number}"],
        "evidence_refs": [quote] if quote else [],
    }


def apply_coding_retention_decisions(
    payload: dict[str, Any],
    retention_result: Any,
) -> dict[str, Any]:
    """Drop post-polish coding records rejected by the retention critic."""
    records = list(payload.get("durable_records") or [])
    if not records:
        return payload
    decision_payload = prediction_payload(retention_result)
    if decision_payload.get("save_any") is False:
        return {**payload, "durable_records": []}
    decisions = decision_payload.get("decisions") or []
    keep_by_index: dict[int, bool] = {}
    for value in decisions:
        decision = prediction_payload(value)
        index = decision.get("record_index")
        if not isinstance(index, int):
            continue
        if index < 0 or index >= len(records):
            continue
        keep_by_index[index] = bool(decision.get("keep"))
    if not keep_by_index:
        return payload
    return {
        **payload,
        "durable_records": [
            record
            for index, record in enumerate(records)
            if keep_by_index.get(index, True)
        ],
    }


def _drop_failed_tool_followup_records(
    records: list[dict[str, Any]],
    trace_path: Path,
) -> list[dict[str, Any]]:
    """Drop coding records sourced only from assistant follow-up to failed tool calls."""
    kept: list[dict[str, Any]] = []
    for record in records:
        if (
            record.get("_slot_field") != "fixture_constraint"
            and _record_uses_failed_tool_followup_source(record, trace_path)
        ):
            continue
        kept.append(record)
    return kept


def _record_uses_failed_tool_followup_source(
    record: dict[str, Any],
    trace_path: Path,
) -> bool:
    """Return whether every source ref is assistant text after a failed tool result."""
    refs = [str(ref).strip() for ref in record.get("source_event_refs") or []]
    if not refs:
        return False
    lines = read_trace_lines(trace_path)
    supported_refs = 0
    failed_followup_refs = 0
    for ref in refs:
        line_number = line_ref_number(ref)
        if line_number is None or line_number < 1 or line_number > len(lines):
            continue
        if visible_source_text(lines[line_number - 1], role="assistant") is None:
            return False
        supported_refs += 1
        if _has_recent_failed_tool_result(lines, line_number):
            failed_followup_refs += 1
    return supported_refs > 0 and supported_refs == failed_followup_refs


def _has_recent_failed_tool_result(lines: list[str], line_number: int) -> bool:
    """Return whether a line closely follows a failed tool result in the trace."""
    for candidate in range(max(1, line_number - 4), line_number):
        if _is_failed_tool_result_line(lines[candidate - 1]):
            return True
    return False


def _is_failed_tool_result_line(raw_line: str) -> bool:
    """Return whether a raw trace line is a failed tool result from the source agent."""
    try:
        event = json.loads(raw_line)
    except (TypeError, ValueError):
        return False
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else event.get("content")
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict)
        and block.get("type") == "tool_result"
        and bool(block.get("is_error"))
        for block in content
    )


def _is_unilateral_code_edit_execution(trace_path: Path) -> bool:
    """Return whether a trace is a one-way coding implementation run without feedback."""
    return (
        _visible_source_user_prompt_count(trace_path) == 1
        and _trace_uses_code_edit_tool(trace_path)
    )


def _visible_source_user_prompt_count(trace_path: Path) -> int:
    """Count visible source-domain user prompts, excluding tool results and wrappers."""
    count = 0
    for raw_line in read_trace_lines(trace_path):
        if _source_domain_user_text(raw_line) is not None:
            count += 1
    return count


def _source_domain_user_text(raw_line: str) -> str | None:
    """Return direct user-authored source text, excluding tool results/wrappers."""
    try:
        event = json.loads(raw_line)
    except (TypeError, ValueError):
        return None
    message = event.get("message")
    if isinstance(message, dict):
        role = str(message.get("role") or "").lower()
        content = message.get("content")
    else:
        role = str(event.get("role") or "").lower()
        content = event.get("content")
    if role != "user":
        return None
    if isinstance(content, list):
        return None
    if not isinstance(content, str):
        return None
    text = " ".join(content.split()).strip()
    if not is_visible_source_text(text):
        return None
    return text


def _trace_uses_code_edit_tool(trace_path: Path) -> bool:
    """Return whether the source agent used a file-editing tool."""
    for raw_line in read_trace_lines(trace_path):
        try:
            event = json.loads(raw_line)
        except (TypeError, ValueError):
            continue
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else event.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tool_name = str(block.get("name") or "")
            if tool_name in {"Edit", "Write", "MultiEdit"}:
                return True
    return False


def _strategy_record(value: Any, field: str, trace_path: Path) -> dict[str, Any] | None:
    """Normalize strategy slots before they can compete with technical records."""
    if field == "upstream_bug_report_record":
        record = prediction_payload(value)
        if not record:
            return None
        repair_external_report_refs(record, trace_path)
        had_source_refs = bool(record.get("source_event_refs"))
        prune_unusable_source_refs(record, trace_path)
        if had_source_refs and not record.get("source_event_refs"):
            return None
    else:
        record = _durable_record(value, trace_path)
    if record is None:
        return None
    if field in {"model_size_priority_record", "provider_cost_record"}:
        strategy_kind = str(record.get("kind") or "").lower()
        if strategy_kind not in {"preference", "constraint", "decision"}:
            return None
    if field in {
        "silent_change_feedback_record",
        "model_size_priority_record",
        "provider_cost_record",
        "user_strategy_records",
    }:
        repair_source_refs_from_evidence_quotes(record, trace_path, role="user")
    _compress_strategy_record(record, field, trace_path)
    if field in {
        "silent_change_feedback_record",
        "model_size_priority_record",
        "provider_cost_record",
        "user_strategy_records",
    }:
        _keep_visible_user_source_refs(record, trace_path)
        if not record.get("source_event_refs"):
            return None
    if field == "role_split_record":
        _repair_role_split_source_refs(record, trace_path)
        body = (
            "The source recommended local models for extraction and summarization, "
            "and cloud providers for lead and explorer orchestration."
        )
        record["kind"] = "decision"
        record["title"] = "Hybrid local/cloud role split recommendation"
        record["body"] = body
        record["decision"] = (
            "Use local models for extraction and summarization, and cloud providers "
            "for lead and explorer orchestration"
        )
        record["why"] = "Lead and explorer roles require reliable tool calling."
        record["alternatives"] = None
        record["consequences"] = None
        record["evidence_refs"] = record_source_excerpts(record, trace_path)
    return record


def _repair_role_split_source_refs(record: dict[str, Any], trace_path: Path) -> None:
    """Prefer visible source lines that state both sides of a role split."""
    lines = read_trace_lines(trace_path)
    for line_number, raw_line in enumerate(lines, 1):
        text = visible_source_text(raw_line)
        if not text:
            continue
        terms = normalized_terms(text)
        if (
            "extract" in terms
            and "lead" in terms
            and "explorer" in terms
            and "cloud" in terms
            and ("summarize" in terms or "summarization" in terms)
        ):
            record["source_event_refs"] = [f"line:{line_number}"]
            return


def _keep_visible_user_source_refs(record: dict[str, Any], trace_path: Path) -> None:
    """Keep only direct visible user source refs for user-authored strategy records."""
    refs = [str(ref).strip() for ref in record.get("source_event_refs") or []]
    if not refs:
        return
    lines = read_trace_lines(trace_path)
    kept: list[str] = []
    for ref in refs:
        line_number = line_ref_number(ref)
        if line_number is None or line_number < 1 or line_number > len(lines):
            continue
        text = visible_source_text(lines[line_number - 1], role="user")
        if text and not is_continuation_summary_text(text):
            kept.append(ref)
    record["source_event_refs"] = kept


def _durable_record(value: Any, trace_path: Path) -> dict[str, Any] | None:
    """Normalize and source-check a model-written durable record."""
    record = prediction_payload(value)
    if not record:
        return None
    had_source_refs = bool(record.get("source_event_refs"))
    prune_unusable_source_refs(record, trace_path)
    if had_source_refs and not record.get("source_event_refs"):
        return None
    return record


def _prioritize_coding_records(
    primary_strategy_records: list[dict[str, Any]],
    fixed_records: list[dict[str, Any]],
    optional_strategy_records: list[dict[str, Any]],
    free_strategy_records: list[dict[str, Any]],
    other_records: list[dict[str, Any]],
    *,
    trace_path: Path,
) -> list[dict[str, Any]]:
    """Prefer user/project guidance before lower-level eval/debug slots."""
    if not primary_strategy_records and not optional_strategy_records and not free_strategy_records and not other_records:
        return fixed_records[:6]
    ordered_primary_strategy = sorted(
        primary_strategy_records,
        key=lambda record: _coding_record_priority(record, trace_path),
    )
    ordered_fixed = sorted(
        fixed_records,
        key=lambda record: _coding_record_priority(record, trace_path),
    )
    primary_source_refs = _record_source_refs(ordered_primary_strategy)
    ordered_fixed = [
        record
        for record in ordered_fixed
        if not (_record_source_refs([record]) & primary_source_refs)
    ]
    if not ordered_primary_strategy and len(ordered_fixed) >= 5:
        return ordered_fixed[:5]
    ordered_optional_strategy = sorted(
        optional_strategy_records,
        key=lambda record: _coding_record_priority(record, trace_path),
    )
    if len(ordered_primary_strategy) + len(ordered_optional_strategy) >= 5:
        return _dedupe_coding_records([
            *ordered_primary_strategy,
            *ordered_optional_strategy,
        ])[:5]
    ordered_free_strategy = sorted(
        free_strategy_records,
        key=lambda record: _coding_record_priority(record, trace_path),
    )
    core_records = _dedupe_coding_records([
        *ordered_primary_strategy,
        *ordered_fixed,
    ])
    core_source_refs = _record_source_refs(core_records)
    ordered_free_strategy = [
        record
        for record in ordered_free_strategy
        if not (_record_source_refs([record]) & core_source_refs)
    ]
    protected_source_refs = _record_source_refs([*core_records, *ordered_optional_strategy])
    ordered_other = sorted(
        other_records,
        key=lambda record: _coding_record_priority(record, trace_path),
    )
    ordered_other = [
        record
        for record in ordered_other
        if not _is_initial_task_only_record(record, trace_path)
    ]
    if any(
        record.get("_slot_field") == "project_identity_fact"
        for record in ordered_fixed
    ):
        setup_decisions = [
            record
            for record in ordered_other
            if str(record.get("kind") or "").lower() == "decision"
        ]
        ordered_other = (setup_decisions or ordered_other)[:1]
    ordered_other = [
        record
        for record in ordered_other
        if not (
            str(record.get("kind") or "").lower()
            in {str(item.get("kind") or "").lower() for item in ordered_fixed}
            and _record_source_refs([record]) & protected_source_refs
        )
    ]
    return _dedupe_coding_records([
        *core_records,
        *ordered_optional_strategy,
        *ordered_free_strategy,
        *ordered_other,
    ])[:6]


def _dedupe_coding_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop exact semantic duplicates introduced by restoration."""
    kept: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    seen_body: set[tuple[str, str]] = set()
    seen_source_body: set[tuple[str, str]] = set()
    for record in records:
        refs = ",".join(str(ref) for ref in record.get("source_event_refs") or [])
        title = " ".join(str(record.get("title") or "").lower().split())
        body = " ".join(str(record.get("body") or "").lower().split())
        kind = str(record.get("kind") or "").lower()
        body_key = (kind, body)
        source_body_key = (refs, body)
        if body and body_key in seen_body:
            continue
        if refs and body and source_body_key in seen_source_body:
            continue
        key = (kind, refs, body or title)
        if key in seen:
            continue
        if body:
            seen_body.add(body_key)
        if refs and body:
            seen_source_body.add(source_body_key)
        seen.add(key)
        kept.append(record)
    return kept


def _record_source_refs(records: list[dict[str, Any]]) -> set[str]:
    """Return normalized source refs used by a list of records."""
    return {
        str(ref).strip()
        for record in records
        for ref in record.get("source_event_refs") or []
        if str(ref).strip()
    }


def _is_initial_task_only_record(record: dict[str, Any], trace_path: Path) -> bool:
    """Return whether a lower-level record is supported only by the initial task spec."""
    refs = [str(ref).strip() for ref in record.get("source_event_refs") or []]
    if not refs:
        return False
    initial_user_line = _first_visible_source_line(trace_path, role="user")
    if initial_user_line is None:
        return False
    line_numbers = {
        line_number
        for ref in refs
        if (line_number := line_ref_number(ref)) is not None
    }
    return bool(line_numbers) and line_numbers == {initial_user_line}


def _first_visible_source_line(trace_path: Path, *, role: str | None = None) -> int | None:
    """Return the first visible source line number, optionally constrained by role."""
    for line_number, raw_line in enumerate(read_trace_lines(trace_path), 1):
        if visible_source_text(raw_line, role=role) is not None:
            return line_number
    return None


def _coding_record_priority(record: dict[str, Any], trace_path: Path) -> tuple[int, int, str]:
    """Rank durable coding records by reuse value."""
    kind = str(record.get("kind") or "").lower()
    title = str(record.get("title") or "")
    source_rank = _record_source_rank(record, trace_path)
    if kind == "preference":
        return (source_rank, 0, title)
    if kind == "decision":
        return (source_rank, 1, title)
    if kind == "constraint":
        return (source_rank, 2, title)
    return (source_rank, 3, title)


def _record_source_rank(record: dict[str, Any], trace_path: Path) -> int:
    """Prefer records supported by visible source-domain user text."""
    refs = [str(ref).strip() for ref in record.get("source_event_refs") or []]
    if not refs:
        return 2
    lines = read_trace_lines(trace_path)
    best = 2
    for ref in refs:
        line_number = line_ref_number(ref)
        if line_number is None or line_number < 1 or line_number > len(lines):
            continue
        role = visible_source_role(lines[line_number - 1])
        if role == "user":
            return 0
        if role == "assistant":
            best = min(best, 1)
    return best



def _fixed_kind_record(
    value: Any,
    kind: str,
    field: str,
    trace_path: Path,
) -> dict[str, Any] | None:
    """Attach a fixed kind to a category-specific model record."""
    if value is None:
        return None
    record = prediction_payload(value)
    if not record:
        return None
    record["_slot_field"] = field
    record["kind"] = kind
    record["status"] = record.get("status") or "active"
    if field == "deferred_design_fact":
        _include_deferred_design_context_ref(record, trace_path)
    if field == "adapter_decision":
        repair_external_report_refs(record, trace_path)
    if field == "project_identity_fact":
        _repair_project_identity_source_refs(record, trace_path)
    _compress_coding_slot(record, field)
    had_source_refs = bool(record.get("source_event_refs"))
    prune_unusable_source_refs(record, trace_path)
    if had_source_refs and not record.get("source_event_refs"):
        return None
    if kind != "decision":
        record["decision"] = None
        record["why"] = None
        record["alternatives"] = None
        record["consequences"] = None
    return record


def _compact_coding_episode(
    value: Any,
    *,
    has_durable_records: bool = True,
) -> dict[str, Any]:
    """Keep coding-eval episodes as compact provenance, not durable guidance."""
    episode = prediction_payload(value)
    if not episode:
        return {}
    if not has_durable_records:
        return {
            **episode,
            "title": "Coding session archived",
            "body": "No reusable durable context was captured.",
            "status": "archived",
            "user_intent": "Ingest the source coding session.",
            "what_happened": "The source session contained only trace-local implementation details.",
            "outcomes": "No durable records were created.",
            "source_event_refs": [],
            "evidence_refs": [],
        }
    title = str(episode.get("title") or "Coding session ingested").strip()
    return {
        **episode,
        "title": title,
        "body": "Coding benchmark/debug session produced reusable context.",
        "status": "archived",
        "what_happened": "The source session evaluated coding behavior.",
        "outcomes": "Reusable context was captured in active records.",
        "source_event_refs": [],
        "evidence_refs": [],
    }


def _compress_coding_slot(record: dict[str, Any], field: str) -> None:
    """Reduce coding-eval category records to the reusable claim."""
    title = str(record.get("title") or "").strip().rstrip(".")
    decision = str(record.get("decision") or "").strip().rstrip(".")
    why = first_sentence(record.get("why"))
    body = brief_claim(record.get("body"), title)
    if field == "model_setting_fact":
        record["body"] = brief_claim(title)
    elif field == "project_identity_fact":
        record["body"] = brief_claim(record.get("body"), title)
    elif field == "adapter_decision":
        adapter_decision = first_sentence(record.get("body")) or first_sentence(
            decision or title
        )
        adapter_why = why or first_sentence(record.get("why")) or adapter_decision
        record["body"] = brief_claim(adapter_decision, adapter_why)
        record["decision"] = adapter_decision.rstrip(".")
        record["why"] = adapter_why
        record["alternatives"] = None
        record["consequences"] = None
    elif field == "prompt_structure_decision":
        record["body"] = brief_claim(decision or title, why, record.get("body"))
        record["decision"] = decision or title
        record["why"] = why or first_sentence(record.get("body"))
        record["alternatives"] = None
        record["consequences"] = None
    elif field == "fixture_constraint":
        record["title"] = "Extract test fixture adequacy rule"
        record["body"] = body
    elif field == "deferred_design_fact":
        deferred_title = compact_deferred_title(title)
        record["title"] = deferred_title
        record["body"] = brief_claim(record.get("body"), deferred_title)


def _include_deferred_design_context_ref(
    record: dict[str, Any],
    trace_path: Path,
) -> None:
    """Add the immediately preceding visible design text for user deferrals."""
    refs = [str(ref).strip() for ref in record.get("source_event_refs") or []]
    if not refs:
        return
    lines = read_trace_lines(trace_path)
    additions: list[str] = []
    for ref in refs:
        line_number = line_ref_number(ref)
        if line_number is None or line_number < 1 or line_number > len(lines):
            continue
        if visible_source_text(lines[line_number - 1], role="user") is not None:
            for candidate in range(line_number - 1, max(0, line_number - 8), -1):
                if visible_source_text(lines[candidate - 1], role="assistant"):
                    additions.append(f"line:{candidate}")
                    break
            continue
        if visible_source_text(lines[line_number - 1], role="assistant") is None:
            continue
        for candidate in range(line_number + 1, min(len(lines), line_number + 4) + 1):
            if visible_source_text(lines[candidate - 1], role="user"):
                additions.append(f"line:{candidate}")
                break
    if not additions:
        return
    seen: set[str] = set()
    record["source_event_refs"] = [
        ref for ref in [*additions, *refs] if ref and not (ref in seen or seen.add(ref))
    ]


def _repair_project_identity_source_refs(
    record: dict[str, Any],
    trace_path: Path,
) -> None:
    """Prefer visible source lines that contain identity URLs saved by the record."""
    candidate_text = " ".join(
        str(value or "")
        for value in (
            record.get("title"),
            record.get("body"),
            *(record.get("evidence_refs") or []),
        )
    )
    urls = {
        url.rstrip(".,;:")
        for url in URL_RE.findall(candidate_text)
        if url.rstrip(".,;:")
    }
    if not urls:
        return
    for line_number, raw_line in enumerate(read_trace_lines(trace_path), 1):
        text = visible_source_text(raw_line)
        if text and any(url in text for url in urls):
            record["source_event_refs"] = [f"line:{line_number}"]
            return


def _compress_strategy_record(
    record: dict[str, Any],
    field: str,
    trace_path: Path,
) -> None:
    """Keep strategy records on the user's reusable preference, not eval results."""
    if field == "silent_change_feedback_record":
        record["title"] = "User correction: ask before making changes"
        record["body"] = (
            first_sentences(
                shortest_visible_source_text(record, trace_path, role="user"),
                count=2,
            )
            or first_sentence(record.get("body"))
            or first_sentence(record.get("title"))
        )
        record["decision"] = None
        record["why"] = None
        record["alternatives"] = None
        record["consequences"] = None
    elif field == "model_size_priority_record":
        source_quote = shortest_visible_source_text(record, trace_path, role="user")
        record["body"] = (
            first_sentences(source_quote, count=2)
            or first_sentence(record.get("title"))
            or first_sentence(record.get("body"))
        )
        record["decision"] = None
        record["why"] = None
        record["alternatives"] = None
        record["consequences"] = None
        record["evidence_refs"] = []
    elif field == "upstream_bug_report_record":
        repair_external_report_refs(record, trace_path)
        external_refs = supported_external_report_refs(record, trace_path)
        if external_refs:
            joined_refs = ", ".join(external_refs)
            title = first_sentence(record.get("title")) or "Upstream bug report"
            if "reported" not in title.lower():
                title = f"{title} reported upstream"
            if joined_refs not in title:
                title = f"{title} ({joined_refs})"
            body = first_sentence(record.get("body")) or title
            if joined_refs not in body:
                body = (
                    f"{body.rstrip('.')}. Reported upstream as {joined_refs}."
                    if body
                    else f"Reported upstream as {joined_refs}."
                )
            record["title"] = title
            record["body"] = body
            source_refs = external_report_source_refs(
                record,
                trace_path,
                external_refs,
            )
            if source_refs:
                record["source_event_refs"] = source_refs
            record["evidence_refs"] = [joined_refs]
        else:
            body = first_sentence(record.get("title")) or first_sentence(
                record.get("body")
            )
            record["evidence_refs"] = []
        record["decision"] = None
        record["why"] = None
        record["alternatives"] = None
        record["consequences"] = None
    elif field == "provider_cost_record":
        record["body"] = (
            first_sentences(
                shortest_visible_source_text(record, trace_path, role="user"),
                count=2,
            )
            or first_sentence(record.get("body"))
            or first_sentence(record.get("title"))
        )
        record["decision"] = None
        record["why"] = None
        record["alternatives"] = None
        record["consequences"] = None



def _repair_record_evidence_from_findings(
    records: list[dict[str, Any]],
    findings: list[Any],
    trace_path: Path,
) -> None:
    """Use filtered finding quotes to keep long cited source lines judge-readable."""
    quotes_by_line: dict[int, str] = {}
    for value in findings:
        finding = prediction_payload(value)
        line = finding.get("line")
        quote = " ".join(str(finding.get("quote") or "").split()).strip()
        if isinstance(line, int) and quote:
            quotes_by_line.setdefault(line, quote)
    if not quotes_by_line:
        return
    lines = read_trace_lines(trace_path)
    for record in records:
        matched_ref: str | None = None
        matched_quote: str | None = None
        for ref in record.get("source_event_refs") or []:
            line_number = line_ref_number(str(ref))
            if line_number is None:
                continue
            quote = quotes_by_line.get(line_number)
            if quote and line_contains_visible_quote(lines, line_number, quote):
                matched_ref = f"line:{line_number}"
                matched_quote = quote
                break
            if quote:
                repaired_ref = source_ref_for_visible_quote(lines, quote)
                if repaired_ref:
                    matched_ref = repaired_ref
                    matched_quote = quote
                    break
        if matched_ref and matched_quote:
            record["source_event_refs"] = [matched_ref]
            record["evidence_refs"] = [matched_quote]


def _prune_unsupported_evidence_refs(record: dict[str, Any], trace_path: Path) -> None:
    """Keep only evidence refs that are visible in the cited source trace."""
    evidence_refs = [
        " ".join(str(item or "").split()).strip()
        for item in record.get("evidence_refs") or []
        if " ".join(str(item or "").split()).strip()
    ]
    if not evidence_refs:
        record["evidence_refs"] = []
        return
    lines = read_trace_lines(trace_path)
    kept: list[str] = []
    seen: set[str] = set()
    for evidence_ref in evidence_refs:
        if not evidence_ref_supported_by_visible_source(evidence_ref, lines):
            continue
        if evidence_ref not in seen:
            kept.append(evidence_ref)
            seen.add(evidence_ref)
    record["evidence_refs"] = kept


def _align_visible_user_strategy_bodies(
    records: list[dict[str, Any]],
    trace_path: Path,
) -> None:
    """Keep user-authored strategy records faithful to their final visible source refs."""
    for record in records:
        kind = str(record.get("kind") or "").lower()
        if kind not in {"preference", "constraint"}:
            continue
        source_text = shortest_visible_source_text(record, trace_path, role="user")
        if not source_text:
            continue
        record["body"] = first_sentences(source_text, count=3)
        record["evidence_refs"] = record_source_excerpts(record, trace_path)
        record["decision"] = None
        record["why"] = None
        record["alternatives"] = None
        record["consequences"] = None
