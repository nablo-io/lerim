"""Agent tools for Lerim's simplified DB-only context architecture."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic import ValidationError
from pydantic_ai import ModelRetry, RunContext

from lerim.context import ContextStore, ProjectIdentity
from lerim.context.spec import (
    ALLOWED_FINDING_LEVELS,
    ALLOWED_KINDS,
    ALLOWED_STATUSES,
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


@dataclass
class ContextDeps:
    """Dependencies and per-run state shared across tool calls."""

    context_db_path: Path
    project_identity: ProjectIdentity
    session_id: str
    project_ids: list[str] | None = None
    trace_path: Path | None = None
    session_started_at: str = ""
    trace_total_lines: int = 0
    read_ranges: list[tuple[int, int]] = field(default_factory=list)
    notes: list[TraceFinding] = field(default_factory=list)
    findings_checked: bool = False
    pruned_offsets: set[int] = field(default_factory=set)
    fetched_context_record_ids: set[str] = field(default_factory=set)
    last_context_tokens: int = 0
    last_context_fill_ratio: float = 0.0


def _store(ctx: RunContext[ContextDeps]) -> ContextStore:
    """Return the canonical context store for the current run."""
    store = ContextStore(ctx.deps.context_db_path)
    store.initialize()
    store.register_project(ctx.deps.project_identity)
    return store


def _source_session_started_at(
    ctx: RunContext[ContextDeps], store: ContextStore
) -> str:
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


def _auto_prune_before_trace_read(
    ctx: RunContext[ContextDeps], offset: int
) -> list[int]:
    """Prune old trace reads under context pressure before returning more trace."""
    if offset <= 0:
        return []
    fill_ratio = float(ctx.deps.last_context_fill_ratio or 0.0)
    if fill_ratio < CONTEXT_SOFT_PRESSURE_PCT:
        return []
    older_offsets = _older_read_offsets(ctx)
    if not older_offsets:
        return []
    before = set(ctx.deps.pruned_offsets)
    ctx.deps.pruned_offsets.update(older_offsets)
    return sorted(ctx.deps.pruned_offsets - before)


def read_trace(
    ctx: RunContext[ContextDeps], start_line: int = 1, line_count: int = 100
) -> str:
    """Read the next numbered trace chunk from the source session.

    Args:
        start_line: 1-based first line to read. After scanning starts,
            overlapping or out-of-order values advance to the first unread line.
        line_count: Maximum lines to return, capped by Lerim.
    """
    trace_path = ctx.deps.trace_path
    if trace_path is None:
        return "Error: no trace path configured"
    lines = _trace_lines(trace_path)
    total = len(lines)
    ctx.deps.trace_total_lines = total
    offset = max(0, int(start_line) - 1)
    adjusted_from: int | None = None
    next_unread = _first_uncovered_offset(ctx.deps.read_ranges, total)
    if next_unread is None and ctx.deps.read_ranges:
        return (
            f"[{total} lines, trace coverage complete] "
            "All trace lines have already been read. Save the episode and any durable records now."
        )
    if next_unread is not None and ctx.deps.read_ranges and offset != next_unread:
        adjusted_from = offset
        offset = next_unread
    if offset >= total and total > 0:
        raise ModelRetry(
            f"read_trace start_line {start_line} is past the end of the trace. "
            f"Use a start_line from 1 to {max(1, total)}."
        )
    if line_count <= 0 or line_count > TRACE_MAX_LINES_PER_READ:
        line_count = TRACE_MAX_LINES_PER_READ
    auto_pruned = _auto_prune_before_trace_read(ctx, offset)
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
    if adjusted_from is not None:
        header += f" [advanced from requested line {adjusted_from + 1} to first unread line {offset + 1}]"
    if auto_pruned:
        pruned_lines = ", ".join(str(item + 1) for item in auto_pruned)
        header += f" [auto-pruned older read_trace start lines: {pruned_lines}]"
    if last_line < total:
        header += (
            f" — {total - last_line} more lines, call "
            f"read_trace(start_line={last_line + 1}, line_count={TRACE_MAX_LINES_PER_READ}) for the next chunk"
        )
    return header + "\n" + "\n".join(numbered)


def search_context(
    ctx: RunContext[ContextDeps],
    query: str,
    kind: str | None = None,
    status: str | None = None,
    valid_at: str | None = None,
    include_archived: bool | str = False,
    limit: int = 8,
) -> str:
    """Search saved context by meaning.

    Args:
        query: Natural-language search text.
        kind: Optional context kind to match.
        status: Optional record lifecycle status.
        valid_at: Optional timestamp for historical lookup.
        include_archived: Include archived records.
        limit: Maximum hits to return.
    """
    store = _store(ctx)
    trimmed_query = str(query or "").strip()
    if not trimmed_query or trimmed_query == "*":
        raise ModelRetry(
            "search_context needs a real text query. "
            "Use list_context when you want to browse recent or filtered context."
        )
    active_filters = _filters(
        kind=kind,
        status=status,
        valid_at=valid_at,
        include_archived=include_archived,
    )
    normalized_kinds = [active_filters["kind"]] if active_filters["kind"] else None
    normalized_statuses = (
        [active_filters["status"]] if active_filters["status"] else None
    )
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
    kind: str | None = None,
    status: str | None = None,
    source_session_id: str | None = None,
    created_since: str | None = None,
    created_until: str | None = None,
    updated_since: str | None = None,
    updated_until: str | None = None,
    valid_at: str | None = None,
    include_archived: bool | str = False,
    order_by: str = "updated_at",
    limit: int = 8,
) -> str:
    """List saved context with exact filters and ordering.

    Args:
        kind: Optional context kind to match.
        status: Optional record lifecycle status.
        source_session_id: Optional source session filter.
        created_since: Optional inclusive created_at lower bound.
        created_until: Optional exclusive created_at upper bound.
        updated_since: Optional inclusive updated_at lower bound.
        updated_until: Optional exclusive updated_at upper bound.
        valid_at: Optional timestamp for historical lookup.
        include_archived: Include archived records.
        order_by: Timestamp field used for newest-first ordering.
        limit: Maximum rows to return.
    """
    store = _store(ctx)
    active_filters = _filters(
        kind=kind,
        status=status,
        source_session_id=source_session_id,
        created_since=created_since,
        created_until=created_until,
        updated_since=updated_since,
        updated_until=updated_until,
        valid_at=valid_at,
        include_archived=include_archived,
    )
    order = str(_clean_scalar(order_by) or "updated_at").strip().lower()
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
    kind: str | None = None,
    status: str | None = None,
    source_session_id: str | None = None,
    created_since: str | None = None,
    created_until: str | None = None,
    updated_since: str | None = None,
    updated_until: str | None = None,
    valid_at: str | None = None,
    include_archived: bool | str = False,
) -> str:
    """Count saved context records with exact filters.

    Args:
        kind: Optional context kind to match.
        status: Optional record lifecycle status.
        source_session_id: Optional source session filter.
        created_since: Optional inclusive created_at lower bound.
        created_until: Optional exclusive created_at upper bound.
        updated_since: Optional inclusive updated_at lower bound.
        updated_until: Optional exclusive updated_at upper bound.
        valid_at: Optional timestamp for historical lookup.
        include_archived: Include archived records.
    """
    store = _store(ctx)
    active_filters = _filters(
        kind=kind,
        status=status,
        source_session_id=source_session_id,
        created_since=created_since,
        created_until=created_until,
        updated_since=updated_since,
        updated_until=updated_until,
        valid_at=valid_at,
        include_archived=include_archived,
    )
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
        raise ModelRetry("get_context detail must be 'concise' or 'detailed'.")
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
        fetched_record_id = str(record["record_id"])
        ctx.deps.fetched_context_record_ids.add(fetched_record_id)
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


def _require_fetched_context_records(
    ctx: RunContext[ContextDeps], tool_name: str, *record_ids: str
) -> list[str]:
    """Require mutating context tools to operate only on fetched records."""
    normalized_ids = [str(record_id or "").strip() for record_id in record_ids]
    missing_ids = [
        record_id
        for record_id in normalized_ids
        if not record_id or record_id not in ctx.deps.fetched_context_record_ids
    ]
    if missing_ids:
        missing_text = ", ".join(record_id or "<blank>" for record_id in missing_ids)
        raise ModelRetry(
            f"{tool_name} can only mutate records fetched by get_context in this run. "
            f"Fetch the full record(s) first, then retry. Unfetched record_id(s): {missing_text}."
        )
    return normalized_ids


def _normalize_kind(kind: str) -> str:
    """Normalize kind names before store validation."""
    return normalize_record_kind(kind)


def _normalize_status(status: str) -> str:
    """Normalize status names before store validation."""
    return normalize_record_status(status)


def _clean_scalar(value: Any) -> Any:
    """Strip accidental JSON quoting from simple tool arguments."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    for _ in range(2):
        if len(text) < 2:
            return text or None
        if text[0] == '"' and text[-1] == '"':
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                return text
            if isinstance(decoded, str):
                text = decoded.strip()
                continue
            return decoded
        if text[0] == "'" and text[-1] == "'":
            text = text[1:-1].strip()
            continue
        return text or None
    return text or None


