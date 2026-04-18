"""Unit tests for api.py functions: detect_agents, write_init_config,
api_sync, api_maintain, api_health, api_status, api_project_*,
api_retry_all_dead_letter, api_skip_all_dead_letter, looks_like_auth_error,
and docker_available.

Focuses on functions testable without Docker/Ollama by mocking the runtime,
filesystem, and subprocess calls.
"""

from __future__ import annotations

from contextlib import contextmanager
import subprocess
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import lerim.server.api as api_mod
from lerim.server.api import (
	AGENT_DEFAULT_PATHS,
	api_connect,
	api_connect_list,
	api_health,
	api_maintain,
	api_project_add,
	api_project_list,
	api_project_remove,
	api_retry_all_dead_letter,
	api_skip_all_dead_letter,
	api_status,
	api_sync,
	detect_agents,
	docker_available,
	looks_like_auth_error,
	write_init_config,
)
from lerim.server.daemon import SyncSummary
from tests.helpers import make_config


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
	monkeypatch.setattr(api_mod, "count_unscoped_sessions_by_agent", lambda projects: {})
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
	for name in AGENT_DEFAULT_PATHS:
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
	monkeypatch.setattr(
		api_mod, "save_config_patch", lambda patch: saved.append(patch)
	)
	monkeypatch.setattr(api_mod, "USER_CONFIG_PATH", tmp_path / "config.toml")

	selected = {"claude": "/home/user/.claude/projects", "codex": "/home/user/.codex"}
	write_init_config(selected)

	assert len(saved) == 1
	assert saved[0] == {"agents": selected}


def test_write_init_config_returns_path(monkeypatch, tmp_path) -> None:
	"""write_init_config returns the USER_CONFIG_PATH."""
	expected_path = tmp_path / "config.toml"
	monkeypatch.setattr(api_mod, "save_config_patch", lambda patch: None)
	monkeypatch.setattr(api_mod, "USER_CONFIG_PATH", expected_path)

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
	assert not looks_like_auth_error("3 learnings extracted")
	assert not looks_like_auth_error("")
	assert not looks_like_auth_error(None)


# ---------------------------------------------------------------------------
# docker_available
# ---------------------------------------------------------------------------


def test_docker_available_true(monkeypatch) -> None:
	"""docker_available returns True when docker info succeeds."""
	monkeypatch.setattr(
		subprocess, "run",
		lambda cmd, **kw: MagicMock(returncode=0),
	)
	assert docker_available() is True


