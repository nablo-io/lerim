"""Targeted real-LLM integration cases for the curate agent."""

from __future__ import annotations


import pytest

from tests.integration.context_curator.helpers import load_curate_expectation, run_curate_case
from tests.live_helpers import (
    FRAMEWORK_TOOL_NAMES,
    CONTEXT_CURATOR_EVENT_NAMES,
    assert_clean_context_schema,
    assert_quality_metrics,
    audit_context_db,
    connect_context_db,
)


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_curate_duplicate_durable_records_superseded(
    live_config,
    live_repo_root,
) -> None:
    """Curate should keep the stronger duplicate active and supersede the weaker one."""
    expectation = load_curate_expectation("duplicate_durable_records_superseded")["expected"]
    outcome = run_curate_case(
        case_name="duplicate_durable_records_superseded",
        live_config=live_config,
        live_repo_root=live_repo_root,
        seed_records=[
            {
                "record_id": "rec_storage_boundary_weak",
                "kind": "decision",
                "title": "Separate runtime state paths",
                "body": (
                    "Keep product-facing state and queue-processing state separate. "
                    "This is the same storage boundary but written too vaguely."
                ),
                "decision": "Separate product state from queue-processing state.",
                "why": "One path should not own both responsibilities.",
                "backdate_hours": 72,
            },
            {
                "record_id": "rec_storage_boundary_strong",
                "kind": "decision",
                "title": "Keep product state and queue-processing state separate",
                "body": (
                    "Product context and queue-processing state should stay in separate persistence paths. "
                    "Why: lifecycle and recovery requirements differ."
                ),
                "decision": "Keep product state and queue-processing state separate.",
                "why": "Lifecycle and recovery requirements differ.",
                "backdate_hours": 48,
            },
        ],
    )

    event_names = outcome.event_names
    assert set(event_names).issubset(CONTEXT_CURATOR_EVENT_NAMES | FRAMEWORK_TOOL_NAMES)
    for event_name in expectation["must_use_events"]:
        assert event_name in event_names
    for event_name in expectation["must_not_use_events"]:
        assert event_name not in event_names

    assert outcome.result.completion_summary.strip()
    weak = next(record for record in outcome.records if record["record_id"] == "rec_storage_boundary_weak")
    strong = next(record for record in outcome.records if record["record_id"] == "rec_storage_boundary_strong")
    changed_record_ids = {str(record["record_id"]) for record in outcome.changed_records}
    assert weak["record_id"] in changed_record_ids
    assert weak["superseded_by_record_id"] == strong["record_id"]
    assert weak["valid_until"]
    assert strong["status"] == "active"

    weak_change_kinds = {str(version["change_kind"]) for version in weak["versions"]}
    assert "supersede" in weak_change_kinds

    with connect_context_db(live_config.context_db_path) as conn:
        archive_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM record_versions WHERE changed_by_session_id = ? AND change_kind = 'archive'",
                ("integration-curate-duplicate_durable_records_superseded",),
            ).fetchone()[0]
        )

    assert archive_count == 0
    assert_clean_context_schema(live_config.context_db_path)
    assert_quality_metrics(audit_context_db(live_config.context_db_path))

@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_curate_fresh_duplicate_should_not_be_archived(
    live_config,
    live_repo_root,
) -> None:
    """Curate should supersede fresh duplicate durable records instead of archiving them."""
    expectation = load_curate_expectation("fresh_duplicate_should_not_be_archived")["expected"]
    outcome = run_curate_case(
        case_name="fresh_duplicate_should_not_be_archived",
        live_config=live_config,
        live_repo_root=live_repo_root,
        seed_records=[
            {
                "record_id": "rec_retry_budget_weak",
                "kind": "decision",
                "title": "Keep retry state in persisted metadata",
                "body": (
                    "Store retry state in persisted metadata so work can resume. "
                    "This is the same durable rule but written weakly."
                ),
                "decision": "Keep retry state in persisted metadata.",
                "why": "Workers need a shared recovery point.",
            },
            {
                "record_id": "rec_retry_budget_strong",
                "kind": "decision",
                "title": "Persist retry budget in job metadata",
                "body": (
                    "Persist retry budget in job metadata so retries survive restarts and stay visible to all workers."
                ),
                "decision": "Persist retry budget in job metadata.",
                "why": "Retries must survive restarts and stay visible to all workers.",
            },
        ],
    )

    event_names = outcome.event_names
    assert set(event_names).issubset(CONTEXT_CURATOR_EVENT_NAMES | FRAMEWORK_TOOL_NAMES)
    for event_name in expectation["must_use_events"]:
        assert event_name in event_names
    for event_name in expectation["must_not_use_events"]:
        assert event_name not in event_names

    weak = next(record for record in outcome.records if record["record_id"] == "rec_retry_budget_weak")
    strong = next(record for record in outcome.records if record["record_id"] == "rec_retry_budget_strong")
    assert outcome.result.completion_summary.strip()
    assert weak["superseded_by_record_id"] == strong["record_id"]
    assert weak["status"] == "active"
    assert strong["status"] == "active"
    latest_change_kinds = {str(version["change_kind"]) for version in weak["versions"][:2]}
    assert "supersede" in latest_change_kinds

    with connect_context_db(live_config.context_db_path) as conn:
        archive_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM record_versions WHERE changed_by_session_id = ? AND change_kind = 'archive'",
                ("integration-curate-fresh_duplicate_should_not_be_archived",),
            ).fetchone()[0]
        )

    assert archive_count == 0
    assert_clean_context_schema(live_config.context_db_path)
    assert_quality_metrics(audit_context_db(live_config.context_db_path))

