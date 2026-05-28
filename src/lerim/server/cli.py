"""Command-line interface for Lerim runtime and service operations.

Service commands (answer, ingest, curate, status) are thin HTTP clients that
talk to a running Lerim server (started via ``lerim up`` or ``lerim serve``).
Host-only commands (init, project, up, down, logs, connect)
run locally and never require an HTTP server.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from collections import Counter
from typing import Any

from lerim import __version__
from lerim.adapters.registry import (
    KNOWN_PLATFORMS,
    connect_platform,
    default_path_for,
    list_platforms,
    load_platforms,
    remove_platform,
)
from lerim.integrations.mcp_connect import (
    connect_mcp_target,
    doctor_mcp_target,
    installed_mcp_targets,
    known_mcp_targets,
    resolve_mcp_target,
)

from lerim.server.api import (
    api_memory_reset,
    api_project_add,
    api_project_list,
    api_query,
    api_project_remove,
    detect_agents,
    parse_duration_to_seconds,
    write_init_config,
)
from lerim.server.docker_runtime import (
    api_down,
    api_up,
    current_compose_uses_local_build,
    docker_available,
    is_docker_container_running,
)
from lerim.server.daemon import (
    DAEMON_LOCK_BUSY_RETRY_SECONDS,
    EXIT_LOCK_BUSY,
    run_curate_once,
    run_ingest_once,
    run_context_brief_daily,
    run_context_brief_for_project,
    run_working_memory_daily,
    run_working_memory_for_project,
    resolve_window_bounds,
)
from lerim.server.cli_api_client import (
    ApiClientError,
    api_get as _api_client_get,
    api_post as _api_client_post,
)
from lerim.config.providers import PROVIDER_SETUP_CHOICES
from lerim.config.logging import configure_logging
from lerim.cloud.auth import cmd_auth, cmd_auth_logout, cmd_auth_status
from lerim.config.settings import (
    get_config,
    get_user_config_path,
    get_user_env_path,
    save_config_patch,
)
from lerim.config.tracing import configure_tracing
from lerim.context import ContextStore
from lerim.context.query_spec import QUERY_ENTITIES, QUERY_MODES, QUERY_ORDER_FIELDS
from lerim.profiles import (
    bundled_signal_pack_ids,
    get_signal_pack,
    list_signal_packs,
    load_signal_pack_file,
    normalize_signal_pack_id,
    reload_signal_packs,
)
from lerim.context_brief import (
    resolve_context_brief_project,
    status_to_dict,
    context_brief_paths,
    context_brief_status,
)
from lerim.working_memory import (
    working_memory_paths,
    working_memory_status,
    working_memory_status_to_dict,
)

_LEGACY_COMMAND_ALIASES = {
    "sync": "ingest",
    "maintain": "curate",
    "ask": "answer",
}
_LEGACY_COMMAND_REMOVAL_VERSION = "v0.3.0"
_PLANNED_PLUGIN_TARGETS = {
    "openclaw": {
        "display_name": "OpenClaw",
        "kind": "native plugin",
        "current_path": "Use --mode mcp for current MCP support.",
    },
    "hermes": {
        "display_name": "Hermes",
        "kind": "provider plugin",
        "current_path": "Use --mode mcp for current MCP support.",
    },
    "pi": {
        "display_name": "pi",
        "kind": "extension",
        "current_path": "Use --mode adapter for current native session ingestion.",
    },
}
_PLANNED_PLUGIN_ALIASES = {
    "open-claw": "openclaw",
    "hermes-agent": "hermes",
}
_MCP_TARGET_NATIVE_ADAPTERS = {
    "claude-code": "claude",
    "codex": "codex",
    "cursor": "cursor",
    "opencode": "opencode",
}
_REPO_ROOT = Path(__file__).resolve().parents[3]
_REPO_DASHBOARD_DIR = _REPO_ROOT / "dashboard"
_OPT_DASHBOARD_DIR = Path("/opt/lerim/dashboard")


def _daemon_last_run_after_attempt(
    *,
    finished_at: float,
    interval_seconds: float,
    exit_code: int,
) -> float:
    """Return the daemon timer anchor after one scheduled operation attempt."""
    if exit_code != EXIT_LOCK_BUSY:
        return finished_at
    retry_after = min(
        max(DAEMON_LOCK_BUSY_RETRY_SECONDS, 1.0),
        max(float(interval_seconds), 1.0),
    )
    return finished_at - float(interval_seconds) + retry_after


def _emit(message: object = "", *, file: Any | None = None) -> None:
    """Write one CLI output line to stdout or a provided file-like target."""
    target = file if file is not None else sys.stdout
    target.write(f"{message}\n")


def _warn_if_legacy_command(args: argparse.Namespace) -> None:
    """Emit a one-line deprecation notice for legacy command aliases."""
    command = str(getattr(args, "command", "") or "")
    replacement = _LEGACY_COMMAND_ALIASES.get(command)
    if replacement:
        _emit(
            f"`lerim {command}` is deprecated; use `lerim {replacement}`. "
            f"This alias will be removed in {_LEGACY_COMMAND_REMOVAL_VERSION}.",
            file=sys.stderr,
        )


def _emit_structured(*, title: str, payload: dict[str, Any], as_json: bool) -> None:
    """Emit a dict payload either as JSON or as key/value lines."""
    if as_json:
        _emit(json.dumps(payload, indent=2, ensure_ascii=True))
        return
    _emit(title)
    for key, value in payload.items():
        _emit(f"- {key}: {value}")


def _api_request_failed(error: ApiClientError) -> int:
    """Print an API client failure and return exit 1."""
    _emit(error.message, file=sys.stderr)
    if error.kind == "unreachable":
        _emit(
            "Start with: lerim up (Docker) or lerim serve (direct)",
            file=sys.stderr,
        )
    return 1


def _wait_for_ready(port: int, timeout: int = 30) -> bool:
    """Poll /api/health until the server responds or *timeout* seconds elapse."""
    url = f"http://localhost:{port}/api/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(1)
    return False


def _resolve_dashboard_dir() -> Path:
    """Return the local dashboard source directory for the UI launcher."""
    cwd_dashboard = Path.cwd() / "dashboard"
    for candidate in (cwd_dashboard, _REPO_DASHBOARD_DIR, _OPT_DASHBOARD_DIR):
        if (candidate / "package.json").is_file():
            return candidate
    raise FileNotFoundError(
        "Dashboard source was not found. Run `lerim dashboard` from the Lerim repo "
        "or install a distribution that includes the dashboard directory."
    )


def _ensure_dashboard_backend(port: int) -> bool:
    """Ensure the local Docker backend is reachable before starting the UI."""
    if _wait_for_ready(port, timeout=2):
        return True
    _emit("Starting Lerim backend with `lerim up`...")
    result = api_up(build_local=current_compose_uses_local_build())
    if result.get("error"):
        _emit(result["error"], file=sys.stderr)
        return False
    if _wait_for_ready(port):
        return True
    _emit(
        "Backend started but the API is not responding. Check logs with: lerim logs",
        file=sys.stderr,
    )
    return False


def _run_dashboard_command(
    command: list[str], *, cwd: Path, env: dict[str, str]
) -> int:
    """Run one dashboard subprocess and return its exit code."""
    try:
        return subprocess.run(command, cwd=cwd, env=env, check=False).returncode
    except KeyboardInterrupt:
        return 130


def _api_get(path: str) -> dict[str, Any]:
    """GET from the running Lerim server or raise a classified failure."""
    config = get_config()
    return _api_client_get(path, server_port=config.server_port)


def _api_post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """POST JSON to the running Lerim server or raise a classified failure."""
    config = get_config()
    return _api_client_post(path, body, server_port=config.server_port)


def _hoist_global_json_flag(raw: list[str]) -> list[str]:
    """Allow ``--json`` before or after subcommands by normalizing argv order."""
    if "--json" not in raw:
        return raw
    return ["--json"] + [item for item in raw if item != "--json"]


def _restart_docker_for_project_change(message: str) -> int:
    """Restart the Docker runtime when project mounts changed."""
    if not is_docker_container_running():
        return 0
    _emit(message)
    result = api_up(build_local=current_compose_uses_local_build())
    if result.get("error"):
        _emit(result["error"], file=sys.stderr)
        return 1
    _emit("Done.")
    return 0


def _cmd_connect(args: argparse.Namespace) -> int:
    """Handle ``lerim connect`` actions (list/auto/remove/connect)."""
    config = get_config()
    platforms_path = config.platforms_path
    action = getattr(args, "platform_name", None)
    mode = str(getattr(args, "mode", "adapter") or "adapter")

    if mode == "auto":
        return _cmd_connect_auto(args)

    if mode == "plugin":
        return _cmd_connect_plugin(args)

    if action == "doctor":
        return _cmd_connect_doctor(args)

    if mode == "mcp":
        return _cmd_connect_mcp(args)

    if action == "list" or action is None:
        entries = list_platforms(platforms_path)
        if getattr(args, "all", False):
            if getattr(args, "json", False):
                payload = {
                    "platforms": entries,
                    "mcp_targets": [
                        doctor_mcp_target(target) for target in known_mcp_targets()
                    ],
                }
                _emit(json.dumps(payload, indent=2, ensure_ascii=True))
                return 0
            _emit("MCP targets:")
            for target in known_mcp_targets():
                status = doctor_mcp_target(target)
                configured = "configured" if status["configured"] else "not configured"
                detected = "detected" if status["detected"] else "not detected"
                _emit(
                    f"- {target.name}: {status['config_path']} ({configured}, {detected})"
                )
            if entries:
                _emit("")
        if not entries:
            _emit("No platforms connected.")
            return 0
        _emit(f"Connected platforms: {len(entries)}")
        for entry in entries:
            status = "ok" if entry["exists"] else "missing"
            _emit(
                f"- {entry['name']}: {entry['path']} ({entry['session_count']} sessions, {status})"
            )
        return 0

    if action == "auto":
        connected = 0
        for name in KNOWN_PLATFORMS:
            result = connect_platform(platforms_path, name, custom_path=None)
            if result.get("status") == "connected":
                connected += 1
        _emit(f"Auto connected: {connected}")
        if connected > 0 and is_docker_container_running():
            _emit("Restarting Lerim to mount connected platform paths...")
            api_up(build_local=current_compose_uses_local_build())
            _emit("Done.")
        return 0

    if action == "remove":
        name = getattr(args, "extra_arg", None)
        if not name:
            _emit("Usage: lerim connect remove <platform>", file=sys.stderr)
            return 2
        removed = remove_platform(platforms_path, name)
        _emit(f"Removed: {name}" if removed else f"Platform not connected: {name}")
        if removed and is_docker_container_running():
            _emit("Restarting Lerim...")
            api_up(build_local=current_compose_uses_local_build())
            _emit("Done.")
        return 0

    name = action
    if name not in KNOWN_PLATFORMS:
        _emit(f"Unknown platform: {name}", file=sys.stderr)
        _emit(f"Known platforms: {', '.join(KNOWN_PLATFORMS)}", file=sys.stderr)
        return 2

    existing = load_platforms(platforms_path)
    existing_path = (existing.get("platforms", {}).get(name) or {}).get("path")
    result = connect_platform(
        platforms_path, name, custom_path=getattr(args, "path", None)
    )
    status = str(result.get("status") or "")
    if status == "path_not_found":
        _emit(f"Path not found: {result.get('path')}", file=sys.stderr)
        return 1
    if status == "unknown_platform":
        _emit(f"Unknown platform: {name}", file=sys.stderr)
        return 1

    _emit(f"Connected: {name}")
    _emit(f"- Path: {result.get('path')}")
    _emit(f"- Sessions: {result.get('session_count')}")
    if existing_path and existing_path == result.get("path"):
        _emit("- Path unchanged, no initial reindex trigger.")
    elif is_docker_container_running():
        _emit("Restarting Lerim to mount connected platform path...")
        api_up(build_local=current_compose_uses_local_build())
        _emit("Done.")
    return 0


def _cmd_connect_auto(args: argparse.Namespace) -> int:
    """Connect every available integration path for the requested target."""
    action = getattr(args, "platform_name", None)
    dry_run = bool(getattr(args, "dry_run", False))
    force = bool(getattr(args, "force", False))
    adapter_results = _connect_auto_adapters(args, dry_run=dry_run)
    mcp_results = _connect_auto_mcp_targets(action, dry_run=dry_run, force=force)
    payload = {
        "mode": "auto",
        "target": action or "list",
        "adapters": adapter_results,
        "mcp_targets": [item.to_dict() for item in mcp_results],
    }
    if not adapter_results and not mcp_results and action not in {None, "list", "auto"}:
        _emit(f"Unknown connect target for auto mode: {action}", file=sys.stderr)
        _emit(
            "Known native adapters: " + ", ".join(KNOWN_PLATFORMS),
            file=sys.stderr,
        )
        _emit(
            "Known MCP targets: "
            + ", ".join(target.name for target in known_mcp_targets()),
            file=sys.stderr,
        )
        return 2
    if getattr(args, "json", False):
        _emit(json.dumps(payload, indent=2, ensure_ascii=True))
        return _auto_connect_exit_code(payload)
    _emit("Auto connection summary")
    _emit("Native adapters:")
    if adapter_results:
        for result in adapter_results:
            _emit(_format_adapter_auto_result(result))
    else:
        _emit("- none")
    _emit("MCP targets:")
    if mcp_results:
        for result in mcp_results:
            _emit(_format_mcp_connect_result(result.to_dict()))
    else:
        _emit("- none detected")
    return _auto_connect_exit_code(payload)


def _connect_auto_adapters(
    args: argparse.Namespace,
    *,
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Connect native adapters selected by ``--mode auto``."""
    config = get_config()
    action = getattr(args, "platform_name", None)
    if action in {None, "list"}:
        return []
    names = _native_adapter_names_for_auto(action)
    results: list[dict[str, Any]] = []
    for name in names:
        if name not in KNOWN_PLATFORMS:
            continue
        custom_path = None if action == "auto" else getattr(args, "path", None)
        if dry_run:
            results.append(_dry_run_adapter_auto_result(name, custom_path))
            continue
        result = connect_platform(
            config.platforms_path,
            name,
            custom_path=custom_path,
        )
        results.append({**result, "dry_run": False})
    return results


def _native_adapter_names_for_auto(action: str | None) -> tuple[str, ...]:
    """Resolve target-specific ``--mode auto`` native adapter names."""
    if action == "auto":
        return KNOWN_PLATFORMS
    if not action:
        return ()
    name = str(action)
    if name in KNOWN_PLATFORMS:
        return (name,)
    target = resolve_mcp_target(name)
    mapped = _MCP_TARGET_NATIVE_ADAPTERS.get(target.name) if target else None
    return (mapped,) if mapped else ()


