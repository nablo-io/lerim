"""Structured model schemas for context curation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


ContextCuratorRecordKind = Literal[
    "decision",
    "preference",
    "constraint",
    "fact",
    "episode",
]
ContextCuratorActionType = Literal["noop", "revise", "archive", "supersede"]
RecordStatus = Literal["active", "archived", "superseded"]


class ContextCuratorRecordPatch(BaseModel):
    """Complete replacement fields for one revised context record."""

    kind: ContextCuratorRecordKind
    title: str
    body: str
    status: RecordStatus | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    decision: str | None = None
    why: str | None = None
    alternatives: str | None = None
    consequences: str | None = None
    user_intent: str | None = None
    what_happened: str | None = None
    outcomes: str | None = None


class ContextCurationAction(BaseModel):
    """One proposed context-record mutation."""

    action_type: ContextCuratorActionType
    record_id: str
    replacement_record_id: str | None = None
    reason: str
    patch: ContextCuratorRecordPatch | None = None


class ContextCurationPlan(BaseModel):
    """Structured curation action plan."""

    actions: list[ContextCurationAction]
    completion_summary: str | None = None
