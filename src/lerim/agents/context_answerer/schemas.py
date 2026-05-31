"""Structured model schemas for the context-answer workflow."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ContextRetrievalActionType = Literal["count", "list", "search"]
ContextRecordKind = Literal["decision", "preference", "constraint", "fact", "episode"]
ContextRecordRole = Literal[
    "general",
    "procedure",
    "gotcha",
    "failure_mode",
    "artifact",
    "state_change",
    "eval_asset",
]


class ContextRetrievalAction(BaseModel):
    """One read-only context-store action selected by the model."""

    action_type: ContextRetrievalActionType
    query: str | None = None
    kind: ContextRecordKind | None = None
    record_role: ContextRecordRole | None = None
    status: str | None = None
    source_session_id: str | None = None
    created_since: str | None = None
    created_until: str | None = None
    updated_since: str | None = None
    updated_until: str | None = None
    valid_at: str | None = None
    include_archived: bool | None = None
    order_by: str | None = None
    limit: int | None = None
    rationale: str | None = None


class ContextRetrievalPlan(BaseModel):
    """Structured retrieval plan generated before store reads."""

    actions: list[ContextRetrievalAction] = Field(default_factory=list)
    rationale: str | None = None


class ContextAnswer(BaseModel):
    """Structured grounded answer generated from retrieved context."""

    answer: str
    supporting_record_ids: list[str] = Field(default_factory=list)
