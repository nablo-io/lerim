"""DSPy Working Memory pipeline over recent context record versions."""

from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from lerim.agents.dspy_compat import dspy
from lerim.agents.model_helpers import call_model_step, prediction_payload
from lerim.agents.model_runtime import ModelRuntime, build_model_runtime
from lerim.agents.working_memory.schemas import WorkingMemoryDraftOutput
from lerim.agents.working_memory.signatures import CompileWorkingMemory
from lerim.config.settings import Config
from lerim.context import ContextStore
from lerim.context_brief import ContextBriefProject
from lerim.working_memory import (
    WORKING_MEMORY_RECORD_LIMIT,
    WORKING_MEMORY_VERSION_LIMIT,
    WORKING_MEMORY_WINDOW_HOURS,
    WorkingMemoryData,
    compact_text,
    current_record_status,
    git_output,
    is_current_record,
    load_working_memory_data,
    summarize_git_status,
    truncate_working_memory_lines,
    working_memory_record_ids,
)

RUN_INSTRUCTION = (
    "Compile a concise short-term Working Memory handoff from recent persisted "
    "context changes. Keep it useful for continuation, cite exact record IDs, "
    "and do not invent tasks or live workspace state."
)

SECTION_LIMITS = {
    "summary": 2,
    "start_here": 4,
    "recent_changes": 5,
    "current_context": 6,
    "open_questions": 3,
}


class WorkingMemoryPipeline(dspy.Module):
    """Load recent context changes, compile a handoff, and render Working Memory."""

    def __init__(
        self,
        *,
        store: ContextStore,
        project: ContextBriefProject,
        config: Config,
        generated_at: str,
        window_started_at: str,
        previous_generated_at: str | None,
        generation_trigger: str,
        db_records_changed_since_previous: int,
        current_file: Path | None = None,
        run_folder: Path | None = None,
        runtime: ModelRuntime | None = None,
        compile_step: Any | None = None,
    ) -> None:
        """Create the Working Memory pipeline."""
        super().__init__()
        self.store = store
        self.project = project
        self.config = config
        self.generated_at = generated_at
        self.window_started_at = window_started_at
        self.previous_generated_at = previous_generated_at
        self.generation_trigger = generation_trigger
        self.db_records_changed_since_previous = db_records_changed_since_previous
        self.current_file = current_file
        self.run_folder = run_folder
        self.runtime = runtime
        self.adapter = dspy.JSONAdapter()
        self.uses_real_model = compile_step is None
        self.compile_step = compile_step or dspy.Predict(CompileWorkingMemory)

    def forward(self) -> dict[str, Any]:
        """Run the Working Memory workflow and return markdown plus metadata."""
        data = load_working_memory_data(
            self.store,
            project_id=self.project.identity.project_id,
            since=self.window_started_at,
        )
        record_ids = working_memory_record_ids(data)
        snapshot = workspace_snapshot(self.project.identity.repo_path)
        events: list[dict[str, Any]] = [
            {
                "kind": "load_recent_context",
                "recent_versions": len(data.versions),
                "current_records": len(data.current_records),
                "replacement_count": len(data.replacements),
            }
        ]
        if record_ids:
            with self.model_context():
                output, retry_events, attempts = call_model_step(
                    lambda instruction: self.compile_step(
                        run_instruction=instruction,
                        project_json=json.dumps(project_payload(self.project), ensure_ascii=True),
                        recent_changes_json=json.dumps(recent_changes_payload(data), ensure_ascii=True),
                        current_records_json=json.dumps(record_payloads(data.current_records), ensure_ascii=True),
                        replacements_json=json.dumps(replacement_payloads(data), ensure_ascii=True),
                        workspace_snapshot_json=json.dumps(snapshot, ensure_ascii=True),
                        generation_context_json=json.dumps(self.generation_context(), ensure_ascii=True),
                    ),
                    stage="compile_working_memory",
                    progress=False,
                    progress_label="working-memory",
                    run_instruction=RUN_INSTRUCTION,
                    validate_result=lambda result: validate_memory_output(
                        result,
                        valid_record_ids=set(record_ids),
                    ),
                    make_observation=model_event,
                    semantic_retry_content=memory_retry_content,
                    validation_retry_target="complete corrected Working Memory",
                )
            events.extend(retry_events)
            draft = draft_from_output(output)
            events.append(
                {
                    "kind": "model_step",
                    "stage": "compile_working_memory",
                    "attempts": attempts,
                    "record_count": len(record_ids),
                }
            )
        else:
            draft = WorkingMemoryDraftOutput(
                summary=[
                    {
                        "text": "No persisted context records changed inside this short-term window.",
                        "record_ids": [],
                    }
                ]
            )
        markdown = render_working_memory_markdown(
            project=self.project,
            generated_at=self.generated_at,
            window_started_at=self.window_started_at,
            previous_generated_at=self.previous_generated_at,
            generation_trigger=self.generation_trigger,
            db_records_changed_since_previous=self.db_records_changed_since_previous,
            data=data,
            draft=draft,
            workspace_snapshot=snapshot,
            current_file=self.current_file,
            run_folder=self.run_folder,
        )
        return {
            "markdown": markdown,
            "data": data,
            "record_ids": record_ids,
            "events": events,
            "done": True,
        }

    def model_context(self):
        """Return a DSPy context only when real predictors need a configured LM."""
        if not self.uses_real_model:
            return nullcontext()
        if self.runtime is None:
            self.runtime = build_model_runtime(config=self.config)
        return dspy.context(lm=self.runtime.lm, adapter=self.adapter)

    def generation_context(self) -> dict[str, Any]:
        """Return generation metadata shown to the compiler."""
        return {
            "generated_at": self.generated_at,
            "window_started_at": self.window_started_at,
            "window_hours": WORKING_MEMORY_WINDOW_HOURS,
            "previous_generated_at": self.previous_generated_at,
            "generation_trigger": self.generation_trigger,
            "db_records_changed_since_previous": self.db_records_changed_since_previous,
        }


