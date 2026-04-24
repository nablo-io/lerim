"""Claude desktop session adapter for reading JSONL trace sessions."""

from __future__ import annotations

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
    normalize_timestamp_iso,
    parse_timestamp,
    write_session_cache,
)


_DROP_TYPES = {"progress", "file-history-snapshot", "queue-operation", "pr-link"}
_KEEP_FIELDS = {"type", "message", "timestamp"}
_CANONICAL_TYPES = {"user", "assistant"}


def _clean_entry(obj: dict[str, Any]) -> dict[str, Any] | None:
    """Apply Claude-specific cleaning to a single JSONL entry.

    Drops: progress, file-history-snapshot, queue-operation, pr-link lines.
    Strips: metadata fields not needed for extraction (parentUuid, toolUseResult, etc.).
    Clears: all tool_result content (replaced with size descriptor).
    Clears: thinking block content (replaced with size descriptor).
    """
    if obj.get("type") in _DROP_TYPES:
        return None
    # Drop non-conversation types (system entries contain prompts, not conversation)
    if obj.get("type") not in _CANONICAL_TYPES:
        return None
    # Strip to only conversation-relevant fields
    obj = {k: v for k, v in obj.items() if k in _KEEP_FIELDS}
    # Normalize timestamp to ISO 8601 UTC
    obj["timestamp"] = normalize_timestamp_iso(obj.get("timestamp"))
    # Strip metadata from inner message -- keep only role and content
    msg = obj.get("message")
    if isinstance(msg, dict):
        obj["message"] = {k: v for k, v in msg.items() if k in {"role", "content"}}
        msg = obj["message"]
    # Clear tool_result content and thinking blocks
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    inner = block.get("content", "")
                    if isinstance(inner, str) and not inner.startswith("[cleared:"):
                        block["content"] = f"[cleared: {len(inner)} chars]"
                    elif isinstance(inner, list):
                        total = sum(
                            len(s.get("text", ""))
                            for s in inner
                            if isinstance(s, dict)
                        )
                        block["content"] = f"[cleared: {total} chars]"
                elif block.get("type") == "thinking":
                    text = block.get("thinking", "")
                    if not text.startswith("[thinking cleared:"):
                        block["thinking"] = f"[thinking cleared: {len(text)} chars]"
                    block.pop("signature", None)
    return obj


def compact_trace(raw_text: str) -> str:
    """Strip tool outputs and noise from Claude session JSONL."""
    return compact_jsonl(raw_text, _clean_entry)


def _default_cache_dir() -> Path:
    """Return the default cache directory for compacted Claude JSONL files."""
    return Path("~/.lerim/cache/claude").expanduser()


def default_path() -> Path | None:
    """Return the default Claude traces directory."""
    return Path("~/.claude/projects/").expanduser()


def count_sessions(path: Path) -> int:
    """Count readable non-empty Claude session JSONL files."""
    return count_non_empty_files(path, "*.jsonl")


def iter_sessions(
    traces_dir: Path | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    known_run_ids: set[str] | None = None,
) -> list[SessionRecord]:
    """Enumerate Claude sessions and optionally skip already indexed IDs."""
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

        # Skip subagent/sidechain transcripts — their content flows back to
        # the parent session via tool results, so extracting from both would
        # double-count. Also skip tiny sessions (< 6 conversation turns) which
        # are typically eval judge calls or trivial interactions.
        is_sidechain = any(e.get("isSidechain") for e in entries[:5])
        if is_sidechain:
            continue
        conv_turns = sum(
            1 for e in entries
            if e.get("type") in ("user", "assistant")
        )
        if conv_turns < 6:
            continue

        started_at: datetime | None = None
        repo_name: str | None = None
        cwd: str | None = None
        summaries: list[str] = []
        message_count = 0
        tool_calls = 0
        errors = 0
        total_tokens = 0

        for entry in entries:
            ts = (
                parse_timestamp(str(entry.get("timestamp") or ""))
                if entry.get("timestamp")
                else None
            )
            if ts:
                if started_at is None or ts < started_at:
                    started_at = ts
            if not repo_name:
                repo_name = entry.get("gitBranch") or None
            if not cwd:
                cwd = entry.get("cwd")

            entry_type = entry.get("type")
            if entry_type == "summary":
                summary = str(entry.get("summary") or "").strip()
                if summary:
                    summaries.append(summary)
            elif entry_type in {"user", "assistant", "system"}:
                message_count += 1

            message = entry.get("message")
            if isinstance(message, dict):
                usage = message.get("usage", {})
                if isinstance(usage, dict):
                    total_tokens += int(usage.get("input_tokens", 0) or 0)
                    total_tokens += int(usage.get("output_tokens", 0) or 0)
                content = message.get("content")
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_use":
                            tool_calls += 1
                        if block.get("type") == "tool_result" and block.get("is_error"):
                            errors += 1

        if not in_window(started_at, start, end):
            continue

        # Compact and export to cache
        raw_lines = path.read_text(encoding="utf-8").rstrip("\n").split("\n")
        cache_path = write_session_cache(cache_dir, run_id, raw_lines, compact_trace)
        content_hash = compute_file_hash(cache_path)

        records.append(
            SessionRecord(
                run_id=run_id,
                agent_type="claude",
                session_path=str(cache_path),
                start_time=started_at.isoformat() if started_at else None,
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