def _dry_run_adapter_auto_result(
    name: str,
    custom_path: str | None,
) -> dict[str, Any]:
    """Return a non-mutating native adapter auto-connect preview."""
    resolved = Path(custom_path).expanduser().resolve() if custom_path else default_path_for(name)
    exists = bool(resolved and resolved.exists())
    return {
        "name": name,
        "path": str(resolved) if resolved else None,
        "session_count": 0,
        "connected_at": None,
        "status": "would_connect" if exists else "path_not_found",
        "dry_run": True,
    }


def _connect_auto_mcp_targets(
    action: str | None,
    *,
    dry_run: bool,
    force: bool,
) -> list[Any]:
    """Connect MCP targets selected by ``--mode auto``."""
    if action in {None, "list"}:
        return []
    if action == "auto":
        targets = installed_mcp_targets()
    else:
        target = resolve_mcp_target(str(action))
        targets = [target] if target is not None else []
    return [
        connect_mcp_target(target, dry_run=dry_run, force=force)
        for target in targets
    ]


def _auto_connect_exit_code(payload: dict[str, Any]) -> int:
    """Return an exit code for a composite auto-connect payload."""
    mcp_failed = any(
        item.get("status") == "verification_failed"
        for item in payload.get("mcp_targets", [])
        if isinstance(item, dict)
    )
    return 1 if mcp_failed else 0


def _format_adapter_auto_result(payload: dict[str, Any]) -> str:
    """Render one native adapter auto-connect result for humans."""
    lines = [
        f"- {payload.get('name')}: {payload.get('status')}",
        f"  Path: {payload.get('path')}",
        f"  Dry run: {payload.get('dry_run', False)}",
    ]
    if payload.get("session_count") is not None:
        lines.append(f"  Sessions: {payload.get('session_count')}")
    return "\n".join(lines)


def _cmd_connect_plugin(args: argparse.Namespace) -> int:
    """Report planned native plugin support without falling back to MCP."""
    action = getattr(args, "platform_name", None)
    target_name = getattr(args, "extra_arg", None) if action == "doctor" else action
    if action in {None, "list"}:
        payload = {
            "mode": "plugin",
            "plugins": [_plugin_pending_payload(name) for name in _PLANNED_PLUGIN_TARGETS],
        }
        if getattr(args, "json", False):
            _emit(json.dumps(payload, indent=2, ensure_ascii=True))
        else:
            _emit("Planned plugin targets:")
            for item in payload["plugins"]:
                _emit(_format_plugin_pending(item))
        return 0
    if action == "auto":
        payload = {
            "mode": "plugin",
            "plugins": [_plugin_pending_payload(name) for name in _PLANNED_PLUGIN_TARGETS],
        }
        if getattr(args, "json", False):
            _emit(json.dumps(payload, indent=2, ensure_ascii=True))
        else:
            _emit("Plugin mode is planned but not implemented.")
            for item in payload["plugins"]:
                _emit(_format_plugin_pending(item))
        return 1
    if not target_name:
        _emit("Usage: lerim connect <openclaw|hermes|pi> --mode plugin", file=sys.stderr)
        return 2
    normalized = str(target_name).strip().lower().replace("_", "-")
    normalized = _PLANNED_PLUGIN_ALIASES.get(normalized, normalized)
    if normalized not in _PLANNED_PLUGIN_TARGETS:
        _emit(f"Plugin mode is not planned for: {target_name}", file=sys.stderr)
        _emit(
            "Planned plugin targets: "
            + ", ".join(_PLANNED_PLUGIN_TARGETS.keys()),
            file=sys.stderr,
        )
        return 2
    payload = _plugin_pending_payload(normalized)
    if getattr(args, "json", False):
        _emit(json.dumps(payload, indent=2, ensure_ascii=True))
    else:
        _emit(_format_plugin_pending(payload))
    return 1


def _plugin_pending_payload(name: str) -> dict[str, Any]:
    """Return the documented pending plugin status for one target."""
    target = _PLANNED_PLUGIN_TARGETS[name]
    return {
        "name": name,
        "display_name": target["display_name"],
        "kind": target["kind"],
        "status": "planned_not_implemented",
        "installed": False,
        "message": (
            f"{target['display_name']} {target['kind']} support is planned, "
            f"but not implemented yet. {target['current_path']}"
        ),
    }


def _format_plugin_pending(payload: dict[str, Any]) -> str:
    """Render one pending plugin status for humans."""
    return "\n".join(
        [
            f"{payload['display_name']} {payload['kind']}: {payload['status']}",
            f"- Kind: {payload['kind']}",
            f"- {payload['message']}",
        ]
    )


def _cmd_connect_mcp(args: argparse.Namespace) -> int:
    """Install Lerim's MCP server into an external agent config."""
    action = getattr(args, "platform_name", None)
    dry_run = bool(getattr(args, "dry_run", False))
    force = bool(getattr(args, "force", False))

    if action == "list" or action is None:
        targets = known_mcp_targets()
        if getattr(args, "json", False):
            payload = [doctor_mcp_target(target) for target in targets]
            _emit(json.dumps(payload, indent=2, ensure_ascii=True))
            return 0
        _emit(f"Known MCP targets: {len(targets)}")
        for target in targets:
            status = doctor_mcp_target(target)
            configured = "configured" if status["configured"] else "not configured"
            detected = "detected" if status["detected"] else "not detected"
            _emit(f"- {target.name}: {configured}, {detected}")
        return 0

    if action == "auto":
        targets = installed_mcp_targets()
        if not targets:
            _emit("No installed MCP targets detected.")
            return 0
        results = [
            connect_mcp_target(target, dry_run=dry_run, force=force)
            for target in targets
        ]
        if getattr(args, "json", False):
            _emit(json.dumps([item.to_dict() for item in results], indent=2, ensure_ascii=True))
            return 0
        for result in results:
            _emit(_format_mcp_connect_result(result.to_dict()))
        return 0

    target = resolve_mcp_target(str(action))
    if target is None:
        _emit(f"Unknown MCP target: {action}", file=sys.stderr)
        _emit(
            "Known MCP targets: "
            + ", ".join(target.name for target in known_mcp_targets()),
            file=sys.stderr,
        )
        return 2
    result = connect_mcp_target(target, dry_run=dry_run, force=force)
    payload = result.to_dict()
    if getattr(args, "json", False):
        _emit(json.dumps(payload, indent=2, ensure_ascii=True))
    else:
        _emit(_format_mcp_connect_result(payload))
    return 1 if result.status == "verification_failed" else 0


def _cmd_connect_doctor(args: argparse.Namespace) -> int:
    """Show read-only connection diagnostics for one target."""
    target_name = getattr(args, "extra_arg", None)
    if not target_name:
        _emit("Usage: lerim connect doctor <agent>", file=sys.stderr)
        return 2
    target = resolve_mcp_target(str(target_name))
    if target is None:
        _emit(f"Unknown MCP target: {target_name}", file=sys.stderr)
        return 2
    payload = doctor_mcp_target(target)
    if getattr(args, "json", False):
        _emit(json.dumps(payload, indent=2, ensure_ascii=True))
        return 0
    _emit(f"{payload['display_name']} MCP")
    _emit(f"- Config: {payload['config_path']}")
    _emit(f"- Exists: {payload['config_exists']}")
    _emit(f"- Detected: {payload['detected']}")
    _emit(f"- Configured: {payload['configured']}")
    if payload.get("parse_error"):
        _emit(f"- Parse error: {payload['parse_error']}")
    if payload.get("docs_url"):
        _emit(f"- Docs: {payload['docs_url']}")
    return 0


def _format_mcp_connect_result(payload: dict[str, Any]) -> str:
    """Render an MCP connection result for humans."""
    lines = [
        f"{payload['display_name']}: {payload['status']}",
        f"- Config: {payload['config_path']}",
        f"- Dry run: {payload['dry_run']}",
    ]
    if payload.get("backup_path"):
        lines.append(f"- Backup: {payload['backup_path']}")
    if payload.get("message"):
        lines.append(f"- {payload['message']}")
    return "\n".join(lines)


def _cmd_ingest(args: argparse.Namespace) -> int:
    """Forward ingest request to the running Lerim server."""
    _warn_if_legacy_command(args)
    body: dict[str, Any] = {
        "agent": getattr(args, "agent", None),
        "window": getattr(args, "window", None),
        "since": getattr(args, "since", None),
        "until": getattr(args, "until", None),
        "max_sessions": getattr(args, "max_sessions", None),
        "run_id": getattr(args, "run_id", None),
        "no_extract": getattr(args, "no_extract", False),
        "force": getattr(args, "force", False),
        "dry_run": getattr(args, "dry_run", False),
        "blocking": True,
    }
    try:
        data = _api_post("/api/ingest", body)
    except ApiClientError as error:
        return _api_request_failed(error)
    _emit_structured(title="Ingest:", payload=data, as_json=args.json)
    if not args.json:
        queue_health = data.get("queue_health") or {}
        if queue_health.get("degraded"):
            _emit("! Queue degraded")
            advice = str(queue_health.get("advice") or "").strip()
            if advice:
                _emit(f"  {advice}")
    return 0


def _cmd_curate(args: argparse.Namespace) -> int:
    """Forward context curation request to the running Lerim server."""
    _warn_if_legacy_command(args)
    body = {
        "dry_run": getattr(args, "dry_run", False),
        "blocking": True,
    }
    try:
        data = _api_post("/api/curate", body)
    except ApiClientError as error:
        return _api_request_failed(error)
    _emit_structured(title="Curate:", payload=data, as_json=args.json)
    if not args.json:
        queue_health = data.get("queue_health") or {}
        if queue_health.get("degraded"):
            _emit("! Queue degraded")
            advice = str(queue_health.get("advice") or "").strip()
            if advice:
                _emit(f"  {advice}")
    return 0


def _context_brief_project_from_args(args: argparse.Namespace) -> Any:
    """Resolve the current Context Brief project for CLI commands."""
    config = get_config()
    return resolve_context_brief_project(
        config=config,
        project=getattr(args, "project", None),
        cwd=Path.cwd(),
    )


def _cmd_context_brief(args: argparse.Namespace) -> int:
    """Handle local context brief commands."""
    _warn_if_legacy_command(args)
    action = getattr(args, "context_brief_action", None)
    command = str(getattr(args, "command", None) or "context-brief")
    if not action:
        _emit(f"Usage: lerim {command} {{show,status,path,refresh}}", file=sys.stderr)
        return 2
    try:
        project = _context_brief_project_from_args(args)
    except ValueError as exc:
        if args.json:
            _emit(json.dumps({"error": True, "message": str(exc)}, indent=2))
        else:
            _emit(str(exc), file=sys.stderr)
        return 1

    config = get_config()
    paths = context_brief_paths(config, project.identity.project_id)

    if action == "path":
        payload = {
            "project": project.name,
            "project_id": project.identity.project_id,
            "current_file": str(paths.current_file),
            "exists": paths.current_file.is_file(),
        }
        if args.json:
            _emit(json.dumps(payload, indent=2, ensure_ascii=True))
        else:
            _emit(paths.current_file)
        return 0

    if action == "show":
        if paths.current_file.is_file():
            store = ContextStore(config.context_db_path)
            status = status_to_dict(
                context_brief_status(config=config, store=store, project=project)
            )
            preface = [
                "Context Brief Live Status:",
                f"- availability: {status['availability']}",
                f"- generated_at: {status['generated_at']}",
                f"- age: {status['age']}",
                (
                    "- db_records_changed_since_generation: "
                    f"{status['records_changed_since_generation']}"
                ),
                (
                    "- db_records_missing_since_generation: "
                    f"{status['records_missing_since_generation']}"
                ),
                f"- suggested_action: {context_brief_show_action(status)}",
                "",
                "---",
                "",
            ]
            _emit(
                "\n".join(preface)
                + paths.current_file.read_text(encoding="utf-8").rstrip("\n")
            )
            return 0
        message = (
            f"No Context Brief generated yet for project `{project.name}`.\n"
            "Run: lerim context-brief refresh"
        )
        if args.json:
            _emit(
                json.dumps(
                    {
                        "error": True,
                        "project": project.name,
                        "project_id": project.identity.project_id,
                        "current_file": str(paths.current_file),
                        "message": message,
                    },
                    indent=2,
                    ensure_ascii=True,
                )
            )
        else:
            _emit(message)
        return 1

    if action == "status":
        store = ContextStore(config.context_db_path)
        status = context_brief_status(config=config, store=store, project=project)
        payload = status_to_dict(status)
        if args.json:
            _emit(json.dumps(payload, indent=2, ensure_ascii=True))
        else:
            _emit("Context Brief:")
            for key, value in payload.items():
                _emit(f"- {key}: {value}")
        return 0

    if action == "refresh":
        result = run_context_brief_for_project(
            project_name=project.name,
            project_path=project.identity.repo_path,
            trigger="manual",
            force=bool(getattr(args, "force", False)),
        )
        if args.json:
            _emit(json.dumps(result, indent=2, ensure_ascii=True))
        else:
            if result.get("status") == "skipped":
                _emit(
                    f"Context Brief skipped for {project.name}: {result.get('skip_reason')}"
                )
            elif result.get("status") == "failed":
                _emit(
                    f"Context Brief failed for {project.name}: {result.get('error')}",
                    file=sys.stderr,
                )
                return 1
            else:
                _emit(f"Context Brief generated for {project.name}.")
            if result.get("current_file"):
                _emit(f"- current_file: {result.get('current_file')}")
            if result.get("run_folder"):
                _emit(f"- run_folder: {result.get('run_folder')}")
        return 0

    _emit(f"Unknown context-brief action: {action}", file=sys.stderr)
    return 2


def context_brief_show_action(status: dict[str, Any]) -> str:
    """Return a contextual action for the already-running show command."""
    if status.get("availability") == "stale":
        if int(status.get("records_missing_since_generation") or 0) > 0:
            return "Refresh because this Context Brief cites records no longer present in the live DB."
        return "Refresh if newest persisted DB context matters."
    if status.get("availability") == "available":
        return "Continue with this startup context; inspect sources or query deeper if needed."
    return str(status.get("suggested_action") or "Run `lerim context-brief status`.")