def test_docker_available_false_nonzero(monkeypatch) -> None:
	"""docker_available returns False when docker info fails."""
	monkeypatch.setattr(
		subprocess, "run",
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
# api_sync
# ---------------------------------------------------------------------------


def test_api_sync_returns_code_and_summary(monkeypatch, tmp_path) -> None:
	"""api_sync calls run_sync_once and returns code + summary dict."""
	cfg = make_config(tmp_path)
	monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

	summary = SyncSummary(
		indexed_sessions=2,
		extracted_sessions=1,
		skipped_sessions=0,
		failed_sessions=0,
		run_ids=["r1"],
		cost_usd=0.005,
	)
	monkeypatch.setattr(api_mod, "run_sync_once", lambda **kw: (0, summary))
	monkeypatch.setattr(api_mod, "ollama_lifecycle", _noop_lifecycle)
	_stub_status_catalog(monkeypatch)

	result = api_sync(agent="claude", window="7d")

	assert result["code"] == 0
	assert result["extracted_sessions"] == 1


def test_api_sync_dry_run(monkeypatch, tmp_path) -> None:
	"""api_sync passes dry_run flag through to run_sync_once."""
	cfg = make_config(tmp_path)
	monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

	captured_kwargs: dict[str, Any] = {}

	def fake_sync(**kwargs):
		"""Capture sync arguments."""
		captured_kwargs.update(kwargs)
		return (0, SyncSummary(0, 0, 0, 0, []))

	monkeypatch.setattr(api_mod, "run_sync_once", fake_sync)
	monkeypatch.setattr(api_mod, "ollama_lifecycle", _noop_lifecycle)
	_stub_status_catalog(monkeypatch)

	api_sync(dry_run=True)

	assert captured_kwargs["dry_run"] is True


def test_api_sync_force_flag(monkeypatch, tmp_path) -> None:
	"""api_sync passes force flag through to run_sync_once."""
	cfg = make_config(tmp_path)
	monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

	captured_kwargs: dict[str, Any] = {}

	def fake_sync(**kwargs):
		"""Capture sync arguments."""
		captured_kwargs.update(kwargs)
		return (0, SyncSummary(0, 0, 0, 0, []))

	monkeypatch.setattr(api_mod, "run_sync_once", fake_sync)
	monkeypatch.setattr(api_mod, "ollama_lifecycle", _noop_lifecycle)
	_stub_status_catalog(monkeypatch)

	api_sync(force=True)

	assert captured_kwargs["force"] is True


def test_api_ask_includes_debug_when_verbose(monkeypatch, tmp_path) -> None:
	"""api_ask should pass verbose through to runtime ask and expose debug payload."""
	cfg = make_config(tmp_path)
	monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
	monkeypatch.setattr(api_mod, "_resolve_selected_projects", lambda **kw: [])

	class _FakeRuntime:
		def ask(self, question, project_ids=None, repo_root=None, include_debug=False):
			assert question == "how many records"
			assert include_debug is True
			return (
				"3 records",
				"sid-1",
				0.0,
				{"tool_calls": [{"tool_name": "context_query"}], "tool_results": []},
			)

	monkeypatch.setattr(api_mod, "LerimRuntime", lambda: _FakeRuntime())

	payload = api_mod.api_ask("how many records", verbose=True)
	assert payload["answer"] == "3 records"
	assert payload["debug"]["tool_calls"][0]["tool_name"] == "context_query"


def test_api_sync_includes_queue_health_warning(monkeypatch, tmp_path) -> None:
	"""Sync API response surfaces degraded queue warning hints."""
	cfg = make_config(tmp_path)
	monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
	monkeypatch.setattr(
		api_mod, "run_sync_once", lambda **kw: (0, SyncSummary(0, 0, 0, 0, []))
	)
	monkeypatch.setattr(api_mod, "ollama_lifecycle", _noop_lifecycle)
	monkeypatch.setattr(
		api_mod,
		"queue_health_snapshot",
		lambda: {"degraded": True, "advice": "run `lerim queue --failed`"},
	)
	result = api_sync()
	assert result["queue_health"]["degraded"] is True
	assert "warning" in result


# ---------------------------------------------------------------------------
# api_maintain
# ---------------------------------------------------------------------------


def test_api_maintain_returns_code_and_payload(monkeypatch, tmp_path) -> None:
	"""api_maintain calls run_maintain_once and returns code + payload."""
	cfg = make_config(tmp_path)
	monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
	monkeypatch.setattr(
		api_mod, "run_maintain_once",
		lambda **kw: (0, {"projects": {"test": {"counts": {}}}}),
	)
	monkeypatch.setattr(api_mod, "ollama_lifecycle", _noop_lifecycle)
	_stub_status_catalog(monkeypatch)

	result = api_maintain()

	assert result["code"] == 0
	assert "projects" in result


def test_api_maintain_dry_run(monkeypatch, tmp_path) -> None:
	"""api_maintain passes dry_run through."""
	cfg = make_config(tmp_path)
	monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

	captured: dict[str, Any] = {}

	def fake_maintain(**kwargs):
		"""Capture maintain arguments."""
		captured.update(kwargs)
		return (0, {"dry_run": True})

	monkeypatch.setattr(api_mod, "run_maintain_once", fake_maintain)
	monkeypatch.setattr(api_mod, "ollama_lifecycle", _noop_lifecycle)
	_stub_status_catalog(monkeypatch)

	api_maintain(dry_run=True)

	assert captured["dry_run"] is True


def test_api_maintain_includes_queue_health_warning(monkeypatch, tmp_path) -> None:
	"""Maintain API response surfaces degraded queue warning hints."""
	cfg = make_config(tmp_path)
	monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
	monkeypatch.setattr(
		api_mod, "run_maintain_once", lambda **kw: (0, {"projects": {}})
	)
	monkeypatch.setattr(api_mod, "ollama_lifecycle", _noop_lifecycle)
	monkeypatch.setattr(
		api_mod,
		"queue_health_snapshot",
		lambda: {"degraded": True, "advice": "run `lerim queue --failed`"},
	)
	result = api_maintain()
	assert result["queue_health"]["degraded"] is True
	assert "warning" in result


# ---------------------------------------------------------------------------
# api_status
# ---------------------------------------------------------------------------


def test_api_status_returns_expected_keys(monkeypatch, tmp_path) -> None:
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
		api_mod, "count_session_jobs_by_status",
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
	assert result["sync_window_days"] == cfg.sync_window_days
	assert "queue_health" in result
	assert result["scope"]["strict_project_only"] is True


def test_api_status_no_records(monkeypatch, tmp_path) -> None:
	"""api_status returns 0 when no records exist yet."""
	cfg = make_config(tmp_path)
	monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
	monkeypatch.setattr(api_mod, "list_platforms", lambda path: [])
	monkeypatch.setattr(api_mod, "count_fts_indexed", lambda: 0)
	monkeypatch.setattr(
		api_mod, "count_session_jobs_by_status", lambda: {}
	)
	monkeypatch.setattr(api_mod, "latest_service_run", lambda svc: None)
	_stub_status_catalog(monkeypatch)

	result = api_status()
	assert result["record_count"] == 0


def test_api_status_scope_skipped_unscoped_from_latest_sync(monkeypatch, tmp_path) -> None:
	"""Status exposes strict-scope skipped counter from latest sync details."""
	cfg = make_config(tmp_path)
	monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
	monkeypatch.setattr(api_mod, "list_platforms", lambda path: [])
	monkeypatch.setattr(api_mod, "count_fts_indexed", lambda: 0)
	monkeypatch.setattr(api_mod, "count_session_jobs_by_status", lambda: {})
	monkeypatch.setattr(
		api_mod,
		"latest_service_run",
		lambda svc: {"details": {"skipped_unscoped": 7}} if svc == "sync" else None,
	)
	_stub_status_catalog(monkeypatch)
	result = api_status()
	assert result["scope"]["skipped_unscoped"] == 7


# ---------------------------------------------------------------------------
# api_project_list / api_project_add / api_project_remove
# ---------------------------------------------------------------------------


def test_api_project_list_empty(monkeypatch, tmp_path) -> None:
	"""api_project_list returns empty list when no projects registered."""
	cfg = make_config(tmp_path)
	monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

	result = api_project_list()
	assert result == []


def test_api_project_list_with_projects(monkeypatch, tmp_path) -> None:
	"""api_project_list returns project info for registered projects."""
	proj_dir = tmp_path / "myproject"
	proj_dir.mkdir()

	cfg = replace(make_config(tmp_path), projects={"myproject": str(proj_dir)})
	monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

	result = api_project_list()
	assert len(result) == 1
	assert result[0]["name"] == "myproject"
	assert result[0]["exists"] is True
	assert "has_lerim" not in result[0]


def test_api_project_add_registers_project_in_context_db(monkeypatch, tmp_path) -> None:
	"""api_project_add registers project metadata in the global context DB."""
	proj_dir = tmp_path / "newproject"
	proj_dir.mkdir()
	cfg = replace(make_config(tmp_path), projects={})

	saved: list[dict] = []
	monkeypatch.setattr(
		api_mod, "save_config_patch", lambda patch: saved.append(patch)
	)
	monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

	result = api_project_add(str(proj_dir))

	assert result["name"] == "newproject"
	assert result["context_db_path"] == str(cfg.context_db_path)
	assert "project_id" in result
	assert not (proj_dir / ".lerim").exists()
	assert len(saved) == 1
	assert "newproject" in saved[0]["projects"]


def test_api_status_reports_projects_and_unscoped(monkeypatch, tmp_path) -> None:
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
	for path, title in ((project_a, "A record"), (project_b, "B record")):
		identity = api_mod.resolve_project_identity(path)
		store.register_project(identity)
		store.create_record(
			project_id=identity.project_id,
			session_id=None,
			kind="fact",
			title=title,
			body=title,
		)
	monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
	monkeypatch.setattr(api_mod, "list_platforms", lambda path: [])
	monkeypatch.setattr(api_mod, "count_fts_indexed", lambda: 11)
	monkeypatch.setattr(api_mod, "count_session_jobs_by_status", lambda: {"pending": 1})
	monkeypatch.setattr(api_mod, "latest_service_run", lambda svc: None)
	monkeypatch.setattr(api_mod, "queue_health_snapshot", lambda: {"degraded": False, "advice": ""})
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

	result = api_status()
	assert result["record_count"] == 2
	assert len(result["projects"]) == 2
	assert all("indexed_sessions_count" in item for item in result["projects"])
	assert all("latest_session_start_time" in item for item in result["projects"])
	assert result["unscoped_sessions"]["total"] == 4
	assert result["unscoped_sessions"]["by_agent"]["cursor"] == 3


def test_api_project_add_not_a_directory(tmp_path) -> None:
	"""api_project_add returns error for non-directory path."""
	result = api_project_add(str(tmp_path / "nonexistent"))
	assert "error" in result
	assert result["name"] is None


def test_api_project_remove_success(monkeypatch, tmp_path) -> None:
	"""api_project_remove removes project from config."""
	cfg = replace(
		make_config(tmp_path), projects={"myproject": str(tmp_path)}
	)
	monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

	config_file = tmp_path / "user_config.toml"
	config_file.write_text(
		'[projects]\nmyproject = "/tmp/myproject"\n', encoding="utf-8"
	)
	monkeypatch.setattr(api_mod, "USER_CONFIG_PATH", config_file)
	monkeypatch.setattr(api_mod, "_write_config_full", lambda data: None)

	result = api_project_remove("myproject")
	assert result["removed"] is True


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
	dead_jobs = [
		{"run_id": "dl-1"},
		{"run_id": "dl-2"},
		{"run_id": ""},  # empty run_id — skipped
	]
	monkeypatch.setattr(
		api_mod, "list_queue_jobs", lambda **kw: dead_jobs
	)
	monkeypatch.setattr(api_mod, "retry_session_job", lambda rid: True)
	monkeypatch.setattr(
		api_mod, "count_session_jobs_by_status", lambda: {"dead_letter": 0}
	)

	result = api_retry_all_dead_letter()
	assert result["retried"] == 2  # empty run_id skipped


def test_api_skip_all_dead_letter(monkeypatch) -> None:
	"""api_skip_all_dead_letter skips all dead letter jobs."""
	dead_jobs = [{"run_id": "dl-1"}, {"run_id": "dl-2"}]
	monkeypatch.setattr(
		api_mod, "list_queue_jobs", lambda **kw: dead_jobs
	)
	monkeypatch.setattr(api_mod, "skip_session_job", lambda rid: True)
	monkeypatch.setattr(
		api_mod, "count_session_jobs_by_status", lambda: {"dead_letter": 0}
	)

	result = api_skip_all_dead_letter()
	assert result["skipped"] == 2


def test_api_retry_all_dead_letter_partial_failure(monkeypatch) -> None:
	"""api_retry_all_dead_letter counts only successful retries."""
	dead_jobs = [{"run_id": "dl-1"}, {"run_id": "dl-2"}]
	monkeypatch.setattr(
		api_mod, "list_queue_jobs", lambda **kw: dead_jobs
	)
	# First succeeds, second fails
	retries = iter([True, False])
	monkeypatch.setattr(api_mod, "retry_session_job", lambda rid: next(retries))
	monkeypatch.setattr(
		api_mod, "count_session_jobs_by_status", lambda: {"dead_letter": 1}
	)

	result = api_retry_all_dead_letter()
	assert result["retried"] == 1


# ---------------------------------------------------------------------------
# api_connect_list / api_connect
# ---------------------------------------------------------------------------


def test_api_connect_list(monkeypatch, tmp_path) -> None:
	"""api_connect_list returns platforms from registry."""
	cfg = make_config(tmp_path)
	monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
	monkeypatch.setattr(
		api_mod, "list_platforms",
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
		api_mod, "connect_platform",
		lambda platforms_path, platform, custom_path=None: {
			"platform": platform, "connected": True
		},
	)

	result = api_connect("claude", "/custom/path")
	assert result["platform"] == "claude"
	assert result["connected"] is True


# ---------------------------------------------------------------------------
# api_up / api_down / is_container_running
# ---------------------------------------------------------------------------


def test_api_up_docker_not_available(monkeypatch) -> None:
	"""api_up returns error when Docker is not running."""
	monkeypatch.setattr(api_mod, "docker_available", lambda: False)

	result = api_mod.api_up()
	assert "error" in result
	assert "Docker" in result["error"]


def test_api_up_build_local_no_dockerfile(monkeypatch) -> None:
	"""api_up returns error when build_local=True but no Dockerfile found."""
	monkeypatch.setattr(api_mod, "docker_available", lambda: True)
	monkeypatch.setattr(api_mod, "_find_package_root", lambda: None)
	monkeypatch.setattr(api_mod, "reload_config", lambda: make_config(Path("/tmp")))

	result = api_mod.api_up(build_local=True)
	assert "error" in result
	assert "Dockerfile" in result["error"]


def test_api_up_compose_timeout(monkeypatch, tmp_path) -> None:
	"""api_up returns error when docker compose times out."""
	monkeypatch.setattr(api_mod, "docker_available", lambda: True)
	monkeypatch.setattr(api_mod, "reload_config", lambda: make_config(tmp_path))
	monkeypatch.setattr(api_mod, "COMPOSE_PATH", tmp_path / "docker-compose.yml")

	def raise_timeout(*args, **kwargs):
		"""Simulate compose timeout."""
		raise subprocess.TimeoutExpired(cmd="docker compose up", timeout=300)

	monkeypatch.setattr(subprocess, "run", raise_timeout)

	result = api_mod.api_up()
	assert "error" in result
	assert "timed out" in result["error"]


def test_api_up_compose_failure(monkeypatch, tmp_path) -> None:
	"""api_up returns error when docker compose up fails."""
	monkeypatch.setattr(api_mod, "docker_available", lambda: True)
	monkeypatch.setattr(api_mod, "reload_config", lambda: make_config(tmp_path))
	monkeypatch.setattr(api_mod, "COMPOSE_PATH", tmp_path / "docker-compose.yml")
	monkeypatch.setattr(
		subprocess, "run", lambda cmd, **kw: MagicMock(returncode=1)
	)

	result = api_mod.api_up()
	assert "error" in result
	assert "failed" in result["error"]


def test_api_up_success(monkeypatch, tmp_path) -> None:
	"""api_up returns success when compose starts cleanly."""
	monkeypatch.setattr(api_mod, "docker_available", lambda: True)
	monkeypatch.setattr(api_mod, "reload_config", lambda: make_config(tmp_path))
	compose_path = tmp_path / "docker-compose.yml"
	monkeypatch.setattr(api_mod, "COMPOSE_PATH", compose_path)
	monkeypatch.setattr(
		subprocess, "run", lambda cmd, **kw: MagicMock(returncode=0)
	)

	result = api_mod.api_up()
	assert result["status"] == "started"
	assert compose_path.exists()


def test_api_down_no_compose_file(monkeypatch, tmp_path) -> None:
	"""api_down returns not_running when compose file does not exist."""
	monkeypatch.setattr(
		api_mod, "COMPOSE_PATH", tmp_path / "nonexistent-compose.yml"
	)

	result = api_mod.api_down()
	assert result["status"] == "not_running"


def test_api_down_success(monkeypatch, tmp_path) -> None:
	"""api_down returns stopped after successful compose down."""
	compose_path = tmp_path / "docker-compose.yml"
	compose_path.write_text("services: {}", encoding="utf-8")
	monkeypatch.setattr(api_mod, "COMPOSE_PATH", compose_path)
	monkeypatch.setattr(api_mod, "is_container_running", lambda: True)
	monkeypatch.setattr(
		subprocess, "run",
		lambda cmd, **kw: MagicMock(returncode=0),
	)

	result = api_mod.api_down()
	assert result["status"] == "stopped"
	assert result["was_running"] is True


def test_api_down_failure(monkeypatch, tmp_path) -> None:
	"""api_down returns error when compose down fails."""
	compose_path = tmp_path / "docker-compose.yml"
	compose_path.write_text("services: {}", encoding="utf-8")
	monkeypatch.setattr(api_mod, "COMPOSE_PATH", compose_path)
	monkeypatch.setattr(api_mod, "is_container_running", lambda: False)
	monkeypatch.setattr(
		subprocess, "run",
		lambda cmd, **kw: MagicMock(returncode=1, stderr="compose error"),
	)

	result = api_mod.api_down()
	assert "error" in result


def test_is_container_running_unreachable(monkeypatch, tmp_path) -> None:
	"""is_container_running returns False when health endpoint is unreachable."""
	cfg = make_config(tmp_path)
	monkeypatch.setattr(api_mod, "get_config", lambda: cfg)

	def raise_url_error(*args, **kwargs):
		"""Simulate unreachable container."""
		raise urllib.error.URLError("connection refused")

	monkeypatch.setattr(urllib.request, "urlopen", raise_url_error)

	result = api_mod.is_container_running()
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
	result = api_mod._find_package_root()
	assert result is None or isinstance(result, Path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------



@contextmanager
def _noop_lifecycle(config):
	"""No-op context manager replacing ollama_lifecycle in tests."""
	yield
