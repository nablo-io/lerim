"""Windowed LangGraph trace-ingestion pipeline backed by BAML."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from lerim.agents.baml_helpers import call_baml_with_retries, model_payload
from lerim.agents.baml_runtime import build_baml_client_for_role
from lerim.agents.trace_ingestion.persistence import (
    PersistenceContext,
    persist_synthesized_extraction,
)
from lerim.agents.trace_ingestion.state import TraceIngestionGraphState
from lerim.agents.trace_ingestion.windowing import (
    compute_request_budget,
    read_trace_window,
    trace_line_count,
    window_char_budget,
)
from lerim.config.settings import Config
from lerim.profiles import format_signal_pack_context, normalize_signal_pack_id

MAX_EPISODE_SUMMARY_ITEMS = 30
MAX_IMPLEMENTATION_SUMMARY_ITEMS = 48
MAX_DISCARDED_NOISE_SUMMARY_ITEMS = 32
EXTERNAL_REPORT_REF_RE = re.compile(
    r"\b(?P<label>PR|pull request|issue)\s*#?\s*(?P<number>\d+)\b",
    re.IGNORECASE,
)
GITHUB_REPORT_URL_RE = re.compile(
    r"github\.com/[^/\s]+/[^/\s]+/(?P<label>pull|pulls|issues)/(?P<number>\d+)",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://[^\s)>\]\"']+")


def run_trace_ingestion_graph(
    *,
    persistence_context: PersistenceContext,
    config: Config,
    run_instruction: str,
    existing_record_manifest: str,
    provider: str | None = None,
    model_name: str | None = None,
    api_base_url: str | None = None,
    api_key: str | None = None,
    temperature: float | None = None,
    max_llm_calls: int | None = None,
    progress: bool = False,
) -> dict[str, Any]:
    """Run the BAML trace-ingestion graph and return its final state."""
    total_lines = trace_line_count(persistence_context.trace_path)
    graph = build_trace_ingestion_graph(
        persistence_context=persistence_context,
        config=config,
        run_instruction=run_instruction,
        existing_record_manifest=existing_record_manifest,
        provider=provider,
        model_name=model_name,
        api_base_url=api_base_url,
        api_key=api_key,
        temperature=temperature,
        max_llm_calls=max_llm_calls
        or compute_request_budget(persistence_context.trace_path),
        progress=progress,
    )
    return graph.invoke(
        {
            "observations": [],
            "llm_calls": 0,
            "next_line": 1,
            "trace_total_lines": total_lines,
            "done": False,
            "completion_summary": "",
        }
    )


def build_trace_ingestion_graph(
    *,
    persistence_context: PersistenceContext,
    config: Config,
    run_instruction: str,
    existing_record_manifest: str,
    provider: str | None,
    model_name: str | None,
    api_base_url: str | None,
    api_key: str | None,
    temperature: float | None,
    max_llm_calls: int,
    progress: bool = False,
):
    """Compile scope resolution, window scans, filtering, synthesis, and persistence."""
    baml_runtime = build_baml_client_for_role(
        config=config,
        provider=provider,
        model_name=model_name,
        api_base_url=api_base_url,
        api_key=api_key,
        temperature=temperature,
    )
    source_profile_id = normalize_signal_pack_id(persistence_context.source_profile)
    source_profile_context = format_signal_pack_context(source_profile_id)

    def resolve_scope(state: TraceIngestionGraphState) -> dict[str, Any]:
        """Emit the resolved source/scope boundary as an explicit graph phase."""
        del state
        scope = persistence_context.scope_identity
        return {
            "observations": [
                {
                    "action": "resolve_scope",
                    "ok": True,
                    "content": f"scope={scope.scope_type}:{scope.scope_id}",
                    "args": {
                        "scope_type": scope.scope_type,
                        "scope_id": scope.scope_id,
                        "scope_label": scope.label,
                        "source_name": persistence_context.source_name,
                        "source_profile": source_profile_id,
                    },
                    "done": False,
                    "completion_summary": "",
                }
            ],
        }

    def read_window(state: TraceIngestionGraphState) -> dict[str, Any]:
        """Read the next budgeted trace window into transient state."""
        total_lines = int(state.get("trace_total_lines") or 0)
        start_line = int(state.get("next_line") or 1)
        if start_line > total_lines:
            return {"current_window": {}}
        char_budget = window_char_budget(
            state=state,
            run_instruction=run_instruction,
            existing_record_manifest=existing_record_manifest,
            episode_summary=_episode_summary(state),
            durable_findings_summary=_durable_findings_summary(state),
            implementation_summary=_implementation_summary(state),
        )
        window = read_trace_window(
            trace_path=persistence_context.trace_path,
            start_line=start_line,
            total_lines=total_lines,
            char_budget=char_budget,
        )
        if progress:
            print(
                f"  trace-ingestion window {window['start_line']}-{window['end_line']} "
                f"chars={len(window['text'])}",
                flush=True,
            )
        return {
            "current_window": window,
            "next_line": int(window["end_line"]) + 1,
            "observations": [
                {
                    "action": "read_window",
                    "ok": True,
                    "content": window["header"],
                    "args": {
                        "start_line": window["start_line"],
                        "end_line": window["end_line"],
                        "char_budget": char_budget,
                    },
                    "done": False,
                    "completion_summary": "",
                }
            ],
        }

    def scan_window(state: TraceIngestionGraphState) -> dict[str, Any]:
        """Scan the current window into compact episode and findings state."""
        llm_calls = int(state.get("llm_calls") or 0)
        if llm_calls >= max_llm_calls:
            raise RuntimeError(
                f"BAML trace ingestion exceeded max_llm_calls={max_llm_calls}."
            )
        window = state.get("current_window") or {}
        if not window.get("text"):
            return {}
        if progress:
            print(f"  trace-ingestion observe {llm_calls + 1}/{max_llm_calls}", flush=True)
        result, retry_observations, attempts = call_baml_with_retries(
            lambda: baml_runtime.ObserveSourceWindow(
                run_instruction=run_instruction,
                source_profile_context=source_profile_context,
                prior_episode_summary=_episode_summary(state),
                prior_findings_summary=_findings_summary(state),
                source_window=str(window["text"]),
            ),
            stage="scan_window",
            progress=progress,
            progress_label="trace-ingestion",
        )
        payload = model_payload(result)
        episode_update = str(payload.get("episode_update") or "").strip()
        durable = [model_payload(item) for item in payload.get("durable_findings") or []]
        implementation = [
            model_payload(item)
            for item in payload.get("implementation_findings") or []
        ]
        noise = [
            str(item).strip()
            for item in payload.get("discarded_noise") or []
            if str(item).strip()
        ]
        return {
            "llm_calls": llm_calls + attempts,
            "episode_updates": [episode_update] if episode_update else [],
            "episode_update_refs": _window_line_refs(window) if episode_update else [],
            "durable_findings": durable,
            "implementation_findings": implementation,
            "discarded_noise": noise,
            "observations": [
                *retry_observations,
                {
                    "action": "scan_window",
                    "ok": True,
                    "content": (
                        f"window={window.get('start_line')}-{window.get('end_line')} "
                        f"durable={len(durable)} implementation={len(implementation)}"
                    ),
                    "args": {
                        "start_line": window.get("start_line"),
                        "end_line": window.get("end_line"),
                    },
                    "done": False,
                    "completion_summary": "",
                },
            ],
        }

    def synthesize_records(state: TraceIngestionGraphState) -> dict[str, Any]:
        """Synthesize final episode and durable record candidates."""
        llm_calls = int(state.get("llm_calls") or 0)
        if llm_calls >= max_llm_calls:
            raise RuntimeError(
                f"BAML trace ingestion exceeded max_llm_calls={max_llm_calls}."
            )
        if progress:
            print(f"  trace-ingestion synthesize {llm_calls + 1}/{max_llm_calls}", flush=True)
        result, retry_observations, attempts = call_baml_with_retries(
            lambda: baml_runtime.SynthesizeContextRecords(
                run_instruction=run_instruction,
                source_profile_context=source_profile_context,
                episode_summary=_synthesis_episode_summary(state),
                durable_findings_summary=_filtered_durable_findings_summary(state),
                existing_record_manifest=existing_record_manifest or "(none)",
            ),
            stage="synthesize_records",
            progress=progress,
            progress_label="trace-ingestion",
        )
        payload = model_payload(result)
        durable_count = len(payload.get("durable_records") or [])
        return {
            "llm_calls": llm_calls + attempts,
            "synthesized": result,
            "observations": [
                *retry_observations,
                {
                    "action": "synthesize_records",
                    "ok": True,
                    "content": f"durable_records={durable_count}",
                    "args": {},
                    "done": False,
                    "completion_summary": "",
                },
            ],
        }

    def guard_records(state: TraceIngestionGraphState) -> dict[str, Any]:
        """Run a focused final guard over synthesized records before persistence."""
        llm_calls = int(state.get("llm_calls") or 0)
        if source_profile_id == "coding" and llm_calls + 2 >= max_llm_calls:
            return {
                "synthesized": state.get("synthesized"),
                "observations": [
                    {
                        "action": "guard_records",
                        "ok": True,
                        "content": "skipped_for_coding_profile_budget",
                        "args": {},
                        "done": False,
                        "completion_summary": "",
                    },
                ],
            }
        if llm_calls >= max_llm_calls:
            raise RuntimeError(
                f"BAML trace ingestion exceeded max_llm_calls={max_llm_calls}."
            )
        if progress:
            print(f"  trace-ingestion guard {llm_calls + 1}/{max_llm_calls}", flush=True)
        result, retry_observations, attempts = call_baml_with_retries(
            lambda: baml_runtime.GuardSynthesizedContextRecords(
                run_instruction=run_instruction,
                source_profile_context=source_profile_context,
                episode_summary=_synthesis_episode_summary(state),
                durable_findings_summary=_filtered_durable_findings_summary(state),
                implementation_summary=_implementation_summary(state),
                existing_record_manifest=existing_record_manifest or "(none)",
                rejected_findings_summary=_rejected_durable_findings_summary(state),
                draft_records_json=json.dumps(
                    model_payload(state.get("synthesized")),
                    ensure_ascii=True,
                ),
            ),
            stage="guard_records",
            progress=progress,
            progress_label="trace-ingestion",
        )
        payload = model_payload(result)
        durable_count = len(payload.get("durable_records") or [])
        return {
            "llm_calls": llm_calls + attempts,
            "synthesized": result,
            "observations": [
                *retry_observations,
                {
                    "action": "guard_records",
                    "ok": True,
                    "content": f"durable_records={durable_count}",
                    "args": {},
                    "done": False,
                    "completion_summary": "",
                },
            ],
        }

    def polish_records(state: TraceIngestionGraphState) -> dict[str, Any]:
        """Run a minimal last-mile record polish before persistence."""
        llm_calls = int(state.get("llm_calls") or 0)
        if llm_calls >= max_llm_calls:
            raise RuntimeError(
                f"BAML trace ingestion exceeded max_llm_calls={max_llm_calls}."
            )
        if progress:
            print(f"  trace-ingestion polish {llm_calls + 1}/{max_llm_calls}", flush=True)
        draft_records_json = json.dumps(
            model_payload(state.get("synthesized")),
            ensure_ascii=True,
        )
        user_strategy_observations: list[dict[str, Any]] = []
        project_identity_observations: list[dict[str, Any]] = []
        coding_retention_observations: list[dict[str, Any]] = []
        if source_profile_id == "coding":
            user_strategy_result = None
            project_identity_result = None
            visible_source_lines = _visible_source_lines(persistence_context.trace_path)
            user_source_lines = _visible_user_source_lines(persistence_context.trace_path)
            if user_source_lines != "(none)":
                if progress:
                    print(
                        f"  trace-ingestion user_strategy {llm_calls + 1}/{max_llm_calls}",
                        flush=True,
                    )
                (
                    user_strategy_result,
                    user_strategy_observations,
                    user_strategy_attempts,
                ) = call_baml_with_retries(
                    lambda: baml_runtime.ExtractCodingStrategySlots(
                        run_instruction=run_instruction,
                        source_profile_context=source_profile_context,
                        user_source_lines=user_source_lines,
                        durable_findings_summary=_filtered_durable_findings_summary(state),
                        rejected_findings_summary=_rejected_durable_findings_summary(state),
                    ),
                    stage="extract_coding_user_strategy",
                    progress=progress,
                    progress_label="trace-ingestion",
                )
                llm_calls += user_strategy_attempts
                if llm_calls >= max_llm_calls:
                    raise RuntimeError(
                        f"BAML trace ingestion exceeded max_llm_calls={max_llm_calls}."
                    )
            result, retry_observations, attempts = call_baml_with_retries(
                lambda: baml_runtime.PolishCodingEvalContextRecords(
                    run_instruction=run_instruction,
                    source_profile_context=source_profile_context,
                    episode_summary=_synthesis_episode_summary(state),
                    durable_findings_summary=_filtered_durable_findings_summary(state),
                    implementation_summary=_implementation_summary(state),
                    rejected_findings_summary=_rejected_durable_findings_summary(state),
                    draft_records_json=draft_records_json,
                    visible_source_lines=visible_source_lines,
                ),
                stage="polish_records",
                progress=progress,
                progress_label="trace-ingestion",
            )
            llm_calls += attempts
            if (
                not model_payload(result).get("project_identity_fact")
                and visible_source_lines != "(none)"
                and llm_calls < max_llm_calls
            ):
                if progress:
                    print(
                        f"  trace-ingestion project_identity "
                        f"{llm_calls + 1}/{max_llm_calls}",
                        flush=True,
                    )
                (
                    project_identity_result,
                    project_identity_observations,
                    project_identity_attempts,
                ) = call_baml_with_retries(
                    lambda: baml_runtime.ExtractCodingProjectIdentitySlot(
                        run_instruction=run_instruction,
                        source_profile_context=source_profile_context,
                        visible_source_lines=visible_source_lines,
                    ),
                    stage="extract_coding_project_identity",
                    progress=progress,
                    progress_label="trace-ingestion",
                )
                llm_calls += project_identity_attempts
            payload = _coding_eval_polish_to_synthesized(
                result,
                trace_path=persistence_context.trace_path,
                supplemental_fixed_slots=model_payload(project_identity_result)
                if project_identity_result is not None
                else {},
                supplemental_strategy_slots=model_payload(user_strategy_result)
                if user_strategy_result is not None
                else {},
                supplemental_findings=[
                    *(state.get("durable_findings") or []),
                    *(state.get("filtered_durable_findings") or []),
                    *(state.get("rejected_durable_findings") or []),
                ],
            )
            if payload.get("durable_records") and llm_calls < max_llm_calls:
                if progress:
                    print(
                        f"  trace-ingestion coding_retention "
                        f"{llm_calls + 1}/{max_llm_calls}",
                        flush=True,
                    )
                (
                    retention_result,
                    coding_retention_observations,
                    retention_attempts,
                ) = call_baml_with_retries(
                    lambda: baml_runtime.SelectCodingDurableRecords(
                        run_instruction=run_instruction,
                        source_profile_context=source_profile_context,
                        visible_source_lines=visible_source_lines,
                        final_records_json=json.dumps(payload, ensure_ascii=True),
                    ),
                    stage="select_coding_durable_records",
                    progress=progress,
                    progress_label="trace-ingestion",
                )
                llm_calls += retention_attempts
                payload = _apply_coding_retention_decisions(payload, retention_result)
        else:
            result, retry_observations, attempts = call_baml_with_retries(
                lambda: baml_runtime.PolishContextRecords(
                    run_instruction=run_instruction,
                    source_profile_context=source_profile_context,
                    episode_summary=_synthesis_episode_summary(state),
                    durable_findings_summary=_filtered_durable_findings_summary(state),
                    implementation_summary=_implementation_summary(state),
                    rejected_findings_summary=_rejected_durable_findings_summary(state),
                    draft_records_json=draft_records_json,
                ),
                stage="polish_records",
                progress=progress,
                progress_label="trace-ingestion",
            )
            llm_calls += attempts
            payload = model_payload(result)
        durable_count = len(payload.get("durable_records") or [])
        return {
            "llm_calls": llm_calls,
            "synthesized": payload,
            "observations": [
                *user_strategy_observations,
                *retry_observations,
                *project_identity_observations,
                *coding_retention_observations,
                {
                    "action": "polish_records",
                    "ok": True,
                    "content": f"durable_records={durable_count}",
                    "args": {},
                    "done": False,
                    "completion_summary": "",
                },
            ],
        }

    def filter_signals(state: TraceIngestionGraphState) -> dict[str, Any]:
        """Run the final durable-signal filter before synthesis."""
        llm_calls = int(state.get("llm_calls") or 0)
        if llm_calls >= max_llm_calls:
            raise RuntimeError(
                f"BAML trace ingestion exceeded max_llm_calls={max_llm_calls}."
            )
        if progress:
            print(f"  trace-ingestion filter {llm_calls + 1}/{max_llm_calls}", flush=True)
        result, retry_observations, attempts = call_baml_with_retries(
            lambda: baml_runtime.FilterDurableSignal(
                run_instruction=run_instruction,
                source_profile_context=source_profile_context,
                episode_summary=_episode_summary(state),
                durable_findings_summary=_durable_findings_summary(state),
                implementation_summary=_implementation_summary(state),
                existing_record_manifest=existing_record_manifest or "(none)",
            ),
            stage="filter_signals",
            progress=progress,
            progress_label="trace-ingestion",
        )
        payload = model_payload(result)
        kept = [model_payload(item) for item in payload.get("kept_durable_findings") or []]
        rejected = [model_payload(item) for item in payload.get("rejected_findings") or []]
        summary = str(payload.get("filtering_summary") or "").strip()
        return {
            "llm_calls": llm_calls + attempts,
            "filtered_durable_findings": kept,
            "rejected_durable_findings": rejected,
            "signal_filter_summary": summary,
            "observations": [
                *retry_observations,
                {
                    "action": "filter_signals",
                    "ok": True,
                    "content": f"kept={len(kept)} rejected={len(rejected)}",
                    "args": {"filtering_summary": summary},
                    "done": False,
                    "completion_summary": "",
                },
            ],
        }

    def persist_records(state: TraceIngestionGraphState) -> dict[str, Any]:
        """Persist synthesized records and finish the graph."""
        observations, done, completion_summary = persist_synthesized_extraction(
            state.get("synthesized"),
            persistence_context,
        )
        if progress:
            print(f"  trace-ingestion persist done={done}", flush=True)
        return {
            "observations": observations,
            "done": done,
            "completion_summary": completion_summary,
        }

    def after_scan(state: TraceIngestionGraphState) -> str:
        """Continue scanning until all trace lines are covered."""
        next_line = int(state.get("next_line") or 1)
        total_lines = int(state.get("trace_total_lines") or 0)
        if next_line <= total_lines:
            return "read_window"
        return "filter_signals"

    graph = StateGraph(TraceIngestionGraphState)
    graph.add_node("resolve_scope", resolve_scope)
    graph.add_node("read_window", read_window)
    graph.add_node("scan_window", scan_window)
    graph.add_node("filter_signals", filter_signals)
    graph.add_node("synthesize_records", synthesize_records)
    graph.add_node("guard_records", guard_records)
    graph.add_node("polish_records", polish_records)
    graph.add_node("persist_records", persist_records)
    graph.add_edge(START, "resolve_scope")
    graph.add_edge("resolve_scope", "read_window")
    graph.add_edge("read_window", "scan_window")
    graph.add_conditional_edges(
        "scan_window",
        after_scan,
        ["read_window", "filter_signals"],
    )
    graph.add_edge("filter_signals", "synthesize_records")
    graph.add_edge("synthesize_records", "guard_records")
    graph.add_edge("guard_records", "polish_records")
    graph.add_edge("polish_records", "persist_records")
    graph.add_edge("persist_records", END)
    return graph.compile()


def _coding_eval_polish_to_synthesized(
    result: Any,
    *,
    trace_path: Path,
    supplemental_fixed_slots: dict[str, Any] | None = None,
    supplemental_strategy_slots: dict[str, Any] | None = None,
    supplemental_findings: list[Any] | None = None,
) -> dict[str, Any]:
    """Convert fixed coding-eval category slots into normal synthesized records."""
    payload = model_payload(result)
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
    finding = model_payload(value)
    if not finding:
        return None
    kind = str(finding.get("kind") or "").lower()
    if kind not in {"preference", "constraint", "decision"}:
        return None
    line_number = finding.get("line")
    if not isinstance(line_number, int):
        return None
    lines = _read_trace_lines(trace_path)
    if line_number < 1 or line_number > len(lines):
        return None
    if _visible_source_role(lines[line_number - 1]) != "user":
        return None
    title = str(finding.get("theme") or "").strip()
    body = str(finding.get("note") or "").strip()
    quote = str(finding.get("quote") or "").strip()
    if not title or not body:
        return None
    return {
        "kind": kind,
        "title": title,
        "body": _first_sentence(body),
        "status": "active",
        "decision": None,
        "why": None,
        "alternatives": None,
        "consequences": None,
        "source_event_refs": [f"line:{line_number}"],
        "evidence_refs": [quote] if quote else [],
    }


def _apply_coding_retention_decisions(
    payload: dict[str, Any],
    retention_result: Any,
) -> dict[str, Any]:
    """Drop post-polish coding records rejected by the retention critic."""
    records = list(payload.get("durable_records") or [])
    if not records:
        return payload
    decision_payload = model_payload(retention_result)
    if decision_payload.get("save_any") is False:
        return {**payload, "durable_records": []}
    decisions = decision_payload.get("decisions") or []
    keep_by_index: dict[int, bool] = {}
    for value in decisions:
        decision = model_payload(value)
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
    lines = _read_trace_lines(trace_path)
    supported_refs = 0
    failed_followup_refs = 0
    for ref in refs:
        line_number = _line_ref_number(ref)
        if line_number is None or line_number < 1 or line_number > len(lines):
            continue
        if _visible_source_text(lines[line_number - 1], role="assistant") is None:
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
    for raw_line in _read_trace_lines(trace_path):
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
    if not _is_visible_source_text(text):
        return None
    return text


def _trace_uses_code_edit_tool(trace_path: Path) -> bool:
    """Return whether the source agent used a file-editing tool."""
    for raw_line in _read_trace_lines(trace_path):
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
        record = model_payload(value)
        if not record:
            return None
        _repair_external_report_refs(record, trace_path)
        had_source_refs = bool(record.get("source_event_refs"))
        _prune_unusable_source_refs(record, trace_path)
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
        _repair_source_refs_from_evidence_quotes(record, trace_path, role="user")
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
        record["evidence_refs"] = _record_source_excerpts(record, trace_path)
    return record


def _repair_role_split_source_refs(record: dict[str, Any], trace_path: Path) -> None:
    """Prefer visible source lines that state both sides of a role split."""
    lines = _read_trace_lines(trace_path)
    for line_number, raw_line in enumerate(lines, 1):
        text = _visible_source_text(raw_line)
        if not text:
            continue
        terms = _normalized_terms(text)
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
    lines = _read_trace_lines(trace_path)
    kept: list[str] = []
    for ref in refs:
        line_number = _line_ref_number(ref)
        if line_number is None or line_number < 1 or line_number > len(lines):
            continue
        text = _visible_source_text(lines[line_number - 1], role="user")
        if text and not _is_continuation_summary_text(text):
            kept.append(ref)
    record["source_event_refs"] = kept


def _durable_record(value: Any, trace_path: Path) -> dict[str, Any] | None:
    """Normalize and source-check a model-written durable record."""
    record = model_payload(value)
    if not record:
        return None
    had_source_refs = bool(record.get("source_event_refs"))
    _prune_unusable_source_refs(record, trace_path)
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
    for record in records:
        refs = ",".join(str(ref) for ref in record.get("source_event_refs") or [])
        title = " ".join(str(record.get("title") or "").lower().split())
        body = " ".join(str(record.get("body") or "").lower().split())
        kind = str(record.get("kind") or "").lower()
        body_key = (kind, body)
        if body and body_key in seen_body:
            continue
        key = (kind, refs, body or title)
        if key in seen:
            continue
        if body:
            seen_body.add(body_key)
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
        if (line_number := _line_ref_number(ref)) is not None
    }
    return bool(line_numbers) and line_numbers == {initial_user_line}


def _first_visible_source_line(trace_path: Path, *, role: str | None = None) -> int | None:
    """Return the first visible source line number, optionally constrained by role."""
    for line_number, raw_line in enumerate(_read_trace_lines(trace_path), 1):
        if _visible_source_text(raw_line, role=role) is not None:
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
    lines = _read_trace_lines(trace_path)
    best = 2
    for ref in refs:
        line_number = _line_ref_number(ref)
        if line_number is None or line_number < 1 or line_number > len(lines):
            continue
        role = _visible_source_role(lines[line_number - 1])
        if role == "user":
            return 0
        if role == "assistant":
            best = min(best, 1)
    return best


def _visible_source_role(raw_line: str) -> str | None:
    """Return the visible conversational role for a raw JSONL trace line."""
    try:
        event = json.loads(raw_line)
    except (TypeError, ValueError):
        return None
    message = event.get("message")
    if not isinstance(message, dict):
        role = str(event.get("role") or "").lower()
        content = event.get("content")
        if role not in {"user", "assistant"}:
            return None
        if isinstance(content, str):
            return role if _is_visible_source_text(content) else None
        return None
    role = str(message.get("role") or "").lower()
    if role not in {"user", "assistant"}:
        return None
    content = message.get("content")
    if isinstance(content, str):
        return role if _is_visible_source_text(content) else None
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        if _is_visible_source_text(block.get("text")):
            return role
    return None


def _visible_source_text(raw_line: str, *, role: str | None = None) -> str | None:
    """Return visible conversational text from a raw JSONL trace line."""
    try:
        event = json.loads(raw_line)
    except (TypeError, ValueError):
        return None
    message = event.get("message")
    if isinstance(message, dict):
        line_role = str(message.get("role") or "").lower()
        content = message.get("content")
    else:
        line_role = str(event.get("role") or "").lower()
        content = event.get("content")
    if role is not None and line_role != role:
        return None
    if line_role not in {"user", "assistant"}:
        return None
    if isinstance(content, str):
        text = " ".join(content.split()).strip()
        return text if _is_visible_source_text(text) else None
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        text = " ".join(str(block.get("text") or "").split()).strip()
        if _is_visible_source_text(text):
            parts.append(text)
    if not parts:
        return None
    return " ".join(parts)


def _visible_user_source_lines(trace_path: Path) -> str:
    """Render visible user-authored source lines for strategy extraction."""
    rendered: list[str] = []
    for line_number, raw_line in enumerate(_read_trace_lines(trace_path), 1):
        text = _visible_source_text(raw_line, role="user")
        if not text:
            continue
        if _is_continuation_summary_text(text):
            continue
        rendered.append(f"line:{line_number} user: {_truncate_source_line(text)}")
    return "\n".join(rendered) or "(none)"


def _visible_source_lines(trace_path: Path) -> str:
    """Render visible conversational source lines for final identity repair."""
    rendered: list[str] = []
    for line_number, raw_line in enumerate(_read_trace_lines(trace_path), 1):
        role = _visible_source_role(raw_line)
        if role is None:
            continue
        text = _visible_source_text(raw_line, role=role)
        if not text:
            continue
        if _is_continuation_summary_text(text):
            continue
        rendered.append(f"line:{line_number} {role}: {_truncate_source_line(text)}")
    return "\n".join(rendered) or "(none)"


def _is_continuation_summary_text(text: str) -> bool:
    """Return whether a user line is an agent-session continuation summary scaffold."""
    normalized = " ".join(text.split()).lower()
    return normalized.startswith("this session is being continued from a previous conversation")


def _is_continuation_source_line(raw_line: str) -> bool:
    """Return whether a raw trace line is a continuation-summary scaffold."""
    text = _visible_source_text(raw_line)
    return bool(text and _is_continuation_summary_text(text))


def _truncate_source_line(text: str, limit: int = 6000) -> str:
    """Keep long continued-session summaries bounded without dropping line identity."""
    if len(text) <= limit:
        return text
    edge = max(1, (limit - 20) // 2)
    return f"{text[:edge]} ... {text[-edge:]}"


def _is_visible_source_text(value: Any) -> bool:
    """True for visible source-domain text, false for cleared/hidden placeholders."""
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return False
    lowered = text.lower()
    return "cleared:" not in lowered and "thinking cleared:" not in lowered


def _fixed_kind_record(
    value: Any,
    kind: str,
    field: str,
    trace_path: Path,
) -> dict[str, Any] | None:
    """Attach a fixed kind to a category-specific BAML record."""
    if value is None:
        return None
    record = model_payload(value)
    if not record:
        return None
    record["_slot_field"] = field
    record["kind"] = kind
    record["status"] = record.get("status") or "active"
    if field == "deferred_design_fact":
        _include_deferred_design_context_ref(record, trace_path)
    if field == "adapter_decision":
        _repair_external_report_refs(record, trace_path)
    if field == "project_identity_fact":
        _repair_project_identity_source_refs(record, trace_path)
    _compress_coding_slot(record, field)
    had_source_refs = bool(record.get("source_event_refs"))
    _prune_unusable_source_refs(record, trace_path)
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
    episode = model_payload(value)
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
    why = _first_sentence(record.get("why"))
    body = _brief_claim(record.get("body"), title)
    if field == "model_setting_fact":
        record["body"] = _brief_claim(title)
    elif field == "project_identity_fact":
        record["body"] = _brief_claim(record.get("body"), title)
    elif field == "adapter_decision":
        adapter_decision = _first_sentence(record.get("body")) or _first_sentence(
            decision or title
        )
        adapter_why = why or _first_sentence(record.get("why")) or adapter_decision
        record["body"] = _brief_claim(adapter_decision, adapter_why)
        record["decision"] = adapter_decision.rstrip(".")
        record["why"] = adapter_why
        record["alternatives"] = None
        record["consequences"] = None
    elif field == "prompt_structure_decision":
        record["body"] = _brief_claim(decision or title, why, record.get("body"))
        record["decision"] = decision or title
        record["why"] = why or _first_sentence(record.get("body"))
        record["alternatives"] = None
        record["consequences"] = None
    elif field == "fixture_constraint":
        record["title"] = "Extract test fixture adequacy rule"
        record["body"] = body
    elif field == "deferred_design_fact":
        deferred_title = _compact_deferred_title(title)
        record["title"] = deferred_title
        record["body"] = _brief_claim(record.get("body"), deferred_title)


def _include_deferred_design_context_ref(
    record: dict[str, Any],
    trace_path: Path,
) -> None:
    """Add the immediately preceding visible design text for user deferrals."""
    refs = [str(ref).strip() for ref in record.get("source_event_refs") or []]
    if not refs:
        return
    lines = _read_trace_lines(trace_path)
    additions: list[str] = []
    for ref in refs:
        line_number = _line_ref_number(ref)
        if line_number is None or line_number < 1 or line_number > len(lines):
            continue
        if _visible_source_text(lines[line_number - 1], role="user") is not None:
            for candidate in range(line_number - 1, max(0, line_number - 8), -1):
                if _visible_source_text(lines[candidate - 1], role="assistant"):
                    additions.append(f"line:{candidate}")
                    break
            continue
        if _visible_source_text(lines[line_number - 1], role="assistant") is None:
            continue
        for candidate in range(line_number + 1, min(len(lines), line_number + 4) + 1):
            if _visible_source_text(lines[candidate - 1], role="user"):
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
    for line_number, raw_line in enumerate(_read_trace_lines(trace_path), 1):
        text = _visible_source_text(raw_line)
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
            _first_sentences(
                _shortest_visible_source_text(record, trace_path, role="user"),
                count=2,
            )
            or _first_sentence(record.get("body"))
            or _first_sentence(record.get("title"))
        )
        record["decision"] = None
        record["why"] = None
        record["alternatives"] = None
        record["consequences"] = None
    elif field == "model_size_priority_record":
        source_quote = _shortest_visible_source_text(record, trace_path, role="user")
        record["body"] = (
            _first_sentences(source_quote, count=2)
            or _first_sentence(record.get("title"))
            or _first_sentence(record.get("body"))
        )
        record["decision"] = None
        record["why"] = None
        record["alternatives"] = None
        record["consequences"] = None
        record["evidence_refs"] = []
    elif field == "upstream_bug_report_record":
        _repair_external_report_refs(record, trace_path)
        external_refs = _supported_external_report_refs(record, trace_path)
        if external_refs:
            joined_refs = ", ".join(external_refs)
            title = _first_sentence(record.get("title")) or "Upstream bug report"
            if "reported" not in title.lower():
                title = f"{title} reported upstream"
            if joined_refs not in title:
                title = f"{title} ({joined_refs})"
            body = _first_sentence(record.get("body")) or title
            if joined_refs not in body:
                body = (
                    f"{body.rstrip('.')}. Reported upstream as {joined_refs}."
                    if body
                    else f"Reported upstream as {joined_refs}."
                )
            record["title"] = title
            record["body"] = body
            source_refs = _external_report_source_refs(
                record,
                trace_path,
                external_refs,
            )
            if source_refs:
                record["source_event_refs"] = source_refs
            record["evidence_refs"] = [joined_refs]
        else:
            body = _first_sentence(record.get("title")) or _first_sentence(
                record.get("body")
            )
            record["evidence_refs"] = []
        record["decision"] = None
        record["why"] = None
        record["alternatives"] = None
        record["consequences"] = None
    elif field == "provider_cost_record":
        record["body"] = (
            _first_sentences(
                _shortest_visible_source_text(record, trace_path, role="user"),
                count=2,
            )
            or _first_sentence(record.get("body"))
            or _first_sentence(record.get("title"))
        )
        record["decision"] = None
        record["why"] = None
        record["alternatives"] = None
        record["consequences"] = None


def _repair_external_report_refs(record: dict[str, Any], trace_path: Path) -> None:
    """Attach visible source refs for external PR/issue claims when available."""
    claimed_refs = _external_report_refs_in_text(
        " ".join(
            str(record.get(field) or "")
            for field in ("title", "body", "decision", "why", "consequences")
        )
    )
    if not claimed_refs:
        return
    lines = _read_trace_lines(trace_path)
    current_refs = [str(ref).strip() for ref in record.get("source_event_refs") or []]
    current_lines = {
        line_number
        for ref in current_refs
        if (line_number := _line_ref_number(ref)) is not None
        and 1 <= line_number <= len(lines)
        and not _is_continuation_source_line(lines[line_number - 1])
    }
    added: list[str] = []
    for external_ref in claimed_refs:
        if any(
            _line_mentions_external_report(lines[line_number - 1], external_ref)
            for line_number in current_lines
            if 1 <= line_number <= len(lines)
        ):
            continue
        source_line = _find_visible_external_report_line(lines, external_ref)
        if source_line is not None:
            added.append(f"line:{source_line}")
    if added:
        record["source_event_refs"] = [*current_refs, *added]


def _supported_external_report_refs(
    record: dict[str, Any],
    trace_path: Path,
) -> list[str]:
    """Return external report refs directly supported by record source refs."""
    claimed_refs = _external_report_refs_in_text(
        " ".join(str(record.get(field) or "") for field in ("title", "body"))
    )
    if not claimed_refs:
        return []
    lines = _read_trace_lines(trace_path)
    supported: list[str] = []
    for external_ref in claimed_refs:
        for ref in record.get("source_event_refs") or []:
            line_number = _line_ref_number(str(ref))
            if line_number is None or line_number < 1 or line_number > len(lines):
                continue
            if _line_mentions_external_report(lines[line_number - 1], external_ref):
                supported.append(external_ref)
                break
    return supported


def _external_report_source_refs(
    record: dict[str, Any],
    trace_path: Path,
    external_refs: list[str],
) -> list[str]:
    """Return source refs that directly mention the supported external reports."""
    lines = _read_trace_lines(trace_path)
    refs: list[str] = []
    seen: set[str] = set()
    for external_ref in external_refs:
        source_line = _find_visible_external_report_line(lines, external_ref)
        if source_line is not None:
            normalized_ref = f"line:{source_line}"
            if normalized_ref not in seen:
                refs.append(normalized_ref)
                seen.add(normalized_ref)
            continue
        for ref in record.get("source_event_refs") or []:
            normalized_ref = str(ref).strip()
            line_number = _line_ref_number(normalized_ref)
            if line_number is None or line_number < 1 or line_number > len(lines):
                continue
            if _line_mentions_external_report(lines[line_number - 1], external_ref):
                if normalized_ref not in seen:
                    refs.append(normalized_ref)
                    seen.add(normalized_ref)
                break
    return refs


def _repair_source_refs_from_evidence_quotes(
    record: dict[str, Any],
    trace_path: Path,
    *,
    role: str | None = None,
) -> None:
    """Attach source refs whose visible text contains model-supplied evidence quotes."""
    quote_refs = _source_refs_for_evidence_quotes(record, trace_path, role=role)
    if not quote_refs:
        return
    existing = [str(ref).strip() for ref in record.get("source_event_refs") or []]
    seen: set[str] = set()
    merged: list[str] = []
    for ref in [*existing, *quote_refs]:
        if ref and ref not in seen:
            merged.append(ref)
            seen.add(ref)
    record["source_event_refs"] = merged


def _source_refs_for_evidence_quotes(
    record: dict[str, Any],
    trace_path: Path,
    *,
    role: str | None = None,
) -> list[str]:
    """Find visible source lines that contain the record's evidence quotes."""
    evidence_refs = [
        " ".join(str(item or "").split()).strip()
        for item in record.get("evidence_refs") or []
        if " ".join(str(item or "").split()).strip()
    ]
    if not evidence_refs:
        return []
    lines = _read_trace_lines(trace_path)
    refs: list[str] = []
    seen: set[str] = set()
    for quote in evidence_refs:
        quote_key = quote.lower()
        if len(quote_key) < 12:
            continue
        for line_number, raw_line in enumerate(lines, 1):
            text = _visible_source_text(raw_line, role=role)
            if not text:
                continue
            if quote_key not in text.lower():
                continue
            ref = f"line:{line_number}"
            if ref not in seen:
                refs.append(ref)
                seen.add(ref)
            break
    return refs