@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_curate_obsolete_fact_replaced_by_new_truth(
    live_config,
    live_repo_root,
) -> None:
    """Curate should supersede older truth when a newer active fact replaces it."""
    expectation = load_curate_expectation("obsolete_fact_replaced_by_new_truth")["expected"]
    outcome = run_curate_case(
        case_name="obsolete_fact_replaced_by_new_truth",
        live_config=live_config,
        live_repo_root=live_repo_root,
        seed_records=[
            {
                "record_id": "rec_old_lease_truth",
                "kind": "fact",
                "title": "Worker memory owns lease state",
                "body": (
                    "Lease ownership lives in worker memory. Recovery relies on the worker keeping the latest state."
                ),
                "backdate_hours": 144,
            },
            {
                "record_id": "rec_new_lease_truth",
                "kind": "fact",
                "title": "Persist lease ownership in the queue row",
                "body": (
                    "Authoritative lease ownership must live in the persisted queue row so recovery and failover "
                    "can reconstruct ownership safely."
                ),
                "backdate_hours": 24,
            },
        ],
    )

    event_names = outcome.event_names
    assert set(event_names).issubset(CONTEXT_CURATOR_EVENT_NAMES | FRAMEWORK_TOOL_NAMES)
    for event_name in expectation["must_use_events"]:
        assert event_name in event_names
    for event_name in expectation["must_not_use_events"]:
        assert event_name not in event_names

    old_record = next(record for record in outcome.records if record["record_id"] == "rec_old_lease_truth")
    new_record = next(record for record in outcome.records if record["record_id"] == "rec_new_lease_truth")
    assert outcome.result.completion_summary.strip()
    assert old_record["superseded_by_record_id"] == new_record["record_id"]
    assert old_record["valid_until"]
    latest_change_kinds = {str(version["change_kind"]) for version in old_record["versions"][:2]}
    assert "supersede" in latest_change_kinds

    with connect_context_db(live_config.context_db_path) as conn:
        archive_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM record_versions WHERE changed_by_session_id = ? AND change_kind = 'archive'",
                ("integration-curate-obsolete_fact_replaced_by_new_truth",),
            ).fetchone()[0]
        )

    assert archive_count == 0
    assert_clean_context_schema(live_config.context_db_path)
    assert_quality_metrics(audit_context_db(live_config.context_db_path))


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_curate_old_capability_gap_superseded_by_newer_support(
    live_config,
    live_repo_root,
) -> None:
    """Curate should retire older missing-capability claims when newer records contradict them."""
    expectation = load_curate_expectation("old_capability_gap_superseded_by_newer_support")["expected"]
    outcome = run_curate_case(
        case_name="old_capability_gap_superseded_by_newer_support",
        live_config=live_config,
        live_repo_root=live_repo_root,
        seed_records=[
            {
                "record_id": "rec_old_replay_gap",
                "kind": "fact",
                "title": "Batch replay lacks persistent recovery support",
                "body": (
                    "The batch replay path does not yet persist enough recovery state, so restart recovery "
                    "cannot safely resume interrupted work."
                ),
                "backdate_hours": 168,
            },
            {
                "record_id": "rec_new_replay_support",
                "kind": "fact",
                "title": "Batch replay persists recovery state",
                "body": (
                    "The batch replay path persists recovery state so interrupted work can resume safely "
                    "after restart or failover."
                ),
                "backdate_hours": 12,
            },
        ],
    )

    event_names = outcome.event_names
    assert set(event_names).issubset(CONTEXT_CURATOR_EVENT_NAMES | FRAMEWORK_TOOL_NAMES)
    for event_name in expectation["must_use_events"]:
        assert event_name in event_names
    for event_name in expectation["must_not_use_events"]:
        assert event_name not in event_names

    old_record = next(record for record in outcome.records if record["record_id"] == "rec_old_replay_gap")
    new_record = next(record for record in outcome.records if record["record_id"] == "rec_new_replay_support")
    assert outcome.result.completion_summary.strip()
    assert old_record["superseded_by_record_id"] == new_record["record_id"]
    assert old_record["valid_until"]
    assert new_record["status"] == "active"
    assert new_record["superseded_by_record_id"] in (None, "")

    latest_change_kinds = {str(version["change_kind"]) for version in old_record["versions"][:2]}
    assert "supersede" in latest_change_kinds
    assert_clean_context_schema(live_config.context_db_path)
    assert_quality_metrics(audit_context_db(live_config.context_db_path))

