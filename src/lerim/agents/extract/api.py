"""Production extract-agent API."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import overload

from lerim.agents.baml_runtime import model_label
from lerim.agents.extract.graph import run_windowed_extract_graph
from lerim.agents.extract.persistence import (
    PersistenceContext,
    format_existing_record_manifest,
    prepare_context_store,
)
from lerim.agents.extract.types import (
    ExtractionEvent,
    ExtractionResult,
    ExtractionRunDetails,
)
from lerim.agents.extract.windowing import trace_line_count
from lerim.agents.mlflow_observability import mlflow_span
from lerim.config.settings import Config, get_config
from lerim.context import ProjectIdentity


@overload
def run_extraction(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity,
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
) -> ExtractionResult:
    ...


@overload
def run_extraction(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity,
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
) -> tuple[ExtractionResult, ExtractionRunDetails]:
    ...


def run_extraction(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity,
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
) -> ExtractionResult | tuple[ExtractionResult, ExtractionRunDetails]:
    """Run the BAML and LangGraph extract agent on one trace."""
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
    persistence_context = PersistenceContext(
        context_db_path=resolved_context_db_path,
        project_identity=project_identity,
        session_id=session_id,
        trace_path=resolved_trace_path,
        session_started_at=source_started_at,
        model_name=effective_model_label,
    )
    prepare_context_store(persistence_context)
    existing_record_manifest = format_existing_record_manifest(
        context_db_path=resolved_context_db_path,
        project_identity=project_identity,
    )
    run_instruction = _build_run_instruction(
        context_db_path=resolved_context_db_path,
        project_identity=project_identity,
        trace_path=resolved_trace_path,
        session_started_at=source_started_at,
        existing_record_manifest=existing_record_manifest,
    )
    line_count = trace_line_count(resolved_trace_path)
    with mlflow_span(
        "lerim.agent.extract",
        span_type="AGENT",
        attributes={"lerim.agent_name": "extract"},
        inputs={
            "trace_path": str(resolved_trace_path),
            "trace_line_count": line_count,
            "model_name": effective_model_label,
        },
    ):
        final_state = run_windowed_extract_graph(
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
    result = ExtractionResult(
        completion_summary=str(final_state.get("completion_summary") or "").strip()
        or "Extraction completed."
    )
    events = [
        ExtractionEvent.model_validate(item)
        for item in final_state.get("observations", [])
    ]
    details = ExtractionRunDetails(
        events=events,
        llm_calls=int(final_state.get("llm_calls") or 0),
        done=bool(final_state.get("done")),
        context_db_path=str(resolved_context_db_path),
        project_id=project_identity.project_id,
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
    project_identity: ProjectIdentity,
    trace_path: Path,
    session_started_at: str,
    existing_record_manifest: str | None = None,
) -> str:
    """Build extraction task framing for the BAML graph."""
    del context_db_path, project_identity
    line_count = trace_line_count(trace_path)
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
        f"This trace has {line_count} lines. Read all chunks before writing. "
        "If relevant existing durable records are shown below, treat them as duplicate-risk "
        "context only; prefer skipping near-duplicates over creating duplicates."
        + (f"\n\n{existing_record_manifest}" if existing_record_manifest else "")
    )
    return prompt