def _clean_bool(value: bool | str | None) -> bool:
    """Normalize boolean-ish tool arguments."""
    value = _clean_scalar(value)
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return bool(text)


def _filters(
    *,
    kind: str | None = None,
    status: str | None = None,
    source_session_id: str | None = None,
    created_since: str | None = None,
    created_until: str | None = None,
    updated_since: str | None = None,
    updated_until: str | None = None,
    valid_at: str | None = None,
    include_archived: bool | str = False,
) -> dict[str, Any]:
    """Normalize optional retrieval filters into store query arguments."""
    data = {
        "kind": _clean_scalar(kind),
        "status": _clean_scalar(status),
        "source_session_id": _clean_scalar(source_session_id),
        "created_since": _clean_scalar(created_since),
        "created_until": _clean_scalar(created_until),
        "updated_since": _clean_scalar(updated_since),
        "updated_until": _clean_scalar(updated_until),
        "valid_at": _clean_scalar(valid_at),
        "include_archived": _clean_bool(include_archived),
    }
    data["kind"] = _normalize_kind(str(data["kind"] or "")) or None
    data["status"] = (
        _normalize_status(str(data["status"])) if data.get("status") else None
    )
    return data


def _context_changes(
    *,
    kind: str,
    title: str,
    body: str,
    status: str | None = "active",
    valid_from: str | None = None,
    valid_until: str | None = None,
    decision: str | None = None,
    why: str | None = None,
    alternatives: str | None = None,
    consequences: str | None = None,
    user_intent: str | None = None,
    what_happened: str | None = None,
    outcomes: str | None = None,
) -> dict[str, Any]:
    """Convert flat tool arguments into store fields."""
    data = {
        "kind": _clean_scalar(kind),
        "title": title,
        "body": body,
        "status": _clean_scalar(status),
        "valid_from": _clean_scalar(valid_from),
        "valid_until": _clean_scalar(valid_until),
        "decision": decision,
        "why": why,
        "alternatives": alternatives,
        "consequences": consequences,
        "user_intent": user_intent,
        "what_happened": what_happened,
        "outcomes": outcomes,
    }
    changes: dict[str, Any] = {
        "kind": _normalize_kind(str(data["kind"] or "")),
        "title": str(data["title"] or ""),
        "body": str(data["body"] or ""),
        "status": _normalize_status(str(data["status"] or "active")),
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
    if code.startswith("invalid_kind:"):
        raise ModelRetry(
            f"Record kind is invalid. Use one of: {', '.join(ALLOWED_KINDS)}."
        ) from exc
    if code.startswith("invalid_status:"):
        raise ModelRetry(
            f"Record status must be one of: {', '.join(ALLOWED_STATUSES)}."
        ) from exc


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


def _require_trace_ready_for_write(
    ctx: RunContext[ContextDeps], changes: dict[str, Any] | None = None
) -> None:
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
    is_archived_episode = (
        changes is not None
        and changes.get("kind") == "episode"
        and changes.get("status") == "archived"
    )
    if (
        total_lines > TRACE_MAX_LINES_PER_READ
        and not ctx.deps.notes
        and not ctx.deps.findings_checked
        and not is_archived_episode
    ):
        raise ModelRetry(
            "This trace is longer than one read_trace chunk. "
            "Call note_trace_findings once for each strongest durable or implementation finding, "
            "or call it with no arguments if the full trace has no reusable signal, then create or update records."
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


def save_context(
    ctx: RunContext[ContextDeps],
    kind: str,
    title: str,
    body: str,
    status: str = "active",
    valid_from: str | None = None,
    valid_until: str | None = None,
    decision: str | None = None,
    why: str | None = None,
    alternatives: str | None = None,
    consequences: str | None = None,
    user_intent: str | None = None,
    what_happened: str | None = None,
    outcomes: str | None = None,
) -> str:
    """Save one context record.

    For kind="episode", provide both user_intent and what_happened.
    For kind="decision", provide both decision and why.
    """
    changes = _context_changes(
        kind=kind,
        title=title,
        body=body,
        status=status,
        valid_from=valid_from,
        valid_until=valid_until,
        decision=decision,
        why=why,
        alternatives=alternatives,
        consequences=consequences,
        user_intent=user_intent,
        what_happened=what_happened,
        outcomes=outcomes,
    )
    _require_trace_ready_for_write(ctx, changes)
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
    reason: str,
    kind: str,
    title: str,
    body: str,
    status: str | None = "",
    valid_from: str | None = "",
    valid_until: str | None = "",
    decision: str | None = None,
    why: str | None = None,
    alternatives: str | None = None,
    consequences: str | None = None,
    user_intent: str | None = None,
    what_happened: str | None = None,
    outcomes: str | None = None,
) -> str:
    """Revise an existing context record with a complete improved payload."""
    [target_record_id] = _require_fetched_context_records(
        ctx, "revise_context", record_id
    )
    store = _store(ctx)
    existing = store.fetch_record(
        target_record_id,
        project_ids=ctx.deps.project_ids or [ctx.deps.project_identity.project_id],
        include_versions=False,
    )
    if existing is None:
        raise ModelRetry(
            "The record to revise does not exist in the current project scope. "
            "Search or list context again, fetch the target, then retry with its record_id."
        )
    status_value = _clean_scalar(status)
    valid_from_value = _clean_scalar(valid_from)
    valid_until_value = _clean_scalar(valid_until)
    changes = _context_changes(
        kind=kind,
        title=title,
        body=body,
        status=status_value if status_value is not None else existing["status"],
        valid_from=(
            None
            if valid_from is None
            else valid_from_value
            if valid_from_value is not None
            else existing["valid_from"]
        ),
        valid_until=(
            None
            if valid_until is None
            else valid_until_value
            if valid_until_value is not None
            else existing["valid_until"]
        ),
        decision=decision,
        why=why,
        alternatives=alternatives,
        consequences=consequences,
        user_intent=user_intent,
        what_happened=what_happened,
        outcomes=outcomes,
    )
    _require_trace_ready_for_write(ctx, changes)
    if changes["kind"] != existing["kind"]:
        raise ModelRetry(
            "revise_context cannot change a record's kind. "
            "Create a new context record when the corrected context belongs to a different kind."
        )
    try:
        result = store.update_record(
            record_id=target_record_id,
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
    [target_record_id] = _require_fetched_context_records(
        ctx, "archive_context", record_id
    )
    store = _store(ctx)
    try:
        result = store.archive_record(
            record_id=target_record_id,
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
    target_record_id, target_replacement_record_id = _require_fetched_context_records(
        ctx, "supersede_context", record_id, replacement_record_id
    )
    store = _store(ctx)
    try:
        result = store.supersede_record(
            record_id=target_record_id,
            session_id=ctx.deps.session_id,
            project_ids=ctx.deps.project_ids or [ctx.deps.project_identity.project_id],
            replacement_record_id=target_replacement_record_id,
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
        if message.startswith("record_not_found:") or message.startswith(
            "record_out_of_scope:"
        ):
            raise ModelRetry(
                "The record to supersede does not exist in the current project scope. "
                "Search or list context again, fetch the target, then retry with its record_id."
            ) from exc
        _maybe_raise_record_retry(exc)
        raise
    return json.dumps({"ok": True, "result": result}, ensure_ascii=True, indent=2)


def note_trace_findings(
    ctx: RunContext[ContextDeps],
    theme: str = "",
    line: int | str = 0,
    quote: str = "",
    level: str = "implementation",
) -> str:
    """Record one trace finding with line evidence, or call with no args for none."""
    if not str(theme or "").strip() and not str(quote or "").strip() and not line:
        ctx.deps.findings_checked = True
        return "No findings recorded; trace findings checkpoint saved."
    try:
        line_number = int(_clean_scalar(line) or 0)
    except (TypeError, ValueError) as exc:
        raise ModelRetry("Finding line must be a 1-based trace line number.") from exc
    try:
        finding = TraceFinding(
            theme=str(theme or "").strip(),
            line=line_number,
            quote=str(quote or "").strip(),
            level=str(_clean_scalar(level) or "").strip(),
        )
    except ValidationError as exc:
        raise ModelRetry(
            "Finding must include a valid 1-based line and level. "
            f"Allowed levels: {format_allowed_finding_levels()}."
        ) from exc
    ctx.deps.notes.append(finding)
    ctx.deps.findings_checked = True
    total = len(ctx.deps.notes)
    return f"Noted 1 finding (total {total} so far)."


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
    return f"Pruned {added} new trace read(s); total pruned: {len(ctx.deps.pruned_offsets)}."


def compute_request_budget(trace_path: Path) -> int:
    """Scale extract request budget from trace size.

    Budget from the actual number of trace reads plus room for notes, pruning,
    writes, final validation, and retries. Long traces are expensive in tool
    calls; under-budgeting them turns otherwise recoverable sessions into
    request-limit failures.
    """
    try:
        line_count = 0
        estimated_bytes = 0
        with trace_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line_count += 1
                estimated_bytes += min(
                    len(line.rstrip("\n").encode("utf-8")),
                    TRACE_MAX_LINE_BYTES,
                )
    except OSError:
        return 50
    read_calls = max(1, math.ceil(line_count / TRACE_MAX_LINES_PER_READ))
    byte_limited_calls = max(1, math.ceil(estimated_bytes / TRACE_MAX_CHUNK_BYTES))
    read_calls = max(read_calls, byte_limited_calls)
    if read_calls == 1:
        return 50
    prune_cycles = max(0, read_calls - 1)
    overhead = 80
    return max(50, read_calls + prune_cycles + overhead)


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
