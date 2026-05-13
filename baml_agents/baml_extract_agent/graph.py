"""Windowed LangGraph extraction pipeline whose LLM steps are produced by BAML."""

from __future__ import annotations

from datetime import datetime, timezone
import math
import operator
from pathlib import Path
from typing import Annotated, Any
from typing_extensions import TypedDict

from baml_py import ClientRegistry
from langgraph.graph import END, START, StateGraph

from baml_client.sync_client import b
from lerim.agents.extract import _format_existing_record_manifest
from lerim.config.settings import get_config
from lerim.agents.tools import (
    CONTEXT_SOFT_PRESSURE_PCT,
    _TOKENS_PER_CHAR,
    MODEL_CONTEXT_TOKEN_LIMIT,
    ContextDeps,
    TRACE_MAX_CHUNK_BYTES,
    TRACE_MAX_LINE_BYTES,
    compute_request_budget,
)
from lerim.context import ProjectIdentity, resolve_project_identity

from baml_extract_agent.tool_bridge import (
    build_tool_context,
    persist_synthesized_extraction,
    prepare_context_deps,
)


MODEL_NAME = "MiniMax-M2.7"
BAML_PROVIDER = "minimax"
OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
MINIMAX_BASE_URL = "https://api.minimax.io/v1"
MINIMAX_TEMPERATURE_FLOOR = 0.01
MAX_BAML_MODEL_RETRIES = 3
BAML_HTTP_CONNECT_TIMEOUT_MS = 10_000
BAML_HTTP_TIME_TO_FIRST_TOKEN_TIMEOUT_MS = 120_000
BAML_HTTP_IDLE_TIMEOUT_MS = 30_000
BAML_HTTP_REQUEST_TIMEOUT_MS = 300_000
WINDOW_RESERVE_TOKENS = 30_000
MIN_WINDOW_CHARS = 20_000
MAX_WINDOW_CHARS = TRACE_MAX_CHUNK_BYTES
BAML_RECOVERABLE_ERROR_NAMES = {
    "BamlClientFinishReasonError",
    "BamlClientHttpError",
    "BamlTimeoutError",
    "BamlValidationError",
}


class WindowExtractGraphState(TypedDict, total=False):
    """State for the windowed BAML extraction pipeline."""

    observations: Annotated[list[dict[str, Any]], operator.add]
    llm_calls: int
    next_line: int
    trace_total_lines: int
    current_window: dict[str, Any]
    episode_updates: Annotated[list[str], operator.add]
    durable_findings: Annotated[list[dict[str, Any]], operator.add]
    implementation_findings: Annotated[list[dict[str, Any]], operator.add]
    discarded_noise: Annotated[list[str], operator.add]
    synthesized: Any
    done: bool
    completion_summary: str


def run_baml_extraction(
    *,
    trace_path: Path,
    context_db_path: Path,
    project_root: Path | None = None,
    session_id: str = "baml-extract-session",
    session_started_at: str | None = None,
    model_name: str = MODEL_NAME,
    baml_provider: str = BAML_PROVIDER,
    api_base_url: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.0,
    ollama_base_url: str = OLLAMA_BASE_URL,
    max_llm_calls: int | None = None,
    progress: bool = False,
) -> dict[str, Any]:
    """Run the minimal BAML plus LangGraph extraction experiment."""
    resolved_trace_path = trace_path.expanduser().resolve()
    resolved_context_db_path = context_db_path.expanduser().resolve()
    identity = resolve_project_identity(project_root.expanduser().resolve() if project_root else Path.cwd())
    started_at = session_started_at or datetime.now(timezone.utc).isoformat()
    deps = prepare_context_deps(
        context_db_path=resolved_context_db_path,
        project_identity=identity,
        session_id=session_id,
        trace_path=resolved_trace_path,
        session_started_at=started_at,
        model_name=model_name,
    )
    existing_record_manifest = _format_existing_record_manifest(
        context_db_path=resolved_context_db_path,
        project_identity=identity,
    )
    run_instruction = _build_run_instruction(
        context_db_path=resolved_context_db_path,
        project_identity=identity,
        trace_path=resolved_trace_path,
        session_started_at=started_at,
        existing_record_manifest=existing_record_manifest,
    )
    graph = build_windowed_extract_graph(
        deps=deps,
        trace_path=resolved_trace_path,
        run_instruction=run_instruction,
        existing_record_manifest=existing_record_manifest,
        model_name=model_name,
        baml_provider=baml_provider,
        api_base_url=api_base_url,
        api_key=api_key,
        temperature=temperature,
        ollama_base_url=ollama_base_url,
        max_llm_calls=max_llm_calls or compute_request_budget(resolved_trace_path),
        progress=progress,
    )
    final_state = graph.invoke(
        {
            "observations": [],
            "llm_calls": 0,
            "next_line": 1,
            "trace_total_lines": _trace_line_count(resolved_trace_path),
            "done": False,
            "completion_summary": "",
        }
    )
    return {
        "completion_summary": final_state.get("completion_summary", ""),
        "llm_calls": final_state.get("llm_calls", 0),
        "observations": final_state.get("observations", []),
        "done": bool(final_state.get("done")),
        "context_db_path": str(resolved_context_db_path),
        "project_id": identity.project_id,
        "session_id": session_id,
        "model_name": model_name,
        "baml_provider": baml_provider,
    }


