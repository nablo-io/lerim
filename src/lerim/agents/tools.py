"""Agent tools for Lerim's simplified DB-only context architecture."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_ai import ModelRetry, RunContext

from lerim.context import ContextStore, ProjectIdentity
from lerim.context.spec import (
    ALLOWED_FINDING_LEVELS,
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


class Finding(BaseModel):
    """Structured extract finding captured during trace scanning."""

    theme: str = Field(description="Short theme label for the finding.")
    offset: int = Field(description="Trace line where the supporting evidence appears.")
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
    run_folder: Path | None = None
    session_started_at: str = ""
    trace_total_lines: int = 0
    read_ranges: list[tuple[int, int]] = field(default_factory=list)
    notes: list[Finding] = field(default_factory=list)
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
            "Call note first with the strongest durable and implementation findings from the chunks already read. "
            "Then continue reading."
        )
    missing_offsets = [
        offset for offset in older_offsets if offset not in ctx.deps.pruned_offsets
    ]
    if missing_offsets:
        offsets_text = ", ".join(str(item) for item in missing_offsets)
        raise ModelRetry(
            f"Context pressure is {pressure} ({fill_ratio:.0%} of the configured window). "
            "Prune older trace_read results before reading more so the context stays focused. "
            f"Call prune(trace_offsets=[{offsets_text}]) now, then continue reading."
        )


def trace_read(ctx: RunContext[ContextDeps], offset: int = 0, limit: int = 100) -> str:
    """Read normalized trace chunks with line numbers and bounded size."""
    trace_path = ctx.deps.trace_path
    if trace_path is None:
        return "Error: no trace path configured"
    lines = _trace_lines(trace_path)
    total = len(lines)
    ctx.deps.trace_total_lines = total
    offset = max(0, int(offset))
    if offset >= total and total > 0:
        raise ModelRetry(
            f"trace_read offset {offset} is past the end of the trace. "
            f"Use an offset from 0 to {max(0, total - 1)}."
        )
    if limit <= 0 or limit > TRACE_MAX_LINES_PER_READ:
        limit = TRACE_MAX_LINES_PER_READ
    _require_note_or_prune_before_trace_read(ctx, offset)
    chunk = lines[offset : offset + limit]
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
            f"trace_read(offset={last_line}, limit={TRACE_MAX_LINES_PER_READ}) for the next chunk"
        )
    return header + "\n" + "\n".join(numbered)


def search_records(
    ctx: RunContext[ContextDeps],
    query: str,
    kind_filters: list[str] | None = None,
    status_filters: list[str] | None = None,
    valid_at: str = "",
    include_archived: bool = False,
    limit: int = 8,
) -> str:
    """Search records by topic or meaning.

    Use this when the question is semantic, such as "what do we know about X?"
    Do not use it as the first step for exact count, latest, date-window,
    truth-at-time, current-vs-historical, or mixed time-plus-topic questions;
    those are better served by `context_query` or `list_records` first. For
    current-vs-historical questions, semantic search is only a follow-up aid
    after archived-capable exact retrieval has already surfaced the current and
    historical candidates. If an exact time-window narrowing step already
    returned zero rows, do not use this tool to widen scope unless the user
    explicitly asks for broader history.
    """
    store = _store(ctx)
    trimmed_query = str(query or "").strip()
    if not trimmed_query or trimmed_query == "*":
        raise ModelRetry(
            "search_records needs a real text query. "
            "Use list_records when you want to browse recent or filtered records."
        )
    normalized_kinds = _normalize_filter_list(
        kind_filters,
        _normalize_kind,
        label="search_records kind_filters",
    )
    normalized_statuses = _normalize_filter_list(
        status_filters,
        _normalize_status,
        label="search_records status_filters",
    )
    effective_include_archived = bool(include_archived or str(valid_at or "").strip())
    hits = store.search(
        project_ids=ctx.deps.project_ids or [ctx.deps.project_identity.project_id],
        query=trimmed_query,
        kind_filters=normalized_kinds,
        statuses=normalized_statuses,
        valid_at=valid_at.strip() or None,
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


def list_records(
    ctx: RunContext[ContextDeps],
    kind_filters: list[str] | None = None,
    status_filters: list[str] | None = None,
    created_since: str = "",
    created_until: str = "",
    updated_since: str = "",
    updated_until: str = "",
    valid_at: str = "",
    include_archived: bool = False,
    order_by: str = "updated_at",
    limit: int = 8,
) -> str:
    """List compact record rows using exact filters and ordering.

    Best for browsing recent rows or narrowing by exact fields such as kind,
    created/updated windows, status, or `valid_at`. For latest-by-kind,
    exact date-window, current-vs-historical, and mixed time-plus-topic
    questions, prefer this or `context_query` before any semantic search. Use
    `include_archived=True` when the question asks for historical truth or a
    before-vs-now comparison. The rows are previews; fetch the records you
    will rely on before answering from them. If a requested time window returns
    zero rows, answer from that zero result rather than widening scope.
    """
    store = _store(ctx)
    normalized_kinds = _normalize_filter_list(
        kind_filters,
        _normalize_kind,
        max_items=1,
        label="list_records kind_filters",
    )
    normalized_statuses = _normalize_filter_list(
        status_filters,
        _normalize_status,
        max_items=1,
        label="list_records status_filters",
    )
    order = str(order_by or "updated_at").strip().lower()
    if order not in {"created_at", "updated_at", "valid_from"}:
        raise ModelRetry(
            "list_records order_by must be one of: created_at, updated_at, valid_from."
        )
    effective_include_archived = bool(include_archived or valid_at.strip())
    status: str | None = None
    if normalized_statuses:
        status = normalized_statuses[0]
    elif not effective_include_archived:
        status = "active"
    listing = store.query(
        entity="records",
        mode="list",
        project_ids=ctx.deps.project_ids or [ctx.deps.project_identity.project_id],
        kind=normalized_kinds[0] if normalized_kinds else None,
        status=status,
        created_since=created_since.strip() or None,
        created_until=created_until.strip() or None,
        updated_since=updated_since.strip() or None,
        updated_until=updated_until.strip() or None,
        valid_at=valid_at.strip() or None,
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


def fetch_records(
    ctx: RunContext[ContextDeps],
    record_ids: list[str],
    include_versions: bool = False,
    response_format: str = "detailed",
) -> str:
    """Fetch full canonical records by ID after you identify candidates.

    Use this after `search_records`, `list_records`, or `context_query` when
    you need the complete body or typed fields before answering.

    For extract and maintain flows, shortlist signals are not enough for an
    update. Fetch the canonical record before `update_record`, especially when
    more than one nearby record could plausibly match.
    """
    mode = (response_format or "concise").strip().lower()
    if mode not in {"concise", "detailed"}:
        raise ModelRetry(
            "fetch_records response_format must be 'concise' or 'detailed'."
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


def _normalize_filter_list(
    values: list[str] | None,
    normalizer,
    *,
    max_items: int | None = None,
    label: str,
) -> list[str] | None:
    """Normalize optional tool filter lists and reject empty or oversized values."""
    normalized = [
        item
        for item in (normalizer(value) for value in (values or []))
        if item
    ]
    if max_items is not None and len(normalized) > max_items:
        raise ModelRetry(
            f"{label} currently supports at most {max_items} value(s). Narrow the filter or use repeated calls."
        )
    return normalized or None


def _maybe_raise_record_retry(exc: ValueError) -> None:
    """Convert record-quality validation errors into guided model retries."""
    code = str(exc or "").strip()
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
            f"Call trace_read(offset=0, limit={TRACE_MAX_LINES_PER_READ}) "
            "before you create or update records."
        )
    next_offset = _first_uncovered_offset(ctx.deps.read_ranges, total_lines)
    if next_offset is not None:
        raise ModelRetry(
            "Unread trace lines remain. "
            f"Continue reading with trace_read(offset={next_offset}, limit={TRACE_MAX_LINES_PER_READ}) "
            "before you create or update records."
        )
    if total_lines > TRACE_MAX_LINES_PER_READ and not ctx.deps.notes:
        raise ModelRetry(
            "This trace is longer than one trace_read chunk. "
            "Call note first with the strongest durable and implementation findings, "
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


def _require_full_trace_coverage_before_write(ctx: RunContext[ContextDeps]) -> None:
    """Backward-compatible wrapper for the unified trace write gate."""
    _require_trace_ready_for_write(ctx)


def _require_notes_before_long_trace_write(ctx: RunContext[ContextDeps]) -> None:
    """Backward-compatible wrapper for the unified trace write gate."""
    _require_trace_ready_for_write(ctx)


def create_record(
    ctx: RunContext[ContextDeps],
    kind: str,
    title: str,
    body: str,
    status: str = "active",
    valid_from: str = "",
    valid_until: str = "",
    decision: str = "",
    why: str = "",
    alternatives: str = "",
    consequences: str = "",
    user_intent: str = "",
    what_happened: str = "",
    outcomes: str = "",
    change_reason: str = "",
) -> str:
    """Create one durable record with explicit typed fields.

    Durable fact, decision, preference, constraint, and reference records
    should be canonical project context, not trace recaps. Do not include
    comma-separated or parenthetical lists of discarded implementation lures;
    if contrast matters, use one broad category such as ephemeral local state.
    Do not append sentences whose main purpose is to say which cleanup,
    test, logging, or implementation details are not durable context; those
    exclusions are extraction evidence, not durable project context.
    For dependency or environment facts, name the requirement directly. Never
    copy exception class names, stderr, commands, or quoted failure fragments
    into durable fact text.

    Episode records should be `active` only when they remain useful context
    for future sessions. Use `archived` for routine operational sessions with
    no durable signal.
    """
    _require_trace_ready_for_write(ctx)
    normalized_kind = _normalize_kind(kind)
    store = _store(ctx)
    project_id = ctx.deps.project_identity.project_id
    session_id = ctx.deps.session_id
    source_started_at = _source_session_started_at(ctx, store)
    try:
        result = store.create_record(
            project_id=project_id,
            session_id=session_id,
            kind=normalized_kind,
            title=title,
            body=body,
            status=_normalize_status(status),
            created_at=source_started_at or None,
            valid_from=valid_from.strip() or None,
            valid_until=valid_until.strip() or None,
            decision=decision.strip() or None,
            why=why.strip() or None,
            alternatives=alternatives.strip() or None,
            consequences=consequences.strip() or None,
            user_intent=user_intent.strip() or None,
            what_happened=what_happened.strip() or None,
            outcomes=outcomes.strip() or None,
            change_reason=change_reason.strip() or None,
        )
    except ValueError as exc:
        _maybe_raise_record_retry(exc)
        raise
    return json.dumps({"ok": True, "result": result}, ensure_ascii=True, indent=2)


def update_record(
    ctx: RunContext[ContextDeps],
    record_id: str,
    title: str = "",
    body: str = "",
    status: str = "",
    valid_from: str = "",
    valid_until: str = "",
    superseded_by_record_id: str = "",
    decision: str = "",
    why: str = "",
    alternatives: str = "",
    consequences: str = "",
    user_intent: str = "",
    what_happened: str = "",
    outcomes: str = "",
    change_reason: str = "",
) -> str:
    """Update one durable record with explicit typed fields.

    Call this only after you have already inspected the canonical existing
    record with `fetch_records`. Shortlist summaries, search hits, and injected
    manifests are not sufficient evidence for an update by themselves.
    Preserve canonical project context wording: avoid trace recaps and lists of
    discarded implementation lures in updated durable records.
    Do not append sentences whose main purpose is to say which cleanup,
    test, logging, or implementation details are not durable context; those
    exclusions are extraction evidence, not durable project context.
    For dependency or environment facts, name the requirement directly rather
    than copying exception classes, stderr, commands, or log fragments.
    """
    _require_trace_ready_for_write(ctx)
    changes: dict[str, Any] = {}
    for key, value in {
        "title": title,
        "body": body,
        "valid_from": valid_from,
        "valid_until": valid_until,
        "superseded_by_record_id": superseded_by_record_id,
        "decision": decision,
        "why": why,
        "alternatives": alternatives,
        "consequences": consequences,
        "user_intent": user_intent,
        "what_happened": what_happened,
        "outcomes": outcomes,
    }.items():
        stripped = str(value or "").strip()
        if stripped:
            changes[key] = stripped
    if str(status or "").strip():
        changes["status"] = _normalize_status(status)
    store = _store(ctx)
    try:
        result = store.update_record(
            record_id=str(record_id or "").strip(),
            session_id=ctx.deps.session_id,
            project_ids=ctx.deps.project_ids or [ctx.deps.project_identity.project_id],
            changes=changes,
            change_reason=str(change_reason or "").strip() or None,
        )
    except ValueError as exc:
        _maybe_raise_record_retry(exc)
        raise
    return json.dumps({"ok": True, "result": result}, ensure_ascii=True, indent=2)


def archive_record(
    ctx: RunContext[ContextDeps],
    record_id: str,
    reason: str = "",
) -> str:
    """Archive one durable record."""
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
                "Use supersede_record when it is a duplicate, or leave it active."
            ) from exc
        raise
    return json.dumps({"ok": True, "result": result}, ensure_ascii=True, indent=2)


def supersede_record(
    ctx: RunContext[ContextDeps],
    record_id: str,
    replacement_record_id: str,
    reason: str = "",
    valid_until: str = "",
) -> str:
    """Mark one durable record as superseded by another."""
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
                "Search or list records again, fetch the replacement, then retry with its record_id."
            ) from exc
        if message.startswith("record_not_found:") or message.startswith("record_out_of_scope:"):
            raise ModelRetry(
                "The record to supersede does not exist in the current project scope. "
                "Search or list records again, fetch the target, then retry with its record_id."
            ) from exc
        _maybe_raise_record_retry(exc)
        raise
    return json.dumps({"ok": True, "result": result}, ensure_ascii=True, indent=2)


def context_query(
    ctx: RunContext[ContextDeps],
    entity: str,
    mode: str,
    kind: str = "",
    status: str = "",
    source_session_id: str = "",
    created_since: str = "",
    created_until: str = "",
    updated_since: str = "",
    updated_until: str = "",
    valid_at: str = "",
    order_by: str = "created_at",
    limit: int = 20,
    offset: int = 0,
    include_total: bool = False,
) -> str:
    """Run deterministic count or list queries over records, versions, or sessions.

    Use this for exact questions such as counts, latest rows, strict date
    windows, and truth-at-time queries. For record queries, current rows are
    the default; `valid_at` is the way to include historical rows that were
    true at the requested time. For before-vs-now or current-vs-historical
    comparisons, use `list_records(include_archived=True)` when you need to
    inspect both current and retired candidates explicitly. In record `list`
    mode, the returned rows are shortlist previews, not final evidence; fetch
    the records you will rely on before answering from them. If an exact
    time-window query returns zero rows, treat that as the answer for that
    window unless the user explicitly asks to broaden scope.
    """
    store = _store(ctx)
    entity_name = str(entity or "").strip().lower()
    include_archived = bool(entity_name == "records" and str(valid_at or "").strip())
    try:
        payload = store.query(
            entity=entity_name,
            mode=str(mode or "").strip().lower(),
            project_ids=ctx.deps.project_ids or [ctx.deps.project_identity.project_id],
            kind=_normalize_kind(kind) or None,
            status=_normalize_status(status) if str(status or "").strip() else None,
            source_session_id=str(source_session_id or "").strip() or None,
            created_since=str(created_since or "").strip() or None,
            created_until=str(created_until or "").strip() or None,
            updated_since=str(updated_since or "").strip() or None,
            updated_until=str(updated_until or "").strip() or None,
            valid_at=str(valid_at or "").strip() or None,
            order_by=str(order_by or "created_at").strip(),
            limit=max(1, min(int(limit), 100)),
            offset=max(0, int(offset)),
            include_total=bool(include_total),
            include_archived=include_archived,
        )
    except ValueError as exc:
        message = str(exc)
        if message.startswith("invalid_query_entity:"):
            raise ModelRetry(
                "context_query entity must be one of: records, versions, sessions."
            ) from exc
        if message.startswith("invalid_query_mode:"):
            raise ModelRetry("context_query mode must be 'list' or 'count'.") from exc
        if message.startswith("invalid_query_order:"):
            raise ModelRetry(
                "context_query order_by must be one of: created_at, updated_at, valid_from."
            ) from exc
        raise
    if entity_name == "records" and str(mode or "").strip().lower() == "list":
        payload["rows"] = [
            {
                "record_id": row["record_id"],
                "project_id": row["project_id"],
                "kind": row["kind"],
                "title": row["title"],
                "body_preview": str(row["body"])[:280],
                "status": row["status"],
                "source_session_id": row["source_session_id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "valid_from": row["valid_from"],
                "valid_until": row["valid_until"],
                "superseded_by_record_id": row["superseded_by_record_id"],
            }
            for row in payload["rows"]
        ]
    return json.dumps(payload, ensure_ascii=True, indent=2)


def note(ctx: RunContext[ContextDeps], findings: list[Finding]) -> str:
    """Record structured findings from the trace chunks just read.

    Use durable levels only for reusable project context. Keep dead ends,
    discarded hypotheses, and trace-local noise at `implementation` level so
    they support the main theme without becoming their own durable record.
    """
    if not findings:
        return "No findings recorded."
    ctx.deps.notes.extend(findings)
    total = len(ctx.deps.notes)
    return f"Noted {len(findings)} findings (total {total} so far)."


def prune(ctx: RunContext[ContextDeps], trace_offsets: list[int]) -> str:
    """Stub prior trace reads in future turns to reduce context pressure."""
    if not trace_offsets:
        return "No offsets to prune."
    read_offsets = set(_read_offsets(ctx))
    requested = {int(offset) for offset in trace_offsets}
    unknown_offsets = sorted(requested - read_offsets)
    if unknown_offsets:
        known = ", ".join(str(offset) for offset in sorted(read_offsets)) or "none"
        bad = ", ".join(str(offset) for offset in unknown_offsets)
        raise ModelRetry(
            f"Cannot prune unread trace offset(s): {bad}. "
            f"Only previously read offsets can be pruned; read offsets: {known}."
        )
    before = len(ctx.deps.pruned_offsets)
    ctx.deps.pruned_offsets.update(requested)
    added = len(ctx.deps.pruned_offsets) - before
    return (
        f"Pruned {added} new offset(s); total pruned: {len(ctx.deps.pruned_offsets)}."
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