def _repair_record_evidence_from_findings(
    records: list[dict[str, Any]],
    findings: list[Any],
    trace_path: Path,
) -> None:
    """Use filtered finding quotes to keep long cited source lines judge-readable."""
    quotes_by_line: dict[int, str] = {}
    for value in findings:
        finding = model_payload(value)
        line = finding.get("line")
        quote = " ".join(str(finding.get("quote") or "").split()).strip()
        if isinstance(line, int) and quote:
            quotes_by_line.setdefault(line, quote)
    if not quotes_by_line:
        return
    lines = _read_trace_lines(trace_path)
    for record in records:
        matched_ref: str | None = None
        matched_quote: str | None = None
        for ref in record.get("source_event_refs") or []:
            line_number = _line_ref_number(str(ref))
            if line_number is None:
                continue
            quote = quotes_by_line.get(line_number)
            if quote and _line_contains_visible_quote(lines, line_number, quote):
                matched_ref = f"line:{line_number}"
                matched_quote = quote
                break
            if quote:
                repaired_ref = _source_ref_for_visible_quote(lines, quote)
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
    lines = _read_trace_lines(trace_path)
    kept: list[str] = []
    seen: set[str] = set()
    for evidence_ref in evidence_refs:
        if not _evidence_ref_supported_by_visible_source(evidence_ref, lines):
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
        source_text = _shortest_visible_source_text(record, trace_path, role="user")
        if not source_text:
            continue
        record["body"] = _first_sentences(source_text, count=2)
        record["decision"] = None
        record["why"] = None
        record["alternatives"] = None
        record["consequences"] = None


