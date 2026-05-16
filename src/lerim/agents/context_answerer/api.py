"""Production context-answerer agent API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lerim.agents.context_answerer.graph import run_context_answerer_graph
from lerim.agents.context_answerer.types import ContextAnswerResult
from lerim.agents.mlflow_observability import mlflow_span
from lerim.config.settings import Config, get_config
from lerim.context import ProjectIdentity


CONTEXT_ANSWERER_SYSTEM_PROMPT = """\
Lerim context answering plans retrieval, executes read-only context-store queries, and
synthesizes an answer from retrieved records only.
"""


def run_context_answerer(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity,
    project_ids: list[str] | None,
    session_id: str,
    question: str,
    config: Config | None = None,
    hints: str = "",
    return_messages: bool = False,
) -> ContextAnswerResult | tuple[ContextAnswerResult, list[dict[str, Any]]]:
    """Plan retrieval, execute store reads, and synthesize a grounded answer."""
    cfg = config or get_config()
    resolved_project_ids = project_ids or [project_identity.project_id]
    with mlflow_span(
        "lerim.agent.context_answerer",
        span_type="AGENT",
        attributes={"lerim.agent_name": "context_answerer"},
        inputs={
            "question": question.strip(),
            "project_ids": resolved_project_ids,
            "session_id": session_id,
        },
    ):
        final_state = run_context_answerer_graph(
            context_db_path=context_db_path,
            project_identity=project_identity,
            project_ids=resolved_project_ids,
            question=question,
            config=cfg,
            hints=hints,
        )
    result = final_state.get("result")
    if not isinstance(result, ContextAnswerResult):
        raise ValueError("context_answerer_result_missing")
    events = [
        dict(item)
        for item in final_state.get("events", [])
        if isinstance(item, dict)
    ]
    if return_messages:
        return result, events
    return result


if __name__ == "__main__":
    """Run a tiny constructor smoke check."""
    assert CONTEXT_ANSWERER_SYSTEM_PROMPT
    print("context answerer: self-test passed")
