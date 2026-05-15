"""Comprehensive unit tests for lerim.agents.tools."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pydantic_ai import ModelRetry
from lerim.agents.tools import (
    _normalize_kind,
    _normalize_status,
    _store,
    count_context,
    get_context,
    list_context,
    search_context,
)
from lerim.context import ContextStore
from tests.unit.agents.conftest import make_run_context


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
# _store
# ---------------------------------------------------------------------------


class TestStore:
    def test_creates_initialized_store(self, deps):
        ctx = make_run_context(deps)
        store = _store(ctx)
        assert isinstance(store, ContextStore)


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