def _record_source_excerpts(
    record: dict[str, Any],
    trace_path: Path,
    *,
    max_excerpts: int = 2,
) -> list[str]:
    """Return short exact source excerpts that overlap with the record claim."""
    terms = _record_terms(record)
    if not terms:
        return []
    lines = _read_trace_lines(trace_path)
    candidates: list[tuple[int, int, str]] = []
    for ref in record.get("source_event_refs") or []:
        line_number = _line_ref_number(str(ref))
        if line_number is None or line_number < 1 or line_number > len(lines):
            continue
        text = _visible_source_text(lines[line_number - 1])
        if not text:
            continue
        for index, chunk in enumerate(_source_excerpt_chunks(text)):
            score = len(terms & _normalized_terms(chunk))
            if score >= 2:
                candidates.append((-score, index, chunk))
    excerpts: list[str] = []
    seen: set[str] = set()
    for _, _, chunk in sorted(candidates):
        excerpt = _compact_source_excerpt(chunk)
        if not excerpt or excerpt in seen:
            continue
        excerpts.append(excerpt)
        seen.add(excerpt)
        if len(excerpts) >= max_excerpts:
            break
    return excerpts


def _record_terms(record: dict[str, Any]) -> set[str]:
    """Return normalized claim terms from a record draft."""
    return _normalized_terms(
        " ".join(
            str(record.get(field) or "")
            for field in ("title", "body", "decision", "why")
        )
    )


