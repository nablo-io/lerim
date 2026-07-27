"""Dashboard data helpers for HTTP views over trajectory-v1 trace caches."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from lerim.adapters.common import load_jsonl_dict_lines
from lerim.sessions.catalog import list_sessions_window

_SESSION_DETAILS_CACHE: dict[str, dict[str, Any]] = {}


def build_extract_report(
    *,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    agent_types: list[str] | None = None,
    repo_path: str | None = None,
) -> dict[str, Any]:
    """Build aggregate extraction stats for dashboard and maintenance views."""
    rows, _ = list_sessions_window(
        limit=500,
        offset=0,
        agent_types=agent_types,
        since=window_start,
        until=window_end,
        repo_path=repo_path,
    )
    totals = defaultdict(int)
    for row in rows:
        totals["sessions"] += int(1)
        totals["messages"] += int(row.get("message_count") or 0)
        totals["tool_calls"] += int(row.get("tool_call_count") or 0)
        totals["errors"] += int(row.get("error_count") or 0)
        totals["tokens"] += int(row.get("total_tokens") or 0)
    return {
        "window_start": window_start.isoformat() if window_start else None,
        "window_end": window_end.isoformat() if window_end else None,
        "agent_filter": ",".join(agent_types) if agent_types else "all",
        "aggregates": {"totals": dict(totals)},
        "narratives": {
            "at_a_glance": {
                "working": "",
                "hindering": "",
                "quick_wins": "",
                "horizon": "",
            }
        },
    }


def extract_session_details(session_path: str) -> dict[str, Any]:
    """Summarize one trajectory-v1 trace cache for the dashboard.

    Returns ``model`` from the leading meta record, ``tools`` counting every
    ``assistant.tool_calls`` entry by name, and ``tool_results`` counting the
    calls that received a ``role: "tool"`` record, matched by ``tool_call_id``.
    A name whose result count trails its call count had calls left unanswered.
    """
    if session_path in _SESSION_DETAILS_CACHE:
        return _SESSION_DETAILS_CACHE[session_path]
    records = load_jsonl_dict_lines(Path(session_path).expanduser())
    model = ""
    tools: dict[str, int] = {}
    tool_results: dict[str, int] = {}
    call_names: dict[str, str] = {}
    for record in records:
        role = str(record.get("role") or "").lower()
        if role == "meta":
            model = model or str(record.get("model") or "").strip()
            continue
        if role == "assistant":
            for call in record.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                name = str(call.get("name") or "unknown")
                tools[name] = tools.get(name, 0) + 1
                call_id = str(call.get("id") or "")
                if call_id:
                    call_names[call_id] = name
            continue
        if role == "tool":
            name = call_names.get(str(record.get("tool_call_id") or ""))
            if name:
                tool_results[name] = tool_results.get(name, 0) + 1
    result: dict[str, Any] = {
        "model": model,
        "tools": tools,
        "tool_results": tool_results,
    }
    _SESSION_DETAILS_CACHE[session_path] = result
    return result