def _cmd_working_memory(args: argparse.Namespace) -> int:
    """Handle local Working Memory commands."""
    action = getattr(args, "working_memory_action", None)
    if not action:
        _emit("Usage: lerim working-memory {show,status,path,refresh}", file=sys.stderr)
        return 2
    try:
        project = resolve_context_brief_project(
            config=get_config(),
            project=getattr(args, "project", None),
            cwd=Path.cwd(),
        )
    except ValueError as exc:
        if args.json:
            _emit(json.dumps({"error": True, "message": str(exc)}, indent=2))
        else:
            _emit(str(exc), file=sys.stderr)
        return 1

    config = get_config()
    paths = working_memory_paths(config, project.identity.project_id)

    if action == "path":
        payload = {
            "project": project.name,
            "project_id": project.identity.project_id,
            "current_file": str(paths.current_file),
            "exists": paths.current_file.is_file(),
        }
        if args.json:
            _emit(json.dumps(payload, indent=2, ensure_ascii=True))
        else:
            _emit(paths.current_file)
        return 0

    if action == "show":
        if paths.current_file.is_file():
            store = ContextStore(config.context_db_path)
            status = working_memory_status_to_dict(
                working_memory_status(config=config, store=store, project=project)
            )
            preface = [
                "Working Memory Live Status:",
                f"- availability: {status['availability']}",
                f"- generated_at: {status['generated_at']}",
                f"- age: {status['age']}",
                f"- window_hours: {status['window_hours']}",
                f"- window_started_at: {status['window_started_at']}",
                (
                    "- db_records_changed_since_generation: "
                    f"{status['records_changed_since_generation']}"
                ),
                (
                    "- db_records_missing_since_generation: "
                    f"{status['records_missing_since_generation']}"
                ),
                f"- suggested_action: {working_memory_show_action(status)}",
                "",
                "---",
                "",
            ]
            _emit(
                "\n".join(preface)
                + paths.current_file.read_text(encoding="utf-8").rstrip("\n")
            )
            return 0
        message = (
            f"No Working Memory generated yet for project `{project.name}`.\n"
            "Run: lerim working-memory refresh"
        )
        if args.json:
            _emit(
                json.dumps(
                    {
                        "error": True,
                        "project": project.name,
                        "project_id": project.identity.project_id,
                        "current_file": str(paths.current_file),
                        "message": message,
                    },
                    indent=2,
                    ensure_ascii=True,
                )
            )
        else:
            _emit(message)
        return 1

    if action == "status":
        store = ContextStore(config.context_db_path)
        status = working_memory_status(config=config, store=store, project=project)
        payload = working_memory_status_to_dict(status)
        if args.json:
            _emit(json.dumps(payload, indent=2, ensure_ascii=True))
        else:
            _emit("Working Memory:")
            for key, value in payload.items():
                _emit(f"- {key}: {value}")
        return 0

    if action == "refresh":
        result = run_working_memory_for_project(
            project_name=project.name,
            project_path=project.identity.repo_path,
            trigger="manual",
            force=bool(getattr(args, "force", False)),
        )
        if args.json:
            _emit(json.dumps(result, indent=2, ensure_ascii=True))
        else:
            if result.get("status") == "skipped":
                _emit(
                    f"Working Memory skipped for {project.name}: {result.get('skip_reason')}"
                )
            elif result.get("status") == "failed":
                _emit(
                    f"Working Memory failed for {project.name}: {result.get('error')}",
                    file=sys.stderr,
                )
                return 1
            else:
                _emit(f"Working Memory generated for {project.name}.")
            if result.get("current_file"):
                _emit(f"- current_file: {result.get('current_file')}")
            if result.get("run_folder"):
                _emit(f"- run_folder: {result.get('run_folder')}")
        return 0

    _emit(f"Unknown working-memory action: {action}", file=sys.stderr)
    return 2


def working_memory_show_action(status: dict[str, Any]) -> str:
    """Return a contextual action for the already-running show command."""
    if status.get("availability") == "stale":
        if int(status.get("records_missing_since_generation") or 0) > 0:
            return "Refresh because this Working Memory cites records no longer present in the live DB."
        return str(status.get("suggested_action") or "Refresh short-term memory.")
    if status.get("availability") == "available":
        return "Continue with this recent memory; use Context Brief for long-term context."
    return str(status.get("suggested_action") or "Run `lerim working-memory status`.")


def _cmd_dashboard(args: argparse.Namespace) -> int:
    """Start the local dashboard UI and ensure the backend is running."""
    config = get_config()
    try:
        dashboard_dir = _resolve_dashboard_dir()
    except FileNotFoundError as exc:
        _emit(str(exc), file=sys.stderr)
        return 1
    ui_port = int(getattr(args, "port", 3000) or 3000)
    api_url = f"http://localhost:{config.server_port}"

    if shutil.which("npm") is None:
        _emit("`npm` is required to run the dashboard UI.", file=sys.stderr)
        return 1
    if not _ensure_dashboard_backend(int(config.server_port)):
        return 1

    env = os.environ.copy()
    env["LERIM_API_URL"] = api_url

    if not (dashboard_dir / "node_modules").is_dir():
        _emit("Installing dashboard dependencies with `npm install`...")
        install_code = _run_dashboard_command(
            ["npm", "install"], cwd=dashboard_dir, env=env
        )
        if install_code != 0:
            return install_code

    _emit()
    _emit(f"  Lerim Dashboard: http://localhost:{ui_port}")
    _emit(f"  API:             {api_url}")
    _emit("  Press Ctrl-C to stop the dashboard UI.")
    _emit()
    sys.stdout.flush()
    return _run_dashboard_command(
        ["npm", "run", "dev", "--", "--port", str(ui_port)],
        cwd=dashboard_dir,
        env=env,
    )


def _cmd_mcp(args: argparse.Namespace) -> int:
    """Run Lerim's MCP stdio server."""
    del args
    from lerim.mcp_server import run_mcp_server

    run_mcp_server()
    return 0


def _cmd_trace(args: argparse.Namespace) -> int:
    """Handle host-only generic trace commands."""
    action = getattr(args, "trace_action", None)
    if action == "submissions":
        from lerim.traces.submissions import (
            list_submission_manifests,
            submission_status_counts,
        )

        rows = list_submission_manifests(
            root=get_config().global_data_dir,
            status=getattr(args, "status", None),
            limit=int(getattr(args, "limit", 20) or 20),
        )
        payload = {
            "error": False,
            "count": len(rows),
            "status_counts": submission_status_counts(rows),
            "rows": rows,
        }
        if args.json:
            _emit(json.dumps(payload, indent=2, ensure_ascii=True))
        else:
            _emit("Trace submissions.")
            if not rows:
                _emit("- no submitted traces found")
            for row in rows:
                _emit(
                    "- "
                    f"{row.get('status', 'unknown')} "
                    f"{row.get('trace_path', '')} "
                    f"(attempts: {row.get('attempt_count', 0)})"
                )
                if row.get("last_error"):
                    error = row["last_error"]
                    _emit(
                        f"  error: {error.get('type', 'Error')}: "
                        f"{error.get('message', '')}"
                    )
                if row.get("retry_command"):
                    _emit(f"  retry: {row['retry_command']}")
        return 0

    if action == "retry":
        from lerim.traces.submissions import (
            load_submission_manifest,
            retry_submitted_trace,
        )

        try:
            result = retry_submitted_trace(
                Path(args.path),
                force=bool(getattr(args, "force", False)),
            )
            manifest = load_submission_manifest(Path(args.path))
        except Exception as exc:
            if args.json:
                _emit(
                    json.dumps(
                        {"error": True, "message": str(exc), "type": type(exc).__name__},
                        indent=2,
                        ensure_ascii=True,
                    )
                )
            else:
                _emit(f"Trace submission retry failed: {exc}", file=sys.stderr)
            return 1

        payload = {
            "error": False,
            "trace_id": result.trace_id,
            "session_id": result.session_id,
            "submitted_trace_path": manifest.get("trace_path"),
            "submission_manifest_path": manifest.get("manifest_path"),
            "retry_command": manifest.get("retry_command"),
            "attempt_count": manifest.get("attempt_count"),
            "normalized_trace_path": str(result.normalized_trace_path),
            "scope_type": result.scope_identity.scope_type,
            "scope_id": result.scope_identity.scope_id,
            "scope_label": result.scope_identity.label,
            **result.ingest_result,
        }
        if args.json:
            _emit(json.dumps(payload, indent=2, ensure_ascii=True))
        else:
            _emit("Trace submission retried.")
            _emit(f"- trace_id: {result.trace_id}")
            _emit(f"- scope: {result.scope_identity.scope_key}")
            _emit(f"- normalized_trace_path: {result.normalized_trace_path}")
            _emit(f"- records_created: {payload.get('records_created', 0)}")
            _emit(f"- attempts: {payload.get('attempt_count', 0)}")
        return 0

    if action != "import":
        _emit("Usage: lerim trace import <path>", file=sys.stderr)
        return 2
    from lerim.traces import import_trace_file

    try:
        result = import_trace_file(
            trace_path=Path(args.path),
            source_name=str(args.source_name),
            source_profile=str(args.source_profile),
            scope_type=str(args.scope_type),
            scope=str(args.scope),
            scope_label=getattr(args, "scope_label", None),
            session_id=getattr(args, "session_id", None),
            force=bool(getattr(args, "force", False)),
        )
    except Exception as exc:
        if args.json:
            _emit(
                json.dumps(
                    {"error": True, "message": str(exc), "type": type(exc).__name__},
                    indent=2,
                    ensure_ascii=True,
                )
            )
        else:
            _emit(f"Trace import failed: {exc}", file=sys.stderr)
        return 1

    payload = {
        "error": False,
        "trace_id": result.trace_id,
        "session_id": result.session_id,
        "normalized_trace_path": str(result.normalized_trace_path),
        "scope_type": result.scope_identity.scope_type,
        "scope_id": result.scope_identity.scope_id,
        "scope_label": result.scope_identity.label,
        **result.ingest_result,
    }
    if args.json:
        _emit(json.dumps(payload, indent=2, ensure_ascii=True))
    else:
        _emit("Trace imported.")
        _emit(f"- trace_id: {result.trace_id}")
        _emit(f"- scope: {result.scope_identity.scope_key}")
        _emit(f"- normalized_trace_path: {result.normalized_trace_path}")
        _emit(f"- records_created: {payload.get('records_created', 0)}")
        _emit(f"- run_folder: {payload.get('run_folder', '')}")
    return 0


def _normalize_scope(raw: str | None) -> str:
    """Normalize CLI scope flag to all|project."""
    return "project" if str(raw or "").strip().lower() == "project" else "all"


def _render_answer_trace(debug: dict[str, Any]) -> list[str]:
    """Render sanitized answer trace in notebook-style message order."""
    messages = debug.get("messages") or []
    if not messages:
        return []

    lines = [
        "=" * 70,
        f"ANSWER TRACE ({len(messages)} messages)",
        "=" * 70,
    ]
    for message in messages:
        message_index = int(message.get("message_index", 0))
        kind = str(message.get("kind") or "?")
        lines.append("")
        lines.append(f"--- Message {message_index} [{kind}] ---")
        for part in message.get("parts") or []:
            part_kind = str(part.get("part_kind") or "")
            if part_kind == "system-prompt":
                lines.append(
                    f"  [system-prompt] ({int(part.get('char_count', 0))} chars)"
                )
                continue
            if part_kind == "user-prompt":
                lines.append(f"  [user-prompt] {str(part.get('content') or '')}")
                continue
            if part_kind == "tool-call":
                args_json = json.dumps(part.get("args"), ensure_ascii=True)
                lines.append(
                    f"  [tool-call] {str(part.get('tool_name') or '?')}({args_json})"
                )
                continue
            if part_kind == "tool-return":
                content = str(part.get("content_preview") or "").replace("\n", " ")
                lines.append(
                    f"  [tool-return] {str(part.get('tool_name') or '?')} -> {content}"
                )
                continue
            if part_kind in {"count", "list", "search"}:
                content = (
                    part.get("content") if isinstance(part.get("content"), dict) else {}
                )
                result_count = content.get("result_count", "?")
                lines.append(f"  [retrieval] {part_kind} results={result_count}")
                continue
            if part_kind in {"PlanContextRetrieval", "AnswerFromContext"}:
                lines.append(f"  [baml] {part_kind}")
                continue
            if part_kind == "text":
                lines.append(f"  [text] {str(part.get('content') or '')}")
                continue
            lines.append(f"  [{part_kind}] {str(part.get('content') or '')}")
    return lines


