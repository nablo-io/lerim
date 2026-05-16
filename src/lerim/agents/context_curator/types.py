"""Public types returned by the context-curator flow."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ContextCuratorEvent(BaseModel):
    """One structured event emitted by the context-curator graph."""

    action: str
    ok: bool = True
    content: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    done: bool = False
    completion_summary: str = ""


class ContextCuratorResult(BaseModel):
    """Structured output for the context-curator flow."""

    completion_summary: str = Field(description="Short plain-text completion summary")


class ContextCuratorRunDetails(BaseModel):
    """Structured trace for one context-curator run."""

    events: list[ContextCuratorEvent] = Field(default_factory=list)
    llm_calls: int = 0
    done: bool = False
    context_db_path: str
    project_id: str
    session_id: str
    model_name: str
    active_record_count: int = 0
    cluster_count: int = 0
