"""History processors for extract-agent orchestration.

These are not tools. They deterministically rewrite or augment the message
history between model turns so the extractor can stay within context limits
while preserving its own intermediate notes.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import replace

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    SystemPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

from lerim.agents.tools import (
    MODEL_CONTEXT_TOKEN_LIMIT,
    _TOKENS_PER_CHAR,
    _classify_context_pressure,
    _first_uncovered_offset,
    ContextDeps,
)
from lerim.context.spec import DURABLE_FINDING_LEVELS, IMPLEMENTATION_FINDING_LEVELS

PRUNED_STUB = "[pruned]"


def notes_state_injector(
    ctx: RunContext[ContextDeps],
    history: list[ModelMessage],
) -> list[ModelMessage]:
    """Inject a compact notes dashboard into the next model request."""
    findings = ctx.deps.notes
    if not findings:
        summary = "NOTES: 0 findings"
        if ctx.deps.findings_checked:
            summary += " (checkpoint recorded)"
    else:
        counts = Counter(f.level for f in findings)
        durable_findings = [f for f in findings if f.level in DURABLE_FINDING_LEVELS]
        theme_source = durable_findings or findings
        themes = Counter(f.theme for f in theme_source)
        durable = sum(counts.get(level, 0) for level in DURABLE_FINDING_LEVELS)
        implementation = sum(
            counts.get(level, 0) for level in IMPLEMENTATION_FINDING_LEVELS
        )
        top_themes = ", ".join(
            f"{theme}({count})" for theme, count in themes.most_common(5)
        )
        summary = (
            f"NOTES: {len(findings)} findings ({durable} durable, {implementation} implementation) "
            f"across {len(themes)} theme(s)"
        )
        if top_themes:
            summary += f"\nTop themes: {top_themes}"
    if ctx.deps.read_ranges:
        next_uncovered = _first_uncovered_offset(
            ctx.deps.read_ranges, int(ctx.deps.trace_total_lines)
        )
        covered_chunks = len(
            {(int(start), int(end)) for start, end in ctx.deps.read_ranges}
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
    """Inject approximate context-pressure information into the next model request."""
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
    summary = (
        f"CONTEXT: {approx_tokens}/{MODEL_CONTEXT_TOKEN_LIMIT} ({pct:.0%}) [{pressure}]"
    )
    injected = list(history)
    injected.append(ModelRequest(parts=[SystemPromptPart(content=summary)]))
    return injected


def prune_history_processor(
    ctx: RunContext[ContextDeps],
    history: list[ModelMessage],
) -> list[ModelMessage]:
    """Rewrite prior read_trace results to tiny stubs for pruned offsets."""
    if not ctx.deps.pruned_offsets:
        return history
    pruned = set(ctx.deps.pruned_offsets)
    rewritten: list[ModelMessage] = []
    pending_offset: int | None = None
    for message in history:
        parts = getattr(message, "parts", []) or []
        new_parts = []
        for part in parts:
            if (
                isinstance(part, ToolCallPart)
                and getattr(part, "tool_name", "") == "read_trace"
            ):
                args = getattr(part, "args", None)
                offset = None
                if isinstance(args, dict):
                    try:
                        offset = max(0, int(args.get("start_line", 1)) - 1)
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
