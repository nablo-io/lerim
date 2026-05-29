"""Production context-graph agent API."""

from __future__ import annotations

from pathlib import Path
from typing import overload

from lerim.agents.context_graph.inventory import prepare_context_graph_store
from lerim.agents.context_graph.pipeline import ContextGraphPipeline
from lerim.agents.context_graph.types import ContextGraphEvent, ContextGraphResult, ContextGraphRunDetails
from lerim.agents.mlflow_observability import mlflow_span
from lerim.agents.model_runtime import model_label
from lerim.config.settings import Config, get_config
from lerim.context import ProjectIdentity


@overload
def run_context_graph(
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
) -> ContextGraphResult:
    ...


@overload
def run_context_graph(
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
) -> tuple[ContextGraphResult, ContextGraphRunDetails]:
    ...


def run_context_graph(
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
) -> ContextGraphResult | tuple[ContextGraphResult, ContextGraphRunDetails]:
    """Run the context-graph pipeline on one project scope."""
    cfg = config or get_config()
    resolved_context_db_path = context_db_path.expanduser().resolve()
    effective_model_label = model_label(
        config=cfg,
        provider=provider,
        model_name=model_name,
    )
    prepare_context_graph_store(
        context_db_path=resolved_context_db_path,
        project_identity=project_identity,
        session_id=session_id,
        model_name=effective_model_label,
    )
    with mlflow_span(
        "lerim.agent.context_graph",
        span_type="AGENT",
        attributes={"lerim.agent_name": "context_graph"},
        inputs={"model_name": effective_model_label},
    ):
        final_state = ContextGraphPipeline(
            context_db_path=resolved_context_db_path,
            project_identity=project_identity,
            session_id=session_id,
            config=cfg,
            provider=provider,
            model_name=model_name,
            api_base_url=api_base_url,
            api_key=api_key,
            temperature=temperature,
            max_model_steps=max_llm_calls or 20,
            progress=progress,
        )()
    write_summary = final_state.get("write_summary") or {}
    result = ContextGraphResult(
        completion_summary=str(final_state.get("completion_summary") or "").strip()
        or "Context graph refreshed.",
        nodes_written=int(write_summary.get("nodes_written") or 0),
        edges_written=int(write_summary.get("edges_written") or 0),
        semantic_clusters=int(write_summary.get("semantic_clusters") or 0),
    )
    events = [
        ContextGraphEvent.model_validate(item)
        for item in final_state.get("observations", [])
    ]
    details = ContextGraphRunDetails(
        events=events,
        llm_calls=int(final_state.get("llm_calls") or 0),
        done=bool(final_state.get("done")),
        context_db_path=str(resolved_context_db_path),
        project_id=project_identity.project_id,
        session_id=session_id,
        model_name=effective_model_label,
        active_record_count=len(final_state.get("records") or []),
        semantic_cluster_count=len(final_state.get("semantic_clusters") or []),
        candidate_pair_count=len(final_state.get("candidate_pairs") or []),
        proposed_link_count=len(final_state.get("proposed_links") or []),
        written_node_count=result.nodes_written,
        written_edge_count=result.edges_written,
    )
    if return_details:
        return result, details
    return result
