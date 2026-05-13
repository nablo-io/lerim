"""Bridge BAML-selected actions to Lerim's existing extraction tools."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
import json
from pathlib import Path
from typing import Any, Callable

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from lerim.agents import tools as extract_tools
from lerim.agents.toolsets import EXTRACT_TOOLS
from lerim.agents.tools import ContextDeps
from lerim.agents.tools import TRACE_MAX_LINES_PER_READ, _first_uncovered_offset
from lerim.context import ContextStore, ProjectIdentity


TOOL_NAMES = tuple(tool.__name__ for tool in EXTRACT_TOOLS)


@dataclass(frozen=True)
class ToolObservation:
    """Observed result after dispatching one ReAct action."""

    action: str
    ok: bool
    content: str
    args: dict[str, Any]
    done: bool = False
    completion_summary: str = ""


def build_tool_context(deps: ContextDeps) -> RunContext[ContextDeps]:
    """Build the minimal PydanticAI run context required by Lerim tools."""
    return RunContext(deps=deps, model=TestModel(), usage=RunUsage())


def tool_manifest() -> str:
    """Render the live Lerim extraction tool signatures for the BAML prompt."""
    lines: list[str] = []
    for name in TOOL_NAMES:
        func = getattr(extract_tools, name)
        signature = _public_signature(func)
        doc = inspect.getdoc(func) or ""
        first_line = doc.splitlines()[0] if doc else ""
        lines.append(f"- {name}{signature}: {first_line}")
    lines.append(
        "- final_result(completion_summary: str): Finish after exactly one episode exists."
    )
    return "\n".join(lines)


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


def execute_step(
    step: Any,
    ctx: RunContext[ContextDeps],
) -> ToolObservation:
    """Dispatch one BAML-selected step to the matching Lerim tool."""
    action = _action_name(getattr(step, "action", ""))
    forced_read = _read_next_uncovered_chunk(action, ctx)
    if forced_read is not None:
        return forced_read

    if action == "final_result":
        summary = _final_summary(step)
        episode_count = count_current_session_episodes(ctx.deps)
        if episode_count != 1:
            return ToolObservation(
                action=action,
                ok=False,
                content=(
                    "final_result refused: expected exactly one episode record "
                    f"for this session, found {episode_count}."
                ),
                args={},
            )
        return ToolObservation(
            action=action,
            ok=True,
            content=summary,
            args={},
            done=True,
            completion_summary=summary,
        )

    args = _args_for_action(step, action)
    if args is None:
        return ToolObservation(
            action=action,
            ok=False,
            content=f"Missing argument object for action {action}.",
            args={},
        )

    try:
        content = _dispatch_tool(action, ctx, args)
    except ModelRetry as exc:
        content = f"Tool retry needed: {exc}"
        return ToolObservation(action=action, ok=False, content=content, args=args)
    except Exception as exc:
        content = f"Tool error: {type(exc).__name__}: {exc}"
        return ToolObservation(action=action, ok=False, content=content, args=args)
    return ToolObservation(action=action, ok=True, content=content, args=args)


def _read_next_uncovered_chunk(
    action: str,
    ctx: RunContext[ContextDeps],
) -> ToolObservation | None:
    """Force full trace coverage before model-directed non-read actions."""
    if action == "read_trace" or ctx.deps.trace_path is None:
        return None
    try:
        total_lines = sum(
            1 for _ in ctx.deps.trace_path.open("r", encoding="utf-8")
        )
    except OSError:
        return None
    next_offset = _first_uncovered_offset(ctx.deps.read_ranges, total_lines)
    if next_offset is None:
        return None
    args = {
        "start_line": next_offset + 1,
        "line_count": TRACE_MAX_LINES_PER_READ,
    }
    try:
        content = _read_trace(ctx, args)
    except Exception as exc:
        return ToolObservation(
            action="read_trace",
            ok=False,
            content=f"Forced trace read failed: {type(exc).__name__}: {exc}",
            args=args,
        )
    return ToolObservation(action="read_trace", ok=True, content=content, args=args)


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


def format_observation(observation: dict[str, Any], deps: ContextDeps) -> str:
    """Format a tool result as compact scratchpad text for the next BAML call."""
    action = str(observation.get("action") or "")
    status = "ok" if bool(observation.get("ok")) else "error"
    content = _pruned_content(observation, deps)
    return f"Action: {action}\nStatus: {status}\nObservation:\n{content}"


def _dispatch_tool(
    action: str,
    ctx: RunContext[ContextDeps],
    args: dict[str, Any],
) -> str:
    """Call the raw Lerim tool function for one normalized action."""
    handlers: dict[str, Callable[[RunContext[ContextDeps], dict[str, Any]], str]] = {
        "read_trace": _read_trace,
        "search_context": _search_context,
        "get_context": _get_context,
        "save_context": _save_context,
        "revise_context": _revise_context,
        "note_trace_findings": _note_trace_findings,
        "prune_trace_reads": _prune_trace_reads,
    }
    handler = handlers.get(action)
    if handler is None:
        return f"Unknown action: {action}"
    return handler(ctx, args)


def _read_trace(ctx: RunContext[ContextDeps], args: dict[str, Any]) -> str:
    """Call read_trace with defaulted numeric arguments."""
    return extract_tools.read_trace(
        ctx,
        start_line=int(args.get("start_line") or 1),
        line_count=int(args.get("line_count") or extract_tools.TRACE_MAX_LINES_PER_READ),
    )


def _search_context(ctx: RunContext[ContextDeps], args: dict[str, Any]) -> str:
    """Call search_context with only its supported arguments."""
    return extract_tools.search_context(
        ctx,
        query=str(args.get("query") or ""),
        kind=args.get("kind"),
        status=args.get("status"),
        valid_at=args.get("valid_at"),
        include_archived=bool(args.get("include_archived") or False),
        limit=int(args.get("limit") or 8),
    )


def _get_context(ctx: RunContext[ContextDeps], args: dict[str, Any]) -> str:
    """Call get_context with BAML-provided record IDs."""
    return extract_tools.get_context(
        ctx,
        record_ids=list(args.get("record_ids") or []),
        include_versions=bool(args.get("include_versions") or False),
        detail=str(args.get("detail") or "detailed"),
    )


def _save_context(ctx: RunContext[ContextDeps], args: dict[str, Any]) -> str:
    """Call save_context with a complete record payload."""
    return extract_tools.save_context(ctx, **_with_defaults(args, {"status": "active"}))


def _revise_context(ctx: RunContext[ContextDeps], args: dict[str, Any]) -> str:
    """Call revise_context with a complete replacement payload."""
    return extract_tools.revise_context(ctx, **args)


def _note_trace_findings(ctx: RunContext[ContextDeps], args: dict[str, Any]) -> str:
    """Call note_trace_findings, allowing the no-findings checkpoint form."""
    if not any(args.get(name) for name in ("theme", "line", "quote")):
        return extract_tools.note_trace_findings(ctx)
    return extract_tools.note_trace_findings(
        ctx,
        theme=str(args.get("theme") or ""),
        line=args.get("line") or 0,
        quote=str(args.get("quote") or ""),
        level=str(args.get("level") or "implementation"),
    )


def _prune_trace_reads(ctx: RunContext[ContextDeps], args: dict[str, Any]) -> str:
    """Call prune_trace_reads with the start-line list."""
    return extract_tools.prune_trace_reads(
        ctx,
        start_lines=[int(value) for value in args.get("start_lines") or []],
    )


def _pruned_content(observation: dict[str, Any], deps: ContextDeps) -> str:
    """Return a read_trace stub when that chunk has been pruned."""
    action = str(observation.get("action") or "")
    if action != "read_trace":
        return str(observation.get("content") or "")
    args = observation.get("args") if isinstance(observation.get("args"), dict) else {}
    offset = max(0, int(args.get("start_line") or 1) - 1)
    if offset in deps.pruned_offsets:
        return "[pruned]"
    return str(observation.get("content") or "")


def _args_for_action(step: Any, action: str) -> dict[str, Any] | None:
    """Return the BAML argument object matching an action."""
    field_name = action
    payload = getattr(step, field_name, None)
    if payload is None:
        return None
    if hasattr(payload, "model_dump"):
        return _coerce_tool_value(payload.model_dump(exclude_none=True))
    if isinstance(payload, dict):
        return _coerce_tool_value(
            {key: value for key, value in payload.items() if value is not None}
        )
    return _coerce_tool_value(
        json.loads(json.dumps(payload, default=lambda value: value.__dict__))
    )


def _action_name(action: Any) -> str:
    """Normalize a BAML enum value into a Lerim tool name."""
    raw = str(getattr(action, "value", action) or "").strip()
    aliases = {
        "READ_TRACE": "read_trace",
        "SEARCH_CONTEXT": "search_context",
        "GET_CONTEXT": "get_context",
        "SAVE_CONTEXT": "save_context",
        "REVISE_CONTEXT": "revise_context",
        "NOTE_TRACE_FINDINGS": "note_trace_findings",
        "PRUNE_TRACE_READS": "prune_trace_reads",
        "FINAL_RESULT": "final_result",
    }
    return aliases.get(raw, raw.lower())


def _final_summary(step: Any) -> str:
    """Extract final_result.completion_summary from a generated BAML step."""
    payload = getattr(step, "final_result", None)
    if payload is None:
        return ""
    return str(getattr(payload, "completion_summary", "") or "").strip()


def _with_defaults(
    args: dict[str, Any], defaults: dict[str, Any]
) -> dict[str, Any]:
    """Fill omitted optional tool arguments with Lerim's defaults."""
    payload = dict(defaults)
    payload.update(args)
    return payload


def _public_signature(func: Callable[..., str]) -> str:
    """Return a tool signature without the PydanticAI context parameter."""
    signature = inspect.signature(func)
    params = list(signature.parameters.values())
    if params and params[0].name == "ctx":
        params = params[1:]
    return "(" + ", ".join(str(param) for param in params) + ")"


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
