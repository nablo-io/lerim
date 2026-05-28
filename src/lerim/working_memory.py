"""Short-term continuation handoff derived from recent context record versions."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from lerim.config.settings import Config
from lerim.context import ContextStore
from lerim.context_brief import (
    ContextBriefProject,
    age_seconds_since,
    count_changed_records_since,
    count_missing_included_records,
    human_age,
    read_manifest,
)

WORKING_MEMORY_FILENAME = "WORKING_MEMORY.md"
WORKING_MEMORY_MANIFEST_FILENAME = "WORKING_MEMORY.manifest.json"
WORKING_MEMORY_OPERATION = "working-memory"
WORKING_MEMORY_WINDOW_HOURS = 6
WORKING_MEMORY_VERSION_LIMIT = 40
WORKING_MEMORY_RECORD_LIMIT = 14
WORKING_MEMORY_LINE_LIMIT = 130
KIND_PRIORITY = {
    "decision": 0,
    "preference": 1,
    "constraint": 2,
    "fact": 3,
    "episode": 4,
}


@dataclass(frozen=True)
class WorkingMemoryPaths:
    """Stable current Working Memory artifact paths for one project."""

    current_dir: Path
    current_file: Path
    current_manifest: Path


@dataclass(frozen=True)
class WorkingMemoryData:
    """Recent version rows plus the records agents should treat as current."""

    versions: tuple[dict[str, Any], ...]
    changed_records: tuple[dict[str, Any], ...]
    current_records: tuple[dict[str, Any], ...]
    replacements: tuple[tuple[str, dict[str, Any]], ...]


@dataclass(frozen=True)
class WorkingMemoryStatus:
    """Freshness and availability metadata for one project's Working Memory."""

    availability: str
    project: str
    project_id: str
    repo_path: str
    generated_at: str | None
    age_seconds: int | None
    window_hours: int
    window_started_at: str | None
    recent_versions_considered: int
    records_included: int
    records_changed_since_generation: int
    records_missing_since_generation: int
    current_file: str
    current_manifest: str
    latest_run_folder: str | None
    suggested_action: str


def working_memory_paths(config: Config, project_id: str) -> WorkingMemoryPaths:
    """Return stable current Working Memory paths for a project."""
    current_dir = config.global_data_dir / "workspace" / "current" / project_id
    return WorkingMemoryPaths(
        current_dir=current_dir,
        current_file=current_dir / WORKING_MEMORY_FILENAME,
        current_manifest=current_dir / WORKING_MEMORY_MANIFEST_FILENAME,
    )


def working_memory_window_start(
    *,
    now: datetime | None = None,
    window_hours: int = WORKING_MEMORY_WINDOW_HOURS,
) -> str:
    """Return the lower timestamp bound for the recent Working Memory window."""
    effective_now = now or datetime.now(timezone.utc)
    if effective_now.tzinfo is None:
        effective_now = effective_now.replace(tzinfo=timezone.utc)
    start = effective_now.astimezone(timezone.utc) - timedelta(hours=max(1, int(window_hours)))
    return start.isoformat()


def load_recent_record_versions(
    store: ContextStore,
    *,
    project_id: str,
    since: str,
    limit: int = WORKING_MEMORY_VERSION_LIMIT,
) -> list[dict[str, Any]]:
    """Load recent record-version rows for one project."""
    payload = store.query(
        entity="versions",
        mode="list",
        project_ids=[project_id],
        updated_since=since,
        order_by="updated_at",
        limit=max(1, int(limit)),
        include_total=False,
    )
    return list(payload.get("rows") or [])