def _cmd_answer(args: argparse.Namespace) -> int:
    """Forward context answer query to the running Lerim server."""
    _warn_if_legacy_command(args)
    scope = _normalize_scope(getattr(args, "scope", None))
    payload: dict[str, Any] = {
        "question": args.question,
        "scope": scope,
        "verbose": bool(getattr(args, "verbose", False)),
    }
    project = getattr(args, "project", None)
    if project:
        payload["project"] = str(project)
    try:
        data = _api_post("/api/answer", payload)
    except ApiClientError as error:
        return _api_request_failed(error)
    if data.get("error"):
        _emit(data.get("answer", "Error"), file=sys.stderr)
        return 1
    if args.json:
        _emit(json.dumps(data, indent=2, ensure_ascii=True))
    else:
        _emit(data.get("answer", ""))
        if getattr(args, "verbose", False):
            debug = data.get("debug") or {}
            trace_lines = _render_answer_trace(debug)
            if trace_lines:
                _emit("")
                for line in trace_lines:
                    _emit(line)
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    """Run deterministic context query locally and print the result."""
    scope = _normalize_scope(getattr(args, "scope", None))
    payload = api_query(
        entity=args.entity,
        mode=args.mode,
        scope=scope,
        project=getattr(args, "project", None),
        kind=getattr(args, "kind", None),
        status=getattr(args, "status", None),
        source_profile=getattr(args, "source_profile", None),
        source_session_id=getattr(args, "source_session_id", None),
        created_since=getattr(args, "created_since", None),
        created_until=getattr(args, "created_until", None),
        updated_since=getattr(args, "updated_since", None),
        updated_until=getattr(args, "updated_until", None),
        valid_at=getattr(args, "valid_at", None),
        order_by=getattr(args, "order_by", "created_at"),
        limit=int(getattr(args, "limit", 20)),
        offset=int(getattr(args, "offset", 0)),
        include_total=bool(getattr(args, "include_total", False)),
    )
    if args.json:
        _emit(json.dumps(payload, indent=2, ensure_ascii=True))
        return 1 if payload.get("error") else 0
    if payload.get("error"):
        _emit(str(payload.get("message") or "query failed"), file=sys.stderr)
        return 1
    _emit(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0


def _cmd_context_records(args: argparse.Namespace) -> int:
    """List context records with profile/kind filters."""
    requested_kind = _normalize_record_kind(
        getattr(args, "kind", None)
    )
    requested_limit = max(1, int(getattr(args, "limit", 20)))
    payload = api_query(
        entity="records",
        mode="list",
        scope=_normalize_scope(getattr(args, "scope", None)),
        project=getattr(args, "project", None),
        kind=requested_kind,
        status=getattr(args, "status", None),
        source_profile=getattr(args, "profile", None)
        or getattr(args, "source_profile", None),
        source_session_id=getattr(args, "source_session_id", None),
        order_by="updated_at",
        limit=requested_limit,
        offset=max(0, int(getattr(args, "offset", 0))),
        include_total=True,
    )
    if payload.get("error"):
        _emit(str(payload.get("message") or "query failed"), file=sys.stderr)
        return 1
    rows = payload.get("rows") or []
    if args.json:
        grouped = {
            key: values for key, values in _group_record_rows_by_kind(rows).items()
        }
        _emit(json.dumps({**payload, "groups": grouped}, indent=2, ensure_ascii=True))
        return 0
    profile = getattr(args, "profile", None) or getattr(args, "source_profile", None)
    title = (
        f"{get_signal_pack(profile).display_name} Context Records"
        if profile
        else "Context Records"
    )
    _emit(title)
    _emit(f"{len(rows)} records shown")
    if requested_kind:
        _emit(f"Kind: {requested_kind}")
    if not rows:
        return 0
    for kind, group_rows in _group_record_rows_by_kind(rows).items():
        _emit("")
        _emit(_record_kind_label(kind))
        for row in group_rows:
            scope = f"{row.get('scope_type', 'project')}:{row.get('scope_id', 'unknown')}"
            status = str(row.get("status") or "").strip()
            title_text = str(row.get("title") or "")[:140].replace("\n", " ")
            _emit(f"- {title_text}")
            _emit(
                "  "
                f"{row.get('record_id', '')} | {row.get('kind', '')} | {status} | "
                f"{scope} | {row.get('source_profile', 'coding')}"
            )
            evidence = _evidence_summary(row)
            if evidence:
                _emit(f"  Evidence: {evidence}")
    return 0


def _relative_time(iso_str: str) -> str:
    """Convert an ISO timestamp to a human-readable relative string."""
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        secs = int(delta.total_seconds())
        if secs < 0:
            return "just now"
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except (ValueError, TypeError):
        return "?"


def _format_queue_counts(counts: dict[str, int]) -> str:
    """Format queue status counts into a summary string."""
    order = ["pending", "running", "done", "failed", "dead_letter"]
    parts = []
    for status in order:
        n = counts.get(status, 0)
        if n > 0:
            parts.append(f"{n} {status}")
    return ", ".join(parts) if parts else "empty"


def _resolve_project_repo_path(name: str) -> str | None:
    """Resolve an exact project name/path to repo_path."""
    config = get_config()
    if name in config.projects:
        return str(Path(config.projects[name]).expanduser().resolve())
    token = str(name).strip()
    try:
        raw_path = Path(token).expanduser().resolve()
    except Exception:
        raw_path = None
    if raw_path is not None:
        for ppath in config.projects.values():
            resolved = Path(ppath).expanduser().resolve()
            if resolved == raw_path:
                return str(resolved)
    return None


def _cmd_queue(args: argparse.Namespace) -> int:
    """Display the session extraction queue."""
    from lerim.sessions.catalog import list_queue_jobs, count_session_jobs_by_status

    project_filter: str | None = None
    project_exact = False
    project = getattr(args, "project", None)
    if project:
        repo_path = _resolve_project_repo_path(str(project))
        if not repo_path:
            _emit(f"Project not found: {project}", file=sys.stderr)
            return 1
        project_filter = repo_path
        project_exact = True

    jobs = list_queue_jobs(
        status_filter=getattr(args, "status", None),
        project_filter=project_filter,
        project_exact=project_exact,
        failed_only=getattr(args, "failed", False),
    )
    counts = count_session_jobs_by_status()

    if args.json:
        _emit(
            json.dumps(
                {"jobs": jobs, "total": len(jobs), "queue": counts},
                indent=2,
                default=str,
            )
        )
        return 0

    if not jobs:
        _emit("Session Queue: no jobs")
        _emit(_format_queue_counts(counts))
        return 0

    from rich.console import Console
    from rich.table import Table

    table = Table(title=f"Session Queue ({len(jobs)} jobs)")
    table.add_column("STATUS", style="bold")
    table.add_column("RUN ID")
    table.add_column("PROJECT")
    table.add_column("AGENT")
    table.add_column("AGE")
    table.add_column("ERROR")

    status_styles = {
        "pending": "dim white",
        "running": "cyan",
        "failed": "yellow",
        "dead_letter": "red bold",
        "done": "green",
    }

    for job in jobs:
        st = str(job.get("status") or "")
        style = status_styles.get(st, "")
        rid = str(job.get("run_id") or "")[:8]
        rp = str(job.get("repo_path") or "")
        proj = Path(rp).name if rp else ""
        agent = str(job.get("agent_type") or "")
        age = _relative_time(str(job.get("updated_at") or ""))
        err = str(job.get("error") or "")[:50]
        table.add_row(f"[{style}]{st}[/{style}]", rid, proj, agent, age, err)

    Console().print(table)
    _emit(_format_queue_counts(counts))
    if counts.get("dead_letter", 0) > 0:
        _emit("Retry: lerim retry <run_id>  |  Retry all: lerim retry --all")
    return 0


def _cmd_unscoped(args: argparse.Namespace) -> int:
    """Show indexed sessions that are currently unscoped (no project match)."""
    query_path = f"/api/unscoped?limit={max(1, int(args.limit))}"
    try:
        data = _api_get(query_path)
    except ApiClientError as error:
        return _api_request_failed(error)
    items = data.get("items") or []
    if args.json:
        _emit(json.dumps(data, indent=2, ensure_ascii=True))
        return 0
    count_by_agent = data.get("count_by_agent") or {}
    if not items:
        _emit("No unscoped sessions.")
        if count_by_agent:
            _emit(f"by_agent: {json.dumps(count_by_agent, ensure_ascii=True)}")
        return 0
    _emit(f"Unscoped sessions ({len(items)} shown):")
    for item in items:
        run_id = str(item.get("run_id") or "")[:8]
        agent = str(item.get("agent_type") or "unknown")
        repo_path = str(item.get("repo_path") or "(missing cwd)")
        _emit(f"- {run_id}  {agent}  {repo_path}")
    if count_by_agent:
        _emit(f"by_agent: {json.dumps(count_by_agent, ensure_ascii=True)}")
    return 0


def _cmd_status_live(args: argparse.Namespace) -> int:
    """Live status dashboard for project stream health and queue state."""
    from datetime import datetime, timezone
    from rich.live import Live
    from lerim.server.status_tui import render_status_output

    interval = max(0.5, float(getattr(args, "interval", 3.0)))
    scope = _normalize_scope(getattr(args, "scope", None))
    project = getattr(args, "project", None)
    query = f"/api/status?scope={scope}"
    if project:
        query += f"&project={urllib.parse.quote(str(project))}"

    def _fetch() -> dict[str, Any]:
        return _api_get(query)

    if args.json:
        try:
            payload = _fetch()
        except ApiClientError as error:
            return _api_request_failed(error)
        _emit(json.dumps(payload, indent=2, ensure_ascii=True))
        return 0

    try:
        with Live(refresh_per_second=4, screen=False) as live:
            while True:
                try:
                    payload = _fetch()
                except ApiClientError as error:
                    live.stop()
                    return _api_request_failed(error)
                refreshed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
                live.update(render_status_output(payload, refreshed_at=refreshed_at))
                time.sleep(interval)
    except KeyboardInterrupt:
        _emit("")
        return 0


def _dead_letter_action(
    args: argparse.Namespace,
    *,
    verb: str,
    single_fn_name: str,
    project_fn_name: str,
    all_fn_name: str,
    done_suffix: str = "",
    noun: str = "dead_letter",
    action: str | None = None,
) -> int:
    """Shared handler for retry/skip blocked queue operations."""
    from lerim.sessions.catalog import (
        resolve_run_id_prefix,
        count_session_jobs_by_status,
    )
    import lerim.sessions.catalog as _catalog

    single_fn = getattr(_catalog, single_fn_name)
    project_fn = getattr(_catalog, project_fn_name)
    all_fn = getattr(_catalog, all_fn_name)

    run_id = getattr(args, "run_id", None)
    project = getattr(args, "project", None)
    do_all = getattr(args, "all", False)
    action_label = action or verb.lower()

    if do_all:
        count = int(all_fn())
        if count == 0:
            _emit(f"No {noun} jobs to {action_label}.")
            return 0
        _emit(f"{verb} {count} {noun} job(s).")
        _emit(_format_queue_counts(count_session_jobs_by_status()))
        return 0

    if project:
        repo_path = _resolve_project_repo_path(project)
        if not repo_path:
            _emit(f"Project not found: {project}", file=sys.stderr)
            return 1
        count = project_fn(repo_path)
        _emit(f"{verb} {count} {noun} job(s) for project {project}.")
        _emit(_format_queue_counts(count_session_jobs_by_status()))
        return 0

    if not run_id:
        _emit("Provide a run_id, --project, or --all.", file=sys.stderr)
        return 2
    if len(run_id) < 6:
        _emit("Run ID prefix must be at least 6 characters.", file=sys.stderr)
        return 2

    full_id = resolve_run_id_prefix(run_id)
    if not full_id:
        _emit(f"Run ID not found or ambiguous: {run_id}", file=sys.stderr)
        return 1

    if single_fn(full_id):
        _emit(f"{verb}: {run_id}{done_suffix}")
        _emit(_format_queue_counts(count_session_jobs_by_status()))
    else:
        _emit(f"Job {run_id} is not in {noun} status.", file=sys.stderr)
        return 1
    return 0


def _cmd_retry(args: argparse.Namespace) -> int:
    """Retry failed and dead-letter jobs."""
    return _dead_letter_action(
        args,
        verb="Retried",
        single_fn_name="retry_session_job",
        project_fn_name="retry_project_jobs",
        all_fn_name="retry_all_dead_letter_jobs",
        noun="failed/dead_letter",
        action="retry",
    )


def _cmd_skip(args: argparse.Namespace) -> int:
    """Skip dead_letter jobs."""
    return _dead_letter_action(
        args,
        verb="Skipped",
        single_fn_name="skip_session_job",
        project_fn_name="skip_project_jobs",
        all_fn_name="skip_all_dead_letter_jobs",
        done_suffix=" -> done",
    )


def _cmd_status(args: argparse.Namespace) -> int:
    """Forward status request to the running Lerim server."""
    if getattr(args, "live", False):
        live_args = argparse.Namespace(
            command="status",
            json=getattr(args, "json", False),
            interval=float(getattr(args, "interval", 3.0)),
            scope=getattr(args, "scope", "all"),
            project=getattr(args, "project", None),
        )
        return _cmd_status_live(live_args)

    scope = _normalize_scope(getattr(args, "scope", None))
    query = f"/api/status?scope={scope}"
    project = getattr(args, "project", None)
    if project:
        query += f"&project={urllib.parse.quote(str(project))}"
    try:
        data = _api_get(query)
    except ApiClientError as error:
        return _api_request_failed(error)
    if data.get("error"):
        if args.json:
            _emit(json.dumps(data, indent=2, ensure_ascii=True))
        else:
            _emit(str(data.get("error")), file=sys.stderr)
        return 1
    if args.json:
        _emit(json.dumps(data, indent=2, ensure_ascii=True))
    else:
        from datetime import datetime, timezone
        from rich.console import Console
        from lerim.server.status_tui import render_status_output

        refreshed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        Console().print(render_status_output(data, refreshed_at=refreshed_at))
    return 0


def _setup_api_keys() -> None:
    """Interactive API key setup — saves to the active Lerim .env file."""
    env_path = get_user_env_path()

    # Load existing keys if any
    existing: dict[str, str] = {}
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                existing[k.strip()] = v.strip()

    _emit("\n── LLM Provider Setup ──────────────────────────────")
    _emit("")
    _emit(
        "  Lerim needs an LLM provider to extract context records from your sessions."
    )
    _emit("  Select your provider(s) and enter API keys. You can change these")
    _emit(f"  later in {env_path} and {get_user_config_path()}.")
    _emit("")
    _emit("  Available providers:")
    _emit("")
    for i, provider in enumerate(PROVIDER_SETUP_CHOICES, 1):
        env_var = provider.api_key_env
        has_key = "✓" if existing.get(env_var) else " "
        _emit(f"  [{has_key}] {i}. {provider.display_name:<14} {provider.description}")
    _emit("")

    answer = input(
        "  Enter provider numbers (comma-separated, e.g. 1,3) or press Enter to skip: "
    ).strip()
    if not answer:
        if existing:
            _emit(f"  Keeping existing keys in {env_path}")
        else:
            _emit(f"  Skipped. Set API keys later in {env_path}")
        return

    # Parse selections
    new_keys: dict[str, str] = dict(existing)  # preserve existing
    try:
        indices = [int(x.strip()) - 1 for x in answer.split(",") if x.strip()]
    except ValueError:
        _emit("  Invalid input. Skipping API key setup.")
        return

    _emit("")
    for idx in indices:
        if idx < 0 or idx >= len(PROVIDER_SETUP_CHOICES):
            continue
        provider = PROVIDER_SETUP_CHOICES[idx]
        env_var = provider.api_key_env
        name = provider.display_name
        if not env_var:
            _emit(f"  {name}: no API key needed (local provider)")
            continue

        current = existing.get(env_var, "")
        masked = f" (current: ...{current[-8:]})" if current else ""
        key = input(f"  {name} API key{masked}: ").strip()
        if key:
            new_keys[env_var] = key
        elif current:
            _emit("    Keeping existing key")

    # Write ~/.lerim/.env
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Lerim API keys — managed by `lerim init`", ""]
    for k, v in sorted(new_keys.items()):
        lines.append(f"{k}={v}")
    lines.append("")
    env_path.write_text("\n".join(lines), encoding="utf-8")
    env_path.chmod(0o600)  # restrict permissions — secrets file
    _emit(f"\n  Keys saved to {env_path} (permissions: 600)")


def _cmd_init(args: argparse.Namespace) -> int:
    """Interactive setup wizard — agents, API keys, config."""
    _emit("")
    _emit("  ╔═══════════════════════════════════╗")
    _emit("  ║       Welcome to Lerim            ║")
    _emit("  ╚═══════════════════════════════════╝")

    # Step 1: Detect supported local trace sources.
    _emit("\n── Supported Trace Sources ────────────────────────")
    _emit("")
    _emit("  Which supported coding-agent trace sources should Lerim watch?")
    _emit("  Custom trace folders can be added later with `lerim project add <folder> --type custom`.")
    _emit("")

    detected = detect_agents()
    selected: dict[str, str] = {}
    for name, info in detected.items():
        exists = info["exists"]
        marker = "detected" if exists else "not found"
        answer = (
            input(f"  {name} ({marker}) [{'Y/n' if exists else 'y/N'}]: ")
            .strip()
            .lower()
        )
        if (exists and answer != "n") or (not exists and answer == "y"):
            selected[name] = info["path"]

    if selected:
        write_init_config(selected)
        _emit(f"\n  Config written to {get_user_config_path()}")
        _emit(f"  Agents: {', '.join(selected.keys())}")
    else:
        _emit("\n  No agents selected. Add them later in:")
        _emit(f"  {get_user_config_path()}")

    # Step 2: API keys
    _setup_api_keys()

    # Step 3: Docker check
    _emit("\n── Docker ─────────────────────────────────────────")
    _emit("")
    if docker_available():
        _emit("  Docker: found ✓")
    else:
        _emit("  Docker: not found")
        _emit("  Install Docker to use `lerim up` (recommended).")
        _emit("  Or run `lerim serve` directly without Docker.")

    # Done
    _emit("\n── Next Steps ─────────────────────────────────────")
    _emit("")
    _emit("  1. lerim project add /path/to/repo   # register a project")
    _emit("  2. lerim up                           # start the service")
    _emit("")
    _emit("  Change providers:  ~/.lerim/config.toml")
    _emit("  Change API keys:   ~/.lerim/.env")
    _emit("")
    return 0


def _cmd_project(args: argparse.Namespace) -> int:
    """Dispatch project subcommands."""
    action = getattr(args, "project_action", None)
    if not action:
        _emit("Usage: lerim project {add,list,remove}", file=sys.stderr)
        return 2

    if action == "list":
        projects = api_project_list()
        if args.json:
            _emit(json.dumps(projects, indent=2, ensure_ascii=True))
            return 0
        if not projects:
            _emit("No projects registered.")
            return 0
        _emit(f"Registered projects: {len(projects)}")
        for p in projects:
            status = "ok" if p["exists"] else "missing"
            project_type = str(p.get("type") or "supported")
            profile = str(p.get("source_profile") or "").strip()
            profile_text = f", profile={profile}" if profile else ""
            _emit(
                f"  {p['name']}: {p['path']} "
                f"({project_type}, {status}{profile_text})"
            )
        return 0

    if action == "add":
        path_str = getattr(args, "path", None)
        if not path_str:
            _emit("Usage: lerim project add <path>", file=sys.stderr)
            return 2
        result = api_project_add(
            path_str,
            project_type=str(getattr(args, "project_type", "supported") or "supported"),
            source_profile=getattr(args, "source_profile", None),
        )
        if result.get("error"):
            _emit(result["error"], file=sys.stderr)
            return 1
        profile_text = (
            f', source_profile={result["source_profile"]}'
            if result.get("source_profile")
            else ""
        )
        _emit(
            f'Added project "{result["name"]}" '
            f'({result["path"]}, type={result["type"]}{profile_text})'
        )
        return _restart_docker_for_project_change(
            "Restarting Lerim to mount new project..."
        )

    if action == "remove":
        name = getattr(args, "name", None)
        if not name:
            _emit("Usage: lerim project remove <name>", file=sys.stderr)
            return 2
        result = api_project_remove(name)
        if result.get("error"):
            _emit(result["error"], file=sys.stderr)
            return 1
        _emit(f'Removed project "{name}"')
        return _restart_docker_for_project_change("Restarting Lerim...")

    _emit("Usage: lerim project {add,list,remove}", file=sys.stderr)
    return 2


def _cmd_memory(args: argparse.Namespace) -> int:
    """Dispatch memory subcommands."""
    action = getattr(args, "memory_action", None)
    if action != "reset":
        _emit(
            "Usage: lerim memory reset (--project <name-or-path> | --all)",
            file=sys.stderr,
        )
        return 2

    project = getattr(args, "project", None)
    all_projects = bool(getattr(args, "all", False))
    if bool(project) == all_projects:
        _emit("Provide exactly one of --project or --all.", file=sys.stderr)
        return 2

    if args.json and not bool(getattr(args, "yes", False)):
        _emit(
            json.dumps(
                {
                    "error": True,
                    "message": "--yes is required with --json for memory reset.",
                },
                indent=2,
                ensure_ascii=True,
            )
        )
        return 2

    preview = api_memory_reset(project=project, all_projects=all_projects, dry_run=True)
    if preview.get("error"):
        _emit(str(preview.get("message") or "memory reset failed"), file=sys.stderr)
        return 1

    if not bool(getattr(args, "yes", False)):
        _print_memory_reset_preview(preview)
        answer = input("Continue? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            _emit("Memory reset cancelled.")
            return 1

    result = api_memory_reset(project=project, all_projects=all_projects, dry_run=False)
    if args.json:
        _emit(json.dumps(result, indent=2, ensure_ascii=True))
        return 1 if result.get("error") else 0
    if result.get("error"):
        _emit(str(result.get("message") or "memory reset failed"), file=sys.stderr)
        return 1
    _print_memory_reset_result(result)
    return 0


def _normalize_profile(profile: Any) -> str:
    """Normalize source profile values for case-insensitive matching."""
    return normalize_signal_pack_id(str(profile or "coding"))


def _normalize_record_kind(kind: Any) -> str | None:
    """Normalize an optional record kind filter."""
    text = str(kind or "").strip().lower()
    return text or None


def _group_record_rows_by_kind(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group rows by durable record kind while preserving row order."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("kind") or "fact"), []).append(row)
    return grouped


def _record_kind_label(kind: str) -> str:
    """Render one record-kind heading."""
    words = str(kind or "context").replace("_", " ").strip().split()
    return " ".join(word.capitalize() for word in words) or "Context"


def _evidence_summary(row: dict[str, Any]) -> str:
    """Render compact evidence refs for card output."""
    for key in ("source_event_refs", "evidence_refs"):
        raw = row.get(key)
        refs = _parse_refs(raw)
        if refs:
            return ", ".join(refs[:3])
    return ""


def _parse_refs(raw: Any) -> list[str]:
    """Parse stored JSON or plain evidence refs."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return [text]
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [str(parsed).strip()] if str(parsed).strip() else []


def _cmd_profile_validate(args: argparse.Namespace) -> int:
    """Validate one source-profile YAML file without changing config."""
    path = Path(str(getattr(args, "path", ""))).expanduser()
    try:
        resolved_path = path.resolve(strict=True)
        pack = load_signal_pack_file(resolved_path)
    except (OSError, ValueError) as exc:
        _emit(f"Invalid source profile: {exc}", file=sys.stderr)
        return 1

    reserved = pack.id in bundled_signal_pack_ids()
    payload = {
        "source_profile": pack.id,
        "display_name": pack.display_name,
        "description": pack.description,
        "source": pack.source,
        "path": str(resolved_path),
        "reserved_bundled_id": reserved,
        "focus_rules": list(pack.focus_rules),
        "reject_as_noise": list(pack.reject_as_noise),
        "evidence_rules": list(pack.evidence_rules),
        "scope_rules": list(pack.scope_rules),
    }
    if getattr(args, "json", False):
        _emit(json.dumps(payload, indent=2, ensure_ascii=True))
        return 0 if not reserved else 1
    _emit(f"Source profile YAML: {pack.id}")
    _emit(f"- Name: {pack.display_name}")
    _emit(f"- Description: {pack.description}")
    _emit(f"- Path: {resolved_path}")
    if reserved:
        _emit("- Status: invalid for registration; id is bundled", file=sys.stderr)
        return 1
    _emit("- Status: valid")
    return 0


def _cmd_profile_register(args: argparse.Namespace) -> int:
    """Register one custom source-profile YAML file in Lerim config."""
    path = Path(str(getattr(args, "path", ""))).expanduser()
    try:
        resolved_path = path.resolve(strict=True)
        pack = load_signal_pack_file(resolved_path)
    except (OSError, ValueError) as exc:
        _emit(f"Invalid source profile: {exc}", file=sys.stderr)
        return 1

    if pack.id in bundled_signal_pack_ids():
        _emit(
            f"Cannot register '{pack.id}': bundled profile ids are reserved.",
            file=sys.stderr,
        )
        return 1

    config = get_config()
    existing = config.profiles.get(pack.id)
    if existing:
        existing_path = Path(existing).expanduser().resolve()
        if existing_path != resolved_path and not getattr(args, "force", False):
            _emit(
                f"Profile '{pack.id}' is already registered at {existing_path}. "
                "Use --force to replace it.",
                file=sys.stderr,
            )
            return 1

    save_config_patch({"profiles": {pack.id: str(resolved_path)}})
    reload_signal_packs()

    payload = {
        "source_profile": pack.id,
        "display_name": pack.display_name,
        "path": str(resolved_path),
        "config_path": str(get_user_config_path()),
    }
    if getattr(args, "json", False):
        _emit(json.dumps(payload, indent=2, ensure_ascii=True))
        return 0
    _emit(f"Registered source profile: {pack.id}")
    _emit(f"- Name: {pack.display_name}")
    _emit(f"- Path: {resolved_path}")
    _emit(f"- Config: {get_user_config_path()}")
    _emit(f"Use it with: lerim trace import <trace.jsonl> --source-profile {pack.id}")
    return 0


def _cmd_profile(args: argparse.Namespace) -> int:
    """Show source-profile discoverability and statistics from stored records."""
    action = getattr(args, "profile_action", None)
    if action == "validate":
        return _cmd_profile_validate(args)
    if action == "register":
        return _cmd_profile_register(args)
    if action not in {"list", "show"}:
        _emit("Usage: lerim profile {list,show,validate,register}", file=sys.stderr)
        return 2

    config = get_config()
    store = ContextStore(config.context_db_path)
    store.initialize()
    kind_filter = _normalize_record_kind(
        getattr(args, "kind", None)
    )
    limit = max(1, int(getattr(args, "limit", 10)))

    if action == "list":
        with store.connect() as conn:
            count_rows = conn.execute(
                """
                SELECT
                  COALESCE(NULLIF(TRIM(LOWER(source_profile)), ''), 'generic') AS source_profile,
                  COUNT(*) AS record_count
                FROM records
                GROUP BY COALESCE(NULLIF(TRIM(LOWER(source_profile)), ''), 'generic')
                ORDER BY record_count DESC, source_profile
                """
            ).fetchall()
        counts = {
            str(row["source_profile"]): int(row["record_count"])
            for row in count_rows
        }
        profiles = [
            {
                "source_profile": pack.id,
                "display_name": pack.display_name,
                "description": pack.description,
                "source": pack.source,
                "path": pack.path,
                "record_count": counts.get(pack.id, 0),
                "focus_rules": list(pack.focus_rules),
            }
            for pack in list_signal_packs()
        ]
        if args.json:
            _emit(json.dumps({"profiles": profiles}, indent=2, ensure_ascii=True))
            return 0
        _emit("Source profiles:")
        for row in profiles:
            source = f" [{row['source']}]" if row.get("source") else ""
            _emit(
                f"- {row['source_profile']}: {row['display_name']} "
                f"({row['record_count']} records){source}"
            )
        return 0

    requested_profile = _normalize_profile(getattr(args, "name", None))
    pack = get_signal_pack(requested_profile)
    where_clause = """
            WHERE COALESCE(NULLIF(TRIM(LOWER(source_profile)), ''), 'generic') = ?
        """
    where_params = [requested_profile]
    if kind_filter:
        where_clause += (
            " AND COALESCE(NULLIF(TRIM(LOWER(kind)), ''), 'fact') = ?"
        )
        where_params.append(kind_filter)

    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT
              source_profile,
              kind,
              status,
              scope_type,
              scope_id,
              source_name,
              title
            FROM records
            {where_clause}
            ORDER BY updated_at DESC
            LIMIT ?
            """.replace(
                "{where_clause}", where_clause
            ),
            (*where_params, limit),
        ).fetchall()

    kind_counts = Counter(str(row["kind"] or "").strip().lower() for row in rows)
    status_counts = Counter(str(row["status"] or "").strip().lower() for row in rows)
    scope_counts = Counter(
        f"{str(row['scope_type'] or 'project')}:{str(row['scope_id'] or 'unknown')}"
        for row in rows
    )

    payload = {
        "source_profile": requested_profile,
        "display_name": pack.display_name,
        "description": pack.description,
        "source": pack.source,
        "path": pack.path,
        "focus_rules": list(pack.focus_rules),
        "kind": kind_filter,
        "records": len(rows),
        "records_by_kind": {name: count for name, count in sorted(kind_counts.items())},
        "records_by_status": {name: count for name, count in sorted(status_counts.items())},
        "scope_count": len(scope_counts),
        "top_records": [
            {
                "kind": row["kind"],
                "status": row["status"],
                "scope": f"{row['scope_type']}:{row['scope_id']}",
                "source_name": row["source_name"],
                "title": row["title"],
            }
            for row in rows[:10]
        ],
    }
    if args.json:
        _emit(json.dumps(payload, indent=2, ensure_ascii=True))
        return 0

    _emit(f"Source profile: {requested_profile}")
    _emit(f"- Name: {pack.display_name}")
    _emit(f"- Description: {pack.description}")
    _emit(f"- Source: {pack.source}")
    if pack.path:
        _emit(f"- Path: {pack.path}")
    if pack.focus_rules:
        _emit("- Focus rules:")
        for rule in pack.focus_rules:
            _emit(f"  - {rule}")
    if kind_filter:
        _emit(f"- Kind filter: {kind_filter}")
    _emit(f"- Records: {payload['records']}")
    _emit(f"- Scope count: {payload['scope_count']}")
    if payload["records_by_kind"]:
        _emit("- Kind breakdown:")
        for key, value in payload["records_by_kind"].items():
            _emit(f"  - {key}: {value}")
    if payload["records_by_status"]:
        _emit("- Status breakdown:")
        for key, value in payload["records_by_status"].items():
            _emit(f"  - {key}: {value}")
    _emit("- Recent records:")
    for item in payload["top_records"][:5]:
        _emit(
            "  - "
            f"{item['kind']} ({item['status']}) [{item['scope']}] "
            f"source={item['source_name']} title={item['title']}"
        )
    return 0


def _print_memory_reset_preview(payload: dict[str, Any]) -> None:
    """Print a confirmation summary for memory reset."""
    scope = str(payload.get("scope") or "")
    if scope == "all":
        _emit("This will reset Lerim memory for all registered projects.")
    else:
        _emit(f'This will reset Lerim memory for project "{payload.get("project")}".')
    _emit("It will delete:")
    for key, value in (payload.get("deleted") or {}).items():
        _emit(f"- {key}: {value}")
    cloud_reset = payload.get("cloud_reset") or {}
    if cloud_reset.get("configured"):
        if cloud_reset.get("error"):
            _emit(f"Cloud dashboard reset failed: {cloud_reset.get('message')}")
        elif cloud_reset.get("dry_run"):
            _emit("Cloud dashboard data will also be reset.")
        else:
            _emit("Cloud dashboard reset:")
            for key, value in (cloud_reset.get("deleted") or {}).items():
                _emit(f"- {key}: {value}")
    kept = ", ".join(str(item) for item in payload.get("kept") or [])
    if kept:
        _emit(f"It will keep: {kept}.")
    for note in payload.get("notes") or []:
        _emit(f"Note: {note}.")


def _print_memory_reset_result(payload: dict[str, Any]) -> None:
    """Print memory reset completion details."""
    scope = str(payload.get("scope") or "")
    if scope == "all":
        _emit("Reset Lerim memory for all registered projects.")
    else:
        _emit(f'Reset Lerim memory for project "{payload.get("project")}".')
    for key, value in (payload.get("deleted") or {}).items():
        _emit(f"- {key}: {value}")
    cloud_reset = payload.get("cloud_reset") or {}
    if cloud_reset.get("configured"):
        if cloud_reset.get("error"):
            _emit(f"Cloud dashboard reset failed: {cloud_reset.get('message')}")
        elif cloud_reset.get("dry_run"):
            _emit("Cloud dashboard data will also be reset.")
        else:
            _emit("Cloud dashboard reset:")
            for key, value in (cloud_reset.get("deleted") or {}).items():
                _emit(f"- {key}: {value}")
    kept = ", ".join(str(item) for item in payload.get("kept") or [])
    if kept:
        _emit(f"Kept: {kept}.")
    for note in payload.get("notes") or []:
        _emit(f"Note: {note}.")


def _cmd_up(args: argparse.Namespace) -> int:
    """Start the Docker container."""
    config = get_config()
    _emit(
        f"Starting Lerim with {len(config.projects)} projects and {len(config.agents)} agents..."
    )
    build_local = (
        bool(getattr(args, "build", False)) or current_compose_uses_local_build()
    )
    result = api_up(
        build_local=build_local,
        no_build=bool(getattr(args, "no_build", False)),
    )
    if result.get("error"):
        _emit(result["error"], file=sys.stderr)
        return 1

    if not _wait_for_ready(config.server_port):
        _emit(
            "Container started but the server is not responding. "
            "Check logs with: lerim logs",
            file=sys.stderr,
        )
        return 1

    source = str(result.get("runtime_source") or "unknown")
    image = str(result.get("runtime_image") or "")
    source_detail = f" ({image})" if image else ""
    _emit(f"Lerim is running at http://localhost:{config.server_port}")
    _emit(f"Runtime source: {source}{source_detail}")
    return 0


def _cmd_down(args: argparse.Namespace) -> int:
    """Stop the Docker container."""
    result = api_down()
    if result.get("error"):
        _emit(result["error"], file=sys.stderr)
        return 1
    if result.get("status") == "not_running":
        _emit("Lerim is not running.")
        return 0
    if result.get("was_running"):
        _emit("Lerim stopped.")
    else:
        _emit("Lerim was not running. Cleaned up containers.")
    return 0


def _parse_since(since: str) -> float:
    """Parse a relative duration string (e.g. ``1h``, ``30m``, ``2d``) into seconds."""
    import re

    m = re.fullmatch(r"(\d+)\s*([smhd])", since.strip().lower())
    if not m:
        raise ValueError(
            f"Invalid --since format: {since!r}  (expected e.g. 1h, 30m, 2d)"
        )
    value, unit = int(m.group(1)), m.group(2)
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return value * multipliers[unit]


def _fmt_log_line(entry: dict[str, Any], *, color: bool) -> str:
    """Format a parsed JSONL log entry for terminal display."""
    ts_raw = str(entry.get("ts") or "")
    # Extract HH:MM:SS from ISO-8601 timestamp
    hms = ts_raw[11:19] if len(ts_raw) >= 19 else ts_raw
    level = str(entry.get("level") or "").upper()
    message = str(entry.get("message") or "")

    if not color:
        return f"{hms} | {level:<8} | {message}"

    # ANSI colour codes for log levels
    _LEVEL_COLORS: dict[str, str] = {
        "TRACE": "\033[37m",  # white/grey
        "DEBUG": "\033[36m",  # cyan
        "INFO": "\033[32m",  # green
        "SUCCESS": "\033[1;32m",  # bold green
        "WARNING": "\033[33m",  # yellow
        "ERROR": "\033[31m",  # red
        "CRITICAL": "\033[1;31m",  # bold red
    }
    _RESET = "\033[0m"
    clr = _LEVEL_COLORS.get(level, "")
    return f"\033[32m{hms}\033[0m | {clr}{level:<8}{_RESET} | {clr}{message}{_RESET}"


def _cmd_logs(args: argparse.Namespace) -> int:
    """Read and display local JSONL log entries."""
    from lerim.config.logging import iter_log_files, log_file_path

    jsonl_paths = iter_log_files("lerim.jsonl")
    if not jsonl_paths and not getattr(args, "follow", False):
        _emit("No log file found. Logs will appear after Lerim runs.", file=sys.stderr)
        return 0

    is_tty = sys.stdout.isatty()
    raw_json = getattr(args, "raw_json", False) or getattr(args, "json", False)
    level_filter = (getattr(args, "level", None) or "").upper() or None
    since_str = getattr(args, "since", None)
    follow = getattr(args, "follow", False)

    # Compute cutoff timestamp for --since
    cutoff_ts: float | None = None
    if since_str:
        import datetime as _dt

        cutoff_ts = (
            _dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(seconds=_parse_since(since_str))
        ).timestamp()

    def _matches(entry: dict[str, Any]) -> bool:
        """Return True if the entry passes level and time filters."""
        if level_filter and str(entry.get("level") or "").upper() != level_filter:
            return False
        if cutoff_ts is not None:
            from datetime import datetime, timezone

            ts_raw = str(entry.get("ts") or "")
            try:
                entry_dt = datetime.fromisoformat(ts_raw)
                if entry_dt.tzinfo is None:
                    entry_dt = entry_dt.replace(tzinfo=timezone.utc)
                if entry_dt.timestamp() < cutoff_ts:
                    return False
            except (ValueError, TypeError):
                return False
        return True

    def _print_entry(entry: dict[str, Any]) -> None:
        if raw_json:
            _emit(json.dumps(entry, ensure_ascii=True, default=str))
        else:
            _emit(_fmt_log_line(entry, color=is_tty))

    if follow:
        # Live tail: seek to the current day file and reopen if the day changes.
        current_path = log_file_path("lerim.jsonl")
        try:
            fh = None
            try:
                while True:
                    next_path = log_file_path("lerim.jsonl")
                    if fh is None or next_path != current_path:
                        if fh is not None:
                            fh.close()
                        current_path = next_path
                        current_path.parent.mkdir(parents=True, exist_ok=True)
                        fh = open(current_path, "a+", encoding="utf-8")
                        fh.seek(0, 2)
                    line = fh.readline()
                    if not line:
                        time.sleep(0.25)
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if _matches(entry):
                        _print_entry(entry)
            finally:
                if fh is not None:
                    fh.close()
        except KeyboardInterrupt:
            pass
        return 0

    # Non-follow: read last N matching lines
    limit = 50
    matching: list[dict[str, Any]] = []
    try:
        for jsonl_path in jsonl_paths:
            with open(jsonl_path, "r", encoding="utf-8") as fh:
                for raw_line in fh:
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        entry = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    if _matches(entry):
                        matching.append(entry)
    except OSError as exc:
        _emit(f"Error reading log file: {exc}", file=sys.stderr)
        return 1

    # Show only the last `limit` entries
    for entry in matching[-limit:]:
        _print_entry(entry)

    return 0


def _ship_to_cloud_once(config: Any, logger: Any) -> None:
    if not config.cloud_token:
        return
    try:
        import asyncio

        from lerim.cloud.shipper import ship_once

        results = asyncio.run(ship_once(config))
        if results:
            logger.info("cloud ship: {}", results)
    except Exception as exc:
        logger.warning("cloud ship error: {}", exc)


def _cmd_serve(args: argparse.Namespace) -> int:
    """Start HTTP API + daemon loop in one process."""
    import signal
    import threading
    from http.server import ThreadingHTTPServer

    from lerim.server.httpd import DashboardHandler
    from lerim.sessions.catalog import (
        init_sessions_db,
        queue_health_snapshot,
        reap_stale_running_jobs,
    )

    config = get_config()
    host = args.host or config.server_host or "0.0.0.0"
    port = int(args.port or config.server_port or 8765)

    init_sessions_db()
    reaped_at_startup = reap_stale_running_jobs()
    httpd = ThreadingHTTPServer((host, port), DashboardHandler)
    httpd.timeout = 1.0

    stop_event = threading.Event()

    def _daemon_loop() -> None:
        """Background daemon loop with independent ingest and curate intervals."""
        from lerim.config.logging import logger
        from lerim.server.api import ollama_lifecycle

        ingest_interval = max(config.ingest_interval_minutes * 60, 30)
        curate_interval = max(config.curate_interval_minutes * 60, 30)
        context_brief_interval = 24 * 60 * 60

        # Initialise to (now - interval) so both trigger on the first
        # iteration regardless of the monotonic clock epoch.  In Docker
        # containers the monotonic clock reflects VM uptime which can be
        # smaller than curate_interval, causing the first curate to
        # be silently skipped when initialised to 0.0.
        _now_init = time.monotonic()
        last_ingest = _now_init - ingest_interval
        last_curate = _now_init - curate_interval
        last_context_brief = _now_init - context_brief_interval
        last_degraded_log_at = 0.0
        degraded_log_interval_seconds = 300.0

        logger.info(
            "daemon loop started (ingest every {}s, curate every {}s)",
            ingest_interval,
            curate_interval,
        )
        if reaped_at_startup > 0:
            logger.warning(
                "recovered {} stale running job(s) at startup",
                reaped_at_startup,
            )

        _ship_to_cloud_once(config, logger)

        while not stop_event.is_set():
            now = time.monotonic()

            if now - last_ingest >= ingest_interval:
                try:
                    window_start, window_end = resolve_window_bounds(
                        window=f"{config.ingest_window_days}d",
                        since_raw=None,
                        until_raw=None,
                        parse_duration_to_seconds=parse_duration_to_seconds,
                    )
                    with ollama_lifecycle(config):
                        code, summary = run_ingest_once(
                            run_id=None,
                            agent_filter=None,
                            no_extract=False,
                            force=False,
                            max_sessions=config.ingest_max_sessions,
                            dry_run=False,
                            ignore_lock=False,
                            trigger="daemon",
                            window_start=window_start,
                            window_end=window_end,
                        )
                    logger.info(
                        "daemon ingest done — indexed={} extracted={} skipped={} failed={}",
                        summary.indexed_sessions,
                        summary.extracted_sessions,
                        summary.skipped_sessions,
                        summary.failed_sessions,
                    )
                    last_ingest = _daemon_last_run_after_attempt(
                        finished_at=time.monotonic(),
                        interval_seconds=ingest_interval,
                        exit_code=code,
                    )
                except Exception as exc:
                    logger.warning("daemon ingest error: {}", exc)
                    last_ingest = time.monotonic()

            if now - last_curate >= curate_interval:
                try:
                    with ollama_lifecycle(config):
                        code, details = run_curate_once(
                            dry_run=False,
                            trigger="daemon",
                        )
                    logger.info("daemon curate done — {}", details)
                    last_curate = _daemon_last_run_after_attempt(
                        finished_at=time.monotonic(),
                        interval_seconds=curate_interval,
                        exit_code=code,
                    )
                except Exception as exc:
                    logger.warning("daemon curate error: {}", exc)
                    last_curate = time.monotonic()

            if now - last_context_brief >= context_brief_interval:
                try:
                    with ollama_lifecycle(config):
                        details = run_context_brief_daily(trigger="daily")
                        memory_details = run_working_memory_daily(trigger="daily")
                    logger.info("daemon context-brief done — {}", details)
                    logger.info("daemon working-memory done — {}", memory_details)
                except Exception as exc:
                    logger.warning("daemon context-brief error: {}", exc)
                last_context_brief = time.monotonic()

            _ship_to_cloud_once(config, logger)

            queue_health = queue_health_snapshot()
            if (
                queue_health.get("degraded")
                and now - last_degraded_log_at >= degraded_log_interval_seconds
            ):
                logger.warning(
                    "queue degraded | stale_running={} dead_letter={} advice={}",
                    int(queue_health.get("stale_running_count") or 0),
                    int(queue_health.get("dead_letter_count") or 0),
                    str(queue_health.get("advice") or ""),
                )
                last_degraded_log_at = now

            next_ingest = last_ingest + ingest_interval
            next_curate = last_curate + curate_interval
            next_context_brief = last_context_brief + context_brief_interval
            sleep_for = max(
                1.0,
                min(next_ingest, next_curate, next_context_brief) - time.monotonic(),
            )
            stop_event.wait(sleep_for)

    daemon_thread = threading.Thread(
        target=_daemon_loop, name="lerim-daemon", daemon=True
    )
    daemon_thread.start()

    def _shutdown(_signum: int, _frame: Any) -> None:
        """Signal handler — just set the stop flag (no lock-acquiring calls)."""
        stop_event.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    from lerim.config.logging import logger

    logger.info("Lerim serve running at http://{}:{}/", host, port)
    while not stop_event.is_set():
        httpd.handle_request()
    httpd.server_close()
    daemon_thread.join(timeout=5)
    return 0


_SKILL_TARGETS: dict[str, Path] = {
    "agents": Path.home() / ".agents" / "skills" / "lerim",
    "claude": Path.home() / ".claude" / "skills" / "lerim",
}
"""Skill install targets: ~/.agents/skills (shared by most agents) + ~/.claude/skills (Claude-specific)."""


def _cmd_skill(args: argparse.Namespace) -> int:
    """Install Lerim skill files into coding agent directories."""
    action = getattr(args, "skill_action", None)
    if action != "install":
        _emit("Usage: lerim skill install")
        return 2

    from lerim.skills import SKILLS_DIR

    skill_files = [SKILLS_DIR / "SKILL.md", SKILLS_DIR / "cli-reference.md"]
    missing = [f for f in skill_files if not f.exists()]
    if missing:
        _emit(f"Skill files not found in package: {missing}", file=sys.stderr)
        return 1

    installed = []
    for label, dest in _SKILL_TARGETS.items():
        dest.mkdir(parents=True, exist_ok=True)
        for src in skill_files:
            (dest / src.name).write_text(src.read_text())
        installed.append(
            f"~/.{label}/skills/lerim"
            if label != "agents"
            else "~/.agents/skills/lerim"
        )

    _emit(f"Installed lerim skill to: {', '.join(installed)}")
    _emit("  ~/.agents/skills/lerim  → Cursor, Codex, OpenCode, and others")
    _emit("  ~/.claude/skills/lerim  → Claude Code")
    return 0


def _cmd_auth_dispatch(args: argparse.Namespace) -> int:
    """Dispatch auth subcommands to the appropriate handler."""
    auth_command = getattr(args, "auth_command", None)
    if auth_command == "status":
        return cmd_auth_status(args)
    if auth_command == "logout":
        return cmd_auth_logout(args)
    # Default: login (bare `lerim auth` or `lerim auth login`)
    return cmd_auth(args)


def _add_force_flag(parser: argparse.ArgumentParser) -> None:
    """Add --force flag to *parser*."""
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-extract already processed sessions.",
    )


def _add_dry_run_flag(parser: argparse.ArgumentParser) -> None:
    """Add --dry-run flag to *parser*."""
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview mode: no writes.",
    )


