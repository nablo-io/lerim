"""Tests for model extract persistence helpers."""

from __future__ import annotations

from pathlib import Path

from lerim.agents.trace_ingestion.persistence import (
    PersistenceContext,
    persist_synthesized_extraction,
    prepare_context_store,
)
from lerim.context import ContextStore, resolve_project_identity, scope_from_project


def _context(tmp_path: Path) -> PersistenceContext:
    """Return one isolated persistence context."""
    project_identity = resolve_project_identity(tmp_path)
    return PersistenceContext(
        context_db_path=tmp_path / "context.sqlite3",
        project_identity=project_identity,
        scope_identity=scope_from_project(project_identity),
        session_id="session-1",
        trace_path=tmp_path / "trace.jsonl",
        session_started_at="2026-01-01T00:00:00+00:00",
        model_name="test-model",
    )


def _synthesized_payload() -> dict:
    """Return one synthesized extraction payload."""
    return {
        "completion_summary": "Extraction completed.",
        "episode": {
            "title": "Implement extract replay idempotency",
            "body": "The session extracted context from a trace.",
            "status": "archived",
            "user_intent": "Extract context from the source session.",
            "what_happened": "The trace was scanned and persisted.",
            "outcomes": "One episode and one durable fact were produced.",
        },
        "durable_records": [
            {
                "kind": "fact",
                "title": "Session catalog rebuild can replay traces",
                "body": "Rebuilding the session catalog may replay a trace whose episode is already in the context DB.",
                "status": "active",
            }
        ],
    }


def test_duplicate_episode_replay_is_idempotent(tmp_path) -> None:
    """Replaying an already-extracted session skips cleanly without duplicates."""
    ctx = _context(tmp_path)
    prepare_context_store(ctx)

    first_observations, first_done, _summary = persist_synthesized_extraction(
        _synthesized_payload(),
        ctx,
    )
    second_observations, second_done, _summary = persist_synthesized_extraction(
        _synthesized_payload(),
        ctx,
    )

    store = ContextStore(ctx.context_db_path)
    rows = store.query(
        entity="records",
        mode="list",
        project_ids=[ctx.project_identity.project_id],
        source_session_id=ctx.session_id,
        limit=10,
        include_archived=True,
    )["rows"]

    assert first_done is True
    assert first_observations[0]["ok"] is True
    assert second_done is True
    assert second_observations[0]["ok"] is True
    assert "duplicate_episode_for_session" in second_observations[0]["content"]
    assert len(rows) == 2
    assert sorted(row["kind"] for row in rows) == ["episode", "fact"]


def test_episode_defaults_to_archived_when_durable_record_exists(tmp_path) -> None:
    """Trace recap episodes should not stay active by default beside durable context."""
    ctx = _context(tmp_path)
    prepare_context_store(ctx)
    payload = _synthesized_payload()
    payload["episode"].pop("status", None)

    observations, done, _summary = persist_synthesized_extraction(payload, ctx)

    store = ContextStore(ctx.context_db_path)
    rows = store.query(
        entity="records",
        mode="list",
        project_ids=[ctx.project_identity.project_id],
        source_session_id=ctx.session_id,
        limit=10,
        include_archived=True,
    )["rows"]
    episode = next(row for row in rows if row["kind"] == "episode")

    assert done is True
    assert observations[0]["ok"] is True
    assert episode["status"] == "archived"


def test_episode_is_archived_when_model_marks_active_beside_durable_record(tmp_path) -> None:
    """Durable records should carry reusable signal, not the episode wrapper."""
    ctx = _context(tmp_path)
    prepare_context_store(ctx)
    payload = _synthesized_payload()
    payload["episode"]["status"] = "active"

    observations, done, _summary = persist_synthesized_extraction(payload, ctx)

    store = ContextStore(ctx.context_db_path)
    rows = store.query(
        entity="records",
        mode="list",
        project_ids=[ctx.project_identity.project_id],
        source_session_id=ctx.session_id,
        limit=10,
        include_archived=True,
    )["rows"]
    episode = next(row for row in rows if row["kind"] == "episode")

    assert done is True
    assert observations[0]["ok"] is True
    assert episode["status"] == "archived"


def test_episode_title_is_compacted_for_ingested_records(tmp_path) -> None:
    """Ingested episode titles stay within the extraction benchmark title budget."""
    ctx = _context(tmp_path)
    prepare_context_store(ctx)
    payload = _synthesized_payload()
    payload["episode"]["title"] = (
        "Established cloud record provenance and all-projects labeling constraints"
    )

    observations, done, _summary = persist_synthesized_extraction(payload, ctx)

    store = ContextStore(ctx.context_db_path)
    rows = store.query(
        entity="records",
        mode="list",
        project_ids=[ctx.project_identity.project_id],
        source_session_id=ctx.session_id,
        limit=10,
        include_archived=True,
    )["rows"]
    episode = next(row for row in rows if row["kind"] == "episode")

    assert done is True
    assert observations[0]["ok"] is True
    assert len(episode["title"]) <= 72


def test_model_completion_summary_is_not_persisted(tmp_path) -> None:
    """Persistence uses deterministic summaries instead of model-authored chatter."""
    ctx = _context(tmp_path)
    prepare_context_store(ctx)
    payload = _synthesized_payload()
    payload["completion_summary"] = (
        "Created context but also mentioned temporary browser console CSS details."
    )

    observations, done, summary = persist_synthesized_extraction(payload, ctx)

    assert done is True
    assert summary == "Trace ingestion completed: 1 durable record created."
    assert observations[-1]["content"] == summary
    assert "CSS" not in summary


def test_persists_operational_role_metadata(tmp_path) -> None:
    """Durable record role annotations are saved with the canonical record."""
    ctx = _context(tmp_path)
    prepare_context_store(ctx)
    payload = _synthesized_payload()
    payload["durable_records"][0]["record_role"] = "gotcha"
    payload["durable_records"][0]["role_payload"] = {
        "condition": "Replaying an already-ingested trace.",
        "symptom": "The episode already exists.",
        "avoid": "Do not create duplicate episode records.",
        "recover": "Treat duplicate episodes as idempotent replays.",
    }

    observations, done, _summary = persist_synthesized_extraction(payload, ctx)

    store = ContextStore(ctx.context_db_path)
    rows = store.query(
        entity="records",
        mode="list",
        project_ids=[ctx.project_identity.project_id],
        record_role="gotcha",
        limit=10,
        include_archived=True,
    )["rows"]

    assert done is True
    assert observations[1]["ok"] is True
    assert len(rows) == 1
    assert rows[0]["record_role"] == "gotcha"
    assert "duplicate episode records" in rows[0]["role_payload"]
