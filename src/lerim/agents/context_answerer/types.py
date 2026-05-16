"""Public types for the context-answerer agent."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ContextAnswerResult(BaseModel):
    """Structured output for the context-answerer flow."""

    answer: str = Field(description="Answer text with record citations when available")