def _add_ingest_args(parser: argparse.ArgumentParser, *, config: Any) -> None:
    """Add ingest-compatible session discovery arguments to *parser*."""
    parser.add_argument(
        "--run-id",
        help="Target a single session by its run ID. If it is not already indexed, "
        "discover it through the selected connected adapter first. Use with --force "
        "to re-extract.",
    )
    parser.add_argument(
        "--agent",
        help="Comma-separated list of platforms to ingest (e.g. 'claude,codex'). "
        "Omit to ingest all connected platforms.",
    )
    parser.add_argument(
        "--window",
        default=None,
        help="Time window for session discovery. Accepts durations like 30s, 2m, 1h, 7d, "
        "or the literal 'all' to scan every session. Ignored when --since is set. "
        f"(default: ingest_window_days from config, currently {config.ingest_window_days}d)",
    )
    parser.add_argument(
        "--since",
        help="Absolute start bound (ISO-8601, e.g. 2026-02-01T00:00:00Z). Overrides --window.",
    )
    parser.add_argument(
        "--until",
        help="Absolute end bound (ISO-8601). Defaults to now if omitted. Only used with --since.",
    )
    parser.add_argument(
        "--max-sessions",
        type=int,
        default=None,
        help="Maximum number of sessions to extract in one run. "
        f"(default: ingest_max_sessions from config, currently {config.ingest_max_sessions})",
    )
    parser.add_argument(
        "--no-extract",
        action="store_true",
        help="Index and enqueue sessions but skip extraction entirely. "
        "Useful to populate the queue without creating records yet.",
    )
    _add_force_flag(parser)
    _add_dry_run_flag(parser)


