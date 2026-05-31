"""Generated Context Brief use case for durable startup context."""

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

CONTEXT_BRIEF_FILENAME = "CONTEXT_BRIEF.md"
CONTEXT_BRIEF_MANIFEST_FILENAME = "manifest.json"
CONTEXT_BRIEF_OPERATION = "context-brief"
MAX_CANDIDATE_RECORDS = 60
MAX_LINE_COUNT = 120
REFERENCE_LINE_LIMIT = 20
SECTION_LINE_LIMITS = {
    "summary": 2,
    "start_here": 4,
    "current_handoff": 4,
    "decisions": 8,
    "constraints_preferences": 8,
    "operational_context": 6,
    "project_facts": 6,
    "open_risks": 4,
    "follow_up_queries": 3,
}
KIND_PRIORITY = {
    "decision": 0,
    "preference": 1,
    "constraint": 2,
    "fact": 3,
    "episode": 4,
}
KIND_BUDGETS = {
    "decision": 16,
    "preference": 8,
    "constraint": 12,
    "fact": 12,
    "episode": 2,
}
SECTION_KIND_RULES = {
    "decisions": {"decision"},
    "constraints_preferences": {"constraint", "preference"},
    "project_facts": {"fact"},
}


@dataclass(frozen=True)
class ContextBriefProject:
    """Resolved project metadata for Context Brief commands."""

    name: str
    identity: ProjectIdentity


@dataclass(frozen=True)
class ContextBriefPaths:
    """Stable current Context Brief artifact paths for one project."""

    current_dir: Path
    current_file: Path
    current_manifest: Path


@dataclass(frozen=True)
class MemoryLine:
    """One cited Context Brief line returned by synthesis."""

    text: str
    record_ids: tuple[str, ...]


@dataclass(frozen=True)
class MemorySection:
    """One rendered Context Brief section."""

    title: str
    lines: tuple[MemoryLine, ...]


@dataclass(frozen=True)
class ContextBriefDraft:
    """Structured synthesized Context Brief content before markdown rendering."""

    summary: tuple[MemoryLine, ...]
    start_here: tuple[MemoryLine, ...] = ()
    current_handoff: tuple[MemoryLine, ...] = ()
    decisions: tuple[MemoryLine, ...] = ()
    constraints_preferences: tuple[MemoryLine, ...] = ()
    operational_context: tuple[MemoryLine, ...] = ()
    project_facts: tuple[MemoryLine, ...] = ()
    open_risks: tuple[MemoryLine, ...] = ()
    follow_up_queries: tuple[MemoryLine, ...] = ()
    sections: tuple[MemorySection, ...] = ()


@dataclass(frozen=True)
class ContextBriefStatus:
    """Freshness and availability metadata for one project's current artifact."""

    availability: str
    project: str
    project_id: str
    repo_path: str
    generated_at: str | None
    age_seconds: int | None
    records_included: int
    records_changed_since_generation: int
    records_missing_since_generation: int
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


def context_brief_paths(config: Config, project_id: str) -> ContextBriefPaths:
    """Return stable current Context Brief paths for a project."""
    current_dir = config.global_data_dir / "workspace" / "current" / project_id
    return ContextBriefPaths(
        current_dir=current_dir,
        current_file=current_dir / CONTEXT_BRIEF_FILENAME,
        current_manifest=current_dir / CONTEXT_BRIEF_MANIFEST_FILENAME,
    )


