"""Windowed LangGraph trace-ingestion pipeline backed by BAML."""

from __future__ import annotations

import json
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

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

MAX_BAML_MODEL_RETRIES = 3
BAML_RECOVERABLE_ERROR_NAMES = {
    "BamlClientFinishReasonError",
    "BamlClientHttpError",
    "BamlTimeoutError",
    "BamlValidationError",
}


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
                        "source_profile": persistence_context.source_profile,
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
        result, retry_observations, attempts = _call_baml_with_retries(
            lambda: baml_runtime.ObserveSourceWindow(
                run_instruction=run_instruction,
                prior_episode_summary=_episode_summary(state),
                prior_findings_summary=_findings_summary(state),
                source_window=str(window["text"]),
            ),
            stage="scan_window",
            progress=progress,
        )
        payload = _model_payload(result)
        episode_update = str(payload.get("episode_update") or "").strip()
        durable = [_model_payload(item) for item in payload.get("durable_findings") or []]
        implementation = [
            _model_payload(item)
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
        result, retry_observations, attempts = _call_baml_with_retries(
            lambda: baml_runtime.SynthesizeContextRecords(
                run_instruction=run_instruction,
                episode_summary=_episode_summary(state),
                durable_findings_summary=_filtered_durable_findings_summary(state),
                existing_record_manifest=existing_record_manifest or "(none)",
            ),
            stage="synthesize_records",
            progress=progress,
        )
        payload = _model_payload(result)
        durable_count = len(payload.get("durable_records") or [])
        update_count = len(payload.get("record_updates") or [])
        return {
            "llm_calls": llm_calls + attempts,
            "synthesized": result,
            "observations": [
                *retry_observations,
                {
                    "action": "synthesize_records",
                    "ok": True,
                    "content": (
                        f"durable_records={durable_count} "
                        f"record_updates={update_count}"
                    ),
                    "args": {},
                    "done": False,
                    "completion_summary": "",
                },
            ],
        }

    def review_records(state: TraceIngestionGraphState) -> dict[str, Any]:
        """Review synthesized records before persistence."""
        llm_calls = int(state.get("llm_calls") or 0)
        if llm_calls >= max_llm_calls:
            raise RuntimeError(
                f"BAML trace ingestion exceeded max_llm_calls={max_llm_calls}."
            )
        if progress:
            print(f"  trace-ingestion review {llm_calls + 1}/{max_llm_calls}", flush=True)
        synthesized_payload = _model_payload(state.get("synthesized"))
        result, retry_observations, attempts = _call_baml_with_retries(
            lambda: baml_runtime.ReviewSynthesizedContextRecords(
                run_instruction=run_instruction,
                durable_findings_summary=_filtered_durable_findings_summary(state),
                existing_record_manifest=existing_record_manifest or "(none)",
                synthesized_records_json=json.dumps(
                    synthesized_payload,
                    ensure_ascii=True,
                    indent=2,
                ),
            ),
            stage="review_records",
            progress=progress,
        )
        payload = _model_payload(result)
        payload, cap_observation = _cap_durable_changes_to_filtered_signal(
            payload,
            kept_count=len(state.get("filtered_durable_findings") or []),
        )
        durable_count = len(payload.get("durable_records") or [])
        update_count = len(payload.get("record_updates") or [])
        observations = [*retry_observations]
        if cap_observation:
            observations.append(cap_observation)
        return {
            "llm_calls": llm_calls + attempts,
            "synthesized": payload,
            "observations": [
                *observations,
                {
                    "action": "review_records",
                    "ok": True,
                    "content": (
                        f"durable_records={durable_count} "
                        f"record_updates={update_count}"
                    ),
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
        result, retry_observations, attempts = _call_baml_with_retries(
            lambda: baml_runtime.FilterDurableSignal(
                run_instruction=run_instruction,
                episode_summary=_episode_summary(state),
                durable_findings_summary=_durable_findings_summary(state),
                implementation_summary=_implementation_summary(state),
                existing_record_manifest=existing_record_manifest or "(none)",
            ),
            stage="filter_signals",
            progress=progress,
        )
        payload = _model_payload(result)
        kept = [
            _model_payload(item)
            for item in payload.get("kept_durable_findings") or []
        ]
        rejected = [
            _model_payload(item)
            for item in payload.get("rejected_findings") or []
        ]
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
    graph.add_node("review_records", review_records)
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
    graph.add_edge("synthesize_records", "review_records")
    graph.add_edge("review_records", "persist_records")
    graph.add_edge("persist_records", END)
    return graph.compile()


def _call_baml_with_retries(
    call: Callable[[], Any],
    *,
    stage: str,
    progress: bool,
) -> tuple[Any, list[dict[str, Any]], int]:
    """Run one BAML call with graph-visible recoverable retries."""
    observations: list[dict[str, Any]] = []
    attempts = 0
    while True:
        attempts += 1
        try:
            return call(), observations, attempts
        except Exception as exc:
            if not _is_recoverable_baml_error(exc) or attempts > MAX_BAML_MODEL_RETRIES:
                raise
            if progress:
                print(f"  trace-ingestion retry {stage} attempt={attempts}", flush=True)
            observations.append(
                {
                    "action": "model_retry",
                    "ok": False,
                    "content": _model_retry_observation(exc),
                    "args": {"stage": stage, "attempt": attempts},
                    "done": False,
                    "completion_summary": "",
                }
            )


def _model_payload(value: Any) -> dict[str, Any]:
    """Convert generated BAML objects into plain dictionaries."""
    if hasattr(value, "model_dump"):
        return _plain_value(value.model_dump(exclude_none=True))
    if isinstance(value, dict):
        return _plain_value(
            {key: item for key, item in value.items() if item is not None}
        )
    if value is None:
        return {}
    return _plain_value(getattr(value, "__dict__", {}))


def _plain_value(value: Any) -> Any:
    """Convert enum-ish values recursively into JSON-like values."""
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return enum_value
    if isinstance(value, dict):
        return {key: _plain_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain_value(item) for item in value]
    return value


def _episode_summary(state: TraceIngestionGraphState) -> str:
    """Render compact rolling episode summary."""
    updates = [item for item in state.get("episode_updates", []) if item]
    return "\n".join(f"- {item}" for item in updates) or "(none yet)"


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


def _cap_durable_changes_to_filtered_signal(
    payload: dict[str, Any],
    *,
    kept_count: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Keep durable writes no broader than the filtered durable signal."""
    if kept_count <= 0:
        return payload, None
    durable_records = list(payload.get("durable_records") or [])
    record_updates = list(payload.get("record_updates") or [])
    total_changes = len(durable_records) + len(record_updates)
    if total_changes <= kept_count:
        return payload, None

    remaining = kept_count
    capped_updates = record_updates[:remaining]
    remaining -= len(capped_updates)
    capped_records = durable_records[:remaining]
    capped = dict(payload)
    capped["record_updates"] = capped_updates
    capped["durable_records"] = capped_records
    return capped, {
        "action": "cap_durable_changes",
        "ok": True,
        "content": (
            f"kept_signal={kept_count} durable_changes={total_changes} "
            f"capped_to={len(capped_updates) + len(capped_records)}"
        ),
        "args": {
            "kept_signal_count": kept_count,
            "original_durable_records": len(durable_records),
            "original_record_updates": len(record_updates),
        },
        "done": False,
        "completion_summary": "",
    }


def _implementation_summary(state: TraceIngestionGraphState) -> str:
    """Render implementation findings and discarded noise compactly."""
    parts: list[str] = []
    findings = state.get("implementation_findings", [])
    if findings:
        parts.append("\n".join(_format_finding(finding) for finding in findings))
    noise = state.get("discarded_noise", [])
    if noise:
        parts.append("Discarded noise:\n" + "\n".join(f"- {item}" for item in noise))
    return "\n".join(parts) if parts else "(none)"


def _format_finding(finding: dict[str, Any]) -> str:
    """Render one scan finding as one compact bullet."""
    level = str(finding.get("level") or "").strip()
    theme = str(finding.get("theme") or "").strip()
    note = str(finding.get("note") or "").strip()
    line = finding.get("line")
    quote = str(finding.get("quote") or "").strip()
    prefix = f"- {level}: {theme}" if level or theme else "-"
    details = note
    if line:
        details += f" (line {line})"
    if quote:
        details += f" Evidence: {quote}"
    return f"{prefix}: {details}".strip()


def _is_recoverable_baml_error(exc: Exception) -> bool:
    """Return whether a BAML model/parsing failure should be retried."""
    return type(exc).__name__ in BAML_RECOVERABLE_ERROR_NAMES


def _model_retry_observation(exc: Exception) -> str:
    """Render a compact model failure note."""
    message = str(exc).replace("\n", " ")[:1200]
    return (
        "The previous BAML model call did not produce a valid next action. "
        "Retry and return exactly one JSON object matching the requested schema. "
        "Do not include <think> tags, hidden reasoning, markdown, or prose before "
        f"the JSON. Error: {type(exc).__name__}: {message}"
    )
