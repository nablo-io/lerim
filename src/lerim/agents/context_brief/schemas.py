"""Structured model schemas for Context Brief compilation."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ContextBriefLineDraft(BaseModel):
    """One compact memory line with exact source record IDs."""

    text: str = Field(description="Compact memory statement without inline citations.")
    record_ids: list[str] = Field(
        description="Exact source record IDs copied from the candidate records."
    )


class ContextBriefDraftOutput(BaseModel):
    """Fixed-section Context Brief model output."""

    summary: list[ContextBriefLineDraft] = Field(default_factory=list)
    start_here: list[ContextBriefLineDraft] = Field(default_factory=list)
    current_handoff: list[ContextBriefLineDraft] = Field(default_factory=list)
    decisions: list[ContextBriefLineDraft] = Field(
        default_factory=list,
        description="Only cite candidate records whose kind is decision.",
    )
    constraints_preferences: list[ContextBriefLineDraft] = Field(
        default_factory=list,
        description="Only cite candidate records whose kind is preference or constraint.",
    )
    operational_context: list[ContextBriefLineDraft] = Field(
        default_factory=list,
        description="Reusable procedures, gotchas, failure modes, artifacts, state changes, and eval assets.",
    )
    project_facts: list[ContextBriefLineDraft] = Field(
        default_factory=list,
        description="Only cite candidate records whose kind is fact.",
    )
    open_risks: list[ContextBriefLineDraft] = Field(default_factory=list)
    follow_up_queries: list[ContextBriefLineDraft] = Field(default_factory=list)
