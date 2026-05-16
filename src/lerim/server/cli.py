"""Command-line interface for Lerim runtime and service operations.

Service commands (answer, ingest, curate, status) are thin HTTP clients that
talk to a running Lerim server (started via ``lerim up`` or ``lerim serve``).
Host-only commands (init, project, up, down, logs, connect)
run locally and never require an HTTP server.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from lerim import __version__
from lerim.adapters.registry import (
    KNOWN_PLATFORMS,
    connect_platform,
    list_platforms,
    load_platforms,
    remove_platform,
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
    run_curate_once,
    run_ingest_once,
    run_context_brief_daily,
    run_context_brief_for_project,
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
from lerim.config.settings import get_config, get_user_config_path, get_user_env_path
from lerim.config.tracing import configure_tracing
from lerim.context import ContextStore
from lerim.context.query_spec import QUERY_ENTITIES, QUERY_MODES, QUERY_ORDER_FIELDS
from lerim.context_brief import (
    resolve_context_brief_project,
    status_to_dict,
    context_brief_paths,
    context_brief_status,
)

_LEGACY_COMMAND_ALIASES = {
    "sync": "ingest",
    "maintain": "curate",
    "ask": "answer",
    "working-memory": "context-brief",
}
_LEGACY_COMMAND_REMOVAL_VERSION = "v0.3.0"


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

    if action == "list" or action is None:
        entries = list_platforms(platforms_path)
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
                _emit(f"Context Brief skipped for {project.name}: {result.get('skip_reason')}")
            elif result.get("status") == "failed":
                _emit(f"Context Brief failed for {project.name}: {result.get('error')}", file=sys.stderr)
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
        return "Refresh if newest persisted DB context matters."
    if status.get("availability") == "available":
        return "Continue with this startup context; inspect sources or query deeper if needed."
    return str(status.get("suggested_action") or "Run `lerim context-brief status`.")


def _cmd_dashboard(args: argparse.Namespace) -> int:
    """Show dashboard transition message."""
    print()
    print("  Lerim Dashboard is moving to the cloud.")
    print("  The new dashboard will be available at https://lerim.dev")
    print()
    print("  In the meantime, use these CLI commands:")
    print("    lerim status        - system overview")
    print("    lerim answer        - query your stored context")
    print("    lerim queue         - view session processing queue")
    print("    lerim ingest        - process new sessions")
    print("    lerim curate        - refine stored records")
    print("    lerim context-brief - read generated startup context")
    print()
    return 0


def _cmd_trace(args: argparse.Namespace) -> int:
    """Handle host-only generic trace commands."""
    action = getattr(args, "trace_action", None)
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
                content = part.get("content") if isinstance(part.get("content"), dict) else {}
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
) -> int:
    """Shared handler for retry/skip dead_letter operations."""
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

    if do_all:
        count = int(all_fn())
        if count == 0:
            _emit(f"No dead_letter jobs to {verb.lower()}.")
            return 0
        _emit(f"{verb} {count} dead_letter job(s).")
        _emit(_format_queue_counts(count_session_jobs_by_status()))
        return 0

    if project:
        repo_path = _resolve_project_repo_path(project)
        if not repo_path:
            _emit(f"Project not found: {project}", file=sys.stderr)
            return 1
        count = project_fn(repo_path)
        _emit(f"{verb} {count} dead_letter job(s) for project {project}.")
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
        _emit(f"Job {run_id} is not in dead_letter status.", file=sys.stderr)
        return 1
    return 0


def _cmd_retry(args: argparse.Namespace) -> int:
    """Retry dead_letter jobs."""
    return _dead_letter_action(
        args,
        verb="Retried",
        single_fn_name="retry_session_job",
        project_fn_name="retry_project_jobs",
        all_fn_name="retry_all_dead_letter_jobs",
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

    # Step 1: Detect coding agents
    _emit("\n── Coding Agents ──────────────────────────────────")
    _emit("")
    _emit("  Which coding agents do you use?")
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
            _emit(f"  {p['name']}: {p['path']} ({status})")
        return 0

    if action == "add":
        path_str = getattr(args, "path", None)
        if not path_str:
            _emit("Usage: lerim project add <path>", file=sys.stderr)
            return 2
        result = api_project_add(path_str)
        if result.get("error"):
            _emit(result["error"], file=sys.stderr)
            return 1
        _emit(f'Added project "{result["name"]}" ({result["path"]})')
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
    build_local = bool(getattr(args, "build", False)) or current_compose_uses_local_build()
    result = api_up(build_local=build_local)
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


def _cmd_serve(args: argparse.Namespace) -> int:
    """Start HTTP API + daemon loop in one process (web UI is Lerim Cloud)."""
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
                        _code, summary = run_ingest_once(
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
                except Exception as exc:
                    logger.warning("daemon ingest error: {}", exc)
                last_ingest = time.monotonic()

            if now - last_curate >= curate_interval:
                try:
                    with ollama_lifecycle(config):
                        _code, details = run_curate_once(
                            dry_run=False,
                            trigger="daemon",
                        )
                    logger.info("daemon curate done — {}", details)
                except Exception as exc:
                    logger.warning("daemon curate error: {}", exc)
                last_curate = time.monotonic()

            if now - last_context_brief >= context_brief_interval:
                try:
                    with ollama_lifecycle(config):
                        details = run_context_brief_daily(trigger="daily")
                    logger.info("daemon context-brief done — {}", details)
                except Exception as exc:
                    logger.warning("daemon context-brief error: {}", exc)
                last_context_brief = time.monotonic()

            # Ship to cloud (best-effort)
            if config.cloud_token:
                try:
                    from lerim.cloud.shipper import ship_once
                    import asyncio

                    results = asyncio.run(ship_once(config))
                    if results:
                        logger.info("cloud ship: {}", results)
                except Exception as exc:
                    logger.warning("cloud ship error: {}", exc)

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
                min(next_ingest, next_curate, next_context_brief)
                - time.monotonic(),
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
        help="Target a single session by its run ID. Bypasses the normal index scan "
        "and fetches this session directly. Use with --force to re-extract.",
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


def _add_dead_letter_args(parser: argparse.ArgumentParser, *, verb: str) -> None:
    """Add run_id, --project, and --all arguments for dead-letter commands."""
    parser.add_argument(
        "run_id",
        nargs="?",
        help=f"Run ID (or prefix, min 6 chars) of the job to {verb}.",
    )
    parser.add_argument(
        "--project",
        help=f"{verb.capitalize()} all dead_letter jobs for a project (by name).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help=f"{verb.capitalize()} all dead_letter jobs across all projects.",
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
            "  lerim connect auto              # auto-detect all platforms\n"
            "  lerim connect claude             # connect Claude"
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
    connect.set_defaults(func=_cmd_connect)

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
            "--source-profile generic --scope-type domain --scope support"
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
        help="Source profile/category, for example generic, support, research, or web.",
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
    trace.set_defaults(func=_cmd_trace)
    trace_import.set_defaults(func=_cmd_trace)

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
        help="Read or refresh generated startup context",
        description=(
            "Generated markdown startup context for agents.\n\n"
            "Examples:\n"
            "  lerim context-brief show\n"
            "  lerim context-brief status\n"
            "  lerim context-brief refresh --force"
        ),
    )
    _add_context_brief_subcommands(context_brief)
    context_brief.set_defaults(func=_cmd_context_brief)

    context_brief_alias = sub.add_parser(
        "working-memory",
        formatter_class=_F,
        help="Deprecated alias for `lerim context-brief`",
        description=(
            "`lerim working-memory` is a deprecated compatibility alias for "
            "`lerim context-brief`.\n\n"
            "Examples:\n"
            "  lerim context-brief show\n"
            "  lerim context-brief status\n"
            "  lerim context-brief refresh --force"
        ),
    )
    _add_context_brief_subcommands(context_brief_alias)
    context_brief_alias.set_defaults(func=_cmd_context_brief)

    # ── dashboard ────────────────────────────────────────────────────
    dashboard = sub.add_parser(
        "dashboard",
        formatter_class=_F,
        help="Show Lerim Cloud web UI link",
        description="Print Lerim Cloud web UI link and CLI alternatives.",
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
    answer_alias.add_argument("question", help="Your question (use quotes if it contains spaces).")
    answer_alias.add_argument(
        "--scope",
        choices=["all", "project"],
        default="all",
        help="Read scope: all projects (default) or one project.",
    )
    answer_alias.add_argument("--project", help="Project name/path when --scope=project.")
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
        help="Retry dead_letter session jobs",
        description=(
            "Reset dead_letter jobs to pending for re-processing.\n\n"
            "Example: lerim retry a1b2c3d4"
        ),
    )
    _add_dead_letter_args(retry, verb="retry")
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

    if args.command in {"context-brief", "working-memory"} and not getattr(
        args,
        "context_brief_action",
        None,
    ):
        parser.parse_args([args.command, "--help"])
        return 0

    if args.command == "trace" and not getattr(args, "trace_action", None):
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
