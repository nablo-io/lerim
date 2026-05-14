"""Windowed LangGraph extraction pipeline backed by BAML."""

from __future__ import annotations

from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from lerim.agents.baml_runtime import build_baml_client_for_role
from lerim.agents.extract.persistence import (
    PersistenceContext,
    persist_synthesized_extraction,
)
from lerim.agents.extract.state import WindowExtractGraphState
from lerim.agents.extract.windowing import (
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


def run_windowed_extract_graph(
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
    """Run the BAML extraction graph and return its final state."""
    total_lines = trace_line_count(persistence_context.trace_path)
    graph = build_windowed_extract_graph(
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


def build_windowed_extract_graph(
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
    """Compile the windowed scan, synthesize, and persist extraction graph."""
    baml_runtime = build_baml_client_for_role(
        config=config,
        provider=provider,
        model_name=model_name,
        api_base_url=api_base_url,
        api_key=api_key,
        temperature=temperature,
    )

    def read_window(state: WindowExtractGraphState) -> dict[str, Any]:
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
                f"  extract window {window['start_line']}-{window['end_line']} "
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

    def scan_window(state: WindowExtractGraphState) -> dict[str, Any]:
        """Scan the current window into compact episode and findings state."""
        llm_calls = int(state.get("llm_calls") or 0)
        if llm_calls >= max_llm_calls:
            raise RuntimeError(
                f"BAML extraction exceeded max_llm_calls={max_llm_calls}."
            )
        window = state.get("current_window") or {}
        if not window.get("text"):
            return {}
        if progress:
            print(f"  extract scan {llm_calls + 1}/{max_llm_calls}", flush=True)
        result, retry_observations, attempts = _call_baml_with_retries(
            lambda: baml_runtime.ScanTraceWindow(
                run_instruction=run_instruction,
                prior_episode_summary=_episode_summary(state),
                prior_findings_summary=_findings_summary(state),
                trace_window=str(window["text"]),
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

    def synthesize_records(state: WindowExtractGraphState) -> dict[str, Any]:
        """Synthesize final episode and durable record candidates."""
        llm_calls = int(state.get("llm_calls") or 0)
        if llm_calls >= max_llm_calls:
            raise RuntimeError(
                f"BAML extraction exceeded max_llm_calls={max_llm_calls}."
            )
        if progress:
            print(f"  extract synth {llm_calls + 1}/{max_llm_calls}", flush=True)
        result, retry_observations, attempts = _call_baml_with_retries(
            lambda: baml_runtime.SynthesizeExtractRecords(
                run_instruction=run_instruction,
                episode_summary=_episode_summary(state),
                durable_findings_summary=_durable_findings_summary(state),
                existing_record_manifest=existing_record_manifest or "(none)",
            ),
            stage="synthesize_records",
            progress=progress,
        )
        payload = _model_payload(result)
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

    def persist_records(state: WindowExtractGraphState) -> dict[str, Any]:
        """Persist synthesized records and finish the graph."""
        observations, done, completion_summary = persist_synthesized_extraction(
            state.get("synthesized"),
            persistence_context,
        )
        if progress:
            print(f"  extract persist done={done}", flush=True)
        return {
            "observations": observations,
            "done": done,
            "completion_summary": completion_summary,
        }

    def after_scan(state: WindowExtractGraphState) -> str:
        """Continue scanning until all trace lines are covered."""
        next_line = int(state.get("next_line") or 1)
        total_lines = int(state.get("trace_total_lines") or 0)
        if next_line <= total_lines:
            return "read_window"
        return "synthesize_records"

    graph = StateGraph(WindowExtractGraphState)
    graph.add_node("read_window", read_window)
    graph.add_node("scan_window", scan_window)
    graph.add_node("synthesize_records", synthesize_records)
    graph.add_node("persist_records", persist_records)
    graph.add_edge(START, "read_window")
    graph.add_edge("read_window", "scan_window")
    graph.add_conditional_edges(
        "scan_window",
        after_scan,
        ["read_window", "synthesize_records"],
    )
    graph.add_edge("synthesize_records", "persist_records")
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
                print(f"  extract retry {stage} attempt={attempts}", flush=True)
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


def _episode_summary(state: WindowExtractGraphState) -> str:
    """Render compact rolling episode summary."""
    updates = [item for item in state.get("episode_updates", []) if item]
    return "\n".join(f"- {item}" for item in updates) or "(none yet)"


def _findings_summary(state: WindowExtractGraphState) -> str:
    """Render all prior findings for the next scan window."""
    return "\n\n".join(
        [
            "Durable findings:\n" + _durable_findings_summary(state),
            "Implementation/noise findings:\n" + _implementation_summary(state),
        ]
    )


def _durable_findings_summary(state: WindowExtractGraphState) -> str:
    """Render durable findings compactly for BAML prompts."""
    findings = state.get("durable_findings", [])
    if not findings:
        return "(none)"
    return "\n".join(_format_finding(finding) for finding in findings)


def _implementation_summary(state: WindowExtractGraphState) -> str:
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
