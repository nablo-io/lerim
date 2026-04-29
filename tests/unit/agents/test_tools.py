"""Comprehensive unit tests for lerim.agents.tools."""

from __future__ import annotations

import json
import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pydantic import ValidationError
from pydantic_ai import ModelRetry
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

from lerim.agents.history_processors import (
    PRUNED_STUB,
    context_pressure_injector,
    notes_state_injector,
    prune_history_processor,
)
from lerim.agents.tools import (
    CONTEXT_HARD_PRESSURE_PCT,
    CONTEXT_SOFT_PRESSURE_PCT,
    MODEL_CONTEXT_TOKEN_LIMIT,
    TRACE_MAX_LINE_BYTES,
    ContextDeps,
    TraceFinding,
    _classify_context_pressure,
    _first_uncovered_offset,
    _maybe_raise_record_retry,
    _normalize_kind,
    _normalize_status,
    _require_trace_ready_for_write,
    _store,
    archive_context,
    compute_request_budget,
    count_context,
    save_context,
    get_context,
    list_context,
    note_trace_findings,
    prune_trace_reads,
    search_context,
    supersede_context,
    read_trace,
    revise_context,
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


def _fact(title: str = "Fact", body: str = "Reusable fact.", **kwargs) -> dict:
    """Return fact tool args for direct tool tests."""
    return {"kind": "fact", "title": title, "body": body, **kwargs}


def _fetch_records(ctx, *record_ids: str) -> None:
    """Fetch records through the public tool before mutating them."""
    get_context(ctx, record_ids=list(record_ids), detail="detailed")


def _decision(
    title: str = "Decision",
    body: str = "Reusable decision.",
    decision: str = "Use the selected approach.",
    why: str = "It is the supported path.",
    **kwargs,
) -> dict:
    """Return decision tool args for direct tool tests."""
    return {
        "kind": "decision",
        "title": title,
        "body": body,
        "decision": decision,
        "why": why,
        **kwargs,
    }


def _episode(
    title: str = "Episode",
    body: str = "Short session recap.",
    user_intent: str = "Do the requested work.",
    what_happened: str = "Completed the requested work.",
    **kwargs,
) -> dict:
    """Return episode tool args for direct tool tests."""
    return {
        "kind": "episode",
        "title": title,
        "body": body,
        "user_intent": user_intent,
        "what_happened": what_happened,
        **kwargs,
    }


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
        assert compute_request_budget(p) == 50

    def test_200_lines(self, tmp_path):
        p = _make_trace(tmp_path / "t.jsonl", 200)
        assert compute_request_budget(p) == 83

    def test_500_lines(self, tmp_path):
        p = _make_trace(tmp_path / "t.jsonl", 500)
        budget = compute_request_budget(p)
        assert budget == 89

    def test_1000_lines(self, tmp_path):
        p = _make_trace(tmp_path / "t.jsonl", 1000)
        budget = compute_request_budget(p)
        assert budget == 99

    def test_5000_lines(self, tmp_path):
        p = _make_trace(tmp_path / "t.jsonl", 5000)
        assert compute_request_budget(p) == 179

    def test_large_lines_budget_for_byte_limited_chunks(self, tmp_path):
        p = tmp_path / "trace.jsonl"
        p.write_text("\n".join("x" * 4000 for _ in range(200)), encoding="utf-8")
        assert compute_request_budget(p) == 111

    def test_missing_file(self, tmp_path):
        p = tmp_path / "nonexistent.jsonl"
        assert compute_request_budget(p) == 50

    def test_100_lines(self, tmp_path):
        p = _make_trace(tmp_path / "t.jsonl", 100)
        assert compute_request_budget(p) == 50


# ---------------------------------------------------------------------------
# read_trace
# ---------------------------------------------------------------------------


class TestTraceRead:
    def test_no_trace_path(self, deps):
        ctx = make_run_context(deps)
        result = read_trace(ctx)
        assert "no trace path" in result

    def test_basic_read(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        result = read_trace(ctx, start_line=1, line_count=5)
        assert "[10 lines" in result
        assert "showing 1-5" in result
        assert "5 more lines" in result
        assert len(deps_with_trace.read_ranges) == 1
        assert deps_with_trace.read_ranges[0] == (0, 5)

    def test_read_full(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        result = read_trace(ctx, start_line=1, line_count=100)
        assert "showing 1-10" in result
        assert "more lines" not in result

    def test_offset_past_end(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        with pytest.raises(ModelRetry, match="past the end"):
            read_trace(ctx, start_line=101)

    def test_negative_offset_clamped_to_start(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        result = read_trace(ctx, start_line=-20, line_count=2)
        assert "showing 1-2" in result

    def test_limit_zero_clamped(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        result = read_trace(ctx, start_line=1, line_count=0)
        assert "[10 lines" in result

    def test_limit_over_max_clamped(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        result = read_trace(ctx, start_line=1, line_count=999)
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
        result = read_trace(ctx, start_line=1, line_count=10)
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
        result = read_trace(ctx, start_line=1, line_count=20)
        numbered_lines = [line for line in result.split("\n") if "\t" in line]
        assert len(numbered_lines) < 20

    def test_read_ranges_tracking(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        read_trace(ctx, start_line=1, line_count=5)
        read_trace(ctx, start_line=6, line_count=5)
        assert len(deps_with_trace.read_ranges) == 2
        assert deps_with_trace.read_ranges[0] == (0, 5)
        assert deps_with_trace.read_ranges[1] == (5, 10)

    def test_overlapping_read_advances_to_first_unread_line(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        read_trace(ctx, start_line=1, line_count=5)

        result = read_trace(ctx, start_line=3, line_count=3)

        assert "showing 6-8" in result
        assert "advanced from requested line 3 to first unread line 6" in result
        assert deps_with_trace.read_ranges[-1] == (5, 8)

    def test_completed_trace_read_returns_done_message(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        read_trace(ctx, start_line=1, line_count=5)
        read_trace(ctx, start_line=6, line_count=5)

        result = read_trace(ctx, start_line=1, line_count=5)

        assert "trace coverage complete" in result
        assert len(deps_with_trace.read_ranges) == 2

    def test_read_trace_auto_prunes_under_context_pressure(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        read_trace(ctx, start_line=1, line_count=3)
        read_trace(ctx, start_line=4, line_count=3)
        deps_with_trace.last_context_fill_ratio = CONTEXT_SOFT_PRESSURE_PCT

        result = read_trace(ctx, start_line=7, line_count=3)

        assert 0 in deps_with_trace.pruned_offsets
        assert "auto-pruned older read_trace start lines: 1" in result


# ---------------------------------------------------------------------------
# _require_trace_ready_for_write
# ---------------------------------------------------------------------------


class TestRequireTraceReadyForWrite:
    def test_no_trace_path_passes(self, deps):
        ctx = make_run_context(deps)
        _require_trace_ready_for_write(ctx)

    def test_no_read_ranges_raises_from_trace_start(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        with pytest.raises(ModelRetry, match=r"read_trace\(start_line=1"):
            _require_trace_ready_for_write(ctx)

    def test_empty_trace_passes_without_read_ranges(self, tmp_path, project_identity):
        trace_path = tmp_path / "trace.jsonl"
        trace_path.write_text("", encoding="utf-8")
        deps = ContextDeps(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=project_identity,
            session_id="sess_test",
            trace_path=trace_path,
            trace_total_lines=0,
        )
        ctx = make_run_context(deps)
        _require_trace_ready_for_write(ctx)

    def test_full_coverage_passes(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        deps_with_trace.read_ranges = [(0, 10)]
        _require_trace_ready_for_write(ctx)

    def test_partial_coverage_raises(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        deps_with_trace.read_ranges = [(0, 5)]
        with pytest.raises(ModelRetry, match="Unread trace lines"):
            _require_trace_ready_for_write(ctx)

    def test_already_has_notes_passes(self, tmp_path, project_identity):
        trace_path = tmp_path / "trace.jsonl"
        _make_trace(trace_path, 200)
        deps = ContextDeps(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=project_identity,
            session_id="sess_test",
            trace_path=trace_path,
            read_ranges=[(0, 200)],
            notes=[TraceFinding(theme="t", line=1, quote="q", level="fact")],
        )
        ctx = make_run_context(deps)
        _require_trace_ready_for_write(ctx)

    def test_short_trace_passes(self, deps_with_trace):
        ctx = make_run_context(deps_with_trace)
        deps_with_trace.read_ranges = [(0, 10)]
        _require_trace_ready_for_write(ctx)

    def test_long_trace_no_notes_raises(self, tmp_path, project_identity):
        trace_path = tmp_path / "trace.jsonl"
        _make_trace(trace_path, 200)
        deps = ContextDeps(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=project_identity,
            session_id="sess_test",
            trace_path=trace_path,
            read_ranges=[(0, 200)],
        )
        ctx = make_run_context(deps)
        with pytest.raises(ModelRetry, match="note_trace_findings"):
            _require_trace_ready_for_write(ctx)

    def test_long_trace_empty_findings_checkpoint_passes(
        self, tmp_path, project_identity
    ):
        trace_path = tmp_path / "trace.jsonl"
        _make_trace(trace_path, 200)
        deps = ContextDeps(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=project_identity,
            session_id="sess_test",
            trace_path=trace_path,
            read_ranges=[(0, 200)],
            findings_checked=True,
        )
        ctx = make_run_context(deps)
        _require_trace_ready_for_write(ctx)


# ---------------------------------------------------------------------------
# search_context
# ---------------------------------------------------------------------------


class TestSearchContext:
    def test_blank_query_raises_retry(self, deps):
        ctx = make_run_context(deps)
        with pytest.raises(ModelRetry, match="real text query"):
            search_context(ctx, query="")

    def test_star_query_raises_retry(self, deps):
        ctx = make_run_context(deps)
        with pytest.raises(ModelRetry, match="real text query"):
            search_context(ctx, query="*")

    def test_whitespace_query_raises_retry(self, deps):
        ctx = make_run_context(deps)
        with pytest.raises(ModelRetry, match="real text query"):
            search_context(ctx, query="   ")

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
        result = search_context(ctx, query="Test fact")
        parsed = json.loads(result)
        assert "count" in parsed
        assert "hits" in parsed

    def test_accepts_compact_filters(self, deps, mock_embeddings):
        ctx = make_run_context(deps)
        store = ContextStore(deps.context_db_path)
        store.initialize()
        store.register_project(deps.project_identity)
        _seed_session(store, deps.project_identity.project_id)
        store.create_record(
            project_id=deps.project_identity.project_id,
            session_id="sess_test",
            kind="fact",
            title="Normalized filter fact",
            body="The filter normalization path should still find this record.",
        )

        result = search_context(
            ctx,
            query="filter normalization",
            kind="fact",
            status="active",
        )
        parsed = json.loads(result)

        assert parsed["count"] >= 1

    def test_accepts_quoted_filter_scalars(self, deps, mock_embeddings):
        ctx = make_run_context(deps)
        store = ContextStore(deps.context_db_path)
        store.initialize()
        store.register_project(deps.project_identity)
        _seed_session(store, deps.project_identity.project_id)
        store.create_record(
            project_id=deps.project_identity.project_id,
            session_id="sess_test",
            kind="fact",
            title="Quoted filter fact",
            body="The quoted filter path should still find this record.",
        )

        result = search_context(
            ctx,
            query="quoted filter",
            kind='"fact"',
            status='"active"',
            include_archived='"false"',
        )
        parsed = json.loads(result)

        assert parsed["count"] >= 1

    def test_valid_at_includes_archived_history(self, deps, mock_embeddings):
        ctx = make_run_context(deps)
        store = ContextStore(deps.context_db_path)
        store.initialize()
        store.register_project(deps.project_identity)
        _seed_session(store, deps.project_identity.project_id, session_id="sess_hist")
        store.create_record(
            project_id=deps.project_identity.project_id,
            session_id="sess_hist",
            record_id="rec_hist_openrouter_search",
            kind="fact",
            status="archived",
            title="OpenRouter was the default agent provider",
            body="The default agent role used OpenRouter during this interval.",
            valid_from="2025-01-01T00:00:00+00:00",
            valid_until="2026-03-01T00:00:00+00:00",
        )

        result = search_context(
            ctx,
            query="default agent provider",
            valid_at="2026-02-15T00:00:00+00:00",
        )
        parsed = json.loads(result)

        assert parsed["count"] >= 1
        assert any(
            hit["record_id"] == "rec_hist_openrouter_search" for hit in parsed["hits"]
        )

# ---------------------------------------------------------------------------
# list_context
# ---------------------------------------------------------------------------


class TestListContext:
    def test_invalid_order_by_raises_retry(self, deps):
        ctx = make_run_context(deps)
        with pytest.raises(ModelRetry, match="order_by must be one of"):
            list_context(ctx, order_by="invalid_field")

    def test_valid_order_by(self, deps):
        ctx = make_run_context(deps)
        for order in ("created_at", "updated_at", "valid_from"):
            result = list_context(ctx, order_by=order)
            parsed = json.loads(result)
            assert "count" in parsed

    def test_accepts_json_quoted_order_by(self, deps):
        ctx = make_run_context(deps)
        result = list_context(ctx, order_by='"updated_at"')
        parsed = json.loads(result)
        assert "count" in parsed

    def test_default_returns_json(self, deps):
        ctx = make_run_context(deps)
        result = list_context(ctx)
        parsed = json.loads(result)
        assert "count" in parsed
        assert "records" in parsed

    def test_valid_at_includes_archived_history(self, deps, mock_embeddings):
        ctx = make_run_context(deps)
        store = ContextStore(deps.context_db_path)
        store.initialize()
        store.register_project(deps.project_identity)
        _seed_session(store, deps.project_identity.project_id, session_id="sess_hist")
        store.create_record(
            project_id=deps.project_identity.project_id,
            session_id="sess_hist",
            record_id="rec_hist_openrouter_list",
            kind="fact",
            status="archived",
            title="OpenRouter was the default agent provider",
            body="The default agent role used OpenRouter during this interval.",
            valid_from="2025-01-01T00:00:00+00:00",
            valid_until="2026-03-01T00:00:00+00:00",
        )

        result = list_context(
            ctx,
            valid_at="2026-02-15T00:00:00+00:00",
        )
        parsed = json.loads(result)

        assert parsed["count"] >= 1
        assert any(
            record["record_id"] == "rec_hist_openrouter_list"
            for record in parsed["records"]
        )

    def test_accepts_flat_filters(self, deps):
        ctx = make_run_context(deps)
        store = ContextStore(deps.context_db_path)
        store.initialize()
        store.register_project(deps.project_identity)
        _seed_session(store, deps.project_identity.project_id)
        store.create_record(
            project_id=deps.project_identity.project_id,
            session_id="sess_test",
            kind="fact",
            title="Flat filter fact",
            body="Flat filter arguments should find this record.",
        )

        result = list_context(ctx, kind="fact", status='"active"', order_by='"updated_at"')
        parsed = json.loads(result)

        assert parsed["count"] >= 1


# ---------------------------------------------------------------------------
# get_context
# ---------------------------------------------------------------------------


class TestGetContext:
    def test_empty_list(self, deps):
        ctx = make_run_context(deps)
        result = get_context(ctx, record_ids=[])
        parsed = json.loads(result)
        assert parsed["count"] == 0
        assert deps.fetched_context_record_ids == set()

    def test_invalid_response_format(self, deps):
        ctx = make_run_context(deps)
        with pytest.raises(ModelRetry, match="detail"):
            get_context(ctx, record_ids=["rec_1"], detail="yaml")

    def test_nonexistent_record_returns_empty(self, deps):
        ctx = make_run_context(deps)
        result = get_context(ctx, record_ids=["rec_nonexistent"])
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
        result = get_context(ctx, record_ids=[rec["record_id"]], detail="concise")
        parsed = json.loads(result)
        assert parsed["count"] == 1
        record = parsed["records"][0]
        assert "body" in record
        assert "decision" in record
        assert "why" in record
        assert len(record["body"]) <= 2000
        assert rec["record_id"] in deps.fetched_context_record_ids

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
        result = get_context(ctx, record_ids=[rec["record_id"]], detail="detailed")
        parsed = json.loads(result)
        assert len(parsed["records"][0]["body"]) == 300
        assert rec["record_id"] in deps.fetched_context_record_ids


# ---------------------------------------------------------------------------
# save_context
# ---------------------------------------------------------------------------


class TestSaveContext:
    def test_write_tools_expose_flat_record_fields(self):
        save_params = inspect.signature(save_context).parameters
        revise_params = inspect.signature(revise_context).parameters

        assert "context" not in save_params
        assert "context" not in revise_params
        for field_name in ("kind", "title", "body"):
            assert field_name in save_params
            assert field_name in revise_params

    def test_basic_create(self, deps_with_session, mock_embeddings):
        ctx = make_run_context(deps_with_session)
        result = save_context(
            ctx,
            **_fact(title="Test fact", body="A test body."),
        )
        parsed = json.loads(result)
        assert parsed["ok"] is True

    def test_create_accepts_json_quoted_scalars(
        self, deps_with_session, mock_embeddings
    ):
        ctx = make_run_context(deps_with_session)
        result = save_context(
            ctx,
            kind='"fact"',
            title="Quoted fact",
            body="A quoted body.",
            status='"active"',
        )
        record = json.loads(result)["result"]

        assert record["kind"] == "fact"
        assert record["title"] == "Quoted fact"
        assert record["body"] == "A quoted body."

    def test_invalid_kind_raises_retry(self, deps_with_session, mock_embeddings):
        ctx = make_run_context(deps_with_session)
        with pytest.raises(ModelRetry, match="Record kind is invalid"):
            save_context(ctx, kind="note", title="Bad kind", body="Body")

    def test_invalid_status_raises_retry(self, deps_with_session, mock_embeddings):
        ctx = make_run_context(deps_with_session)
        with pytest.raises(ModelRetry, match="status must be one of"):
            save_context(
                ctx,
                kind="fact",
                title="Bad status",
                body="Body",
                status="pending",
            )

    def test_create_anchors_record_time_to_source_session(
        self, deps_with_session, mock_embeddings
    ):
        ctx = make_run_context(deps_with_session)
        result = save_context(
            ctx,
            **_fact(
                title="Historical source fact",
                body="This fact was learned from a historical session.",
            ),
        )
        record = json.loads(result)["result"]

        assert record["created_at"] == "2026-01-01T00:00:00+00:00"
        assert record["updated_at"] == "2026-01-01T00:00:00+00:00"
        assert record["valid_from"] == "2026-01-01T00:00:00+00:00"

    def test_explicit_valid_from_overrides_source_session_time(
        self, deps_with_session, mock_embeddings
    ):
        ctx = make_run_context(deps_with_session)
        result = save_context(
            ctx,
            **_fact(
                title="Explicit validity fact",
                body="This fact became valid at a specific time.",
                valid_from="2026-02-01T00:00:00+00:00",
            ),
        )
        record = json.loads(result)["result"]

        assert record["created_at"] == "2026-01-01T00:00:00+00:00"
        assert record["valid_from"] == "2026-02-01T00:00:00+00:00"

    def test_missing_title_raises_retry(self, deps_with_session, mock_embeddings):
        ctx = make_run_context(deps_with_session)
        with pytest.raises(ModelRetry, match="non-empty title"):
            save_context(ctx, **_fact(title="", body="body"))

    def test_missing_body_raises_retry(self, deps_with_session, mock_embeddings):
        ctx = make_run_context(deps_with_session)
        with pytest.raises(ModelRetry, match="non-empty body"):
            save_context(ctx, **_fact(title="title", body=""))

    def test_title_too_long_raises_retry(self, deps_with_session, mock_embeddings):
        ctx = make_run_context(deps_with_session)
        with pytest.raises(ModelRetry, match="too long"):
            save_context(ctx, **_fact(title="x" * 200, body="body"))

    def test_guard_full_trace_coverage(self, deps_with_trace, mock_embeddings):
        ctx = make_run_context(deps_with_trace)
        deps_with_trace.read_ranges = [(0, 5)]
        with pytest.raises(ModelRetry, match="Unread trace lines"):
            save_context(ctx, **_fact(title="t", body="b"))

    def test_guard_refuses_create_before_any_read_trace(
        self, deps_with_trace, mock_embeddings
    ):
        ctx = make_run_context(deps_with_trace)
        with pytest.raises(ModelRetry, match=r"read_trace\(start_line=1"):
            save_context(ctx, **_fact(title="t", body="b"))

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
            trace_total_lines=200,
            read_ranges=[(0, 200)],
        )
        ctx = make_run_context(deps)
        with pytest.raises(ModelRetry, match="read_trace chunk"):
            save_context(ctx, **_fact(title="t", body="b"))

    def test_guard_allows_archived_episode_after_full_long_trace(
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
            trace_total_lines=200,
            read_ranges=[(0, 200)],
        )
        ctx = make_run_context(deps)

        result = save_context(ctx, **_episode(status="archived"))

        assert json.loads(result)["ok"] is True

    def test_decision_without_why_raises_retry(
        self, deps_with_session, mock_embeddings
    ):
        ctx = make_run_context(deps_with_session)
        with pytest.raises(ModelRetry, match="both `decision` and `why`"):
            save_context(
                ctx,
                **_decision(
                    title="A decision",
                    body="We decided something.",
                    decision="Use X",
                    why="",
                ),
            )

    def test_duplicate_episode_raises_guided_retry(
        self, deps_with_session, mock_embeddings
    ):
        ctx = make_run_context(deps_with_session)
        save_context(
            ctx,
            **_episode(
                title="Session title",
                body="Short session recap.",
                user_intent="Fix the extractor.",
                what_happened="Read the trace and stored the main outcomes.",
            ),
        )

        with pytest.raises(ModelRetry, match="already has an episode record"):
            save_context(
                ctx,
                **_episode(
                    title="Another session title",
                    body="Another short session recap.",
                    user_intent="Fix the extractor.",
                    what_happened="Tried to write a second episode.",
                ),
            )

    def test_extract_allows_durable_create_before_episode(
        self, deps_with_session, mock_embeddings
    ):
        ctx = make_run_context(deps_with_session)

        result = save_context(ctx, **_fact(title="Fact", body="Reusable fact."))

        assert json.loads(result)["ok"] is True


# ---------------------------------------------------------------------------
# revise_context
# ---------------------------------------------------------------------------


class TestReviseContext:
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
        _fetch_records(ctx, rec["record_id"])
        result = revise_context(
            ctx,
            record_id=rec["record_id"],
            reason="tighten title",
            **_fact(title="New title", body="Old body"),
        )
        parsed = json.loads(result)
        assert parsed["ok"] is True

    def test_requires_fetch_before_update(self, deps, mock_embeddings):
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

        with pytest.raises(ModelRetry, match="get_context"):
            revise_context(
                ctx,
                record_id=rec["record_id"],
                reason="tighten title",
                **_fact(title="New title", body="Old body"),
            )

    def test_guard_full_trace_coverage(self, deps_with_trace, mock_embeddings):
        ctx = make_run_context(deps_with_trace)
        store = ContextStore(deps_with_trace.context_db_path)
        store.initialize()
        store.register_project(deps_with_trace.project_identity)
        _seed_session(store, deps_with_trace.project_identity.project_id)
        rec = store.create_record(
            project_id=deps_with_trace.project_identity.project_id,
            session_id="sess_test",
            kind="fact",
            title="Old title",
            body="Old body",
        )
        _fetch_records(ctx, rec["record_id"])
        deps_with_trace.read_ranges = [(0, 5)]
        with pytest.raises(ModelRetry, match="Unread trace lines"):
            revise_context(
                ctx,
                record_id=rec["record_id"],
                reason="test",
                **_fact(title="t", body="b"),
            )

    def test_guard_refuses_update_before_any_read_trace(
        self, deps_with_trace, mock_embeddings
    ):
        ctx = make_run_context(deps_with_trace)
        store = ContextStore(deps_with_trace.context_db_path)
        store.initialize()
        store.register_project(deps_with_trace.project_identity)
        _seed_session(store, deps_with_trace.project_identity.project_id)
        rec = store.create_record(
            project_id=deps_with_trace.project_identity.project_id,
            session_id="sess_test",
            kind="fact",
            title="Old title",
            body="Old body",
        )
        _fetch_records(ctx, rec["record_id"])
        with pytest.raises(ModelRetry, match=r"read_trace\(start_line=1"):
            revise_context(
                ctx,
                record_id=rec["record_id"],
                reason="test",
                **_fact(title="t", body="b"),
            )

    def test_update_does_not_require_episode_ordering(
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
        _fetch_records(ctx, rec["record_id"])

        result = revise_context(
            ctx,
            record_id=rec["record_id"],
            reason="tighten title",
            **_fact(title="New title", body="Old body"),
        )

        assert json.loads(result)["ok"] is True

    def test_preserves_lifecycle_fields_when_omitted(self, deps, mock_embeddings):
        ctx = make_run_context(deps)
        store = ContextStore(deps.context_db_path)
        store.initialize()
        store.register_project(deps.project_identity)
        _seed_session(store, deps.project_identity.project_id)
        rec = store.create_record(
            project_id=deps.project_identity.project_id,
            session_id="sess_test",
            kind="fact",
            status="archived",
            title="Old title",
            body="Old body",
            valid_from="2025-01-01T00:00:00+00:00",
            valid_until="2025-02-01T00:00:00+00:00",
        )
        _fetch_records(ctx, rec["record_id"])

        result = revise_context(
            ctx,
            record_id=rec["record_id"],
            reason="tighten title",
            **_fact(title="New title", body="Old body"),
        )
        record = json.loads(result)["result"]

        assert record["status"] == "archived"
        assert record["valid_from"] == "2025-01-01T00:00:00+00:00"
        assert record["valid_until"] == "2025-02-01T00:00:00+00:00"

    def test_no_meaningful_change_raises_retry(self, deps, mock_embeddings):
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
        _fetch_records(ctx, rec["record_id"])

        with pytest.raises(ModelRetry, match="meaningful field change"):
            revise_context(
                ctx,
                record_id=rec["record_id"],
                reason="same payload",
                **_fact(title="Old title", body="Old body"),
            )

    def test_rejects_kind_change(self, deps, mock_embeddings):
        ctx = make_run_context(deps)
        store = ContextStore(deps.context_db_path)
        store.initialize()
        store.register_project(deps.project_identity)
        _seed_session(store, deps.project_identity.project_id)
        rec = store.create_record(
            project_id=deps.project_identity.project_id,
            session_id="sess_test",
            kind="fact",
            title="Old fact",
            body="Old body",
        )
        _fetch_records(ctx, rec["record_id"])

        with pytest.raises(ModelRetry, match="cannot change a record's kind"):
            revise_context(
                ctx,
                record_id=rec["record_id"],
                reason="wrong kind",
                **_decision(title="Decision", body="Decision body"),
            )


# ---------------------------------------------------------------------------
# archive_context
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
        _fetch_records(ctx, rec["record_id"])
        with pytest.raises(ModelRetry, match="Do not archive"):
            archive_context(ctx, record_id=rec["record_id"])

    def test_requires_fetch_before_archive(self, deps, mock_embeddings):
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

        with pytest.raises(ModelRetry, match="get_context"):
            archive_context(ctx, record_id=rec["record_id"])

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
        _fetch_records(ctx, rec["record_id"])
        result = archive_context(ctx, record_id=rec["record_id"], reason="old")
        parsed = json.loads(result)
        assert parsed["ok"] is True


# ---------------------------------------------------------------------------
# supersede_context
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
        _fetch_records(ctx, old["record_id"], new["record_id"])
        result = supersede_context(
            ctx,
            record_id=old["record_id"],
            replacement_record_id=new["record_id"],
            reason="replaced",
        )
        parsed = json.loads(result)
        assert parsed["ok"] is True

    def test_requires_fetch_before_supersede(self, deps, mock_embeddings):
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

        with pytest.raises(ModelRetry, match="get_context"):
            supersede_context(
                ctx,
                record_id=old["record_id"],
                replacement_record_id=new["record_id"],
                reason="replaced",
            )

    def test_unfetched_replacement_raises_retry(self, deps, mock_embeddings):
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
        _fetch_records(ctx, old["record_id"])

        with pytest.raises(ModelRetry, match="get_context"):
            supersede_context(
                ctx,
                record_id=old["record_id"],
                replacement_record_id="rec_missing",
                reason="replaced",
            )


# ---------------------------------------------------------------------------
# count_context
# ---------------------------------------------------------------------------


class TestCountContext:
    def test_default_count_returns_json(self, deps):
        ctx = make_run_context(deps)
        result = count_context(ctx)
        parsed = json.loads(result)
        assert "count" in parsed

    def test_default_record_count_excludes_archived_rows(self, deps, mock_embeddings):
        ctx = make_run_context(deps)
        store = ContextStore(deps.context_db_path)
        store.initialize()
        store.register_project(deps.project_identity)
        _seed_session(store, deps.project_identity.project_id, session_id="sess_active")
        _seed_session(
            store, deps.project_identity.project_id, session_id="sess_archived"
        )
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
        result = count_context(ctx, kind="decision")
        parsed = json.loads(result)
        assert parsed["count"] == 1

    def test_valid_at_count_includes_archived_history(self, deps, mock_embeddings):
        ctx = make_run_context(deps)
        store = ContextStore(deps.context_db_path)
        store.initialize()
        store.register_project(deps.project_identity)
        _seed_session(store, deps.project_identity.project_id, session_id="sess_hist")
        store.create_record(
            project_id=deps.project_identity.project_id,
            session_id="sess_hist",
            record_id="rec_hist_count_context",
            kind="fact",
            status="archived",
            title="OpenRouter was the default agent provider",
            body="The default agent role used OpenRouter during this interval.",
            valid_from="2025-01-01T00:00:00+00:00",
            valid_until="2026-03-01T00:00:00+00:00",
        )
        result = count_context(ctx, valid_at="2026-02-15T00:00:00+00:00")
        parsed = json.loads(result)
        assert parsed["count"] == 1

    def test_accepts_quoted_scalars(self, deps, mock_embeddings):
        ctx = make_run_context(deps)
        store = ContextStore(deps.context_db_path)
        store.initialize()
        store.register_project(deps.project_identity)
        _seed_session(store, deps.project_identity.project_id)
        store.create_record(
            project_id=deps.project_identity.project_id,
            session_id="sess_test",
            kind="decision",
            title="Quoted count decision",
            body="Current active decision.",
            decision="Current active decision",
            why="It is still current.",
        )

        result = count_context(ctx, kind='"decision"', status='"active"')
        parsed = json.loads(result)
        assert parsed["count"] == 1


# ---------------------------------------------------------------------------
# note_trace_findings
# ---------------------------------------------------------------------------


class TestNote:
    def test_tool_exposes_flat_finding_fields(self):
        params = inspect.signature(note_trace_findings).parameters

        assert "findings" not in params
        for field_name in ("theme", "line", "quote", "level"):
            assert field_name in params

    def test_finding_line_is_one_based(self):
        with pytest.raises(ValidationError):
            TraceFinding(theme="auth", line=0, quote="q", level="decision")

    def test_tool_rejects_zero_line(self, deps):
        ctx = make_run_context(deps)
        with pytest.raises(ModelRetry, match="valid 1-based line"):
            note_trace_findings(ctx, theme="auth", line=0, quote="q", level="decision")
        assert deps.findings_checked is False

    def test_appends_findings(self, deps):
        ctx = make_run_context(deps)
        note_trace_findings(ctx, theme="auth", line=1, quote="use JWT", level="decision")
        result = note_trace_findings(
            ctx, theme="db", line=5, quote="use sqlite", level="fact"
        )
        assert "1 finding" in result
        assert "total 2" in result
        assert len(deps.notes) == 2
        assert deps.findings_checked is True

    def test_empty_findings(self, deps):
        ctx = make_run_context(deps)
        result = note_trace_findings(ctx)
        assert "No findings" in result
        assert deps.findings_checked is True

    def test_accumulates_across_calls(self, deps):
        ctx = make_run_context(deps)
        note_trace_findings(ctx, theme="a", line=1, quote="q", level="fact")
        result = note_trace_findings(
            ctx, theme="b", line=1, quote="q", level="decision"
        )
        assert "total 2" in result
        assert len(deps.notes) == 2

    def test_runtime_only_no_db(self, deps):
        ctx = make_run_context(deps)
        note_trace_findings(ctx, theme="a", line=1, quote="q", level="fact")
        assert len(deps.notes) == 1


# ---------------------------------------------------------------------------
# prune_trace_reads
# ---------------------------------------------------------------------------


class TestPrune:
    def test_marks_offsets(self, deps):
        ctx = make_run_context(deps)
        deps.read_ranges = [(0, 10), (100, 110)]
        result = prune_trace_reads(ctx, start_lines=[1, 101])
        assert "2 new" in result
        assert 0 in deps.pruned_offsets
        assert 100 in deps.pruned_offsets

    def test_empty_offsets(self, deps):
        ctx = make_run_context(deps)
        result = prune_trace_reads(ctx, start_lines=[])
        assert "No trace reads" in result

    def test_deduplication(self, deps):
        ctx = make_run_context(deps)
        deps.read_ranges = [(0, 10), (100, 110), (200, 210)]
        prune_trace_reads(ctx, start_lines=[1, 101])
        result = prune_trace_reads(ctx, start_lines=[1, 201])
        assert "1 new" in result
        assert len(deps.pruned_offsets) == 3

    def test_rejects_unread_offset(self, deps):
        ctx = make_run_context(deps)
        deps.read_ranges = [(0, 10)]
        with pytest.raises(ModelRetry, match="Cannot prune unread trace start line"):
            prune_trace_reads(ctx, start_lines=[101])

    def test_runtime_only_no_db(self, deps):
        ctx = make_run_context(deps)
        deps.read_ranges = [(5, 15)]
        prune_trace_reads(ctx, start_lines=[6])
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
            TraceFinding(theme="auth", line=1, quote="q", level="decision"),
            TraceFinding(theme="db", line=1, quote="q", level="fact"),
            TraceFinding(theme="api", line=2, quote="q", level="implementation"),
        ]
        result = notes_state_injector(ctx, [])
        content = result[-1].parts[0].content
        assert "3 findings" in content
        assert "2 durable" in content
        assert "1 implementation" in content

    def test_read_trace_info(self, deps_with_trace):
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

    def test_prunes_matching_read_trace(self, deps):
        ctx = make_run_context(deps)
        deps.pruned_offsets = {0}
        call = ToolCallPart(
            tool_name="read_trace", args={"start_line": 1, "line_count": 10}
        )
        ret = ToolReturnPart(tool_name="read_trace", content="line data here")
        history: list = [ModelResponse(parts=[call]), ModelRequest(parts=[ret])]
        result = prune_history_processor(ctx, history)
        assert result[1].parts[0].content == PRUNED_STUB

    def test_does_not_prune_non_matching(self, deps):
        ctx = make_run_context(deps)
        deps.pruned_offsets = {50}
        call = ToolCallPart(
            tool_name="read_trace", args={"start_line": 1, "line_count": 10}
        )
        ret = ToolReturnPart(tool_name="read_trace", content="line data here")
        history: list = [ModelResponse(parts=[call]), ModelRequest(parts=[ret])]
        result = prune_history_processor(ctx, history)
        assert result[1].parts[0].content == "line data here"

    def test_does_not_prune_other_tools(self, deps):
        ctx = make_run_context(deps)
        deps.pruned_offsets = {0}
        call = ToolCallPart(tool_name="search_context", args={"query": "test"})
        ret = ToolReturnPart(tool_name="search_context", content="search data")
        history: list = [ModelResponse(parts=[call]), ModelRequest(parts=[ret])]
        result = prune_history_processor(ctx, history)
        assert result[1].parts[0].content == "search data"
