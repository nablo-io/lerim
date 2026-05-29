"""Tests for trace-ingestion structured output schema defaults."""

from __future__ import annotations

from lerim.agents.trace_ingestion.schemas import (
    CodingEvalPolishedContextRecords,
    CodingStrategySlotRecords,
)


def test_coding_polish_schema_accepts_omitted_empty_record_lists() -> None:
    """DSPy providers may omit optional empty list slots."""
    result = CodingEvalPolishedContextRecords.model_validate(
        {
            "episode": {
                "title": "Postgres migration planning",
                "body": "The session chose reusable database conventions.",
                "status": "active",
                "user_intent": "Choose database conventions.",
                "what_happened": "The team selected simple migration and ID rules.",
                "source_event_refs": ["line:1"],
                "evidence_refs": ["line:1"],
            },
            "completion_summary": "Done.",
        }
    )

    assert result.user_strategy_records == []
    assert result.other_records == []


def test_coding_strategy_schema_accepts_no_general_strategy_records() -> None:
    """The direct-user strategy extractor can return no generic records."""
    result = CodingStrategySlotRecords.model_validate(
        {
            "silent_change_feedback_record": None,
            "model_size_priority_record": None,
            "provider_cost_record": None,
            "role_split_record": None,
        }
    )

    assert result.user_strategy_records == []
