"""Tests for retryable submitted-trace manifests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lerim.traces.submissions import (
    SCHEMA_VERSION,
    create_submission_manifest,
    list_submission_manifests,
    load_submission_manifest,
    mark_submission_failed,
    retry_command_for,
    retry_submitted_trace,
    submission_manifest_path,
    submission_status_counts,
)
from tests.helpers import make_config


class _Scope:
    """Small scope double for importer result tests."""

    scope_type = "domain"
    scope_id = "scope_support"
    label = "Support"


class _Result:
    """Small importer result double matching the retry contract."""

    trace_id = "trace_retry"
    session_id = "sess-retry"
    normalized_trace_path = Path("/tmp/normalized.jsonl")
    scope_identity = _Scope()
    ingest_result = {
        "status": "ingested",
        "records_created": 2,
        "records_updated": 0,
        "records_archived": 0,
        "run_folder": "/tmp/run",
    }


def test_submission_manifest_records_retry_metadata(tmp_path: Path) -> None:
    """Submitted traces get a sidecar with enough metadata to retry."""
    trace_path = tmp_path / "workspace" / "mcp-submissions" / "trace.json"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text('{"messages":[]}', encoding="utf-8")

    manifest = create_submission_manifest(
        trace_path=trace_path,
        source_name="support-agent",
        source_profile="support",
        scope_type="domain",
        scope="support-ops",
        scope_label="Support Ops",
        session_id="sess-1",
        filename_hint="trace",
        force=False,
    )

    manifest_path = submission_manifest_path(trace_path)
    assert manifest["manifest_path"] == str(manifest_path)
    assert manifest_path.is_file()
    loaded = load_submission_manifest(trace_path)
    assert loaded["status"] == "submitted"
    assert loaded["source_name"] == "support-agent"
    assert loaded["scope"] == "support-ops"
    assert loaded["retry_command"] == retry_command_for(trace_path)


def test_mark_failed_and_list_submissions(tmp_path: Path) -> None:
    """Failed submissions are observable through the manifest listing."""
    root = tmp_path / ".lerim"
    trace_path = root / "workspace" / "mcp-submissions" / "2026" / "trace.json"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text('{"messages":[]}', encoding="utf-8")
    create_submission_manifest(
        trace_path=trace_path,
        source_name="support-agent",
        source_profile="support",
        scope_type="domain",
        scope="support-ops",
        scope_label=None,
        session_id="sess-1",
        filename_hint=None,
        force=False,
    )

    mark_submission_failed(trace_path=trace_path, exc=RuntimeError("model unavailable"))

    rows = list_submission_manifests(root=root, status="failed", limit=10)
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["attempt_count"] == 1
    assert rows[0]["last_error"] == {
        "type": "RuntimeError",
        "message": "model unavailable",
    }
    assert submission_status_counts(rows) == {"failed": 1}


def test_retry_submitted_trace_uses_saved_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry imports the original submitted trace with saved scope metadata."""
    cfg = make_config(tmp_path / ".lerim")
    trace_path = cfg.global_data_dir / "workspace" / "mcp-submissions" / "trace.json"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text('{"messages":[{"role":"user","content":"hello"}]}', encoding="utf-8")
    create_submission_manifest(
        trace_path=trace_path,
        source_name="support-agent",
        source_profile="support",
        scope_type="domain",
        scope="support-ops",
        scope_label="Support Ops",
        session_id="sess-retry",
        filename_hint=None,
        force=False,
    )
    captured: dict[str, Any] = {}

    def _fake_import_trace_file(**kwargs: Any) -> _Result:
        captured.update(kwargs)
        return _Result()

    monkeypatch.setattr(
        "lerim.traces.submissions.import_trace_file",
        _fake_import_trace_file,
    )

    result = retry_submitted_trace(trace_path, force=True, config=cfg)

    assert result.trace_id == "trace_retry"
    assert captured["trace_path"] == trace_path
    assert captured["source_name"] == "support-agent"
    assert captured["source_profile"] == "support"
    assert captured["scope_type"] == "domain"
    assert captured["scope"] == "support-ops"
    assert captured["scope_label"] == "Support Ops"
    assert captured["session_id"] == "sess-retry"
    assert captured["force"] is True
    manifest = load_submission_manifest(trace_path)
    assert manifest["status"] == "ingested"
    assert manifest["attempt_count"] == 1
    assert manifest["last_result"]["records_created"] == 2


def test_retry_submitted_trace_records_failed_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed retry increments the manifest and preserves the error."""
    cfg = make_config(tmp_path / ".lerim")
    trace_path = cfg.global_data_dir / "workspace" / "mcp-submissions" / "trace.json"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text("plain transcript", encoding="utf-8")
    create_submission_manifest(
        trace_path=trace_path,
        source_name="agent",
        source_profile="generic",
        scope_type="domain",
        scope="ops",
        scope_label=None,
        session_id=None,
        filename_hint=None,
        force=False,
    )

    def _raise_import_error(**_kwargs: Any) -> _Result:
        raise ValueError("bad trace")

    monkeypatch.setattr(
        "lerim.traces.submissions.import_trace_file",
        _raise_import_error,
    )

    with pytest.raises(ValueError, match="bad trace"):
        retry_submitted_trace(trace_path, config=cfg)

    manifest = load_submission_manifest(trace_path)
    assert manifest["status"] == "failed"
    assert manifest["attempt_count"] == 1
    assert manifest["last_error"] == {"type": "ValueError", "message": "bad trace"}


def test_load_submission_manifest_rejects_bad_json(tmp_path: Path) -> None:
    """Malformed sidecars fail explicitly instead of being silently retried."""
    trace_path = tmp_path / "trace.json"
    trace_path.write_text("{}", encoding="utf-8")
    submission_manifest_path(trace_path).write_text("{bad", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid submission manifest"):
        load_submission_manifest(trace_path)


def test_manifest_json_is_ascii_and_stable(tmp_path: Path) -> None:
    """Manifest files are readable JSON for external inspection."""
    trace_path = tmp_path / "trace.json"
    trace_path.write_text("{}", encoding="utf-8")
    create_submission_manifest(
        trace_path=trace_path,
        source_name="agent",
        source_profile="generic",
        scope_type="domain",
        scope="ops",
        scope_label=None,
        session_id=None,
        filename_hint=None,
        force=False,
    )

    raw = submission_manifest_path(trace_path).read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert json.loads(raw)["schema_version"] == SCHEMA_VERSION


def test_load_submission_manifest_rejects_a_pre_trajectory_manifest(tmp_path: Path) -> None:
    """A schema-1 manifest points at a trace written in the retired cache shape.

    Retrying it would resubmit records in the old {"type","message"} format, so
    the mismatch has to be a loud error telling the user to resubmit the trace.
    """
    trace_path = tmp_path / "trace.json"
    trace_path.write_text("{}", encoding="utf-8")
    submission_manifest_path(trace_path).write_text(
        json.dumps({"schema_version": 1, "trace_path": str(trace_path)}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported submission manifest schema: 1"):
        load_submission_manifest(trace_path)
