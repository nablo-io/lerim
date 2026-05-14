"""Agent tools for Lerim's simplified DB-only context architecture."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic_ai import ModelRetry, RunContext

from lerim.context import ContextStore, ProjectIdentity
from lerim.context.spec import (
    ALLOWED_KINDS,
    ALLOWED_STATUSES,
    RECORD_TYPED_FIELDS,
    normalize_record_kind,
    normalize_record_status,
    record_validation_message,
)

DetailLevel = Literal["concise", "detailed"]


@dataclass
class ContextDeps:
    """Dependencies and per-run state shared across tool calls."""

    context_db_path: Path
    project_identity: ProjectIdentity
    session_id: str
    project_ids: list[str] | None = None
    fetched_context_record_ids: set[str] = field(default_factory=set)


def _store(ctx: RunContext[ContextDeps]) -> ContextStore:
    """Return the canonical context store for the current run."""
    store = ContextStore(ctx.deps.context_db_path)
    store.initialize()
    store.register_project(ctx.deps.project_identity)
    return store


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


if __name__ == "__main__":
    """Run a small smoke check for context-tool helpers."""
    assert _normalize_kind("FACT") == "fact"
    print("agent tools: self-test passed")
