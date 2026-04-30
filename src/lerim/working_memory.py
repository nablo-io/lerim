"""Generated Working Memory use case for fast agent startup context."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lerim.config.project_scope import match_session_project
from lerim.config.settings import Config
from lerim.context import ContextStore, ProjectIdentity, resolve_project_identity

WORKING_MEMORY_FILENAME = "WORKING_MEMORY.md"
WORKING_MEMORY_MANIFEST_FILENAME = "manifest.json"
WORKING_MEMORY_OPERATION = "working-memory"
MAX_CANDIDATE_RECORDS = 80
MAX_LINE_COUNT = 80
REFERENCE_LINE_LIMIT = 12
KIND_PRIORITY = {
    "decision": 0,
    "preference": 1,
    "constraint": 2,
    "fact": 3,
    "reference": 4,
    "episode": 5,
}
KIND_BUDGETS = {
    "decision": 20,
    "preference": 10,
    "constraint": 14,
    "fact": 14,
    "reference": 8,
    "episode": 8,
}


@dataclass(frozen=True)
class WorkingMemoryProject:
    """Resolved project metadata for Working Memory commands."""

    name: str
    identity: ProjectIdentity


@dataclass(frozen=True)
class WorkingMemoryPaths:
    """Stable current Working Memory artifact paths for one project."""

    current_dir: Path
    current_file: Path
    current_manifest: Path


@dataclass(frozen=True)
class MemoryLine:
    """One cited Working Memory line returned by synthesis."""

    text: str
    record_ids: tuple[str, ...]


@dataclass(frozen=True)
class MemorySection:
    """One rendered Working Memory section."""

    title: str
    lines: tuple[MemoryLine, ...]


@dataclass(frozen=True)
class WorkingMemoryDraft:
    """Structured synthesized Working Memory content before markdown rendering."""

    summary: tuple[MemoryLine, ...]
    start_here: tuple[MemoryLine, ...] = ()
    current_handoff: tuple[MemoryLine, ...] = ()
    decisions: tuple[MemoryLine, ...] = ()
    constraints_preferences: tuple[MemoryLine, ...] = ()
    project_facts: tuple[MemoryLine, ...] = ()
    open_risks: tuple[MemoryLine, ...] = ()
    follow_up_queries: tuple[MemoryLine, ...] = ()
    sections: tuple[MemorySection, ...] = ()


@dataclass(frozen=True)
class WorkingMemoryStatus:
    """Freshness and availability metadata for one project's current artifact."""

    availability: str
    project: str
    project_id: str
    repo_path: str
    generated_at: str | None
    age_seconds: int | None
    records_included: int
    records_changed_since_generation: int
    current_file: str
    current_manifest: str
    latest_run_folder: str | None
    suggested_action: str


def utc_now_iso() -> str:
    """Return current UTC time as ISO text."""
    return datetime.now(timezone.utc).isoformat()


def parse_iso_datetime(raw: str | None) -> datetime | None:
    """Parse an ISO timestamp into an aware UTC datetime when possible."""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_seconds_since(raw: str | None, *, now: datetime | None = None) -> int | None:
    """Return elapsed seconds since an ISO timestamp."""
    parsed = parse_iso_datetime(raw)
    if parsed is None:
        return None
    effective_now = now or datetime.now(timezone.utc)
    return max(0, int((effective_now - parsed).total_seconds()))


def human_age(seconds: int | None) -> str:
    """Render an elapsed duration in compact human form."""
    if seconds is None:
        return "unknown age"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def working_memory_paths(config: Config, project_id: str) -> WorkingMemoryPaths:
    """Return stable current Working Memory paths for a project."""
    current_dir = config.global_data_dir / "workspace" / "current" / project_id
    return WorkingMemoryPaths(
        current_dir=current_dir,
        current_file=current_dir / WORKING_MEMORY_FILENAME,
        current_manifest=current_dir / WORKING_MEMORY_MANIFEST_FILENAME,
    )