def resolve_context_brief_project(
    *,
    config: Config,
    project: str | None = None,
    cwd: Path | None = None,
) -> ContextBriefProject:
    """Resolve a project name/path or cwd into a registered project identity."""
    projects = config.projects or {}
    if not projects:
        raise ValueError("No registered projects. Add one with `lerim project add <path>`.")

    if project:
        token = str(project).strip()
        if token in projects:
            project_path = Path(projects[token]).expanduser().resolve()
            return ContextBriefProject(
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
        return ContextBriefProject(
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
    return ContextBriefProject(
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


def count_missing_included_records(
    store: ContextStore,
    *,
    project_id: str,
    record_ids: list[str],
) -> int:
    """Count manifest-cited records that are no longer live for this project."""
    cleaned_ids = [str(record_id).strip() for record_id in record_ids if str(record_id).strip()]
    if not cleaned_ids:
        return 0
    placeholders = ", ".join("?" for _ in cleaned_ids)
    with store.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT record_id
            FROM records
            WHERE project_id = ?
              AND record_id IN ({placeholders})
            """,
            (project_id, *cleaned_ids),
        ).fetchall()
    existing = {str(row["record_id"]) for row in rows}
    return len(set(cleaned_ids) - existing)


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


def empty_context_brief_draft() -> ContextBriefDraft:
    """Return an empty draft for projects with no active context records."""
    return ContextBriefDraft(summary=())


def validate_draft(
    draft: ContextBriefDraft,
    *,
    allowed_record_ids: set[str],
    record_kinds: dict[str, str] | None = None,
) -> None:
    """Validate that substantive lines cite known records in matching sections."""
    normalized_kinds = {
        str(record_id): str(kind).strip().lower()
        for record_id, kind in (record_kinds or {}).items()
    }
    for section_name, line in iter_named_draft_lines(draft):
        if not line.text.strip():
            continue
        if not line.record_ids:
            raise ValueError("context_brief_line_missing_record_id")
        unknown = [record_id for record_id in line.record_ids if record_id not in allowed_record_ids]
        if unknown:
            raise ValueError(f"context_brief_line_unknown_record_id:{unknown[0]}")
        allowed_kinds = SECTION_KIND_RULES.get(section_name)
        if allowed_kinds:
            for record_id in line.record_ids:
                kind = normalized_kinds.get(record_id)
                if kind and kind not in allowed_kinds:
                    raise ValueError(
                        "context_brief_line_wrong_section:"
                        f"{section_name}:{record_id}:{kind}"
                    )


def sanitize_draft_section_kinds(
    draft: ContextBriefDraft,
    *,
    record_kinds: dict[str, str],
) -> ContextBriefDraft:
    """Drop fixed-section lines that cite records outside that section's kind rule."""
    normalized_kinds = {
        str(record_id): str(kind).strip().lower()
        for record_id, kind in record_kinds.items()
    }

    def keep_matching(section_name: str, lines: tuple[MemoryLine, ...]) -> tuple[MemoryLine, ...]:
        allowed_kinds = SECTION_KIND_RULES.get(section_name)
        if not allowed_kinds:
            return lines
        kept: list[MemoryLine] = []
        for line in lines:
            kinds = [normalized_kinds.get(record_id) for record_id in line.record_ids]
            if kinds and all(kind in allowed_kinds for kind in kinds):
                kept.append(line)
        return tuple(kept)

    return ContextBriefDraft(
        summary=draft.summary,
        start_here=draft.start_here,
        current_handoff=draft.current_handoff,
        decisions=keep_matching("decisions", draft.decisions),
        constraints_preferences=keep_matching(
            "constraints_preferences",
            draft.constraints_preferences,
        ),
        operational_context=draft.operational_context,
        project_facts=keep_matching("project_facts", draft.project_facts),
        open_risks=draft.open_risks,
        follow_up_queries=draft.follow_up_queries,
        sections=draft.sections,
    )


def trim_context_brief_draft(draft: ContextBriefDraft) -> ContextBriefDraft:
    """Limit synthesized sections to a startup-sized brief instead of a record dump."""
    trimmed_sections: list[MemorySection] = []
    for section in draft.sections[:2]:
        trimmed_sections.append(
            MemorySection(
                title=section.title,
                lines=section.lines[:6],
            )
        )
    return ContextBriefDraft(
        summary=draft.summary[: SECTION_LINE_LIMITS["summary"]],
        start_here=draft.start_here[: SECTION_LINE_LIMITS["start_here"]],
        current_handoff=draft.current_handoff[: SECTION_LINE_LIMITS["current_handoff"]],
        decisions=draft.decisions[: SECTION_LINE_LIMITS["decisions"]],
        constraints_preferences=draft.constraints_preferences[
            : SECTION_LINE_LIMITS["constraints_preferences"]
        ],
        operational_context=draft.operational_context[
            : SECTION_LINE_LIMITS["operational_context"]
        ],
        project_facts=draft.project_facts[: SECTION_LINE_LIMITS["project_facts"]],
        open_risks=draft.open_risks[: SECTION_LINE_LIMITS["open_risks"]],
        follow_up_queries=draft.follow_up_queries[
            : SECTION_LINE_LIMITS["follow_up_queries"]
        ],
        sections=tuple(trimmed_sections),
    )


def iter_draft_lines(draft: ContextBriefDraft) -> tuple[MemoryLine, ...]:
    """Return all synthesized memory lines in rendered section order."""
    return tuple(line for _section_name, line in iter_named_draft_lines(draft))


def iter_named_draft_lines(draft: ContextBriefDraft) -> tuple[tuple[str, MemoryLine], ...]:
    """Return synthesized memory lines with their source section names."""
    named_lines: list[tuple[str, MemoryLine]] = []
    for section_name, section_lines in (
        ("summary", draft.summary),
        ("start_here", draft.start_here),
        ("current_handoff", draft.current_handoff),
        ("decisions", draft.decisions),
        ("constraints_preferences", draft.constraints_preferences),
        ("operational_context", draft.operational_context),
        ("project_facts", draft.project_facts),
        ("open_risks", draft.open_risks),
        ("follow_up_queries", draft.follow_up_queries),
    ):
        named_lines.extend((section_name, line) for line in section_lines)
    for section in draft.sections:
        named_lines.extend((section.title, line) for line in section.lines)
    return tuple(named_lines)


def included_record_ids(draft: ContextBriefDraft) -> tuple[str, ...]:
    """Return unique record IDs in first-citation order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for line in iter_draft_lines(draft):
        for record_id in line.record_ids:
            if record_id not in seen:
                seen.add(record_id)
                ordered.append(record_id)
    return tuple(ordered)


def render_context_brief_markdown(
    *,
    project: ContextBriefProject,
    generated_at: str,
    previous_generated_at: str | None,
    generation_trigger: str,
    records_considered: int,
    records_included: int,
    db_records_changed_since_previous: int,
    draft: ContextBriefDraft,
    candidate_records: list[dict[str, Any]] | None = None,
    current_file: Path | None = None,
    run_folder: Path | None = None,
) -> str:
    """Render Context Brief markdown with freshness metadata and citations."""
    lines = [
        "# Context Brief",
        "",
        "> Long-term Lerim project memory generated from SQLite context. This markdown is a derived view, not the source of truth.",
        "> It reflects persisted durable records only. Use Working Memory for short-term continuation context, plus git status, tests, and the current chat for live workspace state.",
        "",
        f"- Project: `{project.name}`",
        f"- Generated: `{generated_at}`",
        "",
    ]
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
            "- Run `git status` before editing; Context Brief is DB context, not workspace state.",
            "- Run `lerim working-memory show` for short-term continuation context.",
            "- Treat any test/build results below as historical persisted evidence; rerun relevant checks after edits.",
        ]
    )
    for item in draft.start_here[:6]:
        rendered = render_memory_line(item, seen_lines=seen_lines)
        if rendered:
            lines.append(rendered)
    if not has_episode_evidence:
        lines.append(
            "- No implementation handoff is available from persisted episode records here; use Working Memory, the current chat, tests, and git state for live work."
        )
    lines.append("")
    if not iter_draft_lines(draft):
        lines.extend(
            [
                "## Summary",
                "",
                "No active project context records exist yet. Run `lerim ingest` and `lerim curate` to build context.",
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
                "> No recent episode records were cited. Treat this as durable long-term context, not a continuation handoff.",
            ]
        )
    append_memory_section(
        lines,
        title="Continuation Handoff",
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
        title="Reusable Workflows & Gotchas",
        items=draft.operational_context[:10],
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
    append_context_brief_technical_details(
        lines,
        project=project,
        generated_at=generated_at,
        previous_generated_at=previous_generated_at,
        generation_trigger=generation_trigger,
        records_considered=records_considered,
        records_included=records_included,
        db_records_changed_since_previous=db_records_changed_since_previous,
        current_file=current_file,
        run_folder=run_folder,
    )
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
    """Append a fixed Context Brief section when it has rendered lines."""
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
        role = str(record.get("record_role") or "general").strip()
        role_text = f", role {role}" if role and role != "general" else ""
        updated_at = str(record.get("updated_at") or "").strip()
        source_session = str(record.get("source_session_id") or "").strip()
        source_text = f"; source_session: `{source_session}`" if source_session else ""
        lines.append(
            f"- `{record_id}` ({kind}{role_text}, updated `{updated_at}`): {title}{source_text}"
        )
    if len(cited_record_ids) > REFERENCE_LINE_LIMIT:
        remaining = len(cited_record_ids) - REFERENCE_LINE_LIMIT
        lines.append(
            f"- {remaining} more cited source(s); run `lerim context-brief status` "
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
            "> Truncated for startup size. Use `lerim query` or `lerim answer` for deeper context.",
        ]
    )
    return visible


def append_sources_with_budget(lines: list[str], source_lines: list[str]) -> list[str]:
    """Append as many sources as fit without hiding the startup brief body."""
    sources_block = ["", "## Sources", "", *source_lines]
    if len(lines) + len(sources_block) <= MAX_LINE_COUNT:
        return [*lines, *sources_block]
    body = list(lines)
    minimum_source_budget = 6
    if len(body) > MAX_LINE_COUNT - minimum_source_budget:
        body = body[: max(0, MAX_LINE_COUNT - minimum_source_budget - 2)]
        strip_dangling_section_header(body)
        body.extend(
            [
                "",
                "> Body truncated for startup size. Use `lerim query` for deeper context.",
            ]
        )
    remaining = MAX_LINE_COUNT - len(body) - 3
    if remaining <= 0:
        return truncate_markdown_lines(body)
    visible_sources = source_lines[:remaining]
    hidden = len(source_lines) - len(visible_sources)
    if hidden > 0 and visible_sources:
        visible_sources = visible_sources[:-1]
        hidden = len(source_lines) - len(visible_sources)
        visible_sources.append(
            f"- {hidden} more cited source(s); run `lerim query records list --json` for full DB details."
        )
    return [*body, "", "## Sources", "", *visible_sources]


def append_context_brief_technical_details(
    lines: list[str],
    *,
    project: ContextBriefProject,
    generated_at: str,
    previous_generated_at: str | None,
    generation_trigger: str,
    records_considered: int,
    records_included: int,
    db_records_changed_since_previous: int,
    current_file: Path | None,
    run_folder: Path | None,
) -> None:
    """Append machine-oriented Context Brief metadata after useful memory content."""
    lines.extend(["", "## Technical Details", ""])
    lines.extend(
        [
            f"- Project ID: `{project.identity.project_id}`",
            f"- Repo path: `{project.identity.repo_path}`",
            f"- Generated: `{generated_at}`",
            f"- Previous generation: `{previous_generated_at or 'none'}`",
            f"- Generation trigger: `{generation_trigger}`",
            f"- Records considered: {records_considered}",
            f"- Records cited: {records_included}",
            f"- DB records changed before this generation: {db_records_changed_since_previous}",
            "- Live DB freshness: `lerim context-brief status`",
            "- Refresh: `lerim context-brief refresh`",
            "- Continuation handoff: `lerim working-memory show`",
            "- Inspect full sources: "
            f"`lerim query records list --scope project --project {project.identity.repo_path} --json`.",
        ]
    )
    if current_file is not None:
        lines.append(f"- Current file: `{current_file}`")
    if run_folder is not None:
        lines.append(f"- Run folder: `{run_folder}`")


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
    project: ContextBriefProject,
    records_considered: int,
    records_included: int,
    included_record_ids_value: tuple[str, ...],
    changed_records_since_previous: int,
    trigger: str,
    current_file: Path,
    run_folder: Path,
) -> dict[str, Any]:
    """Build the Context Brief manifest payload."""
    return {
        "run_id": run_id,
        "operation": CONTEXT_BRIEF_OPERATION,
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


def context_brief_status(
    *,
    config: Config,
    store: ContextStore,
    project: ContextBriefProject,
) -> ContextBriefStatus:
    """Compute current Context Brief freshness and availability."""
    paths = context_brief_paths(config, project.identity.project_id)
    manifest = read_manifest(paths.current_manifest) or {}
    generated_at = str(manifest.get("generated_at") or "").strip() or None
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
        action = "Run `lerim context-brief refresh`."
    elif not manifest_exists:
        availability = "error"
        action = "Run `lerim context-brief refresh --force`."
    elif missing > 0:
        availability = "stale"
        action = "Refresh because this Context Brief cites records no longer present in the live DB."
    elif changed > 0:
        availability = "stale"
        action = "Refresh if newest persisted DB context matters."
    else:
        availability = "available"
        action = "Continue with this startup context; inspect sources or query deeper if needed."
    return ContextBriefStatus(
        availability=availability,
        project=project.name,
        project_id=project.identity.project_id,
        repo_path=str(project.identity.repo_path),
        generated_at=generated_at,
        age_seconds=age,
        records_included=int(manifest.get("records_included") or 0),
        records_changed_since_generation=changed,
        records_missing_since_generation=missing,
        current_file=str(paths.current_file),
        current_manifest=str(paths.current_manifest),
        latest_run_folder=str(manifest.get("run_folder") or "") or None,
        suggested_action=action,
    )


def write_current_artifacts(
    *,
    paths: ContextBriefPaths,
    run_markdown: Path,
    run_manifest: Path,
) -> None:
    """Copy dated artifacts into the stable current location."""
    paths.current_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(run_markdown, paths.current_file)
    shutil.copyfile(run_manifest, paths.current_manifest)


def status_to_dict(status: ContextBriefStatus) -> dict[str, Any]:
    """Convert status dataclass to JSON-ready dict."""
    payload = asdict(status)
    payload["age"] = human_age(status.age_seconds)
    return payload