def _add_context_brief_subcommands(parser: argparse.ArgumentParser) -> None:
    """Add shared context-brief subcommands to canonical and legacy parsers."""
    brief_sub = parser.add_subparsers(dest="context_brief_action")
    for action_name in ("show", "status", "path"):
        action = brief_sub.add_parser(
            action_name,
            formatter_class=argparse.RawDescriptionHelpFormatter,
            help=f"{action_name} Context Brief",
        )
        action.add_argument(
            "--project",
            help="Registered project name or path. Defaults to cwd project.",
        )
    refresh = brief_sub.add_parser(
        "refresh",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="Generate Context Brief for the resolved project",
    )
    refresh.add_argument(
        "--project",
        help="Registered project name or path. Defaults to cwd project.",
    )
    _add_force_flag(refresh)


def _add_working_memory_subcommands(parser: argparse.ArgumentParser) -> None:
    """Add Working Memory subcommands to its parser."""
    memory_sub = parser.add_subparsers(dest="working_memory_action")
    for action_name in ("show", "status", "path"):
        action = memory_sub.add_parser(
            action_name,
            formatter_class=argparse.RawDescriptionHelpFormatter,
            help=f"{action_name} Working Memory",
        )
        action.add_argument(
            "--project",
            help="Registered project name or path. Defaults to cwd project.",
        )
    refresh = memory_sub.add_parser(
        "refresh",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="Generate Working Memory for the resolved project",
    )
    refresh.add_argument(
        "--project",
        help="Registered project name or path. Defaults to cwd project.",
    )
    _add_force_flag(refresh)


