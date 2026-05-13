"""Bridge synthesized BAML records to Lerim's canonical extraction tools."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import textwrap
from typing import Any

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from lerim.agents import tools as extract_tools
from lerim.agents.tools import ContextDeps
from lerim.context import ContextStore, ProjectIdentity
from lerim.context.spec import (
    MAX_DURABLE_BODY_CHARS,
    MAX_EPISODE_BODY_CHARS,
    MAX_EPISODE_OUTCOMES_CHARS,
    MAX_EPISODE_USER_INTENT_CHARS,
    MAX_EPISODE_WHAT_HAPPENED_CHARS,
    MAX_RECORD_TITLE_CHARS,
)


@dataclass(frozen=True)
class ToolObservation:
    """Observed result after dispatching one persistence action."""

    action: str
    ok: bool
    content: str
    args: dict[str, Any]
    done: bool = False
    completion_summary: str = ""


def build_tool_context(deps: ContextDeps) -> RunContext[ContextDeps]:
    """Build the minimal PydanticAI run context required by Lerim tools."""
    return RunContext(deps=deps, model=TestModel(), usage=RunUsage())


def count_current_session_episodes(deps: ContextDeps) -> int:
    """Count current-session episode records in the canonical context store."""
    store = ContextStore(deps.context_db_path)
    store.initialize()
    store.register_project(deps.project_identity)
    rows = store.query(
        entity="records",
        mode="count",
        project_ids=[deps.project_identity.project_id],
        kind="episode",
        source_session_id=deps.session_id,
        include_archived=True,
    )
    return int(rows.get("count") or 0)


def persist_synthesized_extraction(
    synthesized: Any,
    ctx: RunContext[ContextDeps],
) -> tuple[list[dict[str, Any]], bool, str]:
    """Persist synthesized episode and durable records through the real tools."""
    payload = _tool_payload(synthesized)
    completion_summary = str(payload.get("completion_summary") or "").strip()
    episode = _prepare_episode(payload.get("episode") or {}, completion_summary)
    durable_records = [
        record
        for record in (_tool_payload(item) for item in payload.get("durable_records") or [])
        if _is_persistable_durable_record(record)
    ]

    observations: list[dict[str, Any]] = []
    for index, record in enumerate([episode, *durable_records]):
        default_status = "archived" if record.get("kind") == "episode" else "active"
        args = _with_defaults(record, {"status": default_status})
        try:
            content = _save_context(ctx, args)
            observation = ToolObservation(
                action="save_context",
                ok=True,
                content=content,
                args=args,
            )
        except ModelRetry as exc:
            observation = ToolObservation(
                action="save_context",
                ok=False,
                content=f"Tool retry needed: {exc}",
                args=args,
            )
        except Exception as exc:
            observation = ToolObservation(
                action="save_context",
                ok=False,
                content=f"Tool error: {type(exc).__name__}: {exc}",
                args=args,
            )
        observations.append(observation_to_state(observation))
        if index == 0 and not observation.ok:
            break

    episode_count = count_current_session_episodes(ctx.deps)
    done = episode_count == 1
    if not completion_summary:
        completion_summary = "Extraction completed."
    final_observation = ToolObservation(
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


def prepare_context_deps(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity,
    session_id: str,
    trace_path: Path,
    session_started_at: str,
    model_name: str,
) -> ContextDeps:
    """Initialize store provenance and return dependencies for tool calls."""
    store = ContextStore(context_db_path)
    store.initialize()
    store.register_project(project_identity)
    store.upsert_session(
        project_id=project_identity.project_id,
        session_id=session_id,
        agent_type="baml-langgraph-extract",
        source_trace_ref=str(trace_path),
        repo_path=str(project_identity.repo_path),
        cwd=str(project_identity.repo_path),
        started_at=session_started_at,
        model_name=model_name,
        instructions_text=None,
        prompt_text=None,
        metadata={"experiment": "baml_agents"},
    )
    return ContextDeps(
        context_db_path=context_db_path,
        project_identity=project_identity,
        session_id=session_id,
        trace_path=trace_path,
        session_started_at=session_started_at,
    )


def observation_to_state(observation: ToolObservation) -> dict[str, Any]:
    """Convert a tool observation into serializable graph state."""
    return {
        "action": observation.action,
        "ok": observation.ok,
        "content": observation.content,
        "args": observation.args,
        "done": observation.done,
        "completion_summary": observation.completion_summary,
    }


def _prepare_episode(value: Any, completion_summary: str) -> dict[str, Any]:
    """Normalize a synthesized episode draft into a valid save_context payload."""
    episode = _tool_payload(value)
    episode["kind"] = "episode"
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
    episode["title"] = _compact_text(episode.get("title"), MAX_RECORD_TITLE_CHARS)
    episode["user_intent"] = _compact_text(
        episode.get("user_intent"),
        MAX_EPISODE_USER_INTENT_CHARS,
    )
    episode["what_happened"] = _compact_text(
        episode.get("what_happened"),
        MAX_EPISODE_WHAT_HAPPENED_CHARS,
    )
    episode["outcomes"] = _compact_optional_text(
        episode.get("outcomes"),
        MAX_EPISODE_OUTCOMES_CHARS,
    )
    episode["body"] = _compact_text(episode.get("body"), MAX_EPISODE_BODY_CHARS)
    return episode


def _is_persistable_durable_record(record: dict[str, Any]) -> bool:
    """Return whether a synthesized durable record is complete enough to save."""
    kind = str(record.get("kind") or "").strip().lower()
    if not kind or kind == "episode":
        return False
    record["title"] = _compact_text(record.get("title"), MAX_RECORD_TITLE_CHARS)
    record["body"] = _compact_text(record.get("body"), MAX_DURABLE_BODY_CHARS)
    return bool(
        str(record.get("title") or "").strip()
        and str(record.get("body") or "").strip()
    )


def _save_context(ctx: RunContext[ContextDeps], args: dict[str, Any]) -> str:
    """Call save_context with a complete record payload."""
    return extract_tools.save_context(ctx, **_with_defaults(args, {"status": "active"}))


def _with_defaults(args: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """Fill omitted optional tool arguments with Lerim's defaults."""
    payload = dict(defaults)
    payload.update(args)
    return payload


def _coerce_tool_value(value: Any) -> Any:
    """Convert generated BAML enum values into plain JSON-like values."""
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return enum_value
    if isinstance(value, dict):
        return {key: _coerce_tool_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_coerce_tool_value(item) for item in value]
    return value


def _tool_payload(value: Any) -> dict[str, Any]:
    """Return a plain dict from a generated BAML/Pydantic-ish object."""
    if hasattr(value, "model_dump"):
        return _coerce_tool_value(value.model_dump(exclude_none=True))
    if isinstance(value, dict):
        return _coerce_tool_value(
            {key: item for key, item in value.items() if item is not None}
        )
    if value is None:
        return {}
    return _coerce_tool_value(
        json.loads(json.dumps(value, default=lambda item: item.__dict__))
    )


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
