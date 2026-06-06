"""Unit tests for api.py functions: detect_agents, write_init_config,
api_ingest, api_curate, api_health, api_status, api_project_*,
api_retry_all_dead_letter, api_skip_all_dead_letter, looks_like_auth_error,
and Docker runtime exports.

Focuses on functions testable without Docker/Ollama by mocking the runtime,
filesystem, and subprocess calls.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3
import subprocess
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import lerim.server.api as api_mod
import lerim.server.docker_runtime as docker_mod
import lerim.server.skill_api as skill_api_mod
import lerim.sessions.catalog as catalog_mod
from lerim.adapters.registry import KNOWN_PLATFORMS
from lerim.context import ContextStore, resolve_project_identity
from lerim.server.api import (
    api_connect,
    api_connect_list,
    api_health,
    api_curate,
    api_memory_reset,
    api_project_add,
    api_project_list,
    api_project_remove,
    api_record_filters,
    api_retry_all_dead_letter,
    api_skip_all_dead_letter,
    api_status,
    api_ingest,
    detect_agents,
    looks_like_auth_error,
    write_init_config,
)
from lerim.server.docker_runtime import docker_available
from lerim.server.daemon import IngestSummary
from tests.helpers import make_config


@pytest.fixture
def mock_embeddings(monkeypatch):
    provider = MagicMock()
    provider.embedding_dims = 384
    provider.model_id = "test-model"
    provider.embed_document.return_value = [0.1] * 384
    provider.embed_query.return_value = [0.1] * 384
    monkeypatch.setattr("lerim.context.store.get_embedding_provider", lambda: provider)
    monkeypatch.setattr(
        "lerim.context.embedding.get_embedding_provider", lambda: provider
    )


def _stub_status_catalog(monkeypatch) -> None:
    """Stub queue/catalog helpers so unit tests stay local."""
    monkeypatch.setattr(
        api_mod,
        "queue_health_snapshot",
        lambda: {
            "degraded": False,
            "stale_running_count": 0,
            "dead_letter_count": 0,
            "oldest_running_age_seconds": None,
            "oldest_dead_letter_age_seconds": None,
            "advice": "",
        },
    )
    monkeypatch.setattr(
        api_mod, "count_unscoped_sessions_by_agent", lambda projects: {}
    )
    monkeypatch.setattr(api_mod, "list_session_jobs", lambda **kwargs: [])
    monkeypatch.setattr(api_mod, "list_service_runs", lambda **kwargs: [])


# ---------------------------------------------------------------------------
# api_health
# ---------------------------------------------------------------------------


def test_api_health_returns_ok() -> None:
    """api_health returns status ok with version."""
    result = api_health()
    assert result["status"] == "ok"
    assert "version" in result


# ---------------------------------------------------------------------------
# detect_agents
# ---------------------------------------------------------------------------


def test_detect_agents_returns_all_known() -> None:
    """detect_agents returns entries for all known agent default paths."""
    agents = detect_agents()
    for name in KNOWN_PLATFORMS:
        assert name in agents
        assert "path" in agents[name]
        assert "exists" in agents[name]
        assert isinstance(agents[name]["exists"], bool)


def test_detect_agents_path_expanded() -> None:
    """detect_agents expands ~ in paths."""
    agents = detect_agents()
    for name, info in agents.items():
        assert "~" not in info["path"], f"{name} path not expanded"


# ---------------------------------------------------------------------------
# write_init_config
# ---------------------------------------------------------------------------


def test_write_init_config_saves_agents(monkeypatch, tmp_path) -> None:
    """write_init_config calls save_config_patch with agents dict."""
    saved: list[dict] = []
    monkeypatch.setattr(api_mod, "save_config_patch", lambda patch: saved.append(patch))
    monkeypatch.setattr(
        api_mod, "get_user_config_path", lambda: tmp_path / "config.toml"
    )

    selected = {"claude": "/home/user/.claude/projects", "codex": "/home/user/.codex"}
    write_init_config(selected)

    assert len(saved) == 1
    assert saved[0] == {"agents": selected}


def test_write_init_config_returns_path(monkeypatch, tmp_path) -> None:
    """write_init_config returns the USER_CONFIG_PATH."""
    expected_path = tmp_path / "config.toml"
    monkeypatch.setattr(api_mod, "save_config_patch", lambda patch: None)
    monkeypatch.setattr(api_mod, "get_user_config_path", lambda: expected_path)

    result = write_init_config({"claude": "/path"})
    assert result == expected_path


# ---------------------------------------------------------------------------
# looks_like_auth_error
# ---------------------------------------------------------------------------


def test_looks_like_auth_error_positive_cases() -> None:
    """looks_like_auth_error returns True for known auth error strings."""
    assert looks_like_auth_error("failed to authenticate with provider")
    assert looks_like_auth_error("authentication_error occurred")
    assert looks_like_auth_error("OAuth token has expired")
    assert looks_like_auth_error("Invalid API key provided")
    assert looks_like_auth_error("401 Unauthorized access")


def test_looks_like_auth_error_negative_cases() -> None:
    """looks_like_auth_error returns False for normal responses."""
    assert not looks_like_auth_error("Memory saved successfully")
    assert not looks_like_auth_error("3 records extracted")
    assert not looks_like_auth_error("")
    assert not looks_like_auth_error(None)


# ---------------------------------------------------------------------------
# docker_available
# ---------------------------------------------------------------------------


def test_docker_available_true(monkeypatch) -> None:
    """docker_available returns True when docker info succeeds."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: MagicMock(returncode=0),
    )
    assert docker_available() is True


