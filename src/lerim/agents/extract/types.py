"""Public types returned by the extract flow."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ExtractionEvent(BaseModel):
    """One structured event emitted by the extract graph."""

    action: str
    ok: bool = True
    content: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    done: bool = False
    completion_summary: str = ""


class ExtractionResult(BaseModel):
    """Structured output for the extract flow."""

    completion_summary: str = Field(description="Short plain-text completion summary")


class ExtractionRunDetails(BaseModel):
    """Structured trace for one extract run."""

    events: list[ExtractionEvent] = Field(default_factory=list)
    llm_calls: int = 0
    done: bool = False
    context_db_path: str
    project_id: str
    session_id: str
    model_name: str
    trace_total_lines: int = 0