def resolve_working_memory_project(
    *,
    config: Config,
    project: str | None = None,
    cwd: Path | None = None,
) -> WorkingMemoryProject:
    """Resolve a project name/path or cwd into a registered project identity."""
    projects = config.projects or {}
    if not projects:
        raise ValueError("No registered projects. Add one with `lerim project add <path>`.")

    if project:
        token = str(project).strip()
        if token in projects:
            project_path = Path(projects[token]).expanduser().resolve()
            return WorkingMemoryProject(
                name=token,
                identity=resolve_project_identity(project_path),
            )
        try:
            candidate = Path(token).expanduser().resolve()
        except OSError as exc:
            raise ValueError(f"Project not found: {project}") from exc
        match = match_session_project(str(candidate), projects)
        if match is None:
            raise ValueError(f"Project not found: {project}")
        name, project_path = match
        return WorkingMemoryProject(
            name=name,
            identity=resolve_project_identity(project_path),
        )

    candidate_cwd = (cwd or Path.cwd()).expanduser().resolve()
    match = match_session_project(str(candidate_cwd), projects)
    if match is None:
        raise ValueError(
            "Current directory is not inside a registered Lerim project. "
            "Run `lerim project add <path>` or pass `--project <name-or-path>`."
        )
    name, project_path = match
    return WorkingMemoryProject(
        name=name,
        identity=resolve_project_identity(project_path),
    )


def read_manifest(path: Path) -> dict[str, Any] | None:
    """Read a manifest JSON object, returning None when unavailable."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def count_changed_records_since(
    store: ContextStore,
    *,
    project_id: str,
    since: str | None,
) -> int:
    """Count distinct project records changed after the provided timestamp."""
    store.initialize()
    with store.connect() as conn:
        if since:
            row = conn.execute(
                """
                SELECT COUNT(DISTINCT record_id) AS total
                FROM record_versions
                WHERE project_id = ? AND changed_at > ?
                """,
                (project_id, since),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT COUNT(1) AS total
                FROM records
                WHERE project_id = ? AND status = 'active'
                """,
                (project_id,),
            ).fetchone()
    return int(row["total"] or 0) if row else 0


def load_candidate_records(
    store: ContextStore,
    *,
    project_id: str,
    limit: int = MAX_CANDIDATE_RECORDS,
) -> list[dict[str, Any]]:
    """Load bounded active records with durable-kind and recency balance."""
    protected_ids: list[str] = []
    protected_by_id: dict[str, dict[str, Any]] = {}
    for kind, budget in KIND_BUDGETS.items():
        payload = store.query(
            entity="records",
            mode="list",
            project_ids=[project_id],
            kind=kind,
            status="active",
            order_by="updated_at",
            limit=max(1, int(budget)),
            include_total=False,
        )
        for row in payload.get("rows") or []:
            record_id = str(row.get("record_id") or "")
            if record_id and record_id not in protected_by_id:
                protected_ids.append(record_id)
                protected_by_id[record_id] = row

    payload = store.query(
        entity="records",
        mode="list",
        project_ids=[project_id],
        status="active",
        order_by="updated_at",
        limit=max(1, int(limit)),
        include_total=False,
    )
    row_by_id = dict(protected_by_id)
    for row in payload.get("rows") or []:
        record_id = str(row.get("record_id") or "")
        if record_id:
            row_by_id.setdefault(record_id, row)

    protected = [protected_by_id[record_id] for record_id in protected_ids]
    fill = [
        row
        for record_id, row in row_by_id.items()
        if record_id not in protected_by_id
    ]
    sort_candidates(protected)
    sort_candidates(fill)
    rows = protected[: max(1, int(limit))] + fill
    return rows[: max(1, int(limit))]


def sort_candidates(rows: list[dict[str, Any]]) -> None:
    """Sort candidate rows by kind priority, then newest update first."""
    rows.sort(
        key=lambda row: (
            str(row.get("updated_at") or ""),
            str(row.get("record_id") or ""),
        ),
        reverse=True,
    )
    rows.sort(key=lambda row: KIND_PRIORITY.get(str(row.get("kind") or ""), 50))


def empty_working_memory_draft() -> WorkingMemoryDraft:
    """Return an empty draft for projects with no active context records."""
    return WorkingMemoryDraft(summary=())


def validate_draft(draft: WorkingMemoryDraft, *, allowed_record_ids: set[str]) -> None:
    """Validate that every substantive line has a known record citation."""
    for line in iter_draft_lines(draft):
        if not line.text.strip():
            continue
        if not line.record_ids:
            raise ValueError("working_memory_line_missing_record_id")
        unknown = [record_id for record_id in line.record_ids if record_id not in allowed_record_ids]
        if unknown:
            raise ValueError(f"working_memory_line_unknown_record_id:{unknown[0]}")


