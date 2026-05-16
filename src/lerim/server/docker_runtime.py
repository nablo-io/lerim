"""Docker Compose runtime orchestration for the Lerim server."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from lerim import __version__
from lerim.adapters.registry import get_connected_platform_paths
from lerim.config.settings import (
    get_config,
    get_global_data_dir_path,
    get_user_config_path,
    get_user_env_path,
    reload_config,
)


COMPOSE_PATH = get_global_data_dir_path() / "docker-compose.yml"
GHCR_IMAGE = "ghcr.io/lerim-dev/lerim-cli"
LOCAL_IMAGE = "lerim-lerim:local"
RUNTIME_SOURCE_ENV = "LERIM_RUNTIME_SOURCE"
RUNTIME_IMAGE_ENV = "LERIM_RUNTIME_IMAGE"


_API_KEY_ENV_NAMES = (
    "MINIMAX_API_KEY",
    "OPENAI_API_KEY",
    "OPENCODE_API_KEY",
    "OPENROUTER_API_KEY",
    "ZAI_API_KEY",
)


def docker_available() -> bool:
    """Check if Docker is installed and the daemon is running."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _find_package_root() -> Path | None:
    """Locate the Lerim source tree root by walking up from this file."""
    candidate = Path(__file__).resolve().parent
    for _ in range(5):
        if (candidate / "Dockerfile").is_file():
            return candidate
        candidate = candidate.parent
    return None


def _generate_compose_yml(build_local: bool = False) -> str:
    """Generate docker-compose.yml content from current config.

    When *build_local* is True the compose file uses a ``build:`` directive
    pointing at the local source tree (requires a Dockerfile).  Otherwise it
    references the pre-built GHCR image tagged with the current version.
    """
    config = reload_config()
    home = str(Path.home())
    user_spec = f"{os.getuid()}:{os.getgid()}"

    lerim_dir = str(config.global_data_dir)
    volumes: list[str] = []
    mounted_paths: set[str] = set()

    def _add_volume(path: Path | str, *, readonly: bool = False) -> None:
        """Add a same-path bind mount once."""
        resolved = str(Path(path).expanduser().resolve())
        if resolved in mounted_paths:
            return
        mounted_paths.add(resolved)
        suffix = ":ro" if readonly else ""
        volumes.append(f"      - {resolved}:{resolved}{suffix}")

    _add_volume(lerim_dir)

    # If the active config or env file lives outside the global data dir, mount
    # it too. Otherwise custom [data].dir / LERIM_CONFIG installs boot the
    # container with package defaults and ignore the mounted state.
    global_data_dir = config.global_data_dir.expanduser().resolve()

    def _covered_by_global_data(path: Path) -> bool:
        try:
            path.expanduser().resolve().relative_to(global_data_dir)
        except ValueError:
            return False
        return True

    active_config_path = get_user_config_path()
    if active_config_path.is_file() and not _covered_by_global_data(active_config_path):
        resolved = str(active_config_path.expanduser().resolve())
        _add_volume(resolved, readonly=True)

    active_env_path = get_user_env_path()
    if active_env_path.is_file() and not _covered_by_global_data(active_env_path):
        resolved = str(active_env_path.expanduser().resolve())
        _add_volume(resolved, readonly=True)

    # Connected platform session dirs (read-only — Lerim reads traces only).
    for _name, platform_path in get_connected_platform_paths(
        config.platforms_path
    ).items():
        _add_volume(platform_path, readonly=True)

    # Registered project roots must be visible at the same absolute paths for
    # project-scoped ingest, status, and curate flows inside Docker.
    for project_path in (config.projects or {}).values():
        resolved = Path(project_path).expanduser().resolve()
        if resolved.exists():
            _add_volume(resolved, readonly=True)

    volumes_block = "\n".join(volumes)
    port = config.server_port

    # Forward API keys by name only — Docker reads values from host env.
    # NEVER write secret values into the compose file.
    env_lines = [
        f"      - HOME={home}",
        f"      - FASTEMBED_CACHE_PATH={lerim_dir}/models/embeddings",
        f"      - XDG_CACHE_HOME={lerim_dir}/cache",
        f"      - HF_HOME={lerim_dir}/models/huggingface",
        f"      - HF_HUB_CACHE={lerim_dir}/models/huggingface/hub",
        f"      - {RUNTIME_SOURCE_ENV}={'local-build' if build_local else 'ghcr'}",
        f"      - {RUNTIME_IMAGE_ENV}={LOCAL_IMAGE if build_local else f'{GHCR_IMAGE}:{__version__}'}",
    ]
    explicit_config = os.environ.get("LERIM_CONFIG")
    if explicit_config:
        resolved_config = str(Path(explicit_config).expanduser().resolve())
        env_lines.append(f"      - LERIM_CONFIG={resolved_config}")
    for key in _API_KEY_ENV_NAMES:
        if os.environ.get(key):
            env_lines.append(f"      - {key}")
    # Make tracing explicit for Docker; config.toml is mounted too, but this
    # keeps generated compose output honest about observability being enabled.
    if config.mlflow_enabled:
        env_lines.append("      - LERIM_MLFLOW=true")
    if build_local:
        pkg_root = _find_package_root()
        if pkg_root is None:
            raise FileNotFoundError(
                "Cannot find Dockerfile in the Lerim source tree. "
                "Use 'lerim up' without --build to pull the GHCR image."
            )
        image_or_build = f"    image: {LOCAL_IMAGE}\n    build: {pkg_root}"
    else:
        image_or_build = f"    image: {GHCR_IMAGE}:{__version__}"
    env_block = "\n".join(env_lines)

    # Resolve seccomp profile path (shipped with the package)
    seccomp_path = Path(__file__).parent / "lerim-seccomp.json"
    seccomp_line = ""
    if seccomp_path.exists():
        seccomp_line = f"\n      - seccomp={seccomp_path}"

    return f"""\
# Auto-generated by lerim up — do not edit manually.
# Regenerated from the active Lerim config on every `lerim up`.
services:
  lerim:
{image_or_build}
    user: "{user_spec}"
    command: ["--host", "0.0.0.0", "--port", "{port}"]
    restart: "no"
    ports:
      - "127.0.0.1:{port}:{port}"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    # Container hardening
    read_only: true
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true{seccomp_line}
    pids_limit: 256
    mem_limit: 2g
    tmpfs:
      - /tmp:size=100M
      - {home}/.codex:size=50M
      - {home}/.config:size=10M
      - /root/.codex:size=50M
      - /root/.config:size=10M
    environment:
{env_block}
    volumes:
{volumes_block}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:{port}/api/health"]
      interval: 30s
      timeout: 5s
      retries: 3
"""


