"""Shared runner helpers for targeted answer-agent integration cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from lerim.agents.context_answerer import ContextAnswerResult, run_context_answerer
from lerim.context import ContextStore, resolve_project_identity
from tests.conftest import ANSWER_EXPECTATIONS_DIR
from tests.integration.common_helpers import (
    extract_tool_calls,
    extract_tool_returns,
    load_yaml_expectation,
    retry_on_overload,
    seed_session,
)
from tests.live_helpers import dump_messages, extract_tool_names


@dataclass
class AnswerCaseOutcome:
    """Observed result for one answer integration case."""

    result: ContextAnswerResult
    tool_names: list[str]
    tool_calls: list[dict[str, Any]]
    tool_returns: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    project_id: str


def load_answer_expectation(case_name: str) -> dict[str, Any]:
    """Load one YAML expectation file for an answer case."""
    return load_yaml_expectation(ANSWER_EXPECTATIONS_DIR, case_name)


def _apply_seed_timestamps(
    store: ContextStore,
    *,
    record_id: str,
    created_at: str | None,
    updated_at: str | None,
) -> None:
    """Backfill explicit record timestamps for time-sensitive integration cases."""
    if not created_at and not updated_at:
        return
    with store.connect() as conn:
        if created_at and updated_at:
            conn.execute(
                """
                UPDATE records
                SET created_at = ?, updated_at = ?
                WHERE record_id = ?
                """,
                (created_at, updated_at, record_id),
            )
            conn.execute(
                """
                UPDATE record_versions
                SET created_at = ?, updated_at = ?, changed_at = ?
                WHERE record_id = ?
                """,
                (created_at, updated_at, updated_at, record_id),
            )
            return
        if created_at:
            conn.execute(
                "UPDATE records SET created_at = ? WHERE record_id = ?",
                (created_at, record_id),
            )
            conn.execute(
                "UPDATE record_versions SET created_at = ?, changed_at = ? WHERE record_id = ?",
                (created_at, created_at, record_id),
            )
        if updated_at:
            conn.execute(
                "UPDATE records SET updated_at = ? WHERE record_id = ?",
                (updated_at, record_id),
            )
            conn.execute(
                "UPDATE record_versions SET updated_at = ?, changed_at = ? WHERE record_id = ?",
                (updated_at, updated_at, record_id),
            )


def _resolve_relative_timestamp(raw: str | None) -> str | None:
    """Resolve simple UTC-relative timestamp placeholders used by live answer fixtures."""
    if not raw:
        return raw
    value = str(raw).strip()
    if not value.startswith("{{"):
        return value

    today = datetime.now(timezone.utc).date()
    replacements = {
        "{{today}}": today.isoformat(),
        "{{yesterday}}": (today - timedelta(days=1)).isoformat(),
        "{{days_ago:2}}": (today - timedelta(days=2)).isoformat(),
        "{{days_ago:3}}": (today - timedelta(days=3)).isoformat(),
        "{{days_ago:4}}": (today - timedelta(days=4)).isoformat(),
        "{{days_ago:5}}": (today - timedelta(days=5)).isoformat(),
    }
    for marker, date_text in replacements.items():
        if marker in value:
            return value.replace(marker, date_text)
    return value


def run_answer_case(
    *,
    case_name: str,
    live_config,
    live_repo_root: Path,
) -> AnswerCaseOutcome:
    """Run one named answer case against a fresh isolated live test DB."""
    case = load_answer_expectation(case_name)
    session_id = f"integration-answer-{case_name}"

    identity = resolve_project_identity(live_repo_root)
    store = ContextStore(live_config.context_db_path)
    store.initialize()
    store.register_project(identity)

    seeded_sessions: set[str] = set()
    for index, seed in enumerate(case.get("seed_records", []), start=1):
        seed_session_id = str(seed.get("session_id") or f"{case_name}-seed-{index}")
        if seed_session_id not in seeded_sessions:
            seed_session(
                store,
                project_id=identity.project_id,
                session_id=seed_session_id,
                repo_root=live_repo_root,
                agent_type="integration-answer",
                source_trace_ref=f"answer-seed:{case_name}:{index}",
            )
            seeded_sessions.add(seed_session_id)
        record = store.create_record(
            project_id=identity.project_id,
            session_id=seed_session_id,
            kind=str(seed["kind"]),
            title=str(seed["title"]),
            body=str(seed["body"]),
            status=str(seed.get("status") or "active"),
            valid_from=_resolve_relative_timestamp(str(seed.get("valid_from") or "").strip()) or None,
            valid_until=_resolve_relative_timestamp(str(seed.get("valid_until") or "").strip()) or None,
            superseded_by_record_id=str(seed.get("superseded_by_record_id") or "").strip() or None,
            decision=str(seed.get("decision") or "").strip() or None,
            why=str(seed.get("why") or "").strip() or None,
            alternatives=str(seed.get("alternatives") or "").strip() or None,
            consequences=str(seed.get("consequences") or "").strip() or None,
            user_intent=str(seed.get("user_intent") or "").strip() or None,
            what_happened=str(seed.get("what_happened") or "").strip() or None,
            outcomes=str(seed.get("outcomes") or "").strip() or None,
            change_reason="integration_seed",
            record_id=str(seed.get("record_id") or "").strip() or None,
        )
        _apply_seed_timestamps(
            store,
            record_id=str(record["record_id"]),
            created_at=_resolve_relative_timestamp(str(seed.get("created_at") or "").strip()) or None,
            updated_at=_resolve_relative_timestamp(str(seed.get("updated_at") or "").strip()) or None,
        )

    result, messages = retry_on_overload(
        lambda: run_context_answerer(
            context_db_path=live_config.context_db_path,
            project_identity=identity,
            project_ids=[identity.project_id],
            session_id=session_id,
            config=live_config,
            question=str(case["question"]),
            hints=str(case.get("hints") or "").strip(),
            return_messages=True,
        )
    )
    payload = dump_messages(messages)
    return AnswerCaseOutcome(
        result=result,
        tool_names=extract_tool_names(payload),
        tool_calls=extract_tool_calls(payload),
        tool_returns=extract_tool_returns(payload),
        messages=payload,
        project_id=identity.project_id,
    )
