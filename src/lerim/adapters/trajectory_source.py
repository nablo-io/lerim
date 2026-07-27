"""The only parser for native agent harness sessions: Letta's trajectory-v1.

Lerim used to hand-roll one parser per harness. It now discovers and normalizes
every native session through the pinned ``@letta-ai/trajectory`` package, so a
cached trace is a trajectory-v1 record stream:

    {"role":"meta","source":"claude-code","cwd":"...","git_branch":"...","model":"..."}
    {"role":"user","content":"...","timestamp":"..."}
    {"role":"reasoning","content":"...","timestamp":"..."}
    {"role":"assistant","content":null,"tool_calls":[{"id","name","args"}],"timestamp":"..."}
    {"role":"tool","tool_call_id":"...","content":"...","timestamp":"..."}

Discovery and normalization are deliberately separate. :func:`list_sessions`
only reads directory metadata, so it returns ``size_bytes`` and ``updated_at``
per session and a caller can skip unchanged sessions without paying to parse
them. :func:`normalize_batch` then parses a chosen set in as few node
subprocesses as the payload budget allows.

Lerim owns everything downstream of the normalizer: secret redaction (the
library does not redact), the JSONL cache write, the content hash, and the
:class:`~lerim.sessions.types.SessionRecord` the catalog indexes.

Only sources whose listing points at a readable transcript file are wired up.
``hermes`` (SQLite store), ``openhands`` (directory of event files) and
``deepagents`` (LangGraph checkpoints behind a Python bridge) each require
caller-side transcript assembly, which is exactly the per-harness parsing this
module exists to delete; they stay unsupported until upstream can hand Lerim a
transcript. Cursor, OpenCode and pi have no upstream adapter at all.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from lerim.adapters.common import (
    compute_file_hash,
    in_window,
    is_failed_tool_result_text,
    parse_timestamp,
    write_trajectory_jsonl,
)
from lerim.adapters.trajectory_bridge import (
    MAX_BATCH_PAYLOAD_BYTES,
    TRAJECTORY_VERSION,
    TrajectoryErrorCode,
    batch_requests,
    run,
)
from lerim.config.settings import get_trace_cache_dir
from lerim.redaction import redact_text
from lerim.sessions.types import SessionRecord

# Lerim platform id -> trajectory source id. Every entry lists sessions as
# standalone transcript files, which is what makes one read-and-normalize path
# enough for all of them.
SOURCE_MAP: dict[str, str] = {
    "claude": "claude-code",
    "codex": "codex",
    "letta-code": "letta-code",
    "openclaw": "openclaw",
}

# Normalization failures that mean "this transcript has nothing to extract"
# rather than "something went wrong". Abandoned sessions dominate a real store:
# 58 of 82 claude transcripts in a two-day window on one developer machine were
# a single user turn with no reply, and every one logged at warning buried the
# four failures that did need attention. Those are counted and summarised
# instead.
#
# Only this one code qualifies. Its neighbour MISSING_USER_RECORDS looks
# equally benign and is not: upstream returns it for an unparseable transcript
# too (measured — arbitrary text, empty files and valid-JSON-wrong-shape all
# come back as MISSING_USER_RECORDS), so demoting it would silently swallow
# corruption. MISSING_ASSISTANT_RECORDS can only be reached once user records
# have parsed, which makes it unambiguous.
#
# The cost of keeping MISSING_USER_RECORDS loud is small and known: over the
# same 30-day store it warned 9 times in 440 claude sessions, all of them
# transcripts holding only mode/attachment/system records. They are abandoned
# too, but nothing in the response distinguishes them from a damaged file, and
# a warning that is occasionally redundant beats corruption that is never
# reported.
BENIGN_SKIP_CODES: frozenset[str] = frozenset(
    {TrajectoryErrorCode.MISSING_ASSISTANT_RECORDS}
)

# Platforms Lerim knows about but cannot ingest, mapped to the reason shown when
# someone tries to connect them. Kept as data so the registry has one source of
# truth for "known but not ingestible".
UNSUPPORTED_PLATFORMS: dict[str, str] = {
    "cursor": (
        "Cursor is not supported in this release: it stores sessions in a "
        "SQLite workspace database and the trajectory standard has no cursor "
        "adapter yet (https://github.com/letta-ai/trajectory). Lerim's own "
        "Cursor parser was removed when trajectory-v1 became the single trace "
        "format."
    ),
    "hermes": (
        "Hermes is not supported in this release: the trajectory standard has "
        "a hermes adapter, but it normalizes a transcript the caller must "
        "first export from the `sessions`/message tables of the shared "
        "~/.hermes/state.db. Lerim reads a discovered session as one transcript "
        "file, and hand-writing that SQLite export is the custom parsing "
        "trajectory-v1 replaced."
    ),
    "opencode": (
        "OpenCode is not supported in this release: the trajectory standard "
        "has no opencode adapter yet "
        "(https://github.com/letta-ai/trajectory). Lerim's own OpenCode parser "
        "was removed when trajectory-v1 became the single trace format."
    ),
    "openhands": (
        "OpenHands is not supported in this release: the trajectory standard "
        "has an openhands adapter, but it normalizes an event array the caller "
        "must first assemble from the per-session event directory under "
        "~/.openhands/sessions. Lerim reads a discovered session as one "
        "transcript file, and hand-writing that assembly is the custom parsing "
        "trajectory-v1 replaced."
    ),
    "pi": (
        "pi is not supported in this release: the trajectory standard has no "
        "pi adapter yet (https://github.com/letta-ai/trajectory). Lerim's own "
        "pi parser was removed when trajectory-v1 became the single trace "
        "format."
    ),
}

# Tool results dominate transcript size. The deleted adapters erased them
# outright ("[cleared: N chars]"), which also erased every command output the
# extractor could learn from. A 4,000-character head-tail window keeps the
# command and its outcome: measured over 50 real claude-code sessions it still
# compresses 14.8M characters to 1.0M (14.3x) while preserving both ends of
# every truncated result.
TOOL_RESULT_MAX_CHARACTERS = 4_000
TOOL_RESULT_STRATEGY = "head-tail"

# Everything besides the transcript itself that decides what a cached trace
# looks like. A caller that remembers "this transcript already produced a
# session" must also remember which normalizer said so, because after an
# upgrade or a bounds change the cache on disk no longer matches what this code
# would write. Bump it by hand when redaction rules change, since those are
# regex definitions rather than a declared setting.
#
# redaction2: tool call arguments are redacted as decoded JSON values rather
# than as flat text, so caches written by redaction1 can hold arguments whose
# escapes a placeholder truncated into invalid JSON.
NORMALIZER_FINGERPRINT = (
    f"{TRAJECTORY_VERSION}/{TOOL_RESULT_MAX_CHARACTERS}/{TOOL_RESULT_STRATEGY}/redaction2"
)

# Sessions below this many user+assistant turns are eval judge calls, aborted
# starts, and one-shot questions. The Claude adapter dropped them before this
# migration; the threshold now applies to every source.
MIN_CONVERSATION_TURNS = 6

# The catalog indexes `summaries` into full-text search. trajectory-v1 drops
# harness summary records and the listings carry no title, so the opening turn
# is the only real description of the session available.
SUMMARY_MAX_CHARACTERS = 300

# Harness-injected `user` records. Every harness prepends machine-written
# context to the conversation as user turns: codex opens with
# `<recommended_plugins>` and an AGENTS.md dump, Claude Code with a skill
# preamble. Upstream drops four of codex's own prefixes and nothing else, so
# the rest arrive as ordinary user records. They describe the harness, not the
# session, and they are near-identical across sessions: 51 of 60 local codex
# sessions open with the same block, so summarizing from the first user record
# collapses full-text search onto one constant blob. Injected blocks are
# recognizable structurally — an XML-ish container tag, or a fixed
# machine-written header line, all four measured in the local corpus.
_INJECTED_USER_PREFIXES = (
    "# agents.md instructions for",
    "# files mentioned by the user",
    "base directory for this skill:",
    "caveat: the messages below were generated by the user",
)
_XML_BLOCK_HEAD_RE = re.compile(r"^<[a-zA-Z][\w.-]*(\s[^>]*)?>")

# trajectory-v1 carries no token usage. Tokens are estimated from content
# characters at the common ~4 chars/token ratio.
CHARS_PER_TOKEN = 4

# The upstream listing caps a page at 1,000 items.
_LISTING_PAGE_SIZE = 1_000

_TEXT_ROLES = frozenset({"user", "reasoning", "assistant", "tool"})
_CONVERSATION_ROLES = frozenset({"user", "assistant"})

_DEFAULT_ROOTS: dict[str, Path] = {
    "claude": Path("~/.claude/projects"),
    "codex": Path("~/.codex/sessions"),
    "letta-code": Path("~/.letta/transcripts"),
}


class UnsupportedPlatformError(RuntimeError):
    """The requested platform has no trajectory-v1 adapter Lerim can drive."""


@dataclass(frozen=True, slots=True)
class SessionListing:
    """One discovered session, described by directory metadata alone.

    ``size_bytes`` and ``updated_at`` come from the source listing without
    reading the transcript, so a caller can decide whether normalizing this
    session is worth it.
    """

    platform: str
    run_id: str
    path: Path
    updated_at: datetime
    size_bytes: int


def default_root(platform: str) -> Path | None:
    """Return the default local store root for a Lerim platform id."""
    if platform == "openclaw":
        return _openclaw_state_dir()
    root = _DEFAULT_ROOTS.get(platform)
    return root.expanduser() if root is not None else None


def list_sessions(
    platform: str,
    *,
    root: Path | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[SessionListing]:
    """List a platform's sessions, filtered to the ``start``/``end`` window.

    The window is applied to each session's last-modified time, so a long
    session that started earlier but was still active inside the window is
    included. No transcript is read.
    """
    source = source_for(platform)
    listings: list[SessionListing] = []
    cursor: str | None = None
    while True:
        request: dict[str, Any] = {"source": source, "limit": _LISTING_PAGE_SIZE}
        if root is not None:
            request["root"] = str(root)
        if cursor is not None:
            request["cursor"] = cursor
        payload = run([{"list": request}])[0].unwrap()
        for item in payload["items"]:
            listing = _listing_from_item(platform, item)
            if in_window(listing.updated_at, start, end):
                listings.append(listing)
        cursor = payload.get("nextCursor")
        if not cursor:
            return listings


def cached_run_ids(platform: str) -> set[str]:
    """Return the run ids that still have a trace cache file on disk.

    One directory scan, so a caller that skips unchanged sessions can tell
    "already normalized" from "normalized once, cache since deleted" without
    a stat per session. The cache lives under a directory named ``cache``, so
    losing it to a disk cleanup is ordinary, not exotic.
    """
    cache_dir = get_trace_cache_dir(platform)
    if not cache_dir.is_dir():
        return set()
    return {path.stem for path in cache_dir.glob("*.jsonl")}


def normalize_batch(listings: Sequence[SessionListing]) -> list[SessionRecord]:
    """Normalize discovered sessions and cache them as trajectory-v1 JSONL.

    Sessions travel to node in groups, not one subprocess each, but only a
    budget's worth of transcript is held in memory at a time: ``size_bytes``
    from the listing sizes each group before anything is read. A session that
    cannot be read or that the normalizer rejects — abandoned transcripts with
    no assistant turn are the common case — is logged and skipped, never
    allowed to fail its group.
    """
    records: list[SessionRecord] = []
    for group in _budgeted_groups(listings):
        records.extend(_normalize_group(group))
    records.sort(key=lambda record: (record.start_time or "", record.run_id))
    return records


def _budgeted_groups(
    listings: Sequence[SessionListing],
) -> Iterator[list[SessionListing]]:
    """Group listings so one group's transcripts fit the bridge payload budget.

    Sizing on the listing's declared bytes keeps the live set proportional to a
    group instead of the whole corpus. It matters because a transcript is
    expensive in transit: Python holds it as UCS-4 text and the bridge
    serializes it again, so one 41.7 MB session alone costs about 1 GB. Reading
    400 real sessions (343 MB) at once peaked at 2.9 GB against 2.2 GB grouped,
    and the ungrouped figure keeps climbing with corpus size while the grouped
    one does not. A session larger than the budget becomes its own group rather
    than being dropped.
    """
    group: list[SessionListing] = []
    group_bytes = 0
    for listing in listings:
        if group and group_bytes + listing.size_bytes > MAX_BATCH_PAYLOAD_BYTES:
            yield group
            group = []
            group_bytes = 0
        group.append(listing)
        group_bytes += listing.size_bytes
    if group:
        yield group


def _normalize_group(group: Sequence[SessionListing]) -> list[SessionRecord]:
    """Read, normalize, and cache one budgeted group of sessions."""
    pending: list[tuple[SessionListing, dict[str, Any]]] = []
    for listing in group:
        try:
            pending.append((listing, _normalize_request(listing)))
        except OSError as exc:
            logger.warning(
                "session skipped | platform={} run_id={} error={}",
                listing.platform,
                listing.run_id,
                str(exc),
            )
    if not pending:
        return []

    records: list[SessionRecord] = []
    empty_skips: Counter[str] = Counter()
    index = 0
    for batch in batch_requests([request for _, request in pending]):
        for outcome in run(batch):
            listing = pending[index][0]
            index += 1
            if outcome.error is not None:
                code = str(outcome.error.code)
                if code in BENIGN_SKIP_CODES:
                    empty_skips[code] += 1
                    logger.debug(
                        "session skipped | platform={} run_id={} code={}",
                        listing.platform,
                        listing.run_id,
                        code,
                    )
                else:
                    logger.warning(
                        "session skipped | platform={} run_id={} code={} error={}",
                        listing.platform,
                        listing.run_id,
                        code,
                        outcome.error.message,
                    )
                continue
            record = _build_record(listing, outcome.unwrap())
            if record is not None:
                records.append(record)
    if empty_skips:
        logger.info(
            "sessions with nothing to extract | platform={} skipped={} ({})",
            group[0].platform,
            sum(empty_skips.values()),
            ", ".join(f"{code}={count}" for code, count in sorted(empty_skips.items())),
        )
    return records


def source_for(platform: str) -> str:
    """Return the trajectory source id for a Lerim platform id."""
    source = SOURCE_MAP.get(platform)
    if source is not None:
        return source
    reason = UNSUPPORTED_PLATFORMS.get(platform)
    if reason is not None:
        raise UnsupportedPlatformError(reason)
    raise UnsupportedPlatformError(
        f"unknown platform {platform!r}; supported platforms: "
        f"{', '.join(sorted(SOURCE_MAP))}"
    )


def _openclaw_state_dir() -> Path:
    """Resolve OpenClaw's state directory the way OpenClaw itself resolves it."""
    override = (
        os.environ.get("OPENCLAW_STATE_DIR", "").strip()
        or os.environ.get("CLAWDBOT_STATE_DIR", "").strip()
    )
    if override:
        return Path(override).expanduser()
    current = Path("~/.openclaw").expanduser()
    if current.exists():
        return current
    return Path("~/.clawdbot").expanduser()


