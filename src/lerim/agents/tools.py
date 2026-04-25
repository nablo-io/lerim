"""Agent tools for Lerim's simplified DB-only context architecture."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_ai import ModelRetry, RunContext

from lerim.context import ContextStore, ProjectIdentity
from lerim.context.spec import (
    ALLOWED_FINDING_LEVELS,
    RECORD_TYPED_FIELDS,
    format_allowed_finding_levels,
    normalize_finding_level,
    normalize_record_kind,
    normalize_record_status,
    record_validation_message,
)

TRACE_MAX_LINES_PER_READ = 100
TRACE_MAX_LINE_BYTES = 5_000
TRACE_MAX_CHUNK_BYTES = 50_000
MODEL_CONTEXT_TOKEN_LIMIT = 200_000
CONTEXT_SOFT_PRESSURE_PCT = 0.60
CONTEXT_HARD_PRESSURE_PCT = 0.80
_TOKENS_PER_CHAR = 0.25

ContextKind = Literal[
    "decision",
    "preference",
    "constraint",
    "fact",
    "reference",
    "episode",
]
ContextStatus = Literal["active", "archived"]
ContextOrder = Literal["created_at", "updated_at", "valid_from"]
DetailLevel = Literal["concise", "detailed"]


class TraceFinding(BaseModel):
    """Structured extract finding captured during trace scanning."""

    model_config = ConfigDict(extra="forbid")

    theme: str = Field(description="Short theme label for the finding.")
    line: int = Field(ge=1, description="1-based trace line with supporting evidence.")
    quote: str = Field(description="Short verbatim evidence snippet from the trace.")
    level: str = Field(
        description=(
            "Signal level: use durable levels only for reusable project context. "
            "Use `implementation` for dead ends, discarded hypotheses, trace-local noise, "
            "and supporting evidence that should not become its own durable theme. "
            "Allowed levels: "
            f"{format_allowed_finding_levels()}."
        )
    )

    @field_validator("level")
    @classmethod
    def validate_level(cls, value: str) -> str:
        """Validate finding levels against the shared canonical spec."""
        normalized = normalize_finding_level(value)
        if normalized not in ALLOWED_FINDING_LEVELS:
            allowed = ", ".join(ALLOWED_FINDING_LEVELS)
            raise ValueError(f"level must be one of: {allowed}")
        return normalized


class ContextFilters(BaseModel):
    """Filters shared by context retrieval tools."""

    model_config = ConfigDict(extra="forbid")

    kind: ContextKind | None = Field(default=None, description="Context kind to match.")
    status: ContextStatus | None = Field(default=None, description="Record lifecycle status.")
    source_session_id: str | None = Field(
        default=None, description="Only records extracted from this source session."
    )
    created_since: str | None = Field(default=None, description="Inclusive created_at lower bound.")
    created_until: str | None = Field(default=None, description="Exclusive created_at upper bound.")
    updated_since: str | None = Field(default=None, description="Inclusive updated_at lower bound.")
    updated_until: str | None = Field(default=None, description="Exclusive updated_at upper bound.")
    valid_at: str | None = Field(default=None, description="Return records valid at this time.")
    include_archived: bool = Field(default=False, description="Include archived records.")


class SearchFilters(BaseModel):
    """Filters supported by semantic context search."""

    model_config = ConfigDict(extra="forbid")

    kind: ContextKind | None = Field(default=None, description="Context kind to match.")
    status: ContextStatus | None = Field(default=None, description="Record lifecycle status.")
    valid_at: str | None = Field(default=None, description="Return records valid at this time.")
    include_archived: bool = Field(default=False, description="Include archived records.")


class ContextDraft(BaseModel):
    """Context record payload for save_context and revise_context."""

    model_config = ConfigDict(extra="forbid")

    kind: ContextKind = Field(description="Context kind.")
    title: str = Field(description="Short reusable title.")
    body: str = Field(description="Canonical context text.")
    status: ContextStatus = Field(default="active", description="Record lifecycle status.")
    valid_from: str | None = Field(default=None, description="When this context became valid.")
    valid_until: str | None = Field(default=None, description="When this context stopped being valid.")
    decision: str | None = Field(default=None, description="Decision records only: chosen approach.")
    why: str | None = Field(default=None, description="Decision records only: rationale.")
    alternatives: str | None = Field(default=None, description="Decision records only: alternatives considered.")
    consequences: str | None = Field(default=None, description="Decision records only: practical effects.")
    user_intent: str | None = Field(default=None, description="Episode records only: session purpose.")
    what_happened: str | None = Field(default=None, description="Episode records only: session recap.")
    outcomes: str | None = Field(default=None, description="One short sentence with the result.")


@dataclass
class ContextDeps:
    """Dependencies and per-run state shared across tool calls."""

    context_db_path: Path
    project_identity: ProjectIdentity
    session_id: str
    project_ids: list[str] | None = None
    trace_path: Path | None = None
    run_folder: Path | None = None
    session_started_at: str = ""
    trace_total_lines: int = 0
    read_ranges: list[tuple[int, int]] = field(default_factory=list)
    notes: list[TraceFinding] = field(default_factory=list)
    pruned_offsets: set[int] = field(default_factory=set)
    last_context_tokens: int = 0
    last_context_fill_ratio: float = 0.0


def _store(ctx: RunContext[ContextDeps]) -> ContextStore:
    """Return the canonical context store for the current run."""
    store = ContextStore(ctx.deps.context_db_path)
    store.initialize()
    store.register_project(ctx.deps.project_identity)
    return store


def _source_session_started_at(ctx: RunContext[ContextDeps], store: ContextStore) -> str:
    """Return the source session start timestamp for record provenance."""
    explicit = str(ctx.deps.session_started_at or "").strip()
    if explicit:
        return explicit
    session_id = str(ctx.deps.session_id or "").strip()
    if not session_id:
        return ""
    with store.connect() as conn:
        row = conn.execute(
            """
            SELECT started_at
            FROM sessions
            WHERE session_id = ? AND project_id = ?
            """,
            (session_id, ctx.deps.project_identity.project_id),
        ).fetchone()
    if row is None:
        return ""
    return str(row["started_at"] or "").strip()


def _trace_lines(trace_path: Path) -> list[str]:
    """Read the current trace file into a list of lines."""
    return trace_path.read_text(encoding="utf-8").splitlines()


def _read_offsets(ctx: RunContext[ContextDeps]) -> list[int]:
    """Return unique trace-read offsets in order."""
    return sorted({int(start) for start, _end in ctx.deps.read_ranges})


def _older_read_offsets(ctx: RunContext[ContextDeps]) -> list[int]:
    """Return older read offsets, keeping the newest chunk in context."""
    offsets = _read_offsets(ctx)
    if len(offsets) <= 1:
        return []
    return offsets[:-1]


def _classify_context_pressure(fill_ratio: float) -> str:
    """Convert current fill ratio into a user-facing pressure label."""
    if fill_ratio >= CONTEXT_HARD_PRESSURE_PCT:
        return "hard"
    if fill_ratio >= CONTEXT_SOFT_PRESSURE_PCT:
        return "soft"
    return "normal"


def _require_note_or_prune_before_trace_read(
    ctx: RunContext[ContextDeps], offset: int
) -> None:
    """Gate additional trace reads based on current context pressure."""
    if offset <= 0:
        return
    fill_ratio = float(ctx.deps.last_context_fill_ratio or 0.0)
    if fill_ratio < CONTEXT_SOFT_PRESSURE_PCT:
        return
    older_offsets = _older_read_offsets(ctx)
    if not older_offsets:
        return
    pressure = _classify_context_pressure(fill_ratio)
    if not ctx.deps.notes:
        raise ModelRetry(
            f"Context pressure is already {pressure} ({fill_ratio:.0%} of the configured window). "
            "Call note_trace_findings first with the strongest durable and implementation findings from the chunks already read. "
            "Then continue reading."
        )
    missing_offsets = [
        offset for offset in older_offsets if offset not in ctx.deps.pruned_offsets
    ]
    if missing_offsets:
        lines_text = ", ".join(str(item + 1) for item in missing_offsets)
        raise ModelRetry(
            f"Context pressure is {pressure} ({fill_ratio:.0%} of the configured window). "
            "Prune older read_trace results before reading more so the context stays focused. "
            f"Call prune_trace_reads(start_lines=[{lines_text}]) now, then continue reading."
        )


def read_trace(
    ctx: RunContext[ContextDeps], start_line: int = 1, line_count: int = 100
) -> str:
    """Read numbered trace lines from the source session.

    Args:
        start_line: 1-based first line to read.
        line_count: Maximum lines to return, capped by Lerim.
    """
    trace_path = ctx.deps.trace_path
    if trace_path is None:
        return "Error: no trace path configured"
    lines = _trace_lines(trace_path)
    total = len(lines)
    ctx.deps.trace_total_lines = total
    offset = max(0, int(start_line) - 1)
    if offset >= total and total > 0:
        raise ModelRetry(
            f"read_trace start_line {start_line} is past the end of the trace. "
            f"Use a start_line from 1 to {max(1, total)}."
        )
    if line_count <= 0 or line_count > TRACE_MAX_LINES_PER_READ:
        line_count = TRACE_MAX_LINES_PER_READ
    _require_note_or_prune_before_trace_read(ctx, offset)
    chunk = lines[offset : offset + line_count]
    safe_chunk: list[str] = []
    running_bytes = 0
    for line in chunk:
        if len(line) > TRACE_MAX_LINE_BYTES:
            dropped = len(line) - TRACE_MAX_LINE_BYTES
            line = (
                line[:TRACE_MAX_LINE_BYTES]
                + f" ... [truncated {dropped} chars from this line]"
            )
        line_bytes = len(line.encode("utf-8"))
        if running_bytes + line_bytes > TRACE_MAX_CHUNK_BYTES:
            break
        safe_chunk.append(line)
        running_bytes += line_bytes
    numbered = [
        f"{offset + index + 1}\t{line}" for index, line in enumerate(safe_chunk)
    ]
    last_line = offset + len(safe_chunk)
    ctx.deps.read_ranges.append((int(offset), int(last_line)))
    header = f"[{total} lines, showing {offset + 1}-{last_line}]"
    if last_line < total:
        header += (
            f" — {total - last_line} more lines, call "
            f"read_trace(start_line={last_line + 1}, line_count={TRACE_MAX_LINES_PER_READ}) for the next chunk"
        )
    return header + "\n" + "\n".join(numbered)


def search_context(
    ctx: RunContext[ContextDeps],
    query: str,
    filters: SearchFilters | None = None,
    limit: int = 8,
) -> str:
    """Search saved context by meaning.

    Args:
        query: Natural-language search text.
        filters: Optional kind, status, valid_at, or archived-scope filters.
        limit: Maximum hits to return.
    """
    store = _store(ctx)
    trimmed_query = str(query or "").strip()
    if not trimmed_query or trimmed_query == "*":
        raise ModelRetry(
            "search_context needs a real text query. "
            "Use list_context when you want to browse recent or filtered context."
        )
    active_filters = _search_filters(filters)
    normalized_kinds = [active_filters["kind"]] if active_filters["kind"] else None
    normalized_statuses = [active_filters["status"]] if active_filters["status"] else None
    effective_include_archived = bool(
        active_filters["include_archived"] or active_filters["valid_at"]
    )
    hits = store.search(
        project_ids=ctx.deps.project_ids or [ctx.deps.project_identity.project_id],
        query=trimmed_query,
        kind_filters=normalized_kinds,
        statuses=normalized_statuses,
        valid_at=active_filters["valid_at"],
        include_archived=effective_include_archived,
        limit=max(1, min(int(limit), 8)),
    )
    payload = {
        "count": len(hits),
        "hits": [
            {
                "record_id": hit.record_id,
                "kind": hit.kind,
                "title": hit.title,
                "body_preview": hit.body[:280],
                "status": hit.status,
                "created_at": hit.created_at,
                "updated_at": hit.updated_at,
                "valid_from": hit.valid_from,
                "valid_until": hit.valid_until,
                "score": round(hit.score, 6),
                "sources": hit.sources,
            }
            for hit in hits
        ],
    }
    return json.dumps(payload, ensure_ascii=True, indent=2)


def list_context(
    ctx: RunContext[ContextDeps],
    filters: ContextFilters | None = None,
    order_by: ContextOrder = "updated_at",
    limit: int = 8,
) -> str:
    """List saved context with exact filters and ordering.

    Args:
        filters: Optional kind, status, session, time, or archived-scope filters.
        order_by: Timestamp field used for newest-first ordering.
        limit: Maximum rows to return.
    """
    store = _store(ctx)
    active_filters = _filters(filters)
    order = str(order_by or "updated_at").strip().lower()
    if order not in {"created_at", "updated_at", "valid_from"}:
        raise ModelRetry(
            "list_context order_by must be one of: created_at, updated_at, valid_from."
        )
    effective_include_archived = bool(
        active_filters["include_archived"] or active_filters["valid_at"]
    )
    status = active_filters["status"]
    if status is None and not effective_include_archived:
        status = "active"
    listing = store.query(
        entity="records",
        mode="list",
        project_ids=ctx.deps.project_ids or [ctx.deps.project_identity.project_id],
        kind=active_filters["kind"],
        status=status,
        source_session_id=active_filters["source_session_id"],
        created_since=active_filters["created_since"],
        created_until=active_filters["created_until"],
        updated_since=active_filters["updated_since"],
        updated_until=active_filters["updated_until"],
        valid_at=active_filters["valid_at"],
        order_by=order,
        limit=max(1, min(int(limit), 50)),
        include_total=False,
        include_archived=effective_include_archived,
    )
    payload = {
        "count": listing["count"],
        "records": [
            {
                "record_id": row["record_id"],
                "kind": row["kind"],
                "title": row["title"],
                "body_preview": str(row["body"])[:280],
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "valid_from": row["valid_from"],
                "valid_until": row["valid_until"],
                "superseded_by_record_id": row["superseded_by_record_id"],
            }
            for row in listing["rows"]
        ],
    }
    return json.dumps(payload, ensure_ascii=True, indent=2)


def count_context(
    ctx: RunContext[ContextDeps],
    filters: ContextFilters | None = None,
) -> str:
    """Count saved context records with exact filters.

    Args:
        filters: Optional kind, status, session, time, or archived-scope filters.
    """
    store = _store(ctx)
    active_filters = _filters(filters)
    effective_include_archived = bool(
        active_filters["include_archived"] or active_filters["valid_at"]
    )
    status = active_filters["status"]
    if status is None and not effective_include_archived:
        status = "active"
    payload = store.query(
        entity="records",
        mode="count",
        project_ids=ctx.deps.project_ids or [ctx.deps.project_identity.project_id],
        kind=active_filters["kind"],
        status=status,
        source_session_id=active_filters["source_session_id"],
        created_since=active_filters["created_since"],
        created_until=active_filters["created_until"],
        updated_since=active_filters["updated_since"],
        updated_until=active_filters["updated_until"],
        valid_at=active_filters["valid_at"],
        include_archived=effective_include_archived,
    )
    return json.dumps(payload, ensure_ascii=True, indent=2)


def get_context(
    ctx: RunContext[ContextDeps],
    record_ids: list[str],
    include_versions: bool = False,
    detail: DetailLevel = "detailed",
) -> str:
    """Fetch saved context records by ID.

    Args:
        record_ids: Record IDs returned by search_context or list_context.
        include_versions: Include prior versions of each record.
        detail: Concise or detailed record payload.
    """
    mode = str(detail or "concise").strip().lower()
    if mode not in {"concise", "detailed"}:
        raise ModelRetry(
            "get_context detail must be 'concise' or 'detailed'."
        )
    if not record_ids:
        return json.dumps({"count": 0, "records": []}, indent=2)
    store = _store(ctx)
    allowed_project_ids = ctx.deps.project_ids or [ctx.deps.project_identity.project_id]
    records: list[dict[str, Any]] = []
    for record_id in record_ids:
        record = store.fetch_record(
            record_id,
            project_ids=allowed_project_ids,
            include_versions=bool(include_versions),
        )
        if record is None:
            continue
        if mode == "concise":
            records.append(
                {
                    "record_id": record["record_id"],
                    "kind": record["kind"],
                    "title": record["title"],
                    "body": record["body"][:2000],
                    "status": record["status"],
                    "created_at": record["created_at"],
                    "updated_at": record["updated_at"],
                    "valid_from": record["valid_from"],
                    "valid_until": record["valid_until"],
                    "decision": record["decision"],
                    "why": record["why"],
                    "alternatives": record["alternatives"],
                    "consequences": record["consequences"],
                    "user_intent": record["user_intent"],
                    "what_happened": record["what_happened"],
                    "outcomes": record["outcomes"],
                    "superseded_by_record_id": record["superseded_by_record_id"],
                }
            )
            continue
        records.append(record)
    return json.dumps(
        {"count": len(records), "records": records}, ensure_ascii=True, indent=2
    )


def _normalize_kind(kind: str) -> str:
    """Normalize kind names before store validation."""
    return normalize_record_kind(kind)


def _normalize_status(status: str) -> str:
    """Normalize status names before store validation."""
    return normalize_record_status(status)


def _filters(filters: ContextFilters | None) -> dict[str, Any]:
    """Normalize optional retrieval filters into store query arguments."""
    if filters is None:
        filters = ContextFilters()
    data = filters.model_dump()
    data["kind"] = _normalize_kind(data.get("kind") or "") or None
    data["status"] = (
        _normalize_status(data.get("status")) if data.get("status") else None
    )
    for key, value in list(data.items()):
        if isinstance(value, str):
            data[key] = value.strip() or None
    data["include_archived"] = bool(data.get("include_archived"))
    return data


def _search_filters(filters: SearchFilters | None) -> dict[str, Any]:
    """Normalize optional semantic search filters into store search arguments."""
    if filters is None:
        filters = SearchFilters()
    data = filters.model_dump()
    data["kind"] = _normalize_kind(data.get("kind") or "") or None
    data["status"] = (
        _normalize_status(data.get("status")) if data.get("status") else None
    )
    if isinstance(data.get("valid_at"), str):
        data["valid_at"] = data["valid_at"].strip() or None
    data["include_archived"] = bool(data.get("include_archived"))
    return data


def _context_changes(context: ContextDraft) -> dict[str, Any]:
    """Convert one typed context draft into store fields."""
    data = context.model_dump()
    changes: dict[str, Any] = {
        "kind": _normalize_kind(data.pop("kind")),
        "title": data.pop("title"),
        "body": data.pop("body"),
        "status": _normalize_status(data.pop("status")),
        "valid_from": data.pop("valid_from") or None,
        "valid_until": data.pop("valid_until") or None,
    }
    for field_name in RECORD_TYPED_FIELDS:
        changes[field_name] = data.get(field_name) or None
    return changes


def _maybe_raise_record_retry(exc: ValueError) -> None:
    """Convert record-quality validation errors into guided model retries."""
    code = str(exc or "").strip()
    if code == "no_changes":
        raise ModelRetry(
            "revise_context needs at least one meaningful field change. "
            "Fetch the record, compare it to your intended update, then retry only if something should change."
        ) from exc
    message = record_validation_message(code)
    if message:
        raise ModelRetry(message) from exc


def _trace_line_count(ctx: RunContext[ContextDeps]) -> int:
    """Return and cache the current trace line count."""
    trace_path = ctx.deps.trace_path
    if trace_path is None:
        return 0
    total_lines = int(ctx.deps.trace_total_lines)
    if total_lines > 0:
        return total_lines
    try:
        total_lines = sum(1 for _ in trace_path.open("r", encoding="utf-8"))
    except OSError:
        return 0
    ctx.deps.trace_total_lines = total_lines
    return total_lines


def _require_trace_ready_for_write(ctx: RunContext[ContextDeps]) -> None:
    """Require trace coverage and note discipline before extract writes."""
    trace_path = ctx.deps.trace_path
    if trace_path is None:
        return
    total_lines = _trace_line_count(ctx)
    if total_lines <= 0:
        return
    if not ctx.deps.read_ranges:
        raise ModelRetry(
            "No trace lines have been read yet. "
            f"Call read_trace(start_line=1, line_count={TRACE_MAX_LINES_PER_READ}) "
            "before you create or update records."
        )
    next_offset = _first_uncovered_offset(ctx.deps.read_ranges, total_lines)
    if next_offset is not None:
        raise ModelRetry(
            "Unread trace lines remain. "
            f"Continue reading with read_trace(start_line={next_offset + 1}, line_count={TRACE_MAX_LINES_PER_READ}) "
            "before you create or update records."
        )
    if total_lines > TRACE_MAX_LINES_PER_READ and not ctx.deps.notes:
        raise ModelRetry(
            "This trace is longer than one read_trace chunk. "
            "Call note_trace_findings first with the strongest durable and implementation findings, "
            "then create or update records."
        )


def _first_uncovered_offset(
    read_ranges: list[tuple[int, int]], total_lines: int
) -> int | None:
    """Return the first unread trace offset, or None when coverage is complete."""
    if total_lines <= 0:
        return None
    merged: list[tuple[int, int]] = []
    for start, end in sorted(read_ranges):
        start = max(0, int(start))
        end = max(start, int(end))
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    expected = 0
    for start, end in merged:
        if start > expected:
            return expected
        expected = max(expected, end)
        if expected >= total_lines:
            return None
    if expected < total_lines:
        return expected
    return None


def save_context(ctx: RunContext[ContextDeps], context: ContextDraft) -> str:
    """Save one typed context record.

    Args:
        context: Complete context payload to persist.
    """
    _require_trace_ready_for_write(ctx)
    changes = _context_changes(context)
    store = _store(ctx)
    project_id = ctx.deps.project_identity.project_id
    session_id = ctx.deps.session_id
    source_started_at = _source_session_started_at(ctx, store)
    try:
        result = store.create_record(
            project_id=project_id,
            session_id=session_id,
            kind=changes["kind"],
            title=changes["title"],
            body=changes["body"],
            status=changes["status"],
            created_at=source_started_at or None,
            valid_from=changes["valid_from"],
            valid_until=changes["valid_until"],
            decision=changes["decision"],
            why=changes["why"],
            alternatives=changes["alternatives"],
            consequences=changes["consequences"],
            user_intent=changes["user_intent"],
            what_happened=changes["what_happened"],
            outcomes=changes["outcomes"],
        )
    except ValueError as exc:
        _maybe_raise_record_retry(exc)
        raise
    return json.dumps({"ok": True, "result": result}, ensure_ascii=True, indent=2)


def revise_context(
    ctx: RunContext[ContextDeps],
    record_id: str,
    context: ContextDraft,
    reason: str,
) -> str:
    """Revise an existing context record with a complete improved payload.

    Args:
        record_id: Existing record to revise.
        context: Complete corrected context payload after revision.
        reason: Short reason for the revision.
    """
    _require_trace_ready_for_write(ctx)
    changes = _context_changes(context)
    store = _store(ctx)
    existing = store.fetch_record(
        str(record_id or "").strip(),
        project_ids=ctx.deps.project_ids or [ctx.deps.project_identity.project_id],
        include_versions=False,
    )
    if existing is None:
        raise ModelRetry(
            "The record to revise does not exist in the current project scope. "
            "Search or list context again, fetch the target, then retry with its record_id."
        )
    if changes["kind"] != existing["kind"]:
        raise ModelRetry(
            "revise_context cannot change a record's kind. "
            "Create a new context record when the corrected context belongs to a different kind."
        )
    explicit_fields = context.model_fields_set
    for field_name in ("status", "valid_from", "valid_until"):
        if field_name not in explicit_fields:
            changes[field_name] = existing[field_name]
    try:
        result = store.update_record(
            record_id=str(record_id or "").strip(),
            session_id=ctx.deps.session_id,
            project_ids=ctx.deps.project_ids or [ctx.deps.project_identity.project_id],
            changes=changes,
            change_reason=str(reason or "").strip() or None,
        )
    except ValueError as exc:
        _maybe_raise_record_retry(exc)
        raise
    return json.dumps({"ok": True, "result": result}, ensure_ascii=True, indent=2)


def archive_context(
    ctx: RunContext[ContextDeps],
    record_id: str,
    reason: str = "",
) -> str:
    """Archive low-value or obsolete context.

    Args:
        record_id: Record to archive.
        reason: Short reason for archiving.
    """
    store = _store(ctx)
    try:
        result = store.archive_record(
            record_id=str(record_id or "").strip(),
            session_id=ctx.deps.session_id,
            project_ids=ctx.deps.project_ids or [ctx.deps.project_identity.project_id],
            reason=str(reason or "").strip() or None,
        )
    except ValueError as exc:
        if str(exc).startswith("refuse_archive_recent_active_record:"):
            raise ModelRetry(
                "Do not archive a fresh active fact or decision directly. "
                "Use supersede_context when it is a duplicate, or leave it active."
            ) from exc
        raise
    return json.dumps({"ok": True, "result": result}, ensure_ascii=True, indent=2)


def supersede_context(
    ctx: RunContext[ContextDeps],
    record_id: str,
    replacement_record_id: str,
    reason: str = "",
    valid_until: str = "",
) -> str:
    """Mark one context record as replaced by another.

    Args:
        record_id: Older record being replaced.
        replacement_record_id: Newer record that replaces it.
        reason: Short reason for supersession.
        valid_until: Optional validity end timestamp for the older record.
    """
    store = _store(ctx)
    try:
        result = store.supersede_record(
            record_id=str(record_id or "").strip(),
            session_id=ctx.deps.session_id,
            project_ids=ctx.deps.project_ids or [ctx.deps.project_identity.project_id],
            replacement_record_id=str(replacement_record_id or "").strip(),
            reason=str(reason or "").strip() or None,
            valid_until=str(valid_until or "").strip() or None,
        )
    except ValueError as exc:
        message = str(exc)
        if message.startswith("replacement_record_not_found:"):
            raise ModelRetry(
                "The replacement record does not exist in the current project scope. "
                "Search or list context again, fetch the replacement, then retry with its record_id."
            ) from exc
        if message.startswith("record_not_found:") or message.startswith("record_out_of_scope:"):
            raise ModelRetry(
                "The record to supersede does not exist in the current project scope. "
                "Search or list context again, fetch the target, then retry with its record_id."
            ) from exc
        _maybe_raise_record_retry(exc)
        raise
    return json.dumps({"ok": True, "result": result}, ensure_ascii=True, indent=2)


def note_trace_findings(
    ctx: RunContext[ContextDeps], findings: list[TraceFinding]
) -> str:
    """Record findings from trace chunks already read.

    Args:
        findings: Durable and implementation findings from read trace lines.
    """
    if not findings:
        return "No findings recorded."
    ctx.deps.notes.extend(findings)
    total = len(ctx.deps.notes)
    return f"Noted {len(findings)} findings (total {total} so far)."


def prune_trace_reads(ctx: RunContext[ContextDeps], start_lines: list[int]) -> str:
    """Prune earlier read_trace results after findings are noted.

    Args:
        start_lines: 1-based start lines from earlier read_trace calls.
    """
    if not start_lines:
        return "No trace reads to prune."
    read_offsets = set(_read_offsets(ctx))
    requested = {max(0, int(line) - 1) for line in start_lines}
    unknown_offsets = sorted(requested - read_offsets)
    if unknown_offsets:
        known = ", ".join(str(offset + 1) for offset in sorted(read_offsets)) or "none"
        bad = ", ".join(str(offset + 1) for offset in unknown_offsets)
        raise ModelRetry(
            f"Cannot prune unread trace start line(s): {bad}. "
            f"Only previously read start lines can be pruned; read start lines: {known}."
        )
    before = len(ctx.deps.pruned_offsets)
    ctx.deps.pruned_offsets.update(requested)
    added = len(ctx.deps.pruned_offsets) - before
    return (
        f"Pruned {added} new trace read(s); total pruned: {len(ctx.deps.pruned_offsets)}."
    )


def compute_request_budget(trace_path: Path) -> int:
    """Scale extract request budget from trace size.

    Real traces need more headroom than the old 20-turn floor allowed,
    even when the trace itself is short. Keep the budget adaptive, but
    bias toward successful completion over premature request-limit exits.
    """
    try:
        line_count = sum(1 for _ in trace_path.open("r", encoding="utf-8"))
    except OSError:
        return 40
    if line_count <= 200:
        return 40
    if line_count >= 5000:
        return 100
    return max(40, min(100, int(40 + (line_count / 100.0))))

if __name__ == "__main__":
    """Run a small smoke check for request budget logic."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        trace_path = Path(tmp) / "trace.jsonl"
        trace_path.write_text(
            "\n".join(f"line {i}" for i in range(240)), encoding="utf-8"
        )
        budget = compute_request_budget(trace_path)
        assert budget >= 20
        print("agent tools: self-test passed")