def test_docker_available_false_nonzero(monkeypatch) -> None:
    """docker_available returns False when docker info fails."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: MagicMock(returncode=1),
    )
    assert docker_available() is False


def test_docker_available_not_installed(monkeypatch) -> None:
    """docker_available returns False when docker binary not found."""

    def raise_fnf(*args, **kwargs):
        """Simulate missing docker binary."""
        raise FileNotFoundError("docker not found")

    monkeypatch.setattr(subprocess, "run", raise_fnf)
    assert docker_available() is False


def test_docker_available_timeout(monkeypatch) -> None:
    """docker_available returns False when docker info times out."""

    def raise_timeout(*args, **kwargs):
        """Simulate docker info timeout."""
        raise subprocess.TimeoutExpired(cmd="docker info", timeout=10)

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    assert docker_available() is False


# ---------------------------------------------------------------------------
# api_ingest
# ---------------------------------------------------------------------------


def test_api_ingest_returns_code_and_summary(monkeypatch, tmp_path) -> None:
    """api_ingest calls run_ingest_once and returns code + summary dict."""
    cfg = make_config(tmp_path)
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

    summary = IngestSummary(
        indexed_sessions=2,
        extracted_sessions=1,
        skipped_sessions=0,
        failed_sessions=0,
        run_ids=["r1"],
        cost_usd=0.005,
    )
    monkeypatch.setattr(api_mod, "run_ingest_once", lambda **kw: (0, summary))
    monkeypatch.setattr(api_mod, "ollama_lifecycle", _noop_lifecycle)
    _stub_status_catalog(monkeypatch)

    result = api_ingest(agent="claude", window="7d")

    assert result["code"] == 0
    assert result["extracted_sessions"] == 1


def test_api_ingest_dry_run(monkeypatch, tmp_path) -> None:
    """api_ingest passes dry_run flag through to run_ingest_once."""
    cfg = make_config(tmp_path)
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

    captured_kwargs: dict[str, Any] = {}

    def fake_ingest(**kwargs):
        """Capture ingest arguments."""
        captured_kwargs.update(kwargs)
        return (0, IngestSummary(0, 0, 0, 0, []))

    monkeypatch.setattr(api_mod, "run_ingest_once", fake_ingest)
    monkeypatch.setattr(api_mod, "ollama_lifecycle", _noop_lifecycle)
    _stub_status_catalog(monkeypatch)

    api_ingest(dry_run=True)

    assert captured_kwargs["dry_run"] is True


def test_api_ingest_force_flag(monkeypatch, tmp_path) -> None:
    """api_ingest passes force flag through to run_ingest_once."""
    cfg = make_config(tmp_path)
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

    captured_kwargs: dict[str, Any] = {}

    def fake_ingest(**kwargs):
        """Capture ingest arguments."""
        captured_kwargs.update(kwargs)
        return (0, IngestSummary(0, 0, 0, 0, []))

    monkeypatch.setattr(api_mod, "run_ingest_once", fake_ingest)
    monkeypatch.setattr(api_mod, "ollama_lifecycle", _noop_lifecycle)
    _stub_status_catalog(monkeypatch)

    api_ingest(force=True)

    assert captured_kwargs["force"] is True


def test_api_answer_includes_debug_when_verbose(monkeypatch, tmp_path) -> None:
    """api_answer should pass verbose through to runtime answer and expose debug payload."""
    cfg = make_config(tmp_path)
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(api_mod, "_resolve_selected_projects", lambda **kw: [])

    class _FakeRuntime:
        def answer(
            self, question, project_ids=None, repo_root=None, include_debug=False
        ):
            assert question == "how many records"
            assert include_debug is True
            return (
                "3 records",
                "sid-1",
                0.0,
                {"retrieval_actions": [{"action_type": "count", "result_count": 3}]},
            )

    monkeypatch.setattr(api_mod, "LerimRuntime", lambda: _FakeRuntime())

    payload = api_mod.api_answer("how many records", verbose=True)
    assert payload["answer"] == "3 records"
    assert payload["debug"]["retrieval_actions"][0]["action_type"] == "count"


def test_api_query_empty_project_selection_returns_empty_scope(
    monkeypatch,
    tmp_path,
    mock_embeddings,
) -> None:
    """api_query preserves empty project selections instead of querying unscoped."""
    cfg = make_config(tmp_path)
    project_root = tmp_path / "removed-project"
    project_root.mkdir()
    identity = resolve_project_identity(project_root)
    store = ContextStore(cfg.context_db_path)
    store.initialize()
    store.register_project(identity)
    store.upsert_session(
        project_id=identity.project_id,
        session_id="sess_removed",
        agent_type="test",
        source_trace_ref="test.jsonl",
        repo_path=str(project_root),
        cwd=str(project_root),
        started_at="2026-01-01T00:00:00Z",
        model_name="test-model",
        instructions_text=None,
        prompt_text=None,
    )
    store.create_record(
        project_id=identity.project_id,
        session_id="sess_removed",
        kind="decision",
        title="Removed project record",
        body="This should not leak into empty project selections.",
        decision="Keep removed project rows scoped out.",
        why="Empty project selections must not become unscoped queries.",
    )
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

    payload = api_mod.api_query(entity="records", mode="count", scope="project")

    assert payload["error"] is False
    assert payload["projects_used"] == []
    assert payload["scope"] == "project"
    assert payload["count"] == 0


def test_api_query_status_all_includes_active_and_archived_records(
    monkeypatch,
    tmp_path,
    mock_embeddings,
) -> None:
    """api_query treats the all-status sentinel as no status filter."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    cfg = replace(make_config(tmp_path), projects={"project": str(project_root)})
    identity = resolve_project_identity(project_root)
    store = ContextStore(cfg.context_db_path)
    store.initialize()
    store.register_project(identity)
    store.create_record(
        project_id=identity.project_id,
        session_id=None,
        kind="fact",
        title="Active record",
        body="Active records should appear in all-status queries.",
    )
    store.create_record(
        project_id=identity.project_id,
        session_id=None,
        kind="fact",
        title="Archived record",
        body="Archived records should appear in all-status queries.",
        status="archived",
    )
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

    all_payload = api_mod.api_query(
        entity="records",
        mode="count",
        scope="project",
        project="project",
        status="all",
    )
    active_payload = api_mod.api_query(
        entity="records",
        mode="count",
        scope="project",
        project="project",
        status="active",
    )
    archived_payload = api_mod.api_query(
        entity="records",
        mode="count",
        scope="project",
        project="project",
        status="archived",
    )

    assert all_payload["count"] == 2
    assert active_payload["count"] == 1
    assert archived_payload["count"] == 1


def test_api_query_versions_does_not_receive_record_archival_filter(
    monkeypatch,
    tmp_path,
    mock_embeddings,
) -> None:
    """api_query keeps record-only archival controls away from version queries."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    cfg = replace(make_config(tmp_path), projects={"project": str(project_root)})
    identity = resolve_project_identity(project_root)
    store = ContextStore(cfg.context_db_path)
    store.initialize()
    store.register_project(identity)
    store.create_record(
        project_id=identity.project_id,
        session_id=None,
        kind="fact",
        title="Versioned record",
        body="Version queries power dashboard charts.",
    )
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

    payload = api_mod.api_query(
        entity="versions",
        mode="list",
        scope="project",
        project="project",
        include_total=True,
    )

    assert payload["error"] is False
    assert payload["entity"] == "versions"
    assert payload["total"] == 1


def test_api_query_storage_error_returns_structured_failure(
    monkeypatch,
    tmp_path,
) -> None:
    cfg = make_config(tmp_path)
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

    class BrokenStore:
        def query(self, **kwargs):
            raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(api_mod, "_context_store", lambda config: BrokenStore())

    payload = api_mod.api_query(entity="records", mode="count")

    assert payload["error"] is True
    assert payload["status_code"] == 503
    assert payload["message"] == "Context query storage is unavailable."


def test_api_record_filters_reads_distinct_values_without_limit_sampling(
    monkeypatch,
    tmp_path,
    mock_embeddings,
) -> None:
    """Record filters come from distinct DB values instead of a sampled records page."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    cfg = replace(make_config(tmp_path), projects={"project": str(project_root)})
    identity = resolve_project_identity(project_root)
    store = ContextStore(cfg.context_db_path)
    store.initialize()
    store.register_project(identity)
    for index in range(505):
        store.create_record(
            project_id=identity.project_id,
            session_id=None,
            kind="fact" if index < 504 else "constraint",
            title=f"Record {index}",
            body="Body.",
            record_role="general" if index < 504 else "procedure",
            scope_label="project",
        )
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

    payload = api_record_filters()

    assert payload["types"] == ["constraint", "fact"]
    assert payload["roles"] == ["general", "procedure"]
    assert payload["projects"] == ["project"]
    assert payload["error"] is False


def test_api_record_filters_use_registered_project_scope(
    monkeypatch,
    tmp_path,
    mock_embeddings,
) -> None:
    """Filter options exclude stale records from unregistered projects."""
    registered_root = tmp_path / "registered"
    stale_root = tmp_path / "stale"
    registered_root.mkdir()
    stale_root.mkdir()
    cfg = replace(make_config(tmp_path), projects={"registered": str(registered_root)})
    registered_identity = resolve_project_identity(registered_root)
    stale_identity = resolve_project_identity(stale_root)
    store = ContextStore(cfg.context_db_path)
    store.initialize()
    store.register_project(registered_identity)
    store.register_project(stale_identity)
    store.create_record(
        project_id=registered_identity.project_id,
        session_id=None,
        kind="fact",
        title="Registered",
        body="Registered body.",
        record_role="procedure",
        scope_label="registered",
    )
    store.create_record(
        project_id=stale_identity.project_id,
        session_id=None,
        kind="constraint",
        title="Stale",
        body="Stale body.",
        record_role="gotcha",
        scope_label="stale",
    )
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

    payload = api_record_filters()

    assert payload["types"] == ["fact"]
    assert payload["roles"] == ["procedure"]
    assert payload["projects"] == ["registered"]


def test_api_record_filters_accept_project_child_path(
    monkeypatch,
    tmp_path,
    mock_embeddings,
) -> None:
    """Project-scoped record filters accept child paths and exclude siblings."""
    selected_root = tmp_path / "selected"
    sibling_root = tmp_path / "sibling"
    selected_child = selected_root / "src"
    selected_child.mkdir(parents=True)
    sibling_root.mkdir()
    cfg = replace(
        make_config(tmp_path),
        projects={"selected": str(selected_root), "sibling": str(sibling_root)},
    )
    selected_identity = resolve_project_identity(selected_root)
    sibling_identity = resolve_project_identity(sibling_root)
    store = ContextStore(cfg.context_db_path)
    store.initialize()
    store.register_project(selected_identity)
    store.register_project(sibling_identity)
    store.create_record(
        project_id=selected_identity.project_id,
        session_id=None,
        kind="preference",
        title="Selected",
        body="Selected project preference.",
        record_role="procedure",
        scope_label="selected",
    )
    store.create_record(
        project_id=sibling_identity.project_id,
        session_id=None,
        kind="constraint",
        title="Sibling",
        body="Sibling project constraint.",
        record_role="gotcha",
        scope_label="sibling",
    )
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

    payload = api_record_filters(project=str(selected_child))

    assert payload["types"] == ["preference"]
    assert payload["roles"] == ["procedure"]
    assert payload["projects"] == ["selected"]


