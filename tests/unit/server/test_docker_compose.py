"""Tests for Docker compose generation and GHCR image publishing support.

Verifies that _generate_compose_yml produces correct image/build directives,
that API key values never leak into compose content, and that api_up handles
Docker-unavailable and missing-Dockerfile scenarios gracefully.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lerim import __version__
from lerim.config.settings import reload_config as settings_reload_config
from lerim.server.api import (
    GHCR_IMAGE,
    _generate_compose_yml,
    api_up,
)
from tests.helpers import make_config


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Monkeypatch reload_config so compose generation uses a temp config."""
    cfg = make_config(tmp_path)
    monkeypatch.setattr("lerim.server.api.reload_config", lambda: cfg)


def test_default_compose_uses_ghcr_image() -> None:
    """Default compose (build_local=False) emits an image directive with GHCR."""
    content = _generate_compose_yml(build_local=False)
    assert f"image: {GHCR_IMAGE}:" in content
    assert "build:" not in content


def test_build_local_uses_build_directive(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_local=True emits a build directive instead of an image directive."""
    fake_root = Path("/fake/lerim-root")
    monkeypatch.setattr("lerim.server.api._find_package_root", lambda: fake_root)
    content = _generate_compose_yml(build_local=True)
    assert f"build: {fake_root}" in content
    assert "image:" not in content


def test_build_local_no_dockerfile_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_local=True raises FileNotFoundError when Dockerfile is missing."""
    monkeypatch.setattr("lerim.server.api._find_package_root", lambda: None)
    with pytest.raises(FileNotFoundError, match="Cannot find Dockerfile"):
        _generate_compose_yml(build_local=True)


def test_no_api_key_values_in_compose(monkeypatch: pytest.MonkeyPatch) -> None:
    """API key values from the environment must not appear in compose content."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-secret-key-12345")
    content = _generate_compose_yml(build_local=False)
    assert "sk-secret-key-12345" not in content


def test_version_tag_matches_dunder_version() -> None:
    """The image tag in the compose file matches the package __version__."""
    content = _generate_compose_yml(build_local=False)
    expected = f"{GHCR_IMAGE}:{__version__}"
    assert expected in content


def test_api_up_docker_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """api_up returns an error dict when Docker is not available."""
    monkeypatch.setattr("lerim.server.api.docker_available", lambda: False)
    result = api_up()
    assert "error" in result
    assert "Docker" in result["error"]


def test_api_up_build_local_no_dockerfile(monkeypatch: pytest.MonkeyPatch) -> None:
    """api_up(build_local=True) returns error dict when Dockerfile is missing."""
    monkeypatch.setattr("lerim.server.api.docker_available", lambda: True)
    monkeypatch.setattr("lerim.server.api._find_package_root", lambda: None)
    result = api_up(build_local=True)
    assert "error" in result
    assert "Dockerfile" in result["error"]


# -- Container hardening tests --


def test_compose_has_read_only_root() -> None:
    """Container should have read-only root filesystem."""
    content = _generate_compose_yml(build_local=False)
    assert "read_only: true" in content


def test_compose_runs_as_host_uid_gid() -> None:
    """Container should write ~/.lerim as the host user, not root."""
    content = _generate_compose_yml(build_local=False)
    assert f'user: "{os.getuid()}:{os.getgid()}"' in content


def test_compose_drops_all_capabilities() -> None:
    """Container should drop all Linux capabilities."""
    content = _generate_compose_yml(build_local=False)
    assert "cap_drop:" in content
    assert "- ALL" in content


def test_compose_has_no_new_privileges() -> None:
    """Container should prevent privilege escalation."""
    content = _generate_compose_yml(build_local=False)
    assert "no-new-privileges:true" in content


def test_compose_has_pids_limit() -> None:
    """Container should have a PID limit to prevent fork bombs."""
    content = _generate_compose_yml(build_local=False)
    assert "pids_limit:" in content


def test_compose_has_memory_limit() -> None:
    """Container should have a memory limit."""
    content = _generate_compose_yml(build_local=False)
    assert "mem_limit:" in content


def test_compose_has_tmpfs() -> None:
    """Container should have tmpfs for writable /tmp."""
    content = _generate_compose_yml(build_local=False)
    assert "tmpfs:" in content
    assert "/tmp:" in content


def test_compose_mounts_only_global_lerim_and_agent_dirs(tmp_path, monkeypatch) -> None:
    """Compose should mount global Lerim state, not per-project local folders."""
    from dataclasses import replace
    cfg = make_config(tmp_path)
    cfg = replace(cfg, projects={"test": str(tmp_path / "myproject")})
    monkeypatch.setattr("lerim.server.api.reload_config", lambda: cfg)

    content = _generate_compose_yml(build_local=False)
    assert f"{Path.home() / '.lerim'}:{Path.home() / '.lerim'}" in content
    assert str(tmp_path / "myproject" / ".lerim") not in content
    assert str(tmp_path / "myproject") not in content


def test_compose_does_not_set_project_local_working_dir(tmp_path, monkeypatch) -> None:
    """Compose should keep the container working directory on the global runtime path."""
    from dataclasses import replace

    cfg = make_config(tmp_path)
    cfg = replace(cfg, projects={"test": str(tmp_path / "myproject")})
    monkeypatch.setattr("lerim.server.api.reload_config", lambda: cfg)

    content = _generate_compose_yml(build_local=False)
    assert "working_dir:" not in content
    assert str(tmp_path / "myproject" / ".lerim") not in content


def test_compose_does_not_pin_container_name() -> None:
    """Compose should let Docker Compose manage container naming."""
    content = _generate_compose_yml(build_local=False)
    assert "container_name:" not in content


def test_compose_agent_dirs_read_only(tmp_path, monkeypatch) -> None:
    """Agent session directories should be mounted read-only."""
    from dataclasses import replace
    cfg = make_config(tmp_path)
    agent_path = str(tmp_path / "sessions")
    cfg = replace(cfg, agents={"claude": agent_path})
    monkeypatch.setattr("lerim.server.api.reload_config", lambda: cfg)

    content = _generate_compose_yml(build_local=False)
    assert f"{agent_path}:{agent_path}:ro" in content


def test_compose_mounts_connected_platform_dirs_from_platforms_registry(
    tmp_path, monkeypatch
) -> None:
    """Compose should still mount connected platform paths when [agents] is empty."""
    monkeypatch.setattr(
        "lerim.config.settings.USER_CONFIG_PATH", tmp_path / "user-config.toml"
    )
    sessions_path = tmp_path / "codex-sessions"
    sessions_path.mkdir(parents=True)
    (tmp_path / "platforms.json").write_text(
        json.dumps(
            {
                "platforms": {
                    "codex": {
                        "path": str(sessions_path),
                        "connected_at": "2026-04-22T00:00:00+00:00",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[data]\ndir = "{tmp_path}"\n', encoding="utf-8")
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    monkeypatch.setattr("lerim.server.api.reload_config", settings_reload_config)

    content = _generate_compose_yml(build_local=False)
    resolved = str(sessions_path.resolve())
    assert f"{resolved}:{resolved}:ro" in content