@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_curate_no_change_when_store_is_already_clean(
    live_config,
    live_repo_root,
) -> None:
    """Curate should leave a healthy active store unchanged."""
    expectation = load_curate_expectation("no_change_when_store_is_already_clean")["expected"]
    outcome = run_curate_case(
        case_name="no_change_when_store_is_already_clean",
        live_config=live_config,
        live_repo_root=live_repo_root,
        seed_records=[
            {
                "record_id": "rec_clean_decision",
                "kind": "decision",
                "title": "Keep context and session state in separate stores",
                "body": (
                    "Durable context and session-operational state should stay in separate stores. "
                    "Why: lifecycle, recovery, and query patterns differ."
                ),
                "decision": "Keep context and session state in separate stores.",
                "why": "Lifecycle, recovery, and query patterns differ.",
                "backdate_hours": 48,
            },
            {
                "record_id": "rec_clean_fact",
                "kind": "fact",
                "title": "Normalize provider events at the adapter boundary",
                "body": (
                    "Normalize provider event shapes at the adapter boundary so downstream systems can rely on one "
                    "stable contract across providers."
                ),
                "backdate_hours": 24,
            },
        ],
    )

    event_names = outcome.event_names
    assert set(event_names).issubset(CONTEXT_CURATOR_EVENT_NAMES | FRAMEWORK_TOOL_NAMES)
    for event_name in expectation["must_use_events"]:
        assert event_name in event_names
    for event_name in expectation["must_not_use_events"]:
        assert event_name not in event_names

    assert outcome.result.completion_summary.strip()
    assert outcome.changed_version_rows == []
    assert outcome.changed_records == []

    titles = {str(record["title"]) for record in outcome.records}
    for title in expectation["preserved_titles"]:
        assert title in titles

    clean_decision = next(record for record in outcome.records if record["record_id"] == "rec_clean_decision")
    clean_fact = next(record for record in outcome.records if record["record_id"] == "rec_clean_fact")
    assert clean_decision["status"] == "active"
    assert clean_fact["status"] == "active"
    assert clean_decision["superseded_by_record_id"] in (None, "")
    assert clean_fact["superseded_by_record_id"] in (None, "")
    assert len(clean_decision["versions"]) == 1
    assert len(clean_fact["versions"]) == 1

    assert_clean_context_schema(live_config.context_db_path)
    assert_quality_metrics(audit_context_db(live_config.context_db_path))


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_curate_healthy_fresh_record_is_true_noop(
    live_config,
    live_repo_root,
) -> None:
    """A fresh healthy durable record should not be rewritten just for style."""
    expectation = load_curate_expectation("healthy_fresh_record_is_true_noop")["expected"]
    outcome = run_curate_case(
        case_name="healthy_fresh_record_is_true_noop",
        live_config=live_config,
        live_repo_root=live_repo_root,
        seed_records=[
            {
                "record_id": "rec_fresh_clean_decision",
                "kind": "decision",
                "title": "Persist lease ownership in the queue row",
                "body": "Persist lease ownership in the queue row so restart and failover can recover worker ownership safely.",
                "decision": "Persist lease ownership in the queue row.",
                "why": "Restart and failover need one authoritative recovery source.",
            }
        ],
    )

    event_names = outcome.event_names
    assert set(event_names).issubset(CONTEXT_CURATOR_EVENT_NAMES | FRAMEWORK_TOOL_NAMES)
    for event_name in expectation["must_use_events"]:
        assert event_name in event_names
    for event_name in expectation["must_not_use_events"]:
        assert event_name not in event_names
    record = next(record for record in outcome.records if record["record_id"] == "rec_fresh_clean_decision")
    assert outcome.changed_version_rows == []
    assert len(record["versions"]) == 1
    assert record["status"] == "active"
    assert record["superseded_by_record_id"] in (None, "")


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_curate_obsolete_low_value_durable_archived(
    live_config,
    live_repo_root,
) -> None:
    """An old low-value non-episode durable row can be archived directly."""
    expectation = load_curate_expectation("obsolete_low_value_durable_archived")["expected"]
    outcome = run_curate_case(
        case_name="obsolete_low_value_durable_archived",
        live_config=live_config,
        live_repo_root=live_repo_root,
        seed_records=[
            {
                "record_id": "rec_obsolete_reference",
                "kind": "reference",
                "title": "Temporary rollout scratchpad",
                "body": "Temporary scratchpad for one rollout audit that no longer serves as a source of truth.",
                "backdate_hours": 240,
            },
            {
                "record_id": "rec_healthy_neighbor",
                "kind": "fact",
                "title": "Queue workers use bounded retry backoff",
                "body": "Queue workers use bounded retry backoff to keep failures observable and prevent runaway pressure.",
                "backdate_hours": 24,
            },
        ],
    )

    event_names = outcome.event_names
    assert set(event_names).issubset(CONTEXT_CURATOR_EVENT_NAMES | FRAMEWORK_TOOL_NAMES)
    for event_name in expectation["must_use_events"]:
        assert event_name in event_names
    for event_name in expectation["must_not_use_events"]:
        assert event_name not in event_names

    record = next(record for record in outcome.records if record["record_id"] == "rec_obsolete_reference")
    assert record["status"] == "archived"
    assert record["valid_until"]
    assert record["superseded_by_record_id"] in (None, "")


