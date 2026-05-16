"""LangGraph state for the BAML context-brief compiler."""

from __future__ import annotations

import operator
from typing import Annotated, Any

from typing_extensions import TypedDict


class ContextBriefGraphState(TypedDict, total=False):
    """State for candidate preparation and brief synthesis."""

    candidates: list[dict[str, Any]]
    compact_candidates: list[dict[str, Any]]
    candidate_profile: dict[str, Any]
    candidate_profile_json: str
    candidate_records_json: str
    output: Any
    draft: Any
    events: Annotated[list[dict[str, Any]], operator.add]
    done: bool
