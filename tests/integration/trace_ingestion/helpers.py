"""Shared runner helpers for targeted extract-agent integration cases."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from lerim.agents.trace_ingestion import TraceIngestionResult, run_trace_ingestion
from lerim.context import ContextStore, resolve_project_identity
from tests.conftest import TRACE_INGESTION_EXPECTATIONS_DIR, TRACE_INGESTION_TRACES_DIR
from tests.integration.common_helpers import (
    load_yaml_expectation,
    retry_on_overload,
    seed_session,
)


@dataclass
class ExtractCaseOutcome:
    """Observed result for one extract integration case."""

    result: TraceIngestionResult
    tool_names: list[str]
    tool_calls: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    records: list[dict[str, Any]]
    changed_version_rows: list[dict[str, Any]]
    changed_records: list[dict[str, Any]]
    project_id: str


def load_extract_expectation(case_name: str) -> dict[str, Any]:
    """Load one YAML expectation file for an extract case."""
    return load_yaml_expectation(TRACE_INGESTION_EXPECTATIONS_DIR, case_name)


def _build_very_long_window_trace(trace_path: Path) -> None:
    """Materialize one very long trace that can create real context pressure."""
    messages: list[dict[str, str]] = [
        {
            "role": "user",
            "content": (
                "Investigate the lease-handoff failures in the distributed worker. "
                "Keep only durable context. Do not store the long debugging story."
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
        "useful for this investigation but not durable context. "
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
                    "The lasting durable record is one state-boundary decision."
                ),
            },
        ]
    )

    trace_path.write_text(
        "\n".join(json.dumps(message, ensure_ascii=True) for message in messages) + "\n",
        encoding="utf-8",
    )


def _build_late_disambiguation_trace(trace_path: Path) -> None:
    """Materialize a long trace whose final chunk overturns an earlier lure."""
    messages: list[dict[str, str]] = [
        {
            "role": "user",
            "content": (
                "Figure out the durable lesson from this debugging session. "
                    "Keep only one durable record if there is one."
            ),
        },
        {
            "role": "assistant",
            "content": (
                "I will read the full trace before writing durable context because the early sections may be misleading."
            ),
        },
    ]
    for index in range(1, 151):
        if index % 25 == 0:
            filler = (
                "This chunk repeats noisy discussion about worker-local budget handling, helper renames, debug labels, "
                "and temporary metrics. It still sounds like the issue might be the worker-local budget theory, "
                "but the evidence is incomplete."
            )
        else:
            filler = (
                "This chunk repeats noisy discussion about worker-local counters, helper renames, debug labels, "
                "log formatting, temporary metrics, and backoff tuning. "
                "The evidence is still incomplete and these observations may be a distraction."
            )
        messages.append(
            {
                "role": "assistant",
                "content": (
                    f"Chunk {index}: {filler} "
                    "Several local comments mention retry budget, attempt count, and backoff tuning."
                ),
            }
        )
    messages.extend(
        [
            {
                "role": "user",
                "content": (
                    "Important clarification from the final investigation: the earlier budget theory was a distraction. "
                    "The real durable rule is that authoritative lease ownership must live in the persisted queue row."
                ),
            },
            {
                "role": "assistant",
                "content": (
                    "Understood. The durable boundary is queue-row lease ownership, not retry budget tuning."
                ),
            },
            {
                "role": "assistant",
                "content": (
                    "Everything before this point was noisy debugging context. "
                    "The lasting durable record is one lease-ownership rule for restart and failover recovery."
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
    static_path = TRACE_INGESTION_TRACES_DIR / f"{case_name}.jsonl"
    if static_path.exists():
        return static_path
    if case_name == "very_long_trace_uses_windows":
        generated = run_folder / f"{case_name}.jsonl"
        _build_very_long_window_trace(generated)
        return generated
    if case_name == "late_disambiguation_at_end_of_trace":
        generated = run_folder / f"{case_name}.jsonl"
        _build_late_disambiguation_trace(generated)
        return generated
    raise FileNotFoundError(f"no extract trace fixture found for case {case_name!r}")


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
    run_folder = live_config.global_data_dir / "workspace" / "ingest" / session_id
    run_folder.mkdir(parents=True, exist_ok=True)
    trace_path = _resolve_trace_path(case_name, run_folder)

    identity = resolve_project_identity(live_repo_root)
    store = ContextStore(live_config.context_db_path)
    store.initialize()
    store.register_project(identity)
    if seed_records:
        seed_session(
            store,
            project_id=identity.project_id,
            session_id=seed_session_id,
            repo_root=live_repo_root,
            agent_type="integration-extract",
            source_trace_ref=str(trace_path),
        )
        for seed in seed_records:
            store.create_record(
                project_id=identity.project_id,
                session_id=seed_session_id,
                change_reason="integration_seed",
                **seed,
            )
    seed_session(
        store,
        project_id=identity.project_id,
        session_id=session_id,
        repo_root=live_repo_root,
        agent_type="integration-extract",
        source_trace_ref=str(trace_path),
    )

    result, details = retry_on_overload(
        lambda: run_trace_ingestion(
            context_db_path=live_config.context_db_path,
            project_identity=identity,
            session_id=session_id,
            trace_path=trace_path,
            config=live_config,
            session_started_at="2026-01-01T00:00:00Z",
            return_details=True,
        )
    )

    rows = store.query(
        entity="records",
        mode="list",
        project_ids=[identity.project_id],
        source_session_id=session_id,
        order_by="created_at",
        limit=20,
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
                (session_id,),
            ).fetchall()
        ]
    changed_record_ids = list(dict.fromkeys(str(row["record_id"]) for row in version_rows))
    changed_records = [
        store.fetch_record(record_id, project_ids=[identity.project_id], include_versions=True)
        for record_id in changed_record_ids
    ]

    payload = [event.model_dump(mode="json") for event in details.events]
    return ExtractCaseOutcome(
        result=result,
        tool_names=[str(event.get("action") or "") for event in payload],
        tool_calls=payload,
        rows=rows,
        records=[record for record in records if record is not None],
        changed_version_rows=version_rows,
        changed_records=[record for record in changed_records if record is not None],
        project_id=identity.project_id,
    )