def test_api_skill_target_add_scopes_registered_project_paths(
    monkeypatch,
    tmp_path,
    mock_embeddings,
) -> None:
    """Targets inside a registered project are saved with that project scope."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    skill = project_root / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo.\n---\n\nUse evidence.\n",
        encoding="utf-8",
    )
    cfg = replace(make_config(tmp_path), projects={"project": str(project_root)})
    monkeypatch.setattr(skill_api_mod, "get_config", lambda: cfg)

    payload = skill_api_mod.api_skill_target_add(path=str(skill), name="demo")

    assert payload["target"]["scope_type"] == "project"
    assert payload["target"]["scope_id"] == resolve_project_identity(project_root).project_id


def test_api_ingest_includes_queue_health_warning(monkeypatch, tmp_path) -> None:
    """Ingest API response surfaces degraded queue warning hints."""
    cfg = make_config(tmp_path)
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(
        api_mod, "run_ingest_once", lambda **kw: (0, IngestSummary(0, 0, 0, 0, []))
    )
    monkeypatch.setattr(api_mod, "ollama_lifecycle", _noop_lifecycle)
    monkeypatch.setattr(
        api_mod,
        "queue_health_snapshot",
        lambda: {"degraded": True, "advice": "run `lerim queue --failed`"},
    )
    result = api_ingest()
    assert result["queue_health"]["degraded"] is True
    assert "warning" in result


# ---------------------------------------------------------------------------
# api_curate
# ---------------------------------------------------------------------------


def test_api_curate_returns_code_and_payload(monkeypatch, tmp_path) -> None:
    """api_curate calls run_curate_once and returns code + payload."""
    cfg = make_config(tmp_path)
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(
        api_mod,
        "run_curate_once",
        lambda **kw: (0, {"projects": {"test": {"counts": {}}}}),
    )
    monkeypatch.setattr(api_mod, "ollama_lifecycle", _noop_lifecycle)
    _stub_status_catalog(monkeypatch)

    result = api_curate()

    assert result["code"] == 0
    assert "projects" in result


def test_api_curate_dry_run(monkeypatch, tmp_path) -> None:
    """api_curate passes dry_run through."""
    cfg = make_config(tmp_path)
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

    captured: dict[str, Any] = {}

    def fake_curate(**kwargs):
        """Capture curate arguments."""
        captured.update(kwargs)
        return (0, {"dry_run": True})

    monkeypatch.setattr(api_mod, "run_curate_once", fake_curate)
    monkeypatch.setattr(api_mod, "ollama_lifecycle", _noop_lifecycle)
    _stub_status_catalog(monkeypatch)

    api_curate(dry_run=True)

    assert captured["dry_run"] is True
    assert set(captured) == {"dry_run"}


def test_api_curate_includes_queue_health_warning(monkeypatch, tmp_path) -> None:
    """Curate API response surfaces degraded queue warning hints."""
    cfg = make_config(tmp_path)
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(api_mod, "run_curate_once", lambda **kw: (0, {"projects": {}}))
    monkeypatch.setattr(api_mod, "ollama_lifecycle", _noop_lifecycle)
    monkeypatch.setattr(
        api_mod,
        "queue_health_snapshot",
        lambda: {"degraded": True, "advice": "run `lerim queue --failed`"},
    )
    result = api_curate()
    assert result["queue_health"]["degraded"] is True
    assert "warning" in result


# ---------------------------------------------------------------------------
# api_status
# ---------------------------------------------------------------------------


def test_api_status_returns_expected_keys(
    monkeypatch, tmp_path, mock_embeddings
) -> None:
    """api_status returns dict with all required status fields."""
    cfg = replace(make_config(tmp_path), projects={"repo": str(tmp_path)})
    store = api_mod.ContextStore(cfg.context_db_path)
    store.initialize()
    identity = api_mod.resolve_project_identity(tmp_path)
    store.register_project(identity)
    store.create_record(
        project_id=identity.project_id,
        session_id=None,
        kind="fact",
        title="Canonical store",
        body="Context is stored in SQLite.",
    )

    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(api_mod, "list_platforms", lambda path: [])
    monkeypatch.setattr(api_mod, "count_fts_indexed", lambda: 5)
    monkeypatch.setattr(
        api_mod,
        "count_session_jobs_by_status",
        lambda: {"pending": 0, "done": 3},
    )
    monkeypatch.setattr(api_mod, "latest_service_run", lambda svc: None)
    _stub_status_catalog(monkeypatch)

    result = api_status()

    assert "timestamp" in result
    assert "connected_agents" in result
    assert "platforms" in result
    assert result["record_count"] == 1
    assert result["sessions_indexed_count"] == 5
    assert result["queue"] == {"pending": 0, "done": 3}
    assert result["ingest_window_days"] == cfg.ingest_window_days
    assert (
        result["schedule"]["ingest"]["interval_minutes"] == cfg.ingest_interval_minutes
    )
    assert result["schedule"]["ingest"]["seconds_until_next"] == 0
    assert (
        result["schedule"]["curate"]["interval_minutes"] == cfg.curate_interval_minutes
    )
    assert result["schedule"]["curate"]["seconds_until_next"] == 0
    assert "queue_health" in result
    assert result["scope"]["strict_project_only"] is True


def test_api_status_no_records(monkeypatch, tmp_path) -> None:
    """api_status returns 0 when no records exist yet."""
    cfg = make_config(tmp_path)
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(api_mod, "list_platforms", lambda path: [])
    monkeypatch.setattr(api_mod, "count_fts_indexed", lambda: 0)
    monkeypatch.setattr(api_mod, "count_session_jobs_by_status", lambda: {})
    monkeypatch.setattr(api_mod, "latest_service_run", lambda svc: None)
    _stub_status_catalog(monkeypatch)

    result = api_status()
    assert result["record_count"] == 0


def test_api_status_degrades_when_session_catalog_unavailable(
    monkeypatch, tmp_path
) -> None:
    """api_status reports catalog storage failure without raising."""
    cfg = make_config(tmp_path)
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(api_mod, "list_platforms", lambda path: [])

    def broken_latest_service_run(_job_type: str) -> None:
        """Simulate a malformed session catalog."""
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(api_mod, "latest_service_run", broken_latest_service_run)

    result = api_status()

    assert result["session_catalog"]["status"] == "unavailable"
    assert result["session_catalog"]["error"]
    assert result["queue"] == api_mod._empty_queue_counts()
    assert result["queue_health"]["degraded"] is True
    assert result["recent_activity"] == []


def test_api_curate_degrades_when_queue_health_unavailable(
    monkeypatch, tmp_path
) -> None:
    """api_curate still returns its payload when queue health cannot read."""
    cfg = make_config(tmp_path)
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(
        api_mod,
        "run_curate_once",
        lambda dry_run: (0, {"dry_run": dry_run}),
    )

    def broken_queue_health() -> None:
        """Simulate a malformed session catalog."""
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(api_mod, "queue_health_snapshot", broken_queue_health)

    result = api_curate(dry_run=True)

    assert result["code"] == 0
    assert result["dry_run"] is True
    assert result["queue_health"]["degraded"] is True
    assert result["queue_health"]["error"]


def test_api_status_scope_skipped_unscoped_from_latest_ingest(
    monkeypatch, tmp_path
) -> None:
    """Status exposes strict-scope skipped counter from latest ingest details."""
    cfg = make_config(tmp_path)
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(api_mod, "list_platforms", lambda path: [])
    monkeypatch.setattr(api_mod, "count_fts_indexed", lambda: 0)
    monkeypatch.setattr(api_mod, "count_session_jobs_by_status", lambda: {})
    monkeypatch.setattr(
        api_mod,
        "latest_service_run",
        lambda svc: (
            {"details": {"ingest_metrics": {"skipped_unscoped": 7}}}
            if svc == "ingest"
            else None
        ),
    )
    _stub_status_catalog(monkeypatch)
    result = api_status()
    assert result["scope"]["skipped_unscoped"] == 7
    assert result["latest_ingest"]["details"]["skipped_unscoped"] == 7


def test_api_status_normalizes_non_curate_history_to_ingest(
    monkeypatch, tmp_path
) -> None:
    """Status should not leak stale pre-canonical service run type names."""
    cfg = make_config(tmp_path)
    run = {
        "id": 123,
        "job_type": "old-ingest-row",
        "status": "completed",
        "started_at": "2026-05-16T07:00:00+00:00",
        "completed_at": "2026-05-16T07:01:00+00:00",
        "trigger": "daemon",
        "details": {
            "projects_metrics": {"proj-a": {"sessions_analyzed": 1}},
            "ingest_metrics": {"sessions_analyzed": 1},
        },
    }
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(api_mod, "list_platforms", lambda path: [])
    monkeypatch.setattr(api_mod, "count_fts_indexed", lambda: 0)
    monkeypatch.setattr(api_mod, "count_session_jobs_by_status", lambda: {})
    monkeypatch.setattr(
        api_mod,
        "latest_service_run",
        lambda svc: run if svc == "ingest" else None,
    )
    _stub_status_catalog(monkeypatch)
    monkeypatch.setattr(api_mod, "list_service_runs", lambda **kwargs: [run])

    result = api_status()

    assert result["latest_ingest"]["job_type"] == "ingest"
    assert result["recent_activity"][0]["op_type"] == "ingest"


def test_api_status_reports_active_writer_lock_as_running(
    monkeypatch, tmp_path
) -> None:
    """Status uses the live writer lock when service-run history is stale."""
    cfg = make_config(tmp_path)
    lock_path = cfg.global_data_dir / "index" / api_mod.WRITER_LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = "2026-05-16T21:07:56+00:00"
    lock_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "owner": "curate",
                "command": "lerim curate",
                "started_at": started_at,
                "heartbeat_at": datetime.now(timezone.utc).isoformat(),
                "host": "test-host",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(api_mod, "list_platforms", lambda path: [])
    monkeypatch.setattr(api_mod, "count_fts_indexed", lambda: 0)
    monkeypatch.setattr(api_mod, "count_session_jobs_by_status", lambda: {})
    monkeypatch.setattr(
        api_mod,
        "latest_service_run",
        lambda svc: {
            "job_type": svc,
            "status": "partial",
            "started_at": "2026-05-16T19:50:01+00:00",
            "completed_at": "2026-05-16T20:09:09+00:00",
        },
    )
    _stub_status_catalog(monkeypatch)

    result = api_status()

    assert result["schedule"]["curate"]["running"] is True
    assert result["schedule"]["curate"]["last_status"] == "started"
    assert result["schedule"]["curate"]["last_started_at"] == started_at
    assert result["schedule"]["curate"]["last_completed_at"] is None
    assert result["schedule"]["ingest"]["running"] is False


def test_schedule_item_retries_lock_busy_quickly() -> None:
    """Lock-busy status should not advertise the full daemon interval."""
    completed_at = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
    item = api_mod._schedule_item(
        latest={
            "status": "lock_busy",
            "started_at": "2026-05-17T11:59:59+00:00",
            "completed_at": completed_at.isoformat(),
        },
        interval_minutes=50,
        now=completed_at + timedelta(seconds=5),
    )

    assert item["last_status"] == "lock_busy"
    assert item["seconds_until_next"] == 5
    assert (
        item["next_due_at"]
        == (
            completed_at + timedelta(seconds=api_mod.DAEMON_LOCK_BUSY_RETRY_SECONDS)
        ).isoformat()
    )


def test_api_status_preserves_bad_project_selection_error(
    monkeypatch, tmp_path
) -> None:
    """Project status reports which project argument was not registered."""
    cfg = replace(make_config(tmp_path), projects={"known": str(tmp_path)})
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(api_mod, "list_platforms", lambda path: [])
    monkeypatch.setattr(api_mod, "count_fts_indexed", lambda: 0)
    monkeypatch.setattr(api_mod, "count_session_jobs_by_status", lambda: {})
    monkeypatch.setattr(api_mod, "latest_service_run", lambda svc: None)
    _stub_status_catalog(monkeypatch)

    result = api_status(scope="project", project="missing")

    assert result["error"] == "Project not found: missing"
    assert result["projects"] == []


def test_api_status_preserves_missing_project_selection_error(
    monkeypatch, tmp_path
) -> None:
    """Project status explains when a project argument is required."""
    project_a = tmp_path / "a"
    project_b = tmp_path / "b"
    project_a.mkdir()
    project_b.mkdir()
    cfg = replace(
        make_config(tmp_path),
        projects={"a": str(project_a), "b": str(project_b)},
    )
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(api_mod, "list_platforms", lambda path: [])
    monkeypatch.setattr(api_mod, "count_fts_indexed", lambda: 0)
    monkeypatch.setattr(api_mod, "count_session_jobs_by_status", lambda: {})
    monkeypatch.setattr(api_mod, "latest_service_run", lambda svc: None)
    _stub_status_catalog(monkeypatch)

    result = api_status(scope="project")

    assert result["error"] == (
        "scope=project requires a project name when multiple projects are registered."
    )
    assert result["projects"] == []


# ---------------------------------------------------------------------------
# api_project_list / api_project_add / api_project_remove
# ---------------------------------------------------------------------------


def _seed_memory_reset_project(
    tmp_path: Path,
    cfg: Any,
    project_name: str,
    project_path: Path,
) -> str:
    """Seed context and session rows for memory reset tests."""
    store = ContextStore(cfg.context_db_path)
    store.initialize()
    identity = resolve_project_identity(project_path)
    store.register_project(identity)
    store.upsert_session(
        project_id=identity.project_id,
        session_id=f"{project_name}-session",
        agent_type="codex",
        source_trace_ref=str(tmp_path / f"{project_name}.jsonl"),
        repo_path=str(project_path),
        cwd=str(project_path),
        started_at="2026-04-01T00:00:00+00:00",
        model_name="test-model",
        instructions_text=None,
        prompt_text=None,
    )
    store.create_record(
        project_id=identity.project_id,
        session_id=f"{project_name}-session",
        kind="fact",
        title=f"{project_name} fact",
        body=f"{project_name} body",
    )
    catalog_mod.index_session_for_fts(
        run_id=f"{project_name}-run",
        agent_type="codex",
        content=f"{project_name} indexed content",
        repo_path=str(project_path),
        session_path=str(tmp_path / f"{project_name}.jsonl"),
        content_hash=f"{project_name}-hash",
    )
    catalog_mod.enqueue_session_job(
        f"{project_name}-run",
        agent_type="codex",
        session_path=str(tmp_path / f"{project_name}.jsonl"),
        repo_path=str(project_path),
    )
    return identity.project_id


def test_api_memory_reset_rejects_missing_scope(monkeypatch, tmp_path) -> None:
    cfg = make_config(tmp_path)
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

    result = api_memory_reset()

    assert result["error"] is True
    assert "exactly one" in result["message"]


def test_api_memory_reset_rejects_bad_project(monkeypatch, tmp_path) -> None:
    cfg = replace(make_config(tmp_path), projects={"known": str(tmp_path)})
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

    result = api_memory_reset(project="missing")

    assert result["error"] is True
    assert result["message"] == "Project not found: missing"


def test_api_memory_reset_dry_run_does_not_register_project(
    monkeypatch, tmp_path
) -> None:
    project_path = tmp_path / "proj-a"
    project_path.mkdir()
    cfg = replace(make_config(tmp_path), projects={"proj-a": str(project_path)})
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(catalog_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(
        api_mod,
        "reset_cloud_data",
        lambda config, *, dry_run: {
            "configured": True,
            "dry_run": dry_run,
            "error": False,
            "deleted": {"records": 2, "graph_nodes": 0},
        },
    )

    result = api_memory_reset(project="proj-a", dry_run=True)

    assert result["error"] is False
    assert result["dry_run"] is True
    store = ContextStore(cfg.context_db_path)
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(1) FROM projects").fetchone()[0] == 0


def test_api_memory_reset_project_deletes_only_that_project(
    monkeypatch, tmp_path, mock_embeddings
) -> None:
    project_a = tmp_path / "proj-a"
    project_b = tmp_path / "proj-b"
    project_a.mkdir()
    project_b.mkdir()
    cfg = replace(
        make_config(tmp_path),
        projects={"proj-a": str(project_a), "proj-b": str(project_b)},
    )
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(catalog_mod, "get_config", lambda: cfg)
    project_a_id = _seed_memory_reset_project(tmp_path, cfg, "proj-a", project_a)
    project_b_id = _seed_memory_reset_project(tmp_path, cfg, "proj-b", project_b)

    result = api_memory_reset(project="proj-a")

    assert result["error"] is False
    assert result["scope"] == "project"
    assert result["deleted"]["records"] == 1
    assert result["deleted"]["record_versions"] == 1
    assert result["deleted"]["context_sessions"] == 1
    assert result["deleted"]["records_fts"] == 1
    assert result["deleted"]["indexed_sessions"] == 1
    assert result["deleted"]["session_jobs"] == 1
    store = ContextStore(cfg.context_db_path)
    with store.connect() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(1) FROM records WHERE project_id = ?",
                (project_a_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(1) FROM records WHERE project_id = ?",
                (project_b_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT COUNT(1) FROM record_versions WHERE project_id = ?",
                (project_a_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(1) FROM records_fts WHERE project_id = ?",
                (project_a_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(1) FROM record_embeddings WHERE project_id = ?",
                (project_a_id,),
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(1) FROM projects WHERE project_id IN (?, ?)",
                (project_a_id, project_b_id),
            ).fetchone()[0]
            == 2
        )
    assert catalog_mod.fetch_session_doc("proj-a-run") is None
    assert catalog_mod.fetch_session_doc("proj-b-run") is not None


def test_api_memory_reset_all_clears_memory_and_cloud_state(
    monkeypatch, tmp_path, mock_embeddings
) -> None:
    project_a = tmp_path / "proj-a"
    project_b = tmp_path / "proj-b"
    project_a.mkdir()
    project_b.mkdir()
    cfg = replace(
        make_config(tmp_path),
        projects={"proj-a": str(project_a), "proj-b": str(project_b)},
    )
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(catalog_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(
        api_mod,
        "reset_cloud_data",
        lambda config, *, dry_run: {
            "configured": True,
            "dry_run": dry_run,
            "error": False,
            "deleted": {"records": 2, "sessions": 2, "graph_nodes": 0},
        },
    )
    _seed_memory_reset_project(tmp_path, cfg, "proj-a", project_a)
    _seed_memory_reset_project(tmp_path, cfg, "proj-b", project_b)
    catalog_mod.record_service_run(
        job_type="ingest",
        status="completed",
        started_at="2026-04-01T00:00:00+00:00",
        completed_at="2026-04-01T00:00:01+00:00",
        trigger="test",
        details={},
    )
    cloud_state = cfg.global_data_dir / "cloud_shipper_state.json"
    cloud_state.write_text(
        '{"records_shipped_at":"2026-04-01T00:00:00Z"}', encoding="utf-8"
    )
    legacy_memory_dir = cfg.global_data_dir / "memory"
    legacy_memory_dir.mkdir()
    (legacy_memory_dir / "old.txt").write_text("legacy", encoding="utf-8")

    result = api_memory_reset(all_projects=True)

    assert result["error"] is False
    assert result["scope"] == "all"
    assert result["deleted"]["records"] == 2
    assert result["deleted"]["indexed_sessions"] == 2
    assert result["deleted"]["session_jobs"] == 2
    assert result["deleted"]["service_runs"] == 1
    assert result["deleted"]["cloud_shipper_state"] == 1
    assert result["cloud_reset"]["configured"] is True
    assert result["cloud_reset"]["deleted"]["records"] == 2
    assert not cloud_state.exists()
    assert not legacy_memory_dir.exists()
    store = ContextStore(cfg.context_db_path)
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(1) FROM records").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(1) FROM projects").fetchone()[0] == 2
    assert catalog_mod.count_fts_indexed() == 0
    assert sum(catalog_mod.count_session_jobs_by_status().values()) == 0


def test_api_memory_reset_cleanup_failure_is_best_effort(monkeypatch, tmp_path) -> None:
    cfg = make_config(tmp_path)
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(catalog_mod, "get_config", lambda: cfg)
    released: list[bool] = []

    class TrackingLock:
        def __init__(self, *args, **kwargs):
            pass

        def acquire(self, *args, **kwargs):
            return None

        def release(self):
            released.append(True)

    def fail_cleanup(_path: Path) -> bool:
        raise OSError("permission denied")

    monkeypatch.setattr(api_mod, "ServiceLock", TrackingLock)
    monkeypatch.setattr(api_mod, "remove_legacy_memory_dir", fail_cleanup)

    result = api_memory_reset(all_projects=True)

    assert result["error"] is False
    assert released == [True]


def test_api_memory_reset_refuses_busy_writer_lock(monkeypatch, tmp_path) -> None:
    cfg = make_config(tmp_path)
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

    class BusyLock:
        def __init__(self, *args, **kwargs):
            pass

        def acquire(self, *args, **kwargs):
            raise api_mod.LockBusyError(tmp_path / "writer.lock", {"owner": "ingest"})

        def release(self):
            raise AssertionError("busy lock should not be released")

    monkeypatch.setattr(api_mod, "ServiceLock", BusyLock)

    result = api_memory_reset(all_projects=True)

    assert result["error"] is True
    assert "Cannot reset memory" in result["message"]


def test_api_memory_reset_reports_cloud_failure(
    monkeypatch, tmp_path, mock_embeddings
) -> None:
    """All-project reset should surface cloud reset failures without aborting local reset."""
    project_a = tmp_path / "proj-a"
    project_a.mkdir()
    cfg = replace(make_config(tmp_path), projects={"proj-a": str(project_a)})
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(catalog_mod, "get_config", lambda: cfg)
    _seed_memory_reset_project(tmp_path, cfg, "proj-a", project_a)
    monkeypatch.setattr(
        api_mod,
        "reset_cloud_data",
        lambda config, *, dry_run: {
            "configured": True,
            "dry_run": dry_run,
            "error": True,
            "message": "cloud reset failed",
        },
    )

    result = api_memory_reset(all_projects=True)

    assert result["error"] is False
    assert result["deleted"]["records"] == 1
    assert result["cloud_reset"]["error"] is True
    assert "cloud dashboard reset failed" in result["notes"][0]


def test_api_project_list_empty(monkeypatch, tmp_path) -> None:
    """api_project_list returns empty list when no projects registered."""
    cfg = make_config(tmp_path)
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

    result = api_project_list()
    assert result == []


def test_api_project_list_with_projects(monkeypatch, tmp_path, mock_embeddings) -> None:
    """api_project_list returns project info for registered projects."""
    proj_dir = tmp_path / "myproject"
    proj_dir.mkdir()

    cfg = replace(
        make_config(tmp_path),
        projects={"myproject": str(proj_dir)},
        project_types={"myproject": "custom"},
    )
    identity = resolve_project_identity(proj_dir)
    store = ContextStore(cfg.context_db_path)
    store.initialize()
    store.register_project(identity)
    store.create_record(
        project_id=identity.project_id,
        session_id=None,
        kind="fact",
        title="Active",
        body="Active project record.",
    )
    store.create_record(
        project_id=identity.project_id,
        session_id=None,
        kind="fact",
        title="Archived",
        body="Archived project record.",
        status="archived",
    )
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

    result = api_project_list()
    assert len(result) == 1
    assert result[0]["name"] == "myproject"
    assert result[0]["type"] == "custom"
    assert result[0]["exists"] is True
    assert result[0]["record_count"] == 1
    assert result[0]["active_record_count"] == 1
    assert result[0]["archived_record_count"] == 1
    assert result[0]["total_record_count"] == 2
    assert "has_lerim" not in result[0]


def test_api_project_add_registers_project_in_context_db(monkeypatch, tmp_path) -> None:
    """api_project_add registers project metadata in the global context DB."""
    proj_dir = tmp_path / "newproject"
    proj_dir.mkdir()
    cfg = replace(make_config(tmp_path), projects={})

    saved: list[dict] = []
    monkeypatch.setattr(api_mod, "save_config_patch", lambda patch: saved.append(patch))
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

    result = api_project_add(str(proj_dir))

    assert result["name"] == "newproject"
    assert result["context_db_path"] == str(cfg.context_db_path)
    assert "project_id" in result
    assert not (proj_dir / ".lerim").exists()
    assert len(saved) == 1
    assert "newproject" in saved[0]["projects"]
    assert saved[0]["project_types"]["newproject"] == "supported"


def test_api_project_add_registers_custom_project_type(monkeypatch, tmp_path) -> None:
    """api_project_add persists the custom source type when requested."""
    traces_dir = tmp_path / "clean-traces"
    traces_dir.mkdir()
    cfg = replace(make_config(tmp_path), projects={})

    saved: list[dict] = []
    monkeypatch.setattr(api_mod, "save_config_patch", lambda patch: saved.append(patch))
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

    result = api_project_add(str(traces_dir), project_type="custom")

    assert result["name"] == "clean-traces"
    assert result["type"] == "custom"
    assert saved[0]["project_types"]["clean-traces"] == "custom"


def test_api_project_add_registers_project_source_profile(
    monkeypatch,
    tmp_path,
) -> None:
    """api_project_add persists a validated project default source profile."""
    traces_dir = tmp_path / "research-traces"
    traces_dir.mkdir()
    cfg = replace(make_config(tmp_path), projects={})

    saved: list[dict] = []
    monkeypatch.setattr(api_mod, "save_config_patch", lambda patch: saved.append(patch))
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(
        api_mod,
        "list_signal_packs",
        lambda: [SimpleNamespace(id="coding"), SimpleNamespace(id="research")],
    )

    result = api_project_add(
        str(traces_dir),
        project_type="custom",
        source_profile="RESEARCH",
    )

    assert result["name"] == "research-traces"
    assert saved[0]["project_profiles"]["research-traces"] == "research"


def test_api_project_add_rejects_unknown_source_profile(
    monkeypatch,
    tmp_path,
) -> None:
    """api_project_add rejects profiles that are not bundled or registered."""
    traces_dir = tmp_path / "research-traces"
    traces_dir.mkdir()
    monkeypatch.setattr(
        api_mod,
        "list_signal_packs",
        lambda: [SimpleNamespace(id="coding")],
    )

    result = api_project_add(str(traces_dir), source_profile="research")

    assert result["name"] is None
    assert "unknown source profile" in result["error"]


def test_api_project_add_rejects_unknown_project_type(tmp_path) -> None:
    """api_project_add rejects undocumented source types."""
    traces_dir = tmp_path / "clean-traces"
    traces_dir.mkdir()

    result = api_project_add(str(traces_dir), project_type="unknown")

    assert result["name"] is None
    assert "project type must be one of" in result["error"]


def test_api_project_add_disambiguates_duplicate_basenames(
    monkeypatch, tmp_path
) -> None:
    """api_project_add should not overwrite an existing project with the same basename."""
    first = tmp_path / "apps" / "service"
    second = tmp_path / "packages" / "service"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    cfg = replace(make_config(tmp_path), projects={"service": str(first)})

    saved: list[dict] = []
    monkeypatch.setattr(api_mod, "save_config_patch", lambda patch: saved.append(patch))
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

    result = api_project_add(str(second))

    assert result["name"] != "service"
    assert result["name"].startswith("service-")
    assert saved[0]["projects"][result["name"]] == str(second.resolve())
    assert saved[0]["project_types"][result["name"]] == "supported"


def test_api_status_reports_projects_and_unscoped(
    monkeypatch, tmp_path, mock_embeddings
) -> None:
    """api_status includes per-project payloads and unscoped counts."""
    project_a = tmp_path / "proj-a"
    project_b = tmp_path / "proj-b"
    project_a.mkdir()
    project_b.mkdir()

    cfg = replace(
        make_config(tmp_path),
        projects={"proj-a": str(project_a), "proj-b": str(project_b)},
    )
    store = api_mod.ContextStore(cfg.context_db_path)
    store.initialize()
    project_a_id = ""
    for path, title in ((project_a, "A record"), (project_b, "B record")):
        identity = api_mod.resolve_project_identity(path)
        store.register_project(identity)
        if path == project_a:
            project_a_id = identity.project_id
        store.create_record(
            project_id=identity.project_id,
            session_id=None,
            kind="fact",
            title=title,
            body=title,
        )
    store.create_record(
        project_id=project_a_id,
        session_id=None,
        kind="fact",
        title="A archived record",
        body="Archived project history should stay visible in totals.",
        status="archived",
    )
    store.create_record(
        project_id=project_a_id,
        session_id=None,
        kind="fact",
        title="A expired active record",
        body="Expired active records should not count as current context.",
        status="active",
        valid_until="2000-01-01T00:00:00Z",
    )
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(api_mod, "list_platforms", lambda path: [])
    monkeypatch.setattr(api_mod, "count_fts_indexed", lambda: 11)
    monkeypatch.setattr(api_mod, "count_session_jobs_by_status", lambda: {"pending": 1})
    monkeypatch.setattr(api_mod, "latest_service_run", lambda svc: None)
    monkeypatch.setattr(
        api_mod, "queue_health_snapshot", lambda: {"degraded": False, "advice": ""}
    )
    monkeypatch.setattr(
        api_mod,
        "_queue_counts_for_repo",
        lambda **kwargs: ({"pending": 1, "dead_letter": 0}, None, None),
    )
    monkeypatch.setattr(
        api_mod,
        "count_unscoped_sessions_by_agent",
        lambda projects: {"cursor": 3, "codex": 1},
    )
    monkeypatch.setattr(api_mod, "list_session_jobs", lambda **kwargs: [])
    monkeypatch.setattr(api_mod, "list_service_runs", lambda **kwargs: [])

    monkeypatch.delenv(api_mod.RUNTIME_SOURCE_ENV, raising=False)
    monkeypatch.delenv(api_mod.RUNTIME_IMAGE_ENV, raising=False)

    result = api_status()
    assert result["record_count"] == 2
    assert result["active_record_count"] == 2
    assert result["archived_record_count"] == 1
    assert result["total_record_count"] == 4
    assert api_mod.api_query(entity="records", mode="count", status="active")["count"] == 2
    assert result["runtime"]["version"]
    assert result["runtime"]["source"] == "direct"
    assert len(result["projects"]) == 2
    assert all("indexed_sessions_count" in item for item in result["projects"])
    assert all("latest_session_start_time" in item for item in result["projects"])
    assert result["unscoped_sessions"]["total"] == 4
    assert result["unscoped_sessions"]["by_agent"]["cursor"] == 3
    project_a_payload = result["projects"][0]
    assert project_a_payload["record_count"] == 1
    assert project_a_payload["archived_record_count"] == 1
    assert project_a_payload["total_record_count"] == 3


def test_api_status_project_scope_counts_child_session_paths(
    monkeypatch, tmp_path, mock_embeddings
) -> None:
    """api_status project scope counts sessions and jobs under child paths."""
    project_path = tmp_path / "repo"
    child_path = project_path / "packages" / "worker"
    child_path.mkdir(parents=True)
    cfg = replace(make_config(tmp_path), projects={"repo": str(project_path)})
    store = api_mod.ContextStore(cfg.context_db_path)
    store.initialize()
    identity = api_mod.resolve_project_identity(project_path)
    store.register_project(identity)
    store.create_record(
        project_id=identity.project_id,
        session_id=None,
        kind="fact",
        title="Project record",
        body="Project status should be scoped.",
    )
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(catalog_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(api_mod, "list_platforms", lambda path: [])
    monkeypatch.setattr(api_mod, "latest_service_run", lambda svc: None)
    monkeypatch.setattr(
        api_mod, "queue_health_snapshot", lambda: {"degraded": False, "advice": ""}
    )
    monkeypatch.setattr(api_mod, "count_unscoped_sessions_by_agent", lambda projects: {})
    monkeypatch.setattr(api_mod, "list_session_jobs", lambda **kwargs: [])
    monkeypatch.setattr(api_mod, "list_service_runs", lambda **kwargs: [])
    catalog_mod.init_sessions_db()
    for run_id, path in (("root-run", project_path), ("child-run", child_path)):
        assert catalog_mod.index_session_for_fts(
            run_id=run_id,
            agent_type="codex",
            content=f"{run_id} content",
            repo_path=str(path),
            repo_name=path.name,
            start_time="2026-03-20T10:00:00Z",
        )
    now = "2026-03-20T10:00:00Z"
    with sqlite3.connect(cfg.sessions_db_path) as conn:
        conn.executemany(
            """INSERT INTO session_jobs (
                run_id, job_type, agent_type, session_path, start_time, status,
                available_at, created_at, updated_at, repo_path
            ) VALUES (?, 'extract', 'codex', '', ?, ?, ?, ?, ?, ?)""",
            [
                ("root-run", now, "pending", now, now, now, str(project_path)),
                ("child-run", now, "dead_letter", now, now, now, str(child_path)),
                ("other-run", now, "failed", now, now, now, str(tmp_path / "other")),
            ],
        )

    result = api_status(scope="project", project="repo")

    assert result["record_count"] == 1
    assert result["sessions_indexed_count"] == 2
    assert result["queue"]["pending"] == 1
    assert result["queue"]["dead_letter"] == 1
    assert result["projects"][0]["indexed_sessions_count"] == 2
    assert result["projects"][0]["queue"]["dead_letter"] == 1
    queue_payload = api_mod.api_queue_jobs(project="repo")
    assert {job["run_id"] for job in queue_payload["jobs"]} == {
        "root-run",
        "child-run",
    }
    assert queue_payload["queue"]["pending"] == 1
    assert queue_payload["queue"]["dead_letter"] == 1
    assert queue_payload["queue"]["failed"] == 0
    child_path_result = api_status(scope="project", project=str(child_path))
    assert child_path_result["record_count"] == 1
    assert child_path_result["sessions_indexed_count"] == 2
    before_reset = catalog_mod.count_indexed_sessions_for_project(str(project_path))
    assert before_reset["indexed_sessions"] == 2
    assert before_reset["session_jobs"] == 2

    deleted = catalog_mod.reset_indexed_sessions_for_project(str(project_path))

    assert deleted["indexed_sessions"] == 2
    assert deleted["session_jobs"] == 2
    assert catalog_mod.count_indexed_sessions_for_project(str(project_path)) == {
        "indexed_sessions": 0,
        "session_jobs": 0,
        "service_runs": 0,
    }


def test_api_status_project_scope_latest_uses_scoped_activity(
    monkeypatch, tmp_path, mock_embeddings
) -> None:
    """Project status latest/schedule fields should not reuse global service runs."""
    repo_path = tmp_path / "repo"
    other_path = tmp_path / "other"
    repo_path.mkdir()
    other_path.mkdir()
    cfg = replace(
        make_config(tmp_path),
        projects={"repo": str(repo_path), "other": str(other_path)},
    )
    store = api_mod.ContextStore(cfg.context_db_path)
    store.initialize()
    repo_identity = api_mod.resolve_project_identity(repo_path)
    other_identity = api_mod.resolve_project_identity(other_path)
    store.register_project(repo_identity)
    store.register_project(other_identity)
    store.create_record(
        project_id=repo_identity.project_id,
        session_id=None,
        kind="fact",
        title="Repo record",
        body="Repo-scoped status should use repo activity.",
    )
    store.create_record(
        project_id=other_identity.project_id,
        session_id=None,
        kind="fact",
        title="Other record",
        body="Other activity should not leak into repo status.",
    )
    global_ingest = {
        "id": "other-ingest",
        "job_type": "ingest",
        "status": "completed",
        "started_at": "2026-03-21T10:00:00+00:00",
        "completed_at": "2026-03-21T10:01:00+00:00",
        "trigger": "daemon",
        "details": {
            "projects_metrics": {"other": {}},
            "ingest_metrics": {"sessions_analyzed": 99, "skipped_unscoped": 7},
        },
    }
    global_curate = {
        "id": "other-curate",
        "job_type": "curate",
        "status": "completed",
        "started_at": "2026-03-21T11:00:00+00:00",
        "completed_at": "2026-03-21T11:01:00+00:00",
        "trigger": "daemon",
        "details": {
            "projects_metrics": {"other": {}},
            "curate_metrics": {"counts": {"created": 9}},
        },
    }
    repo_ingest = {
        "id": "repo-ingest",
        "job_type": "ingest",
        "status": "completed",
        "started_at": "2026-03-20T10:00:00+00:00",
        "completed_at": "2026-03-20T10:01:00+00:00",
        "trigger": "manual",
        "details": {
            "projects_metrics": {
                "repo": {"sessions_analyzed": 2, "records_created": 1},
                "other": {"sessions_analyzed": 97, "records_created": 9},
            },
            "ingest_metrics": {
                "sessions_analyzed": 99,
                "records_created": 10,
                "skipped_unscoped": 1,
            },
        },
    }
    repo_curate = {
        "id": "repo-curate",
        "job_type": "curate",
        "status": "completed",
        "started_at": "2026-03-20T11:00:00+00:00",
        "completed_at": "2026-03-20T11:01:00+00:00",
        "trigger": "manual",
        "details": {
            "projects_metrics": {
                "repo": {
                    "records_created": 1,
                    "curate_counts": {"created": 1},
                },
                "other": {
                    "records_created": 8,
                    "curate_counts": {"created": 8},
                },
            },
            "curate_metrics": {"counts": {"created": 9}, "records_created": 9},
        },
    }
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(api_mod, "list_platforms", lambda path: [])
    monkeypatch.setattr(api_mod, "count_fts_indexed", lambda: 999)
    monkeypatch.setattr(
        api_mod, "count_session_jobs_by_status", lambda: {"pending": 99}
    )
    monkeypatch.setattr(
        api_mod,
        "latest_service_run",
        lambda svc: global_ingest if svc == "ingest" else global_curate,
    )
    monkeypatch.setattr(
        api_mod, "queue_health_snapshot", lambda: {"degraded": False, "advice": ""}
    )
    monkeypatch.setattr(api_mod, "count_unscoped_sessions_by_agent", lambda projects: {})
    monkeypatch.setattr(api_mod, "list_session_jobs", lambda **kwargs: [])
    monkeypatch.setattr(
        api_mod,
        "list_service_runs",
        lambda **kwargs: [global_ingest, global_curate, repo_ingest, repo_curate],
    )

    result = api_status(scope="project", project="repo")

    assert result["latest_ingest"]["id"] == "repo-ingest"
    assert result["latest_curate"]["id"] == "repo-curate"
    assert result["latest_ingest"]["details"]["sessions_analyzed"] == 2
    assert result["latest_ingest"]["details"]["records_created"] == 1
    assert result["latest_curate"]["details"]["curate_counts"]["created"] == 1
    assert result["latest_curate"]["details"]["records_created"] == 1
    assert result["recent_activity"][0]["shared"] is True
    assert result["recent_activity"][0]["shared_projects"] == ["other"]
    assert result["recent_activity"][0]["all_projects"] == ["other", "repo"]
    assert result["scope"]["skipped_unscoped"] == 0
    assert result["schedule"]["ingest"]["last_started_at"] == repo_ingest["started_at"]
    assert result["schedule"]["curate"]["last_started_at"] == repo_curate["started_at"]


def test_runtime_identity_uses_docker_env(monkeypatch) -> None:
    monkeypatch.setenv(api_mod.RUNTIME_SOURCE_ENV, "local-build")
    monkeypatch.setenv(api_mod.RUNTIME_IMAGE_ENV, "lerim-test:local")

    assert api_mod._runtime_identity() == {
        "version": api_mod.__version__,
        "source": "local-build",
        "image": "lerim-test:local",
    }


def test_api_project_add_not_a_directory(tmp_path) -> None:
    """api_project_add returns error for non-directory path."""
    result = api_project_add(str(tmp_path / "nonexistent"))
    assert "error" in result
    assert result["name"] is None


def test_api_project_remove_success(monkeypatch, tmp_path) -> None:
    """api_project_remove removes project from config."""
    cfg = replace(make_config(tmp_path), projects={"myproject": str(tmp_path)})
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

    config_file = tmp_path / "user_config.toml"
    config_file.write_text(
        '[projects]\nmyproject = "/tmp/myproject"\n\n'
        '[project_types]\nmyproject = "custom"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(api_mod, "get_user_config_path", lambda: config_file)
    written: list[dict] = []
    monkeypatch.setattr(
        api_mod, "_write_config_full", lambda data: written.append(data)
    )

    result = api_project_remove("myproject")
    assert result["removed"] is True
    assert written[0]["projects"] == {}
    assert written[0]["project_types"] == {}


def test_api_project_remove_not_found(monkeypatch, tmp_path) -> None:
    """api_project_remove returns error for unregistered project."""
    cfg = make_config(tmp_path)
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

    result = api_project_remove("nonexistent")
    assert result["removed"] is False
    assert "error" in result


# ---------------------------------------------------------------------------
# api_retry_all_dead_letter / api_skip_all_dead_letter
# ---------------------------------------------------------------------------


def test_api_retry_all_dead_letter(monkeypatch) -> None:
    """api_retry_all_dead_letter retries all dead letter jobs."""
    monkeypatch.setattr(api_mod, "retry_all_dead_letter_jobs", lambda: 2)
    monkeypatch.setattr(
        api_mod, "count_session_jobs_by_status", lambda: {"dead_letter": 0}
    )

    result = api_retry_all_dead_letter()
    assert result["retried"] == 2


def test_api_skip_all_dead_letter(monkeypatch) -> None:
    """api_skip_all_dead_letter skips all dead letter jobs."""
    monkeypatch.setattr(api_mod, "skip_all_dead_letter_jobs", lambda: 2)
    monkeypatch.setattr(
        api_mod, "count_session_jobs_by_status", lambda: {"dead_letter": 0}
    )

    result = api_skip_all_dead_letter()
    assert result["skipped"] == 2


def test_api_retry_all_dead_letter_partial_failure(monkeypatch) -> None:
    """api_retry_all_dead_letter reports the catalog bulk transition count."""
    monkeypatch.setattr(api_mod, "retry_all_dead_letter_jobs", lambda: 51)
    monkeypatch.setattr(
        api_mod, "count_session_jobs_by_status", lambda: {"dead_letter": 1}
    )

    result = api_retry_all_dead_letter()
    assert result["retried"] == 51


def test_api_retry_all_dead_letter_does_not_page_queue(monkeypatch) -> None:
    """Bulk retry uses the uncapped catalog transition, not the queue listing."""
    monkeypatch.setattr(api_mod, "retry_all_dead_letter_jobs", lambda: 55)
    monkeypatch.setattr(
        api_mod,
        "list_queue_jobs",
        lambda **kw: pytest.fail("bulk retry should not list paginated jobs"),
    )
    monkeypatch.setattr(
        api_mod, "count_session_jobs_by_status", lambda: {"dead_letter": 0}
    )

    result = api_retry_all_dead_letter()
    assert result["retried"] == 55


def test_api_skip_all_dead_letter_does_not_page_queue(monkeypatch) -> None:
    """Bulk skip uses the uncapped catalog transition, not the queue listing."""
    monkeypatch.setattr(api_mod, "skip_all_dead_letter_jobs", lambda: 55)
    monkeypatch.setattr(
        api_mod,
        "list_queue_jobs",
        lambda **kw: pytest.fail("bulk skip should not list paginated jobs"),
    )
    monkeypatch.setattr(
        api_mod, "count_session_jobs_by_status", lambda: {"dead_letter": 0}
    )

    result = api_skip_all_dead_letter()
    assert result["skipped"] == 55


# ---------------------------------------------------------------------------
# api_connect_list / api_connect
# ---------------------------------------------------------------------------


def test_api_connect_list(monkeypatch, tmp_path) -> None:
    """api_connect_list returns platforms from registry."""
    cfg = make_config(tmp_path)
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(
        api_mod,
        "list_platforms",
        lambda path: [{"name": "claude", "connected": True}],
    )

    result = api_connect_list()
    assert len(result) == 1
    assert result[0]["name"] == "claude"


def test_api_connect(monkeypatch, tmp_path) -> None:
    """api_connect calls connect_platform with correct args."""
    cfg = make_config(tmp_path)
    monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(
        api_mod,
        "connect_platform",
        lambda platforms_path, platform, custom_path=None: {
            "name": platform,
            "connected_at": "2026-03-20T10:00:00+00:00",
            "session_count": 3,
            "exists": True,
            "status": "connected",
        },
    )

    result = api_connect("claude", "/custom/path")
    assert result["name"] == "claude"
    assert result["status"] == "connected"
    assert result["session_count"] == 3


# ---------------------------------------------------------------------------
# api_up / api_down / is_docker_container_running
# ---------------------------------------------------------------------------


def test_api_up_docker_not_available(monkeypatch) -> None:
    """api_up returns error when Docker is not running."""
    monkeypatch.setattr(docker_mod, "docker_available", lambda: False)

    result = docker_mod.api_up()
    assert "error" in result
    assert "Docker" in result["error"]


def test_api_up_build_local_no_dockerfile(monkeypatch) -> None:
    """api_up returns error when build_local=True but no Dockerfile found."""
    monkeypatch.setattr(docker_mod, "docker_available", lambda: True)
    monkeypatch.setattr(docker_mod, "_find_package_root", lambda: None)
    monkeypatch.setattr(docker_mod, "reload_config", lambda: make_config(Path("/tmp")))

    result = docker_mod.api_up(build_local=True)
    assert "error" in result
    assert "Dockerfile" in result["error"]


def test_api_up_compose_timeout(monkeypatch, tmp_path) -> None:
    """api_up returns error when docker compose times out."""
    monkeypatch.setattr(docker_mod, "docker_available", lambda: True)
    monkeypatch.setattr(docker_mod, "reload_config", lambda: make_config(tmp_path))
    monkeypatch.setattr(docker_mod, "COMPOSE_PATH", tmp_path / "docker-compose.yml")

    def raise_timeout(*args, **kwargs):
        """Simulate compose timeout."""
        raise subprocess.TimeoutExpired(cmd="docker compose up", timeout=300)

    monkeypatch.setattr(subprocess, "run", raise_timeout)

    result = docker_mod.api_up()
    assert "error" in result
    assert "timed out" in result["error"]


def test_api_up_compose_failure(monkeypatch, tmp_path) -> None:
    """api_up returns error when docker compose up fails."""
    monkeypatch.setattr(docker_mod, "docker_available", lambda: True)
    monkeypatch.setattr(docker_mod, "reload_config", lambda: make_config(tmp_path))
    monkeypatch.setattr(docker_mod, "COMPOSE_PATH", tmp_path / "docker-compose.yml")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: MagicMock(returncode=1))

    result = docker_mod.api_up()
    assert "error" in result
    assert "failed" in result["error"]


def test_api_up_success(monkeypatch, tmp_path) -> None:
    """api_up returns success when compose starts cleanly."""
    monkeypatch.setattr(docker_mod, "docker_available", lambda: True)
    monkeypatch.setattr(docker_mod, "reload_config", lambda: make_config(tmp_path))
    compose_path = tmp_path / "docker-compose.yml"
    monkeypatch.setattr(docker_mod, "COMPOSE_PATH", compose_path)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: MagicMock(returncode=0))

    result = docker_mod.api_up()
    assert result["status"] == "started"
    assert compose_path.exists()


def test_generate_compose_mounts_effective_global_data_dir(
    monkeypatch, tmp_path
) -> None:
    """Compose generation should mount the configured global data dir, not ~/.lerim."""
    cfg = make_config(tmp_path / "custom-root")
    monkeypatch.setattr(docker_mod, "reload_config", lambda: cfg)

    compose = docker_mod._generate_compose_yml()

    assert str(cfg.global_data_dir) in compose
    assert f"{Path.home()}/.lerim:{Path.home()}/.lerim" not in compose


def test_api_down_no_compose_file(monkeypatch, tmp_path) -> None:
    """api_down returns not_running when compose file does not exist."""
    monkeypatch.setattr(
        docker_mod, "COMPOSE_PATH", tmp_path / "nonexistent-compose.yml"
    )

    result = docker_mod.api_down()
    assert result["status"] == "not_running"


def test_api_down_success(monkeypatch, tmp_path) -> None:
    """api_down returns stopped after successful compose down."""
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text("services: {}", encoding="utf-8")
    monkeypatch.setattr(docker_mod, "COMPOSE_PATH", compose_path)
    monkeypatch.setattr(docker_mod, "is_docker_container_running", lambda: True)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: MagicMock(returncode=0),
    )

    result = docker_mod.api_down()
    assert result["status"] == "stopped"
    assert result["was_running"] is True


def test_api_down_failure(monkeypatch, tmp_path) -> None:
    """api_down returns error when compose down fails."""
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text("services: {}", encoding="utf-8")
    monkeypatch.setattr(docker_mod, "COMPOSE_PATH", compose_path)
    monkeypatch.setattr(docker_mod, "is_docker_container_running", lambda: False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: MagicMock(returncode=1, stderr="compose error"),
    )

    result = docker_mod.api_down()
    assert "error" in result


def test_is_docker_container_running_false_when_compose_service_is_not_running(
    monkeypatch, tmp_path
) -> None:
    """is_docker_container_running returns False when compose reports no service."""
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text("services: {}", encoding="utf-8")
    monkeypatch.setattr(docker_mod, "COMPOSE_PATH", compose_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: MagicMock(returncode=0, stdout=""),
    )

    result = docker_mod.is_docker_container_running()
    assert result is False


def test_is_server_healthy_unreachable(monkeypatch, tmp_path) -> None:
    """is_server_healthy returns False when the health endpoint is unreachable."""
    cfg = make_config(tmp_path)
    monkeypatch.setattr(docker_mod, "get_config", lambda: cfg)

    def raise_url_error(*args, **kwargs):
        """Simulate unreachable server."""
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", raise_url_error)

    result = docker_mod.is_server_healthy()
    assert result is False


# ---------------------------------------------------------------------------
# _ollama_models / _is_ollama_reachable / _load_model / _unload_model
# ---------------------------------------------------------------------------


def test_is_ollama_reachable_connection_error(monkeypatch) -> None:
    """_is_ollama_reachable returns False on connection error."""
    import httpx

    def raise_connect(*args, **kwargs):
        """Simulate connection failure."""
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", raise_connect)

    result = api_mod._is_ollama_reachable("http://127.0.0.1:11434")
    assert result is False


def test_is_ollama_reachable_timeout(monkeypatch) -> None:
    """_is_ollama_reachable returns False on timeout."""
    import httpx

    def raise_timeout(*args, **kwargs):
        """Simulate timeout."""
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "get", raise_timeout)

    result = api_mod._is_ollama_reachable("http://127.0.0.1:11434")
    assert result is False


# ---------------------------------------------------------------------------
# parse_duration edge cases
# ---------------------------------------------------------------------------


def test_parse_duration_single_char_raises() -> None:
    """Single character input raises ValueError."""
    with pytest.raises(ValueError, match="duration must be"):
        api_mod.parse_duration_to_seconds("s")


def test_parse_duration_non_digit_amount_raises() -> None:
    """Non-digit amount raises ValueError."""
    with pytest.raises(ValueError, match="duration must be"):
        api_mod.parse_duration_to_seconds("abcs")


def test_parse_duration_empty_raises() -> None:
    """Empty string raises ValueError."""
    with pytest.raises(ValueError, match="duration must be"):
        api_mod.parse_duration_to_seconds("")


# ---------------------------------------------------------------------------
# _find_package_root
# ---------------------------------------------------------------------------


def test_find_package_root_returns_path_or_none() -> None:
    """_find_package_root returns a Path or None without crashing."""
    result = docker_mod._find_package_root()
    assert result is None or isinstance(result, Path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextmanager
def _noop_lifecycle(config):
    """No-op context manager replacing ollama_lifecycle in tests."""
    yield
