"""Shared runner helpers for targeted maintain-agent integration cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from lerim.agents.maintain import MaintainResult, run_maintain
from lerim.config.providers import build_pydantic_model
from lerim.context import ContextStore, resolve_project_identity
from tests.conftest import MAINTAIN_EXPECTATIONS_DIR
from tests.integration.common_helpers import (
    extract_tool_calls,
    load_yaml_expectation,
    retry_on_overload,
    seed_session,
)
from tests.live_helpers import dump_messages, extract_tool_names


@dataclass
class MaintainCaseOutcome:
    """Observed result for one maintain integration case."""

    result: MaintainResult
    tool_names: list[str]
    tool_calls: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    records: list[dict[str, Any]]
    changed_version_rows: list[dict[str, Any]]
    changed_records: list[dict[str, Any]]
    project_id: str


def load_maintain_expectation(case_name: str) -> dict[str, Any]:
    """Load one YAML expectation file for a maintain case."""
    return load_yaml_expectation(MAINTAIN_EXPECTATIONS_DIR, case_name)


def _apply_time_shift(
    store: ContextStore,
    *,
    record_id: str,
    session_id: str,
    delta: timedelta,
) -> None:
    """Backdate one seeded record and its first version for deterministic age-based cases."""
    with store.connect() as conn:
        row = conn.execute(
            """
            SELECT created_at, updated_at, valid_from
            FROM records
            WHERE record_id = ?
            """,
            (record_id,),
        ).fetchone()
        if row is None:
            return
        created_at = datetime.fromisoformat(str(row["created_at"]))
        updated_at = datetime.fromisoformat(str(row["updated_at"]))
        valid_from = datetime.fromisoformat(str(row["valid_from"]))
        shifted_created = (created_at - delta).isoformat()
        shifted_updated = (updated_at - delta).isoformat()
        shifted_valid_from = (valid_from - delta).isoformat()
        conn.execute(
            """
            UPDATE records
            SET created_at = ?, updated_at = ?, valid_from = ?
            WHERE record_id = ?
            """,
            (shifted_created, shifted_updated, shifted_valid_from, record_id),
        )
        conn.execute(
            """
            UPDATE record_versions
            SET changed_at = ?
            WHERE record_id = ? AND changed_by_session_id = ?
            """,
            (shifted_created, record_id, session_id),
        )


def run_maintain_case(
    *,
    case_name: str,
    live_config,
    live_repo_root: Path,
    seed_records: list[dict[str, Any]],
) -> MaintainCaseOutcome:
    """Run one named maintain case against a fresh isolated live test DB."""
    seed_session_id = f"integration-seed-{case_name}"
    maintain_session_id = f"integration-maintain-{case_name}"

    identity = resolve_project_identity(live_repo_root)
    store = ContextStore(live_config.context_db_path)
    store.initialize()
    store.register_project(identity)
    seed_session(
        store,
        project_id=identity.project_id,
        session_id=seed_session_id,
        repo_root=live_repo_root,
        agent_type="integration-maintain",
        source_trace_ref="integration-maintain",
    )
    seed_session(
        store,
        project_id=identity.project_id,
        session_id=maintain_session_id,
        repo_root=live_repo_root,
        agent_type="integration-maintain",
        source_trace_ref="integration-maintain",
    )

    for seed in seed_records:
        seed_payload = dict(seed)
        backdate_hours = int(seed_payload.pop("backdate_hours", 0) or 0)
        created = store.create_record(
            project_id=identity.project_id,
            session_id=seed_session_id,
            change_reason="integration_seed",
            **seed_payload,
        )
        if backdate_hours > 0 and created.get("record_id"):
            _apply_time_shift(
                store,
                record_id=str(created["record_id"]),
                session_id=seed_session_id,
                delta=timedelta(hours=backdate_hours),
            )

    model = build_pydantic_model("agent", config=live_config)
    result, messages = retry_on_overload(
        lambda: run_maintain(
            context_db_path=live_config.context_db_path,
            project_identity=identity,
            session_id=maintain_session_id,
            model=model,
            return_messages=True,
        )
    )

    rows = store.query(
        entity="records",
        mode="list",
        project_ids=[identity.project_id],
        order_by="updated_at",
        limit=50,
        include_total=True,
        include_archived=True,
    )["rows"]
    records = [
        store.fetch_record(str(row["record_id"]), project_ids=[identity.project_id], include_versions=True)
        for row in rows
    ]
    with store.connect() as conn:
        version_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM record_versions
                WHERE changed_by_session_id = ?
                ORDER BY changed_at ASC, version_no ASC
                """,
                (maintain_session_id,),
            ).fetchall()
        ]
    changed_record_ids = list(dict.fromkeys(str(row["record_id"]) for row in version_rows))
    changed_records = [
        store.fetch_record(record_id, project_ids=[identity.project_id], include_versions=True)
        for record_id in changed_record_ids
    ]

    payload = dump_messages(messages)
    return MaintainCaseOutcome(
        result=result,
        tool_names=extract_tool_names(payload),
        tool_calls=extract_tool_calls(payload),
        rows=rows,
        records=[record for record in records if record is not None],
        changed_version_rows=version_rows,
        changed_records=[record for record in changed_records if record is not None],
        project_id=identity.project_id,
    )