def _add_dead_letter_args(
    parser: argparse.ArgumentParser, *, verb: str, noun: str = "dead_letter"
) -> None:
    """Add run_id, --project, and --all arguments for blocked queue commands."""
    parser.add_argument(
        "run_id",
        nargs="?",
        help=f"Run ID (or prefix, min 6 chars) of the job to {verb}.",
    )
    parser.add_argument(
        "--project",
        help=f"{verb.capitalize()} all {noun} jobs for a project (by name).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help=f"{verb.capitalize()} all {noun} jobs across all projects.",
    )


def build_parser() -> argparse.ArgumentParser:
    """Construct the canonical Lerim command-line parser."""
    config = get_config()
    _F = argparse.RawDescriptionHelpFormatter  # noqa: N806
    parser = argparse.ArgumentParser(
        prog="lerim",
        formatter_class=_F,
        description="Lerim -- trace-to-context layer for agents.\n"
        "Ingests agent sessions, extracts durable context, and answers questions\n"
        "using accumulated operational knowledge.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured JSON instead of human-readable text (works with status, answer, ingest, etc.)",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # ── connect ──────────────────────────────────────────────────────
    connect = sub.add_parser(
        "connect",
        formatter_class=_F,
        help="Manage connected agent platforms",
        description=(
	            "Register, list, or remove agent platform connections.\n\n"
	            "Examples:\n"
	            "  lerim connect auto --mode auto    # connect native traces and detected MCP configs\n"
	            "  lerim connect claude              # connect Claude traces\n"
	            "  lerim connect gemini-cli --mode mcp --dry-run"
	        ),
    )
    connect.add_argument(
        "platform_name",
        nargs="?",
        help="Action or platform name: 'list' (show connections), 'auto' (connect all detected), "
        "'remove' (disconnect, needs extra_arg), or a platform name to connect",
    )
    connect.add_argument(
        "extra_arg",
        nargs="?",
        help="Used with 'remove' action -- the platform name to disconnect (e.g. lerim connect remove claude)",
    )
    connect.add_argument(
        "--path",
        help="Custom filesystem path to the platform's session store (overrides auto-detected path)",
    )
    connect.add_argument(
        "--mode",
        choices=["adapter", "mcp", "auto", "plugin"],
        default="adapter",
        help=(
            "Connection mode: adapter registers local trace stores; "
            "mcp writes agent MCP config; auto tries both; plugin reports "
            "planned native plugin support."
        ),
    )
    connect.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview MCP config changes without writing files.",
    )
    connect.add_argument(
        "--force",
        action="store_true",
        help="Rewrite an existing Lerim MCP entry.",
    )
    connect.add_argument(
        "--all",
        action="store_true",
        help="With list, show native adapters and MCP targets.",
    )
    connect.set_defaults(func=_cmd_connect)

    mcp = sub.add_parser(
        "mcp",
        formatter_class=_F,
        help="Run Lerim as an MCP stdio server",
        description=(
            "Start Lerim's MCP stdio server for agent clients.\n\n"
            "For client configs, use the command written by `lerim connect`: "
            "/absolute/path/to/python -m lerim.mcp_server"
        ),
    )
    mcp.set_defaults(func=_cmd_mcp)

    # ── ingest ───────────────────────────────────────────────────────
    ingest = sub.add_parser(
        "ingest",
        formatter_class=_F,
        help="Ingest new sessions and extract context records (hot path)",
        description=(
            "Index new sessions and extract context records via BAML/LangGraph.\n\n"
            "Examples:\n"
            "  lerim ingest                      # default 7d window\n"
            "  lerim ingest --window 30d         # last 30 days\n"
            "  lerim ingest --agent claude,codex # filter platforms"
        ),
    )
    _add_ingest_args(ingest, config=config)
    ingest.set_defaults(func=_cmd_ingest)

    ingest_alias = sub.add_parser(
        "sync",
        formatter_class=_F,
        help="Deprecated alias for `lerim ingest`",
        description=(
            "`lerim sync` is a deprecated compatibility alias for `lerim ingest`.\n\n"
            "Examples:\n"
            "  lerim ingest                      # default 7d window\n"
            "  lerim ingest --window 30d         # last 30 days"
        ),
    )
    _add_ingest_args(ingest_alias, config=config)
    ingest_alias.set_defaults(func=_cmd_ingest)

    # ── trace ────────────────────────────────────────────────────────
    trace = sub.add_parser(
        "trace",
        formatter_class=_F,
        help="Import explicit generic agent traces",
        description=(
            "Import a JSON or JSONL trace from any agent, normalize it, and extract "
            "scoped context records.\n\n"
            "Example:\n"
            "  lerim trace import trace.jsonl --source-name support-bot "
            "--source-profile support --scope-type domain --scope support"
        ),
    )
    trace_sub = trace.add_subparsers(dest="trace_action")
    trace_import = trace_sub.add_parser(
        "import",
        formatter_class=_F,
        help="Normalize and extract one explicit trace file",
    )
    trace_import.add_argument("path", help="Path to a JSON, JSONL, or text trace file.")
    trace_import.add_argument(
        "--source-name",
        required=True,
        help="Source agent/system name, for example support-bot or browser-agent.",
    )
    trace_import.add_argument(
        "--source-profile",
        required=True,
        help=(
            "Source profile/category, for example coding, generic, support, "
            "ops, or a registered custom profile."
        ),
    )
    trace_import.add_argument(
        "--scope-type",
        required=True,
        choices=["project", "domain", "user", "session", "workspace", "custom"],
        help="Context isolation type for imported records.",
    )
    trace_import.add_argument(
        "--scope",
        required=True,
        help="Scope token. For project scope, pass a repo path.",
    )
    trace_import.add_argument(
        "--scope-label",
        help="Optional human label for this scope.",
    )
    trace_import.add_argument(
        "--session-id",
        help="Optional stable session id. Defaults to the normalized trace id.",
    )
    trace_import.add_argument(
        "--force",
        action="store_true",
        help="Re-run extraction even when the same session id already has identical normalized trace content.",
    )
    trace_retry = trace_sub.add_parser(
        "retry",
        formatter_class=_F,
        help="Retry a failed MCP-submitted trace using saved metadata",
    )
    trace_retry.add_argument(
        "path",
        help=(
            "Path returned as submitted_trace_path, or the matching "
            ".lerim-submission.json manifest path."
        ),
    )
    trace_retry.add_argument(
        "--force",
        action="store_true",
        help="Force re-extraction even when the submitted trace was already imported.",
    )
    trace_submissions = trace_sub.add_parser(
        "submissions",
        formatter_class=_F,
        help="List recent MCP-submitted trace manifests",
    )
    trace_submissions.add_argument(
        "--status",
        default="all",
        help="Filter by status such as failed, imported, duplicate_skipped, or all.",
    )
    trace_submissions.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum submitted traces to show.",
    )
    trace.set_defaults(func=_cmd_trace)
    trace_import.set_defaults(func=_cmd_trace)
    trace_retry.set_defaults(func=_cmd_trace)
    trace_submissions.set_defaults(func=_cmd_trace)

    # ── curate ───────────────────────────────────────────────────────
    curate = sub.add_parser(
        "curate",
        formatter_class=_F,
        help="Refine existing records offline (cold path)",
        description=(
            "Offline record refinement: merge duplicates, archive low-value items.\n\n"
            "Examples:\n"
            "  lerim curate            # one pass\n"
            "  lerim curate --dry-run  # preview only"
        ),
    )
    _add_dry_run_flag(curate)
    curate.set_defaults(func=_cmd_curate)

    curate_alias = sub.add_parser(
        "maintain",
        formatter_class=_F,
        help="Deprecated alias for `lerim curate`",
        description=(
            "`lerim maintain` is a deprecated compatibility alias for `lerim curate`.\n\n"
            "Examples:\n"
            "  lerim curate            # one pass\n"
            "  lerim curate --dry-run  # preview only"
        ),
    )
    _add_dry_run_flag(curate_alias)
    curate_alias.set_defaults(func=_cmd_curate)

    # ── context-brief ────────────────────────────────────────────────
    context_brief = sub.add_parser(
        "context-brief",
        formatter_class=_F,
        help="Read or refresh generated long-term startup context",
        description=(
            "Generated long-term markdown startup context for agents.\n\n"
            "Examples:\n"
            "  lerim context-brief show\n"
            "  lerim context-brief status\n"
            "  lerim context-brief refresh --force"
        ),
    )
    _add_context_brief_subcommands(context_brief)
    context_brief.set_defaults(func=_cmd_context_brief)

    working_memory = sub.add_parser(
        "working-memory",
        formatter_class=_F,
        help="Read or refresh short-term recent project memory",
        description=(
            "Short-term generated memory from recent persisted context changes.\n"
            "Use Context Brief for long-term durable project context.\n\n"
            "Examples:\n"
            "  lerim working-memory show\n"
            "  lerim working-memory status\n"
            "  lerim working-memory refresh --force"
        ),
    )
    _add_working_memory_subcommands(working_memory)
    working_memory.set_defaults(func=_cmd_working_memory)

    # ── dashboard ────────────────────────────────────────────────────
    dashboard = sub.add_parser(
        "dashboard",
        formatter_class=_F,
        help="Start the local dashboard UI",
        description=(
            "Start the local dashboard UI, ensuring the Lerim backend is running. "
            "Run `lerim up --build` first when you want the backend built from the "
            "local Dockerfile."
        ),
    )
    dashboard.add_argument(
        "--port",
        type=int,
        default=3000,
        help="Dashboard UI port (default: 3000).",
    )
    dashboard.set_defaults(func=_cmd_dashboard)

    # ── answer ───────────────────────────────────────────────────────
    answer = sub.add_parser(
        "answer",
        formatter_class=_F,
        help="Answer a question using stored context records",
        description=(
            "Query Lerim using stored context records.\n\n"
            "Example: lerim answer 'What auth pattern do we use?'"
        ),
    )
    answer.add_argument(
        "question", help="Your question (use quotes if it contains spaces)."
    )
    answer.add_argument(
        "--scope",
        choices=["all", "project"],
        default="all",
        help="Read scope: all projects (default) or one project.",
    )
    answer.add_argument("--project", help="Project name/path when --scope=project.")
    answer.add_argument(
        "--verbose",
        action="store_true",
        help="Show context-answerer retrieval steps and concise results.",
    )
    answer.set_defaults(func=_cmd_answer)

    answer_alias = sub.add_parser(
        "ask",
        formatter_class=_F,
        help="Deprecated alias for `lerim answer`",
        description=(
            "`lerim ask` is a deprecated compatibility alias for `lerim answer`.\n\n"
            "Example: lerim answer 'What auth pattern do we use?'"
        ),
    )
    answer_alias.add_argument(
        "question", help="Your question (use quotes if it contains spaces)."
    )
    answer_alias.add_argument(
        "--scope",
        choices=["all", "project"],
        default="all",
        help="Read scope: all projects (default) or one project.",
    )
    answer_alias.add_argument(
        "--project", help="Project name/path when --scope=project."
    )
    answer_alias.add_argument(
        "--verbose",
        action="store_true",
        help="Show context-answerer retrieval steps and concise results.",
    )
    answer_alias.set_defaults(func=_cmd_answer)

    # ── query ────────────────────────────────────────────────────────
    query = sub.add_parser(
        "query",
        formatter_class=_F,
        help="Run deterministic context queries",
        description=(
            "Deterministic list/count queries over records, versions, or sessions.\n\n"
            "Example: lerim query records list --kind decision --limit 10"
        ),
    )
    query.add_argument("entity", choices=QUERY_ENTITIES)
    query.add_argument("mode", choices=QUERY_MODES)
    query.add_argument("--scope", choices=["all", "project"], default="all")
    query.add_argument("--project", help="Project name/path when --scope=project.")
    query.add_argument("--kind")
    query.add_argument("--status")
    query.add_argument(
        "--source-profile",
        help="Filter records by source profile, for example support or ops.",
    )
    query.add_argument("--source-session-id")
    query.add_argument("--created-since")
    query.add_argument("--created-until")
    query.add_argument("--updated-since")
    query.add_argument("--updated-until")
    query.add_argument("--valid-at")
    query.add_argument("--order-by", choices=QUERY_ORDER_FIELDS, default="created_at")
    query.add_argument("--limit", type=int, default=20)
    query.add_argument("--offset", type=int, default=0)
    query.add_argument("--include-total", action="store_true")
    query.set_defaults(func=_cmd_query)

    # ── context ────────────────────────────────────────────────────────
    context = sub.add_parser(
        "context",
        formatter_class=_F,
        help="List context records",
        description="List and inspect stored context records.",
    )
    context_sub = context.add_subparsers(dest="context_action")
    context_records = context_sub.add_parser(
        "records",
        formatter_class=_F,
        help="List context records by profile and kind.",
        description="List context records with profile/kind filters.",
    )
    context_records.add_argument("--scope", choices=["all", "project"], default="all")
    context_records.add_argument(
        "--project", help="Project name/path when --scope=project."
    )
    context_records.add_argument(
        "--profile", help="Filter by source profile, for example support or ops."
    )
    context_records.add_argument(
        "--source-profile",
        dest="source_profile",
        help="Backward-compatible alias for --profile.",
    )
    context_records.add_argument(
        "--type",
        dest="kind",
        help="Filter by durable record kind, for example fact or constraint.",
    )
    context_records.add_argument(
        "--status",
        help="Filter by storage record status, for example active or archived.",
    )
    context_records.add_argument(
        "--source-session-id",
        help="Filter records created from one source session.",
    )
    context_records.add_argument("--limit", type=int, default=20)
    context_records.add_argument("--offset", type=int, default=0)
    context_records.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output.",
    )
    context_records.set_defaults(func=_cmd_context_records)

    # ── profile ──────────────────────────────────────────────────────
    profile = sub.add_parser(
        "profile",
        formatter_class=_F,
        help="List source profiles and profile-specific record stats",
        description=(
            "Inspect and register source profiles used by trace extraction.\n\n"
            "Examples:\n"
            "  lerim profile list\n"
            "  lerim profile show support\n"
            "  lerim profile validate ./research.yaml\n"
            "  lerim profile register ./research.yaml"
        ),
    )
    profile_sub = profile.add_subparsers(dest="profile_action")
    profile_sub.add_parser(
        "list",
        formatter_class=_F,
        help="List bundled and registered source profiles",
    )
    profile_show = profile_sub.add_parser(
        "show",
        formatter_class=_F,
        help="Show a profile's recent records and signal mix",
    )
    profile_show.add_argument("name", help="Profile name, for example support or coding.")
    profile_show.add_argument(
        "--kind",
        help="Filter only this record kind, for example decision, fact, or episode.",
    )
    profile_show.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of records to inspect and report (default: 20).",
    )
    profile_validate = profile_sub.add_parser(
        "validate",
        formatter_class=_F,
        help="Validate a custom source-profile YAML file",
        description=(
            "Validate a custom source-profile YAML file without changing Lerim config."
        ),
    )
    profile_validate.add_argument(
        "path",
        help="Path to a custom source-profile YAML file.",
    )
    profile_register = profile_sub.add_parser(
        "register",
        formatter_class=_F,
        help="Register a custom source-profile YAML file",
        description=(
            "Validate a custom source-profile YAML file and register it in "
            "the active Lerim config under [profiles]."
        ),
    )
    profile_register.add_argument(
        "path",
        help="Path to a custom source-profile YAML file.",
    )
    profile_register.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing registration for the same source-profile id.",
    )
    profile.set_defaults(func=_cmd_profile)

    # ── status ───────────────────────────────────────────────────────
    status = sub.add_parser(
        "status",
        formatter_class=_F,
        help="Show runtime status (platforms, context records, queue, last runs)",
        description="Runtime summary: platforms, context records, queue stats, last runs.",
    )
    status.add_argument(
        "--scope",
        choices=["all", "project"],
        default="all",
        help="Status scope: all projects (default) or one project.",
    )
    status.add_argument(
        "--project",
        help="Project name/path when --scope=project.",
    )
    status.add_argument(
        "--live",
        action="store_true",
        help="Live refresh mode for status output.",
    )
    status.add_argument(
        "--interval",
        type=float,
        default=3.0,
        help="Refresh interval in seconds for --live. (default: 3.0)",
    )
    status.set_defaults(func=_cmd_status)

    # ── queue ─────────────────────────────────────────────────────────
    queue = sub.add_parser(
        "queue",
        formatter_class=_F,
        help="Display the session extraction queue",
        description=(
            "Show session extraction queue (reads local SQLite catalog).\n\n"
            "Examples:\n"
            "  lerim queue                  # all jobs\n"
            "  lerim queue --failed         # dead_letter + failed only"
        ),
    )
    queue.add_argument(
        "--failed", action="store_true", help="Show only failed + dead_letter jobs."
    )
    queue.add_argument(
        "--status",
        help="Filter by specific status (pending, running, failed, dead_letter, done).",
    )
    queue.add_argument("--project", help="Filter by exact project name/path.")
    queue.set_defaults(func=_cmd_queue)

    # ── unscoped ─────────────────────────────────────────────────────
    unscoped = sub.add_parser(
        "unscoped",
        formatter_class=_F,
        help="List indexed sessions with no project match",
        description=(
            "Show sessions that were indexed but not mapped to registered projects.\n\n"
            "Use this to debug why some sessions are skipped in strict project mode."
        ),
    )
    unscoped.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum items to display. (default: 50)",
    )
    unscoped.set_defaults(func=_cmd_unscoped)

    # ── retry ─────────────────────────────────────────────────────────
    retry = sub.add_parser(
        "retry",
        formatter_class=_F,
        help="Retry failed and dead_letter session jobs",
        description=(
            "Reset failed and dead_letter jobs to pending for re-processing.\n\n"
            "Example: lerim retry a1b2c3d4"
        ),
    )
    _add_dead_letter_args(retry, verb="retry", noun="failed/dead_letter")
    retry.set_defaults(func=_cmd_retry)

    # ── skip ──────────────────────────────────────────────────────────
    skip = sub.add_parser(
        "skip",
        formatter_class=_F,
        help="Skip dead_letter session jobs",
        description=(
            "Mark dead_letter jobs as done, unblocking the project queue.\n\n"
            "Example: lerim skip a1b2c3d4"
        ),
    )
    _add_dead_letter_args(skip, verb="skip")
    skip.set_defaults(func=_cmd_skip)

    # ── init ─────────────────────────────────────────────────────────
    init = sub.add_parser(
        "init",
        formatter_class=_F,
        help="Interactive setup wizard",
        description="Interactive setup wizard: detect agents, configure API keys.",
    )
    init.set_defaults(func=_cmd_init)

    # ── project ──────────────────────────────────────────────────────
    project = sub.add_parser(
        "project",
        formatter_class=_F,
        help="Manage registered projects (add, list, remove)",
        description="Register, list, or remove projects.",
    )
    project_sub = project.add_subparsers(dest="project_action")

    proj_add = project_sub.add_parser(
        "add",
        formatter_class=_F,
        help="Register a project directory",
    )
    proj_add.add_argument("path", help="Path to the project directory.")
    proj_add.add_argument(
        "--type",
        dest="project_type",
        choices=["supported", "custom"],
        default="supported",
        help=(
            "Project source type. Use 'supported' for normal projects connected to "
            "Claude/Codex/Cursor/OpenCode/pi adapters; use 'custom' for folders of "
            "already-clean Lerim canonical JSONL traces."
        ),
    )
    proj_add.add_argument(
        "--source-profile",
        help=(
            "Default source profile for this project. Useful for custom trace "
            "folders that should always extract through one registered profile."
        ),
    )

    project_sub.add_parser(
        "list",
        formatter_class=_F,
        help="List registered projects",
    )

    proj_remove = project_sub.add_parser(
        "remove",
        formatter_class=_F,
        help="Unregister a project",
    )
    proj_remove.add_argument("name", help="Short name of the project to remove.")

    project.set_defaults(func=_cmd_project)

    # ── memory ───────────────────────────────────────────────────────
    memory = sub.add_parser(
        "memory",
        formatter_class=_F,
        help="Reset learned memory while keeping setup",
        description="Reset Lerim learned memory without removing config, API keys, agents, or project registration.",
    )
    memory_sub = memory.add_subparsers(dest="memory_action")
    memory_reset = memory_sub.add_parser(
        "reset",
        formatter_class=_F,
        help="Reset context records and session index state",
        description=(
            "Reset learned context and indexing state so sessions can be re-indexed and re-extracted.\n\n"
            "Examples:\n"
            "  lerim memory reset --project myrepo --yes\n"
            "  lerim memory reset --all --yes"
        ),
    )
    reset_scope = memory_reset.add_mutually_exclusive_group(required=True)
    reset_scope.add_argument(
        "--project", help="Registered project name or path to reset."
    )
    reset_scope.add_argument(
        "--all",
        action="store_true",
        help="Reset memory for all registered projects.",
    )
    memory_reset.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt.",
    )
    memory.set_defaults(func=_cmd_memory)

    # ── up ───────────────────────────────────────────────────────────
    up = sub.add_parser(
        "up",
        formatter_class=_F,
        help="Start the Docker container",
        description=(
            "Start the Docker container (pulls GHCR image by default).\n\n"
            "Example: lerim up --build  # build locally instead"
        ),
    )
    up.add_argument(
        "--build",
        action="store_true",
        help="Build the Docker image from local Dockerfile instead of pulling from GHCR.",
    )
    up.add_argument(
        "--no-build",
        action="store_true",
        help="Reuse the existing image without rebuilding when the compose file is in local-build mode.",
    )
    up.set_defaults(func=_cmd_up)

    # ── down ─────────────────────────────────────────────────────────
    down = sub.add_parser(
        "down",
        formatter_class=_F,
        help="Stop the Docker container",
        description="Stop the running Lerim Docker container.",
    )
    down.set_defaults(func=_cmd_down)

    # ── logs ─────────────────────────────────────────────────────────
    logs = sub.add_parser(
        "logs",
        formatter_class=_F,
        help="View local Lerim log entries",
        description=(
            "Display log entries from ~/.lerim/logs/YYYY/MM/DD/lerim.jsonl (last 50).\n\n"
            "Examples:\n"
            "  lerim logs --level error  # filter by level\n"
            "  lerim logs -f             # live tail"
        ),
    )
    logs.add_argument(
        "--follow",
        "-f",
        action="store_true",
        help="Live tail: watch for new log lines and print as they appear.",
    )
    logs.add_argument(
        "--level",
        default=None,
        help="Filter by log level (case-insensitive). E.g. error, warning, info.",
    )
    logs.add_argument(
        "--since",
        default=None,
        help="Show entries from the last N hours/minutes/days. Format: 1h, 30m, 2d.",
    )
    logs.add_argument(
        "--json",
        dest="raw_json",
        action="store_true",
        help="Output raw JSONL lines instead of formatted text.",
    )
    logs.set_defaults(func=_cmd_logs)

    # ── serve ────────────────────────────────────────────────────────
    serve = sub.add_parser(
        "serve",
        formatter_class=_F,
        help="Start HTTP API + daemon loop (Docker entrypoint)",
        description="HTTP API + daemon loop in one process (Docker entrypoint).",
    )
    serve.add_argument(
        "--host", help="Bind address (default: from config [server].host)."
    )
    serve.add_argument("--port", type=int, help="Bind port (default: 8765).")
    serve.set_defaults(func=_cmd_serve)

    # ── skill ─────────────────────────────────────────────────────────
    skill = sub.add_parser(
        "skill",
        formatter_class=_F,
        help="Install Lerim skill files into coding agent directories",
        description="Install Lerim skill files into agent directories.",
    )
    skill_sub = skill.add_subparsers(dest="skill_action")
    skill_sub.add_parser(
        "install",
        formatter_class=_F,
        help="Copy skill files into agent directories",
    )
    skill.set_defaults(func=_cmd_skill)

    # ── auth ──────────────────────────────────────────────────────────
    auth = sub.add_parser(
        "auth",
        formatter_class=_F,
        help="Authenticate with Lerim Cloud",
        description=(
            "Authenticate with Lerim Cloud (login, status, logout).\n\n"
            "Examples:\n"
            "  lerim auth               # browser-based login\n"
            "  lerim auth status        # check auth state"
        ),
    )
    auth.add_argument(
        "--token",
        default=None,
        help="Authenticate with a token directly (skip browser flow).",
    )
    auth_sub = auth.add_subparsers(dest="auth_command")

    auth_sub.add_parser(
        "login",
        formatter_class=_F,
        help="Log in to Lerim Cloud (same as bare `lerim auth`)",
    )

    auth_sub.add_parser(
        "status",
        formatter_class=_F,
        help="Check current authentication state",
    )

    auth_sub.add_parser(
        "logout",
        formatter_class=_F,
        help="Remove stored credentials",
    )

    auth.set_defaults(func=_cmd_auth_dispatch)

    return parser


