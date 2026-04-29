"""Dashboard data helpers for HTTP views."""

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
) -> dict[str, Any]:
    """Build aggregate extraction stats for dashboard and maintenance views."""
    rows, _ = list_sessions_window(
        limit=500,
        offset=0,
        agent_types=agent_types,
        since=window_start,
        until=window_end,
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
    """Extract model name and tool usage counts from a session JSONL trace."""
    if session_path in _SESSION_DETAILS_CACHE:
        return _SESSION_DETAILS_CACHE[session_path]
    result: dict[str, Any] = {"model": "", "tools": {}}

    def pick_model(row: dict[str, Any], msg_obj: Any, payload_obj: Any) -> str:
        """Pick best-effort model id from known trace formats."""
        msg = msg_obj if isinstance(msg_obj, dict) else {}
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        model_cfg = row.get("modelConfig")
        model_info = row.get("modelInfo")
        payload_info = payload.get("info")
        collab = payload.get("collaboration_mode")
        collab_settings = collab.get("settings") if isinstance(collab, dict) else None
        payload_session = payload.get("session")
        candidates: list[Any] = [
            row.get("model"),
            row.get("model_name"),
            msg.get("model"),
            model_cfg.get("modelName") if isinstance(model_cfg, dict) else "",
            model_info.get("modelName") if isinstance(model_info, dict) else "",
            payload.get("model"),
            payload_info.get("model") if isinstance(payload_info, dict) else "",
            collab_settings.get("model") if isinstance(collab_settings, dict) else "",
            payload_session.get("model") if isinstance(payload_session, dict) else "",
        ]
        for value in candidates:
            text = str(value or "").strip()
            if text:
                return text
        return ""

    try:
        rows = load_jsonl_dict_lines(Path(session_path).expanduser())
        for row in rows:
            msg = row.get("message") if isinstance(row.get("message"), dict) else {}
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}

            picked_model = pick_model(row, msg, payload)
            if picked_model and not result["model"]:
                result["model"] = picked_model

            for block in (
                msg.get("content", []) if isinstance(msg.get("content"), list) else []
            ):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = str(block.get("name", "unknown"))
                    result["tools"][name] = result["tools"].get(name, 0) + 1

            if row.get("tool_name"):
                name = str(row["tool_name"])
                result["tools"][name] = result["tools"].get(name, 0) + 1

            if row.get("type") == "tool_call" and row.get("name"):
                name = str(row["name"])
                result["tools"][name] = result["tools"].get(name, 0) + 1

            if payload.get("type") == "function_call":
                name = str(payload.get("name", "unknown"))
                result["tools"][name] = result["tools"].get(name, 0) + 1
    except Exception:
        pass
    _SESSION_DETAILS_CACHE[session_path] = result
    return result
