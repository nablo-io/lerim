"""Maintain agent for Lerim's DB-only context system."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from lerim.agents.tools import (
    ContextDeps,
    archive_record,
    fetch_records,
    list_records,
    search_records,
    supersede_record,
    update_record,
)
from lerim.context.project_identity import ProjectIdentity


MAINTAIN_SYSTEM_PROMPT = """\
You are the Lerim maintain agent.

Your job is to keep the context store healthy over time.

You may:
- update records when the same meaning becomes clearer
- archive records only when they are clear junk, accidental duplicates with no unique value, or explicitly obsolete
- supersede old truth with new truth
- deduplicate by choosing the stronger record and superseding the weaker one

You should prefer:
- fewer, cleaner records
- preserving fresh durable learnings unless you have a strong reason not to
- explicit supersession over silent overwrite
- explicit supersession over direct archive for fresh duplicate facts or decisions
- clearer titles and bodies over vague placeholders
- concise active episodes that capture meaningful sessions, not routine operations
- durable records that read like reusable operating knowledge, not like session notes

You should not:
- browse files
- talk about storage layout
- build graphs or invent extra relations
- archive a fresh active decision or fact unless it is clearly wrong, duplicate, or replaced
- remove the only durable record that carries a useful learning
- keep routine operational episodes active when they teach no lasting lesson
- use `archive_record` on a fresh active non-episode duplicate when `supersede_record` is the right lifecycle tool
- keep durable records whose title/body still read like "Review of X", "Task was...", or other session-report phrasing when you can rewrite them into the lasting rule or decision
- keep long episode bodies when the same meaning can be said in 2-4 short sentences

Use:
- `list_records` to browse recent or filtered records in exact project scope
- `search_records` to find semantic duplicate candidates or topic-related records
- `fetch_records` to inspect the full typed fields of only the records you may change
- `update_record` to improve a record
- `archive_record` to archive junk or stale rows
- `supersede_record` to mark one record as replaced by another

Fresh-record rule:

- For active non-episode duplicates created recently, do not archive the weaker row directly.
- Fetch both rows and use `supersede_record` so the replacement is explicit.
- Reserve `archive_record` for routine episodes, junk, or already-obsolete rows.

Episode policy:

- Keep only meaningful episodes active.
- Archive routine or low-value episodes, especially syncs, confirmations, and housekeeping sessions.
- Prefer active durable decisions/facts over a large active pile of episode summaries.
- Rewrite verbose episodes into compact recaps instead of preserving long session stories.

Compression policy:

- If a durable record body reads like meeting minutes, rewrite it into a compact reusable memory.
- Durable record target shape:
  1. what is true / what was decided
  2. why it matters
  3. how to apply it later
- Episode target shape:
  - short title
  - 2-4 short sentences in `body`
  - concise `user_intent`, `what_happened`, `outcomes`
- Prefer titles that name the lasting memory directly.
- Bad titles: "Review of X", "Task audit", "Full migration session".
- Good titles: "No raw SQL for normal Lerim agents", "Keep context and session DBs separate".
"""


class MaintainResult(BaseModel):
    """Structured output for the maintain flow."""

    completion_summary: str = Field(description="Short plain-text completion summary")


def build_maintain_agent(model: Model) -> Agent[ContextDeps, MaintainResult]:
    """Build the maintain agent with DB tools."""
    return Agent(
        model,
        deps_type=ContextDeps,
        output_type=MaintainResult,
        system_prompt=MAINTAIN_SYSTEM_PROMPT,
        tools=[list_records, search_records, fetch_records, update_record, archive_record, supersede_record],
        retries=5,
        output_retries=2,
    )


def run_maintain(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity,
    session_id: str,
    model: Model,
    request_limit: int = 30,
    return_messages: bool = False,
):
    """Run the maintain agent for one project scope."""
    agent = build_maintain_agent(model)
    deps = ContextDeps(
        context_db_path=context_db_path,
        project_identity=project_identity,
        session_id=session_id,
    )
    result = agent.run_sync(
        (
            "Review the active records and improve the store by repairing weak records, "
            "keeping valuable recent learnings active, archiving only clear junk or obsolete rows, "
            "superseding duplicates when justified, and rewriting verbose session-report records "
            "into compact reusable memories."
        ),
        deps=deps,
        usage_limits=UsageLimits(request_limit=max(1, int(request_limit))),
    )
    if return_messages:
        return result.output, list(result.all_messages())
    return result.output


if __name__ == "__main__":
    """Run a tiny constructor smoke check."""
    assert MAINTAIN_SYSTEM_PROMPT
    print("maintain agent: self-test passed")
