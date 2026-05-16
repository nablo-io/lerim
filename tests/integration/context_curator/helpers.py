"""Shared runner helpers for targeted curate-agent integration cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from lerim.agents.context_curator import ContextCuratorResult, run_context_curator
from lerim.context import ContextStore, resolve_project_identity
from tests.conftest import CURATE_EXPECTATIONS_DIR
from tests.integration.common_helpers import (
    load_yaml_expectation,
    retry_on_overload,
    seed_session,
)


@dataclass
class CurateCaseOutcome:
    """Observed result for one curate integration case."""

    result: ContextCuratorResult
    event_names: list[str]
    events: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    records: list[dict[str, Any]]
    changed_version_rows: list[dict[str, Any]]
    changed_records: list[dict[str, Any]]
    project_id: str


def load_curate_expectation(case_name: str) -> dict[str, Any]:
    """Load one YAML expectation file for a curate case."""
    return load_yaml_expectation(CURATE_EXPECTATIONS_DIR, case_name)


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


def run_curate_case(
    *,
    case_name: str,
    live_config,
    live_repo_root: Path,
    seed_records: list[dict[str, Any]],
) -> CurateCaseOutcome:
    """Run one named curate case against a fresh isolated live test DB."""
    seed_session_id = f"integration-seed-{case_name}"
    curate_session_id = f"integration-curate-{case_name}"

    identity = resolve_project_identity(live_repo_root)
    store = ContextStore(live_config.context_db_path)
    store.initialize()
    store.register_project(identity)
    seed_session(
        store,
        project_id=identity.project_id,
        session_id=seed_session_id,
        repo_root=live_repo_root,
        agent_type="integration-curate",
        source_trace_ref="integration-curate",
    )
    seed_session(
        store,
        project_id=identity.project_id,
        session_id=curate_session_id,
        repo_root=live_repo_root,
        agent_type="integration-curate",
        source_trace_ref="integration-curate",
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

    result, details = retry_on_overload(
        lambda: run_context_curator(
            context_db_path=live_config.context_db_path,
            project_identity=identity,
            session_id=curate_session_id,
            config=live_config,
            return_details=True,
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
                (curate_session_id,),
            ).fetchall()
        ]
    changed_record_ids = list(dict.fromkeys(str(row["record_id"]) for row in version_rows))
    changed_records = [
        store.fetch_record(record_id, project_ids=[identity.project_id], include_versions=True)
        for record_id in changed_record_ids
    ]

    events = [event.model_dump(mode="json") for event in details.events]
    return CurateCaseOutcome(
        result=result,
        event_names=[str(event.get("action") or "") for event in events],
        events=events,
        rows=rows,
        records=[record for record in records if record is not None],
        changed_version_rows=version_rows,
        changed_records=[record for record in changed_records if record is not None],
        project_id=identity.project_id,
    )
