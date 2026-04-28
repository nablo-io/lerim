"""Codex session adapter for normalized viewer and index records."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from lerim.adapters.base import SessionRecord
from lerim.adapters.common import (
    compact_jsonl,
    compute_file_hash,
    count_non_empty_files,
    in_window,
    load_jsonl_dict_lines,
    make_canonical_entry,
    normalize_timestamp_iso,
    parse_timestamp,
    write_session_cache,
)
from lerim.config.settings import get_trace_cache_dir


def _clean_entry(obj: dict[str, Any]) -> dict[str, Any] | None:
    """Transform a Codex JSONL entry into the canonical compacted schema.

    Drops: session_meta (metadata), non-message event_msg entries,
           developer messages (system prompts), reasoning blocks.
    Transforms response_item entries into canonical
    ``{"type", "message": {"role", "content"}, "timestamp"}`` records.
    """
    line_type = obj.get("type")

    # 1. Drop session metadata entirely
    if line_type == "session_meta":
        return None

    if line_type == "event_msg":
        return _clean_event_msg(obj)

    # 3. response_item entries carry model messages, tool calls, and tool results.
    if line_type != "response_item":
        return None

    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return None

    timestamp = normalize_timestamp_iso(
        obj.get("timestamp") or payload.get("timestamp")
    )
    ptype = payload.get("type")

    # 3a. Drop developer (system prompt) messages
    if ptype == "message" and payload.get("role") == "developer":
        return None

    # 3b. User messages
    if ptype == "message" and payload.get("role") == "user":
        text = _extract_message_text(payload.get("content"))
        if not text:
            return None
        return make_canonical_entry("user", "user", text, timestamp)

    # 3c. Assistant messages -- strip <think> blocks
    if ptype == "message" and payload.get("role") == "assistant":
        text = _extract_message_text(payload.get("content"))
        if not text:
            return None
        text = re.sub(r"<think>[\s\S]*?</think>", "[thinking cleared]", text)
        return make_canonical_entry("assistant", "assistant", text, timestamp)

    # 3d. Function calls
    if ptype == "function_call":
        content = [
            {
                "type": "tool_use",
                "name": payload.get("name", ""),
                "input": payload.get("arguments", ""),
            }
        ]
        return make_canonical_entry("assistant", "assistant", content, timestamp)

    # 3e. Function call outputs -- clear content (idempotent)
    if ptype == "function_call_output":
        output = payload.get("output", "")
        output_str = str(output)
        if output_str.startswith("[cleared:"):
            descriptor = output_str
        else:
            descriptor = f"[cleared: {len(output_str)} chars]"
        content = [{"type": "tool_result", "content": descriptor}]
        return make_canonical_entry("assistant", "assistant", content, timestamp)

    # 3f. Reasoning blocks -- drop
    if ptype == "reasoning":
        return None

    # 4. Any other payload type -- drop
    return None


def _clean_event_msg(obj: dict[str, Any]) -> dict[str, Any] | None:
    """Convert structured Codex user/agent event messages to canonical entries."""
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return None

    event_type = payload.get("type")
    role_by_event = {
        "user_message": "user",
        "agent_message": "assistant",
    }
    role = role_by_event.get(str(event_type))
    if not role:
        return None

    message = payload.get("message")
    if not isinstance(message, str):
        return None
    text = message.strip()
    if not text:
        return None

    timestamp = normalize_timestamp_iso(
        obj.get("timestamp") or payload.get("timestamp")
    )
    return make_canonical_entry(role, role, text, timestamp)


def compact_trace(raw_text: str) -> str:
    """Strip tool outputs and noise from Codex session JSONL."""
    return compact_jsonl(raw_text, _clean_entry)


def _default_cache_dir() -> Path:
    """Return the default cache directory for compacted Codex JSONL files."""
    return get_trace_cache_dir("codex")


def default_path() -> Path | None:
    """Return the default Codex session trace directory."""
    return Path("~/.codex/sessions/").expanduser()


def count_sessions(path: Path) -> int:
    """Count readable non-empty Codex session JSONL files."""
    return count_non_empty_files(path, "*.jsonl")


def _extract_message_text(content: object) -> str | None:
    """Normalize message payload content to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        if parts:
            return "\n".join(parts)
    return None


def iter_sessions(
    traces_dir: Path | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    known_run_ids: set[str] | None = None,
) -> list[SessionRecord]:
    """Enumerate Codex sessions and optionally skip already indexed IDs."""
    base = traces_dir or default_path()
    if base is None or not base.exists():
        return []

    cache_dir = _default_cache_dir()

    records: list[SessionRecord] = []
    for path in base.rglob("*.jsonl"):
        run_id = path.stem
        if known_run_ids and run_id in known_run_ids:
            continue

        entries = load_jsonl_dict_lines(path)
        if not entries:
            continue
        start_time: datetime | None = None
        repo_name: str | None = None
        cwd: str | None = None
        message_count = 0
        tool_calls = 0
        errors = 0
        total_tokens = 0
        summaries: list[str] = []

        for entry in entries:
            payload = entry.get("payload") or {}
            ts = parse_timestamp(
                str(entry.get("timestamp") or payload.get("timestamp") or "")
            )
            if ts:
                if start_time is None or ts < start_time:
                    start_time = ts

            if entry.get("type") == "session_meta" and isinstance(payload, dict):
                git = payload.get("git") or {}
                if isinstance(git, dict) and not repo_name:
                    repo_name = git.get("branch") or None
                if not cwd:
                    cwd = payload.get("cwd") or None

            if entry.get("type") == "event_msg":
                ev_type = payload.get("type")
                if ev_type in {"user_message", "agent_message"}:
                    message_count += 1
                    msg_text = str(payload.get("message") or "").strip()
                    if msg_text:
                        summaries.append(msg_text[:140])
                if ev_type == "token_count":
                    usage = (payload.get("info") or {}).get("last_token_usage", {})
                    if isinstance(usage, dict):
                        total_tokens += int(usage.get("input_tokens", 0) or 0)
                        total_tokens += int(usage.get("output_tokens", 0) or 0)
                        total_tokens += int(
                            usage.get("reasoning_output_tokens", 0) or 0
                        )

            if entry.get("type") == "response_item" and isinstance(payload, dict):
                ptype = payload.get("type")
                if ptype in {"function_call", "custom_tool_call"}:
                    tool_calls += 1
                if ptype in {"function_call_output", "custom_tool_call_output"}:
                    if payload.get("is_error") is True:
                        errors += 1

        if not in_window(start_time, start, end):
            continue

        # Compact and export to cache
        raw_lines = path.read_text(encoding="utf-8").rstrip("\n").split("\n")
        cache_path = write_session_cache(cache_dir, run_id, raw_lines, compact_trace)
        content_hash = compute_file_hash(cache_path)

        records.append(
            SessionRecord(
                run_id=run_id,
                agent_type="codex",
                session_path=str(cache_path),
                start_time=start_time.isoformat() if start_time else None,
                repo_path=cwd,
                repo_name=repo_name,
                message_count=message_count,
                tool_call_count=tool_calls,
                error_count=errors,
                total_tokens=total_tokens,
                summaries=summaries[:5],
                content_hash=content_hash,
            )
        )
    records.sort(key=lambda r: (r.start_time or "", r.run_id))
    return records
