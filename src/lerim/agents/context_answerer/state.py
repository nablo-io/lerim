"""LangGraph state for the BAML context-answerer pipeline."""

from __future__ import annotations

import operator
from typing import Annotated, Any

from typing_extensions import TypedDict


class ContextAnswererGraphState(TypedDict, total=False):
    """State for retrieval planning, store reads, and answer synthesis."""

    question: str
    current_utc: str
    hints: str
    actions: list[dict[str, Any]]
    retrieval_payload: dict[str, Any]
    result: Any
    events: Annotated[list[dict[str, Any]], operator.add]
    done: bool
