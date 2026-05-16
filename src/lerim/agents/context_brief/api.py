"""Production context-brief compiler agent API."""

from __future__ import annotations

from typing import Any

from lerim.agents.context_brief.graph import run_context_brief_graph
from lerim.agents.mlflow_observability import mlflow_span
from lerim.config.settings import Config, get_config
from lerim.context_brief import ContextBriefDraft


def compile_context_brief(
    *,
    config: Config | None = None,
    candidates: list[dict[str, Any]],
    return_messages: bool = False,
) -> ContextBriefDraft | tuple[ContextBriefDraft, list[Any]]:
    """Compile a fixed-section Context Brief from bounded candidate records."""
    cfg = config or get_config()
    with mlflow_span(
        "lerim.agent.context_brief_compiler",
        span_type="AGENT",
        attributes={"lerim.agent_name": "context_brief_compiler"},
        inputs={"candidate_count": len(candidates)},
    ):
        final_state = run_context_brief_graph(
            config=cfg,
            candidates=candidates,
        )
    draft = final_state.get("draft")
    if not isinstance(draft, ContextBriefDraft):
        raise ValueError("context_brief_draft_missing")
    events = [
        dict(item)
        for item in final_state.get("events", [])
        if isinstance(item, dict)
    ]
    if return_messages:
        return draft, events
    return draft
