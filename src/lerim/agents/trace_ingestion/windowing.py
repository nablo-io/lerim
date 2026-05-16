"""Deterministic trace windowing for the trace-ingestion graph."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from lerim.agents.trace_ingestion.state import TraceIngestionGraphState

TRACE_MAX_LINE_BYTES = 5_000
TRACE_MAX_CHUNK_BYTES = 50_000
MODEL_CONTEXT_TOKEN_LIMIT = 200_000
CONTEXT_SOFT_PRESSURE_PCT = 0.60
WINDOW_RESERVE_TOKENS = 30_000
MIN_WINDOW_CHARS = 20_000
MAX_WINDOW_CHARS = TRACE_MAX_CHUNK_BYTES
_TOKENS_PER_CHAR = 0.25


def trace_line_count(trace_path: Path) -> int:
    """Return the number of lines in a trace file."""
    try:
        return sum(1 for _ in trace_path.open("r", encoding="utf-8"))
    except OSError:
        return 0


def compute_request_budget(trace_path: Path) -> int:
    """Scale trace-ingestion request budget from trace size."""
    try:
        line_count = 0
        estimated_bytes = 0
        with trace_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line_count += 1
                estimated_bytes += min(
                    len(line.rstrip("\n").encode("utf-8")),
                    TRACE_MAX_LINE_BYTES,
                )
    except OSError:
        return 50
    line_limited_calls = max(1, math.ceil(estimated_bytes / MAX_WINDOW_CHARS))
    if line_count == 0:
        return 50
    overhead = 80
    return max(50, line_limited_calls + overhead)


def window_char_budget(
    *,
    state: TraceIngestionGraphState,
    run_instruction: str,
    existing_record_manifest: str,
    episode_summary: str,
    durable_findings_summary: str,
    implementation_summary: str,
) -> int:
    """Compute how much raw trace text can fit in the next scan window."""
    soft_tokens = int(MODEL_CONTEXT_TOKEN_LIMIT * CONTEXT_SOFT_PRESSURE_PCT)
    state_text = "\n".join(
        [
            run_instruction,
            existing_record_manifest,
            episode_summary,
            durable_findings_summary,
            implementation_summary,
        ]
    )
    state_tokens = math.ceil(len(state_text) * _TOKENS_PER_CHAR)
    available_tokens = max(
        MIN_WINDOW_CHARS * _TOKENS_PER_CHAR,
        soft_tokens - WINDOW_RESERVE_TOKENS - state_tokens,
    )
    return min(
        MAX_WINDOW_CHARS,
        max(MIN_WINDOW_CHARS, int(available_tokens / _TOKENS_PER_CHAR)),
    )


def read_trace_window(
    *,
    trace_path: Path,
    start_line: int,
    total_lines: int,
    char_budget: int,
) -> dict[str, Any]:
    """Read as many complete trace lines as fit in the character budget."""
    numbered: list[str] = []
    current_chars = 0
    end_line = start_line - 1
    with trace_path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if line_number < start_line:
                continue
            line = raw_line.rstrip("\n")
            if len(line) > TRACE_MAX_LINE_BYTES:
                dropped = len(line) - TRACE_MAX_LINE_BYTES
                line = (
                    line[:TRACE_MAX_LINE_BYTES]
                    + f" ... [truncated {dropped} chars from this line]"
                )
            rendered = f"{line_number}\t{line}"
            if numbered and current_chars + len(rendered) + 1 > char_budget:
                break
            numbered.append(rendered)
            current_chars += len(rendered) + 1
            end_line = line_number
            if current_chars >= char_budget:
                break
    if not numbered and start_line <= total_lines:
        numbered.append(f"{start_line}\t")
        end_line = start_line
    header = f"[{total_lines} lines, window {start_line}-{end_line}]"
    if end_line < total_lines:
        header += f" - next window starts at line {end_line + 1}"
    return {
        "start_line": start_line,
        "end_line": end_line,
        "header": header,
        "text": header + "\n" + "\n".join(numbered),
    }