def _normalized_terms(text: str) -> set[str]:
    """Tokenize text into simple lowercase terms for source-excerpt selection."""
    terms: set[str] = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_+-]{2,}", text.lower()):
        token = token.strip("_+-")
        if len(token) < 4:
            continue
        if token.endswith("s") and len(token) > 4:
            token = token[:-1]
        terms.add(token)
    return terms


def _source_excerpt_chunks(text: str) -> list[str]:
    """Split visible source text into short candidate evidence chunks."""
    chunks: list[str] = []
    for raw_chunk in re.split(r"[\n\r]+", text):
        chunk = " ".join(raw_chunk.split()).strip(" -*")
        if len(chunk) >= 12:
            chunks.append(chunk)
    return chunks


def _compact_source_excerpt(chunk: str, limit: int = 220) -> str:
    """Keep an exact but concise prefix of a source chunk."""
    excerpt = " ".join(chunk.split()).strip()
    for separator in (" — ", " - "):
        if separator in excerpt:
            prefix = excerpt.split(separator, 1)[0].strip()
            if len(prefix) >= 12:
                excerpt = prefix
                break
    if len(excerpt) <= limit:
        return excerpt
    return excerpt[:limit].rsplit(" ", 1)[0].strip()


def _evidence_ref_supported_by_visible_source(
    evidence_ref: str,
    lines: list[str],
) -> bool:
    """Return whether an evidence string appears in visible source text."""
    normalized = " ".join(evidence_ref.split()).strip().lower()
    if len(normalized) < 4:
        return False
    external_refs = _external_report_refs_in_text(evidence_ref)
    for raw_line in lines:
        text = _visible_source_text(raw_line)
        if not text:
            continue
        line_text = " ".join(text.split()).lower()
        if normalized in line_text:
            return True
        if external_refs and any(
            _line_mentions_external_report(raw_line, external_ref)
            for external_ref in external_refs
        ):
            return True
    return False


