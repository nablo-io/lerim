"""Tests for BAML extract persistence helpers."""

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


def test_record_updates_revise_existing_durable_without_duplicate(tmp_path) -> None:
    """Synthesized updates should append a version instead of creating a duplicate."""
    ctx = _context(tmp_path)
    prepare_context_store(ctx)
    store = ContextStore(ctx.context_db_path)
    store.create_record(
        project_id=ctx.project_identity.project_id,
        session_id=ctx.session_id,
        change_reason="seed",
        record_id="rec_existing_boundary",
        kind="decision",
        title="Keep product and queue state separate",
        body="Keep product and queue state separate.",
        status="active",
        decision="Keep product state separate from queue state.",
        why="They should not share one persistence path.",
    )
    payload = _synthesized_payload()
    payload["durable_records"] = []
    payload["record_updates"] = [
        {
            "record_id": "rec_existing_boundary",
            "kind": "decision",
            "title": "Keep product and queue runtime state separate",
            "body": (
                "Keep product state and queue runtime state separate because "
                "their lifecycle and recovery semantics differ."
            ),
            "status": "active",
            "decision": "Keep product state and queue runtime state separate.",
            "why": "Their lifecycle and recovery semantics differ.",
            "consequences": "Future storage work should preserve this boundary.",
        }
    ]

    observations, done, _summary = persist_synthesized_extraction(payload, ctx)

    rows = store.query(
        entity="records",
        mode="list",
        project_ids=[ctx.project_identity.project_id],
        limit=10,
        include_archived=True,
    )["rows"]
    durable_rows = [row for row in rows if row["kind"] != "episode"]
    updated = store.fetch_record(
        "rec_existing_boundary",
        project_ids=[ctx.project_identity.project_id],
        include_versions=True,
    )

    assert done is True
    assert any("updated_record_id" in observation["content"] for observation in observations)
    assert len(durable_rows) == 1
    assert updated is not None
    assert updated["why"] == "Their lifecycle and recovery semantics differ."
    assert len(updated["versions"]) == 2
