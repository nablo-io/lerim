"""Tests for BAML extract persistence helpers."""

from __future__ import annotations

from pathlib import Path

from lerim.agents.extract.persistence import (
    PersistenceContext,
    persist_synthesized_extraction,
    prepare_context_store,
)
from lerim.context import ContextStore, resolve_project_identity


def _context(tmp_path: Path) -> PersistenceContext:
    """Return one isolated persistence context."""
    return PersistenceContext(
        context_db_path=tmp_path / "context.sqlite3",
        project_identity=resolve_project_identity(tmp_path),
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
            "user_intent": "Extract context from the trace.",
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