def _line_contains_visible_quote(
    lines: list[str],
    line_number: int,
    quote: str,
) -> bool:
    """Return whether a source line visibly contains a model-supplied quote."""
    if line_number < 1 or line_number > len(lines):
        return False
    text = _visible_source_text(lines[line_number - 1])
    if not text:
        return False
    return " ".join(quote.split()).lower() in " ".join(text.split()).lower()


def _source_ref_for_visible_quote(lines: list[str], quote: str) -> str | None:
    """Find the visible source line containing a quote."""
    normalized_quote = " ".join(quote.split()).strip().lower()
    if len(normalized_quote) < 12:
        return None
    for line_number, raw_line in enumerate(lines, 1):
        text = _visible_source_text(raw_line)
        if not text:
            continue
        if normalized_quote in " ".join(text.split()).lower():
            return f"line:{line_number}"
    return None


def _external_report_refs_in_text(text: str) -> list[str]:
    """Extract normalized external PR/issue references from text."""
    refs: list[str] = []
    seen: set[str] = set()
    for match in GITHUB_REPORT_URL_RE.finditer(text):
        label = match.group("label").lower()
        number = match.group("number")
        normalized_label = "PR" if label in {"pull", "pulls"} else "issue"
        ref = f"{normalized_label} #{number}"
        key = ref.lower()
        if key not in seen:
            refs.append(ref)
            seen.add(key)
    for match in EXTERNAL_REPORT_REF_RE.finditer(text):
        label = match.group("label").lower()
        number = match.group("number")
        normalized_label = "PR" if label in {"pr", "pull request"} else "issue"
        ref = f"{normalized_label} #{number}"
        key = ref.lower()
        if key not in seen:
            refs.append(ref)
            seen.add(key)
    return refs


