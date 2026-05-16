"""LangGraph state for BAML context curator."""

from __future__ import annotations

import operator
from typing import Annotated, Any
from typing_extensions import TypedDict


class ContextCuratorGraphState(TypedDict, total=False):
    """State for the BAML/LangGraph context-curator pipeline."""

    observations: Annotated[list[dict[str, Any]], operator.add]
    llm_calls: int
    records: list[dict[str, Any]]
    records_by_id: dict[str, dict[str, Any]]
    clusters: list[dict[str, Any]]
    clustered_record_ids: list[str]
    health_batches: list[list[dict[str, Any]]]
    action_plans: Annotated[list[dict[str, Any]], operator.add]
    done: bool
    completion_summary: str