@pytest.mark.integration
@pytest.mark.llm
@pytest.mark.agent
def test_curate_semantic_duplicate_found_via_search(
    live_config,
    live_repo_root,
) -> None:
    """Hidden semantic duplicates should be found via search and resolved with supersede."""
    expectation = load_curate_expectation("semantic_duplicate_found_via_search")["expected"]
    seed_records = [
        {
            "record_id": "rec_retry_handoff_weak",
            "kind": "decision",
            "title": "Keep retry handoff restart-safe",
            "body": "Retry handoff should remain restart-safe across workers.",
            "decision": "Keep retry handoff restart-safe.",
            "why": "Workers should recover retry state after restart.",
        },
        {
            "record_id": "rec_retry_budget_strong",
            "kind": "decision",
            "title": "Persist retry budget in job metadata",
            "body": "Persist retry budget in job metadata so restarts and failover preserve retry state across workers.",
            "decision": "Persist retry budget in job metadata.",
            "why": "Retries must survive restart and stay visible to all workers.",
            "backdate_hours": 48,
        },
    ]
    distractor_facts = [
        (
            "rec_distractor_contract",
            "Normalize webhook payloads before fan-out",
            "Webhook fan-out should normalize provider payloads into one canonical event contract before downstream delivery.",
        ),
        (
            "rec_distractor_metrics",
            "Emit queue lag metrics from the scheduler",
            "Queue lag metrics should be emitted by the scheduler so backlog alerts come from one authoritative source.",
        ),
        (
            "rec_distractor_tokens",
            "Cache token introspection for short-lived retries",
            "Token introspection results may be cached briefly to avoid repeated auth round-trips during burst retries.",
        ),
        (
            "rec_distractor_exports",
            "Export snapshots must use immutable object keys",
            "Snapshot exports should use immutable object-store keys so replays never overwrite earlier artifacts.",
        ),
        (
            "rec_distractor_rate_limit",
            "Rate-limit state belongs in the gateway cache",
            "Gateway-owned rate-limit state should stay in the gateway cache instead of application worker memory.",
        ),
        (
            "rec_distractor_audit",
            "Audit events need a stable actor identifier",
            "Audit events should store a stable actor identifier so later compliance review can reconcile event streams.",
        ),
    ]
    for index, (record_id, title, body) in enumerate(distractor_facts, start=1):
        seed_records.append(
            {
                "record_id": record_id,
                "kind": "fact",
                "title": title,
                "body": body,
                "backdate_hours": 12 + index,
            }
        )
    outcome = run_curate_case(
        case_name="semantic_duplicate_found_via_search",
        live_config=live_config,
        live_repo_root=live_repo_root,
        seed_records=seed_records,
    )

    event_names = outcome.event_names
    assert set(event_names).issubset(CONTEXT_CURATOR_EVENT_NAMES | FRAMEWORK_TOOL_NAMES)
    for event_name in expectation["must_use_events"]:
        assert event_name in event_names
    for event_name in expectation["must_not_use_events"]:
        assert event_name not in event_names

    weak = next(record for record in outcome.records if record["record_id"] == "rec_retry_handoff_weak")
    assert weak["superseded_by_record_id"] == "rec_retry_budget_strong"
    changed_record_ids = {str(row["record_id"]) for row in outcome.changed_version_rows}
    assert changed_record_ids == {"rec_retry_handoff_weak"}

    change_sets: dict[str, set[str]] = {}
    for row in outcome.changed_version_rows:
        change_sets.setdefault(str(row["record_id"]), set()).add(str(row["change_kind"]))
    for change_set in change_sets.values():
        assert "archive" not in change_set