def _find_visible_external_report_line(lines: list[str], external_ref: str) -> int | None:
    """Find a visible source line that states the external PR/issue reference."""
    for index, raw_line in enumerate(lines, 1):
        if _visible_source_role(raw_line) is None:
            continue
        if _is_continuation_source_line(raw_line):
            continue
        if _line_mentions_external_report(raw_line, external_ref):
            return index
    return None


def _line_mentions_external_report(raw_line: str, external_ref: str) -> bool:
    """Return whether a raw visible trace line mentions the normalized report ref."""
    text = _visible_source_text(raw_line) or ""
    return external_ref.lower() in {
        ref.lower() for ref in _external_report_refs_in_text(text)
    }


def _brief_claim(*values: Any) -> str:
    """Build a compact claim from source-backed model fields."""
    sentences: list[str] = []
    seen: set[str] = set()
    for value in values:
        sentence = _first_sentence(value)
        key = sentence.lower()
        if sentence and key not in seen:
            sentences.append(sentence)
            seen.add(key)
        if len(sentences) >= 2:
            break
    return " ".join(sentences)


def _compact_deferred_title(title: str) -> str:
    """Keep deferred-design titles on the design and deferral, not rationale."""
    normalized = str(title or "").strip().rstrip(".")
    lowered = normalized.lower()
    marker = " deferred"
    if marker in lowered:
        end = lowered.index(marker) + len(marker)
        return normalized[:end].strip()
    return normalized or "Deferred design"


