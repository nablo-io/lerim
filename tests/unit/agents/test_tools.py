"""Comprehensive unit tests for lerim.agents.tools."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pydantic_ai import ModelRetry
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

from lerim.agents.history_processors import (
    context_pressure_injector,
    notes_state_injector,
    prune_history_processor,
)
from lerim.agents.tools import (
    CONTEXT_HARD_PRESSURE_PCT,
    CONTEXT_SOFT_PRESSURE_PCT,
    MODEL_CONTEXT_TOKEN_LIMIT,
    PRUNED_STUB,
    TRACE_MAX_LINE_BYTES,
    TRACE_MAX_LINES_PER_READ,
    ContextDeps,
    Finding,
    _classify_context_pressure,
    _first_uncovered_offset,
    _maybe_raise_record_retry,
    _normalize_kind,
    _normalize_status,
    _require_full_trace_coverage_before_write,
    _require_notes_before_long_trace_write,
    _store,
    archive_record,
    compute_request_budget,
    context_query,
    create_record,
    fetch_records,
    list_records,
    note,
    prune,
    search_records,
    supersede_record,
    trace_read,
    update_record,
)
from lerim.context import ContextStore
from tests.unit.agents.conftest import make_run_context


@pytest.fixture
def deps_with_session(deps):
    store = ContextStore(deps.context_db_path)
    store.initialize()
    store.register_project(deps.project_identity)
    _seed_session(store, deps.project_identity.project_id)
    return deps


@pytest.fixture
def mock_embeddings(monkeypatch):
    provider = MagicMock()
    provider.embedding_dims = 384
    provider.model_id = "test-model"
    provider.embed_document.return_value = [0.1] * 384
    provider.embed_query.return_value = [0.1] * 384
    monkeypatch.setattr("lerim.context.store.get_embedding_provider", lambda: provider)
    monkeypatch.setattr(
        "lerim.context.embedding.get_embedding_provider", lambda: provider
    )


def _make_trace(path: Path, lines: int) -> Path:
    content = "\n".join(f'{{"type":"msg","i":{i}}}' for i in range(lines))
    path.write_text(content, encoding="utf-8")
    return path


def _seed_session(store, project_id: str, session_id: str = "sess_test") -> None:
    store.upsert_session(
        project_id=project_id,
        session_id=session_id,
        agent_type="test",
        source_trace_ref="test.jsonl",
        repo_path="/tmp/test",
        cwd="/tmp/test",
        started_at="2026-01-01T00:00:00Z",
        model_name="test-model",
        instructions_text=None,
        prompt_text=None,
    )


# ---------------------------------------------------------------------------
# _normalize_kind
# ---------------------------------------------------------------------------


class TestNormalizeKind:
    def test_lowercase(self):
        assert _normalize_kind("Decision") == "decision"

    def test_strip(self):
        assert _normalize_kind("  fact  ") == "fact"

    def test_empty_string(self):
        assert _normalize_kind("") == ""

    def test_none(self):
        assert _normalize_kind(None) == ""


# ---------------------------------------------------------------------------
# _normalize_status
# ---------------------------------------------------------------------------


class TestNormalizeStatus:
    def test_lowercase(self):
        assert _normalize_status("Active") == "active"

    def test_strip(self):
        assert _normalize_status("  archived  ") == "archived"

    def test_empty_returns_active(self):
        assert _normalize_status("") == "active"

    def test_none_returns_active(self):
        assert _normalize_status(None) == "active"


# ---------------------------------------------------------------------------
# _maybe_raise_record_retry
# ---------------------------------------------------------------------------


class TestMaybeRaiseRecordRetry:
    @pytest.mark.parametrize(
        "code",
        [
            "title_required",
            "body_required",
            "title_too_long",
            "decision_requires_decision_and_why",
            "episode_requires_session_id",
            "episode_requires_user_intent_and_what_happened",
            "duplicate_episode_for_session",
            "episode_body_too_long",
            "episode_user_intent_too_long",
            "episode_what_happened_too_long",
            "episode_outcomes_too_long",
            "record_body_too_long",
        ],
    )
    def test_known_codes_raise_model_retry(self, code):
        with pytest.raises(ModelRetry):
            _maybe_raise_record_retry(ValueError(code))

    def test_unknown_code_does_not_raise(self):
        _maybe_raise_record_retry(ValueError("unknown_error"))

    def test_empty_does_not_raise(self):
        _maybe_raise_record_retry(ValueError(""))

    def test_retry_message_content(self):
        with pytest.raises(ModelRetry, match="non-empty title"):
            _maybe_raise_record_retry(ValueError("title_required"))


# ---------------------------------------------------------------------------
# _first_uncovered_offset
# ---------------------------------------------------------------------------


class TestFirstUncoveredOffset:
    def test_no_ranges_returns_zero(self):
        assert _first_uncovered_offset([], 10) == 0

    def test_total_zero_returns_none(self):
        assert _first_uncovered_offset([], 0) is None

    def test_fully_covered_returns_none(self):
        assert _first_uncovered_offset([(0, 10)], 10) is None

    def test_gap_at_start(self):
        assert _first_uncovered_offset([(5, 10)], 10) == 0

    def test_gap_in_middle(self):
        assert _first_uncovered_offset([(0, 3), (7, 10)], 10) == 3

    def test_truncation_at_end(self):
        assert _first_uncovered_offset([(0, 5)], 10) == 5

    def test_overlapping_ranges_merged(self):
        assert _first_uncovered_offset([(0, 4), (3, 8)], 8) is None

    def test_single_range_partial(self):
        assert _first_uncovered_offset([(0, 5)], 20) == 5


# ---------------------------------------------------------------------------
# _classify_context_pressure
# ---------------------------------------------------------------------------


class TestClassifyContextPressure:
    def test_normal_below_soft(self):
        assert _classify_context_pressure(0.3) == "normal"

    def test_soft_at_threshold(self):
        assert _classify_context_pressure(CONTEXT_SOFT_PRESSURE_PCT) == "soft"

    def test_soft_between(self):
        assert _classify_context_pressure(0.7) == "soft"

    def test_hard_at_threshold(self):
        assert _classify_context_pressure(CONTEXT_HARD_PRESSURE_PCT) == "hard"

    def test_hard_above(self):
        assert _classify_context_pressure(0.95) == "hard"


# ---------------------------------------------------------------------------
# _store
# ---------------------------------------------------------------------------


class TestStore:
    def test_creates_initialized_store(self, deps):
        ctx = make_run_context(deps)
        store = _store(ctx)
        assert isinstance(store, ContextStore)


# ---------------------------------------------------------------------------
# compute_request_budget
# ---------------------------------------------------------------------------


class TestComputeRequestBudget:
    def test_zero_lines(self, tmp_path):
        p = tmp_path / "trace.jsonl"
        p.write_text("", encoding="utf-8")
        assert compute_request_budget(p) == 40

    def test_200_lines(self, tmp_path):
        p = _make_trace(tmp_path / "t.jsonl", 200)
        assert compute_request_budget(p) == 40

    def test_500_lines(self, tmp_path):
        p = _make_trace(tmp_path / "t.jsonl", 500)
        budget = compute_request_budget(p)
        assert budget == 45

    def test_1000_lines(self, tmp_path):
        p = _make_trace(tmp_path / "t.jsonl", 1000)
        budget = compute_request_budget(p)
        assert budget == 50

    def test_5000_lines(self, tmp_path):
        p = _make_trace(tmp_path / "t.jsonl", 5000)
        assert compute_request_budget(p) == 100

    def test_missing_file(self, tmp_path):
        p = tmp_path / "nonexistent.jsonl"
        assert compute_request_budget(p) == 40

    def test_100_lines(self, tmp_path):
        p = _make_trace(tmp_path / "t.jsonl", 100)
        assert compute_request_budget(p) == 40


# ---------------------------------------------------------------------------
# trace_read
# ---------------------------------------------------------------------------


class TestTraceRead:
    def test_no_trace_path(self, deps):
        ctx = make_run_context(deps)
        result = trace_read(ctx)
        assert "no trace path" in result

    def test_basic_read(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        result = trace_read(ctx, offset=0, limit=5)
        assert "[10 lines" in result
        assert "showing 1-5" in result
        assert "5 more lines" in result
        assert len(deps_with_trace.read_ranges) == 1
        assert deps_with_trace.read_ranges[0] == (0, 5)

    def test_read_full(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        result = trace_read(ctx, offset=0, limit=100)
        assert "showing 1-10" in result
        assert "more lines" not in result

    def test_offset_past_end(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        result = trace_read(ctx, offset=100)
        assert "[10 lines" in result

    def test_limit_zero_clamped(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        result = trace_read(ctx, offset=0, limit=0)
        assert "[10 lines" in result

    def test_limit_over_max_clamped(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        result = trace_read(ctx, offset=0, limit=999)
        assert "[10 lines" in result

    def test_line_truncation(self, tmp_path, project_identity):
        trace_path = tmp_path / "trace.jsonl"
        long_line = "x" * (TRACE_MAX_LINE_BYTES + 500)
        trace_path.write_text(long_line, encoding="utf-8")
        deps = ContextDeps(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=project_identity,
            session_id="sess_test",
            trace_path=trace_path,
            trace_total_lines=0,
        )
        ctx = make_run_context(deps)
        result = trace_read(ctx, offset=0, limit=10)
        assert "truncated" in result

    def test_chunk_byte_truncation(self, tmp_path, project_identity):
        trace_path = tmp_path / "trace.jsonl"
        lines = ["x" * 4000 for _ in range(20)]
        trace_path.write_text("\n".join(lines), encoding="utf-8")
        deps = ContextDeps(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=project_identity,
            session_id="sess_test",
            trace_path=trace_path,
            trace_total_lines=0,
        )
        ctx = make_run_context(deps)
        result = trace_read(ctx, offset=0, limit=20)
        numbered_lines = [l for l in result.split("\n") if "\t" in l]
        assert len(numbered_lines) < 20

    def test_read_ranges_tracking(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        trace_read(ctx, offset=0, limit=5)
        trace_read(ctx, offset=5, limit=5)
        assert len(deps_with_trace.read_ranges) == 2
        assert deps_with_trace.read_ranges[0] == (0, 5)
        assert deps_with_trace.read_ranges[1] == (5, 10)


# ---------------------------------------------------------------------------
# _require_full_trace_coverage_before_write
# ---------------------------------------------------------------------------


class TestRequireFullTraceCoverage:
    def test_no_trace_path_passes(self, deps):
        ctx = make_run_context(deps)
        _require_full_trace_coverage_before_write(ctx)

    def test_no_read_ranges_passes(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        _require_full_trace_coverage_before_write(ctx)

    def test_full_coverage_passes(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        deps_with_trace.read_ranges = [(0, 10)]
        _require_full_trace_coverage_before_write(ctx)

    def test_partial_coverage_raises(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        deps_with_trace.read_ranges = [(0, 5)]
        with pytest.raises(ModelRetry, match="Unread trace lines"):
            _require_full_trace_coverage_before_write(ctx)


# ---------------------------------------------------------------------------
# _require_notes_before_long_trace_write
# ---------------------------------------------------------------------------


class TestRequireNotesBeforeLongTraceWrite:
    def test_no_trace_path_passes(self, deps):
        ctx = make_run_context(deps)
        _require_notes_before_long_trace_write(ctx)

    def test_already_has_notes_passes(self, tmp_path, project_identity):
        trace_path = tmp_path / "trace.jsonl"
        _make_trace(trace_path, 200)
        deps = ContextDeps(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=project_identity,
            session_id="sess_test",
            trace_path=trace_path,
            notes=[Finding(theme="t", offset=0, quote="q", level="fact")],
        )
        ctx = make_run_context(deps)
        _require_notes_before_long_trace_write(ctx)

    def test_short_trace_passes(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        _require_notes_before_long_trace_write(ctx)

    def test_long_trace_no_notes_raises(self, tmp_path, project_identity):
        trace_path = tmp_path / "trace.jsonl"
        _make_trace(trace_path, 200)
        deps = ContextDeps(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=project_identity,
            session_id="sess_test",
            trace_path=trace_path,
        )
        ctx = make_run_context(deps)
        with pytest.raises(ModelRetry, match="trace_read chunk"):
            _require_notes_before_long_trace_write(ctx)


# ---------------------------------------------------------------------------
# search_records
# ---------------------------------------------------------------------------


class TestSearchRecords:
    def test_blank_query_raises_retry(self, deps):
        ctx = make_run_context(deps)
        with pytest.raises(ModelRetry, match="real text query"):
            search_records(ctx, query="")

    def test_star_query_raises_retry(self, deps):
        ctx = make_run_context(deps)
        with pytest.raises(ModelRetry, match="real text query"):
            search_records(ctx, query="*")

    def test_whitespace_query_raises_retry(self, deps):
        ctx = make_run_context(deps)
        with pytest.raises(ModelRetry, match="real text query"):
            search_records(ctx, query="   ")

    def test_valid_query_returns_json(self, deps, mock_embeddings):
        ctx = make_run_context(deps)
        store = ContextStore(deps.context_db_path)
        store.initialize()
        store.register_project(deps.project_identity)
        _seed_session(store, deps.project_identity.project_id)
        store.create_record(
            project_id=deps.project_identity.project_id,
            session_id="sess_test",
            kind="fact",
            title="Test fact",
            body="Some body text",
        )
        result = search_records(ctx, query="Test fact")
        parsed = json.loads(result)
        assert "count" in parsed
        assert "hits" in parsed

    def test_valid_at_includes_archived_history(self, deps, mock_embeddings):
        ctx = make_run_context(deps)
        store = ContextStore(deps.context_db_path)
        store.initialize()
        store.register_project(deps.project_identity)
        _seed_session(store, deps.project_identity.project_id, session_id="sess_hist")
        store.create_record(
            project_id=deps.project_identity.project_id,
            session_id="sess_hist",
            record_id="rec_hist_markdown_search",
            kind="fact",
            status="archived",
            title="Markdown files were the canonical context store",
            body="Markdown files were treated as the canonical durable context store.",
            valid_from="2025-01-01T00:00:00+00:00",
            valid_until="2026-03-01T00:00:00+00:00",
        )

        result = search_records(
            ctx,
            query="canonical context store",
            valid_at="2026-02-15T00:00:00+00:00",
        )
        parsed = json.loads(result)

        assert parsed["count"] >= 1
        assert any(hit["record_id"] == "rec_hist_markdown_search" for hit in parsed["hits"])


# ---------------------------------------------------------------------------
# list_records
# ---------------------------------------------------------------------------


class TestListRecords:
    def test_invalid_order_by_raises_retry(self, deps):
        ctx = make_run_context(deps)
        with pytest.raises(ModelRetry, match="order_by must be one of"):
            list_records(ctx, order_by="invalid_field")

    def test_multiple_kind_filters_raise_retry(self, deps):
        ctx = make_run_context(deps)
        with pytest.raises(ModelRetry, match="at most one kind filter"):
            list_records(ctx, kind_filters=["decision", "fact"])

    def test_multiple_status_filters_raise_retry(self, deps):
        ctx = make_run_context(deps)
        with pytest.raises(ModelRetry, match="at most one status filter"):
            list_records(ctx, status_filters=["active", "archived"])

    def test_valid_order_by(self, deps):
        ctx = make_run_context(deps)
        for order in ("created_at", "updated_at", "valid_from"):
            result = list_records(ctx, order_by=order)
            parsed = json.loads(result)
            assert "count" in parsed

    def test_default_returns_json(self, deps):
        ctx = make_run_context(deps)
        result = list_records(ctx)
        parsed = json.loads(result)
        assert "count" in parsed
        assert "records" in parsed

    def test_valid_at_includes_archived_history(self, deps):
        ctx = make_run_context(deps)
        store = ContextStore(deps.context_db_path)
        store.initialize()
        store.register_project(deps.project_identity)
        _seed_session(store, deps.project_identity.project_id, session_id="sess_hist")
        store.create_record(
            project_id=deps.project_identity.project_id,
            session_id="sess_hist",
            record_id="rec_hist_markdown_list",
            kind="fact",
            status="archived",
            title="Markdown files were the canonical context store",
            body="Markdown files were treated as the canonical durable context store.",
            valid_from="2025-01-01T00:00:00+00:00",
            valid_until="2026-03-01T00:00:00+00:00",
        )

        result = list_records(ctx, valid_at="2026-02-15T00:00:00+00:00")
        parsed = json.loads(result)

        assert parsed["count"] >= 1
        assert any(record["record_id"] == "rec_hist_markdown_list" for record in parsed["records"])


# ---------------------------------------------------------------------------
# fetch_records
# ---------------------------------------------------------------------------


class TestFetchRecords:
    def test_empty_list(self, deps):
        ctx = make_run_context(deps)
        result = fetch_records(ctx, record_ids=[])
        parsed = json.loads(result)
        assert parsed["count"] == 0

    def test_invalid_response_format(self, deps):
        ctx = make_run_context(deps)
        result = fetch_records(ctx, record_ids=["rec_1"], response_format="yaml")
        assert "Error" in result

    def test_nonexistent_record_returns_empty(self, deps):
        ctx = make_run_context(deps)
        result = fetch_records(ctx, record_ids=["rec_nonexistent"])
        parsed = json.loads(result)
        assert parsed["count"] == 0

    def test_concise_returns_compact_record(self, deps, mock_embeddings):
        ctx = make_run_context(deps)
        store = ContextStore(deps.context_db_path)
        store.initialize()
        store.register_project(deps.project_identity)
        _seed_session(store, deps.project_identity.project_id)
        rec = store.create_record(
            project_id=deps.project_identity.project_id,
            session_id="sess_test",
            kind="fact",
            title="A fact",
            body="Some body text for concise test.",
        )
        result = fetch_records(
            ctx, record_ids=[rec["record_id"]], response_format="concise"
        )
        parsed = json.loads(result)
        assert parsed["count"] == 1
        record = parsed["records"][0]
        assert "body" in record
        assert "decision" in record
        assert "why" in record
        assert len(record["body"]) <= 2000

    def test_detailed_full_body(self, deps, mock_embeddings):
        ctx = make_run_context(deps)
        store = ContextStore(deps.context_db_path)
        store.initialize()
        store.register_project(deps.project_identity)
        _seed_session(store, deps.project_identity.project_id)
        rec = store.create_record(
            project_id=deps.project_identity.project_id,
            session_id="sess_test",
            kind="fact",
            title="Full body fact",
            body="x" * 300,
        )
        result = fetch_records(
            ctx, record_ids=[rec["record_id"]], response_format="detailed"
        )
        parsed = json.loads(result)
        assert len(parsed["records"][0]["body"]) == 300


# ---------------------------------------------------------------------------
# create_record
# ---------------------------------------------------------------------------


class TestCreateRecord:
    def test_basic_create(self, deps_with_session, mock_embeddings):
        ctx = make_run_context(deps_with_session)
        result = create_record(
            ctx,
            kind="fact",
            title="Test fact",
            body="A test body.",
        )
        parsed = json.loads(result)
        assert parsed["ok"] is True

    def test_missing_title_raises_retry(self, deps_with_session, mock_embeddings):
        ctx = make_run_context(deps_with_session)
        with pytest.raises(ModelRetry, match="non-empty title"):
            create_record(ctx, kind="fact", title="", body="body")

    def test_missing_body_raises_retry(self, deps_with_session, mock_embeddings):
        ctx = make_run_context(deps_with_session)
        with pytest.raises(ModelRetry, match="non-empty body"):
            create_record(ctx, kind="fact", title="title", body="")

    def test_title_too_long_raises_retry(self, deps_with_session, mock_embeddings):
        ctx = make_run_context(deps_with_session)
        with pytest.raises(ModelRetry, match="too long"):
            create_record(ctx, kind="fact", title="x" * 200, body="body")

    def test_guard_full_trace_coverage(self, deps_with_trace, mock_embeddings):
        ctx = make_run_context(deps_with_trace)
        deps_with_trace.read_ranges = [(0, 5)]
        with pytest.raises(ModelRetry, match="Unread trace lines"):
            create_record(ctx, kind="fact", title="t", body="b")

    def test_guard_notes_before_long_trace(
        self, tmp_path, project_identity, mock_embeddings
    ):
        trace_path = tmp_path / "trace.jsonl"
        _make_trace(trace_path, 200)
        db_path = tmp_path / "context.sqlite3"
        store = ContextStore(db_path)
        store.initialize()
        store.register_project(project_identity)
        _seed_session(store, project_identity.project_id)
        deps = ContextDeps(
            context_db_path=db_path,
            project_identity=project_identity,
            session_id="sess_test",
            trace_path=trace_path,
        )
        ctx = make_run_context(deps)
        with pytest.raises(ModelRetry, match="trace_read chunk"):
            create_record(ctx, kind="fact", title="t", body="b")

    def test_decision_without_why_raises_retry(
        self, deps_with_session, mock_embeddings
    ):
        ctx = make_run_context(deps_with_session)
        with pytest.raises(ModelRetry, match="both `decision` and `why`"):
            create_record(
                ctx,
                kind="decision",
                title="A decision",
                body="We decided something.",
                decision="Use X",
                why="",
            )

    def test_duplicate_episode_raises_guided_retry(
        self, deps_with_session, mock_embeddings
    ):
        ctx = make_run_context(deps_with_session)
        create_record(
            ctx,
            kind="episode",
            title="Session title",
            body="Short session recap.",
            user_intent="Fix the extractor.",
            what_happened="Read the trace and stored the main outcomes.",
        )

        with pytest.raises(ModelRetry, match="already has an episode record"):
            create_record(
                ctx,
                kind="episode",
                title="Another session title",
                body="Another short session recap.",
                user_intent="Fix the extractor.",
                what_happened="Tried to write a second episode.",
            )

    def test_extract_requires_episode_before_durable_create(
        self, deps_with_session, mock_embeddings
    ):
        deps_with_session.require_episode_before_durable_write = True
        ctx = make_run_context(deps_with_session)

        with pytest.raises(ModelRetry, match="episode record"):
            create_record(ctx, kind="fact", title="Fact", body="Reusable fact.")

        create_record(
            ctx,
            kind="episode",
            title="Session summary",
            body="The extractor read the trace and captured useful context.",
            user_intent="Capture memory from a coding session.",
            what_happened="Read the trace and prepared durable records.",
        )
        result = create_record(ctx, kind="fact", title="Fact", body="Reusable fact.")

        assert json.loads(result)["ok"] is True

# ---------------------------------------------------------------------------
# update_record
# ---------------------------------------------------------------------------


class TestUpdateRecord:
    def test_basic_update(self, deps, mock_embeddings):
        ctx = make_run_context(deps)
        store = ContextStore(deps.context_db_path)
        store.initialize()
        store.register_project(deps.project_identity)
        _seed_session(store, deps.project_identity.project_id)
        rec = store.create_record(
            project_id=deps.project_identity.project_id,
            session_id="sess_test",
            kind="fact",
            title="Old title",
            body="Old body",
        )
        result = update_record(ctx, record_id=rec["record_id"], title="New title")
        parsed = json.loads(result)
        assert parsed["ok"] is True

    def test_guard_full_trace_coverage(self, deps_with_trace, mock_embeddings):
        ctx = make_run_context(deps_with_trace)
        deps_with_trace.read_ranges = [(0, 5)]
        with pytest.raises(ModelRetry, match="Unread trace lines"):
            update_record(ctx, record_id="rec_1", title="t")

    def test_extract_requires_episode_before_update(
        self, deps_with_session, mock_embeddings
    ):
        ctx = make_run_context(deps_with_session)
        store = ContextStore(deps_with_session.context_db_path)
        store.initialize()
        store.register_project(deps_with_session.project_identity)
        rec = store.create_record(
            project_id=deps_with_session.project_identity.project_id,
            session_id="sess_test",
            kind="fact",
            title="Old title",
            body="Old body",
        )
        deps_with_session.require_episode_before_durable_write = True

        with pytest.raises(ModelRetry, match="episode record"):
            update_record(ctx, record_id=rec["record_id"], title="New title")


# ---------------------------------------------------------------------------
# archive_record
# ---------------------------------------------------------------------------


class TestArchiveRecord:
    def test_refuses_fresh_active_non_episode(self, deps, mock_embeddings):
        ctx = make_run_context(deps)
        store = ContextStore(deps.context_db_path)
        store.initialize()
        store.register_project(deps.project_identity)
        _seed_session(store, deps.project_identity.project_id)
        rec = store.create_record(
            project_id=deps.project_identity.project_id,
            session_id="sess_test",
            kind="fact",
            title="Fresh fact",
            body="body",
        )
        with pytest.raises(ModelRetry, match="Do not archive"):
            archive_record(ctx, record_id=rec["record_id"])

    def test_allows_episode(self, deps, mock_embeddings):
        ctx = make_run_context(deps)
        store = ContextStore(deps.context_db_path)
        store.initialize()
        store.register_project(deps.project_identity)
        _seed_session(store, deps.project_identity.project_id)
        rec = store.create_record(
            project_id=deps.project_identity.project_id,
            session_id="sess_test",
            kind="episode",
            title="An episode",
            body="It happened.",
            user_intent="User wanted X",
            what_happened="We did X",
        )
        result = archive_record(ctx, record_id=rec["record_id"], reason="old")
        parsed = json.loads(result)
        assert parsed["ok"] is True


# ---------------------------------------------------------------------------
# supersede_record
# ---------------------------------------------------------------------------


class TestSupersedeRecord:
    def test_basic_supersede(self, deps, mock_embeddings):
        ctx = make_run_context(deps)
        store = ContextStore(deps.context_db_path)
        store.initialize()
        store.register_project(deps.project_identity)
        _seed_session(store, deps.project_identity.project_id)
        old = store.create_record(
            project_id=deps.project_identity.project_id,
            session_id="sess_test",
            kind="fact",
            title="Old fact",
            body="Old body",
        )
        new = store.create_record(
            project_id=deps.project_identity.project_id,
            session_id="sess_test",
            kind="fact",
            title="New fact",
            body="New body",
        )
        result = supersede_record(
            ctx,
            record_id=old["record_id"],
            replacement_record_id=new["record_id"],
            reason="replaced",
        )
        parsed = json.loads(result)
        assert parsed["ok"] is True


# ---------------------------------------------------------------------------
# context_query
# ---------------------------------------------------------------------------


class TestContextQuery:
    def test_invalid_entity_raises_retry(self, deps):
        ctx = make_run_context(deps)
        with pytest.raises(ModelRetry, match="entity must be one of"):
            context_query(ctx, entity="bogus", mode="list")

    def test_invalid_mode_raises_retry(self, deps):
        ctx = make_run_context(deps)
        with pytest.raises(ModelRetry, match="mode must be"):
            context_query(ctx, entity="records", mode="delete")

    def test_invalid_order_raises_retry(self, deps):
        ctx = make_run_context(deps)
        with pytest.raises(ModelRetry, match="order_by must be one of"):
            context_query(ctx, entity="records", mode="list", order_by="name")

    def test_entity_alias_records(self, deps):
        ctx = make_run_context(deps)
        result = context_query(ctx, entity="memories", mode="list")
        parsed = json.loads(result)
        assert "rows" in parsed or "count" in parsed

    def test_entity_alias_learnings(self, deps):
        ctx = make_run_context(deps)
        result = context_query(ctx, entity="learnings", mode="count")
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_mode_alias_counts(self, deps):
        ctx = make_run_context(deps)
        result = context_query(ctx, entity="records", mode="counts")
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_valid_records_list(self, deps):
        ctx = make_run_context(deps)
        result = context_query(ctx, entity="records", mode="list")
        parsed = json.loads(result)
        assert "rows" in parsed

    def test_default_record_count_excludes_archived_rows(self, deps):
        ctx = make_run_context(deps)
        store = ContextStore(deps.context_db_path)
        store.initialize()
        store.register_project(deps.project_identity)
        _seed_session(store, deps.project_identity.project_id, session_id="sess_active")
        _seed_session(store, deps.project_identity.project_id, session_id="sess_archived")
        store.create_record(
            project_id=deps.project_identity.project_id,
            session_id="sess_active",
            kind="decision",
            title="Active decision",
            body="Current active decision.",
            decision="Current active decision",
            why="It is still current.",
        )
        store.create_record(
            project_id=deps.project_identity.project_id,
            session_id="sess_archived",
            kind="decision",
            status="archived",
            title="Old decision",
            body="Retired decision.",
            decision="Retired decision",
            why="It was replaced.",
            valid_until="2026-03-01T00:00:00+00:00",
        )
        result = context_query(ctx, entity="records", mode="count", kind="decision")
        parsed = json.loads(result)
        assert parsed["count"] == 1

    def test_valid_at_record_query_includes_archived_history(self, deps):
        ctx = make_run_context(deps)
        store = ContextStore(deps.context_db_path)
        store.initialize()
        store.register_project(deps.project_identity)
        _seed_session(store, deps.project_identity.project_id, session_id="sess_hist")
        store.create_record(
            project_id=deps.project_identity.project_id,
            session_id="sess_hist",
            record_id="rec_hist_context_query",
            kind="fact",
            status="archived",
            title="Markdown files were the canonical context store",
            body="Markdown files were treated as the canonical durable context store.",
            valid_from="2025-01-01T00:00:00+00:00",
            valid_until="2026-03-01T00:00:00+00:00",
        )
        result = context_query(
            ctx,
            entity="records",
            mode="list",
            valid_at="2026-02-15T00:00:00+00:00",
        )
        parsed = json.loads(result)
        assert any(row["record_id"] == "rec_hist_context_query" for row in parsed["rows"])


# ---------------------------------------------------------------------------
# note
# ---------------------------------------------------------------------------


class TestNote:
    def test_appends_findings(self, deps):
        ctx = make_run_context(deps)
        findings = [
            Finding(theme="auth", offset=0, quote="use JWT", level="decision"),
            Finding(theme="db", offset=5, quote="use sqlite", level="fact"),
        ]
        result = note(ctx, findings=findings)
        assert "2 findings" in result
        assert "total 2" in result
        assert len(deps.notes) == 2

    def test_empty_findings(self, deps):
        ctx = make_run_context(deps)
        result = note(ctx, findings=[])
        assert "No findings" in result

    def test_accumulates_across_calls(self, deps):
        ctx = make_run_context(deps)
        note(ctx, findings=[Finding(theme="a", offset=0, quote="q", level="fact")])
        result = note(
            ctx, findings=[Finding(theme="b", offset=1, quote="q", level="decision")]
        )
        assert "total 2" in result
        assert len(deps.notes) == 2

    def test_runtime_only_no_db(self, deps):
        ctx = make_run_context(deps)
        note(ctx, findings=[Finding(theme="a", offset=0, quote="q", level="fact")])
        assert len(deps.notes) == 1


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------


class TestPrune:
    def test_marks_offsets(self, deps):
        ctx = make_run_context(deps)
        result = prune(ctx, trace_offsets=[0, 100])
        assert "2 new" in result
        assert 0 in deps.pruned_offsets
        assert 100 in deps.pruned_offsets

    def test_empty_offsets(self, deps):
        ctx = make_run_context(deps)
        result = prune(ctx, trace_offsets=[])
        assert "No offsets" in result

    def test_deduplication(self, deps):
        ctx = make_run_context(deps)
        prune(ctx, trace_offsets=[0, 100])
        result = prune(ctx, trace_offsets=[0, 200])
        assert "1 new" in result
        assert len(deps.pruned_offsets) == 3

    def test_runtime_only_no_db(self, deps):
        ctx = make_run_context(deps)
        prune(ctx, trace_offsets=[5])
        assert 5 in deps.pruned_offsets


# ---------------------------------------------------------------------------
# notes_state_injector
# ---------------------------------------------------------------------------


class TestNotesStateInjector:
    def test_no_findings(self, deps):
        ctx = make_run_context(deps)
        history = [ModelRequest(parts=[SystemPromptPart(content="system")])]
        result = notes_state_injector(ctx, history)
        assert len(result) == 2
        assert "0 findings" in result[-1].parts[0].content

    def test_with_findings(self, deps):
        ctx = make_run_context(deps)
        deps.notes = [
            Finding(theme="auth", offset=0, quote="q", level="decision"),
            Finding(theme="db", offset=1, quote="q", level="fact"),
            Finding(theme="api", offset=2, quote="q", level="implementation"),
        ]
        result = notes_state_injector(ctx, [])
        content = result[-1].parts[0].content
        assert "3 findings" in content
        assert "2 durable" in content
        assert "1 implementation" in content

    def test_trace_read_info(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        deps_with_trace.read_ranges = [(0, 5)]
        result = notes_state_injector(ctx, [])
        content = result[-1].parts[0].content
        assert "Trace reads" in content

    def test_does_not_mutate_original(self, deps):
        ctx = make_run_context(deps)
        original = [ModelRequest(parts=[SystemPromptPart(content="system")])]
        result = notes_state_injector(ctx, original)
        assert len(original) == 1
        assert len(result) == 2


# ---------------------------------------------------------------------------
# context_pressure_injector
# ---------------------------------------------------------------------------


class TestContextPressureInjector:
    def test_empty_history(self, deps):
        ctx = make_run_context(deps)
        result = context_pressure_injector(ctx, [])
        assert len(result) == 1
        content = result[0].parts[0].content
        assert "CONTEXT:" in content
        assert "[normal]" in content

    def test_large_history_soft_pressure(self, deps):
        ctx = make_run_context(deps)
        big_content = "x" * int(MODEL_CONTEXT_TOKEN_LIMIT * 0.65 / 0.25)
        history = [ModelRequest(parts=[SystemPromptPart(content=big_content)])]
        result = context_pressure_injector(ctx, history)
        content = result[-1].parts[0].content
        assert "[soft]" in content

    def test_large_history_hard_pressure(self, deps):
        ctx = make_run_context(deps)
        big_content = "x" * int(MODEL_CONTEXT_TOKEN_LIMIT * 0.85 / 0.25)
        history = [ModelRequest(parts=[SystemPromptPart(content=big_content)])]
        result = context_pressure_injector(ctx, history)
        content = result[-1].parts[0].content
        assert "[hard]" in content

    def test_updates_deps(self, deps):
        ctx = make_run_context(deps)
        context_pressure_injector(ctx, [])
        assert deps.last_context_tokens >= 0
        assert deps.last_context_fill_ratio >= 0.0


# ---------------------------------------------------------------------------
# prune_history_processor
# ---------------------------------------------------------------------------


class TestPruneHistoryProcessor:
    def test_no_pruned_offsets_returns_same(self, deps):
        ctx = make_run_context(deps)
        history = [ModelRequest(parts=[SystemPromptPart(content="hello")])]
        result = prune_history_processor(ctx, history)
        assert result is history

    def test_prunes_matching_trace_read(self, deps):
        ctx = make_run_context(deps)
        deps.pruned_offsets = {0}
        call = ToolCallPart(tool_name="trace_read", args={"offset": 0, "limit": 10})
        ret = ToolReturnPart(tool_name="trace_read", content="line data here")
        history: list = [ModelResponse(parts=[call]), ModelRequest(parts=[ret])]
        result = prune_history_processor(ctx, history)
        assert result[1].parts[0].content == PRUNED_STUB

    def test_does_not_prune_non_matching(self, deps):
        ctx = make_run_context(deps)
        deps.pruned_offsets = {50}
        call = ToolCallPart(tool_name="trace_read", args={"offset": 0, "limit": 10})
        ret = ToolReturnPart(tool_name="trace_read", content="line data here")
        history: list = [ModelResponse(parts=[call]), ModelRequest(parts=[ret])]
        result = prune_history_processor(ctx, history)
        assert result[1].parts[0].content == "line data here"

    def test_does_not_prune_other_tools(self, deps):
        ctx = make_run_context(deps)
        deps.pruned_offsets = {0}
        call = ToolCallPart(tool_name="search_records", args={"query": "test"})
        ret = ToolReturnPart(tool_name="search_records", content="search data")
        history: list = [ModelResponse(parts=[call]), ModelRequest(parts=[ret])]
        result = prune_history_processor(ctx, history)
        assert result[1].parts[0].content == "search data"
