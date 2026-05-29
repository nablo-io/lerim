"""Tests for coding-profile record shaping."""

from __future__ import annotations

from lerim.agents.trace_ingestion.coding_records import (
    _dedupe_coding_records,
    coding_eval_polish_to_synthesized,
)


def test_direct_user_strategy_replaces_fixed_slot_duplicate(tmp_path) -> None:
    """Direct visible-user conventions should outrank lower-level fixed slots."""
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        '{"type":"user","message":{"role":"user","content":"Yes, go with PostgreSQL. Also, I always want to use UUIDs for primary keys instead of auto-increment integers. And make sure all timestamps use UTC timezone. These are project-wide conventions."}}\n',
        encoding="utf-8",
    )

    payload = coding_eval_polish_to_synthesized(
        {
            "episode": {
                "title": "Database convention",
                "body": "The project adopted database conventions.",
                "status": "active",
            },
            "fixture_constraint": {
                "title": "UUID and UTC conventions",
                "body": "Use UUID primary keys and UTC timestamps.",
                "status": "active",
                "source_event_refs": ["line:1"],
                "evidence_refs": ["Use UUID primary keys and UTC timestamps"],
            },
            "completion_summary": "Done.",
        },
        trace_path=trace_path,
        supplemental_strategy_slots={
            "user_strategy_records": [
                {
                    "kind": "constraint",
                    "title": "UUID primary keys and UTC timestamps",
                    "body": "Use UUID primary keys and UTC timestamps as project-wide conventions.",
                    "status": "active",
                    "source_event_refs": ["line:1"],
                    "evidence_refs": ["Use UUID primary keys and UTC timestamps"],
                }
            ]
        },
    )

    [record] = payload["durable_records"]
    assert record["title"] == "UUID primary keys and UTC timestamps"
    assert record["kind"] == "constraint"
    assert "UTC timezone" in record["body"]
    assert record["source_event_refs"] == ["line:1"]


def test_dedupe_drops_same_body_and_source_across_kinds() -> None:
    """The same source/body should not survive under two different kinds."""
    records = [
        {
            "kind": "preference",
            "title": "UUID primary keys",
            "body": "Use UUID primary keys and UTC timestamps.",
            "source_event_refs": ["line:3"],
        },
        {
            "kind": "constraint",
            "title": "UTC timestamps",
            "body": "Use UUID primary keys and UTC timestamps.",
            "source_event_refs": ["line:3"],
        },
    ]

    assert _dedupe_coding_records(records) == [records[0]]
