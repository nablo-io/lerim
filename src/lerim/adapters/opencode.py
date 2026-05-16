"""OpenCode session adapter: reads sessions from OpenCode's SQLite database.

OpenCode stores all session data in a single SQLite database (opencode.db)
under ``~/.local/share/opencode/``.  The schema has three main tables:
``session``, ``message``, and ``part``.  Message and part payloads are JSON
blobs in the ``data`` column.  Timestamps are millisecond-epoch integers.

Like the Cursor adapter, each session is exported to an individual JSONL
cache file so the downstream ingest pipeline and dashboard can read it as
plain text.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from lerim.adapters.base import SessionRecord, ViewerMessage, ViewerSession
from lerim.adapters.common import (
    compact_jsonl,
    compute_file_hash,
    in_window,
    make_canonical_entry,
    normalize_timestamp_iso,
    parse_timestamp,
    readonly_connect,
    validate_canonical_entry,
    write_session_cache,
)
from lerim.config.settings import get_trace_cache_dir


def _clean_entry(obj: dict[str, Any]) -> dict[str, Any] | None:
    """Validate and pass through a canonical OpenCode JSONL entry.

    Entries produced by ``_export_session_jsonl`` are already in canonical
    compacted schema.  This function acts as an idempotency guard:

    1. If the entry is already canonical, ensure any tool_result content
       blocks have their output cleared, then return as-is.
    2. If the entry is not canonical (e.g., old-format cache), return None
       to drop it -- it will be re-exported from the DB on next ingest.
    """
    if not validate_canonical_entry(obj):
        return None

    # Idempotency guard: ensure tool_result blocks are cleared
    content = obj["message"]["content"]
    if isinstance(content, list):
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and "content" in block
            ):
                raw = str(block["content"])
                if not raw.startswith("[cleared:"):
                    block["content"] = f"[cleared: {len(raw)} chars]"

    return obj


def compact_trace(raw_text: str) -> str:
    """Strip tool outputs from OpenCode session JSONL."""
    return compact_jsonl(raw_text, _clean_entry)


def default_path() -> Path | None:
    """Return the default OpenCode storage root."""
    return Path("~/.local/share/opencode/").expanduser()


def _default_cache_dir() -> Path:
    """Return the default cache directory for exported OpenCode JSONL files."""
    return get_trace_cache_dir("opencode")


def _resolve_db_path(root: Path) -> Path | None:
    """Find ``opencode.db`` under *root*."""
    if root.is_file() and root.name == "opencode.db":
        return root
    candidate = root / "opencode.db"
    if candidate.is_file():
        return candidate
    return None


def _json_col(raw: str | None) -> dict[str, Any]:
    """Parse a JSON text column, returning empty dict on failure."""
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return obj if isinstance(obj, dict) else {}


def validate_connection(path: Path) -> dict[str, Any]:
    """Check that *path* resolves to a valid OpenCode DB with data."""
    db_path = _resolve_db_path(path)
    if not db_path:
        return {"ok": False, "error": f"No opencode.db found under {path}"}
    try:
        conn = readonly_connect(db_path)
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for needed in ("session", "message", "part"):
            if needed not in tables:
                conn.close()
                return {
                    "ok": False,
                    "error": f"Table '{needed}' not found in {db_path}",
                }
        sessions = conn.execute("SELECT COUNT(*) FROM session").fetchone()[0]
        messages = conn.execute("SELECT COUNT(*) FROM message").fetchone()[0]
        conn.close()
        return {"ok": True, "sessions": sessions, "messages": messages}
    except sqlite3.Error as exc:
        return {"ok": False, "error": str(exc)}


def count_sessions(path: Path) -> int:
    """Count sessions in the OpenCode database."""
    if not path.exists():
        return 0
    db_path = _resolve_db_path(path)
    if not db_path:
        return 0
    try:
        conn = readonly_connect(db_path)
        n = conn.execute("SELECT COUNT(*) FROM session").fetchone()[0]
        conn.close()
        return n
    except sqlite3.Error:
        return 0


def _read_session_db(db_path: Path, session_id: str) -> ViewerSession | None:
    """Read one OpenCode session directly from the SQLite database."""
    try:
        conn = readonly_connect(db_path)

        sess_row = conn.execute(
            "SELECT directory, version, title FROM session WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not sess_row:
            conn.close()
            return None
        cwd = sess_row["directory"]
        version = sess_row["version"]
        title = sess_row["title"]

        msg_rows = conn.execute(
            "SELECT id, data FROM message WHERE session_id = ? ORDER BY time_created",
            (session_id,),
        ).fetchall()

        total_input = 0
        total_output = 0
        messages: list[ViewerMessage] = []

        for msg_row in msg_rows:
            msg_id = msg_row["id"]
            msg = _json_col(msg_row["data"])
            role = str(msg.get("role") or "assistant")
            time_info = msg.get("time") or {}
            timestamp = parse_timestamp(time_info.get("created"))
            ts_iso = timestamp.isoformat() if timestamp else None

            tokens = msg.get("tokens") or {}
            total_input += int(tokens.get("input") or 0)
            total_output += int(tokens.get("output") or 0)
            total_output += int(tokens.get("reasoning") or 0)

            model_id = msg.get("modelID")

            part_rows = conn.execute(
                "SELECT data FROM part WHERE message_id = ? ORDER BY time_created",
                (msg_id,),
            ).fetchall()

            text_parts: list[str] = []
            for part_row in part_rows:
                part = _json_col(part_row["data"])
                ptype = part.get("type")
                if ptype == "text":
                    text = part.get("text")
                    if isinstance(text, str) and text.strip():
                        text_parts.append(text.strip())
                elif ptype == "tool":
                    tool_name = str(part.get("tool") or "tool")
                    state = part.get("state") or {}
                    tool_ts = parse_timestamp((state.get("time") or {}).get("start"))
                    messages.append(
                        ViewerMessage(
                            role="tool",
                            tool_name=tool_name,
                            tool_input=state.get("input"),
                            tool_output=state.get("output"),
                            timestamp=tool_ts.isoformat() if tool_ts else None,
                        )
                    )

            content = "\n".join(text_parts).strip()
            if content:
                messages.append(
                    ViewerMessage(
                        role=role,
                        content=content,
                        timestamp=ts_iso,
                        model=model_id,
                    )
                )

        conn.close()
        return ViewerSession(
            session_id=session_id,
            cwd=cwd,
            messages=messages,
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            meta={"version": version, "title": title},
        )
    except sqlite3.Error:
        return None


def _export_session_jsonl(session: ViewerSession, out_dir: Path) -> Path:
    """Export a ViewerSession to a compacted JSONL cache file in canonical schema.

    No metadata line is emitted. Every line conforms to the canonical shape:
    ``{"type": "user|assistant", "message": {"role": ..., "content": ...}, "timestamp": "..."}``

    Tool messages are folded into assistant-type entries with structured content
    blocks (tool_use + tool_result with cleared output).
    """
    lines: list[str] = []
    for msg in session.messages:
        ts = normalize_timestamp_iso(msg.timestamp)

        if msg.role == "user":
            if not (msg.content or "").strip():
                continue
            entry = make_canonical_entry("user", "user", msg.content or "", ts)

        elif msg.role == "assistant":
            if not (msg.content or "").strip():
                continue
            entry = make_canonical_entry(
                "assistant", "assistant", msg.content or "", ts
            )

        elif msg.role == "tool":
            tool_name = msg.tool_name or "tool"
            tool_input = msg.tool_input
            tool_output = msg.tool_output
            # Clear tool output, preserving already-cleared descriptors
            output_str = str(tool_output) if tool_output is not None else ""
            if output_str.startswith("[cleared:"):
                descriptor = output_str
            else:
                descriptor = f"[cleared: {len(output_str)} chars]"
            content_blocks: list[dict[str, Any]] = [
                {"type": "tool_use", "name": tool_name, "input": tool_input},
                {"type": "tool_result", "content": descriptor},
            ]
            entry = make_canonical_entry("assistant", "assistant", content_blocks, ts)

        else:
            # Unknown role -- skip
            continue

        lines.append(json.dumps(entry, ensure_ascii=False))

    return write_session_cache(out_dir, session.session_id, lines, compact_trace)


def iter_sessions(
    traces_dir: Path | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    known_run_ids: set[str] | None = None,
    cache_dir: Path | None = None,
) -> list[SessionRecord]:
    """Enumerate OpenCode sessions, export as JSONL, and build session records.

    Reads sessions from the SQLite database, exports each as a JSONL cache
    file in *cache_dir*, and optionally skips sessions already indexed by ID.
    """
    root = traces_dir or default_path()
    if root is None or not root.exists():
        return []
    db_path = _resolve_db_path(root)
    if not db_path:
        return []

    out_dir = cache_dir or _default_cache_dir()

    records: list[SessionRecord] = []
    try:
        conn = readonly_connect(db_path)
        rows = conn.execute(
            """SELECT id, directory, title, time_created FROM session \
