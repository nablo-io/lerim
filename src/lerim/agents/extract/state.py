"""LangGraph state for BAML extraction."""

from __future__ import annotations

import operator
from typing import Annotated, Any
from typing_extensions import TypedDict


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
