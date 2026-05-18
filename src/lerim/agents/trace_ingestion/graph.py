"""Windowed LangGraph trace-ingestion pipeline backed by BAML."""

from __future__ import annotations

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
    graph.add_edge("synthesize_records", "persist_records")
    graph.add_edge("persist_records", END)
    return graph.compile()


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
    return "\n".join(f"- {item}" for item in updates) or "(none yet)"


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