def _listing_from_item(platform: str, item: dict[str, Any]) -> SessionListing:
    """Convert one raw listing item into a typed :class:`SessionListing`."""
    updated_at = parse_timestamp(item.get("updatedAt"))
    size_bytes = item.get("sizeBytes")
    if updated_at is None or not isinstance(size_bytes, int):
        raise UnsupportedPlatformError(
            f"{platform} listing item {item.get('id')!r} carries no file metadata, "
            "so its transcript is not a readable file"
        )
    return SessionListing(
        platform=platform,
        run_id=_run_id(str(item["id"])),
        path=Path(str(item["path"])),
        updated_at=updated_at,
        size_bytes=size_bytes,
    )


def _run_id(listing_id: str) -> str:
    """Return a listing id usable as a cache filename and URL segment."""
    return listing_id.replace("/", "_")


def _normalize_request(listing: SessionListing) -> dict[str, Any]:
    """Build the bridge normalize request for one discovered session."""
    return {
        "source": SOURCE_MAP[listing.platform],
        "transcript": listing.path.read_text(encoding="utf-8", errors="replace"),
        "bounds": {
            "toolResults": {
                "maxCharacters": TOOL_RESULT_MAX_CHARACTERS,
                "strategy": TOOL_RESULT_STRATEGY,
            }
        },
    }


