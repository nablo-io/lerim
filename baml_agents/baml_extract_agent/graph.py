"""LangGraph ReAct loop whose LLM decisions are produced by BAML."""

from __future__ import annotations

from collections import Counter
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
    _TOKENS_PER_CHAR,
    MODEL_CONTEXT_TOKEN_LIMIT,
    ContextDeps,
    _classify_context_pressure,
    _first_uncovered_offset,
    compute_request_budget,
)
from lerim.context import ProjectIdentity, resolve_project_identity
from lerim.context.spec import DURABLE_FINDING_LEVELS, IMPLEMENTATION_FINDING_LEVELS

from baml_extract_agent.tool_bridge import (
    build_tool_context,
    execute_step,
    format_observation,
    observation_to_state,
    prepare_context_deps,
    tool_manifest,
)


MODEL_NAME = "gemma4:e4b"
BAML_PROVIDER = "ollama"
OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
MINIMAX_BASE_URL = "https://api.minimax.io/v1"
MINIMAX_TEMPERATURE_FLOOR = 0.01
MAX_BAML_MODEL_RETRIES = 3
BAML_HTTP_CONNECT_TIMEOUT_MS = 10_000
BAML_HTTP_TIME_TO_FIRST_TOKEN_TIMEOUT_MS = 120_000
BAML_HTTP_IDLE_TIMEOUT_MS = 30_000
BAML_HTTP_REQUEST_TIMEOUT_MS = 300_000
BAML_RECOVERABLE_ERROR_NAMES = {
    "BamlClientFinishReasonError",
    "BamlClientHttpError",
    "BamlTimeoutError",
    "BamlValidationError",
}


class ExtractGraphState(TypedDict, total=False):
    """Mutable state carried through the BAML ReAct graph."""

    observations: Annotated[list[dict[str, Any]], operator.add]
    llm_calls: int
    pending_step: Any
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
    graph = build_extract_graph(
        deps=deps,
        run_instruction=_build_run_instruction(
            context_db_path=resolved_context_db_path,
            project_identity=identity,
            trace_path=resolved_trace_path,
            session_started_at=started_at,
        ),
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
        {"observations": [], "llm_calls": 0, "done": False, "completion_summary": ""}
    )
    if not final_state.get("done"):
        raise RuntimeError("BAML extraction graph stopped before final_result.")
    return {
        "completion_summary": final_state.get("completion_summary", ""),
        "llm_calls": final_state.get("llm_calls", 0),
        "observations": final_state.get("observations", []),
        "context_db_path": str(resolved_context_db_path),
        "project_id": identity.project_id,
        "session_id": session_id,
        "model_name": model_name,
        "baml_provider": baml_provider,
    }


def build_extract_graph(
    *,
    deps: ContextDeps,
    run_instruction: str,
    model_name: str,
    baml_provider: str,
    api_base_url: str | None,
    api_key: str | None,
    temperature: float,
    ollama_base_url: str,
    max_llm_calls: int,
    progress: bool = False,
):
    """Compile the LangGraph state machine for one extraction run."""
    runtime_context = build_tool_context(deps)
    live_tool_manifest = tool_manifest()
    baml_runtime = _baml_client_for_model(
        model_name=model_name,
        baml_provider=baml_provider,
        api_base_url=api_base_url,
        api_key=api_key,
        temperature=temperature,
        ollama_base_url=ollama_base_url,
    )

    def llm_call(state: ExtractGraphState) -> dict[str, Any]:
        """Ask BAML to choose the next ReAct action."""
        llm_calls = int(state.get("llm_calls") or 0)
        if llm_calls >= max_llm_calls:
            raise RuntimeError(
                f"BAML extraction exceeded max_llm_calls={max_llm_calls}."
            )
        observations = state.get("observations", [])
        scratchpad = _scratchpad(observations, deps)
        if progress:
            print(f"  baml llm {llm_calls + 1}/{max_llm_calls}", flush=True)
        try:
            step = baml_runtime.DecideNextExtractStep(
                runtime_dashboard=_runtime_dashboard(deps, observations),
                run_instruction=run_instruction,
                tool_manifest=live_tool_manifest,
                scratchpad=scratchpad,
            )
        except Exception as exc:
            if not _is_recoverable_baml_error(exc):
                raise
            model_retry_count = sum(
                1 for observation in observations if observation.get("action") == "model_retry"
            )
            if model_retry_count >= MAX_BAML_MODEL_RETRIES:
                raise RuntimeError(
                    f"BAML extraction exceeded model_retry_limit={MAX_BAML_MODEL_RETRIES}."
                ) from exc
            step = {
                "action": "model_retry",
                "content": _model_retry_observation(exc),
            }
        return {"pending_step": step, "llm_calls": llm_calls + 1}

    def tool_node(state: ExtractGraphState) -> dict[str, Any]:
        """Execute the BAML-selected action with Lerim's real tools."""
        pending_step = state["pending_step"]
        if isinstance(pending_step, dict) and pending_step.get("action") == "model_retry":
            if progress:
                print("  baml tool model_retry", flush=True)
            return {
                "observations": [
                    {
                        "action": "model_retry",
                        "ok": False,
                        "content": pending_step["content"],
                        "args": {},
                        "done": False,
                        "completion_summary": "",
                    }
                ],
                "done": False,
                "completion_summary": "",
            }
        observation = execute_step(pending_step, runtime_context)
        if progress:
            print(f"  baml tool {observation.action} ok={observation.ok}", flush=True)
        return {
            "observations": [observation_to_state(observation)],
            "done": observation.done,
            "completion_summary": observation.completion_summary,
        }

    def should_continue(state: ExtractGraphState) -> str:
        """Route back to the LLM until final_result validates."""
        if bool(state.get("done")):
            return END
        return "llm_call"

    graph = StateGraph(ExtractGraphState)
    graph.add_node("llm_call", llm_call)
    graph.add_node("tool_node", tool_node)
    graph.add_edge(START, "llm_call")
    graph.add_edge("llm_call", "tool_node")
    graph.add_conditional_edges("tool_node", should_continue, ["llm_call", END])
    return graph.compile()


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
) -> str:
    """Build the same extraction task framing used by Lerim's current agent."""
    try:
        trace_line_count = sum(1 for _ in trace_path.open("r", encoding="utf-8"))
    except OSError:
        trace_line_count = 0
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


