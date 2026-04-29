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
    assert "read_trace" in tool_names
    assert "save_context" in tool_names
    assert set(tool_names).issubset(EXTRACT_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
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
def test_extract_multi_record_trace_keeps_two_independent_records(
    live_config,
    live_repo_root,
) -> None:
    """A single trace can produce two independent durable records when each stands on its own."""
    expectation = load_extract_expectation("multi_record_trace")["expected"]
    outcome = run_extract_case(
        case_name="multi_record_trace",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    tool_names = outcome.tool_names
    assert set(tool_names).issubset(EXTRACT_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    rows = outcome.rows
    episode_rows = [row for row in rows if row["kind"] == "episode"]
    durable_rows = [row for row in rows if row["kind"] != "episode"]
    decision_rows = [row for row in rows if row["kind"] == "decision"]
    fact_rows = [row for row in rows if row["kind"] == "fact"]

    assert outcome.result.completion_summary.strip()
    assert len(episode_rows) == expectation["episode_count"]
    assert len(durable_rows) == expectation["durable_count"]
    assert len(decision_rows) == expectation["decision_count"]
    assert len(fact_rows) == expectation["fact_count"]

    decision = next(record for record in outcome.records if record["kind"] == "decision")
    fact = next(record for record in outcome.records if record["kind"] == "fact")
    episode = next(record for record in outcome.records if record["kind"] == "episode")

    assert decision["decision"]
    assert decision["why"]
    assert len(str(episode["body"])) <= 420
    assert len(decision["versions"]) >= 1
    assert len(fact["versions"]) >= 1

    decision_text = " ".join(
        str(decision.get(field) or "")
        for field in ("title", "body", "decision", "why", "consequences")
    ).lower()
    for token in expectation["decision_text_must_include_all"]:
        assert token in decision_text
    assert any(token in decision_text for token in expectation["decision_text_must_include_any"])
    for token in expectation["decision_text_must_not_include"]:
        assert token not in decision_text

    fact_text = " ".join(
        str(fact.get(field) or "")
        for field in ("title", "body")
    ).lower()
    for token in expectation["fact_text_must_include_all"]:
        assert token in fact_text
    assert any(token in fact_text for token in expectation["fact_text_must_include_any"])
    for token in expectation["fact_text_must_not_include"]:
        assert token not in fact_text

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
def test_extract_recap_temptation_trace_stays_compact_and_durable(
    live_config,
    live_repo_root,
) -> None:
    """A review-shaped trace should still produce one compact durable decision."""
    expectation = load_extract_expectation("recap_temptation_trace")["expected"]
    outcome = run_extract_case(
        case_name="recap_temptation_trace",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    tool_names = outcome.tool_names
    assert set(tool_names).issubset(EXTRACT_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
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
def test_extract_stable_user_preference_creates_preference_record(
    live_config,
    live_repo_root,
) -> None:
    """A stable workflow preference should become its own preference record."""
    expectation = load_extract_expectation("stable_user_preference")["expected"]
    outcome = run_extract_case(
        case_name="stable_user_preference",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    tool_names = outcome.tool_names
    assert set(tool_names).issubset(EXTRACT_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    rows = outcome.rows
    episode_rows = [row for row in rows if row["kind"] == "episode"]
    durable_rows = [row for row in rows if row["kind"] != "episode"]
    preference_rows = [row for row in rows if row["kind"] == "preference"]

    assert outcome.result.completion_summary.strip()
    assert len(episode_rows) == expectation["episode_count"]
    assert len(durable_rows) == expectation["durable_count"]
    assert len(preference_rows) == expectation["preference_count"]

    preference = next(record for record in outcome.records if record["kind"] == "preference")
    episode = next(record for record in outcome.records if record["kind"] == "episode")

    assert len(str(episode["body"])) <= 420
    assert len(preference["versions"]) >= 1

    preference_text = " ".join(
        str(preference.get(field) or "")
        for field in ("title", "body")
    ).lower()
    for token in expectation["preference_text_must_include_all"]:
        assert token in preference_text
    assert any(token in preference_text for token in expectation["preference_text_must_include_any"])
    for token in expectation["preference_text_must_not_include"]:
        assert token not in preference_text

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
def test_extract_environment_fact_from_noisy_error_creates_fact_record(
	live_config,
	live_repo_root,
) -> None:
    """A noisy environment failure should become one fact record, not raw error context."""
    expectation = load_extract_expectation("environment_fact_from_noisy_error")["expected"]
    outcome = run_extract_case(
        case_name="environment_fact_from_noisy_error",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    tool_names = outcome.tool_names
    assert set(tool_names).issubset(EXTRACT_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    rows = outcome.rows
    episode_rows = [row for row in rows if row["kind"] == "episode"]
    durable_rows = [row for row in rows if row["kind"] != "episode"]
    fact_rows = [row for row in rows if row["kind"] == "fact"]

    assert outcome.result.completion_summary.strip()
    assert len(episode_rows) == expectation["episode_count"]
    assert len(durable_rows) == expectation["durable_count"]
    assert len(fact_rows) == expectation["fact_count"]

    fact = next(record for record in outcome.records if record["kind"] == "fact")
    episode = next(record for record in outcome.records if record["kind"] == "episode")

    assert len(str(episode["body"])) <= 420
    assert len(fact["versions"]) >= 1

    fact_text = " ".join(
        str(fact.get(field) or "")
        for field in ("title", "body")
    ).lower()
    for token in expectation["fact_text_must_include_all"]:
        assert token in fact_text
    assert any(token in fact_text for token in expectation["fact_text_must_include_any"])
    for token in expectation["fact_text_must_not_include"]:
        assert token not in fact_text

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
def test_extract_runtime_requirement_from_diagnostics_creates_fact_record(
    live_config,
    live_repo_root,
) -> None:
    """Diagnostics can reveal a durable requirement without storing diagnostic noise."""
    expectation = load_extract_expectation("runtime_requirement_from_diagnostics")["expected"]
    outcome = run_extract_case(
        case_name="runtime_requirement_from_diagnostics",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    tool_names = outcome.tool_names
    assert set(tool_names).issubset(EXTRACT_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    rows = outcome.rows
    episode_rows = [row for row in rows if row["kind"] == "episode"]
    durable_rows = [row for row in rows if row["kind"] != "episode"]
    fact_rows = [row for row in rows if row["kind"] == "fact"]

    assert outcome.result.completion_summary.strip()
    assert len(episode_rows) == expectation["episode_count"]
    assert len(durable_rows) == expectation["durable_count"]
    assert len(fact_rows) == expectation["fact_count"]

    fact = next(record for record in outcome.records if record["kind"] == "fact")
    fact_text = " ".join(str(fact.get(field) or "") for field in ("title", "body")).lower()
    for token in expectation["fact_text_must_include_all"]:
        assert token in fact_text
    assert any(token in fact_text for token in expectation["fact_text_must_include_any"])
    for token in expectation["fact_text_must_not_include"]:
        assert token not in fact_text

    assert_clean_context_schema(live_config.context_db_path)
    assert_quality_metrics(audit_context_db(live_config.context_db_path))


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_extract_constraint_trace_creates_constraint_record(
    live_config,
    live_repo_root,
) -> None:
    """A durable invariant should become a constraint record."""
    expectation = load_extract_expectation("constraint_extraction")["expected"]
    outcome = run_extract_case(
        case_name="constraint_extraction",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    tool_names = outcome.tool_names
    assert set(tool_names).issubset(EXTRACT_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    rows = outcome.rows
    episode_rows = [row for row in rows if row["kind"] == "episode"]
    durable_rows = [row for row in rows if row["kind"] != "episode"]
    constraint_rows = [row for row in rows if row["kind"] == "constraint"]

    assert outcome.result.completion_summary.strip()
    assert len(episode_rows) == expectation["episode_count"]
    assert len(durable_rows) == expectation["durable_count"]
    assert len(constraint_rows) == expectation["constraint_count"]

    constraint = next(record for record in outcome.records if record["kind"] == "constraint")
    constraint_text = " ".join(str(constraint.get(field) or "") for field in ("title", "body")).lower()
    for token in expectation["constraint_text_must_include_all"]:
        assert token in constraint_text
    assert any(token in constraint_text for token in expectation["constraint_text_must_include_any"])
    for token in expectation["constraint_text_must_not_include"]:
        assert token not in constraint_text


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_extract_reference_trace_creates_reference_record(
    live_config,
    live_repo_root,
) -> None:
    """A durable external source-of-truth pointer should become a reference record."""
    expectation = load_extract_expectation("reference_extraction")["expected"]
    outcome = run_extract_case(
        case_name="reference_extraction",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    tool_names = outcome.tool_names
    assert set(tool_names).issubset(EXTRACT_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    rows = outcome.rows
    episode_rows = [row for row in rows if row["kind"] == "episode"]
    durable_rows = [row for row in rows if row["kind"] != "episode"]
    reference_rows = [row for row in rows if row["kind"] == "reference"]

    assert outcome.result.completion_summary.strip()
    assert len(episode_rows) == expectation["episode_count"]
    assert len(durable_rows) == expectation["durable_count"]
    assert len(reference_rows) == expectation["reference_count"]

    reference = next(record for record in outcome.records if record["kind"] == "reference")
    reference_text = " ".join(str(reference.get(field) or "") for field in ("title", "body")).lower()
    for token in expectation["reference_text_must_include_all"]:
        assert token in reference_text
    assert any(token in reference_text for token in expectation["reference_text_must_include_any"])
    for token in expectation["reference_text_must_not_include"]:
        assert token not in reference_text


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_extract_decision_without_why_falls_back_to_fact(
    live_config,
    live_repo_root,
) -> None:
    """When no durable rationale exists, extract should store a fact instead of inventing a decision."""
    expectation = load_extract_expectation("decision_without_why_falls_back_to_fact")["expected"]
    outcome = run_extract_case(
        case_name="decision_without_why_falls_back_to_fact",
        live_config=live_config,
        live_repo_root=live_repo_root,
    )

    tool_names = outcome.tool_names
    assert set(tool_names).issubset(EXTRACT_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    rows = outcome.rows
    episode_rows = [row for row in rows if row["kind"] == "episode"]
    durable_rows = [row for row in rows if row["kind"] != "episode"]
    fact_rows = [row for row in rows if row["kind"] == "fact"]
    decision_rows = [row for row in rows if row["kind"] == "decision"]

    assert outcome.result.completion_summary.strip()
    assert len(episode_rows) == expectation["episode_count"]
    assert len(durable_rows) == expectation["durable_count"]
    assert len(fact_rows) == expectation["fact_count"]
    assert len(decision_rows) == expectation["decision_count"]

    fact = next(record for record in outcome.records if record["kind"] == "fact")
    fact_text = " ".join(str(fact.get(field) or "") for field in ("title", "body")).lower()
    for token in expectation["fact_text_must_include_all"]:
        assert token in fact_text
    assert any(token in fact_text for token in expectation["fact_text_must_include_any"])
    for token in expectation["fact_text_must_not_include"]:
        assert token not in fact_text
