"""LLM synthesis adapter for generated Working Memory artifacts."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from lerim.agents.mlflow_observability import handle_mlflow_event_stream, mlflow_span
from lerim.agents.model_settings import LOW_VARIANCE_AGENT_MODEL_SETTINGS
from lerim.working_memory import MemoryLine, MemorySection, WorkingMemoryDraft


WORKING_MEMORY_SYSTEM_PROMPT = """\
<role>
You write Lerim Working Memory for coding agents.
</role>

<rules>
- Use only the candidate records supplied in the prompt.
- Prefer durable records: decision, preference, constraint, fact, reference.
- Use episode records only for recent flow context or when no durable record covers the topic.
- Keep the result compact enough for a roughly 50-line markdown file.
- Every line must include at least one exact record_id from the supplied candidates.
- Do not quote long evidence. Compress and preserve the practical instruction or fact.
- Do not invent current repository state beyond the stored records.
</rules>
"""


class WorkingMemoryLineOutput(BaseModel):
    """One cited line in the generated Working Memory draft."""

    text: str = Field(description="Compact memory statement without citations")
    record_ids: list[str] = Field(description="Exact source record IDs")


class WorkingMemorySectionOutput(BaseModel):
    """One named section in the generated Working Memory draft."""

    title: str
    lines: list[WorkingMemoryLineOutput]


class WorkingMemoryOutput(BaseModel):
    """Structured output returned by the Working Memory synthesis agent."""

    summary: list[WorkingMemoryLineOutput]
    sections: list[WorkingMemorySectionOutput]


def _candidate_for_prompt(record: dict[str, Any]) -> dict[str, Any]:
    """Return the compact candidate fields shown to the model."""
    return {
        "record_id": record.get("record_id"),
        "kind": record.get("kind"),
        "title": record.get("title"),
        "body": record.get("body"),
        "decision": record.get("decision"),
        "why": record.get("why"),
        "user_intent": record.get("user_intent"),
        "what_happened": record.get("what_happened"),
        "outcomes": record.get("outcomes"),
        "updated_at": record.get("updated_at"),
    }


def build_working_memory_agent(model: Model) -> Agent[None, WorkingMemoryOutput]:
    """Build the Working Memory synthesis agent."""
    return Agent(
        model,
        output_type=WorkingMemoryOutput,
        system_prompt=WORKING_MEMORY_SYSTEM_PROMPT,
        model_settings=LOW_VARIANCE_AGENT_MODEL_SETTINGS,
        retries=3,
        output_retries=2,
    )


def run_working_memory_synthesis(
    *,
    model: Model,
    candidates: list[dict[str, Any]],
    request_limit: int = 8,
    return_messages: bool = False,
) -> WorkingMemoryDraft | tuple[WorkingMemoryDraft, list[Any]]:
    """Run LLM synthesis over bounded candidate records."""
    agent = build_working_memory_agent(model)
    compact_candidates = [_candidate_for_prompt(record) for record in candidates]
    prompt = (
        "Create a compact Working Memory from these candidate records.\n"
        "Return only cited lines using exact record_id values.\n\n"
        f"Candidate records JSON:\n{json.dumps(compact_candidates, ensure_ascii=True)}"
    )
    with mlflow_span(
        "lerim.agent.working_memory",
        span_type="AGENT",
        attributes={"lerim.agent_name": "working_memory"},
        inputs={"candidate_count": len(compact_candidates)},
    ):
        result = agent.run_sync(
            prompt,
            usage_limits=UsageLimits(request_limit=max(1, int(request_limit))),
            event_stream_handler=handle_mlflow_event_stream,
        )
    draft = WorkingMemoryDraft(
        summary=tuple(
            MemoryLine(text=line.text, record_ids=tuple(line.record_ids))
            for line in result.output.summary
        ),
        sections=tuple(
            MemorySection(
                title=section.title,
                lines=tuple(
                    MemoryLine(text=line.text, record_ids=tuple(line.record_ids))
                    for line in section.lines
                ),
            )
            for section in result.output.sections
        ),
    )
    if return_messages:
        return draft, list(result.all_messages())
    return draft