def _scratchpad(observations: list[dict[str, Any]], deps: ContextDeps) -> str:
    """Render prior actions for the next BAML decision."""
    if not observations:
        return "No prior actions."
    return "\n\n".join(
        format_observation(observation, deps) for observation in observations[-20:]
    )


def _runtime_dashboard(deps: ContextDeps, observations: list[dict[str, Any]]) -> str:
    """Render the same context-pressure and notes dashboards as the extract agent."""
    scratchpad_chars = sum(
        len(format_observation(observation, deps)) for observation in observations
    )
    approx_tokens = math.ceil(scratchpad_chars * _TOKENS_PER_CHAR)
    pct = approx_tokens / MODEL_CONTEXT_TOKEN_LIMIT
    pressure = _classify_context_pressure(pct)
    deps.last_context_tokens = approx_tokens
    deps.last_context_fill_ratio = pct
    context_summary = (
        f"CONTEXT: {approx_tokens}/{MODEL_CONTEXT_TOKEN_LIMIT} ({pct:.0%}) [{pressure}]"
    )
    return context_summary + "\n" + _notes_dashboard(deps)


def _notes_dashboard(deps: ContextDeps) -> str:
    """Render the notes and trace-coverage dashboard used between model turns."""
    findings = deps.notes
    if not findings:
        summary = "NOTES: 0 findings"
        if deps.findings_checked:
            summary += " (checkpoint recorded)"
    else:
        counts = Counter(finding.level for finding in findings)
        durable_findings = [
            finding for finding in findings if finding.level in DURABLE_FINDING_LEVELS
        ]
        theme_source = durable_findings or findings
        themes = Counter(finding.theme for finding in theme_source)
        durable = sum(counts.get(level, 0) for level in DURABLE_FINDING_LEVELS)
        implementation = sum(
            counts.get(level, 0) for level in IMPLEMENTATION_FINDING_LEVELS
        )
        top_themes = ", ".join(
            f"{theme}({count})" for theme, count in themes.most_common(5)
        )
        summary = (
            f"NOTES: {len(findings)} findings ({durable} durable, {implementation} implementation) "
            f"across {len(themes)} theme(s)"
        )
        if top_themes:
            summary += f"\nTop themes: {top_themes}"
    if deps.read_ranges:
        next_uncovered = _first_uncovered_offset(
            deps.read_ranges,
            int(deps.trace_total_lines),
        )
        covered_chunks = len({(int(start), int(end)) for start, end in deps.read_ranges})
        summary += (
            f"\nTrace reads: {covered_chunks} chunk(s)"
            f"\nNext unread offset: {next_uncovered if next_uncovered is not None else 'none'}"
            f"\nPruned offsets: {sorted(deps.pruned_offsets) if deps.pruned_offsets else 'none'}"
        )
    return summary
