"""Targeted real-LLM integration cases for the extract agent."""

from __future__ import annotations

import pytest

from tests.integration.extract_helpers import load_extract_expectation, run_extract_case
from tests.live_helpers import (
    EXTRACT_TOOL_NAMES,
    FRAMEWORK_TOOL_NAMES,
    assert_clean_context_schema,
    assert_no_legacy_tools,
    assert_quality_metrics,
    audit_context_db,
    connect_context_db,
)


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_extract_clear_decision_ignores_implementation_noise(
    live_config,
    live_repo_root,
) -> None:
    """Extract should keep the durable DB split and ignore local coding noise."""
    expectation = load_extract_expectation("clear_decision_with_noise")["expected"]
    outcome = run_extract_case(
        case_name="clear_decision_with_noise",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    tool_names = outcome.tool_names
    assert "trace_read" in tool_names
    assert "create_record" in tool_names
    assert set(tool_names).issubset(EXTRACT_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    assert_no_legacy_tools(tool_names)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    rows = outcome.rows
    episode_rows = [row for row in rows if row["kind"] == "episode"]
    durable_rows = [row for row in rows if row["kind"] != "episode"]
    decision_rows = [row for row in rows if row["kind"] == "decision"]

    assert outcome.result.completion_summary.strip()
    assert len(episode_rows) == expectation["episode_count"]
    assert len(durable_rows) == expectation["durable_count"]
    assert len(decision_rows) == expectation["decision_count"]

    decision = next(record for record in outcome.records if record["kind"] == "decision")
    episode = next(record for record in outcome.records if record["kind"] == "episode")

    assert decision["decision"]
    assert decision["why"]
    assert len(str(episode["body"])) <= 420
    assert len(decision["versions"]) >= 1

    decision_text = " ".join(
        str(decision.get(field) or "")
        for field in ("title", "body", "decision", "why", "consequences")
    ).lower()
    for token in expectation["decision_text_must_include_all"]:
        assert token in decision_text
    assert any(token in decision_text for token in expectation["decision_text_must_include_any"])
    for noise_marker in expectation["decision_text_must_not_include"]:
        assert noise_marker not in decision_text

    with connect_context_db(live_config.context_db_path) as conn:
        record_ids = [str(row["record_id"]) for row in rows]
        placeholders = ", ".join("?" for _ in record_ids)
        embedding_count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM record_embeddings WHERE record_id IN ({placeholders})",
                tuple(record_ids),
            ).fetchone()[0]
        )
        fts_count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM records_fts WHERE record_id IN ({placeholders})",
                tuple(record_ids),
            ).fetchone()[0]
        )

    assert embedding_count == len(record_ids)
    assert fts_count == len(record_ids)
    assert_clean_context_schema(live_config.context_db_path)
    assert_quality_metrics(audit_context_db(live_config.context_db_path))


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_extract_updates_existing_memory_instead_of_creating_duplicate(
    live_config,
    live_repo_root,
) -> None:
    """Extract should revise one seeded durable record instead of duplicating it."""
    expectation = load_extract_expectation("duplicate_existing_memory")["expected"]
    outcome = run_extract_case(
        case_name="duplicate_existing_memory",
        live_config=live_config,
        live_repo_root=live_repo_root,
        seed_records=[
            {
                "record_id": "rec_existing_storage_split",
                "kind": "decision",
                "title": "Keep product state and queue state separate",
                "body": (
                    "Keep product state and queue-processing state separate. "
                    "Why: they should not share one persistence path."
                ),
                "decision": "Separate product state from queue-processing state.",
                "why": "They should not share one persistence path.",
            }
        ],
    )

    tool_names = outcome.tool_names
    assert set(tool_names).issubset(EXTRACT_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    assert_no_legacy_tools(tool_names)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    created_rows = outcome.rows
    created_episode_rows = [row for row in created_rows if row["kind"] == "episode"]
    created_durable_rows = [row for row in created_rows if row["kind"] != "episode"]
    changed_records = outcome.changed_records
    changed_decisions = [record for record in changed_records if record["kind"] == "decision"]

    assert outcome.result.completion_summary.strip()
    assert len(created_episode_rows) == expectation["episode_count"]
    assert len(created_durable_rows) == expectation["created_durable_count"]
    assert len(changed_records) == expectation["changed_record_count"]
    assert len(changed_decisions) == expectation["changed_decision_count"]

    updated_decision = next(record for record in changed_decisions if record["record_id"] == "rec_existing_storage_split")
    assert len(updated_decision["versions"]) >= 2
    latest_change_kinds = {str(version["change_kind"]) for version in updated_decision["versions"][:2]}
    assert "update" in latest_change_kinds

    updated_text = " ".join(
        str(updated_decision.get(field) or "")
        for field in ("title", "body", "decision", "why", "consequences")
    ).lower()
    for token in expectation["updated_decision_text_must_include_all"]:
        assert token in updated_text
    assert any(token in updated_text for token in expectation["updated_decision_text_must_include_any"])
    for token in expectation["updated_decision_text_must_not_include"]:
        assert token not in updated_text

    with connect_context_db(live_config.context_db_path) as conn:
        durable_total = int(
            conn.execute(
                "SELECT COUNT(*) FROM records WHERE kind != 'episode' AND project_id = ?",
                (outcome.project_id,),
            ).fetchone()[0]
        )
        seeded_version_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM record_versions WHERE record_id = ?",
                ("rec_existing_storage_split",),
            ).fetchone()[0]
        )

    assert durable_total == 1
    assert seeded_version_count >= 2
    assert_clean_context_schema(live_config.context_db_path)
    assert_quality_metrics(audit_context_db(live_config.context_db_path))
