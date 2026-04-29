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
from lerim.server import docker_runtime
from lerim.server.docker_runtime import (
    GHCR_IMAGE,
    LOCAL_IMAGE,
    RUNTIME_SOURCE_ENV,
    _generate_compose_yml,
    api_up,
)
from tests.helpers import make_config


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Monkeypatch reload_config so compose generation uses a temp config."""
    cfg = make_config(tmp_path)
    monkeypatch.setattr("lerim.server.docker_runtime.reload_config", lambda: cfg)


def test_default_compose_uses_ghcr_image() -> None:
    """Default compose (build_local=False) emits an image directive with GHCR."""
    content = _generate_compose_yml(build_local=False)
    assert f"image: {GHCR_IMAGE}:" in content
    assert "build:" not in content


def test_build_local_uses_build_directive(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_local=True emits a local image tag and build directive."""
    fake_root = Path("/fake/lerim-root")
    monkeypatch.setattr(
        "lerim.server.docker_runtime._find_package_root", lambda: fake_root
    )
    content = _generate_compose_yml(build_local=True)
    assert f"image: {LOCAL_IMAGE}" in content
    assert f"build: {fake_root}" in content
    assert f"{RUNTIME_SOURCE_ENV}=local-build" in content


def test_build_local_no_dockerfile_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_local=True raises FileNotFoundError when Dockerfile is missing."""
    monkeypatch.setattr("lerim.server.docker_runtime._find_package_root", lambda: None)
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
    monkeypatch.setattr("lerim.server.docker_runtime.docker_available", lambda: False)
    result = api_up()
    assert "error" in result
    assert "Docker" in result["error"]


def test_api_up_build_local_no_dockerfile(monkeypatch: pytest.MonkeyPatch) -> None:
    """api_up(build_local=True) returns error dict when Dockerfile is missing."""
    monkeypatch.setattr("lerim.server.docker_runtime.docker_available", lambda: True)
    monkeypatch.setattr("lerim.server.docker_runtime._find_package_root", lambda: None)
    result = api_up(build_local=True)
    assert "error" in result
    assert "Dockerfile" in result["error"]


def test_api_up_build_local_forces_fresh_recreate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Local builds should rebuild and recreate the service from the local image."""
    fake_root = tmp_path / "source"
    fake_root.mkdir()
    compose_path = tmp_path / "docker-compose.yml"
    calls: list[list[str]] = []

    class Result:
        returncode = 0

    monkeypatch.setattr("lerim.server.docker_runtime.docker_available", lambda: True)
    monkeypatch.setattr(
        "lerim.server.docker_runtime._find_package_root", lambda: fake_root
    )
    monkeypatch.setattr("lerim.server.docker_runtime.COMPOSE_PATH", compose_path)
    monkeypatch.setattr(
        "lerim.server.docker_runtime.subprocess.run",
        lambda cmd, **kwargs: calls.append(list(cmd)) or Result(),
    )

    result = api_up(build_local=True)

    assert result["runtime_source"] == "local-build"
    assert result["runtime_image"] == LOCAL_IMAGE
    assert calls == [
        [
            "docker",
            "compose",
            "-f",
            str(compose_path),
            "up",
            "-d",
            "--build",
            "--force-recreate",
        ]
    ]


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


def test_compose_routes_library_caches_to_global_lerim_dir() -> None:
    """Container cache writes should stay under the mounted global data dir."""
    content = _generate_compose_yml(build_local=False)
    cfg = docker_runtime.reload_config()
    cache_dir = cfg.global_data_dir / "cache"
    models_dir = cfg.global_data_dir / "models"
    assert f"XDG_CACHE_HOME={cache_dir}" in content
    assert f"FASTEMBED_CACHE_PATH={models_dir / 'embeddings'}" in content
    assert f"HF_HOME={models_dir / 'huggingface'}" in content
    assert f"HF_HUB_CACHE={models_dir / 'huggingface' / 'hub'}" in content


def test_compose_enables_mlflow_when_configured(tmp_path, monkeypatch) -> None:
    """Docker server should inherit persistent MLflow tracing config."""
    from dataclasses import replace

    cfg = replace(make_config(tmp_path), mlflow_enabled=True)
    monkeypatch.setattr("lerim.server.docker_runtime.reload_config", lambda: cfg)

    content = _generate_compose_yml(build_local=False)

    assert "LERIM_MLFLOW=true" in content


