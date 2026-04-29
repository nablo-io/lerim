"""Unit tests for the hybrid search system in lerim.context.retrieval.

Covers compile_safe_fts_query, rrf_fuse, ContextStore.search, and SearchHit.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

import numpy as np
import pytest

import lerim.context.retrieval as retrieval
from lerim.context.project_identity import resolve_project_identity
from lerim.context.retrieval import (
    RRF_K,
    SearchHit,
    compile_safe_fts_query,
    rrf_fuse,
    semantic_candidates,
)
from lerim.context.store import (
    ContextStore,
)


@pytest.fixture
def mock_embeddings(monkeypatch):

    provider = MagicMock()
    provider.embedding_dims = 384
    provider.model_id = "test-model"

    rng = np.random.RandomState(42)

    def fake_embed(text):
        vec = rng.randn(384).astype(np.float32)
        vec /= np.linalg.norm(vec)
        return vec.tolist()

    provider.embed_document.side_effect = fake_embed
    provider.embed_query.side_effect = fake_embed
    monkeypatch.setattr("lerim.context.store.get_embedding_provider", lambda: provider)
    return provider


def _build_store_with_project(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "lerim.context.store.get_embedding_provider", lambda: _FakeProvider()
    )
    db_path = tmp_path / "context.sqlite3"
    store = ContextStore(db_path)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    identity = resolve_project_identity(repo_root)
    store.initialize()
    store.register_project(identity)
    store.upsert_session(
        project_id=identity.project_id,
        session_id="sess_search",
        agent_type="test",
        source_trace_ref="seed:search",
        repo_path=str(repo_root),
        cwd=str(repo_root),
        started_at="2026-04-18T00:00:00+00:00",
        model_name="test-model",
        instructions_text=None,
        prompt_text=None,
        metadata={},
    )
    return store, identity.project_id


class _FakeProvider:
    model_id = "test-embed-v1"
    embedding_dims = 4

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    def embed_document(self, text: str) -> list[float]:
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        lowered = str(text or "").lower()
        if any(
            w in lowered for w in ("generic", "write api", "mutator", "explicit write")
        ):
            return [1.0, 0.0, 0.0, 0.0]
        if any(w in lowered for w in ("cache", "redis", "ttl")):
            return [0.0, 1.0, 0.0, 0.0]
        if any(w in lowered for w in ("test", "pytest", "unit")):
            return [0.0, 0.0, 1.0, 0.0]
        return [0.0, 0.0, 0.0, 1.0]


class TestCompileSafeFtsQuery:
    def test_none_returns_none(self):
        assert compile_safe_fts_query(None) is None

    def test_empty_string_returns_none(self):
        assert compile_safe_fts_query("") is None

    def test_whitespace_only_returns_none(self):
        assert compile_safe_fts_query("   \t  \n  ") is None

    def test_single_word_is_quoted(self):
        result = compile_safe_fts_query("hello")
        assert result == '"hello"'

    def test_two_words_are_or_joined(self):
        result = compile_safe_fts_query("hello world")
        assert result == '"hello" OR "world"'

    def test_special_chars_become_spaces(self):
        result = compile_safe_fts_query("hello! world? test@foo#bar")
        assert result == '"hello" OR "world" OR "test" OR "foo" OR "bar"'

    def test_punctuation_only_returns_none(self):
        assert compile_safe_fts_query("!@#$%^&*()") is None

    def test_max_eight_terms(self):
        words = " ".join(f"word{i}" for i in range(12))
        result = compile_safe_fts_query(words)
        terms = result.split(" OR ")
        assert len(terms) == 8

    def test_case_insensitive_dedup(self):
        result = compile_safe_fts_query("Hello HELLO hello World world")
        assert result == '"Hello" OR "World"'

    def test_mixed_case_dedup_keeps_first(self):
        result = compile_safe_fts_query("Python python PYTHON")
        assert result == '"Python"'

    def test_digits_preserved(self):
        result = compile_safe_fts_query("python3 version 3.11")
        assert result == '"python3" OR "version" OR "3" OR "11"'

    def test_dashes_become_spaces(self):
        result = compile_safe_fts_query("well-known pattern")
        assert result == '"well" OR "known" OR "pattern"'

    def test_multiple_spaces_collapsed(self):
        result = compile_safe_fts_query("a   b    c")
        assert result == '"a" OR "b" OR "c"'

    def test_unicode_preserved(self):
        result = compile_safe_fts_query("donnée käytäntö")
        assert result == '"donnée" OR "käytäntö"'

    def test_exactly_eight_terms_not_truncated(self):
        words = " ".join(f"w{i}" for i in range(8))
        result = compile_safe_fts_query(words)
        terms = result.split(" OR ")
        assert len(terms) == 8


class TestRRFFusion:
    def test_empty_both_returns_empty(self):
        result = rrf_fuse(semantic_rows=[], lexical_rows=[])
        assert result == []

    def test_semantic_only(self):
        result = rrf_fuse(
            semantic_rows=[("r1", 0.1), ("r2", 0.5)],
            lexical_rows=[],
        )
        assert len(result) == 2
        assert result[0][0] == "r1"
        assert result[0][2] == ["semantic"]
        assert result[1][2] == ["semantic"]

    def test_lexical_only(self):
        result = rrf_fuse(
            semantic_rows=[],
            lexical_rows=[("r1", -5.0), ("r2", -10.0)],
        )
        assert len(result) == 2
        assert result[0][0] == "r1"
        assert result[0][2] == ["fts"]

    def test_both_sources_dual_hit(self):
        result = rrf_fuse(
            semantic_rows=[("r1", 0.1)],
            lexical_rows=[("r1", -5.0)],
        )
        assert len(result) == 1
        record_id, score, sources = result[0]
        assert record_id == "r1"
        assert "semantic" in sources
        assert "fts" in sources

    def test_dual_source_higher_score_than_single(self):
        semantic_only = rrf_fuse(
            semantic_rows=[("r_single", 0.1)],
            lexical_rows=[],
        )
        both = rrf_fuse(
            semantic_rows=[("r_dual", 0.1)],
            lexical_rows=[("r_dual", -5.0)],
        )
        assert both[0][1] > semantic_only[0][1]

    def test_ordering_by_score_descending(self):
        result = rrf_fuse(
            semantic_rows=[("r1", 0.1), ("r2", 0.2)],
            lexical_rows=[("r1", -5.0)],
        )
        scores = [item[1] for item in result]
        assert scores == sorted(scores, reverse=True)

    def test_rrf_formula_rank1(self):
        result = rrf_fuse(
            semantic_rows=[("r1", 0.0)],
            lexical_rows=[],
        )
        expected = 1.0 / (RRF_K + 1)
        assert abs(result[0][1] - expected) < 1e-10

    def test_rrf_formula_rank60(self):
        rows = [(f"r{i}", float(i)) for i in range(60)]
        result = rrf_fuse(semantic_rows=rows, lexical_rows=[])
        last = result[-1]
        expected = 1.0 / (RRF_K + 60)
        assert abs(last[1] - expected) < 1e-10

    def test_sources_sorted_alphabetically(self):
        result = rrf_fuse(
            semantic_rows=[("r1", 0.1)],
            lexical_rows=[("r1", -1.0)],
        )
        assert result[0][2] == ["fts", "semantic"]

    def test_disjoint_lists_combined(self):
        result = rrf_fuse(
            semantic_rows=[("s1", 0.1), ("s2", 0.2)],
            lexical_rows=[("l1", -1.0), ("l2", -2.0)],
        )
        ids = {item[0] for item in result}
        assert ids == {"s1", "s2", "l1", "l2"}

    def test_lexical_rank_contributes_correctly(self):
        result = rrf_fuse(
            semantic_rows=[("r1", 0.0)],
            lexical_rows=[("r1", 0.0)],
        )
        expected = 1.0 / (RRF_K + 1) + 1.0 / (RRF_K + 1)
        assert abs(result[0][1] - expected) < 1e-10


class TestSearchIntegration:
    def test_empty_database_returns_empty(self, tmp_path, monkeypatch):
        store, pid = _build_store_with_project(tmp_path, monkeypatch)
        hits = store.search(project_ids=[pid], query="nothing matches this")
        assert hits == []

    def test_search_returns_search_hit_instances(self, tmp_path, monkeypatch):
        store, pid = _build_store_with_project(tmp_path, monkeypatch)
        store.create_record(
            project_id=pid,
            session_id="sess_search",
            kind="fact",
            title="Python testing guide",
            body="Use pytest for unit tests.",
        )
        hits = store.search(project_ids=[pid], query="pytest testing")
        assert len(hits) >= 1
        assert isinstance(hits[0], SearchHit)

    def test_kind_filter_excludes_non_matching(self, tmp_path, monkeypatch):
        store, pid = _build_store_with_project(tmp_path, monkeypatch)
        store.create_record(
            project_id=pid,
            session_id="sess_search",
            kind="decision",
            title="Use Redis for caching",
            body="Redis provides fast key-value storage with TTL support.",
            decision="Use Redis",
            why="Performance requirements.",
        )
        store.create_record(
            project_id=pid,
            session_id="sess_search",
            kind="fact",
            title="Cache invalidation pattern",
            body="Cache invalidation is one of the hardest problems.",
        )
        hits = store.search(project_ids=[pid], query="cache", kind_filters=["decision"])
        assert all(h.kind == "decision" for h in hits)

    def test_semantic_candidates_oversample_before_kind_filtering(self, tmp_path, monkeypatch):
        class _ScopedProvider:
            model_id = "test-embed-v2"
            embedding_dims = 4

            def embed_query(self, text: str) -> list[float]:
                return [1.0, 0.0, 0.0, 0.0] if "needle" in text.lower() else [0.0, 0.0, 0.0, 1.0]

            def embed_document(self, text: str) -> list[float]:
                lowered = text.lower()
                if "closer distractor" in lowered:
                    return [1.0, 0.0, 0.0, 0.0]
                if "target decision" in lowered:
                    return [0.8, 0.2, 0.0, 0.0]
                return [0.0, 0.0, 0.0, 1.0]

        monkeypatch.setattr("lerim.context.store.get_embedding_provider", lambda: _ScopedProvider())
        store, pid = _build_store_with_project(tmp_path, monkeypatch)
        for index in range(30):
            store.create_record(
                project_id=pid,
                session_id="sess_search",
                kind="fact",
                title=f"Closer distractor {index}",
                body="This is the closer distractor for semantic search.",
            )
        target = store.create_record(
            project_id=pid,
            session_id="sess_search",
            kind="decision",
            title="Target decision",
            body="This target decision should survive semantic shortlist filtering.",
            decision="Target decision",
            why="It is the in-scope decision result.",
        )

        hits = semantic_candidates(
            store,
            project_ids=[pid],
            query="needle query",
            kind_filters=["decision"],
            statuses=None,
            valid_at=None,
            include_archived=False,
            limit=1,
        )

        assert len(hits) == 1
        assert hits[0][0] == target["record_id"]

    def test_status_filter_active_only(self, tmp_path, monkeypatch):
        store, pid = _build_store_with_project(tmp_path, monkeypatch)
        store.create_record(
            project_id=pid,
            session_id="sess_search",
            kind="fact",
            title="Active fact one",
            body="This fact is active.",
        )
        rec2 = store.create_record(
            project_id=pid,
            session_id="sess_search",
            kind="fact",
            title="Active fact two",
            body="Another active fact for testing.",
        )
        from datetime import datetime, timedelta, timezone

        old_created = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        with store.connect() as conn:
            conn.execute(
                "UPDATE records SET created_at = ?, updated_at = ? WHERE record_id = ?",
                (old_created, old_created, rec2["record_id"]),
            )
        store.update_record(
            record_id=rec2["record_id"],
            session_id="sess_search",
            project_ids=[pid],
            changes={"status": "archived"},
            change_reason="test archive",
        )
        hits = store.search(project_ids=[pid], query="active fact", statuses=["active"])
        assert all(h.status == "active" for h in hits)

    def test_project_isolation(self, tmp_path, monkeypatch):
        store, pid_a = _build_store_with_project(tmp_path, monkeypatch)

        repo_b = tmp_path / "repo_b"
        repo_b.mkdir()
        identity_b = resolve_project_identity(repo_b)
        store.register_project(identity_b)
        store.upsert_session(
            project_id=identity_b.project_id,
            session_id="sess_b",
            agent_type="test",
            source_trace_ref="seed:b",
            repo_path=str(repo_b),
            cwd=str(repo_b),
            started_at="2026-04-18T00:00:00+00:00",
            model_name="test-model",
            instructions_text=None,
            prompt_text=None,
            metadata={},
        )

        store.create_record(
            project_id=pid_a,
            session_id="sess_search",
            kind="fact",
            title="Project A exclusive data",
            body="This belongs to project A only.",
        )
        store.create_record(
            project_id=identity_b.project_id,
            session_id="sess_b",
            kind="fact",
            title="Project B exclusive data",
            body="This belongs to project B only.",
        )

        hits_a = store.search(project_ids=[pid_a], query="exclusive data")
        assert all(h.project_id == pid_a for h in hits_a)

        hits_b = store.search(
            project_ids=[identity_b.project_id], query="exclusive data"
        )
        assert all(h.project_id == identity_b.project_id for h in hits_b)

    def test_include_archived_false_excludes_archived(self, tmp_path, monkeypatch):
        store, pid = _build_store_with_project(tmp_path, monkeypatch)
        rec = store.create_record(
            project_id=pid,
            session_id="sess_search",
            kind="fact",
            title="Will be archived soon",
            body="This record will be archived.",
        )
        from datetime import datetime, timedelta, timezone

        old_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        with store.connect() as conn:
            conn.execute(
                "UPDATE records SET created_at = ?, updated_at = ? WHERE record_id = ?",
                (old_ts, old_ts, rec["record_id"]),
            )
        store.update_record(
            record_id=rec["record_id"],
            session_id="sess_search",
            project_ids=[pid],
            changes={"status": "archived"},
            change_reason="test",
        )
        hits = store.search(project_ids=[pid], query="archived", include_archived=False)
        assert rec["record_id"] not in [h.record_id for h in hits]

    def test_include_archived_true_includes_archived(self, tmp_path, monkeypatch):
        store, pid = _build_store_with_project(tmp_path, monkeypatch)
        rec = store.create_record(
            project_id=pid,
            session_id="sess_search",
            kind="fact",
            title="Will be archived include test",
            body="This record will be archived then searched.",
        )
        from datetime import datetime, timedelta, timezone

        old_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        with store.connect() as conn:
            conn.execute(
                "UPDATE records SET created_at = ?, updated_at = ? WHERE record_id = ?",
                (old_ts, old_ts, rec["record_id"]),
            )
        store.update_record(
            record_id=rec["record_id"],
            session_id="sess_search",
            project_ids=[pid],
            changes={"status": "archived"},
            change_reason="test",
        )
        hits = store.search(
            project_ids=[pid], query="archived include test", include_archived=True
        )
        assert any(h.record_id == rec["record_id"] for h in hits)

    def test_superseded_record_excluded_from_current_search(self, tmp_path, monkeypatch):
        store, pid = _build_store_with_project(tmp_path, monkeypatch)
        old_record = store.create_record(
            project_id=pid,
            session_id="sess_search",
            kind="decision",
            title="Storage truth",
            body="Use the sessions database for runtime state.",
            decision="Use sessions database for runtime state",
            why="Operational state belongs in the sessions DB.",
        )
        replacement = store.create_record(
            project_id=pid,
            session_id="sess_search",
            kind="decision",
            title="Storage truth",
            body="Use the sessions database for runtime state and keep product context separate.",
            decision="Use sessions database for runtime state",
            why="Operational state belongs in the sessions DB.",
        )
        store.supersede_record(
            record_id=old_record["record_id"],
            session_id=None,
            project_ids=[pid],
            replacement_record_id=replacement["record_id"],
        )
        hits = store.search(project_ids=[pid], query="runtime state sessions database")
        hit_ids = [hit.record_id for hit in hits]
        assert old_record["record_id"] not in hit_ids
        assert replacement["record_id"] in hit_ids

    def test_limit_respected(self, tmp_path, monkeypatch):
        store, pid = _build_store_with_project(tmp_path, monkeypatch)
        for i in range(10):
            store.create_record(
                project_id=pid,
                session_id="sess_search",
                kind="fact",
                title=f"Test fact number {i}",
                body=f"Body content for fact number {i} about testing.",
            )
        hits = store.search(project_ids=[pid], query="test fact", limit=3)
        assert len(hits) <= 3

    def test_search_hit_fields_populated(self, tmp_path, monkeypatch):
        store, pid = _build_store_with_project(tmp_path, monkeypatch)
        store.create_record(
            project_id=pid,
            session_id="sess_search",
            kind="fact",
            title="Fact with all fields",
            body="Testing that all SearchHit fields are populated.",
        )
        hits = store.search(project_ids=[pid], query="all fields populated")
        assert len(hits) >= 1
        hit = hits[0]
        assert hit.record_id
        assert hit.project_id == pid
        assert hit.kind == "fact"
        assert hit.title
        assert hit.body
        assert hit.status == "active"
        assert hit.created_at
        assert hit.updated_at
        assert hit.valid_from
        assert hit.score > 0
        assert isinstance(hit.sources, list)

    def test_search_with_mock_embeddings(self, tmp_path, mock_embeddings, monkeypatch):
        store, pid = _build_store_with_project(tmp_path, monkeypatch)
        store.create_record(
            project_id=pid,
            session_id="sess_search",
            kind="fact",
            title="Mock embedding test",
            body="Using mock embeddings for deterministic search.",
        )
        hits = store.search(project_ids=[pid], query="mock embedding")
        assert isinstance(hits, list)

    def test_multiple_kind_filters(self, tmp_path, monkeypatch):
        store, pid = _build_store_with_project(tmp_path, monkeypatch)
        store.create_record(
            project_id=pid,
            session_id="sess_search",
            kind="decision",
            title="Use explicit write tools",
            body="Replace generic write api with explicit write tools.",
            decision="Use explicit tools",
            why="Clarity.",
        )
        store.create_record(
            project_id=pid,
            session_id="sess_search",
            kind="fact",
            title="Explicit write tools are defined",
            body="Each tool handles one mutation type.",
        )
        store.create_record(
            project_id=pid,
            session_id="sess_search",
            kind="constraint",
            title="No generic mutators allowed",
            body="Generic mutators must not be used in the codebase.",
        )
        hits = store.search(
            project_ids=[pid],
            query="explicit write tools",
            kind_filters=["decision", "constraint"],
        )
        assert all(h.kind in ("decision", "constraint") for h in hits)

    def test_search_hit_has_sources_from_retrieval(self, tmp_path, monkeypatch):
        store, pid = _build_store_with_project(tmp_path, monkeypatch)
        store.create_record(
            project_id=pid,
            session_id="sess_search",
            kind="fact",
            title="Cache with Redis TTL",
            body="Use Redis for caching with TTL expiration.",
        )
        hits = store.search(project_ids=[pid], query="Redis cache TTL")
        assert len(hits) >= 1
        all_sources = set()
        for hit in hits:
            all_sources.update(hit.sources)
        assert all_sources & {"semantic", "fts"}

    def test_search_reuses_one_connection_for_retrieval(self, tmp_path, monkeypatch):
        store, pid = _build_store_with_project(tmp_path, monkeypatch)
        store.create_record(
            project_id=pid,
            session_id="sess_search",
            kind="fact",
            title="Cache with Redis TTL",
            body="Use Redis for caching with TTL expiration.",
        )
        store.initialize()
        monkeypatch.setattr(store, "initialize", lambda: None)
        opened_connections = []
        helper_connections = []
        original_connect = store.connect
        original_semantic = retrieval.semantic_candidates
        original_lexical = retrieval.lexical_candidates

        @contextmanager
        def tracked_connect():
            with original_connect() as conn:
                opened_connections.append(conn)
                yield conn

        def tracked_semantic(*args, **kwargs):
            helper_connections.append(kwargs.get("conn"))
            return original_semantic(*args, **kwargs)

        def tracked_lexical(*args, **kwargs):
            helper_connections.append(kwargs.get("conn"))
            return original_lexical(*args, **kwargs)

        monkeypatch.setattr(store, "connect", tracked_connect)
        monkeypatch.setattr(retrieval, "semantic_candidates", tracked_semantic)
        monkeypatch.setattr(retrieval, "lexical_candidates", tracked_lexical)

        hits = store.search(project_ids=[pid], query="Redis cache TTL")

        assert hits
        assert len(opened_connections) == 1
        assert helper_connections == [opened_connections[0], opened_connections[0]]

    def test_search_falls_back_to_semantic_when_fts_is_unavailable(self, tmp_path, monkeypatch):
        store, pid = _build_store_with_project(tmp_path, monkeypatch)
        store.create_record(
            project_id=pid,
            session_id="sess_search",
            kind="fact",
            title="Cache with Redis TTL",
            body="Use Redis for caching with TTL expiration.",
        )

        def fail_fts(_conn):
            raise sqlite3.OperationalError("vtable constructor failed")

        monkeypatch.setattr(store, "_prepare_search_fts", fail_fts)

        hits = store.search(project_ids=[pid], query="Redis cache TTL")

        assert hits
        assert "semantic" in hits[0].sources
        assert "fts" not in hits[0].sources


class TestSearchHit:
    def test_construction(self):
        hit = SearchHit(
            record_id="rec_abc",
            project_id="proj_1",
            kind="fact",
            title="Test title",
            body="Test body",
            status="active",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            valid_from="2026-01-01T00:00:00+00:00",
            valid_until=None,
            score=0.95,
            sources=["semantic", "fts"],
        )
        assert hit.record_id == "rec_abc"
        assert hit.kind == "fact"
        assert hit.score == 0.95
        assert hit.sources == ["semantic", "fts"]

    def test_frozen_dataclass(self):
        hit = SearchHit(
            record_id="rec_abc",
            project_id="proj_1",
            kind="fact",
            title="Test",
            body="Body",
            status="active",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            valid_from="2026-01-01T00:00:00+00:00",
            valid_until=None,
            score=1.0,
            sources=[],
        )
        with pytest.raises(FrozenInstanceError):
            hit.record_id = "changed"

    def test_frozen_score(self):
        hit = SearchHit(
            record_id="rec_1",
            project_id="proj_1",
            kind="fact",
            title="t",
            body="b",
            status="active",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            valid_from="2026-01-01T00:00:00+00:00",
            valid_until=None,
            score=0.5,
            sources=[],
        )
        with pytest.raises(FrozenInstanceError):
            hit.score = 0.99

    def test_valid_until_can_be_none(self):
        hit = SearchHit(
            record_id="rec_1",
            project_id="proj_1",
            kind="fact",
            title="t",
            body="b",
            status="active",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            valid_from="2026-01-01T00:00:00+00:00",
            valid_until=None,
            score=0.5,
            sources=[],
        )
        assert hit.valid_until is None

    def test_valid_until_can_be_string(self):
        hit = SearchHit(
            record_id="rec_1",
            project_id="proj_1",
            kind="fact",
            title="t",
            body="b",
            status="archived",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-02T00:00:00+00:00",
            valid_from="2026-01-01T00:00:00+00:00",
            valid_until="2026-01-02T00:00:00+00:00",
            score=0.3,
            sources=["fts"],
        )
        assert hit.valid_until == "2026-01-02T00:00:00+00:00"

    def test_equality(self):
        kwargs = dict(
            record_id="rec_1",
            project_id="proj_1",
            kind="fact",
            title="t",
            body="b",
            status="active",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            valid_from="2026-01-01T00:00:00+00:00",
            valid_until=None,
            score=0.5,
            sources=[],
        )
        assert SearchHit(**kwargs) == SearchHit(**kwargs)

    def test_inequality_different_score(self):
        base = dict(
            record_id="rec_1",
            project_id="proj_1",
            kind="fact",
            title="t",
            body="b",
            status="active",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            valid_from="2026-01-01T00:00:00+00:00",
            valid_until=None,
            sources=[],
        )
        assert SearchHit(score=0.5, **base) != SearchHit(score=0.9, **base)

    def test_not_hashable_due_to_list_field(self):
        hit = SearchHit(
            record_id="rec_1",
            project_id="proj_1",
            kind="fact",
            title="t",
            body="b",
            status="active",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            valid_from="2026-01-01T00:00:00+00:00",
            valid_until=None,
            score=0.5,
            sources=["semantic"],
        )
        with pytest.raises(TypeError):
            hash(hit)