def api_up(build_local: bool = False) -> dict[str, Any]:
    """Generate compose file and start Docker container.

    When *build_local* is True the image is built from the local Dockerfile
    instead of pulling the pre-built GHCR image.  Docker output is streamed
    to stderr in real-time so the user sees pull/build progress.
    """
    if not docker_available():
        return {"error": "Docker is not installed or not running."}

    try:
        compose_content = _generate_compose_yml(build_local=build_local)
    except FileNotFoundError as exc:
        return {"error": str(exc)}

    COMPOSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    COMPOSE_PATH.write_text(compose_content, encoding="utf-8")
    # Owner-only read/write — compose file may reference secret key names.
    COMPOSE_PATH.chmod(0o600)

    cmd = ["docker", "compose", "-f", str(COMPOSE_PATH), "up", "-d"]
    if build_local:
        cmd.extend(["--build", "--force-recreate"])

    try:
        result = subprocess.run(cmd, timeout=300)
    except subprocess.TimeoutExpired:
        return {"error": "Docker compose up timed out after 300 seconds."}
    if result.returncode != 0:
        return {"error": "docker compose up failed"}

    return {
        "status": "started",
        "compose_path": str(COMPOSE_PATH),
        "runtime_source": "local-build" if build_local else "ghcr",
        "runtime_image": LOCAL_IMAGE if build_local else f"{GHCR_IMAGE}:{__version__}",
    }


def api_down() -> dict[str, Any]:
    """Stop Docker container. Reports whether it was actually running."""
    if not COMPOSE_PATH.exists():
        return {"status": "not_running", "message": "No compose file found."}

    was_running = is_docker_container_running()

    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_PATH), "down"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        return {"error": result.stderr.strip() or "docker compose down failed"}
    return {"status": "stopped", "was_running": was_running}


def is_docker_container_running() -> bool:
    """Check whether the Docker Compose service is currently running."""
    if not COMPOSE_PATH.exists():
        return False
    try:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE_PATH),
                "ps",
                "--status",
                "running",
                "--services",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return "lerim" in {line.strip() for line in result.stdout.splitlines()}


def current_compose_uses_local_build() -> bool:
    """Return True when the active compose file has a local build directive."""
    try:
        content = COMPOSE_PATH.read_text(encoding="utf-8")
    except OSError:
        return False
    return "\n    build:" in content


def is_server_healthy() -> bool:
    """Check whether the configured Lerim HTTP server is reachable."""
    import urllib.error
    import urllib.request

    config = get_config()
    url = f"http://localhost:{config.server_port}/api/health"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False
