"""Tests for the context-curator package."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from lerim.agents.context_curator import ContextCuratorResult, ContextCuratorRunDetails, run_context_curator
from lerim.agents.context_curator.inventory import (
    build_health_batches,
    build_similarity_clusters,
    load_seed_and_neighbors,
    record_search_query,
)
from lerim.agents.context_curator.operations import apply_context_curation_plans
from lerim.agents.context_curator.pipeline import validate_action_plan_for_records
from lerim.agents.model_helpers import call_model_step
from lerim.context import ContextStore
from lerim.context.project_identity import ProjectIdentity
from tests.helpers import make_config


def _identity(tmp_path) -> ProjectIdentity:
    """Return one test project identity."""
    return ProjectIdentity(
        project_id="proj_curate",
        project_slug="curate",
        repo_path=tmp_path,
    )


def _seed_session(store: ContextStore, project_id: str, session_id: str) -> None:
    """Seed one context-store session."""
    store.upsert_session(
        project_id=project_id,
        session_id=session_id,
        agent_type="test",
        source_trace_ref="test",
        repo_path="/tmp/test",
        cwd="/tmp/test",
        started_at="2026-01-01T00:00:00+00:00",
        model_name="test",
        instructions_text=None,
        prompt_text=None,
    )


class TestContextCuratorResult:
    """Tests for ContextCuratorResult model."""

    def test_model_dump(self):
        result = ContextCuratorResult(completion_summary="done")
        data = result.model_dump()
        assert "completion_summary" in data
        assert data["completion_summary"] == "done"


class TestRunCurateSignature:
    """Tests for run_context_curator function signature."""

    def test_accepts_expected_kwargs(self):
        params = inspect.signature(run_context_curator).parameters
        expected = {
            "context_db_path",
            "project_identity",
            "session_id",
            "config",
            "return_details",
            "provider",
            "model_name",
            "api_base_url",
            "api_key",
            "temperature",
            "max_llm_calls",
            "progress",
            "seed_record_ids",
        }
        assert set(params.keys()) == expected

    def test_returns_details_when_requested(self, tmp_path, monkeypatch):
        identity = _identity(tmp_path)

        monkeypatch.setattr(
            "lerim.agents.context_curator.api.model_label",
            lambda **_kwargs: "test/model",
        )
        monkeypatch.setattr(
            "lerim.agents.context_curator.api.prepare_context_curator_store",
            lambda **_kwargs: None,
        )
        monkeypatch.setattr(
            "lerim.agents.context_curator.api.ContextCuratorPipeline",
            lambda **_kwargs: lambda: {
                    "completion_summary": "ok",
                    "observations": [
                        {
                            "action": "final_result",
                            "ok": True,
                            "content": "ok",
                            "args": {},
                            "done": True,
                            "completion_summary": "ok",
                        }
                    ],
                    "llm_calls": 1,
                    "done": True,
                    "records": [],
                    "clusters": [],
                },
        )

        result, details = run_context_curator(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=identity,
            session_id="curate-test",
            return_details=True,
        )

        assert result.completion_summary == "ok"
        assert isinstance(details, ContextCuratorRunDetails)
        assert details.done is True
        assert details.events[0].action == "final_result"


class TestContextCuratorGraphValidation:
    """Tests for action-plan validation before mutation."""

    def test_episode_revision_requires_complete_episode_fields(self):
        feedback = validate_action_plan_for_records(
            {
                "actions": [
                    {
                        "action_type": "revise",
                        "record_id": "rec_episode",
                        "patch": {
                            "kind": "episode",
                            "title": "Storage boundary guidance",
                            "body": "Keep product context and queue state persistence separate.",
                            "outcomes": "Storage boundaries stay separate.",
                        },
                    }
                ]
            },
            records=[
                {
                    "record_id": "rec_episode",
                    "kind": "episode",
                    "title": "Verbose episode",
                    "body": "Verbose session story.",
                }
            ],
        )

        assert feedback
        assert "must include non-empty" in feedback

    def test_retries_schema_valid_but_incomplete_plan(self):
        calls: list[str] = []

        def fake_call(instruction: str):
            calls.append(instruction)
            if len(calls) == 1:
                return {
                    "actions": [
                        {
                            "action_type": "revise",
                            "record_id": "rec_episode",
                            "patch": {
                                "kind": "episode",
                                "title": "Storage boundary guidance",
                                "body": "Keep product context and queue state persistence separate.",
                                "outcomes": "Storage boundaries stay separate.",
                            },
                        }
                    ]
                }
            return {
                "actions": [
                    {
                        "action_type": "revise",
                        "record_id": "rec_episode",
                        "patch": {
                            "kind": "episode",
                            "title": "Storage boundary guidance",
                            "body": "Keep product context and queue state persistence separate.",
                            "user_intent": "Preserve the storage boundary decision context.",
                            "what_happened": "The session confirmed separate persistence paths.",
                            "outcomes": "Product context and queue state remain separated.",
                        },
                    }
                ]
            }

        result, observations, attempts = call_model_step(
            fake_call,
            stage="review_health",
            progress=False,
            progress_label="context-curator",
            run_instruction="Keep records compact.",
            validate_result=lambda result: validate_action_plan_for_records(
                result,
                records=[
                    {
                        "record_id": "rec_episode",
                        "kind": "episode",
                        "title": "Verbose episode",
                        "body": "Verbose session story.",
                    }
                ],
            ),
            validation_retry_target="complete corrected action plan",
        )

        assert attempts == 2
        assert len(calls) == 2
        assert "Previous structured output was unsafe" in calls[1]
        assert observations[0]["action"] == "model_retry"
        assert validate_action_plan_for_records(
            result,
            records=[
                {
                    "record_id": "rec_episode",
                    "kind": "episode",
                    "title": "Verbose episode",
                    "body": "Verbose session story.",
                }
            ],
        ) is None


class TestContextCuratorInventory:
    """Tests for context curation inventory helpers."""

    def test_record_search_query_uses_typed_fields(self):
        query = record_search_query(
            {
                "title": "Retry budget",
                "body": "Persist retry state.",
                "decision": "Use job metadata.",
                "why": "Restarts need shared state.",
            }
        )
        assert "Retry budget" in query
        assert "Use job metadata" in query
        assert "Restarts need shared state" in query

    def test_similarity_clusters_are_project_wide_neighbors(self, tmp_path, monkeypatch):
        identity = _identity(tmp_path)
        records = [
            {
                "record_id": "rec_a",
                "kind": "decision",
                "title": "Persist retry state",
                "body": "A",
                "updated_at": "3",
            },
            {
                "record_id": "rec_b",
                "kind": "decision",
                "title": "Retry state survives restart",
                "body": "B",
                "updated_at": "2",
            },
            {
                "record_id": "rec_c",
                "kind": "fact",
                "title": "Unrelated",
                "body": "C",
                "updated_at": "1",
            },
        ]

        def fake_search(self, **kwargs):
            query = str(kwargs["query"])
            if "Persist retry" in query:
                return [SimpleNamespace(record_id="rec_a"), SimpleNamespace(record_id="rec_b")]
            if "survives restart" in query:
                return [SimpleNamespace(record_id="rec_b"), SimpleNamespace(record_id="rec_a")]
            return [SimpleNamespace(record_id="rec_c")]

        monkeypatch.setattr(ContextStore, "search", fake_search)
        clusters = build_similarity_clusters(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=identity,
            records=records,
        )

        assert len(clusters) == 1
        assert set(clusters[0]["record_ids"]) == {"rec_a", "rec_b"}

    def test_similarity_clusters_require_mutual_same_kind_neighbors(
        self, tmp_path, monkeypatch
    ):
        identity = _identity(tmp_path)
        records = [
            {
                "record_id": "rec_a",
                "kind": "decision",
                "title": "Persist retry state",
                "body": "A",
                "updated_at": "3",
            },
            {
                "record_id": "rec_b",
                "kind": "decision",
                "title": "Retry state survives restart",
                "body": "B",
                "updated_at": "2",
            },
            {
                "record_id": "rec_c",
                "kind": "fact",
                "title": "Retry metrics are emitted",
                "body": "C",
                "updated_at": "1",
            },
        ]

        def fake_search(self, **kwargs):
            query = str(kwargs["query"])
            if "Persist retry" in query:
                return [SimpleNamespace(record_id="rec_a"), SimpleNamespace(record_id="rec_b")]
            if "survives restart" in query:
                return [SimpleNamespace(record_id="rec_b")]
            return [SimpleNamespace(record_id="rec_c"), SimpleNamespace(record_id="rec_a")]

        monkeypatch.setattr(ContextStore, "search", fake_search)

        clusters = build_similarity_clusters(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=identity,
            records=records,
        )

        assert clusters == []

    def test_health_batches_skip_records_with_prior_actions(self):
        batches = build_health_batches(
            records=[
                {"record_id": "rec_clustered", "title": "A", "body": "A"},
                {"record_id": "rec_single", "title": "B", "body": "B"},
            ],
            excluded_record_ids={"rec_clustered"},
            batch_size=10,
        )
        assert [[record["record_id"] for record in batch] for batch in batches] == [["rec_single"]]


class TestContextCuratorOperations:
    """Tests for validated context-curation mutation application."""

    def test_applies_supersede_only_for_fetched_records(self, tmp_path):
        identity = _identity(tmp_path)
        store = ContextStore(tmp_path / "context.sqlite3")
        store.initialize()
        store.register_project(identity)
        _seed_session(store, identity.project_id, "seed")
        _seed_session(store, identity.project_id, "curate")
        weak = store.create_record(
            project_id=identity.project_id,
            session_id="seed",
            record_id="rec_weak",
            kind="decision",
            title="Persist retry state",
            body="Persist retry state in metadata.",
            decision="Persist retry state.",
            why="Workers need restart support.",
        )
        strong = store.create_record(
            project_id=identity.project_id,
            session_id="seed",
            record_id="rec_strong",
            kind="decision",
            title="Persist retry budget in job metadata",
            body="Persist retry budget in job metadata so retries survive restarts.",
            decision="Persist retry budget in job metadata.",
            why="Retries must survive restarts.",
        )

        summary = apply_context_curation_plans(
            context_db_path=store.db_path,
            project_identity=identity,
            session_id="curate",
            evidence_record_ids={str(weak["record_id"]), str(strong["record_id"])},
            action_plans=[
                {
                    "actions": [
                        {
                            "action_type": "supersede",
                            "record_id": "rec_weak",
                            "replacement_record_id": "rec_strong",
                            "reason": "stronger duplicate",
                        }
                    ]
                }
            ],
        )

        updated = store.fetch_record("rec_weak", project_ids=[identity.project_id])
        assert summary.records_updated == 1
        assert updated["superseded_by_record_id"] == "rec_strong"

    def test_rejects_unfetched_mutation(self, tmp_path):
        identity = _identity(tmp_path)
        summary = apply_context_curation_plans(
            context_db_path=tmp_path / "context.sqlite3",
            project_identity=identity,
            session_id="curate",
            evidence_record_ids=set(),
            action_plans=[
                {
                    "actions": [
                        {
                            "action_type": "archive",
                            "record_id": "rec_missing",
                            "reason": "not fetched",
                        }
                    ]
                }
            ],
        )

        assert summary.records_archived == 0
        assert summary.observations[0]["ok"] is False
        assert "unfetched_record" in summary.observations[0]["content"]

    def test_rejects_revision_kind_change(self, tmp_path):
        identity = _identity(tmp_path)
        store = ContextStore(tmp_path / "context.sqlite3")
        store.initialize()
        store.register_project(identity)
        _seed_session(store, identity.project_id, "seed")
        _seed_session(store, identity.project_id, "curate")
        record = store.create_record(
            project_id=identity.project_id,
            session_id="seed",
            record_id="rec_decision",
            kind="decision",
            title="Persist retry state",
            body="Persist retry state in metadata.",
            decision="Persist retry state.",
            why="Workers need restart support.",
        )

        summary = apply_context_curation_plans(
            context_db_path=store.db_path,
            project_identity=identity,
            session_id="curate",
            evidence_record_ids={str(record["record_id"])},
            action_plans=[
                {
                    "actions": [
                        {
                            "action_type": "revise",
                            "record_id": "rec_decision",
                            "reason": "bad patch",
                            "patch": {
                                "kind": "fact",
                                "title": "Retry state",
                                "body": "Retry state lives in metadata.",
                            },
                        }
                    ]
                }
            ],
        )

        fetched = store.fetch_record("rec_decision", project_ids=[identity.project_id])
        assert summary.records_updated == 0
        assert fetched["kind"] == "decision"
        assert summary.observations[0]["ok"] is False
        assert "kind_change_not_allowed" in summary.observations[0]["content"]


class TestContextCuratorReconcileOnWrite:
    """Tests for scoped write-time reconciliation (seed_record_ids mode)."""

    def test_load_seed_and_neighbors_collects_seed_and_search_neighbors(
        self, tmp_path, monkeypatch
    ):
        """The scoped inventory is the seed plus its active search neighbors, not all records."""
        identity = _identity(tmp_path)
        store = ContextStore(tmp_path / "context.sqlite3")
        store.initialize()
        store.register_project(identity)
        _seed_session(store, identity.project_id, "seed")
        store.create_record(
            project_id=identity.project_id,
            session_id="seed",
            record_id="rec_seed",
            kind="decision",
            title="Persist retry budget in job metadata",
            body="Persist retry budget in job metadata so retries survive restarts.",
            decision="Persist retry budget in job metadata.",
            why="Retries must survive restarts.",
        )
        store.create_record(
            project_id=identity.project_id,
            session_id="seed",
            record_id="rec_neighbor",
            kind="decision",
            title="Persist retry state",
            body="Persist retry state in metadata.",
            decision="Persist retry state.",
            why="Workers need restart support.",
        )
        store.create_record(
            project_id=identity.project_id,
            session_id="seed",
            record_id="rec_unrelated",
            kind="fact",
            title="Retry metrics are emitted",
            body="Retry metrics are emitted to the dashboard.",
        )

        def fake_search(self, **kwargs):
            return [SimpleNamespace(record_id="rec_neighbor")]

        monkeypatch.setattr(ContextStore, "search", fake_search)

        inventory = load_seed_and_neighbors(
            context_db_path=store.db_path,
            project_identity=identity,
            seed_record_ids=["rec_seed"],
        )

        ids = {record["record_id"] for record in inventory}
        assert ids == {"rec_seed", "rec_neighbor"}
        # A non-neighbor active record is not pulled in, unlike load_active_records.
        assert "rec_unrelated" not in ids

    def test_scoped_supersede_populates_temporal_fields_and_drops_from_retrieval(
        self, tmp_path
    ):
        """A write-time supersede retires the old neighbor and removes it from current retrieval."""
        identity = _identity(tmp_path)
        store = ContextStore(tmp_path / "context.sqlite3")
        store.initialize()
        store.register_project(identity)
        _seed_session(store, identity.project_id, "seed")
        _seed_session(store, identity.project_id, "reconcile")
        store.create_record(
            project_id=identity.project_id,
            session_id="seed",
            record_id="rec_old",
            kind="decision",
            title="Persist retry state",
            body="Persist retry state in metadata.",
            decision="Persist retry state.",
            why="Workers need restart support.",
        )
        store.create_record(
            project_id=identity.project_id,
            session_id="reconcile",
            record_id="rec_new",
            kind="decision",
            title="Persist retry budget in job metadata",
            body="Persist retry budget in job metadata so retries survive restarts.",
            decision="Persist retry budget in job metadata.",
            why="Retries must survive restarts.",
        )

        summary = apply_context_curation_plans(
            context_db_path=store.db_path,
            project_identity=identity,
            session_id="reconcile",
            evidence_record_ids={"rec_old", "rec_new"},
            protected_record_ids={"rec_new"},
            action_plans=[
                {
                    "actions": [
                        {
                            "action_type": "supersede",
                            "record_id": "rec_old",
                            "replacement_record_id": "rec_new",
                            "reason": "new record replaces the older duplicate",
                        }
                    ]
                }
            ],
        )

        assert summary.records_updated == 1
        old = store.fetch_record("rec_old", project_ids=[identity.project_id])
        assert old["valid_until"]
        assert old["superseded_by_record_id"] == "rec_new"

        current_ids = {
            row["record_id"]
            for row in store.query(
                entity="records",
                mode="list",
                project_ids=[identity.project_id],
            )["rows"]
        }
        assert "rec_old" not in current_ids
        assert "rec_new" in current_ids

    def test_protected_seed_is_never_retired_by_its_own_reconciliation(self, tmp_path):
        """The direction guard blocks a supersede that would retire a just-written seed."""
        identity = _identity(tmp_path)
        store = ContextStore(tmp_path / "context.sqlite3")
        store.initialize()
        store.register_project(identity)
        _seed_session(store, identity.project_id, "seed")
        _seed_session(store, identity.project_id, "reconcile")
        store.create_record(
            project_id=identity.project_id,
            session_id="reconcile",
            record_id="rec_new",
            kind="decision",
            title="Persist retry state",
            body="Persist retry state in metadata.",
            decision="Persist retry state.",
            why="Workers need restart support.",
        )
        store.create_record(
            project_id=identity.project_id,
            session_id="seed",
            record_id="rec_old",
            kind="decision",
            title="Persist retry budget in job metadata",
            body="Persist retry budget in job metadata so retries survive restarts.",
            decision="Persist retry budget in job metadata.",
            why="Retries must survive restarts.",
        )

        summary = apply_context_curation_plans(
            context_db_path=store.db_path,
            project_identity=identity,
            session_id="reconcile",
            evidence_record_ids={"rec_old", "rec_new"},
            protected_record_ids={"rec_new"},
            action_plans=[
                {
                    "actions": [
                        {
                            "action_type": "supersede",
                            "record_id": "rec_new",
                            "replacement_record_id": "rec_old",
                            "reason": "model wrongly retires the just-written record",
                        }
                    ]
                }
            ],
        )

        assert summary.records_updated == 0
        new = store.fetch_record("rec_new", project_ids=[identity.project_id])
        assert new["valid_until"] is None
        assert new["superseded_by_record_id"] is None
        assert summary.observations[0]["ok"] is False
        assert "protected_new_record" in summary.observations[0]["content"]

    def test_scoped_run_reviews_only_seed_neighborhood(self, tmp_path, monkeypatch):
        """A scoped run loads the seed neighborhood, not the whole project, and skips health review.

        A lone seed with no returned neighbors yields no clusters, so the run completes
        offline (no model call) while still exercising the scoped forward() branch:
        seed-centered inventory selection and skipped single-record health review.
        """
        identity = _identity(tmp_path)
        store = ContextStore(tmp_path / "context.sqlite3")
        store.initialize()
        store.register_project(identity)
        _seed_session(store, identity.project_id, "seed")
        store.create_record(
            project_id=identity.project_id,
            session_id="seed",
            record_id="rec_seed",
            kind="decision",
            title="Persist retry budget in job metadata",
            body="Persist retry budget in job metadata so retries survive restarts.",
            decision="Persist retry budget in job metadata.",
            why="Retries must survive restarts.",
        )
        # A second active record a whole-project pass would health-review; the scoped
        # run must ignore it because it is not a neighbor of the seed.
        store.create_record(
            project_id=identity.project_id,
            session_id="seed",
            record_id="rec_other",
            kind="fact",
            title="Retry metrics are emitted",
            body="Retry metrics are emitted to the dashboard.",
        )

        monkeypatch.setattr(ContextStore, "search", lambda self, **kwargs: [])

        result, details = run_context_curator(
            context_db_path=store.db_path,
            project_identity=identity,
            session_id="reconcile",
            config=make_config(tmp_path / ".lerim"),
            return_details=True,
            seed_record_ids=["rec_seed"],
        )

        assert details.done is True
        # Scoped inventory is the seed alone; the unrelated fact is never reviewed.
        assert details.active_record_count == 1
        assert result.completion_summary
        for record_id in ("rec_seed", "rec_other"):
            record = store.fetch_record(record_id, project_ids=[identity.project_id])
            assert record["status"] == "active"
            assert record["valid_until"] is None
