"""Targeted real-LLM integration cases for the extract agent."""

from __future__ import annotations

import pytest

from tests.integration.extract.helpers import load_extract_expectation, run_extract_case
from tests.live_helpers import (
    EXTRACT_TOOL_NAMES,
    FRAMEWORK_TOOL_NAMES,
    assert_clean_context_schema,
    assert_quality_metrics,
    audit_context_db,
    connect_context_db,
)


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_extract_long_trace_requires_note_before_writing(
    live_config,
    live_repo_root,
) -> None:
    """Long traces should trigger multi-read extraction with note_trace_findings compression."""
    expectation = load_extract_expectation("long_trace_requires_note")["expected"]
    outcome = run_extract_case(
        case_name="long_trace_requires_note",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    tool_names = outcome.tool_names
    assert set(tool_names).issubset(EXTRACT_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names
    assert tool_names.count("read_trace") >= expectation["read_trace_count_at_least"]
    assert (
        tool_names.count("note_trace_findings")
        >= expectation["note_trace_findings_count_at_least"]
    )

    rows = outcome.rows
    episode_rows = [row for row in rows if row["kind"] == "episode"]
    durable_rows = [row for row in rows if row["kind"] != "episode"]
    decision_rows = [row for row in rows if row["kind"] == "decision"]

    assert outcome.result.completion_summary.strip()
    assert len(episode_rows) == expectation["episode_count"]
    assert len(durable_rows) == expectation["durable_count"]
    assert len(decision_rows) == expectation["decision_count"]

    decision = next(
        record for record in outcome.records if record["kind"] == "decision"
    )
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
    assert any(
        token in decision_text
        for token in expectation["decision_text_must_include_any"]
    )
    for token in expectation["decision_text_must_not_include"]:
        assert token not in decision_text

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
def test_extract_very_long_trace_requires_prune(
    live_config,
    live_repo_root,
) -> None:
    """Very long traces should stay compressed while preserving the extracted signal."""
    expectation = load_extract_expectation("very_long_trace_requires_prune")["expected"]
    outcome = run_extract_case(
        case_name="very_long_trace_requires_prune",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    tool_names = outcome.tool_names
    assert set(tool_names).issubset(EXTRACT_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names
    assert tool_names.count("read_trace") >= expectation["read_trace_count_at_least"]
    assert (
        tool_names.count("note_trace_findings")
        >= expectation["note_trace_findings_count_at_least"]
    )
    assert (
        tool_names.count("prune_trace_reads")
        >= expectation["prune_trace_reads_count_at_least"]
    )

    rows = outcome.rows
    episode_rows = [row for row in rows if row["kind"] == "episode"]
    durable_rows = [row for row in rows if row["kind"] != "episode"]
    durable_kind_any_of = tuple(expectation["durable_kind_any_of"])
    matching_durable_rows = [row for row in rows if row["kind"] in durable_kind_any_of]

    assert outcome.result.completion_summary.strip()
    assert len(episode_rows) == expectation["episode_count"]
    assert len(durable_rows) == expectation["durable_count"]
    assert len(matching_durable_rows) == 1

    durable_record = next(
        record for record in outcome.records if record["kind"] in durable_kind_any_of
    )
    episode = next(record for record in outcome.records if record["kind"] == "episode")

    assert len(str(episode["body"])) <= 420
    assert len(durable_record["versions"]) >= 1
    if durable_record["kind"] == "decision":
        assert durable_record["decision"]
        assert durable_record["why"]

    durable_text = " ".join(
        str(durable_record.get(field) or "")
        for field in ("title", "body", "decision", "why", "consequences")
    ).lower()
    for token in expectation["durable_text_must_include_all"]:
        assert token in durable_text
    assert any(
        token in durable_text for token in expectation["durable_text_must_include_any"]
    )
    for token in expectation["durable_text_must_not_include"]:
        assert token not in durable_text


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_extract_late_disambiguation_at_end_of_trace(
    live_config,
    live_repo_root,
) -> None:
    """The final clarifying chunk should win over earlier lures in a long trace."""
    expectation = load_extract_expectation("late_disambiguation_at_end_of_trace")[
        "expected"
    ]
    outcome = run_extract_case(
        case_name="late_disambiguation_at_end_of_trace",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    tool_names = outcome.tool_names
    assert set(tool_names).issubset(EXTRACT_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    assert tool_names.count("read_trace") >= expectation["min_read_trace_calls"]

    rows = outcome.rows
    episode_rows = [row for row in rows if row["kind"] == "episode"]
    durable_rows = [row for row in rows if row["kind"] != "episode"]
    durable_kind_any_of = tuple(expectation["durable_kind_any_of"])
    matching_durable_rows = [row for row in rows if row["kind"] in durable_kind_any_of]

    assert len(episode_rows) == expectation["episode_count"]
    assert len(durable_rows) == expectation["durable_count"]
    assert len(matching_durable_rows) == 1

    durable_record = next(
        record for record in outcome.records if record["kind"] in durable_kind_any_of
    )
    if durable_record["kind"] == "decision":
        assert durable_record["decision"]
        assert durable_record["why"]

    durable_text = " ".join(
        str(durable_record.get(field) or "")
        for field in ("title", "body", "decision", "why", "consequences")
    ).lower()
    for token in expectation["durable_text_must_include_all"]:
        assert token in durable_text
    assert any(
        token in durable_text for token in expectation["durable_text_must_include_any"]
    )
    for token in expectation["durable_text_must_not_include"]:
        assert token not in durable_text

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
