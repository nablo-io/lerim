"""Agent tools for Lerim's simplified DB-only context architecture."""

from __future__ import annotations

from dataclasses import replace
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

from lerim.context import ALLOWED_KINDS, ALLOWED_STATUSES, ContextStore, ProjectIdentity

TRACE_MAX_LINES_PER_READ = 100
TRACE_MAX_LINE_BYTES = 5_000
TRACE_MAX_CHUNK_BYTES = 50_000
MODEL_CONTEXT_TOKEN_LIMIT = 200_000
CONTEXT_SOFT_PRESSURE_PCT = 0.60
CONTEXT_HARD_PRESSURE_PCT = 0.80
_TOKENS_PER_CHAR = 0.25
PRUNED_STUB = "[pruned]"


class Finding(BaseModel):
    """Structured extract finding captured during trace scanning."""

    theme: str = Field(description="Short theme label for the finding.")
    offset: int = Field(description="Trace line where the supporting evidence appears.")
    quote: str = Field(description="Short verbatim evidence snippet from the trace.")
    level: str = Field(
        description=(
            "Signal level: decision, preference, feedback, reference, "
            "constraint, fact, or implementation."
        )
    )


@dataclass
class ContextDeps:
    """Dependencies and per-run state shared across tool calls."""

    context_db_path: Path
    project_identity: ProjectIdentity
    session_id: str
    project_ids: list[str] | None = None
    trace_path: Path | None = None
    run_folder: Path | None = None
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


