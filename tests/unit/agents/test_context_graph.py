"""Tests for the context-graph package."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from lerim.agents.context_graph.clustering import build_cluster_assignments
from lerim.agents.context_graph.inventory import (
    build_semantic_candidates,
    load_graph_records,
)
from lerim.agents.context_graph.pipeline import (
    ContextGraphPipeline,
    validate_links_for_records,
)
from lerim.agents.context_graph.persistence import replace_context_graph
from lerim.agents.model_helpers import call_model_step
from lerim.context import ContextStore
from lerim.context.project_identity import ProjectIdentity


@pytest.fixture(autouse=True)
def mock_embeddings(monkeypatch):
    """Use deterministic embeddings for context-store writes."""
    provider = MagicMock()
    provider.embedding_dims = 384
    provider.model_id = "test-model"
    provider.embed_document.return_value = [0.1] * 384
    provider.embed_query.return_value = [0.1] * 384
    monkeypatch.setattr("lerim.context.store.get_embedding_provider", lambda: provider)
    monkeypatch.setattr(
        "lerim.context.embedding.get_embedding_provider", lambda: provider
    )


def _identity(tmp_path) -> ProjectIdentity:
    """Return one test project identity."""
    return ProjectIdentity(
        project_id="proj_graph",
        project_slug="graph",
        repo_path=tmp_path,
    )


def _seed_store(tmp_path) -> tuple[ContextStore, ProjectIdentity]:
    """Return a store with two durable records."""
    identity = _identity(tmp_path)
    store = ContextStore(tmp_path / "context.sqlite3")
    store.initialize()
    store.register_project(identity)
    store.upsert_session(
        project_id=identity.project_id,
        session_id="seed",
        agent_type="test",
        source_trace_ref="test",
        repo_path=str(identity.repo_path),
        cwd=str(identity.repo_path),
        started_at="2026-01-01T00:00:00+00:00",
        model_name="test",
        instructions_text=None,
        prompt_text=None,
    )
    store.upsert_session(
        project_id=identity.project_id,
        session_id="graph",
        agent_type="context_graph",
        source_trace_ref="context_graph:proj_graph",
        repo_path=str(identity.repo_path),
        cwd=str(identity.repo_path),
        started_at="2026-01-01T00:00:00+00:00",
        model_name="test",
        instructions_text=None,
        prompt_text=None,
    )
    store.create_record(
        project_id=identity.project_id,
        session_id="seed",
        record_id="rec_decision",
        kind="decision",
        title="Use approval workflow",
        body="Renewal discounts above the threshold need approval.",
        decision="Use approval workflow.",
        why="Large discounts need accountability.",
    )
    store.create_record(
        project_id=identity.project_id,
        session_id="seed",
        record_id="rec_evidence",
        kind="fact",
        title="Approval threshold exists",
        body="The renewal policy requires approval for large discounts.",
    )
    return store, identity


class TestContextGraphValidation:
    """Tests for generated-link validation before persistence."""

    def test_valid_link_passes(self):
        feedback = validate_links_for_records(
            {
                "links": [
                    {
                        "source_record_id": "rec_evidence",
                        "target_record_id": "rec_decision",
                        "relation_kind": "evidence_for",
                        "label": "Evidence for approval decision",
                        "rationale": "Policy evidence supports the decision.",
                        "evidence_record_ids": ["rec_evidence", "rec_decision"],
                        "confidence": 0.82,
                    }
                ]
            },
            records_by_id={"rec_evidence": {}, "rec_decision": {}},
            allowed_pairs={("rec_decision", "rec_evidence")},
        )

        assert feedback is None

    def test_load_graph_records_does_not_let_episodes_consume_limit(self, tmp_path):
        store, identity = _seed_store(tmp_path)
        store.create_record(
            project_id=identity.project_id,
            session_id="seed",
            record_id="rec_late_durable",
            kind="fact",
            title="Late durable fact",
            body="Durable facts should still be eligible for graph linking.",
        )
        store.create_record(
            project_id=identity.project_id,
            session_id="seed",
            record_id="rec_episode",
            kind="episode",
            title="Routine episode",
            body="Routine episode records are not graph nodes.",
            user_intent="Run a routine check.",
            what_happened="The routine check completed without durable signal.",
        )
        with store.connect() as conn:
            conn.execute(
                "UPDATE records SET updated_at = ? WHERE kind = 'episode'",
                ("2030-01-01T00:00:00+00:00",),
            )
            conn.execute(
                "UPDATE records SET updated_at = ? WHERE record_id = ?",
                ("2029-01-01T00:00:00+00:00", "rec_late_durable"),
            )

        records = load_graph_records(
            context_db_path=store.db_path,
            project_identity=identity,
            limit=1,
        )

        assert [record["record_id"] for record in records] == ["rec_late_durable"]

    def test_rejects_link_outside_candidate_pairs(self):
        feedback = validate_links_for_records(
            {
                "links": [
                    {
                        "source_record_id": "rec_a",
                        "target_record_id": "rec_b",
                        "relation_kind": "supports",
                        "label": "Support",
                        "rationale": "Supported.",
                        "evidence_record_ids": ["rec_a"],
                        "confidence": 0.8,
                    }
                ]
            },
            records_by_id={"rec_a": {}, "rec_b": {}},
            allowed_pairs={("rec_a", "rec_c")},
        )

        assert feedback
        assert "candidate_pairs_json" in feedback

    def test_rejects_low_confidence(self):
        feedback = validate_links_for_records(
            {
                "links": [
                    {
                        "source_record_id": "rec_a",
                        "target_record_id": "rec_b",
                        "relation_kind": "related",
                        "label": "Weak relation",
                        "rationale": "Maybe adjacent.",
                        "evidence_record_ids": ["rec_a"],
                        "confidence": 0.2,
                    }
                ]
            },
            records_by_id={"rec_a": {}, "rec_b": {}},
        )

        assert feedback
        assert "too low" in feedback

    def test_rejects_unreviewed_evidence(self):
        feedback = validate_links_for_records(
            {
                "links": [
                    {
                        "source_record_id": "rec_a",
                        "target_record_id": "rec_b",
                        "relation_kind": "supports",
                        "label": "Support",
                        "rationale": "Supported.",
                        "evidence_record_ids": ["rec_missing"],
                        "confidence": 0.8,
                    }
                ]
            },
            records_by_id={"rec_a": {}, "rec_b": {}},
        )

        assert feedback
        assert "not reviewed records" in feedback

    def test_retry_exhaustion_raises_instead_of_persisting_invalid_output(self):
        with pytest.raises(RuntimeError, match="invalid link_records output"):
            call_model_step(
                lambda _instruction: {"links": []},
                stage="link_records",
                progress=False,
                progress_label="context-graph",
                run_instruction="test",
                validate_result=lambda _result: "bad graph output",
                validation_retry_target="complete corrected link plan",
            )


class TestContextGraphInventory:
    """Tests for semantic graph candidate generation."""

    def test_semantic_candidates_allow_cross_kind_neighbors(
        self, tmp_path, monkeypatch
    ):
        identity = _identity(tmp_path)
        records = [
            {
                "record_id": "rec_decision",
                "kind": "decision",
                "title": "Approval workflow",
                "body": "A",
                "updated_at": "3",
            },
            {
                "record_id": "rec_fact",
                "kind": "fact",
                "title": "Approval threshold",
                "body": "B",
                "updated_at": "2",
            },
        ]

        def fake_search(self, **kwargs):
            query = str(kwargs["query"])
            if "workflow" in query:
                return [
                    SimpleNamespace(record_id="rec_decision"),
                    SimpleNamespace(record_id="rec_fact"),
                ]
            return [
                SimpleNamespace(record_id="rec_fact"),
                SimpleNamespace(record_id="rec_decision"),
            ]

        monkeypatch.setattr(ContextStore, "search", fake_search)
        clusters, pairs = build_semantic_candidates(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=identity,
            records=records,
        )

        assert len(clusters) == 1
        assert pairs[0]["source_record_id"] == "rec_decision"
        assert pairs[0]["target_record_id"] == "rec_fact"

    def test_semantic_candidates_do_not_require_mutual_neighbors(
        self, tmp_path, monkeypatch
    ):
        identity = _identity(tmp_path)
        records = [
            {
                "record_id": "rec_decision",
                "kind": "decision",
                "title": "Approval workflow",
                "body": "A",
                "updated_at": "3",
            },
            {
                "record_id": "rec_fact",
                "kind": "fact",
                "title": "Approval threshold",
                "body": "B",
                "updated_at": "2",
            },
        ]

        def fake_search(self, **kwargs):
            del self
            query = str(kwargs["query"])
            if "workflow" in query:
                return [
                    SimpleNamespace(record_id="rec_decision"),
                    SimpleNamespace(record_id="rec_fact"),
                ]
            return [SimpleNamespace(record_id="rec_fact")]

        monkeypatch.setattr(ContextStore, "search", fake_search)
        clusters, pairs = build_semantic_candidates(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=identity,
            records=records,
        )

        assert len(clusters) == 1
        assert pairs[0]["source_record_id"] == "rec_decision"
        assert pairs[0]["target_record_id"] == "rec_fact"


class TestContextGraphPipeline:
    """Tests for the full graph projection contract."""

    def test_run_context_graph_writes_nodes_and_edges(
        self, tmp_path, tmp_config, monkeypatch
    ):
        """A graph run over related durable records should leave a visible projection."""
        store, identity = _seed_store(tmp_path)

        def fake_search(self, **kwargs):
            del self
            query = str(kwargs["query"])
            if "workflow" in query:
                return [
                    SimpleNamespace(record_id="rec_decision"),
                    SimpleNamespace(record_id="rec_evidence"),
                ]
            return [
                SimpleNamespace(record_id="rec_evidence"),
                SimpleNamespace(record_id="rec_decision"),
            ]

        class FakeContextGraphStep:
            def __call__(self, *, candidate_pairs_json: str, **_kwargs):
                pairs = json.loads(candidate_pairs_json)
                pair = pairs[0]
                source = str(pair["target_record_id"])
                target = str(pair["source_record_id"])
                return {
                    "links": [
                        {
                            "source_record_id": source,
                            "target_record_id": target,
                            "relation_kind": "evidence_for",
                            "label": "Evidence for approval decision",
                            "rationale": "The policy threshold supports the approval workflow decision.",
                            "evidence_record_ids": [source, target],
                            "confidence": 0.84,
                        }
                    ]
                }

        class FakeReviewStep:
            def __call__(self, *, proposed_links_json: str, **_kwargs):
                return {"links": json.loads(proposed_links_json)}

        monkeypatch.setattr(ContextStore, "search", fake_search)
        state = ContextGraphPipeline(
            context_db_path=store.db_path,
            project_identity=identity,
            session_id="graph",
            config=tmp_config,
            link_step=FakeContextGraphStep(),
            review_step=FakeReviewStep(),
        )()
        result = state["write_summary"]

        assert result["nodes_written"] == 2
        assert result["edges_written"] == 1
        assert len(state["candidate_pairs"]) == 1
        with store.connect() as conn:
            active_nodes = conn.execute(
                "SELECT COUNT(1) FROM context_nodes WHERE status = 'active'"
            ).fetchone()[0]
            active_edges = conn.execute(
                "SELECT COUNT(1) FROM context_edges WHERE status = 'active'"
            ).fetchone()[0]
        assert active_nodes == 2
        assert active_edges == 1


class TestContextGraphPersistence:
    """Tests for context graph storage and reset."""

    def test_replace_context_graph_writes_nodes_edges_and_clusters(self, tmp_path):
        store, identity = _seed_store(tmp_path)
        records = [
            {
                "record_id": "rec_decision",
                "kind": "decision",
                "title": "Use approval workflow",
                "body": "Decision body.",
            },
            {
                "record_id": "rec_evidence",
                "kind": "fact",
                "title": "Approval threshold exists",
                "body": "Fact body.",
            },
        ]

        summary = replace_context_graph(
            context_db_path=store.db_path,
            project_identity=identity,
            session_id="graph",
            records=records,
            semantic_clusters=[
                {
                    "cluster_id": "semantic_1",
                    "record_ids": ["rec_decision", "rec_evidence"],
                }
            ],
            candidate_pairs=[
                {"source_record_id": "rec_decision", "target_record_id": "rec_evidence"}
            ],
            links=[
                {
                    "source_record_id": "rec_evidence",
                    "target_record_id": "rec_decision",
                    "relation_kind": "evidence_for",
                    "label": "Evidence for approval",
                    "rationale": "The threshold supports the approval decision.",
                    "evidence_record_ids": ["rec_evidence", "rec_decision"],
                    "confidence": 0.84,
                }
            ],
        )

        assert summary.nodes_written == 2
        assert summary.edges_written == 1
        with store.connect() as conn:
            node_count = conn.execute(
                "SELECT COUNT(1) FROM context_nodes WHERE status = 'active'"
            ).fetchone()[0]
            edge = conn.execute(
                "SELECT * FROM context_edges WHERE status = 'active'"
            ).fetchone()
        assert node_count == 2
        assert edge["relation_kind"] == "evidence_for"
        assert edge["source_node_id"] == "rec_evidence"

        counts = store.reset_project_memory(identity.project_id)
        assert counts["context_nodes"] == 2
        assert counts["context_edges"] == 1

    def test_replace_context_graph_archives_removed_candidate_edges(self, tmp_path):
        store, identity = _seed_store(tmp_path)
        records = [
            {
                "record_id": "rec_decision",
                "kind": "decision",
                "title": "Use approval workflow",
                "body": "Decision body.",
            },
            {
                "record_id": "rec_evidence",
                "kind": "fact",
                "title": "Approval threshold exists",
                "body": "Fact body.",
            },
        ]
        link = {
            "source_record_id": "rec_evidence",
            "target_record_id": "rec_decision",
            "relation_kind": "evidence_for",
            "label": "Evidence for approval",
            "rationale": "The threshold supports the approval decision.",
            "evidence_record_ids": ["rec_evidence", "rec_decision"],
            "confidence": 0.84,
        }
        replace_context_graph(
            context_db_path=store.db_path,
            project_identity=identity,
            session_id="graph",
            records=records,
            semantic_clusters=[
                {
                    "cluster_id": "semantic_1",
                    "record_ids": ["rec_decision", "rec_evidence"],
                }
            ],
            candidate_pairs=[
                {"source_record_id": "rec_decision", "target_record_id": "rec_evidence"}
            ],
            links=[link],
        )

        summary = replace_context_graph(
            context_db_path=store.db_path,
            project_identity=identity,
            session_id="graph",
            records=records,
            semantic_clusters=[
                {
                    "cluster_id": "semantic_1",
                    "record_ids": ["rec_decision", "rec_evidence"],
                }
            ],
            candidate_pairs=[
                {"source_record_id": "rec_decision", "target_record_id": "rec_evidence"}
            ],
            links=[],
        )

        assert summary.edges_written == 0
        with store.connect() as conn:
            active_edges = conn.execute(
                "SELECT COUNT(1) FROM context_edges WHERE status = 'active'"
            ).fetchone()[0]
            archived_edges = conn.execute(
                "SELECT COUNT(1) FROM context_edges WHERE status = 'archived'"
            ).fetchone()[0]
        assert active_edges == 0
        assert archived_edges == 1

    def test_replace_context_graph_archives_rows_for_archived_records(self, tmp_path):
        store, identity = _seed_store(tmp_path)
        records = [
            {
                "record_id": "rec_decision",
                "kind": "decision",
                "title": "Use approval workflow",
                "body": "Decision body.",
            },
            {
                "record_id": "rec_evidence",
                "kind": "fact",
                "title": "Approval threshold exists",
                "body": "Fact body.",
            },
        ]
        pair = [
            {"source_record_id": "rec_decision", "target_record_id": "rec_evidence"}
        ]
        replace_context_graph(
            context_db_path=store.db_path,
            project_identity=identity,
            session_id="graph",
            records=records,
            semantic_clusters=[
                {
                    "cluster_id": "semantic_1",
                    "record_ids": ["rec_decision", "rec_evidence"],
                }
            ],
            candidate_pairs=pair,
            links=[
                {
                    "source_record_id": "rec_evidence",
                    "target_record_id": "rec_decision",
                    "relation_kind": "evidence_for",
                    "label": "Evidence for approval",
                    "rationale": "The threshold supports the approval decision.",
                    "evidence_record_ids": ["rec_evidence", "rec_decision"],
                    "confidence": 0.84,
                }
            ],
        )
        store.update_record(
            record_id="rec_evidence",
            session_id="graph",
            project_ids=[identity.project_id],
            changes={
                "status": "archived",
                "valid_until": "2026-01-02T00:00:00+00:00",
            },
        )
        summary = replace_context_graph(
            context_db_path=store.db_path,
            project_identity=identity,
            session_id="graph",
            records=[
                {
                    "record_id": "rec_decision",
                    "kind": "decision",
                    "title": "Use approval workflow",
                    "body": "Decision body.",
                }
            ],
            semantic_clusters=[],
            candidate_pairs=[],
            links=[],
        )

        assert summary.semantic_clusters == 0
        with store.connect() as conn:
            active_nodes = conn.execute(
                "SELECT node_id FROM context_nodes WHERE status = 'active'"
            ).fetchall()
            active_edges = conn.execute(
                "SELECT COUNT(1) FROM context_edges WHERE status = 'active'"
            ).fetchone()[0]
        assert [row["node_id"] for row in active_nodes] == ["rec_decision"]
        assert active_edges == 0

    def test_cluster_assignments_include_semantic_layer(self):
        assignments = build_cluster_assignments(
            records=[{"record_id": "a"}, {"record_id": "b"}],
            semantic_clusters=[{"cluster_id": "semantic_1", "record_ids": ["a", "b"]}],
        )

        assert assignments["a"]["semantic_cluster"] == "semantic_1"
        assert "community_cluster" not in assignments["a"]
        assert "combined_cluster" not in assignments["a"]
