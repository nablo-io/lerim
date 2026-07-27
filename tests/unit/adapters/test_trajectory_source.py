"""Unit tests for the single trajectory-v1 session source.

Every test runs the real node normalizer against real transcript files. The
three properties that break silently if they regress get direct assertions:
parity with upstream's own fixtures, one compact record per line in the written
cache (``line:<N>`` citations depend on it), and redaction before anything
derived from a session is kept.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lerim.adapters import trajectory_source
from lerim.adapters.trajectory_bridge import run
from lerim.adapters.trajectory_source import (
    MIN_CONVERSATION_TURNS,
    SOURCE_MAP,
    SUMMARY_MAX_CHARACTERS,
    TOOL_RESULT_MAX_CHARACTERS,
    UNSUPPORTED_PLATFORMS,
    SessionListing,
    UnsupportedPlatformError,
    cached_run_ids,
    default_root,
    list_sessions,
    normalize_batch,
    source_for,
)
from tests.unit.adapters.conftest import FIXTURE_CASES, TRAJECTORY_FIXTURES_DIR

from tests.trajectory_helpers import (
    BASE_TIME,
    abandoned_transcript,
    assistant_line,
    tool_call_line,
    tool_result_line,
    user_line,
    write_claude_session,
)


@pytest.fixture
def claude_root(trajectory_data_root: Path) -> Path:
    """Return an empty claude-code session store root inside the test data dir."""
    root = trajectory_data_root / "claude-projects"
    root.mkdir(parents=True, exist_ok=True)
    return root


# --------------------------------------------------------------------------
# 1. Fixture parity with upstream
# --------------------------------------------------------------------------


@pytest.mark.parametrize("case", FIXTURE_CASES)
def test_lerim_request_reproduces_upstream_expected_records(case, trajectory_data_root):
    """Lerim's normalize request produces exactly upstream's expected output.

    The fixtures ship with the pinned release, so this pins Lerim to the
    reference implementation's behavior for every source it maps: the request
    Lerim builds (source id plus its tool-result bounds) is what is sent, and
    the whole payload — records and diagnostics — must match byte for byte.
    """
    case_dir = TRAJECTORY_FIXTURES_DIR / case
    source = case.split("/")[0]
    platform = next(key for key, value in SOURCE_MAP.items() if value == source)
    listing = SessionListing(
        platform=platform,
        run_id=case.replace("/", "_"),
        path=case_dir / "input.jsonl",
        updated_at=BASE_TIME,
        size_bytes=(case_dir / "input.jsonl").stat().st_size,
    )
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))

    payload = run([trajectory_source._normalize_request(listing)])[0].unwrap()

    assert payload["records"] == expected["records"]
    assert payload["diagnostics"] == expected["diagnostics"]


@pytest.mark.parametrize("case", FIXTURE_CASES)
def test_upstream_fixture_expectations_are_valid_trajectory_v1(
    case, assert_valid_trajectory
):
    """Upstream's expectations satisfy the schema Lerim validates its own writes against."""
    expected = json.loads(
        (TRAJECTORY_FIXTURES_DIR / case / "expected.json").read_text(encoding="utf-8")
    )
    assert_valid_trajectory(expected["records"])


def test_every_mapped_platform_has_a_parity_fixture():
    """A newly mapped platform cannot ship without upstream parity coverage."""
    assert {case.split("/")[0] for case in FIXTURE_CASES} == set(SOURCE_MAP.values())


# --------------------------------------------------------------------------
# 2. Error tolerance: one rejected session never fails its batch
# --------------------------------------------------------------------------


def test_a_rejected_session_is_skipped_and_logged_while_the_batch_completes(
    claude_root, leveled_log
):
    """An abandoned transcript is skipped by code, and the good ones still index.

    11 of 12 real local sessions are abandoned starts that upstream rejects with
    ``missing_assistant_records``, so a batch that aborted on the first
    rejection would ingest almost nothing.

    Being the common case is also why the skip is quiet: on a real store 58 of
    62 warnings were this, hiding the four that mattered. It stays attributable
    at DEBUG and is summarised once at INFO, but never reaches WARNING.
    """
    write_claude_session(claude_root, "good-one")
    (claude_root / "-workspace-project" / "abandoned.jsonl").write_text(
        abandoned_transcript(), encoding="utf-8"
    )
    write_claude_session(claude_root, "good-two")

    listings = list_sessions("claude", root=claude_root)
    sessions = normalize_batch(listings)

    assert len(listings) == 3
    assert sorted(session.run_id for session in sessions) == ["good-one", "good-two"]
    skipped = [line for line in leveled_log if "abandoned" in line]
    assert [line.split("|")[0] for line in skipped] == ["DEBUG"]
    assert "missing_assistant_records" in skipped[0]
    summary = [line for line in leveled_log if "nothing to extract" in line]
    assert len(summary) == 1
    assert summary[0].startswith("INFO|")
    assert "skipped=1" in summary[0]


