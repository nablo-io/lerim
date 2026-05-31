"""DSPy signatures for Working Memory compilation."""

from __future__ import annotations

from lerim.agents.dspy_compat import dspy
from lerim.agents.working_memory.schemas import WorkingMemoryDraftOutput


class CompileWorkingMemory(dspy.Signature):
    """You are Lerim's Working Memory compiler. Compile a short-term continuation handoff from recent persisted context changes.
    Return only structured output. Do not include <think> tags, hidden reasoning, markdown, or prose.

    Rules:
    - Use only the supplied records and generation context.
    - Working Memory is the short-term companion to Context Brief.
    - Context Brief is long-term durable memory. Working Memory is the last couple hours of movement.
    - Explain what changed recently and where to resume if the next user prompt continues the same work.
    - Keep the artifact simpler than Context Brief.
    - Do not invent next actions. The next user prompt decides the task.
    - Every non-empty line must include at least one exact record_id copied from the supplied records.
    - Put record IDs only in record_ids, never in text.
    - Prefer current active records over superseded or archived records.
    - When recent records have operational roles such as procedure, gotcha, failure_mode, artifact, state_change, or eval_asset, include the role's practical implication in recent_changes or current_context when it affects continuation.
    - When a record was superseded, explain the current replacement rather than repeating the old record as truth.
    - Put the most useful resume point in start_here. If there is no clear resume point, leave start_here empty.
    - Include open_questions only when a recent record explicitly supports an unresolved question.
    - Select the useful short-term context. Do not enumerate every supplied record.
    - Maximum lines: summary 2, start_here 4, recent_changes 5, current_context 6, open_questions 3.
    """

    run_instruction: str = dspy.InputField(desc="RUN INSTRUCTION")
    project_json: str = dspy.InputField(desc="PROJECT JSON")
    recent_changes_json: str = dspy.InputField(desc="RECENT CHANGES JSON")
    current_records_json: str = dspy.InputField(desc="CURRENT RECORDS JSON")
    replacements_json: str = dspy.InputField(desc="SUPERSEDED RECORD REPLACEMENTS JSON")
    workspace_snapshot_json: str = dspy.InputField(desc="GENERATION-TIME WORKSPACE SNAPSHOT JSON")
    generation_context_json: str = dspy.InputField(desc="WORKING MEMORY GENERATION CONTEXT JSON")
    memory: WorkingMemoryDraftOutput = dspy.OutputField(
        desc="Short-term Working Memory handoff"
    )
