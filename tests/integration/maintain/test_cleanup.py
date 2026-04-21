"""Targeted real-LLM integration cases for the maintain agent."""

from __future__ import annotations

import re

import pytest

from tests.integration.maintain.helpers import load_maintain_expectation, run_maintain_case
from tests.live_helpers import (
    FRAMEWORK_TOOL_NAMES,
    MAINTAIN_TOOL_NAMES,
    assert_clean_context_schema,
    assert_no_legacy_tools,
    assert_quality_metrics,
    audit_context_db,
    connect_context_db,
)


def _normalize_assertion_text(value: str) -> str:
    """Normalize punctuation and spacing for resilient text-shape assertions."""
    collapsed = re.sub(r"[-_/]+", " ", value.lower())
    return re.sub(r"\s+", " ", collapsed).strip()


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_maintain_routine_episode_archived(
    live_config,
    live_repo_root,
) -> None:
    """Maintain should archive low-value operational episodes."""
    expectation = load_maintain_expectation("routine_episode_archived")["expected"]
    outcome = run_maintain_case(
        case_name="routine_episode_archived",
        live_config=live_config,
        live_repo_root=live_repo_root,
        seed_records=[
            {
                "record_id": "rec_routine_episode",
                "kind": "episode",
                "title": "Morning sync and queue check",
                "body": (
                    "Confirmed the daemon was running, retried one sync, checked the queue, "
                    "and verified that everything was back to normal."
                ),
                "user_intent": "Check whether the background sync was healthy.",
                "what_happened": "Retried one sync and confirmed the queue drained normally.",
                "outcomes": "No lasting learning beyond routine confirmation.",
                "backdate_hours": 24,
            }
        ],
    )

    tool_names = outcome.tool_names
    assert set(tool_names).issubset(MAINTAIN_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    assert_no_legacy_tools(tool_names)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    episode = next(record for record in outcome.records if record["record_id"] == "rec_routine_episode")
    assert outcome.result.completion_summary.strip()
    assert episode["status"] == "archived"
    assert episode["valid_until"]
    assert len(episode["versions"]) >= 2
    latest_change_kinds = {str(version["change_kind"]) for version in episode["versions"][:2]}
    assert "archive" in latest_change_kinds

    assert_clean_context_schema(live_config.context_db_path)
    assert_quality_metrics(audit_context_db(live_config.context_db_path))

@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_maintain_verbose_episode_compressed(
    live_config,
    live_repo_root,
) -> None:
    """Maintain should rewrite a meaningful but verbose episode into a compact recap."""
    expectation = load_maintain_expectation("verbose_episode_compressed")["expected"]
    original_body = (
        "Reviewed the storage-boundary migration in detail, walked through the queue state path, compared "
        "two designs, wrote notes about why one was simpler, described temporary implementation concerns, "
        "and captured a long narrative of how the session moved from confusion to clarity before landing on "
        "the durable boundary that product context and queue-processing state should not share one persistence path."
    )
    outcome = run_maintain_case(
        case_name="verbose_episode_compressed",
        live_config=live_config,
        live_repo_root=live_repo_root,
        seed_records=[
            {
                "record_id": "rec_verbose_episode",
                "kind": "episode",
                "title": "Full storage-boundary review session",
                "body": original_body,
                "user_intent": "Review the storage-boundary migration and decide whether the split still makes sense.",
                "what_happened": "Compared the queue path, product context path, migration plan, and temporary implementation concerns in one long review.",
                "outcomes": "Ended with the same storage-boundary decision but kept too much session story.",
                "backdate_hours": 30,
            }
        ],
    )

    tool_names = outcome.tool_names
    assert set(tool_names).issubset(MAINTAIN_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    assert_no_legacy_tools(tool_names)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    episode = next(record for record in outcome.records if record["record_id"] == "rec_verbose_episode")
    assert outcome.result.completion_summary.strip()
    assert episode["status"] == "active"
    assert len(str(episode["body"])) < len(original_body)
    assert episode["title"] != "Full storage-boundary review session"
    assert episode["user_intent"] != "Review the storage-boundary migration and decide whether the split still makes sense."
    assert episode["what_happened"] != "Compared the queue path, product context path, migration plan, and temporary implementation concerns in one long review."
    assert episode["outcomes"] != "Ended with the same storage-boundary decision but kept too much session story."
    sentence_count = len([part for part in re.split(r"[.!?]+", str(episode["body"])) if part.strip()])
    assert sentence_count <= int(expectation["max_body_sentences"])
    episode_text = " ".join(
        str(episode.get(field) or "")
        for field in ("title", "body", "user_intent", "what_happened", "outcomes")
    ).lower()
    topic_hits = sum(1 for token in expectation["episode_text_must_include_any"] if token in episode_text)
    assert topic_hits >= 2
    for token in expectation["episode_text_must_not_include"]:
        assert token not in episode_text
    for field_name in ("body", "user_intent", "what_happened", "outcomes"):
        lowered = str(episode[field_name] or "").lower()
        assert "temporary" not in lowered
        assert "implementation" not in lowered
        assert "confusion to clarity" not in lowered

    latest_change_kinds = {str(version["change_kind"]) for version in episode["versions"][:2]}
    assert "update" in latest_change_kinds
    assert_clean_context_schema(live_config.context_db_path)
    assert_quality_metrics(audit_context_db(live_config.context_db_path))

@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_maintain_durable_record_rewritten_from_session_report_style(
    live_config,
    live_repo_root,
) -> None:
    """Maintain should rewrite session-report durable records into reusable memory form."""
    expectation = load_maintain_expectation("durable_record_rewritten_from_session_report_style")["expected"]
    outcome = run_maintain_case(
        case_name="durable_record_rewritten_from_session_report_style",
        live_config=live_config,
        live_repo_root=live_repo_root,
        seed_records=[
            {
                "record_id": "rec_provider_review",
                "kind": "decision",
                "title": "Review of provider normalization migration",
                "body": (
                    "Reviewed the provider normalization migration, discussed the adapter boundary, "
                    "compared a few approaches, and concluded that provider event normalization should "
                    "happen once at the adapter boundary so downstream code sees one stable contract."
                ),
                "decision": "Normalize provider events at the adapter boundary.",
                "why": "Downstream code should see one stable contract instead of provider-specific drift.",
                "backdate_hours": 36,
            }
        ],
    )

    tool_names = outcome.tool_names
    assert set(tool_names).issubset(MAINTAIN_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    assert_no_legacy_tools(tool_names)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    record = next(record for record in outcome.records if record["record_id"] == "rec_provider_review")
    assert outcome.result.completion_summary.strip()
    assert record["status"] == "active"
    latest_change_kinds = {str(version["change_kind"]) for version in record["versions"][:2]}
    assert "update" in latest_change_kinds

    record_text = _normalize_assertion_text(" ".join(
        str(record.get(field) or "")
        for field in ("title", "body", "decision", "why", "consequences")
    ))
    for token in expectation["record_text_must_include_all"]:
        assert _normalize_assertion_text(token) in record_text
    for token in expectation["record_text_must_not_include"]:
        assert _normalize_assertion_text(token) not in record_text

    assert_clean_context_schema(live_config.context_db_path)
    assert_quality_metrics(audit_context_db(live_config.context_db_path))

@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_maintain_valuable_recent_learning_preserved(
    live_config,
    live_repo_root,
) -> None:
    """Maintain should keep a fresh useful durable learning active even if it is a bit rough."""
    expectation = load_maintain_expectation("valuable_recent_learning_preserved")["expected"]
    outcome = run_maintain_case(
        case_name="valuable_recent_learning_preserved",
        live_config=live_config,
        live_repo_root=live_repo_root,
        seed_records=[
            {
                "record_id": "rec_recent_provider_learning",
                "kind": "fact",
                "title": "Provider normalization follow-up",
                "body": (
                    "Normalize provider event shapes at the adapter boundary so downstream systems can depend on one "
                    "stable contract. This learning is recent and still useful even if the wording can improve."
                ),
                "backdate_hours": 2,
            }
        ],
    )

    tool_names = outcome.tool_names
    assert set(tool_names).issubset(MAINTAIN_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    assert_no_legacy_tools(tool_names)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    record = next(record for record in outcome.records if record["record_id"] == "rec_recent_provider_learning")
    assert outcome.result.completion_summary.strip()
    assert record["status"] == "active"
    assert record["superseded_by_record_id"] in (None, "")
    record_text = _normalize_assertion_text(" ".join(str(record.get(field) or "") for field in ("title", "body")))
    for token in expectation["record_text_must_include_all"]:
        assert _normalize_assertion_text(token) in record_text
    latest_change_kinds = {str(version["change_kind"]) for version in record["versions"][:2]}
    assert "archive" not in latest_change_kinds
    assert "supersede" not in latest_change_kinds

    assert_clean_context_schema(live_config.context_db_path)
    assert_quality_metrics(audit_context_db(live_config.context_db_path))

@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_maintain_mixed_store_cleanup(
    live_config,
    live_repo_root,
) -> None:
    """Maintain should handle archive, supersede, and rewrite in one mixed cleanup pass."""
    expectation = load_maintain_expectation("mixed_store_cleanup")["expected"]
    outcome = run_maintain_case(
        case_name="mixed_store_cleanup",
        live_config=live_config,
        live_repo_root=live_repo_root,
        seed_records=[
            {
                "record_id": "rec_mixed_routine_episode",
                "kind": "episode",
                "title": "Queue heartbeat check",
                "body": "Checked the queue heartbeat, retried one worker, and confirmed the daemon was healthy again.",
                "user_intent": "Check whether the daemon recovered after a transient queue hiccup.",
                "what_happened": "Retried one worker and confirmed the queue heartbeat returned to normal.",
                "outcomes": "Routine confirmation only.",
                "backdate_hours": 36,
            },
            {
                "record_id": "rec_mixed_duplicate_weak",
                "kind": "decision",
                "title": "Separate runtime state paths",
                "body": "Keep product state and queue-processing state separate, but the wording is still weak.",
                "decision": "Separate product state from queue-processing state.",
                "why": "One storage path should not own both responsibilities.",
                "backdate_hours": 96,
            },
            {
                "record_id": "rec_mixed_duplicate_strong",
                "kind": "decision",
                "title": "Keep product state and queue-processing state separate",
                "body": "Product context and queue-processing state should stay in separate persistence paths because recovery and lifecycle needs differ.",
                "decision": "Keep product state and queue-processing state separate.",
                "why": "Recovery and lifecycle needs differ.",
                "backdate_hours": 48,
            },
            {
                "record_id": "rec_mixed_session_report",
                "kind": "decision",
                "title": "Task audit for provider normalization migration",
                "body": (
                    "Audited the provider normalization migration, stepped through the adapters, compared temporary "
                    "implementation detail, and concluded that provider event normalization should happen at the adapter boundary."
                ),
                "decision": "Normalize provider events at the adapter boundary.",
                "why": "Downstream code should depend on one stable contract.",
                "backdate_hours": 60,
            },
        ],
    )

    tool_names = outcome.tool_names
    assert set(tool_names).issubset(MAINTAIN_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    assert_no_legacy_tools(tool_names)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names

    routine_episode = next(record for record in outcome.records if record["record_id"] == "rec_mixed_routine_episode")
    weak_duplicate = next(record for record in outcome.records if record["record_id"] == "rec_mixed_duplicate_weak")
    strong_duplicate = next(record for record in outcome.records if record["record_id"] == "rec_mixed_duplicate_strong")
    rewritten = next(record for record in outcome.records if record["record_id"] == "rec_mixed_session_report")

    assert outcome.result.completion_summary.strip()
    assert routine_episode["status"] == "archived"
    assert weak_duplicate["superseded_by_record_id"] == strong_duplicate["record_id"]
    assert rewritten["status"] == "active"

    rewritten_text = _normalize_assertion_text(" ".join(
        str(rewritten.get(field) or "")
        for field in ("title", "body", "decision", "why")
    ))
    for token in expectation["rewritten_text_must_include_all"]:
        assert _normalize_assertion_text(token) in rewritten_text
    for token in expectation["rewritten_text_must_not_include"]:
        assert _normalize_assertion_text(token) not in rewritten_text

    changed_kinds = {str(row["change_kind"]) for row in outcome.changed_version_rows}
    for change_kind in expectation["required_change_kinds"]:
        assert change_kind in changed_kinds
    per_record_kinds: dict[str, set[str]] = {}
    for row in outcome.changed_version_rows:
        per_record_kinds.setdefault(str(row["record_id"]), set()).add(str(row["change_kind"]))
    for change_set in per_record_kinds.values():
        assert not ({"archive", "supersede"} <= change_set)

    assert_clean_context_schema(live_config.context_db_path)
    assert_quality_metrics(audit_context_db(live_config.context_db_path))


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_maintain_meaningful_episode_preserved_with_durable_neighbor(
    live_config,
    live_repo_root,
) -> None:
    """A meaningful episode should stay active even if a durable record covers the same topic."""
    expectation = load_maintain_expectation("meaningful_episode_preserved_with_durable_neighbor")["expected"]
    outcome = run_maintain_case(
        case_name="meaningful_episode_preserved_with_durable_neighbor",
        live_config=live_config,
        live_repo_root=live_repo_root,
        seed_records=[
            {
                "record_id": "rec_meaningful_episode",
                "kind": "episode",
                "title": "Restart recovery incident",
                "body": "Resolved a real restart recovery incident and clarified why persisted lease ownership matters during failover.",
                "user_intent": "Understand why restart recovery was failing.",
                "what_happened": "Traced the incident through restart, failover, and lease recovery behavior.",
                "outcomes": "Clarified the recovery story around persisted lease ownership.",
                "backdate_hours": 24,
            },
            {
                "record_id": "rec_neighbor_decision",
                "kind": "decision",
                "title": "Persist lease ownership in the queue row",
                "body": "Persist lease ownership in the queue row so restart and failover can recover worker ownership safely.",
                "decision": "Persist lease ownership in the queue row.",
                "why": "Restart and failover need one authoritative recovery source.",
                "backdate_hours": 24,
            },
        ],
    )

    tool_names = outcome.tool_names
    assert set(tool_names).issubset(MAINTAIN_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    assert_no_legacy_tools(tool_names)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    episode = next(record for record in outcome.records if record["record_id"] == "rec_meaningful_episode")
    assert episode["status"] == "active"
    assert episode["superseded_by_record_id"] in (None, "")
    if episode["record_id"] in {str(row["record_id"]) for row in outcome.changed_version_rows}:
        kinds = {str(row["change_kind"]) for row in outcome.changed_version_rows if str(row["record_id"]) == episode["record_id"]}
        assert kinds == {"update"}


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_maintain_concise_report_style_durable_rewritten(
    live_config,
    live_repo_root,
) -> None:
    """Even concise report-style durable wording should be rewritten into direct memory form."""
    expectation = load_maintain_expectation("concise_report_style_durable_rewritten")["expected"]
    outcome = run_maintain_case(
        case_name="concise_report_style_durable_rewritten",
        live_config=live_config,
        live_repo_root=live_repo_root,
        seed_records=[
            {
                "record_id": "rec_concise_report_decision",
                "kind": "decision",
                "title": "Compared queue lease storage options",
                "body": "Compared worker-memory and queue-row storage and chose the queue row.",
                "decision": "Use queue-row storage for worker ownership.",
                "why": "Queue-row state survives restart and failover.",
                "backdate_hours": 36,
            }
        ],
    )

    tool_names = outcome.tool_names
    assert set(tool_names).issubset(MAINTAIN_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    assert_no_legacy_tools(tool_names)
    for tool_name in expectation["must_use_tools"]:
        assert tool_name in tool_names
    for tool_name in expectation["must_not_use_tools"]:
        assert tool_name not in tool_names

    record = next(record for record in outcome.records if record["record_id"] == "rec_concise_report_decision")
    record_text = _normalize_assertion_text(" ".join(
        str(record.get(field) or "")
        for field in ("title", "body", "decision", "why")
    ))
    for token in expectation["record_text_must_include_all"]:
        assert _normalize_assertion_text(token) in record_text
    for token in expectation["record_text_must_not_include"]:
        assert _normalize_assertion_text(token) not in record_text