def test_a_transcript_that_parses_to_nothing_is_still_reported_loudly(
    claude_root, warning_log
):
    """Corruption keeps its warning even though its neighbouring code is quiet.

    Upstream answers an unparseable transcript with ``missing_user_records``,
    which reads like the abandoned-session case but is not one. Only
    ``missing_assistant_records`` is demoted, so a file that decodes to nothing
    stays visible instead of being counted as an ordinary empty session.
    """
    write_claude_session(claude_root, "healthy")
    (claude_root / "-workspace-project" / "corrupt.jsonl").write_text(
        "this is not a transcript at all\n", encoding="utf-8"
    )

    sessions = normalize_batch(list_sessions("claude", root=claude_root))

    assert [session.run_id for session in sessions] == ["healthy"]
    logged = "".join(warning_log)
    assert "corrupt" in logged
    assert "missing_user_records" in logged


def test_an_unreadable_transcript_is_skipped_without_failing_its_group(
    claude_root, warning_log
):
    """A transcript deleted between listing and reading costs only that session."""
    write_claude_session(claude_root, "survivor")
    doomed = write_claude_session(claude_root, "deleted-in-flight")

    listings = list_sessions("claude", root=claude_root)
    doomed.unlink()
    sessions = normalize_batch(listings)

    assert [session.run_id for session in sessions] == ["survivor"]
    assert "deleted-in-flight" in "".join(warning_log)


def test_a_session_below_the_turn_threshold_is_dropped(claude_root):
    """Sessions too short to extract from never reach the catalog."""
    write_claude_session(claude_root, "too-short", turns=MIN_CONVERSATION_TURNS - 1)
    write_claude_session(claude_root, "long-enough", turns=MIN_CONVERSATION_TURNS)

    sessions = normalize_batch(list_sessions("claude", root=claude_root))

    assert [session.run_id for session in sessions] == ["long-enough"]
    assert sessions[0].message_count >= MIN_CONVERSATION_TURNS


# --------------------------------------------------------------------------
# 3./4. Written cache files: schema valid, one compact record per line
# --------------------------------------------------------------------------


def test_written_cache_files_are_schema_valid_one_record_per_line(
    claude_root, assert_valid_trace_file
):
    """Every trace Lerim writes validates and keeps `line:<N>` citations addressable."""
    write_claude_session(claude_root, "sess-a")
    write_claude_session(claude_root, "sess-b", project="-workspace-other")

    sessions = normalize_batch(list_sessions("claude", root=claude_root))

    assert len(sessions) == 2
    for session in sessions:
        records = assert_valid_trace_file(Path(session.session_path))
        assert records[0]["source"] == "claude-code"


def test_a_paragraph_separator_in_a_transcript_stays_on_one_cache_line(
    claude_root, assert_valid_trace_file
):
    """U+2028 survives as data without becoming a line break in the cache file.

    ``json.dumps(ensure_ascii=False)`` emits U+2028/U+2029/U+0085 raw and
    ``str.splitlines()`` treats all three as line breaks, so an unescaped one
    would shift every later `line:<N>` citation in that file.
    """
    separator = "\u2028"
    write_claude_session(
        claude_root,
        "sess-sep",
        tool_result=f"var a=1;{separator}var b=2;",
    )

    sessions = normalize_batch(list_sessions("claude", root=claude_root))

    cache_path = Path(sessions[0].session_path)
    raw = cache_path.read_text(encoding="utf-8")
    assert separator not in raw
    records = assert_valid_trace_file(cache_path)
    assert any(
        separator in str(record.get("content") or "") for record in records
    ), "the separator must round-trip as content, not be stripped"