def _build_record(
    listing: SessionListing, payload: dict[str, Any]
) -> SessionRecord | None:
    """Cache one normalized session and summarize it for the catalog.

    Secrets are scrubbed once, here, before anything derived from the records is
    kept: the summary is persisted to the catalog DB, indexed for full-text
    search, shown in the dashboard, and shipped to the cloud, so redacting only
    the cache file would leak a pasted key everywhere except the file.

    Returns ``None`` for a session that is too short to be worth extracting, or
    whose payload does not open with the required ``meta`` record.
    """
    records: list[dict[str, Any]] = [
        _redact_record(record) for record in payload["records"]
    ]
    if not records or records[0].get("role") != "meta":
        logger.warning(
            "session skipped | platform={} run_id={} error=missing meta record",
            listing.platform,
            listing.run_id,
        )
        return None

    diagnostics = Counter(item["code"] for item in payload["diagnostics"])
    if diagnostics:
        logger.debug(
            "normalization diagnostics | run_id={} counts={}",
            listing.run_id,
            dict(diagnostics),
        )

    message_count = sum(1 for r in records if r["role"] in _CONVERSATION_ROLES)
    if message_count < MIN_CONVERSATION_TURNS:
        return None

    timestamps = [
        parsed
        for parsed in (parse_timestamp(r.get("timestamp")) for r in records)
        if parsed is not None
    ]
    start_time = min(timestamps) if timestamps else None
    duration_ms = (
        int((max(timestamps) - start_time).total_seconds() * 1000)
        if start_time is not None
        else 0
    )
    cwd = records[0].get("cwd")
    cache_path = _write_cache(listing, records)

    return SessionRecord(
        run_id=listing.run_id,
        agent_type=listing.platform,
        session_path=str(cache_path),
        start_time=start_time.isoformat() if start_time is not None else None,
        repo_path=cwd,
        repo_name=Path(cwd).name if cwd else None,
        duration_ms=duration_ms,
        message_count=message_count,
        tool_call_count=sum(len(r.get("tool_calls") or ()) for r in records),
        error_count=_error_count(records),
        total_tokens=_estimate_tokens(records),
        summaries=_summaries(records),
        content_hash=compute_file_hash(cache_path),
    )


