"""Integration tests for custom trace-folder ingestion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lerim.config.settings import reload_config
from lerim.server import daemon
from lerim.server.api import api_project_add, api_project_list
from lerim.sessions import catalog
from tests.helpers import write_test_config


def _canonical(role: str, content: str, timestamp: str) -> str:
    """Return one Lerim canonical JSONL row."""
    return json.dumps(
        {
            "type": role,
            "message": {"role": role, "content": content},
            "timestamp": timestamp,
        }
    )


def _write_trace(path: Path, rows: list[str]) -> None:
    """Write one synthetic cleaned trace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


@pytest.mark.integration
def test_custom_project_folder_indexes_and_queues_clean_traces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Register a custom trace folder and verify Lerim uses its clean traces."""
    config_path = write_test_config(
        tmp_path,
        server={"ingest_window_days": 30, "ingest_max_sessions": 10},
    )
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    reload_config()

    trace_root = tmp_path / "support-traces"
    trace_root.mkdir()
    first_trace = trace_root / "renewals" / "run-001.jsonl"
    second_trace = trace_root / "incidents" / "run-002.jsonl"
    invalid_trace = trace_root / "raw-agent-export.jsonl"
    _write_trace(
        first_trace,
        [
            _canonical(
                "user",
                "Customer asks whether renewal needs legal approval.",
                "2026-05-16T08:00:00Z",
            ),
            _canonical(
                "assistant",
                "Agent records that legal approval is required above EUR 500.",
                "2026-05-16T08:02:00Z",
            ),
        ],
    )
    _write_trace(
        second_trace,
        [
            _canonical(
                "user",
                "Operations agent investigates delayed export paperwork.",
                "2026-05-16T09:00:00Z",
            ),
            _canonical(
                "assistant",
                "Agent keeps the customs ticket open until carrier confirmation.",
                "2026-05-16T09:04:00Z",
            ),
        ],
    )
    invalid_trace.write_text(
        '{"role":"assistant","content":"raw vendor shape"}\n',
        encoding="utf-8",
    )
    runtime_calls: list[dict[str, Any]] = []

    class FakeRuntime:
        def __init__(
            self,
            default_cwd: str | None = None,
            config: Any | None = None,
        ) -> None:
            self.default_cwd = default_cwd
            self.config = config

        def ingest(self, session_path: Path, **kwargs: Any) -> dict[str, Any]:
            runtime_calls.append(
                {
                    "default_cwd": self.default_cwd,
                    "session_path": str(session_path),
                    **kwargs,
                }
            )
            return {
                "records_created": 1,
                "records_updated": 0,
                "records_archived": 0,
                "cost_usd": 0.0,
            }

    monkeypatch.setattr(daemon, "LerimRuntime", FakeRuntime)

    added = api_project_add(str(trace_root), project_type="custom")
    assert "error" not in added
    assert added["type"] == "custom"

    projects = api_project_list()
    assert projects == [
        {
            "name": "support-traces",
            "project_id": added["project_id"],
            "type": "custom",
            "exists": True,
            "path": str(trace_root.resolve()),
        }
    ]

    code, summary = daemon.run_ingest_once(
        run_id=None,
        agent_filter=["custom"],
        no_extract=False,
        force=False,
        max_sessions=10,
        dry_run=False,
        ignore_lock=True,
        trigger="integration",
    )

    assert code == daemon.EXIT_OK
    assert summary.indexed_sessions == 2
    assert summary.skipped_sessions == 0
    assert summary.extracted_sessions == 2
    assert summary.failed_sessions == 0
    assert len(summary.run_ids) == 2
    assert all(run_id.startswith("custom_") for run_id in summary.run_ids)

    docs = [catalog.fetch_session_doc(run_id) for run_id in summary.run_ids]
    assert all(doc is not None for doc in docs)
    indexed_paths = {str(doc["session_path"]) for doc in docs if doc}
    assert indexed_paths == {str(first_trace.resolve()), str(second_trace.resolve())}
    assert str(invalid_trace.resolve()) not in indexed_paths

    for doc in docs:
        assert doc is not None
        assert doc["agent_type"] == "custom"
        assert doc["repo_path"] == str(trace_root.resolve())
        assert "/cache/traces/" not in str(doc["session_path"])
        assert "/workspace/imports/" not in str(doc["session_path"])

    jobs = catalog.list_session_jobs(status=catalog.JOB_STATUS_DONE)
    assert len(jobs) == 2
    for job in jobs:
        assert job["agent_type"] == "custom"
        assert job["repo_path"] == str(trace_root.resolve())
        assert job["session_path"] in indexed_paths

    assert len(runtime_calls) == 2
    for call in runtime_calls:
        assert call["default_cwd"] == str(trace_root.resolve())
        assert call["agent_type"] == "custom"
        assert call["session_path"] in indexed_paths
        assert call["session_meta"]["cwd"] == str(trace_root.resolve())