def load_working_memory_data(
    store: ContextStore,
    *,
    project_id: str,
    since: str,
    limit: int = WORKING_MEMORY_VERSION_LIMIT,
) -> WorkingMemoryData:
    """Load recent version rows and resolve their current records."""
    versions = load_recent_record_versions(
        store,
        project_id=project_id,
        since=since,
        limit=limit,
    )
    changed_by_id: dict[str, dict[str, Any]] = {}
    current_by_id: dict[str, dict[str, Any]] = {}
    replacement_by_old_id: dict[str, dict[str, Any]] = {}
    for version in versions:
        record_id = str(version.get("record_id") or "").strip()
        if not record_id:
            continue
        record = store.fetch_record(
            record_id,
            project_ids=[project_id],
            include_versions=False,
        )
        if record is None:
            continue
        changed_by_id.setdefault(record_id, record)
        if is_current_record(record):
            current_by_id.setdefault(record_id, record)
        replacement_id = str(record.get("superseded_by_record_id") or "").strip()
        if replacement_id:
            replacement = store.fetch_record(
                replacement_id,
                project_ids=[project_id],
                include_versions=False,
            )
            if replacement is not None:
                replacement_by_old_id[record_id] = replacement
                if is_current_record(replacement):
                    current_by_id.setdefault(replacement_id, replacement)
    return WorkingMemoryData(
        versions=tuple(versions),
        changed_records=sort_records(changed_by_id.values()),
        current_records=sort_records(current_by_id.values()),
        replacements=tuple(replacement_by_old_id.items()),
    )


def is_current_record(record: dict[str, Any]) -> bool:
    """Return whether a fetched record is the active version agents should use."""
    return (
        str(record.get("status") or "") == "active"
        and not str(record.get("valid_until") or "").strip()
        and not str(record.get("superseded_by_record_id") or "").strip()
    )


def sort_records(records: Any) -> tuple[dict[str, Any], ...]:
    """Return records ordered by durable kind priority and recency."""
    rows = list(records)
    rows.sort(
        key=lambda row: (
            str(row.get("updated_at") or ""),
            str(row.get("record_id") or ""),
        ),
        reverse=True,
    )
    rows.sort(key=lambda row: KIND_PRIORITY.get(str(row.get("kind") or ""), 50))
    return tuple(rows)


def working_memory_record_ids(data: WorkingMemoryData) -> tuple[str, ...]:
    """Return unique record IDs cited by a Working Memory render."""
    ordered: list[str] = []
    seen: set[str] = set()
    for version in data.versions:
        _append_record_id(ordered, seen, version.get("record_id"))
    for record in data.changed_records:
        _append_record_id(ordered, seen, record.get("record_id"))
    for old_record_id, replacement in data.replacements:
        _append_record_id(ordered, seen, old_record_id)
        _append_record_id(ordered, seen, replacement.get("record_id"))
    for record in data.current_records:
        _append_record_id(ordered, seen, record.get("record_id"))
    return tuple(ordered)


