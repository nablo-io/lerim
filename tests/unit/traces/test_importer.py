"""Tests for host-only generic trace imports."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lerim.traces.importer import import_trace_file
from tests.helpers import make_config


class _FakeRuntime:
    """Runtime double that captures imported ingest calls."""

    calls: list[dict[str, Any]] = []

    def __init__(self, *, config) -> None:
        self.config = config

    def ingest_imported_trace(self, trace_path: Path, **kwargs: Any) -> dict[str, Any]:
        """Capture the imported trace extraction call."""
        self.calls.append({"trace_path": trace_path, **kwargs})
        return {
            "trace_path": str(trace_path),
            "context_db_path": str(self.config.context_db_path),
            "project_id": None,
            "scope_type": kwargs["scope_identity"].scope_type,
            "scope_id": kwargs["scope_identity"].scope_id,
            "scope_label": kwargs["scope_identity"].label,
            "workspace_root": str(self.config.global_data_dir / "workspace"),
            "run_folder": str(self.config.global_data_dir / "workspace" / "ingest-run"),
            "artifacts": {},
            "records_created": 1,
            "records_updated": 0,
            "records_archived": 0,
            "cost_usd": 0.0,
        }


def test_import_trace_file_normalizes_and_calls_runtime(tmp_path, monkeypatch):
    """Importer writes a compact trace and extracts it through scoped runtime."""
    _FakeRuntime.calls = []
    monkeypatch.setattr("lerim.traces.importer.LerimRuntime", _FakeRuntime)
    trace_path = tmp_path / "raw.jsonl"
    trace_path.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")
    cfg = make_config(tmp_path / ".lerim")

    result = import_trace_file(
        trace_path=trace_path,
        source_name="support-bot",
        source_profile="support",
        scope_type="domain",
        scope="support",
        config=cfg,
    )

    assert result.normalized_trace_path.is_file()
    assert result.scope_identity.scope_type == "domain"
    assert result.ingest_result["records_created"] == 1
    assert _FakeRuntime.calls[0]["source_name"] == "support-bot"
