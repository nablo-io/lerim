"""Targeted real-LLM integration cases for the extract agent."""

from __future__ import annotations

import pytest

from tests.integration.trace_ingestion.helpers import load_extract_expectation, run_extract_case
from tests.live_helpers import (
    EXTRACT_EVENT_NAMES,
    FRAMEWORK_TOOL_NAMES,
    assert_clean_context_schema,
    assert_quality_metrics,
    audit_context_db,
    connect_context_db,
)


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_extract_updates_existing_record_instead_of_creating_duplicate(
    live_config,
    live_repo_root,
) -> None:
    """Extract should revise one seeded durable record instead of duplicating it."""
    expectation = load_extract_expectation("duplicate_existing_record")["expected"]
    outcome = run_extract_case(
        case_name="duplicate_existing_record",
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
    assert set(tool_names).issubset(EXTRACT_EVENT_NAMES | FRAMEWORK_TOOL_NAMES)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    created_rows = outcome.rows
    created_episode_rows = [row for row in created_rows if row["kind"] == "episode"]
    created_durable_rows = [row for row in created_rows if row["kind"] != "episode"]
    changed_records = outcome.changed_records
    changed_durable_records = [
        record for record in changed_records if record["kind"] != "episode"
    ]
    changed_decisions = [
        record for record in changed_durable_records if record["kind"] == "decision"
    ]

    assert outcome.result.completion_summary.strip()
    assert len(created_episode_rows) == expectation["episode_count"]
    assert len(created_durable_rows) == expectation["created_durable_count"]
    assert len(changed_durable_records) == expectation["changed_record_count"]
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

@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_extract_routine_operational_trace_creates_no_durable_record(
    live_config,
    live_repo_root,
) -> None:
    """Routine operational cleanup should produce only an archived episode."""
    expectation = load_extract_expectation("routine_operational_no_durable_record")["expected"]
    outcome = run_extract_case(
        case_name="routine_operational_no_durable_record",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    tool_names = outcome.tool_names
    assert set(tool_names).issubset(EXTRACT_EVENT_NAMES | FRAMEWORK_TOOL_NAMES)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    rows = outcome.rows
    episode_rows = [row for row in rows if row["kind"] == "episode"]
    durable_rows = [row for row in rows if row["kind"] != "episode"]

    assert outcome.result.completion_summary.strip()
    assert len(episode_rows) == expectation["episode_count"]
    assert len(durable_rows) == expectation["durable_count"]

    episode = next(record for record in outcome.records if record["kind"] == "episode")
    assert episode["status"] == "archived"
    assert episode["user_intent"]
    assert episode["what_happened"]
    assert len(str(episode["body"])) <= 420

    episode_text = " ".join(
        str(episode.get(field) or "")
        for field in ("title", "body", "user_intent", "what_happened", "outcomes")
    ).lower()
    assert any(token in episode_text for token in expectation["episode_text_must_include_any"])

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
        durable_total = int(
            conn.execute(
                "SELECT COUNT(*) FROM records WHERE project_id = ? AND kind != 'episode'",
                (outcome.project_id,),
            ).fetchone()[0]
        )

    assert embedding_count == len(record_ids)
    assert fts_count == len(record_ids)
    assert durable_total == 0
    assert_clean_context_schema(live_config.context_db_path)
    assert_quality_metrics(audit_context_db(live_config.context_db_path))

@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_extract_borderline_non_durable_incident_abstains_from_record(
    live_config,
    live_repo_root,
) -> None:
    """A one-off branch artifact should stay in the episode instead of becoming a durable record."""
    expectation = load_extract_expectation("borderline_non_durable_incident")["expected"]
    outcome = run_extract_case(
        case_name="borderline_non_durable_incident",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    tool_names = outcome.tool_names
    assert set(tool_names).issubset(EXTRACT_EVENT_NAMES | FRAMEWORK_TOOL_NAMES)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    rows = outcome.rows
    episode_rows = [row for row in rows if row["kind"] == "episode"]
    durable_rows = [row for row in rows if row["kind"] != "episode"]

    assert outcome.result.completion_summary.strip()
    assert len(episode_rows) == expectation["episode_count"]
    assert len(durable_rows) == expectation["durable_count"]

    episode = next(record for record in outcome.records if record["kind"] == "episode")
    assert episode["user_intent"]
    assert episode["what_happened"]
    assert len(str(episode["body"])) <= 420

    episode_text = " ".join(
        str(episode.get(field) or "")
        for field in ("title", "body", "user_intent", "what_happened", "outcomes")
    ).lower()
    assert any(token in episode_text for token in expectation["episode_text_must_include_any"])

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
        durable_total = int(
            conn.execute(
                "SELECT COUNT(*) FROM records WHERE project_id = ? AND kind != 'episode'",
                (outcome.project_id,),
            ).fetchone()[0]
        )

    assert embedding_count == len(record_ids)
    assert fts_count == len(record_ids)
    assert durable_total == 0
    assert_clean_context_schema(live_config.context_db_path)
    assert_quality_metrics(audit_context_db(live_config.context_db_path))


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_extract_similar_but_new_decision_creates_new_record(
    live_config,
    live_repo_root,
) -> None:
    """A semantically nearby existing record should not block creation of a genuinely new decision."""
    expectation = load_extract_expectation("similar_but_new_decision")["expected"]
    outcome = run_extract_case(
        case_name="similar_but_new_decision",
        live_config=live_config,
        live_repo_root=live_repo_root,
        seed_records=[
            {
                "record_id": "rec_existing_retry_budget",
                "kind": "decision",
                "title": "Persist retry budget in job metadata",
                "body": "Persist retry budget in job metadata so restarts and failover preserve retry state.",
                "decision": "Persist retry budget in job metadata.",
                "why": "Retry state must survive restarts and be visible to all workers.",
            }
        ],
    )

    tool_names = outcome.tool_names
    assert set(tool_names).issubset(EXTRACT_EVENT_NAMES | FRAMEWORK_TOOL_NAMES)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    created_rows = outcome.rows
    created_episode_rows = [row for row in created_rows if row["kind"] == "episode"]
    created_durable_rows = [row for row in created_rows if row["kind"] != "episode"]
    decision_rows = [row for row in created_rows if row["kind"] == "decision"]

    assert len(created_episode_rows) == expectation["episode_count"]
    assert len(created_durable_rows) == expectation["created_durable_count"]
    assert len(decision_rows) == expectation["decision_count"]

    decision = next(record for record in outcome.records if record["kind"] == "decision")
    decision_text = " ".join(
        str(decision.get(field) or "") for field in ("title", "body", "decision", "why", "consequences")
    ).lower()
    for token in expectation["decision_text_must_include_all"]:
        assert token in decision_text
    assert any(token in decision_text for token in expectation["decision_text_must_include_any"])
    for token in expectation["decision_text_must_not_include"]:
        assert token not in decision_text


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_extract_ambiguous_search_hits_update_only_true_target(
    live_config,
    live_repo_root,
) -> None:
    """When multiple nearby records exist, extract should update only the true target."""
    expectation = load_extract_expectation("ambiguous_search_hits_correct_update_target")["expected"]
    outcome = run_extract_case(
        case_name="ambiguous_search_hits_correct_update_target",
        live_config=live_config,
        live_repo_root=live_repo_root,
        seed_records=[
            {
                "record_id": "rec_storage_split",
                "kind": "decision",
                "title": "Keep product state and queue runtime state separate",
                "body": "Keep product state and queue runtime state separate.",
                "decision": "Keep product state and queue runtime state separate.",
                "why": "They should not share one persistence path.",
            },
            {
                "record_id": "rec_storage_routing",
                "kind": "decision",
                "title": "Route product queries to the context DB",
                "body": "Product queries should read from the context DB while operational indexing uses the sessions DB.",
                "decision": "Route product queries to the context DB.",
                "why": "Operational indexing belongs in the sessions DB.",
            },
        ],
    )

    tool_names = outcome.tool_names
    assert set(tool_names).issubset(EXTRACT_EVENT_NAMES | FRAMEWORK_TOOL_NAMES)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    assert len(outcome.rows) == expectation["episode_count"]
    changed_durable_records = [record for record in outcome.changed_records if record["kind"] != "episode"]
    assert len(changed_durable_records) == expectation["changed_record_count"]
    updated = next(record for record in changed_durable_records if record["record_id"] == expectation["updated_record_id"])
    updated_text = " ".join(
        str(updated.get(field) or "") for field in ("title", "body", "decision", "why", "consequences")
    ).lower()
    for token in expectation["updated_decision_text_must_include_all"]:
        assert token in updated_text
    assert any(token in updated_text for token in expectation["updated_decision_text_must_include_any"])
    for token in expectation["updated_decision_text_must_not_include"]:
        assert token not in updated_text
    changed_record_ids = {str(row["record_id"]) for row in outcome.changed_version_rows}
    for record_id in expectation["unchanged_record_ids"]:
        assert record_id not in changed_record_ids
