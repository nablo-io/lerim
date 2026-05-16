"""Production context-curator agent API."""

from __future__ import annotations

from pathlib import Path
from typing import overload

from lerim.agents.baml_runtime import model_label
from lerim.agents.context_curator.graph import run_context_curator_graph
from lerim.agents.context_curator.inventory import prepare_context_curator_store
from lerim.agents.context_curator.types import ContextCuratorEvent, ContextCuratorResult, ContextCuratorRunDetails
from lerim.agents.mlflow_observability import mlflow_span
from lerim.config.settings import Config, get_config
from lerim.context import ProjectIdentity


@overload
def run_context_curator(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity,
    session_id: str,
    config: Config | None = None,
    return_details: bool = False,
    provider: str | None = None,
    model_name: str | None = None,
    api_base_url: str | None = None,
    api_key: str | None = None,
    temperature: float | None = None,
    max_llm_calls: int | None = None,
    progress: bool = False,
) -> ContextCuratorResult:
    ...


@overload
def run_context_curator(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity,
    session_id: str,
    config: Config | None = None,
    return_details: bool,
    provider: str | None = None,
    model_name: str | None = None,
    api_base_url: str | None = None,
    api_key: str | None = None,
    temperature: float | None = None,
    max_llm_calls: int | None = None,
    progress: bool = False,
) -> tuple[ContextCuratorResult, ContextCuratorRunDetails]:
    ...


def run_context_curator(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity,
    session_id: str,
    config: Config | None = None,
    return_details: bool = False,
    provider: str | None = None,
    model_name: str | None = None,
    api_base_url: str | None = None,
    api_key: str | None = None,
    temperature: float | None = None,
    max_llm_calls: int | None = None,
    progress: bool = False,
) -> ContextCuratorResult | tuple[ContextCuratorResult, ContextCuratorRunDetails]:
    """Run the BAML and LangGraph context-curator agent on one project scope."""
    cfg = config or get_config()
    resolved_context_db_path = context_db_path.expanduser().resolve()
    effective_model_label = model_label(
        config=cfg,
        provider=provider,
        model_name=model_name,
    )
    prepare_context_curator_store(
        context_db_path=resolved_context_db_path,
        project_identity=project_identity,
        session_id=session_id,
        model_name=effective_model_label,
    )
    with mlflow_span(
        "lerim.agent.context_curator",
        span_type="AGENT",
        attributes={"lerim.agent_name": "context_curator"},
        inputs={"model_name": effective_model_label},
    ):
        final_state = run_context_curator_graph(
            context_db_path=resolved_context_db_path,
            project_identity=project_identity,
            session_id=session_id,
            config=cfg,
            provider=provider,
            model_name=model_name,
            api_base_url=api_base_url,
            api_key=api_key,
            temperature=temperature,
            max_llm_calls=max_llm_calls,
            progress=progress,
        )
    result = ContextCuratorResult(
        completion_summary=str(final_state.get("completion_summary") or "").strip()
        or "Context curation completed."
    )
    events = [
        ContextCuratorEvent.model_validate(item)
        for item in final_state.get("observations", [])
    ]
    details = ContextCuratorRunDetails(
        events=events,
        llm_calls=int(final_state.get("llm_calls") or 0),
        done=bool(final_state.get("done")),
        context_db_path=str(resolved_context_db_path),
        project_id=project_identity.project_id,
        session_id=session_id,
        model_name=effective_model_label,
        active_record_count=len(final_state.get("records") or []),
        cluster_count=len(final_state.get("clusters") or []),
    )
    if return_details:
        return result, details
    return result
