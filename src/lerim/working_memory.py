"""Generated Working Memory use case for fast agent startup context."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from lerim.config.project_scope import match_session_project
from lerim.config.settings import Config
from lerim.context import ContextStore, ProjectIdentity, resolve_project_identity

WORKING_MEMORY_FILENAME = "WORKING_MEMORY.md"
WORKING_MEMORY_MANIFEST_FILENAME = "manifest.json"
WORKING_MEMORY_OPERATION = "working-memory"
MAX_CANDIDATE_RECORDS = 80
TARGET_LINE_COUNT = 50
MAX_LINE_COUNT = 80
KIND_PRIORITY = {
    "decision": 0,
    "preference": 1,
    "constraint": 2,
    "fact": 3,
    "reference": 4,
    "episode": 5,
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
    sections: tuple[MemorySection, ...]


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


@dataclass(frozen=True)
class WorkingMemoryGenerationResult:
    """Result payload for a refresh attempt."""

    status: str
    project: str
    project_id: str
    generated_at: str | None
    records_considered: int
    records_included: int
    records_changed_since_previous: int
    included_record_ids: tuple[str, ...]
    current_file: str
    current_manifest: str
    run_folder: str | None
    skip_reason: str | None = None


class WorkingMemorySynthesizer(Protocol):
    """Port for compressing candidate context records into cited sections."""

    def __call__(self, candidates: list[dict[str, Any]]) -> WorkingMemoryDraft:
        """Return a cited Working Memory draft for candidate records."""


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
    """Load bounded active records ordered by durable-kind priority."""
    payload = store.query(
        entity="records",
        mode="list",
        project_ids=[project_id],
        status="active",
        order_by="updated_at",
        limit=max(1, int(limit)),
        include_total=False,
    )
    rows = list(payload.get("rows") or [])
    rows.sort(
        key=lambda row: (str(row.get("updated_at") or ""), str(row.get("record_id") or "")),
        reverse=True,
    )
    rows.sort(key=lambda row: KIND_PRIORITY.get(str(row.get("kind") or ""), 50))
    return rows[: max(1, int(limit))]


def empty_working_memory_draft() -> WorkingMemoryDraft:
    """Return an empty draft for projects with no active context records."""
    return WorkingMemoryDraft(summary=(), sections=())


def validate_draft(draft: WorkingMemoryDraft, *, allowed_record_ids: set[str]) -> None:
    """Validate that every substantive line has a known record citation."""
    lines = [*draft.summary]
    for section in draft.sections:
        lines.extend(section.lines)
    for line in lines:
        if not line.text.strip():
            continue
        if not line.record_ids:
            raise ValueError("working_memory_line_missing_record_id")
        unknown = [record_id for record_id in line.record_ids if record_id not in allowed_record_ids]
        if unknown:
            raise ValueError(f"working_memory_line_unknown_record_id:{unknown[0]}")


def included_record_ids(draft: WorkingMemoryDraft) -> tuple[str, ...]:
    """Return sorted unique record IDs cited by the draft."""
    seen: set[str] = set()
    for line in draft.summary:
        seen.update(line.record_ids)
    for section in draft.sections:
        for line in section.lines:
            seen.update(line.record_ids)
    return tuple(sorted(seen))


def render_working_memory_markdown(
    *,
    project: WorkingMemoryProject,
    generated_at: str,
    records_included: int,
    changed_since_generation: int,
    draft: WorkingMemoryDraft,
) -> str:
    """Render Working Memory markdown with freshness metadata and citations."""
    lines = [
        "# Working Memory",
        "",
        "> Generated from Lerim SQLite context. This markdown is a derived view, not the source of truth.",
        "",
        f"- Project: `{project.name}` (`{project.identity.project_id}`)",
        f"- Generated: `{generated_at}` ({human_age(age_seconds_since(generated_at))})",
        f"- Records included: {records_included}",
        f"- Records changed since generation: {changed_since_generation}",
        "- Refresh: `lerim working-memory refresh`",
        "",
    ]
    if not draft.summary and not draft.sections:
        lines.extend(
            [
                "## Summary",
                "",
                "No active project context records exist yet. Run `lerim sync` and `lerim maintain` to build context.",
            ]
        )
        return "\n".join(lines[:MAX_LINE_COUNT]).rstrip() + "\n"

    lines.extend(["## Summary", ""])
    for item in draft.summary[:8]:
        lines.append(f"- {item.text.strip()} {format_citations(item.record_ids)}")
    for section in draft.sections:
        if not section.lines:
            continue
        lines.extend(["", f"## {section.title.strip()}", ""])
        for item in section.lines[:14]:
            lines.append(f"- {item.text.strip()} {format_citations(item.record_ids)}")
    return "\n".join(lines[:MAX_LINE_COUNT]).rstrip() + "\n"


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
    current_file: Path,
    run_folder: Path,
) -> dict[str, Any]:
    """Build the Working Memory manifest payload."""
    return {
        "run_id": run_id,
        "operation": WORKING_MEMORY_OPERATION,
        "status": status,
        "generated_at": generated_at,
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
    elif changed > 0:
        availability = "stale"
        action = "Read the file, then run `lerim working-memory refresh` if newest context matters."
    elif not manifest_exists:
        availability = "error"
        action = "Run `lerim working-memory refresh --force`."
    else:
        availability = "available"
        action = "Read with `lerim working-memory show`."
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


def result_to_dict(result: WorkingMemoryGenerationResult) -> dict[str, Any]:
    """Convert generation result dataclass to JSON-ready dict."""
    payload = asdict(result)
    payload["included_record_ids"] = list(result.included_record_ids)
    return payload
