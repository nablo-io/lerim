"""Shared runner helpers for targeted extract-agent integration cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from lerim.agents.extract import ExtractionResult, run_extraction
from lerim.config.providers import build_pydantic_model
from lerim.context import ContextStore, resolve_project_identity
from tests.conftest import EXTRACT_EXPECTATIONS_DIR, EXTRACT_TRACES_DIR
from tests.live_helpers import dump_messages, extract_tool_names


@dataclass
class ExtractCaseOutcome:
    """Observed result for one extract integration case."""

    result: ExtractionResult
    tool_names: list[str]
    rows: list[dict[str, Any]]
    records: list[dict[str, Any]]
    changed_version_rows: list[dict[str, Any]]
    changed_records: list[dict[str, Any]]
    project_id: str


def load_extract_expectation(case_name: str) -> dict[str, Any]:
    """Load one YAML expectation file for an extract case."""
    path = EXTRACT_EXPECTATIONS_DIR / f"{case_name}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _seed_session(
    store: ContextStore,
    *,
    project_id: str,
    session_id: str,
    repo_root: Path,
    trace_path: Path,
) -> None:
    """Insert the provenance row required before extract writes records."""
    store.upsert_session(
        project_id=project_id,
        session_id=session_id,
        agent_type="integration-extract",
        source_trace_ref=str(trace_path),
        repo_path=str(repo_root),
        cwd=str(repo_root),
        started_at=datetime.now(timezone.utc).isoformat(),
        model_name="integration-test",
        instructions_text=None,
        prompt_text=None,
        metadata={},
    )


def run_extract_case(
    *,
    case_name: str,
    live_config,
    live_repo_root: Path,
    seed_records: list[dict[str, Any]] | None = None,
) -> ExtractCaseOutcome:
    """Run one named extract case against a fresh isolated live test DB."""
    trace_path = EXTRACT_TRACES_DIR / f"{case_name}.jsonl"
    session_id = f"integration-extract-{case_name}"
    seed_session_id = f"integration-seed-{case_name}"
    run_folder = live_config.global_data_dir / "workspace" / "sync" / session_id
    run_folder.mkdir(parents=True, exist_ok=True)

    identity = resolve_project_identity(live_repo_root)
    store = ContextStore(live_config.context_db_path)
    store.initialize()
    store.register_project(identity)
    if seed_records:
        _seed_session(
            store,
            project_id=identity.project_id,
            session_id=seed_session_id,
            repo_root=live_repo_root,
            trace_path=trace_path,
        )
        for seed in seed_records:
            store.create_record(
                project_id=identity.project_id,
                session_id=seed_session_id,
                change_reason="integration_seed",
                **seed,
            )
    _seed_session(
        store,
        project_id=identity.project_id,
        session_id=session_id,
        repo_root=live_repo_root,
        trace_path=trace_path,
    )

    model = build_pydantic_model("agent", config=live_config)
    result, messages = run_extraction(
        context_db_path=live_config.context_db_path,
        project_identity=identity,
        session_id=session_id,
        trace_path=trace_path,
        model=model,
        run_folder=run_folder,
        return_messages=True,
    )

    rows = store.query(
        entity="records",
        mode="list",
        project_ids=[identity.project_id],
        source_session_id=session_id,
        order_by="created_at",
        limit=20,
        include_total=True,
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
                (session_id,),
            ).fetchall()
        ]
    changed_record_ids = list(dict.fromkeys(str(row["record_id"]) for row in version_rows))
    changed_records = [
        store.fetch_record(record_id, project_ids=[identity.project_id], include_versions=True)
        for record_id in changed_record_ids
    ]

    return ExtractCaseOutcome(
        result=result,
        tool_names=extract_tool_names(dump_messages(messages)),
        rows=rows,
        records=[record for record in records if record is not None],
        changed_version_rows=version_rows,
        changed_records=[record for record in changed_records if record is not None],
        project_id=identity.project_id,
    )