def _require_note_or_prune_before_trace_read(ctx: RunContext[ContextDeps], offset: int) -> None:
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
    if not ctx.deps.pruned_offsets:
        offsets_text = ", ".join(str(item) for item in older_offsets)
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
    if limit <= 0 or limit > TRACE_MAX_LINES_PER_READ:
        limit = TRACE_MAX_LINES_PER_READ
    _require_note_or_prune_before_trace_read(ctx, int(offset))
    lines = _trace_lines(trace_path)
    total = len(lines)
    ctx.deps.trace_total_lines = total
    chunk = lines[offset : offset + limit]
    safe_chunk: list[str] = []
    running_bytes = 0
    for line in chunk:
        if len(line) > TRACE_MAX_LINE_BYTES:
            dropped = len(line) - TRACE_MAX_LINE_BYTES
            line = line[:TRACE_MAX_LINE_BYTES] + f" ... [truncated {dropped} chars from this line]"
        line_bytes = len(line.encode("utf-8"))
        if running_bytes + line_bytes > TRACE_MAX_CHUNK_BYTES:
            break
        safe_chunk.append(line)
        running_bytes += line_bytes
    numbered = [f"{offset + index + 1}\t{line}" for index, line in enumerate(safe_chunk)]
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
    """Search the context store with hybrid retrieval and compact results."""
    store = _store(ctx)
    trimmed_query = str(query or "").strip()
    if not trimmed_query or trimmed_query == "*":
        raise ModelRetry(
            "search_records needs a real text query. "
            "Use list_records when you want to browse recent or filtered records."
        )
    hits = store.search(
        project_ids=ctx.deps.project_ids or [ctx.deps.project_identity.project_id],
        query=trimmed_query,
        kind_filters=kind_filters or None,
        statuses=status_filters or None,
        valid_at=valid_at.strip() or None,
        include_archived=bool(include_archived),
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
    """List compact record rows with exact filters and ordering."""
    store = _store(ctx)
    order = str(order_by or "updated_at").strip().lower()
    if order not in {"created_at", "updated_at", "valid_from"}:
        raise ModelRetry("list_records order_by must be one of: created_at, updated_at, valid_from.")
    status: str | None = None
    if status_filters and len(status_filters) == 1:
        status = _normalize_status(status_filters[0])
    elif not include_archived:
        status = "active"
    listing = store.query(
        entity="records",
        mode="list",
        project_ids=ctx.deps.project_ids or [ctx.deps.project_identity.project_id],
        kind=_normalize_kind(kind_filters[0]) if kind_filters and len(kind_filters) == 1 else None,
        status=status,
        created_since=created_since.strip() or None,
        created_until=created_until.strip() or None,
        updated_since=updated_since.strip() or None,
        updated_until=updated_until.strip() or None,
        valid_at=valid_at.strip() or None,
        order_by=order,
        limit=max(1, min(int(limit), 50)),
        include_total=False,
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
    """Fetch canonical records by ID with concise or detailed response formats."""
    mode = (response_format or "concise").strip().lower()
    if mode not in {"concise", "detailed"}:
        return f"Error: response_format must be 'concise' or 'detailed', got {response_format!r}"
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
    return json.dumps({"count": len(records), "records": records}, ensure_ascii=True, indent=2)


def _normalize_kind(kind: str) -> str:
    """Normalize kind names before store validation."""
    return str(kind or "").strip().lower()


def _normalize_status(status: str) -> str:
    """Normalize status names before store validation."""
    return str(status or "active").strip().lower()


def _maybe_raise_record_retry(exc: ValueError) -> None:
    """Convert record-quality validation errors into guided model retries."""
    code = str(exc or "").strip()
    retry_messages = {
        "title_required": "Every record needs a non-empty title.",
        "body_required": "Every record needs a non-empty body.",
        "title_too_long": "Title is too long. Use one short specific memory title under 120 characters.",
        "decision_requires_decision_and_why": (
            "Decision records need both `decision` and `why`. "
            "If you cannot supply both, create a `fact` instead."
        ),
        "episode_requires_session_id": "Episode records must stay tied to the current session.",
        "episode_requires_user_intent_and_what_happened": (
            "Episode records need both `user_intent` and `what_happened`."
        ),
        "episode_body_too_long": (
            "Episode body is too long. Compress it to 2-4 short sentences."
        ),
        "episode_user_intent_too_long": "user_intent is too long. Compress it to one short sentence.",
        "episode_what_happened_too_long": "what_happened is too long. Keep only the essential session outcome.",
        "episode_outcomes_too_long": "outcomes is too long. Keep only the lasting result.",
        "record_body_too_long": (
            "Durable record body is too long. Keep only the reusable rule/decision/fact and why it matters."
        ),
    }
    message = retry_messages.get(code)
    if message:
        raise ModelRetry(message) from exc


def _require_notes_before_long_trace_write(ctx: RunContext[ContextDeps]) -> None:
    """Require one note step before writes on traces that exceed one read chunk."""
    trace_path = ctx.deps.trace_path
    if trace_path is None or ctx.deps.notes:
        return
    try:
        line_count = sum(1 for _ in trace_path.open("r", encoding="utf-8"))
    except OSError:
        return
    if line_count <= TRACE_MAX_LINES_PER_READ:
        return
    raise ModelRetry(
        "This trace is longer than one trace_read chunk. "
        "Call note first with the strongest durable and implementation findings, "
        "then create or update records."
    )


def _first_uncovered_offset(read_ranges: list[tuple[int, int]], total_lines: int) -> int | None:
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
    """Require contiguous coverage of the full trace before any write."""
    trace_path = ctx.deps.trace_path
    if trace_path is None:
        return
    total_lines = int(ctx.deps.trace_total_lines)
    if total_lines <= 0:
        try:
            total_lines = sum(1 for _ in trace_path.open("r", encoding="utf-8"))
        except OSError:
            return
    if not ctx.deps.read_ranges:
        return
    next_offset = _first_uncovered_offset(ctx.deps.read_ranges, total_lines)
    if next_offset is None:
        return
    raise ModelRetry(
        "Unread trace lines remain. "
        f"Continue reading with trace_read(offset={next_offset}, limit={TRACE_MAX_LINES_PER_READ}) "
        "before you create or update records."
    )


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
    """Create one durable record with explicit typed fields."""
    _require_full_trace_coverage_before_write(ctx)
    _require_notes_before_long_trace_write(ctx)
    store = _store(ctx)
    project_id = ctx.deps.project_identity.project_id
    session_id = ctx.deps.session_id
    try:
        result = store.create_record(
            project_id=project_id,
            session_id=session_id,
            kind=_normalize_kind(kind),
            title=title,
            body=body,
            status=_normalize_status(status),
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
    """Update one durable record with explicit typed fields."""
    _require_full_trace_coverage_before_write(ctx)
    _require_notes_before_long_trace_write(ctx)
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
    result = store.supersede_record(
        record_id=str(record_id or "").strip(),
        session_id=ctx.deps.session_id,
        project_ids=ctx.deps.project_ids or [ctx.deps.project_identity.project_id],
        replacement_record_id=str(replacement_record_id or "").strip(),
        reason=str(reason or "").strip() or None,
        valid_until=str(valid_until or "").strip() or None,
    )
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
    """Run deterministic count/list queries over records, versions, or sessions."""
    store = _store(ctx)
    try:
        payload = store.query(
            entity=str(entity or "").strip().lower(),
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
        )
    except ValueError as exc:
        message = str(exc)
        if message.startswith("invalid_query_entity:"):
            raise ModelRetry(
                "context_query entity must be one of: records, memories, learnings, versions, sessions."
            ) from exc
        if message.startswith("invalid_query_mode:"):
            raise ModelRetry("context_query mode must be 'list' or 'count'.") from exc
        if message.startswith("invalid_query_order_by:"):
            raise ModelRetry("context_query order_by must be one of: created_at, updated_at, valid_from.") from exc
        raise
    return json.dumps(payload, ensure_ascii=True, indent=2)


def note(ctx: RunContext[ContextDeps], findings: list[Finding]) -> str:
    """Record structured findings from the trace chunks just read."""
    if not findings:
        return "No findings recorded."
    ctx.deps.notes.extend(findings)
    total = len(ctx.deps.notes)
    return f"Noted {len(findings)} findings (total {total} so far)."


def prune(ctx: RunContext[ContextDeps], trace_offsets: list[int]) -> str:
    """Stub prior trace reads in future turns to reduce context pressure."""
    if not trace_offsets:
        return "No offsets to prune."
    before = len(ctx.deps.pruned_offsets)
    ctx.deps.pruned_offsets.update(int(offset) for offset in trace_offsets)
    added = len(ctx.deps.pruned_offsets) - before
    return f"Pruned {added} new offset(s); total pruned: {len(ctx.deps.pruned_offsets)}."


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


def notes_state_injector(
    ctx: RunContext[ContextDeps],
    history: list[ModelMessage],
) -> list[ModelMessage]:
    """Inject a compact notes dashboard into the next model request."""
    findings = ctx.deps.notes
    if not findings:
        summary = "NOTES: 0 findings"
    else:
        counts = Counter(f.level for f in findings)
        themes = Counter(f.theme for f in findings)
        durable = sum(counts.get(level, 0) for level in ("decision", "preference", "feedback", "reference", "constraint", "fact"))
        implementation = counts.get("implementation", 0)
        top_themes = ", ".join(f"{theme}({count})" for theme, count in themes.most_common(5))
        summary = (
            f"NOTES: {len(findings)} findings ({durable} durable, {implementation} implementation) "
            f"across {len(themes)} theme(s)"
        )
        if top_themes:
            summary += f"\nTop themes: {top_themes}"
    if ctx.deps.read_ranges:
        next_uncovered = _first_uncovered_offset(ctx.deps.read_ranges, int(ctx.deps.trace_total_lines))
        covered_chunks = len(
            {
                (int(start), int(end))
                for start, end in ctx.deps.read_ranges
            }
        )
        summary += (
            f"\nTrace reads: {covered_chunks} chunk(s)"
            f"\nNext unread offset: {next_uncovered if next_uncovered is not None else 'none'}"
            f"\nPruned offsets: {sorted(ctx.deps.pruned_offsets) if ctx.deps.pruned_offsets else 'none'}"
        )
    injected = list(history)
    injected.append(ModelRequest(parts=[SystemPromptPart(content=summary)]))
    return injected


def context_pressure_injector(
    ctx: RunContext[ContextDeps],
    history: list[ModelMessage],
) -> list[ModelMessage]:
    """Inject approximate context pressure information into the next model request."""
    chars = 0
    for message in history:
        parts = getattr(message, "parts", []) or []
        for part in parts:
            content = getattr(part, "content", None)
            if isinstance(content, str):
                chars += len(content)
            elif content is not None:
                chars += len(json.dumps(content, ensure_ascii=True))
    approx_tokens = math.ceil(chars * _TOKENS_PER_CHAR)
    pct = approx_tokens / MODEL_CONTEXT_TOKEN_LIMIT
    pressure = _classify_context_pressure(pct)
    ctx.deps.last_context_tokens = approx_tokens
    ctx.deps.last_context_fill_ratio = pct
    summary = f"CONTEXT: {approx_tokens}/{MODEL_CONTEXT_TOKEN_LIMIT} ({pct:.0%}) [{pressure}]"
    injected = list(history)
    injected.append(ModelRequest(parts=[SystemPromptPart(content=summary)]))
    return injected


def prune_history_processor(
    ctx: RunContext[ContextDeps],
    history: list[ModelMessage],
) -> list[ModelMessage]:
    """Rewrite prior trace_read results to tiny stubs for pruned offsets."""
    if not ctx.deps.pruned_offsets:
        return history
    pruned = set(ctx.deps.pruned_offsets)
    rewritten: list[ModelMessage] = []
    pending_offset: int | None = None
    for message in history:
        parts = getattr(message, "parts", []) or []
        new_parts = []
        for part in parts:
            if isinstance(part, ToolCallPart) and getattr(part, "tool_name", "") == "trace_read":
                args = getattr(part, "args", None)
                offset = None
                if isinstance(args, dict):
                    try:
                        offset = int(args.get("offset", 0))
                    except Exception:
                        offset = 0
                pending_offset = offset
                new_parts.append(part)
                continue
            if (
                isinstance(part, ToolReturnPart)
                and pending_offset in pruned
                and isinstance(part.content, str)
            ):
                new_parts.append(replace(part, content=PRUNED_STUB))
                pending_offset = None
                continue
            new_parts.append(part)
            if isinstance(part, ToolReturnPart):
                pending_offset = None
        rewritten.append(replace(message, parts=new_parts))
    return rewritten


if __name__ == "__main__":
    """Run a small smoke check for request budget logic."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        trace_path = Path(tmp) / "trace.jsonl"
        trace_path.write_text("\n".join(f"line {i}" for i in range(240)), encoding="utf-8")
        budget = compute_request_budget(trace_path)
        assert budget >= 20
        print("agent tools: self-test passed")