def test_compose_mounts_global_state_agents_and_project_roots(
    tmp_path, monkeypatch
) -> None:
    """Compose should mount global state, agent dirs, and registered project roots."""
    from dataclasses import replace

    cfg = make_config(tmp_path)
    project_root = tmp_path / "myproject"
    project_root.mkdir()
    cfg = replace(cfg, projects={"test": str(project_root)})
    monkeypatch.setattr("lerim.server.docker_runtime.reload_config", lambda: cfg)

    content = _generate_compose_yml(build_local=False)
    assert f"{cfg.global_data_dir}:{cfg.global_data_dir}" in content
    assert f"{project_root}:{project_root}:ro" in content
    assert str(project_root / ".lerim") not in content


def test_compose_mounts_explicit_config_outside_data_dir(tmp_path, monkeypatch) -> None:
    """Compose should preserve custom LERIM_CONFIG installs inside Docker."""
    from dataclasses import replace

    data_dir = tmp_path / "data-root"
    config_path = tmp_path / "config-root" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[data]\n", encoding="utf-8")
    cfg = replace(make_config(data_dir), global_data_dir=data_dir)
    monkeypatch.setattr("lerim.server.docker_runtime.reload_config", lambda: cfg)
    monkeypatch.setattr(
        "lerim.server.docker_runtime.get_user_config_path", lambda: config_path
    )
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))

    content = _generate_compose_yml(build_local=False)
    resolved = str(config_path.resolve())
    assert f"{resolved}:{resolved}:ro" in content
    assert f"LERIM_CONFIG={resolved}" in content


def test_compose_mounts_env_file_outside_data_dir(tmp_path, monkeypatch) -> None:
    """Compose should mount the active env file when it is not in global state."""
    from dataclasses import replace

    data_dir = tmp_path / "data-root"
    env_path = tmp_path / "config-root" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("OPENROUTER_API_KEY=secret\n", encoding="utf-8")
    cfg = replace(make_config(data_dir), global_data_dir=data_dir)
    monkeypatch.setattr("lerim.server.docker_runtime.reload_config", lambda: cfg)
    monkeypatch.setattr(
        "lerim.server.docker_runtime.get_user_env_path", lambda: env_path
    )

    content = _generate_compose_yml(build_local=False)
    resolved = str(env_path.resolve())
    assert f"{resolved}:{resolved}:ro" in content


def test_compose_does_not_set_project_local_working_dir(tmp_path, monkeypatch) -> None:
    """Compose should keep the container working directory on the global runtime path."""
    from dataclasses import replace

    cfg = make_config(tmp_path)
    project_root = tmp_path / "myproject"
    project_root.mkdir()
    cfg = replace(cfg, projects={"test": str(project_root)})
    monkeypatch.setattr("lerim.server.docker_runtime.reload_config", lambda: cfg)

    content = _generate_compose_yml(build_local=False)
    assert "working_dir:" not in content
    assert f"{project_root}:{project_root}:ro" in content
    assert str(project_root / ".lerim") not in content


def test_compose_does_not_pin_container_name() -> None:
    """Compose should let Docker Compose manage container naming."""
    content = _generate_compose_yml(build_local=False)
    assert "container_name:" not in content


def test_compose_mounts_connected_platform_dirs_read_only(
    tmp_path, monkeypatch
) -> None:
    """Connected platform session directories should be mounted read-only."""
    from dataclasses import replace

    cfg = make_config(tmp_path)
    agent_path = str(tmp_path / "sessions")
    Path(agent_path).mkdir(parents=True)
    cfg.platforms_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.platforms_path.write_text(
        json.dumps(
            {
                "platforms": {
                    "claude": {
                        "path": agent_path,
                        "connected_at": "2026-01-01T00:00:00+00:00",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    ignored_agent_config_path = tmp_path / "ignored-agent-config"
    cfg = replace(cfg, agents={"claude": str(ignored_agent_config_path)})
    monkeypatch.setattr("lerim.server.docker_runtime.reload_config", lambda: cfg)

    content = _generate_compose_yml(build_local=False)
    assert f"{agent_path}:{agent_path}:ro" in content
    assert str(ignored_agent_config_path) not in content