def test_reasoning_and_tool_links_survive_into_the_cache(claude_root):
    """The two headline wins of the migration are present in what Lerim stores."""
    write_claude_session(claude_root, "sess-rich")

    sessions = normalize_batch(list_sessions("claude", root=claude_root))

    records = [
        json.loads(line)
        for line in Path(sessions[0].session_path)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(record["role"] == "reasoning" for record in records)
    call_ids = {
        call["id"]
        for record in records
        for call in record.get("tool_calls") or ()
    }
    result_ids = {
        record["tool_call_id"] for record in records if record["role"] == "tool"
    }
    assert call_ids and call_ids == result_ids


def test_tool_results_are_bounded_rather_than_erased(claude_root):
    """A huge tool result is truncated to the configured window, not blanked."""
    write_claude_session(
        claude_root, "sess-big", tool_result="HEAD" + "x" * 40_000 + "TAIL"
    )

    sessions = normalize_batch(list_sessions("claude", root=claude_root))

    records = [
        json.loads(line)
        for line in Path(sessions[0].session_path)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    result = next(record for record in records if record["role"] == "tool")
    assert len(result["content"]) <= TOOL_RESULT_MAX_CHARACTERS * 2
    assert result["content"].startswith("HEAD")
    assert result["content"].endswith("TAIL")


# --------------------------------------------------------------------------
# 5. Redaction — trajectory does not redact, Lerim must
# --------------------------------------------------------------------------


def test_secrets_never_reach_the_cache_file_or_the_summary(claude_root):
    """A pasted key is scrubbed from the stored trace and from everything derived.

    The summary is persisted to the catalog DB, indexed for search, rendered in
    the dashboard and shipped to the cloud, so redacting only the file would
    leak the key everywhere except the file.
    """
    secret = "sk-abcdefghijklmnopqrstuvwx1234"
    write_claude_session(
        claude_root,
        "sess-secret",
        first_user_text=f"deploy with {secret} please",
        tool_result=f"AWS_SECRET_ACCESS_KEY={'A' * 40}\ncontact ops@example.com",
    )

    sessions = normalize_batch(list_sessions("claude", root=claude_root))

    cache_text = Path(sessions[0].session_path).read_text(encoding="utf-8")
    assert secret not in cache_text
    assert "ops@example.com" not in cache_text
    assert "A" * 40 not in cache_text
    assert "[REDACTED:api_key]" in cache_text
    assert secret not in " ".join(sessions[0].summaries)


def test_secrets_in_tool_call_arguments_are_redacted(claude_root):
    """Arguments are a JSON string on the record, and are scrubbed like content."""
    secret = "ghp_abcdefghijklmnopqrstuvwxyz012345"
    write_claude_session(
        claude_root,
        "sess-args",
        extra_lines=(
            tool_call_line(20, "toolu_02B", "Bash", {"command": f"curl -H {secret}"}),
            tool_result_line(21, "toolu_02B", "ok"),
        ),
    )

    sessions = normalize_batch(list_sessions("claude", root=claude_root))

    cache_text = Path(sessions[0].session_path).read_text(encoding="utf-8")
    assert secret not in cache_text
    assert "[REDACTED:github_token]" in cache_text


def test_redacting_arguments_leaves_them_parseable_json(claude_root):
    """A secret on a fresh line is scrubbed without breaking the JSON around it.

    ``args`` is a JSON document, and redacting it as flat text let the email
    pattern match ``nops@example.com`` inside the escape ``\\nops@example.com``.
    That consumed the ``n`` and left a bare backslash before the placeholder, so
    the field no longer parsed — silently, because nothing on the write path
    decodes it. It hit 21 of 28,477 tool calls on a real store.
    """
    written = 'print("hi")\nops@example.com\nprint("bye")'
    write_claude_session(
        claude_root,
        "sess-args-json",
        extra_lines=(
            tool_call_line(
                20, "toolu_02C", "Write", {"file_path": "/tmp/x.py", "content": written}
            ),
            tool_result_line(21, "toolu_02C", "ok"),
        ),
    )

    sessions = normalize_batch(list_sessions("claude", root=claude_root))

    records = [
        json.loads(line)
        for line in Path(sessions[0].session_path).read_text(encoding="utf-8").split("\n")
        if line
    ]
    calls = [call for r in records for call in r.get("tool_calls") or []]
    written_call = next(call for call in calls if call["name"] == "Write")
    arguments = json.loads(written_call["args"])

    assert "ops@example.com" not in written_call["args"]
    assert arguments["content"] == 'print("hi")\n[REDACTED:email]\nprint("bye")'
    assert arguments["file_path"] == "/tmp/x.py"


# --------------------------------------------------------------------------
# 6. Source mapping
# --------------------------------------------------------------------------


@pytest.mark.parametrize("platform", sorted(UNSUPPORTED_PLATFORMS))
def test_a_dropped_platform_raises_a_specific_explanation(platform):
    """Cursor, OpenCode and pi explain the regression instead of KeyError-ing."""
    with pytest.raises(UnsupportedPlatformError) as excinfo:
        source_for(platform)

    message = str(excinfo.value)
    assert platform.replace("-", "") in message.lower().replace("-", "")
    assert "trajectory" in message
    assert message == UNSUPPORTED_PLATFORMS[platform]


@pytest.mark.parametrize("platform", sorted(UNSUPPORTED_PLATFORMS))
def test_a_dropped_platform_has_no_default_store_path(platform):
    """A platform Lerim cannot read must not advertise a path to read it from."""
    assert default_root(platform) is None


def test_an_unknown_platform_names_the_supported_set():
    """An unrecognized name gets the list of what does work, not a bare KeyError."""
    with pytest.raises(UnsupportedPlatformError) as excinfo:
        source_for("no-such-harness")

    message = str(excinfo.value)
    assert "no-such-harness" in message
    for platform in SOURCE_MAP:
        assert platform in message


@pytest.mark.parametrize(("platform", "source"), sorted(SOURCE_MAP.items()))
def test_supported_platforms_map_to_their_trajectory_source(platform, source):
    """Every mapped platform resolves to the source id the bridge expects."""
    assert source_for(platform) == source


@pytest.mark.parametrize("platform", sorted(UNSUPPORTED_PLATFORMS))
def test_listing_a_dropped_platform_raises_instead_of_returning_nothing(platform):
    """Discovery fails loudly for a dropped platform rather than reporting 0 sessions."""
    with pytest.raises(UnsupportedPlatformError):
        list_sessions(platform)


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def test_listing_reports_size_and_mtime_without_reading_transcripts(claude_root):
    """The listing carries exactly the metadata the ingest pre-filter needs."""
    path = write_claude_session(claude_root, "sess-meta")

    listings = list_sessions("claude", root=claude_root)

    assert len(listings) == 1
    listing = listings[0]
    assert listing.platform == "claude"
    assert listing.run_id == "sess-meta"
    assert listing.path == path
    assert listing.size_bytes == path.stat().st_size
    assert listing.updated_at.tzinfo is not None


def test_listing_filters_on_the_requested_window(claude_root):
    """Only sessions touched inside the window are listed."""
    old = write_claude_session(claude_root, "old-session")
    write_claude_session(claude_root, "new-session")
    stale = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(old, (stale, stale))

    recent = list_sessions(
        "claude",
        root=claude_root,
        start=datetime.now(timezone.utc) - timedelta(days=1),
    )
    everything = list_sessions("claude", root=claude_root)

    assert [listing.run_id for listing in recent] == ["new-session"]
    assert sorted(listing.run_id for listing in everything) == [
        "new-session",
        "old-session",
    ]


def test_cached_run_ids_reports_what_is_actually_on_disk(claude_root):
    """The cache scan distinguishes 'never normalized' from 'cache since deleted'."""
    write_claude_session(claude_root, "sess-cached")
    assert cached_run_ids("claude") == set()

    sessions = normalize_batch(list_sessions("claude", root=claude_root))

    assert cached_run_ids("claude") == {"sess-cached"}
    Path(sessions[0].session_path).unlink()
    assert cached_run_ids("claude") == set()


def test_normalizing_no_listings_does_no_work(trajectory_data_root):
    """An empty discovery result never reaches the bridge."""
    assert normalize_batch([]) == []


def test_a_batch_larger_than_the_budget_still_normalizes_every_session(
    claude_root, monkeypatch
):
    """Grouping by declared size bounds memory without dropping sessions."""
    monkeypatch.setattr(trajectory_source, "MAX_BATCH_PAYLOAD_BYTES", 512)
    for index in range(5):
        write_claude_session(claude_root, f"sess-{index}")

    sessions = normalize_batch(list_sessions("claude", root=claude_root))

    assert sorted(session.run_id for session in sessions) == [
        f"sess-{index}" for index in range(5)
    ]


# --------------------------------------------------------------------------
# Catalog record derivation
# --------------------------------------------------------------------------


def test_session_record_fields_are_derived_from_the_normalized_records(claude_root):
    """The catalog row describes the session the cache file actually holds."""
    write_claude_session(
        claude_root, "sess-fields", cwd="/workspace/alpha", branch="feature/x"
    )

    session = normalize_batch(list_sessions("claude", root=claude_root))[0]

    records = [
        json.loads(line)
        for line in Path(session.session_path)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert session.agent_type == "claude"
    assert session.repo_path == "/workspace/alpha"
    assert session.repo_name == "alpha"
    assert session.status == "completed"
    assert session.message_count == sum(
        1 for record in records if record["role"] in {"user", "assistant"}
    )
    assert session.tool_call_count == sum(
        len(record.get("tool_calls") or ()) for record in records
    )
    assert session.total_tokens > 0
    assert session.duration_ms > 0
    assert datetime.fromisoformat(session.start_time) == min(
        datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
        for record in records
        if "timestamp" in record
    )
    assert session.content_hash


def test_the_git_branch_stays_recoverable_from_the_cache(claude_root):
    """`repo_name` is the directory name now, so the branch must live in `meta`."""
    write_claude_session(claude_root, "sess-branch", branch="feature/trajectory")

    session = normalize_batch(list_sessions("claude", root=claude_root))[0]

    meta = json.loads(
        Path(session.session_path).read_text(encoding="utf-8").splitlines()[0]
    )
    assert meta["git_branch"] == "feature/trajectory"
    assert session.repo_name != "feature/trajectory"


def test_failed_tool_results_are_counted(claude_root):
    """`error_count` reflects tool results whose text reports a failure."""
    write_claude_session(claude_root, "sess-ok", tool_result="1\tdef retry():")
    write_claude_session(
        claude_root, "sess-bad", tool_result="Error: command not found: retry"
    )

    sessions = {
        session.run_id: session
        for session in normalize_batch(list_sessions("claude", root=claude_root))
    }

    assert sessions["sess-ok"].error_count == 0
    assert sessions["sess-bad"].error_count == 1


def test_the_summary_is_the_first_typed_user_turn(claude_root):
    """Search and the dashboard describe a session by what the person asked for."""
    write_claude_session(
        claude_root, "sess-summary", first_user_text="  make the retry test   stable  "
    )

    session = normalize_batch(list_sessions("claude", root=claude_root))[0]

    assert session.summaries == ["make the retry test stable"]


@pytest.mark.parametrize(
    "injected",
    [
        "<system-reminder>Read AGENTS.md before you start.</system-reminder>",
        "# AGENTS.md instructions for /workspace/project\n\nRun ruff before committing.",
        "Caveat: the messages below were generated by the user while running a command.",
    ],
)
def test_harness_injected_context_is_not_used_as_a_summary(claude_root, injected):
    """Machine-written preambles are near-identical across sessions, so they are skipped.

    Summarizing from them would collapse full-text search onto one constant
    blob shared by every session that harness ever produced.
    """
    path = claude_root / "-workspace-project" / "sess-injected.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                user_line(1, injected, cwd="/workspace/project", branch="main"),
                assistant_line(2, "Understood."),
                user_line(
                    3, "profile the parser", cwd="/workspace/project", branch="main"
                ),
                assistant_line(4, "Profiling now."),
                user_line(5, "and the writer", cwd="/workspace/project", branch="main"),
                assistant_line(6, "Done."),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    session = normalize_batch(list_sessions("claude", root=claude_root))[0]

    assert session.summaries == ["profile the parser"]


def test_a_long_opening_turn_is_truncated_for_the_summary(claude_root):
    """Summaries stay bounded so one pasted essay cannot dominate the search index."""
    write_claude_session(claude_root, "sess-long", first_user_text="w " * 1_000)

    session = normalize_batch(list_sessions("claude", root=claude_root))[0]

    assert len(session.summaries[0]) == SUMMARY_MAX_CHARACTERS