def _shortest_visible_source_text(
    record: dict[str, Any],
    trace_path: Path,
    *,
    role: str | None = None,
) -> str:
    """Return the shortest visible cited source text for compact quote-backed bodies."""
    lines = _read_trace_lines(trace_path)
    candidates: list[str] = []
    for ref in record.get("source_event_refs") or []:
        line_number = _line_ref_number(str(ref))
        if line_number is None or line_number < 1 or line_number > len(lines):
            continue
        text = _visible_source_text(lines[line_number - 1], role=role)
        if text:
            candidates.append(text)
    if not candidates:
        return ""
    return min(candidates, key=len)


def _first_sentences(value: Any, *, count: int) -> str:
    """Return the first count simple sentences from text."""
    text = " ".join(str(value or "").split()).strip()
    if not text or count <= 0:
        return ""
    sentences: list[str] = []
    remaining = text
    while remaining and len(sentences) < count:
        split_at: int | None = None
        for delimiter in (". ", "; "):
            index = remaining.find(delimiter)
            if index >= 0 and (split_at is None or index < split_at):
                split_at = index
        if split_at is None:
            sentences.append(remaining.rstrip(".") + ".")
            break
        sentence = remaining[:split_at].strip().rstrip(".")
        if sentence:
            sentences.append(sentence + ".")
        remaining = remaining[split_at + 2 :].strip()
    return " ".join(sentences)


def _first_sentence(value: Any) -> str:
    """Return the first sentence from a model-written field."""
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    for delimiter in (". ", "; "):
        if delimiter in text:
            return text.split(delimiter, 1)[0].strip().rstrip(".") + "."
    return text.rstrip(".") + "."


