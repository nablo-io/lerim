"""Production source-session ingestion agent API."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import overload

from lerim.agents.baml_runtime import model_label
from lerim.agents.trace_ingestion.graph import run_trace_ingestion_graph
from lerim.agents.trace_ingestion.persistence import (
    PersistenceContext,
    format_existing_record_manifest,
    prepare_context_store,
)
from lerim.agents.trace_ingestion.types import (
    TraceIngestionEvent,
    TraceIngestionResult,
    TraceIngestionRunDetails,
)
from lerim.agents.trace_ingestion.windowing import trace_line_count
from lerim.agents.mlflow_observability import mlflow_span
from lerim.config.settings import Config, get_config
from lerim.context import ProjectIdentity, ScopeIdentity, scope_from_project


@overload
def run_trace_ingestion(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity | None,
    scope_identity: ScopeIdentity | None = None,
    session_id: str,
    trace_path: Path,
    config: Config | None = None,
    session_started_at: str = "",
    return_details: bool = False,
    provider: str | None = None,
    model_name: str | None = None,
    api_base_url: str | None = None,
    api_key: str | None = None,
    temperature: float | None = None,
    max_llm_calls: int | None = None,
    progress: bool = False,
) -> TraceIngestionResult:
    ...


@overload
def run_trace_ingestion(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity | None,
    scope_identity: ScopeIdentity | None = None,
    session_id: str,
    trace_path: Path,
    config: Config | None = None,
    session_started_at: str = "",
    return_details: bool,
    provider: str | None = None,
    model_name: str | None = None,
    api_base_url: str | None = None,
    api_key: str | None = None,
    temperature: float | None = None,
    max_llm_calls: int | None = None,
    progress: bool = False,
) -> tuple[TraceIngestionResult, TraceIngestionRunDetails]:
    ...


def run_trace_ingestion(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity | None,
    scope_identity: ScopeIdentity | None = None,
    session_id: str,
    trace_path: Path,
    config: Config | None = None,
    session_started_at: str = "",
    return_details: bool = False,
    provider: str | None = None,
    model_name: str | None = None,
    api_base_url: str | None = None,
    api_key: str | None = None,
    temperature: float | None = None,
    max_llm_calls: int | None = None,
    progress: bool = False,
    source_name: str | None = None,
    source_profile: str | None = None,
) -> TraceIngestionResult | tuple[TraceIngestionResult, TraceIngestionRunDetails]:
    """Run the BAML and LangGraph trace-ingestion agent on one source session."""
    cfg = config or get_config()
    resolved_context_db_path = context_db_path.expanduser().resolve()
    resolved_trace_path = trace_path.expanduser().resolve()
    source_started_at = (
        str(session_started_at or "").strip()
        or datetime.now(timezone.utc).isoformat()
    )
    effective_model_label = model_label(
        config=cfg,
        provider=provider,
        model_name=model_name,
    )
    resolved_scope = scope_identity or (
        scope_from_project(project_identity) if project_identity is not None else None
    )
    if resolved_scope is None:
        raise ValueError("scope_identity_required")
    persistence_context = PersistenceContext(
        context_db_path=resolved_context_db_path,
        project_identity=project_identity,
        scope_identity=resolved_scope,
        session_id=session_id,
        trace_path=resolved_trace_path,
        session_started_at=source_started_at,
        model_name=effective_model_label,
        source_name=source_name,
        source_profile=source_profile,
    )
    prepare_context_store(persistence_context)
    existing_record_manifest = format_existing_record_manifest(
        context_db_path=resolved_context_db_path,
        project_identity=project_identity,
    )
    run_instruction = _build_run_instruction(
        context_db_path=resolved_context_db_path,
        project_identity=project_identity,
        scope_identity=resolved_scope,
        trace_path=resolved_trace_path,
        session_started_at=source_started_at,
        existing_record_manifest=existing_record_manifest,
        source_name=source_name,
        source_profile=source_profile,
    )
    line_count = trace_line_count(resolved_trace_path)
    with mlflow_span(
        "lerim.agent.trace_ingestion",
        span_type="AGENT",
        attributes={"lerim.agent_name": "trace_ingestion"},
        inputs={
            "trace_path": str(resolved_trace_path),
            "trace_line_count": line_count,
            "scope_type": resolved_scope.scope_type,
            "scope_id": resolved_scope.scope_id,
            "model_name": effective_model_label,
        },
    ):
        final_state = run_trace_ingestion_graph(
            persistence_context=persistence_context,
            config=cfg,
            run_instruction=run_instruction,
            existing_record_manifest=existing_record_manifest,
            provider=provider,
            model_name=model_name,
            api_base_url=api_base_url,
            api_key=api_key,
            temperature=temperature,
            max_llm_calls=max_llm_calls,
            progress=progress,
        )
    result = TraceIngestionResult(
        completion_summary=str(final_state.get("completion_summary") or "").strip()
        or "Source session ingestion completed."
    )
    events = [
        TraceIngestionEvent.model_validate(item)
        for item in final_state.get("observations", [])
    ]
    details = TraceIngestionRunDetails(
        events=events,
        llm_calls=int(final_state.get("llm_calls") or 0),
        done=bool(final_state.get("done")),
        context_db_path=str(resolved_context_db_path),
        project_id=project_identity.project_id if project_identity else None,
        scope_type=resolved_scope.scope_type,
        scope_id=resolved_scope.scope_id,
        session_id=session_id,
        model_name=effective_model_label,
        trace_total_lines=line_count,
    )
    if return_details:
        return result, details
    return result


def _build_run_instruction(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity | None,
    scope_identity: ScopeIdentity,
    trace_path: Path,
    session_started_at: str,
    existing_record_manifest: str | None = None,
    source_name: str | None = None,
    source_profile: str | None = None,
) -> str:
    """Build source-session ingestion task framing for the BAML graph."""
    del context_db_path, project_identity
    line_count = trace_line_count(trace_path)
    source_time_text = str(session_started_at or "").strip() or "unknown"
    source_text = str(source_name or source_profile or "agent activity stream").strip()
    prompt = (
        "Read the agent activity stream, write exactly one episode record, and write only the strongest "
        "durable records with non-empty title and body. Store reusable rules, decisions, "
        "preferences, constraints, facts, and references, not a polished recap of the interaction. "
        "Treat this as source-session-to-context ingestion for future agents. "
        "Context ingestion has explicit layers: resolve source scope, scan source windows, separate durable candidates from "
        "execution evidence and noise, aggressively filter candidates, then synthesize "
        "records only from the kept signal. "
        f"Scope: {scope_identity.scope_type}:{scope_identity.scope_id} ({scope_identity.label}). "
        f"Source: {source_text}. "
        "Durable records must be positive canonical context: when source text combines a "
        "durable point with cleanup/noise/ignore guidance, exclude that guidance entirely "
        "from the durable record. "
        f"Source session started_at: {source_time_text}. Treat the source material as evidence from "
        "that time, not as a fresh verification of the current workspace. "
        f"The source material has {line_count} lines. Read all chunks before writing. "
        "If relevant existing durable records are shown below, treat them as duplicate-risk "
        "context only; prefer skipping near-duplicates over creating duplicates."
        + (f"\n\n{existing_record_manifest}" if existing_record_manifest else "")
    )
    return prompt