def workspace_snapshot(repo_path: Path) -> dict[str, Any]:
    """Return bounded generation-time git state for one repo path."""
    branch = git_output(repo_path, "branch", "--show-current")
    status = git_output(repo_path, "status", "--short")
    last_commit = git_output(repo_path, "log", "-1", "--oneline")
    groups = summarize_git_status(status) if status else {}
    return {
        "repo_path": str(repo_path),
        "branch": branch,
        "last_commit": last_commit,
        "dirty_file_count": sum(groups.values()),
        "dirty_groups": groups,
        "is_git_repo": any((branch, status, last_commit)),
        "note": "Generation-time snapshot only; rerun git status before editing.",
    }


def project_payload(project: ContextBriefProject) -> dict[str, Any]:
    """Return compact project metadata for the compiler."""
    return {
        "name": project.name,
        "project_id": project.identity.project_id,
        "repo_path": str(project.identity.repo_path),
    }


def record_payloads(records: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    """Return compact record fields for model input."""
    return [record_payload(record) for record in records]


def record_payload(record: dict[str, Any]) -> dict[str, Any]:
    """Return the useful stable fields for one context record."""
    return {
        "record_id": record.get("record_id"),
        "kind": record.get("kind"),
        "record_role": record.get("record_role"),
        "role_payload": record.get("role_payload"),
        "status": record.get("status"),
        "title": record.get("title"),
        "body": record.get("body"),
        "decision": record.get("decision"),
        "why": record.get("why"),
        "user_intent": record.get("user_intent"),
        "what_happened": record.get("what_happened"),
        "outcomes": record.get("outcomes"),
        "updated_at": record.get("updated_at"),
        "superseded_by_record_id": record.get("superseded_by_record_id"),
    }


def recent_changes_payload(data: WorkingMemoryData) -> list[dict[str, Any]]:
    """Return recent version rows enriched with current-status hints."""
    changed_by_id = {str(record.get("record_id") or ""): record for record in data.changed_records}
    replacement_by_id = {old_id: record for old_id, record in data.replacements}
    rows: list[dict[str, Any]] = []
    for version in data.versions[:WORKING_MEMORY_VERSION_LIMIT]:
        record_id = str(version.get("record_id") or "")
        record = changed_by_id.get(record_id, version)
        replacement = replacement_by_id.get(record_id)
        rows.append(
            {
                "record_id": record_id,
                "kind": version.get("kind") or record.get("kind"),
                "record_role": version.get("record_role") or record.get("record_role"),
                "change_kind": version.get("change_kind"),
                "changed_at": version.get("changed_at"),
                "title": record.get("title"),
                "current_status": current_record_status(record, replacement),
            }
        )
    return rows


def replacement_payloads(data: WorkingMemoryData) -> list[dict[str, Any]]:
    """Return old-to-current replacement pairs for superseded records."""
    changed_by_id = {str(record.get("record_id") or ""): record for record in data.changed_records}
    rows: list[dict[str, Any]] = []
    for old_id, replacement in data.replacements:
        old_record = changed_by_id.get(old_id, {"record_id": old_id})
        rows.append(
            {
                "old_record": record_payload(old_record),
                "replacement": record_payload(replacement),
            }
        )
    return rows


def draft_from_output(output: Any) -> WorkingMemoryDraftOutput:
    """Build a Working Memory draft from DSPy or test-double output."""
    payload = prediction_payload(output, output_field="memory")
    if isinstance(payload.get("memory"), dict):
        payload = payload["memory"]
    return WorkingMemoryDraftOutput.model_validate(payload)


def validate_memory_output(output: Any, *, valid_record_ids: set[str]) -> str | None:
    """Return a validation error when model output cites unsupported records."""
    try:
        draft = draft_from_output(output)
    except Exception as exc:
        return f"invalid_schema:{type(exc).__name__}:{exc}"
    for section_name in SECTION_LIMITS:
        lines = getattr(draft, section_name)
        if len(lines) > SECTION_LIMITS[section_name]:
            return f"{section_name}_too_long"
        for line in lines:
            text = line.text.strip()
            record_ids = [record_id.strip() for record_id in line.record_ids if record_id.strip()]
            if text and not record_ids:
                return f"{section_name}_line_missing_record_ids"
            invalid = sorted(set(record_ids) - valid_record_ids)
            if invalid:
                return f"{section_name}_invalid_record_ids:{','.join(invalid)}"
    return None


def render_working_memory_markdown(
    *,
    project: ContextBriefProject,
    generated_at: str,
    window_started_at: str,
    previous_generated_at: str | None,
    generation_trigger: str,
    db_records_changed_since_previous: int,
    data: WorkingMemoryData,
    draft: WorkingMemoryDraftOutput,
    workspace_snapshot: dict[str, Any],
    current_file: Path | None,
    run_folder: Path | None,
) -> str:
    """Render structured Working Memory output as bounded markdown."""
    record_ids = working_memory_record_ids(data)
    lines = [
        "# Working Memory",
        "",
        "> Short-term continuation context generated from recent SQLite record versions.",
        "> Use Context Brief for long-term memory. Use this only to understand what changed recently.",
        "> The next user prompt decides the task; this is not a todo list.",
        "",
        f"- Project: `{project.name}`",
        f"- Generated: `{generated_at}`",
        f"- Recent window: {WORKING_MEMORY_WINDOW_HOURS}h",
        "",
    ]
    append_summary_section(lines, draft=draft, data=data)
    append_start_here_section(lines, draft=draft)
    append_draft_section(lines, "Recent Changes", draft.recent_changes)
    append_draft_section(lines, "Current Context", draft.current_context)
    append_historical_records(lines, data=data)
    append_draft_section(lines, "Open Questions", draft.open_questions)
    append_workspace_section(lines, snapshot=workspace_snapshot)
    append_sources(lines, data=data, cited_record_ids=record_ids)
    append_technical_details(
        lines,
        project=project,
        generated_at=generated_at,
        window_started_at=window_started_at,
        previous_generated_at=previous_generated_at,
        generation_trigger=generation_trigger,
        db_records_changed_since_previous=db_records_changed_since_previous,
        data=data,
        record_ids=record_ids,
        current_file=current_file,
        run_folder=run_folder,
    )
    return "\n".join(truncate_working_memory_lines(lines)).rstrip() + "\n"


def append_summary_section(
    lines: list[str],
    *,
    draft: WorkingMemoryDraftOutput,
    data: WorkingMemoryData,
) -> None:
    """Append the high-level short-term state."""
    lines.extend(["## Summary", ""])
    if draft.summary:
        lines.extend(format_draft_lines(draft.summary))
    elif data.versions:
        lines.append(
            f"- Recent persisted context changed {len(data.versions)} time(s) across {len(data.changed_records)} record(s)."
        )
    else:
        lines.append("- No persisted context records changed inside this short-term window.")
    if data.versions:
        lines.append("- Treat superseded or archived records as history; use current records or replacements as the truth.")
    lines.append("- This is continuation context only; do not treat it as a task list unless the user asks to continue this work.")


def append_start_here_section(
    lines: list[str],
    *,
    draft: WorkingMemoryDraftOutput,
) -> None:
    """Append resume guidance in the same spirit as Context Brief."""
    lines.extend(["", "## Start Here", ""])
    if draft.start_here:
        lines.extend(format_draft_lines(draft.start_here))
        lines.append("- Otherwise, let the next user prompt define the task.")
        return
    lines.append("- No clear continuation point was inferred from recent records.")
    lines.append("- Use this artifact only as recent context.")


def append_workspace_section(lines: list[str], *, snapshot: dict[str, Any]) -> None:
    """Append deterministic generation-time workspace state."""
    if not snapshot.get("is_git_repo"):
        return
    lines.extend(["", "## Workspace Snapshot", ""])
    lines.append(f"- Repo: `{snapshot.get('repo_path')}`")
    if snapshot.get("branch"):
        lines.append(f"- Branch at generation: `{snapshot['branch']}`")
    if snapshot.get("last_commit"):
        lines.append(f"- Last commit at generation: `{snapshot['last_commit']}`")
    dirty_count = int(snapshot.get("dirty_file_count") or 0)
    if dirty_count:
        lines.append(f"- Dirty files at generation: {dirty_count}")
        for group, count in sorted((snapshot.get("dirty_groups") or {}).items()):
            lines.append(f"  - `{group}`: {count}")
        lines.append("- This snapshot can go stale; run `git status --short` before editing.")
    else:
        lines.append("- Git status was clean at generation.")


def append_draft_section(
    lines: list[str],
    title: str,
    draft_lines: list[Any],
) -> None:
    """Append one model-authored section when it contains supported lines."""
    if not draft_lines:
        return
    lines.extend(["", f"## {title}", ""])
    lines.extend(format_draft_lines(draft_lines))


def append_historical_records(lines: list[str], *, data: WorkingMemoryData) -> None:
    """Append recently changed records that are no longer the current truth."""
    replacement_by_id = {old_id: record for old_id, record in data.replacements}
    historical = tuple(record for record in data.changed_records if not is_current_record(record))
    if not historical:
        return
    lines.extend(["", "## Recently Replaced / Archived", ""])
    for record in historical[:WORKING_MEMORY_RECORD_LIMIT]:
        record_id = str(record.get("record_id") or "")
        replacement = replacement_by_id.get(record_id)
        title = compact_text(record.get("title"), limit=90)
        if replacement is not None:
            replacement_title = compact_text(replacement.get("title"), limit=140)
            replacement_id = str(replacement.get("record_id") or "")
            lines.append(
                f"- `{title}` (`{record_id}`) was superseded; use `{replacement_title}` (`{replacement_id}`)."
            )
        else:
            status = str(record.get("status") or "record")
            lines.append(f"- `{title}` (`{record_id}`) is `{status}` and should be treated as historical.")


def append_sources(
    lines: list[str],
    *,
    data: WorkingMemoryData,
    cited_record_ids: tuple[str, ...],
) -> None:
    """Append compact record sources for the Working Memory artifact."""
    if not cited_record_ids:
        return
    by_id: dict[str, dict[str, Any]] = {}
    for record in (*data.changed_records, *data.current_records):
        record_id = str(record.get("record_id") or "")
        if record_id:
            by_id[record_id] = record
    lines.extend(["", "## Sources", ""])
    for record_id in cited_record_ids[:WORKING_MEMORY_RECORD_LIMIT]:
        record = by_id.get(record_id)
        if record is None:
            lines.append(f"- `{record_id}`")
            continue
        title = compact_text(record.get("title"), limit=100)
        kind = str(record.get("kind") or "record")
        role = str(record.get("record_role") or "general")
        role_text = f", role {role}" if role and role != "general" else ""
        updated_at = str(record.get("updated_at") or "")
        lines.append(f"- `{record_id}` ({kind}{role_text}, updated `{updated_at}`): {title}")
    if len(cited_record_ids) > WORKING_MEMORY_RECORD_LIMIT:
        remaining = len(cited_record_ids) - WORKING_MEMORY_RECORD_LIMIT
        lines.append(f"- {remaining} more cited record(s); run `lerim query versions list --scope project --json` for deeper detail.")


def append_technical_details(
    lines: list[str],
    *,
    project: ContextBriefProject,
    generated_at: str,
    window_started_at: str,
    previous_generated_at: str | None,
    generation_trigger: str,
    db_records_changed_since_previous: int,
    data: WorkingMemoryData,
    record_ids: tuple[str, ...],
    current_file: Path | None,
    run_folder: Path | None,
) -> None:
    """Append machine-oriented generation metadata after the useful handoff."""
    lines.extend(["", "## Technical Details", ""])
    lines.extend(
        [
            f"- Project ID: `{project.identity.project_id}`",
            f"- Generated: `{generated_at}`",
            f"- Window start: `{window_started_at}`",
            f"- Window hours: {WORKING_MEMORY_WINDOW_HOURS}",
            f"- Previous generation: `{previous_generated_at or 'none'}`",
            f"- Generation trigger: `{generation_trigger}`",
            f"- Recent versions considered: {len(data.versions)}",
            f"- Records cited: {len(record_ids)}",
            f"- DB records changed before this generation: {db_records_changed_since_previous}",
            "- Live DB freshness: `lerim working-memory status`",
            "- Refresh: `lerim working-memory refresh`",
        ]
    )
    if current_file is not None:
        lines.append(f"- Current file: `{current_file}`")
    if run_folder is not None:
        lines.append(f"- Run folder: `{run_folder}`")


def format_draft_lines(draft_lines: list[Any]) -> list[str]:
    """Return markdown lines with normalized record citations."""
    lines: list[str] = []
    for line in draft_lines:
        payload = prediction_payload(line)
        text = compact_text(payload.get("text"), limit=620)
        record_ids = [str(item) for item in payload.get("record_ids") or [] if str(item).strip()]
        if not text:
            continue
        citation = f" [{', '.join(record_ids)}]" if record_ids else ""
        lines.append(f"- {text}{citation}")
    return lines


def model_event(action: str, ok: bool, content: str, args: dict[str, Any]) -> dict[str, Any]:
    """Return a compact pipeline event."""
    return {"kind": action, "ok": ok, "content": content, **args}


def memory_retry_content(validation_error: str) -> str:
    """Return model-visible feedback for an invalid Working Memory draft."""
    return (
        "Working Memory output was unsupported or too broad. "
        "Return cited short-term handoff lines using only exact supplied record IDs. "
        f"Validation error: {validation_error}"
    )