_TRACE_COMMANDS = frozenset({"serve"})


def main(argv: list[str] | None = None) -> int:
    """Entrypoint for CLI invocation with global flags and dispatch."""
    raw_argv = list(argv or sys.argv[1:])
    # Determine subcommand early to skip heavy init for lightweight commands.
    first_arg = next((a for a in raw_argv if not a.startswith("-")), None)
    wants_help = any(arg in {"-h", "--help"} for arg in raw_argv)
    configure_logging()
    if first_arg in _TRACE_COMMANDS and not wants_help:
        configure_tracing(get_config())
    parser = build_parser()
    args = parser.parse_args(_hoist_global_json_flag(list(argv or sys.argv[1:])))

    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    if args.command == "project" and not getattr(args, "project_action", None):
        parser.parse_args([args.command, "--help"])
        return 0

    if args.command == "memory" and not getattr(args, "memory_action", None):
        parser.parse_args([args.command, "--help"])
        return 0

    if args.command == "context-brief" and not getattr(
        args,
        "context_brief_action",
        None,
    ):
        parser.parse_args([args.command, "--help"])
        return 0

    if args.command == "working-memory" and not getattr(
        args,
        "working_memory_action",
        None,
    ):
        parser.parse_args([args.command, "--help"])
        return 0

    if args.command == "trace" and not getattr(args, "trace_action", None):
        parser.parse_args([args.command, "--help"])
        return 0

    if args.command == "profile" and not getattr(args, "profile_action", None):
        parser.parse_args([args.command, "--help"])
        return 0

    if args.command == "context" and not getattr(args, "context_action", None):
        parser.parse_args([args.command, "--help"])
        return 0

    if args.command == "skill" and not getattr(args, "skill_action", None):
        parser.parse_args([args.command, "--help"])
        return 0

    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return 2
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