def build_windowed_extract_graph(
    *,
    deps: ContextDeps,
    trace_path: Path,
    run_instruction: str,
    existing_record_manifest: str,
    model_name: str,
    baml_provider: str,
    api_base_url: str | None,
    api_key: str | None,
    temperature: float,
    ollama_base_url: str,
    max_llm_calls: int,
    progress: bool = False,
):
    """Compile the windowed scan -> synthesize -> persist extraction graph."""
    runtime_context = build_tool_context(deps)
    baml_runtime = _baml_client_for_model(
        model_name=model_name,
        baml_provider=baml_provider,
        api_base_url=api_base_url,
        api_key=api_key,
        temperature=temperature,
        ollama_base_url=ollama_base_url,
    )

    def read_window(state: WindowExtractGraphState) -> dict[str, Any]:
        """Read the next budgeted trace window into transient state."""
        total_lines = int(state.get("trace_total_lines") or 0)
        start_line = int(state.get("next_line") or 1)
        if start_line > total_lines:
            return {"current_window": {}}
        char_budget = _window_char_budget(
            state=state,
            run_instruction=run_instruction,
            existing_record_manifest=existing_record_manifest,
        )
        window = _read_trace_window(
            trace_path=trace_path,
            start_line=start_line,
            total_lines=total_lines,
            char_budget=char_budget,
        )
        deps.trace_total_lines = total_lines
        deps.read_ranges.append((window["start_line"] - 1, window["end_line"]))
        if progress:
            print(
                f"  baml window {window['start_line']}-{window['end_line']} "
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
        """Scan the current window into compact episode/findings state."""
        llm_calls = int(state.get("llm_calls") or 0)
        if llm_calls >= max_llm_calls:
            raise RuntimeError(
                f"BAML extraction exceeded max_llm_calls={max_llm_calls}."
            )
        window = state.get("current_window") or {}
        if not window.get("text"):
            return {}
        if progress:
            print(f"  baml scan {llm_calls + 1}/{max_llm_calls}", flush=True)
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
            print(f"  baml synth {llm_calls + 1}/{max_llm_calls}", flush=True)
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
        runtime_context.deps.findings_checked = True
        observations, done, completion_summary = persist_synthesized_extraction(
            state.get("synthesized"),
            runtime_context,
        )
        if progress:
            print(f"  baml persist done={done}", flush=True)
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


def _trace_line_count(trace_path: Path) -> int:
    """Return the number of lines in a trace file."""
    try:
        return sum(1 for _ in trace_path.open("r", encoding="utf-8"))
    except OSError:
        return 0


def _window_char_budget(
    *,
    state: WindowExtractGraphState,
    run_instruction: str,
    existing_record_manifest: str,
) -> int:
    """Compute how much raw trace text can fit in the next scan window."""
    soft_tokens = int(MODEL_CONTEXT_TOKEN_LIMIT * CONTEXT_SOFT_PRESSURE_PCT)
    state_text = "\n".join(
        [
            run_instruction,
            existing_record_manifest,
            _episode_summary(state),
            _durable_findings_summary(state),
            _implementation_summary(state),
        ]
    )
    state_tokens = math.ceil(len(state_text) * _TOKENS_PER_CHAR)
    available_tokens = max(
        MIN_WINDOW_CHARS * _TOKENS_PER_CHAR,
        soft_tokens - WINDOW_RESERVE_TOKENS - state_tokens,
    )
    return min(
        MAX_WINDOW_CHARS,
        max(MIN_WINDOW_CHARS, int(available_tokens / _TOKENS_PER_CHAR)),
    )


def _read_trace_window(
    *,
    trace_path: Path,
    start_line: int,
    total_lines: int,
    char_budget: int,
) -> dict[str, Any]:
    """Read as many complete trace lines as fit in the character budget."""
    numbered: list[str] = []
    current_chars = 0
    end_line = start_line - 1
    with trace_path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if line_number < start_line:
                continue
            line = raw_line.rstrip("\n")
            if len(line) > TRACE_MAX_LINE_BYTES:
                dropped = len(line) - TRACE_MAX_LINE_BYTES
                line = (
                    line[:TRACE_MAX_LINE_BYTES]
                    + f" ... [truncated {dropped} chars from this line]"
                )
            rendered = f"{line_number}\t{line}"
            if numbered and current_chars + len(rendered) + 1 > char_budget:
                break
            numbered.append(rendered)
            current_chars += len(rendered) + 1
            end_line = line_number
            if current_chars >= char_budget:
                break
    if not numbered and start_line <= total_lines:
        numbered.append(f"{start_line}\t")
        end_line = start_line
    header = f"[{total_lines} lines, window {start_line}-{end_line}]"
    if end_line < total_lines:
        header += f" — next window starts at line {end_line + 1}"
    return {
        "start_line": start_line,
        "end_line": end_line,
        "header": header,
        "text": header + "\n" + "\n".join(numbered),
    }


def _call_baml_with_retries(call, *, stage: str, progress: bool) -> tuple[Any, list[dict[str, Any]], int]:
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
                print(f"  baml retry {stage} attempt={attempts}", flush=True)
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


def _baml_client_for_model(
    *,
    model_name: str,
    baml_provider: str,
    api_base_url: str | None,
    api_key: str | None,
    temperature: float,
    ollama_base_url: str,
):
    """Return a generated BAML client pointed at the requested runtime model."""
    normalized_provider = baml_provider.strip().lower()
    if normalized_provider == "ollama":
        client_name = "RuntimeOllama"
        base_url = api_base_url or ollama_base_url
        resolved_api_key = api_key
        resolved_temperature = temperature
    elif normalized_provider == "minimax":
        client_name = "RuntimeMiniMax"
        cfg = get_config()
        base_url = api_base_url or cfg.provider_api_bases.get("minimax") or MINIMAX_BASE_URL
        resolved_api_key = api_key or cfg.minimax_api_key
        if not resolved_api_key:
            raise RuntimeError("missing_api_key:MINIMAX_API_KEY required for BAML MiniMax client")
        resolved_temperature = max(
            MINIMAX_TEMPERATURE_FLOOR,
            min(1.0, float(temperature)),
        )
    elif normalized_provider == "openai-generic":
        client_name = "RuntimeOpenAIGeneric"
        base_url = api_base_url
        if not base_url:
            raise RuntimeError("missing_api_base:openai-generic BAML client requires api_base_url")
        resolved_api_key = api_key
        resolved_temperature = temperature
    else:
        raise RuntimeError(f"unsupported_baml_provider:{baml_provider}")

    options: dict[str, Any] = {
        "base_url": base_url,
        "model": model_name,
        "temperature": resolved_temperature,
        "http": {
            "connect_timeout_ms": BAML_HTTP_CONNECT_TIMEOUT_MS,
            "time_to_first_token_timeout_ms": BAML_HTTP_TIME_TO_FIRST_TOKEN_TIMEOUT_MS,
            "idle_timeout_ms": BAML_HTTP_IDLE_TIMEOUT_MS,
            "request_timeout_ms": BAML_HTTP_REQUEST_TIMEOUT_MS,
        },
    }
    if resolved_api_key:
        options["api_key"] = resolved_api_key

    registry = ClientRegistry()
    registry.add_llm_client(
        name=client_name,
        provider="openai-generic",
        options=options,
        retry_policy="ExtractAgentRetry",
    )
    registry.set_primary(client_name)
    return b.with_options(client_registry=registry)


def _is_recoverable_baml_error(exc: Exception) -> bool:
    """Return whether a BAML model/parsing failure should be retried in graph."""
    return type(exc).__name__ in BAML_RECOVERABLE_ERROR_NAMES


def _model_retry_observation(exc: Exception) -> str:
    """Render a compact model failure note for the next BAML turn."""
    message = str(exc).replace("\n", " ")[:1200]
    return (
        "The previous BAML model call did not produce a valid next action. "
        "Retry and return exactly one JSON object matching the requested schema. "
        "Do not include <think> tags, hidden reasoning, markdown, or prose before "
        f"the JSON. Error: {type(exc).__name__}: {message}"
    )


def _build_run_instruction(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity,
    trace_path: Path,
    session_started_at: str,
    existing_record_manifest: str | None = None,
) -> str:
    """Build the same extraction task framing used by Lerim's current agent."""
    try:
        trace_line_count = sum(1 for _ in trace_path.open("r", encoding="utf-8"))
    except OSError:
        trace_line_count = 0
    if existing_record_manifest is None:
        existing_record_manifest = _format_existing_record_manifest(
            context_db_path=context_db_path,
            project_identity=project_identity,
        )
    source_time_text = str(session_started_at or "").strip() or "unknown"
    prompt = (
        "Read the trace, write exactly one episode record, and write only the strongest "
        "durable records with non-empty title and body. Store reusable rules and decisions, "
        "not a polished recap of the meeting. "
        "Durable records must be positive canonical context: when trace text combines a "
        "durable point with cleanup/noise/ignore guidance, exclude that guidance entirely "
        "from the durable record. "
        f"Source session started_at: {source_time_text}. Treat the trace as evidence from "
        "that time, not as a fresh verification of the current repository. "
        f"This trace has {trace_line_count} lines. Read all chunks before writing. "
        "If the trace needs more than one read to cover it, record findings before any write. "
        "If relevant existing durable records are shown below, treat them as a shortlist only; "
        "fetch the full record before any revision."
        + (f"\n\n{existing_record_manifest}" if existing_record_manifest else "")
    )
    return prompt
