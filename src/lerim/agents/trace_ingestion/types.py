"""Public types returned by the trace-ingestion flow."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TraceIngestionEvent(BaseModel):
    """One structured event emitted by the trace-ingestion graph."""

    action: str
    ok: bool = True
    content: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    done: bool = False
    completion_summary: str = ""


class TraceIngestionResult(BaseModel):
    """Structured output for the trace-ingestion flow."""

    completion_summary: str = Field(description="Short plain-text completion summary")


class TraceIngestionRunDetails(BaseModel):
    """Structured trace for one trace-ingestion run."""

    events: list[TraceIngestionEvent] = Field(default_factory=list)
    llm_calls: int = 0
    done: bool = False
    context_db_path: str
    project_id: str | None = None
    scope_type: str = "project"
    scope_id: str = ""
    session_id: str
    model_name: str
    trace_total_lines: int = 0