def iter_draft_lines(draft: WorkingMemoryDraft) -> tuple[MemoryLine, ...]:
    """Return all synthesized memory lines in rendered section order."""
    lines: list[MemoryLine] = []
    lines.extend(draft.summary)
    lines.extend(draft.start_here)
    lines.extend(draft.current_handoff)
    lines.extend(draft.decisions)
    lines.extend(draft.constraints_preferences)
    lines.extend(draft.project_facts)
    lines.extend(draft.open_risks)
    lines.extend(draft.follow_up_queries)
    for section in draft.sections:
        lines.extend(section.lines)
    return tuple(lines)


def included_record_ids(draft: WorkingMemoryDraft) -> tuple[str, ...]:
    """Return unique record IDs in first-citation order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for line in iter_draft_lines(draft):
        for record_id in line.record_ids:
            if record_id not in seen:
                seen.add(record_id)
                ordered.append(record_id)
    return tuple(ordered)


def render_working_memory_markdown(
    *,
    project: WorkingMemoryProject,
    generated_at: str,
    previous_generated_at: str | None,
    generation_trigger: str,
    records_considered: int,
    records_included: int,
    db_records_changed_since_previous: int,
    draft: WorkingMemoryDraft,
    candidate_records: list[dict[str, Any]] | None = None,
    current_file: Path | None = None,
    run_folder: Path | None = None,
) -> str:
    """Render Working Memory markdown with freshness metadata and citations."""
    lines = [
        "# Working Memory",
        "",
        "> Generated from Lerim SQLite context. This markdown is a derived view, not the source of truth.",
        "> It reflects persisted Lerim records only. Check git status, tests, and the current chat for live workspace state.",
        "",
        f"- Project: `{project.name}` (`{project.identity.project_id}`)",
        f"- Generated: `{generated_at}`",
        f"- Previous generation: `{previous_generated_at or 'none'}`",
        f"- Generation trigger: `{generation_trigger}`",
        f"- Records considered: {records_considered}",
        f"- Records cited: {records_included}",
        f"- DB records changed before this generation: {db_records_changed_since_previous}",
        "- Live DB freshness: `lerim working-memory status`",
        "- Refresh: `lerim working-memory refresh`",
        "- Inspect sources: use the Sources section below, or run "
        f"`lerim query records list --scope project --project {project.identity.repo_path} --json`.",
        "",
    ]
    if current_file is not None:
        lines.append(f"- Current file: `{current_file}`")
    if run_folder is not None:
        lines.append(f"- Run folder: `{run_folder}`")
    if current_file is not None or run_folder is not None:
        lines.append("")
    has_episode_evidence = any(
        str(record.get("kind") or "") == "episode"
        for record in candidate_records or []
    )
    seen_lines: set[str] = set()
    lines.extend(
        [
            "## Start Here",
            "",
            f"- Memory scope: `{project.identity.repo_path}` (`{project.name}`).",
            f"- To read this exact memory from another cwd, pass `--project {project.identity.repo_path}`.",
            "- Code/package work may live in a child directory; use more specific Project Facts paths for code and tests.",
            "- Run `git status` before editing; Working Memory is DB context, not workspace state.",
            "- Treat any test/build results below as historical persisted evidence; rerun relevant checks after edits.",
        ]
    )
    for item in draft.start_here[:6]:
        rendered = render_memory_line(item, seen_lines=seen_lines)
        if rendered:
            lines.append(rendered)
    if not has_episode_evidence:
        lines.append(
            "- No implementation handoff is available from persisted episode records; use the current chat, tests, and git state for live work."
        )
    lines.append("")
    if not iter_draft_lines(draft):
        lines.extend(
            [
                "## Summary",
                "",
                "No active project context records exist yet. Run `lerim sync` and `lerim maintain` to build context.",
            ]
        )
        return "\n".join(truncate_markdown_lines(lines)).rstrip() + "\n"

    append_memory_section(
        lines,
        title="Summary",
        items=draft.summary[:8],
        seen_lines=seen_lines,
    )
    cited_ids = included_record_ids(draft)
    if cited_ids and not any(
        str(record.get("kind") or "") == "episode"
        for record in candidate_records or []
        if str(record.get("record_id") or "") in cited_ids
    ):
        lines.extend(
            [
                "",
                "> No recent episode records were cited. Treat this as durable context, not a complete handoff of current work.",
            ]
        )
    append_memory_section(
        lines,
        title="Current Handoff",
        items=draft.current_handoff[:10],
        seen_lines=seen_lines,
    )
    append_memory_section(
        lines,
        title="Decisions",
        items=draft.decisions[:12],
        seen_lines=seen_lines,
    )
    append_memory_section(
        lines,
        title="Constraints & Preferences",
        items=draft.constraints_preferences[:12],
        seen_lines=seen_lines,
    )
    append_memory_section(
        lines,
        title="Project Facts",
        items=draft.project_facts[:12],
        seen_lines=seen_lines,
    )
    append_memory_section(
        lines,
        title="Open Risks / Review Queue",
        items=draft.open_risks[:10],
        seen_lines=seen_lines,
    )
    append_memory_section(
        lines,
        title="Follow-up Queries",
        items=draft.follow_up_queries[:8],
        seen_lines=seen_lines,
    )
    for section in draft.sections:
        if not section.lines:
            continue
        append_memory_section(
            lines,
            title=section.title.strip(),
            items=section.lines[:14],
            seen_lines=seen_lines,
        )
    source_lines = format_source_reference_lines(
        candidate_records or [],
        cited_record_ids=included_record_ids(draft),
    )
    if source_lines:
        lines = append_sources_with_budget(lines, source_lines)
    else:
        lines = truncate_markdown_lines(lines)
    return "\n".join(lines).rstrip() + "\n"


def clean_memory_text(text: str, record_ids: tuple[str, ...]) -> str:
    """Remove accidental inline record IDs from model-written memory text."""
    cleaned = str(text or "").strip()
    for record_id in record_ids:
        cleaned = cleaned.replace(str(record_id), "")
    for token in ("[]", "()", "(,)", "[,]"):
        cleaned = cleaned.replace(token, "")
    return " ".join(cleaned.split()).strip(" -:;,")


def render_memory_line(
    item: MemoryLine,
    *,
    seen_lines: set[str],
) -> str | None:
    """Render one deduped memory bullet."""
    cleaned = clean_memory_text(item.text, item.record_ids)
    if not cleaned:
        return None
    normalized = cleaned.casefold()
    if normalized in seen_lines:
        return None
    seen_lines.add(normalized)
    return f"- {cleaned} {format_citations(item.record_ids)}"


def append_memory_section(
    lines: list[str],
    *,
    title: str,
    items: tuple[MemoryLine, ...],
    seen_lines: set[str],
) -> None:
    """Append a fixed Working Memory section when it has rendered lines."""
    section_lines: list[str] = []
    for item in items:
        rendered = render_memory_line(item, seen_lines=seen_lines)
        if rendered:
            section_lines.append(rendered)
    if section_lines:
        lines.extend(["", f"## {title}", "", *section_lines])


def format_source_reference_lines(
    records: list[dict[str, Any]],
    *,
    cited_record_ids: tuple[str, ...],
) -> list[str]:
    """Render compact source lines for cited records."""
    by_id = {str(record.get("record_id") or ""): record for record in records}
    lines: list[str] = []
    for record_id in cited_record_ids[:REFERENCE_LINE_LIMIT]:
        record = by_id.get(record_id)
        if not record:
            continue
        title = str(record.get("title") or "Untitled").strip()
        kind = str(record.get("kind") or "record").strip()
        updated_at = str(record.get("updated_at") or "").strip()
        source_session = str(record.get("source_session_id") or "").strip()
        source_text = f"; source_session: `{source_session}`" if source_session else ""
        lines.append(
            f"- `{record_id}` ({kind}, updated `{updated_at}`): {title}{source_text}"
        )
    if len(cited_record_ids) > REFERENCE_LINE_LIMIT:
        remaining = len(cited_record_ids) - REFERENCE_LINE_LIMIT
        lines.append(
            f"- {remaining} more cited source(s); run `lerim working-memory status` "
            "and `lerim query records list --json` for full DB details."
        )
    return lines


def truncate_markdown_lines(lines: list[str]) -> list[str]:
    """Return markdown lines with an explicit truncation marker when clipped."""
    if len(lines) <= MAX_LINE_COUNT:
        return lines
    visible = lines[: max(0, MAX_LINE_COUNT - 3)]
    visible.extend(
        [
            "",
            "> Truncated for startup size. Use `lerim query` or `lerim ask` for deeper context.",
        ]
    )
    return visible


def append_sources_with_budget(lines: list[str], source_lines: list[str]) -> list[str]:
    """Append sources while preserving them under the max line budget."""
    sources_block = ["", "## Sources", "", *source_lines]
    if len(lines) + len(sources_block) <= MAX_LINE_COUNT:
        return [*lines, *sources_block]
    reserved = len(sources_block) + 3
    body_budget = max(0, MAX_LINE_COUNT - reserved)
    body = lines[:body_budget]
    strip_dangling_section_header(body)
    body.extend(
        [
            "",
            "> Body truncated for startup size. Sources are preserved below.",
        ]
    )
    return [*body, *sources_block]


def strip_dangling_section_header(lines: list[str]) -> None:
    """Remove a trailing empty section header after body truncation."""
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[-1].startswith("## "):
        lines.pop()
    while lines and not lines[-1].strip():
        lines.pop()


def format_citations(record_ids: tuple[str, ...]) -> str:
    """Format record citations for a rendered markdown line."""
    cleaned = [record_id for record_id in record_ids if record_id]
    return "[" + ", ".join(cleaned) + "]" if cleaned else ""


def build_manifest(
    *,
    run_id: str,
    status: str,
    generated_at: str,
    project: WorkingMemoryProject,
    records_considered: int,
    records_included: int,
    included_record_ids_value: tuple[str, ...],
    changed_records_since_previous: int,
    trigger: str,
    current_file: Path,
    run_folder: Path,
) -> dict[str, Any]:
    """Build the Working Memory manifest payload."""
    return {
        "run_id": run_id,
        "operation": WORKING_MEMORY_OPERATION,
        "status": status,
        "generated_at": generated_at,
        "trigger": trigger,
        "project_id": project.identity.project_id,
        "project": project.name,
        "repo_path": str(project.identity.repo_path),
        "records_considered": records_considered,
        "records_included": records_included,
        "included_record_ids": list(included_record_ids_value),
        "changed_records_since_previous": changed_records_since_previous,
        "current_file": str(current_file),
        "run_folder": str(run_folder),
    }


def working_memory_status(
    *,
    config: Config,
    store: ContextStore,
    project: WorkingMemoryProject,
) -> WorkingMemoryStatus:
    """Compute current Working Memory freshness and availability."""
    paths = working_memory_paths(config, project.identity.project_id)
    manifest = read_manifest(paths.current_manifest) or {}
    generated_at = str(manifest.get("generated_at") or "").strip() or None
    changed = count_changed_records_since(
        store,
        project_id=project.identity.project_id,
        since=generated_at,
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
    elif changed > 0:
        availability = "stale"
        action = "Refresh if newest persisted DB context matters."
    else:
        availability = "available"
        action = "Continue with this startup context; inspect sources or query deeper if needed."
    return WorkingMemoryStatus(
        availability=availability,
        project=project.name,
        project_id=project.identity.project_id,
        repo_path=str(project.identity.repo_path),
        generated_at=generated_at,
        age_seconds=age,
        records_included=int(manifest.get("records_included") or 0),
        records_changed_since_generation=changed,
        current_file=str(paths.current_file),
        current_manifest=str(paths.current_manifest),
        latest_run_folder=str(manifest.get("run_folder") or "") or None,
        suggested_action=action,
    )


def write_current_artifacts(
    *,
    paths: WorkingMemoryPaths,
    run_markdown: Path,
    run_manifest: Path,
) -> None:
    """Copy dated artifacts into the stable current location."""
    paths.current_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(run_markdown, paths.current_file)
    shutil.copyfile(run_manifest, paths.current_manifest)


def status_to_dict(status: WorkingMemoryStatus) -> dict[str, Any]:
    """Convert status dataclass to JSON-ready dict."""
    payload = asdict(status)
    payload["age"] = human_age(status.age_seconds)
    return payload