def _write_cache(listing: SessionListing, records: list[dict[str, Any]]) -> Path:
    """Cache one session's already-redacted trajectory-v1 records."""
    cache_path = get_trace_cache_dir(listing.platform) / f"{listing.run_id}.jsonl"
    return write_trajectory_jsonl(cache_path, records)


def _redact_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of one record with secrets scrubbed from its text fields."""
    if record.get("role") not in _TEXT_ROLES:
        return record
    redacted = dict(record)
    content = redacted.get("content")
    if isinstance(content, str):
        redacted["content"] = redact_text(content)
    tool_calls = redacted.get("tool_calls")
    if isinstance(tool_calls, list):
        redacted["tool_calls"] = [
            {**call, "args": _redact_arguments(call["args"])} for call in tool_calls
        ]
    return redacted


def _redact_arguments(args: str) -> str:
    """Redact secrets inside a tool call's stringified JSON arguments.

    ``args`` carries a JSON document rather than prose, so redacting it as flat
    text corrupts it. An email pattern happily matches ``nadmin@host`` in the
    escape sequence ``\\nadmin@host``, consuming the ``n`` and leaving a bare
    backslash in front of the placeholder — an invalid escape that stops the
    field parsing as JSON at all. Measured on a real store this hit 21 of
    28,477 tool calls, every one of them a file write whose content held an
    address on a fresh line.

    Decoding first also makes redaction see the values a harness actually
    passed, so a secret spanning an escaped quote is matched instead of missed.
    Arguments that are not valid JSON are redacted as text: there is no
    structure left to protect.
    """
    try:
        decoded = json.loads(args)
    except (TypeError, ValueError):
        return redact_text(args)
    return json.dumps(
        _redact_json_values(decoded), ensure_ascii=False, separators=(",", ":")
    )


def _redact_json_values(value: Any) -> Any:
    """Redact every string inside a decoded JSON value, leaving keys alone.

    Keys are parameter names such as ``file_path``; rewriting one would change
    the call's shape rather than hide a secret.
    """
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redact_json_values(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_json_values(item) for key, item in value.items()}
    return value


def _error_count(records: list[dict[str, Any]]) -> int:
    """Count the tool results whose text reports a failed call.

    trajectory-v1 drops the harness ``is_error`` flag, so the count is read
    from result text with the same rule the extraction pipeline cites failures
    by. It is a lower bound: measured against raw claude ``is_error`` blocks
    joined by ``tool_call_id`` over 150 sessions it catches 81 of 129 real
    failures, and flags 10 of 4,153 successful results, each of which is error
    text the harness itself left unflagged.
    """
    return sum(
        1
        for record in records
        if record["role"] == "tool"
        and isinstance(record.get("content"), str)
        and is_failed_tool_result_text(record["content"])
    )


def _estimate_tokens(records: list[dict[str, Any]]) -> int:
    """Estimate the token volume of a session from its content characters."""
    characters = 0
    for record in records:
        content = record.get("content")
        if isinstance(content, str):
            characters += len(content)
        for call in record.get("tool_calls") or ():
            characters += len(call["name"]) + len(call["args"])
    return characters // CHARS_PER_TOKEN


def _summaries(records: list[dict[str, Any]]) -> list[str]:
    """Return one short description of the session for the catalog and search.

    The opening user turn is preferred, but only when a person typed it. When a
    session has no typed prompt at all — subagent and scheduled runs are driven
    entirely by injected instructions, and 22 of 60 local codex sessions are
    exactly that — the assistant's opening message is what describes the run.
    """
    for record in records:
        if record["role"] != "user":
            continue
        text = " ".join(str(record["content"]).split())
        if text and not _is_injected_user_text(text):
            return [text[:SUMMARY_MAX_CHARACTERS]]
    for record in records:
        content = record.get("content")
        if record["role"] != "assistant" or not isinstance(content, str):
            continue
        text = " ".join(content.split())
        if text:
            return [text[:SUMMARY_MAX_CHARACTERS]]
    return []


def _is_injected_user_text(text: str) -> bool:
    """Return whether a user turn is harness-injected context, not a typed prompt."""
    head = text.lstrip()
    if _XML_BLOCK_HEAD_RE.match(head):
        return True
    return head.lower().startswith(_INJECTED_USER_PREFIXES)


if __name__ == "__main__":
    """Run a real-path smoke test against the local claude-code session store."""
    listings = list_sessions("claude")
    print(f"claude sessions listed: {len(listings)}")

    sample = listings[:6]
    for listing in sample:
        print(f"  {listing.run_id} {listing.size_bytes:>9} bytes {listing.updated_at}")

    for record in normalize_batch(sample):
        cache_lines = Path(record.session_path).read_text(encoding="utf-8").splitlines()
        first = json.loads(cache_lines[0])
        assert first["role"] == "meta", first
        assert all(json.loads(line) for line in cache_lines)
        print(
            f"  {record.run_id}: {len(cache_lines)} records, "
            f"{record.message_count} turns, {record.tool_call_count} tool calls, "
            f"~{record.total_tokens} tokens, repo={record.repo_name}"
        )
