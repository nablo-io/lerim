"""Custom trace-folder scanner.

Custom projects are folders of already-clean Lerim canonical JSONL traces. They
are not platform adapters: Lerim does not compact, rewrite, or normalize these
files before ingestion.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from lerim.adapters.base import SessionRecord
from lerim.adapters.common import (
    compute_file_hash,
    parse_timestamp,
    validate_canonical_entry,
)

CUSTOM_AGENT_TYPE = "custom"


def iter_custom_trace_sessions(
    *,
    project_name: str,
    project_path: Path,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[SessionRecord]:
    """Return custom trace sessions from one registered custom project folder."""
    root = project_path.expanduser().resolve()
    if not root.is_dir():
        return []

    sessions: list[SessionRecord] = []
    for trace_path in sorted(root.rglob("*.jsonl")):
        if not trace_path.is_file():
            continue
        try:
            rows = _load_clean_trace(trace_path)
        except ValueError as exc:
            logger.warning(
                "custom trace skipped | project={} path={} error={}",
                project_name,
                trace_path,
                str(exc),
            )
            continue
        if not rows:
            continue

        started_at = _first_timestamp(rows)
        parsed_started_at = parse_timestamp(started_at)
        if not _in_window(parsed_started_at, start=start, end=end):
            continue

        rel_path = trace_path.relative_to(root)
        run_id = _custom_run_id(root=root, relative_path=rel_path)
        sessions.append(
            SessionRecord(
                run_id=run_id,
                agent_type=CUSTOM_AGENT_TYPE,
                session_path=str(trace_path),
                start_time=started_at,
                repo_path=str(root),
                repo_name=project_name,
                status="completed",
                message_count=len(rows),
                tool_call_count=sum(_tool_block_count(row) for row in rows),
                summaries=_summaries(rows),
                content_hash=compute_file_hash(trace_path),
            )
        )
    return sessions


def _load_clean_trace(path: Path) -> list[dict[str, Any]]:
    """Load a strict canonical JSONL trace file."""
    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_number} is not JSON") from exc
        if not isinstance(item, dict) or not validate_canonical_entry(item):
            raise ValueError(f"line {line_number} is not Lerim canonical trace schema")
        rows.append(item)
    if not rows:
        raise ValueError("trace file is empty")
    return rows


def _first_timestamp(rows: list[dict[str, Any]]) -> str | None:
    """Return the first parseable timestamp in the trace."""
    for row in rows:
        timestamp = row.get("timestamp")
        if parse_timestamp(timestamp) is not None:
            return str(timestamp)
    return None


def _in_window(
    started_at: datetime | None,
    *,
    start: datetime | None,
    end: datetime | None,
) -> bool:
    """Return whether a session timestamp overlaps the ingest window."""
    if started_at is None:
        return True
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    if start is not None and started_at < start:
        return False
    if end is not None and started_at > end:
        return False
    return True


def _custom_run_id(*, root: Path, relative_path: Path) -> str:
    """Return a stable custom-session id from project root and relative file path."""
    digest = hashlib.sha1(
        f"{root}\0{relative_path.as_posix()}".encode("utf-8")
    ).hexdigest()[:20]
    return f"custom_{digest}"


def _tool_block_count(row: dict[str, Any]) -> int:
    """Count explicit tool-like blocks in one canonical trace row."""
    content = row.get("message", {}).get("content")
    if not isinstance(content, list):
        return 0
    total = 0
    for block in content:
        if isinstance(block, dict) and str(block.get("type") or "").strip() in {
            "tool_use",
            "tool_result",
            "function_call",
            "function_result",
        }:
            total += 1
    return total


def _summaries(rows: list[dict[str, Any]]) -> list[str]:
    """Build lightweight catalog summaries without rewriting trace content."""
    summaries: list[str] = []
    for row in rows[:8]:
        role = str(row.get("message", {}).get("role") or row.get("type") or "unknown")
        content = row.get("message", {}).get("content")
        if isinstance(content, str):
            text = content.strip().replace("\n", " ")
        else:
            text = json.dumps(content, ensure_ascii=True, default=str)
        if text:
            summaries.append(f"{role}: {text[:300]}")
    return summaries


if __name__ == "__main__":
    """Run a tiny canonical-schema smoke check."""
    sample = {
        "type": "user",
        "message": {"role": "user", "content": "hello"},
        "timestamp": None,
    }
    assert validate_canonical_entry(sample)
