"""Shared runner helpers for targeted extract-agent integration cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
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


def _build_very_long_prune_trace(trace_path: Path) -> None:
    """Materialize one very long trace that can create real context pressure."""
    messages: list[dict[str, str]] = [
        {
            "role": "user",
            "content": (
                "Investigate the lease-handoff failures in the distributed worker. "
                "Keep only durable memory. Do not store the long debugging story."
            ),
        },
        {
            "role": "assistant",
            "content": (
                "I will trace the failure across the worker, queue row, and recovery path, "
                "then keep only the durable rule."
            ),
        },
    ]

    filler = (
        "I inspected another noisy segment of the lease handoff path. "
        "This chunk includes temporary debug labels, helper rename ideas, repeated metric wording, "
        "snapshot formatting concerns, command-output comparisons, and local cleanup notes that are "
        "useful for this investigation but not durable memory. "
        "The implementation noise keeps repeating across worker restarts, queue polling, heartbeats, "
        "timeout handling, and recovery logging, so I am tracking only what could matter later."
    )
    filler = f"{filler} {filler}"

    for index in range(1, 431):
        messages.append(
            {
                "role": "assistant",
                "content": (
                    f"Chunk {index}: {filler} "
                    f"Segment {index} compares worker-local lease state with persisted queue-row state "
                    f"while repeating local observations about log wording, helper naming, and trace noise."
                ),
            }
        )
        if index in {100, 200, 300, 400}:
            messages.append(
                {
                    "role": "assistant",
                    "content": (
                        f"Checkpoint {index}: still mostly implementation detail. "
                        "The one durable boundary is unchanged: authoritative lease ownership must live in the "
                        "persisted queue row, not only in worker memory, so restart and failover can recover it."
                    ),
                }
            )

    messages.extend(
        [
            {
                "role": "user",
                "content": (
                    "Important rule: authoritative lease ownership must live in the persisted queue row, "
                    "not only in worker memory. Restarts and failover must recover ownership from persisted state."
                ),
            },
            {
                "role": "assistant",
                "content": (
                    "Understood. That is the durable decision. The repeated log-cleanup and helper-rename details are not."
                ),
            },
            {
                "role": "assistant",
                "content": (
                    "I checked more recovery paths. Heartbeats, renewals, and takeover logic all depend on the same boundary: "
                    "authoritative lease ownership must be persisted in the queue row so another worker can recover it safely."
                ),
            },
            {
                "role": "assistant",
                "content": (
                    "Everything else in this trace is investigation noise or local cleanup. "
                    "The lasting memory is one state-boundary decision."
                ),
            },
        ]
    )

    trace_path.write_text(
        "\n".join(json.dumps(message, ensure_ascii=True) for message in messages) + "\n",
        encoding="utf-8",
    )


def _resolve_trace_path(case_name: str, run_folder: Path) -> Path:
    """Return the case trace path, materializing generated traces when needed."""
    static_path = EXTRACT_TRACES_DIR / f"{case_name}.jsonl"
    if static_path.exists():
        return static_path
    if case_name == "very_long_trace_requires_prune":
        generated = run_folder / f"{case_name}.jsonl"
        _build_very_long_prune_trace(generated)
        return generated
    raise FileNotFoundError(f"no extract trace fixture found for case {case_name!r}")


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
    session_id = f"integration-extract-{case_name}"
    seed_session_id = f"integration-seed-{case_name}"
    run_folder = live_config.global_data_dir / "workspace" / "sync" / session_id
    run_folder.mkdir(parents=True, exist_ok=True)
    trace_path = _resolve_trace_path(case_name, run_folder)

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
