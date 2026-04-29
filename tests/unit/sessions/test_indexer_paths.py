"""Test indexer platform paths for session discovery across adapters."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from lerim.config.settings import reload_config
from lerim.sessions import catalog
from lerim.sessions.catalog import (
    fetch_session_doc,
    get_indexed_run_ids,
    index_session_for_fts,
    update_session_extract_fields,
)
from tests.helpers import write_test_config


class _FakeAdapter:
    @staticmethod
    def iter_sessions(traces_dir: Path, start=None, end=None, known_run_ids=None):
        _ = (traces_dir, start, end, known_run_ids)
        return [
            SimpleNamespace(
                run_id="run-x",
                agent_type="codex",
                session_path="/tmp/run-x.jsonl",
                start_time="2026-02-14T00:00:00+00:00",
                repo_path=None,
                repo_name="repo-x",
                status="completed",
                duration_ms=100,
                message_count=2,
                tool_call_count=1,
                error_count=0,
                total_tokens=42,
                summaries=["implemented fix"],
                content_hash="hash-new",
            )
        ]

class _FakeCursorAdapter(_FakeAdapter):
    @staticmethod
    def iter_sessions(traces_dir: Path, start=None, end=None, known_run_ids=None):
        _ = (traces_dir, start, end, known_run_ids)
        return [
            SimpleNamespace(
                run_id="run-cursor-1",
                agent_type="cursor",
                session_path="/tmp/run-cursor-1.jsonl",
                start_time="2026-02-14T00:00:00+00:00",
                repo_path=None,
                repo_name="repo-x",
                status="completed",
                duration_ms=100,
                message_count=2,
                tool_call_count=1,
                error_count=0,
                total_tokens=42,
                summaries=["implemented fix"],
                content_hash="hash-cursor",
            )
        ]


def test_index_new_sessions_uses_connected_paths(monkeypatch, tmp_path: Path) -> None:
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    reload_config()

    monkeypatch.setattr(
        catalog.adapter_registry,
        "get_connected_platform_paths",
        lambda _p: {"codex": Path("/tmp")},
    )
    monkeypatch.setattr(
        catalog.adapter_registry, "get_connected_agents", lambda _p: ["codex"]
    )
    monkeypatch.setattr(
        catalog.adapter_registry, "get_adapter", lambda _name: _FakeAdapter
    )

    out = catalog.index_new_sessions(return_details=True)
    assert len(out) == 1
    assert out[0].run_id == "run-x"


def test_index_new_sessions_cursor_path_ingestion(monkeypatch, tmp_path: Path) -> None:
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    reload_config()

    monkeypatch.setattr(
        catalog.adapter_registry,
        "get_connected_platform_paths",
        lambda _p: {"cursor": Path("/tmp")},
    )
    monkeypatch.setattr(
        catalog.adapter_registry, "get_connected_agents", lambda _p: ["cursor"]
    )
    monkeypatch.setattr(
        catalog.adapter_registry, "get_adapter", lambda _name: _FakeCursorAdapter
    )

    out = catalog.index_new_sessions(return_details=True)
    assert len(out) == 1
    assert out[0].run_id == "run-cursor-1"


def test_index_new_sessions_marks_changed_when_known_hash_differs(
    monkeypatch, tmp_path: Path
) -> None:
    """index_new_sessions sets changed=True when a known session hash changes."""
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    reload_config()

    # Pre-seed an existing session so its ID is already known
    index_session_for_fts(
        run_id="run-x",
        agent_type="codex",
        content="old content",
        content_hash="hash-old",
    )

    # Catalog scans known sessions so it can compare hashes and refresh rows.
    # The changed flag should only be true when an indexed hash differs.
    class _FakeAdapterNoSkip:
        @staticmethod
        def iter_sessions(traces_dir, start=None, end=None, known_run_ids=None):
            return [
                SimpleNamespace(
                    run_id="run-x",
                    agent_type="codex",
                    session_path="/tmp/run-x.jsonl",
                    start_time="2026-02-14T00:00:00+00:00",
                    repo_path=None,
                    repo_name="repo-x",
                    status="completed",
                    duration_ms=100,
                    message_count=2,
                    tool_call_count=1,
                    error_count=0,
                    total_tokens=42,
                    summaries=["implemented fix"],
                    content_hash="hash-new",
                )
            ]

    monkeypatch.setattr(
        catalog.adapter_registry,
        "get_connected_platform_paths",
        lambda _p: {"codex": Path("/tmp")},
    )
    monkeypatch.setattr(
        catalog.adapter_registry, "get_connected_agents", lambda _p: ["codex"]
    )
    monkeypatch.setattr(
        catalog.adapter_registry, "get_adapter", lambda _name: _FakeAdapterNoSkip
    )

    out = catalog.index_new_sessions(return_details=True)
    assert len(out) == 1
    assert out[0].run_id == "run-x"
    assert out[0].changed is True

    # Verify the run_id is tracked
    ids = get_indexed_run_ids()
    assert "run-x" in ids


def test_index_new_sessions_skips_known_unchanged_rows(
    monkeypatch, tmp_path: Path
) -> None:
    """Known unchanged sessions do not churn index rows when hashes match."""
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    reload_config()

    index_session_for_fts(
        run_id="run-refresh",
        agent_type="codex",
        content="old content",
        summaries='["old summary"]',
        content_hash="same-hash",
    )
    update_session_extract_fields(
        "run-refresh",
        summary_text="extracted durable summary",
        tags='["durable","tag"]',
        outcome="achieved",
    )
    observed_known_args: list[set[str] | None] = []

    class _FakeAdapterRefresh:
        @staticmethod
        def iter_sessions(traces_dir, start=None, end=None, known_run_ids=None):
            _ = (traces_dir, start, end)
            observed_known_args.append(known_run_ids)
            return [
                SimpleNamespace(
                    run_id="run-refresh",
                    agent_type="codex",
                    session_path="/tmp/run-refresh.jsonl",
                    start_time="2026-02-14T00:00:00+00:00",
                    repo_path=None,
                    repo_name="repo-refresh",
                    status="completed",
                    duration_ms=100,
                    message_count=2,
                    tool_call_count=1,
                    error_count=0,
                    total_tokens=42,
                    summaries=["new summary"],
                    content_hash="same-hash",
                )
            ]

    monkeypatch.setattr(
        catalog.adapter_registry,
        "get_connected_platform_paths",
        lambda _p: {"codex": Path("/tmp")},
    )
    monkeypatch.setattr(
        catalog.adapter_registry, "get_connected_agents", lambda _p: ["codex"]
    )
    monkeypatch.setattr(
        catalog.adapter_registry, "get_adapter", lambda _name: _FakeAdapterRefresh
    )

    out = catalog.index_new_sessions(return_details=True)

    assert observed_known_args == [None]
    assert out == []
    doc = fetch_session_doc("run-refresh")
    assert doc is not None
    assert doc["summary_text"] == "extracted durable summary"
    assert doc["tags"] == '["durable","tag"]'
    assert doc["outcome"] == "achieved"
    assert doc["content_hash"] == "same-hash"


def test_index_new_sessions_marks_new_as_not_changed(
    monkeypatch, tmp_path: Path
) -> None:
    """index_new_sessions sets changed=False for brand-new sessions."""
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    reload_config()

    monkeypatch.setattr(
        catalog.adapter_registry,
        "get_connected_platform_paths",
        lambda _p: {"codex": Path("/tmp")},
    )
    monkeypatch.setattr(
        catalog.adapter_registry, "get_connected_agents", lambda _p: ["codex"]
    )
    monkeypatch.setattr(
        catalog.adapter_registry, "get_adapter", lambda _name: _FakeAdapter
    )

    out = catalog.index_new_sessions(return_details=True)
    assert len(out) == 1
    assert out[0].run_id == "run-x"
    assert out[0].changed is False