def render_working_memory_markdown(
    *,
    project: ContextBriefProject,
    generated_at: str,
    window_started_at: str,
    previous_generated_at: str | None,
    generation_trigger: str,
    db_records_changed_since_previous: int,
    data: WorkingMemoryData,
    current_file: Path | None = None,
    run_folder: Path | None = None,
) -> str:
    """Render Working Memory markdown from recent versions and current records."""
    record_ids = working_memory_record_ids(data)
    lines = [
        "# Working Memory",
        "",
        "> Short-term Lerim continuation handoff. This markdown is derived from recent SQLite record versions, not the source of truth.",
        "> Use this only to understand what was recently done or changed. The next user prompt decides what to do next.",
        "> Use Context Brief for durable long-term context. Check git status, tests, and the current chat for live workspace state.",
        "",
        f"- Project: `{project.name}`",
        f"- Generated: `{generated_at}`",
        f"- Recent window: {WORKING_MEMORY_WINDOW_HOURS}h",
        "",
    ]

    lines.extend(["## Current State", ""])
    if data.versions:
        lines.append(
            f"- Recent persisted context changed {len(data.versions)} time(s) across {len(data.changed_records)} record(s)."
        )
        lines.append(
            "- Treat superseded or archived records as history; use the current final records below for decisions and constraints."
        )
        lines.append(
            "- This is continuation context only; do not treat it as a task list unless the user asks to continue this work."
        )
    else:
        lines.append(
            "- No persisted context records changed inside this short-term window. There is no continuation-specific handoff."
        )

    append_workspace_snapshot(lines, repo_path=project.identity.repo_path)
    append_recent_outcomes(lines, data=data)
    append_changed_context(lines, data=data)
    append_current_records_section(
        lines,
        title="Current Final Decisions",
        records=records_of_kind(data.current_records, {"decision"}),
        limit=WORKING_MEMORY_RECORD_LIMIT,
    )
    append_current_records_section(
        lines,
        title="Current Constraints & Preferences",
        records=records_of_kind(data.current_records, {"constraint", "preference"}),
        limit=WORKING_MEMORY_RECORD_LIMIT,
    )
    append_current_records_section(
        lines,
        title="Current Project Facts",
        records=records_of_kind(data.current_records, {"fact"}),
        limit=WORKING_MEMORY_RECORD_LIMIT,
    )
    append_current_records_section(
        lines,
        title="Recent Episode Evidence",
        records=records_of_kind(data.current_records, {"episode"}),
        limit=8,
    )
    append_historical_records(lines, data=data)
    append_open_questions(lines, data=data)
    append_continuation_handoff(lines, data=data)
    append_working_memory_sources(lines, data=data, cited_record_ids=record_ids)
    append_working_memory_technical_details(
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


def append_workspace_snapshot(lines: list[str], *, repo_path: Path) -> None:
    """Append generated-time git state when the project path is a Git checkout."""
    branch = git_output(repo_path, "branch", "--show-current")
    status = git_output(repo_path, "status", "--short")
    last_commit = git_output(repo_path, "log", "-1", "--oneline")
    if branch is None and status is None and last_commit is None:
        return
    lines.extend(["", "## Workspace Snapshot", ""])
    lines.append(f"- Repo: `{repo_path}`")
    if branch:
        lines.append(f"- Branch at generation: `{branch}`")
    if last_commit:
        lines.append(f"- Last commit at generation: `{last_commit}`")
    if status:
        groups = summarize_git_status(status)
        lines.append(f"- Dirty files at generation: {sum(groups.values())}")
        for group, count in sorted(groups.items()):
            lines.append(f"  - `{group}`: {count}")
        lines.append("- This snapshot can go stale; run `git status --short` before editing.")
    else:
        lines.append("- Git status was clean at generation.")


def git_output(repo_path: Path, *args: str) -> str | None:
    """Return one git command's stdout for a repo path, or None when unavailable."""
    try:
        result = subprocess.run(
            ("git", "-C", str(repo_path), *args),
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    return text or None


def summarize_git_status(status: str) -> dict[str, int]:
    """Group porcelain status rows by the changed file's top-level area."""
    groups: dict[str, int] = {}
    for raw_line in status.splitlines():
        path = raw_line[2:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        group = path.split("/", 1)[0] if "/" in path else path
        if not group:
            group = "root"
        groups[group] = groups.get(group, 0) + 1
    return groups


def append_recent_outcomes(lines: list[str], *, data: WorkingMemoryData) -> None:
    """Append recent episode outcomes without turning them into generic tasks."""
    episodes = records_of_kind(data.current_records, {"episode"})
    lines.extend(["", "## Completed Recently", ""])
    if not episodes:
        lines.append("- No recent episode outcome records were captured in this window.")
        return
    for record in episodes[:8]:
        title = compact_text(record.get("title"), limit=140)
        detail = record_detail_text(record)
        record_id = str(record.get("record_id") or "")
        if detail:
            lines.append(f"- `{title}`: {detail} [{record_id}]")
        else:
            lines.append(f"- `{title}` [{record_id}]")


def append_changed_context(lines: list[str], *, data: WorkingMemoryData) -> None:
    """Append compact recent context changes in newest-first order."""
    if not data.versions:
        return
    replacement_by_id = {old_id: record for old_id, record in data.replacements}
    changed_by_id = {
        str(record.get("record_id") or ""): record for record in data.changed_records
    }
    lines.extend(["", "## Changed Context", ""])
    for version in data.versions[:WORKING_MEMORY_VERSION_LIMIT]:
        record_id = str(version.get("record_id") or "")
        record = changed_by_id.get(record_id, version)
        title = compact_text(record.get("title"), limit=90)
        kind = str(version.get("kind") or record.get("kind") or "record")
        change_kind = str(version.get("change_kind") or "change")
        changed_at = str(version.get("changed_at") or "")
        replacement = replacement_by_id.get(record_id)
        status = current_record_status(record, replacement)
        lines.append(
            f"- `{change_kind}` {kind} `{title}` (`{record_id}`, changed `{changed_at}`): {status}"
        )


def append_open_questions(lines: list[str], *, data: WorkingMemoryData) -> None:
    """Append continuation blockers only when recent records support them."""
    risk_records = [
        record
        for record in data.current_records
        if str(record.get("kind") or "") == "episode"
        and compact_text(record.get("user_intent"), limit=160)
    ]
    lines.extend(["", "## Open Questions", ""])
    if not risk_records:
        lines.append("- No unresolved continuation questions were inferred from recent records.")
        return
    for record in risk_records[:6]:
        record_id = str(record.get("record_id") or "")
        intent = compact_text(record.get("user_intent"), limit=180)
        title = compact_text(record.get("title"), limit=90)
        lines.append(f"- `{title}` captured user intent: {intent} [{record_id}]")


def append_continuation_handoff(lines: list[str], *, data: WorkingMemoryData) -> None:
    """Append evidence-backed handoff notes for a direct continuation chat."""
    handoff_lines: list[str] = []
    for old_record_id, replacement in data.replacements[:6]:
        replacement_id = str(replacement.get("record_id") or "")
        replacement_title = compact_text(replacement.get("title"), limit=140)
        handoff_lines.append(
            f"- If continuing related work, do not reuse `{old_record_id}`; use current replacement `{replacement_title}` (`{replacement_id}`)."
        )
    episode_records = records_of_kind(data.current_records, {"episode"})
    for record in episode_records[:4]:
        title = compact_text(record.get("title"), limit=140)
        detail = record_detail_text(record)
        record_id = str(record.get("record_id") or "")
        if detail:
            handoff_lines.append(
                f"- If the user asks to continue this thread, start from `{title}`: {detail} [{record_id}]"
            )
    lines.extend(["", "## If Continuing This Work", ""])
    if handoff_lines:
        lines.extend(handoff_lines[:8])
        lines.append("- Otherwise, let the next user prompt define the task; this section is not a todo list.")
        return
    lines.append("- No continuation-specific handoff was inferred from recent records.")
    lines.append("- Let the next user prompt define the task; use this artifact only as recent context.")


def append_current_records_section(
    lines: list[str],
    *,
    title: str,
    records: tuple[dict[str, Any], ...],
    limit: int,
) -> None:
    """Append a section of current records when records are present."""
    if not records:
        return
    lines.extend(["", f"## {title}", ""])
    for record in records[: max(1, int(limit))]:
        lines.append(format_current_record_line(record))


def append_historical_records(
    lines: list[str],
    *,
    data: WorkingMemoryData,
) -> None:
    """Append recently changed records that are no longer the current truth."""
    replacement_by_id = {old_id: record for old_id, record in data.replacements}
    historical = tuple(
        record for record in data.changed_records if not is_current_record(record)
    )
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


def append_working_memory_sources(
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
        updated_at = str(record.get("updated_at") or "")
        lines.append(f"- `{record_id}` ({kind}, updated `{updated_at}`): {title}")
    if len(cited_record_ids) > WORKING_MEMORY_RECORD_LIMIT:
        remaining = len(cited_record_ids) - WORKING_MEMORY_RECORD_LIMIT
        lines.append(
            f"- {remaining} more cited record(s); run `lerim query versions list --scope project --json` for deeper detail."
        )


def append_working_memory_technical_details(
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


def records_of_kind(
    records: tuple[dict[str, Any], ...],
    kinds: set[str],
) -> tuple[dict[str, Any], ...]:
    """Return records whose structured kind belongs in one section."""
    return tuple(record for record in records if str(record.get("kind") or "") in kinds)


def format_current_record_line(record: dict[str, Any]) -> str:
    """Render one current record as an actionable memory line."""
    record_id = str(record.get("record_id") or "")
    title = compact_text(record.get("title"), limit=90)
    detail = record_detail_text(record)
    if detail:
        return f"- `{title}`: {detail} [{record_id}]"
    return f"- `{title}` [{record_id}]"


def record_detail_text(record: dict[str, Any]) -> str:
    """Return the most useful structured detail for one current record."""
    kind = str(record.get("kind") or "")
    if kind == "decision":
        pieces = [
            compact_text(record.get("decision"), limit=420),
            compact_text(record.get("why"), limit=420),
        ]
        return " ".join(piece for piece in pieces if piece)
    if kind == "episode":
        pieces = [
            compact_text(record.get("outcomes"), limit=420),
            compact_text(record.get("what_happened"), limit=420),
        ]
        return " ".join(piece for piece in pieces if piece)
    return compact_text(record.get("body"), limit=520)


def current_record_status(
    record: dict[str, Any],
    replacement: dict[str, Any] | None,
) -> str:
    """Describe whether a changed record remains current or points elsewhere."""
    if replacement is not None:
        replacement_id = str(replacement.get("record_id") or "")
        replacement_title = compact_text(replacement.get("title"), limit=90)
        return f"use replacement `{replacement_title}` (`{replacement_id}`)"
    if is_current_record(record):
        return "current active record"
    status = str(record.get("status") or "record")
    valid_until = str(record.get("valid_until") or "").strip()
    if valid_until:
        return f"historical after `{valid_until}`"
    return f"historical `{status}` record"


def compact_text(value: Any, *, limit: int) -> str:
    """Return compact single-line text within a character budget."""
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def truncate_working_memory_lines(lines: list[str]) -> list[str]:
    """Clip Working Memory markdown to a bounded startup size."""
    if len(lines) <= WORKING_MEMORY_LINE_LIMIT:
        return lines
    visible = lines[: max(0, WORKING_MEMORY_LINE_LIMIT - 3)]
    visible.extend(
        [
            "",
            "> Truncated for startup size. Use `lerim query versions list --scope project --json` for deeper detail.",
        ]
    )
    return visible


def build_working_memory_manifest(
    *,
    run_id: str,
    status: str,
    generated_at: str,
    window_started_at: str,
    project: ContextBriefProject,
    data: WorkingMemoryData,
    changed_records_since_previous: int,
    trigger: str,
    current_file: Path,
    run_folder: Path,
) -> dict[str, Any]:
    """Build the Working Memory manifest payload."""
    record_ids = working_memory_record_ids(data)
    return {
        "run_id": run_id,
        "operation": WORKING_MEMORY_OPERATION,
        "status": status,
        "generated_at": generated_at,
        "window_started_at": window_started_at,
        "window_hours": WORKING_MEMORY_WINDOW_HOURS,
        "trigger": trigger,
        "project_id": project.identity.project_id,
        "project": project.name,
        "repo_path": str(project.identity.repo_path),
        "recent_versions_considered": len(data.versions),
        "records_included": len(record_ids),
        "included_record_ids": list(record_ids),
        "changed_records_since_previous": changed_records_since_previous,
        "current_file": str(current_file),
        "run_folder": str(run_folder),
    }


def working_memory_status(
    *,
    config: Config,
    store: ContextStore,
    project: ContextBriefProject,
) -> WorkingMemoryStatus:
    """Compute current Working Memory freshness and availability."""
    paths = working_memory_paths(config, project.identity.project_id)
    manifest = read_manifest(paths.current_manifest) or {}
    generated_at = str(manifest.get("generated_at") or "").strip() or None
    window_hours = int(manifest.get("window_hours") or WORKING_MEMORY_WINDOW_HOURS)
    changed = count_changed_records_since(
        store,
        project_id=project.identity.project_id,
        since=generated_at,
    )
    raw_included_ids = manifest.get("included_record_ids") or []
    included_ids = raw_included_ids if isinstance(raw_included_ids, list) else []
    missing = count_missing_included_records(
        store,
        project_id=project.identity.project_id,
        record_ids=[str(record_id) for record_id in included_ids],
    )
    age = age_seconds_since(generated_at)
    file_exists = paths.current_file.is_file()
    manifest_exists = paths.current_manifest.is_file()
    if not file_exists:
        availability = "missing"
        action = "Run `lerim working-memory refresh`."
    elif not manifest_exists:
        availability = "error"
        action = "Run `lerim working-memory refresh --force`."
    elif missing > 0:
        availability = "stale"
        action = "Refresh because this Working Memory cites records no longer present in the live DB."
    elif changed > 0:
        availability = "stale"
        action = "Refresh if newest short-term DB context matters."
    elif age is None or age > window_hours * 3600:
        availability = "stale"
        action = "Refresh because the short-term memory window has moved."
    else:
        availability = "available"
        action = "Continue with this recent memory; use Context Brief for long-term context."
    return WorkingMemoryStatus(
        availability=availability,
        project=project.name,
        project_id=project.identity.project_id,
        repo_path=str(project.identity.repo_path),
        generated_at=generated_at,
        age_seconds=age,
        window_hours=window_hours,
        window_started_at=str(manifest.get("window_started_at") or "") or None,
        recent_versions_considered=int(manifest.get("recent_versions_considered") or 0),
        records_included=int(manifest.get("records_included") or 0),
        records_changed_since_generation=changed,
        records_missing_since_generation=missing,
        current_file=str(paths.current_file),
        current_manifest=str(paths.current_manifest),
        latest_run_folder=str(manifest.get("run_folder") or "") or None,
        suggested_action=action,
    )


def write_current_working_memory_artifacts(
    *,
    paths: WorkingMemoryPaths,
    run_markdown: Path,
    run_manifest: Path,
) -> None:
    """Copy dated Working Memory artifacts into the stable current location."""
    paths.current_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(run_markdown, paths.current_file)
    shutil.copyfile(run_manifest, paths.current_manifest)


def working_memory_status_to_dict(status: WorkingMemoryStatus) -> dict[str, Any]:
    """Convert Working Memory status to a JSON-ready dict."""
    payload = asdict(status)
    payload["age"] = human_age(status.age_seconds)
    return payload


def _append_record_id(ordered: list[str], seen: set[str], value: Any) -> None:
    """Append one normalized record ID once."""
    record_id = str(value or "").strip()
    if record_id and record_id not in seen:
        seen.add(record_id)
        ordered.append(record_id)


__all__ = [
    "WORKING_MEMORY_FILENAME",
    "WORKING_MEMORY_MANIFEST_FILENAME",
    "WORKING_MEMORY_OPERATION",
    "WORKING_MEMORY_WINDOW_HOURS",
    "WorkingMemoryData",
    "WorkingMemoryPaths",
    "WorkingMemoryStatus",
    "build_working_memory_manifest",
    "load_working_memory_data",
    "load_recent_record_versions",
    "render_working_memory_markdown",
    "working_memory_paths",
    "working_memory_record_ids",
    "working_memory_status",
    "working_memory_status_to_dict",
    "working_memory_window_start",
    "write_current_working_memory_artifacts",
]