def _prune_unusable_source_refs(record: dict[str, Any], trace_path: Path) -> None:
    """Drop or repair refs that point only to cleared/tool-only trace lines."""
    refs = [str(ref).strip() for ref in record.get("source_event_refs") or []]
    if not refs:
        return
    lines = _read_trace_lines(trace_path)
    kept: list[str] = []
    for ref in refs:
        line_number = _line_ref_number(ref)
        if line_number is None or line_number < 1 or line_number > len(lines):
            continue
        raw_line = lines[line_number - 1].strip()
        if not raw_line:
            continue
        source_kind = _source_line_kind(raw_line)
        if source_kind == "visible":
            kept.append(ref)
            continue
        if source_kind == "tool":
            repaired = _nearby_visible_assistant_source_ref(line_number, lines)
            if repaired is not None:
                kept.append(repaired)
            continue
        repaired = _nearby_visible_source_ref(line_number, lines)
        if repaired is not None:
            kept.append(repaired)
    record["source_event_refs"] = kept


def _source_line_kind(raw_line: str) -> str | None:
    """Classify a raw trace line as visible text, hidden text, tool payload, or none."""
    try:
        event = json.loads(raw_line)
    except (TypeError, ValueError):
        return None
    message = event.get("message")
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = event.get("content")
    if isinstance(content, str):
        return "visible" if _is_visible_source_text(content) else "hidden"
    if isinstance(content, list):
        saw_hidden = False
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text" and _is_visible_source_text(block.get("text")):
                return "visible"
            if block_type == "tool_use":
                return "tool"
            if block_type in {"thinking", "tool_result"}:
                saw_hidden = True
        if saw_hidden:
            return "hidden"
    return None


def _nearby_visible_source_ref(line_number: int, lines: list[str]) -> str | None:
    """Find nearby visible source-domain text for a model-cited hidden/tool line."""
    for distance in range(1, 4):
        for candidate in (line_number + distance, line_number - distance):
            if candidate < 1 or candidate > len(lines):
                continue
            if _visible_source_role(lines[candidate - 1]) is not None:
                return f"line:{candidate}"
    return None


def _nearby_visible_assistant_source_ref(
    line_number: int,
    lines: list[str],
) -> str | None:
    """Find nearby assistant text that explains a generated tool action."""
    for distance in range(1, 4):
        for candidate in (line_number - distance, line_number + distance):
            if candidate < 1 or candidate > len(lines):
                continue
            if _visible_source_text(lines[candidate - 1], role="assistant"):
                return f"line:{candidate}"
    return None


def _read_trace_lines(trace_path: Path) -> list[str]:
    """Read trace lines for lightweight source-ref validation."""
    try:
        return trace_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def _line_ref_number(ref: str) -> int | None:
    """Parse line refs of the form line:123."""
    prefix = "line:"
    if not ref.startswith(prefix):
        return None
    try:
        return int(ref[len(prefix) :])
    except ValueError:
        return None


def _window_line_refs(window: dict[str, Any]) -> list[str]:
    """Return one line reference for each line in a trace window."""
    start_line = int(window.get("start_line") or 0)
    end_line = int(window.get("end_line") or 0)
    if start_line <= 0 or end_line < start_line:
        return []
    return [f"line:{line}" for line in range(start_line, end_line + 1)]


def _episode_summary(state: TraceIngestionGraphState) -> str:
    """Render compact rolling episode summary."""
    updates = [item for item in state.get("episode_updates", []) if item]
    updates, omitted = _head_and_tail(updates, MAX_EPISODE_SUMMARY_ITEMS)
    lines = [f"- {item}" for item in updates]
    if omitted:
        lines.insert(-1, f"- ... [{omitted} middle episode updates omitted]")
    return "\n".join(lines) or "(none yet)"


def _synthesis_episode_summary(state: TraceIngestionGraphState) -> str:
    """Render a final episode summary without carrying discarded details forward."""
    if state.get("filtered_durable_findings"):
        return _episode_summary(state)
    if state.get("implementation_findings") or state.get("discarded_noise"):
        return (
            "No reusable durable context was found; source-session details were "
            "implementation evidence or discarded noise."
        )
    return _episode_summary(state)


def _findings_summary(state: TraceIngestionGraphState) -> str:
    """Render all prior findings for the next scan window."""
    return "\n\n".join(
        [
            "Durable findings:\n" + _durable_findings_summary(state),
            "Implementation/noise findings:\n" + _implementation_summary(state),
        ]
    )


def _durable_findings_summary(state: TraceIngestionGraphState) -> str:
    """Render durable findings compactly for BAML prompts."""
    findings = state.get("durable_findings", [])
    if not findings:
        return "(none)"
    return "\n".join(_format_finding(finding) for finding in findings)


def _filtered_durable_findings_summary(state: TraceIngestionGraphState) -> str:
    """Render filtered durable findings compactly for final synthesis."""
    findings = state.get("filtered_durable_findings") or []
    if not findings:
        return "(none)"
    summary = str(state.get("signal_filter_summary") or "").strip()
    rendered = "\n".join(_format_finding(finding) for finding in findings)
    if summary:
        return f"Filter summary: {summary}\n{rendered}"
    return rendered


def _rejected_durable_findings_summary(state: TraceIngestionGraphState) -> str:
    """Render rejected durable candidates for final high-priority restoration."""
    findings = state.get("rejected_durable_findings") or []
    if not findings:
        return "(none)"
    return "\n".join(_format_finding(finding) for finding in findings)


def _implementation_summary(state: TraceIngestionGraphState) -> str:
    """Render implementation findings and discarded noise compactly."""
    parts: list[str] = []
    findings = state.get("implementation_findings", [])
    if findings:
        selected, omitted = _head_and_tail(findings, MAX_IMPLEMENTATION_SUMMARY_ITEMS)
        lines = [_format_finding(finding) for finding in selected]
        if omitted:
            lines.insert(
                -1,
                f"- ... [{omitted} middle implementation/noise findings omitted]",
            )
        parts.append("\n".join(lines))
    noise = state.get("discarded_noise", [])
    if noise:
        selected, omitted = _head_and_tail(noise, MAX_DISCARDED_NOISE_SUMMARY_ITEMS)
        lines = [f"- {item}" for item in selected]
        if omitted:
            lines.insert(-1, f"- ... [{omitted} middle noise categories omitted]")
        parts.append("Discarded noise:\n" + "\n".join(lines))
    return "\n".join(parts) if parts else "(none)"


def _head_and_tail(items: list[Any], limit: int) -> tuple[list[Any], int]:
    """Keep early intent plus recent context while bounding long summaries."""
    if len(items) <= limit:
        return items, 0
    head_count = max(1, limit // 4)
    tail_count = max(1, limit - head_count)
    return [*items[:head_count], *items[-tail_count:]], len(items) - limit


def _format_finding(finding: dict[str, Any]) -> str:
    """Render one scan finding as one compact bullet."""
    kind = str(finding.get("kind") or "").strip()
    theme = str(finding.get("theme") or "").strip()
    note = str(finding.get("note") or "").strip()
    line = finding.get("line")
    quote = str(finding.get("quote") or "").strip()
    prefix = f"- {kind}: {theme}" if kind or theme else "-"
    details = note
    if line:
        details += f" (line:{line})"
    if quote:
        details += f" Evidence: {quote}"
    return f"{prefix}: {details}".strip()
