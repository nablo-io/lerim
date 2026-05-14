"""Persistence helpers for synthesized extract records."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import textwrap
from typing import Any

from lerim.context import ContextStore, ProjectIdentity
from lerim.context.spec import (
    DURABLE_RECORD_KINDS,
    MAX_DURABLE_BODY_CHARS,
    MAX_EPISODE_BODY_CHARS,
    MAX_EPISODE_OUTCOMES_CHARS,
    MAX_EPISODE_USER_INTENT_CHARS,
    MAX_EPISODE_WHAT_HAPPENED_CHARS,
    MAX_RECORD_TITLE_CHARS,
    normalize_record_kind,
    normalize_record_status,
)


@dataclass(frozen=True)
class PersistenceContext:
    """Context needed to write extracted records."""

    context_db_path: Path
    project_identity: ProjectIdentity
    session_id: str
    trace_path: Path
    session_started_at: str
    model_name: str


@dataclass(frozen=True)
class PersistenceObservation:
    """Observed result after one persistence action."""

    action: str
    ok: bool
    content: str
    args: dict[str, Any]
    done: bool = False
    completion_summary: str = ""


def prepare_context_store(ctx: PersistenceContext) -> None:
    """Initialize store provenance for the extract run."""
    store = ContextStore(ctx.context_db_path)
    store.initialize()
    store.register_project(ctx.project_identity)
    store.upsert_session(
        project_id=ctx.project_identity.project_id,
        session_id=ctx.session_id,
        agent_type="baml-langgraph-extract",
        source_trace_ref=str(ctx.trace_path),
        repo_path=str(ctx.project_identity.repo_path),
        cwd=str(ctx.project_identity.repo_path),
        started_at=ctx.session_started_at,
        model_name=ctx.model_name,
        instructions_text=None,
        prompt_text=None,
        metadata={},
    )


def format_existing_record_manifest(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity,
    limit: int = 5,
) -> str:
    """Build a compact manifest of recent active durable records."""
    store = ContextStore(context_db_path)
    store.initialize()
    store.register_project(project_identity)
    rows = store.query(
        entity="records",
        mode="list",
        project_ids=[project_identity.project_id],
        status="active",
        order_by="updated_at",
        limit=max(1, limit * 2),
        include_total=False,
    )["rows"]
    durable_rows = [
        row for row in rows if str(row.get("kind") or "") != "episode"
    ][:limit]
    if not durable_rows:
        return ""

    lines = ["Relevant existing durable records:"]
    for row in durable_rows:
        record_id = str(row.get("record_id") or "")
        kind = str(row.get("kind") or "")
        title = _shorten(str(row.get("title") or ""))
        body = _shorten(str(row.get("body") or ""))
        lines.append(f"- {record_id} | {kind} | {title} | {body}")
    return "\n".join(lines)


def count_current_session_episodes(ctx: PersistenceContext) -> int:
    """Count current-session episode records in the canonical context store."""
    store = ContextStore(ctx.context_db_path)
    store.initialize()
    store.register_project(ctx.project_identity)
    rows = store.query(
        entity="records",
        mode="count",
        project_ids=[ctx.project_identity.project_id],
        kind="episode",
        source_session_id=ctx.session_id,
        include_archived=True,
    )
    return int(rows.get("count") or 0)


def persist_synthesized_extraction(
    synthesized: Any,
    ctx: PersistenceContext,
) -> tuple[list[dict[str, Any]], bool, str]:
    """Persist synthesized episode and durable records through ContextStore."""
    payload = _model_payload(synthesized)
    completion_summary = str(payload.get("completion_summary") or "").strip()
    durable_records = [
        record
        for record in (
            _prepare_durable_record(item)
            for item in payload.get("durable_records") or []
        )
        if record is not None
    ]
    episode = _prepare_episode(
        payload.get("episode") or {},
        completion_summary,
        has_durable_records=bool(durable_records),
    )

    observations: list[dict[str, Any]] = []
    store = ContextStore(ctx.context_db_path)
    store.initialize()
    store.register_project(ctx.project_identity)
    for index, record in enumerate([episode, *durable_records]):
        skip_remaining_records = False
        try:
            result = store.create_record(
                project_id=ctx.project_identity.project_id,
                session_id=ctx.session_id,
                change_reason="baml_extract",
                created_at=ctx.session_started_at or None,
                **record,
            )
            observation = PersistenceObservation(
                action="save_context",
                ok=True,
                content=json.dumps(
                    {"ok": True, "result": result},
                    ensure_ascii=True,
                    indent=2,
                ),
                args=record,
            )
        except ValueError as exc:
            if index == 0 and str(exc) == "duplicate_episode_for_session":
                observation = _duplicate_episode_observation(store, ctx, record)
                skip_remaining_records = True
            else:
                observation = PersistenceObservation(
                    action="save_context",
                    ok=False,
                    content=f"Record write failed: {type(exc).__name__}: {exc}",
                    args=record,
                )
        except Exception as exc:
            observation = PersistenceObservation(
                action="save_context",
                ok=False,
                content=f"Record write failed: {type(exc).__name__}: {exc}",
                args=record,
            )
        observations.append(observation_to_state(observation))
        if skip_remaining_records:
            break
        if index == 0 and not observation.ok:
            break

    episode_count = count_current_session_episodes(ctx)
    done = episode_count == 1
    if not completion_summary:
        completion_summary = "Extraction completed."
    final_observation = PersistenceObservation(
        action="final_result",
        ok=done,
        content=(
            completion_summary
            if done
            else f"final_result refused: expected exactly one episode record, found {episode_count}."
        ),
        args={},
        done=done,
        completion_summary=completion_summary if done else "",
    )
    observations.append(observation_to_state(final_observation))
    return observations, done, completion_summary if done else ""


def _duplicate_episode_observation(
    store: ContextStore,
    ctx: PersistenceContext,
    record: dict[str, Any],
) -> PersistenceObservation:
    """Return an idempotent observation for an already-extracted session."""
    rows = store.query(
        entity="records",
        mode="list",
        project_ids=[ctx.project_identity.project_id],
        kind="episode",
        source_session_id=ctx.session_id,
        limit=1,
        include_archived=True,
    ).get("rows")
    existing = rows[0] if isinstance(rows, list) and rows else {}
    return PersistenceObservation(
        action="save_context",
        ok=True,
        content=json.dumps(
            {
                "ok": True,
                "skipped": "duplicate_episode_for_session",
                "existing_record_id": str(existing.get("record_id") or ""),
            },
            ensure_ascii=True,
            indent=2,
        ),
        args=record,
    )


def observation_to_state(observation: PersistenceObservation) -> dict[str, Any]:
    """Convert a persistence observation into serializable graph state."""
    return {
        "action": observation.action,
        "ok": observation.ok,
        "content": observation.content,
        "args": observation.args,
        "done": observation.done,
        "completion_summary": observation.completion_summary,
    }


def _prepare_episode(
    value: Any,
    completion_summary: str,
    *,
    has_durable_records: bool,
) -> dict[str, Any]:
    """Normalize a synthesized episode draft into a canonical record payload."""
    episode = _model_payload(value)
    status = _status_value(episode.get("status"))
    if not status:
        status = "active" if has_durable_records else "archived"
    if not str(episode.get("title") or "").strip():
        episode["title"] = _episode_title_from_payload(episode, completion_summary)
    if not str(episode.get("user_intent") or "").strip():
        episode["user_intent"] = "Extract context from the source trace."
    if not str(episode.get("what_happened") or "").strip():
        fallback = (
            str(episode.get("body") or "").strip()
            or completion_summary
            or "The trace was scanned and summarized for context extraction."
        )
        episode["what_happened"] = fallback
    if not str(episode.get("body") or "").strip():
        episode["body"] = _episode_body_from_structured_fields(episode)
    return {
        "kind": "episode",
        "title": _compact_text(episode.get("title"), MAX_RECORD_TITLE_CHARS),
        "body": _compact_text(episode.get("body"), MAX_EPISODE_BODY_CHARS),
        "status": status,
        "valid_from": _empty_to_none(episode.get("valid_from")),
        "valid_until": _empty_to_none(episode.get("valid_until")),
        "decision": None,
        "why": None,
        "alternatives": None,
        "consequences": None,
        "user_intent": _compact_text(
            episode.get("user_intent"),
            MAX_EPISODE_USER_INTENT_CHARS,
        ),
        "what_happened": _compact_text(
            episode.get("what_happened"),
            MAX_EPISODE_WHAT_HAPPENED_CHARS,
        ),
        "outcomes": _compact_optional_text(
            episode.get("outcomes"),
            MAX_EPISODE_OUTCOMES_CHARS,
        ),
    }


def _prepare_durable_record(value: Any) -> dict[str, Any] | None:
    """Normalize one durable draft into a canonical record payload."""
    record = _model_payload(value)
    kind = normalize_record_kind(_enum_text(record.get("kind")))
    if kind not in DURABLE_RECORD_KINDS:
        return None
    title = _compact_text(record.get("title"), MAX_RECORD_TITLE_CHARS)
    body = _compact_text(record.get("body"), MAX_DURABLE_BODY_CHARS)
    if not title or not body:
        return None
    decision = _empty_to_none(record.get("decision"))
    why = _empty_to_none(record.get("why"))
    if kind == "decision" and (not decision or not why):
        kind = "fact"
        decision = None
        why = None
    return {
        "kind": kind,
        "title": title,
        "body": body,
        "status": _status_value(record.get("status")) or "active",
        "valid_from": _empty_to_none(record.get("valid_from")),
        "valid_until": _empty_to_none(record.get("valid_until")),
        "decision": decision if kind == "decision" else None,
        "why": why if kind == "decision" else None,
        "alternatives": _empty_to_none(record.get("alternatives"))
        if kind == "decision"
        else None,
        "consequences": _empty_to_none(record.get("consequences"))
        if kind == "decision"
        else None,
        "user_intent": None,
        "what_happened": None,
        "outcomes": None,
    }


def _model_payload(value: Any) -> dict[str, Any]:
    """Return a plain dict from a generated BAML/Pydantic-ish object."""
    if hasattr(value, "model_dump"):
        return _coerce_value(value.model_dump(exclude_none=True))
    if isinstance(value, dict):
        return _coerce_value(
            {key: item for key, item in value.items() if item is not None}
        )
    if value is None:
        return {}
    return _coerce_value(
        json.loads(json.dumps(value, default=lambda item: item.__dict__))
    )


def _coerce_value(value: Any) -> Any:
    """Convert generated BAML enum values into plain JSON-like values."""
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return enum_value
    if isinstance(value, dict):
        return {key: _coerce_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_coerce_value(item) for item in value]
    return value


def _status_value(value: Any) -> str:
    """Return a canonical record status or an empty string."""
    text = normalize_record_status(_enum_text(value), default="")
    return text if text in {"active", "archived"} else ""


def _enum_text(value: Any) -> str:
    """Convert BAML enum text into lowercase alias text."""
    return str(value or "").strip().lower()


def _episode_body_from_structured_fields(episode: dict[str, Any]) -> str:
    """Build an episode body when synthesis provided structured fields only."""
    user_intent = str(episode.get("user_intent") or "").strip()
    what_happened = str(episode.get("what_happened") or "").strip()
    outcomes = str(episode.get("outcomes") or "").strip()
    parts = []
    if user_intent:
        parts.append(f"User intent: {user_intent}")
    if what_happened:
        parts.append(f"What happened: {what_happened}")
    if outcomes:
        parts.append(f"Outcome: {outcomes}")
    return " ".join(parts) or "The session was scanned and summarized for context extraction."


def _episode_title_from_payload(episode: dict[str, Any], completion_summary: str) -> str:
    """Derive a compact episode title from available episode text."""
    candidates = [
        episode.get("user_intent"),
        episode.get("what_happened"),
        episode.get("outcomes"),
        completion_summary,
        episode.get("body"),
    ]
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text[:80].rstrip(" .") or "Extracted session"
    return "Extracted session"


def _compact_text(value: Any, max_chars: int) -> str:
    """Return non-empty text that fits the canonical record field budget."""
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return textwrap.shorten(text, width=max_chars, placeholder="...")


def _compact_optional_text(value: Any, max_chars: int) -> str | None:
    """Return optional compact text, preserving None for empty values."""
    text = _compact_text(value, max_chars)
    return text or None


def _empty_to_none(value: Any) -> str | None:
    """Convert blank values to None."""
    text = " ".join(str(value or "").split())
    return text or None


def _shorten(text: str, max_chars: int = 140) -> str:
    """Shorten one manifest field."""
    value = " ".join((text or "").split())
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."
