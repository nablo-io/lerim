"""Structured model schemas for context graph linking."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


ContextGraphRelationKind = Literal[
    "supports",
    "refines",
    "depends_on",
    "contradicts",
    "same_topic",
    "evidence_for",
    "supersedes",
    "related",
]


class ContextGraphLink(BaseModel):
    """One useful relationship between two context records."""

    source_record_id: str
    target_record_id: str
    relation_kind: ContextGraphRelationKind
    label: str
    rationale: str
    evidence_record_ids: list[str]
    confidence: float


class ContextGraphPlan(BaseModel):
    """Structured context graph link plan."""

    links: list[ContextGraphLink]
    completion_summary: str | None = None