ORDER BY time_created, id"""
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return []

    for row in rows:
        sess_id = row["id"]
        directory = row["directory"]
        time_created = row["time_created"]
        start_dt = parse_timestamp(time_created)
        if not in_window(start_dt, start, end):
            continue

        # ID-based skip (before DB read to avoid wasted work)
        if known_run_ids and sess_id in known_run_ids:
            continue

        session = _read_session_db(db_path, session_id=sess_id)
        if session is None:
            continue

        # Export to JSONL cache file
        jsonl_path = _export_session_jsonl(session, out_dir)
        content_hash = compute_file_hash(jsonl_path)

        summaries: list[str] = []
        for msg in session.messages:
            if msg.role in {"user", "assistant"} and (msg.content or "").strip():
                summaries.append((msg.content or "").strip()[:140])
            if len(summaries) >= 5:
                break

        message_count = len(
            [m for m in session.messages if m.role in {"user", "assistant"}]
        )
        tool_calls = len([m for m in session.messages if m.role == "tool"])
        records.append(
            SessionRecord(
                run_id=sess_id,
                agent_type="opencode",
                session_path=str(jsonl_path),
                start_time=start_dt.isoformat() if start_dt else None,
                repo_path=directory or None,
                repo_name=directory or None,
                message_count=message_count,
                tool_call_count=tool_calls,
                total_tokens=session.total_input_tokens + session.total_output_tokens,
                summaries=summaries,
                content_hash=content_hash,
            )
        )

    return records


# ---------------------------------------------------------------------------
# Self-test (runs against real OpenCode DB on this machine)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    root = default_path()
    if root is None or not root.exists():
        print(f"OpenCode not found at {root}, skipping self-test")
        sys.exit(0)

    db = _resolve_db_path(root)
    if not db:
        print(f"No opencode.db under {root}, skipping self-test")
        sys.exit(0)

    print(f"OpenCode storage: {root}")
    print(f"Database: {db} ({db.stat().st_size / 1024 / 1024:.1f} MB)")

    result = validate_connection(root)
    print(f"validate_connection: {result}")
    assert result["ok"], f"validate_connection failed: {result}"

    n = count_sessions(root)
    print(f"count_sessions: {n}")
    assert n > 0, "Expected at least one session"

    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp)
        sessions = iter_sessions(traces_dir=root, cache_dir=cache)
        print(f"iter_sessions: {len(sessions)} sessions")
        assert len(sessions) > 0, "Expected at least one session record"

        with_messages = [s for s in sessions if s.message_count > 0]
        print(f"  sessions with messages: {len(with_messages)}")
        assert with_messages, "Expected at least one session with messages"

        first = with_messages[0]
        jsonl_path = Path(first.session_path)
        assert jsonl_path.is_file(), f"JSONL not found: {jsonl_path}"
        lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 1, f"Expected at least one line, got {len(lines)}"
        print(f"  first JSONL ({jsonl_path.name}): {len(lines)} lines")

        # Verify direct DB read works
        viewer_db = _read_session_db(db, first.run_id)
        assert viewer_db is not None, "read_session(db) returned None"
        print(f"  read_session(db):   {len(viewer_db.messages)} messages")

        print(
            f"    tokens: in={viewer_db.total_input_tokens} out={viewer_db.total_output_tokens}"
        )
        print(f"    title: {viewer_db.meta.get('title', '?')}")

        roles: dict[str, int] = {}
        for m in viewer_db.messages:
            roles[m.role] = roles.get(m.role, 0) + 1
        print(f"    roles: {roles}")

    print("Self-test passed.")
