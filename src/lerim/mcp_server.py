"""MCP server entrypoint for Lerim context tools."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from lerim.config.settings import get_config
from lerim.context import ContextStore, resolve_project_identity
from lerim.context_brief import (
    context_brief_paths,
    context_brief_status,
    resolve_context_brief_project,
    status_to_dict,
)
from lerim.server.api import api_answer, api_query, api_status
from lerim.server.daemon import run_context_brief_for_project, run_working_memory_for_project
from lerim.traces import import_trace_file
from lerim.traces.submissions import (
    create_submission_manifest,
    mark_submission_failed,
    mark_submission_succeeded,
)
from lerim.working_memory import (
    working_memory_paths,
    working_memory_status,
    working_memory_status_to_dict,
)


SERVER_INSTRUCTIONS = """\
Lerim compiles completed agent sessions into reusable, evidence-backed context.
Use read tools to recover project context before work. Submit completed source
sessions with lerim_trace_submit instead of saving arbitrary memories.
"""


def create_mcp_server() -> FastMCP:
    """Create a configured FastMCP server exposing Lerim context tools."""
    mcp = FastMCP(
        "lerim",
        instructions=SERVER_INSTRUCTIONS,
        website_url="https://lerim.dev",
    )

    @mcp.tool(
        name="lerim_context_brief",
        description=(
            "Return the generated Lerim Context Brief for a project. "
            "Set refresh=true to regenerate it through Lerim's compiler first."
        ),
    )
    def lerim_context_brief(
        project: str | None = None,
        refresh: bool = False,
        max_chars: int = 12000,
    ) -> dict[str, Any]:
        """Return the current project startup context brief."""
        def _impl() -> dict[str, Any]:
            """Load or refresh the brief while stdout is redirected."""
            cfg = get_config()
            resolved = resolve_context_brief_project(
                config=cfg,
                project=project,
                cwd=Path.cwd(),
            )
            if refresh:
                run_context_brief_for_project(
                    project_name=resolved.name,
                    project_path=resolved.identity.repo_path,
                    trigger="mcp",
                    force=True,
                )
            store = ContextStore(cfg.context_db_path)
            status = context_brief_status(config=cfg, store=store, project=resolved)
            paths = context_brief_paths(cfg, resolved.identity.project_id)
            content = ""
            if paths.current_file.is_file():
                content = paths.current_file.read_text(encoding="utf-8", errors="replace")
            truncated = False
            budget = max(1000, int(max_chars or 12000))
            if len(content) > budget:
                content = content[:budget].rstrip() + "\n\n[truncated]"
                truncated = True
            return {
                "error": False,
                "project": resolved.name,
                "project_id": resolved.identity.project_id,
                "repo_path": str(resolved.identity.repo_path),
                "status": status_to_dict(status),
                "content": content,
                "truncated": truncated,
                "suggested_action": status.suggested_action,
            }

        return _run_with_stdout_guard(_impl)

    @mcp.tool(
        name="lerim_working_memory",
        description=(
            "Return Lerim Working Memory for recent project movement. "
            "Set refresh=true to regenerate the short-term artifact first."
        ),
    )
    def lerim_working_memory(
        project: str | None = None,
        refresh: bool = False,
        max_chars: int = 12000,
    ) -> dict[str, Any]:
        """Return the current project short-term Working Memory."""
        def _impl() -> dict[str, Any]:
            """Load or refresh Working Memory while stdout is redirected."""
            cfg = get_config()
            resolved = resolve_context_brief_project(
                config=cfg,
                project=project,
                cwd=Path.cwd(),
            )
            if refresh:
                run_working_memory_for_project(
                    project_name=resolved.name,
                    project_path=resolved.identity.repo_path,
                    trigger="mcp",
                    force=True,
                )
            store = ContextStore(cfg.context_db_path)
            status = working_memory_status(config=cfg, store=store, project=resolved)
            paths = working_memory_paths(cfg, resolved.identity.project_id)
            content = ""
            if paths.current_file.is_file():
                content = paths.current_file.read_text(encoding="utf-8", errors="replace")
            truncated = False
            budget = max(1000, int(max_chars or 12000))
            if len(content) > budget:
                content = content[:budget].rstrip() + "\n\n[truncated]"
                truncated = True
            return {
                "error": False,
                "project": resolved.name,
                "project_id": resolved.identity.project_id,
                "repo_path": str(resolved.identity.repo_path),
                "status": working_memory_status_to_dict(status),
                "content": content,
                "truncated": truncated,
                "suggested_action": status.suggested_action,
            }

        return _run_with_stdout_guard(_impl)

    @mcp.tool(
        name="lerim_context_answer",
        description=(
            "Ask Lerim a question and receive a grounded answer from stored "
            "context records."
        ),
    )
    def lerim_context_answer(
        question: str,
        scope: str = "all",
        project: str | None = None,
        verbose: bool = False,
    ) -> dict[str, Any]:
        """Answer a question using Lerim's context-answerer."""
        if not str(question or "").strip():
            return {"error": True, "message": "question_required"}
        return _run_with_stdout_guard(
            lambda: api_answer(
                str(question),
                scope=scope,
                project=project,
                verbose=bool(verbose),
            )
        )

    @mcp.tool(
        name="lerim_context_search",
        description=(
            "Run hybrid retrieval over Lerim context records for a query. "
            "Returns compact record hits with ids suitable for citation."
        ),
    )
    def lerim_context_search(
        query: str,
        scope: str = "all",
        project: str | None = None,
        kind: str | None = None,
        record_role: str | None = None,
        status: str = "active",
        limit: int = 8,
    ) -> dict[str, Any]:
        """Search stored context records with Lerim retrieval."""
        if not str(query or "").strip():
            return {"error": True, "message": "query_required"}
        def _impl() -> dict[str, Any]:
            """Run the store search while stdout is redirected."""
            cfg = get_config()
            project_ids, projects_used = _project_ids_for_scope(
                scope=scope,
                project=project,
            )
            store = ContextStore(cfg.context_db_path)
            statuses = [status] if str(status or "").strip() else None
            hits = store.search(
                project_ids=project_ids,
                query=str(query),
                kind_filters=[kind] if kind else None,
                role_filters=[record_role] if record_role else None,
                statuses=statuses,
                limit=max(1, min(int(limit or 8), 50)),
            )
            return {
                "error": False,
                "query": query,
                "projects_used": projects_used,
                "rows": [
                    {
                        "record_id": hit.record_id,
                        "project_id": hit.project_id,
                        "kind": hit.kind,
                        "record_role": hit.record_role,
                        "role_payload": hit.role_payload,
                        "title": hit.title,
                        "body": hit.body,
                        "status": hit.status,
                        "score": hit.score,
                        "sources": hit.sources,
                        "decision": hit.decision,
                        "why": hit.why,
                        "updated_at": hit.updated_at,
                    }
                    for hit in hits
                ],
            }

        return _run_with_stdout_guard(_impl)

    @mcp.tool(
        name="lerim_records_list",
        description="List Lerim context records deterministically with filters.",
    )
    def lerim_records_list(
        scope: str = "all",
        project: str | None = None,
        kind: str | None = None,
        record_role: str | None = None,
        status: str | None = "active",
        source_profile: str | None = None,
        source_session_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List context records through Lerim's deterministic query API."""
        return _run_with_stdout_guard(
            lambda: api_query(
                entity="records",
                mode="list",
                scope=scope,
                project=project,
                kind=kind,
                record_role=record_role,
                status=status,
                source_profile=source_profile,
                source_session_id=source_session_id,
                order_by="updated_at",
                limit=max(1, min(int(limit or 20), 100)),
                offset=max(0, int(offset or 0)),
                include_total=True,
            )
        )

    @mcp.tool(
        name="lerim_trace_submit",
        description=(
            "Submit a completed agent session transcript to Lerim for normal "
            "trace import and source-session extraction."
        ),
    )
    def lerim_trace_submit(
        trace_text: str,
        source_name: str,
        source_profile: str = "coding",
        scope_type: str = "project",
        scope: str | None = None,
        scope_label: str | None = None,
        session_id: str | None = None,
        filename_hint: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Submit one completed source session for Lerim extraction."""
        text = str(trace_text or "").strip()
        if not text:
            return {"error": True, "message": "trace_text_required"}
        resolved_scope = scope or str(Path.cwd())
        cfg = get_config()
        submitted_path = _write_submitted_trace(
            root=cfg.global_data_dir,
            trace_text=text,
            source_name=source_name,
            session_id=session_id,
            filename_hint=filename_hint,
        )
        manifest = create_submission_manifest(
            trace_path=submitted_path,
            source_name=source_name,
            source_profile=source_profile,
            scope_type=scope_type,
            scope=resolved_scope,
            scope_label=scope_label,
            session_id=session_id,
            filename_hint=filename_hint,
            force=bool(force),
        )
        try:
            result = _run_with_stdout_guard(
                lambda: import_trace_file(
                    trace_path=submitted_path,
                    source_name=source_name,
                    source_profile=source_profile,
                    scope_type=scope_type,
                    scope=resolved_scope,
                    scope_label=scope_label,
                    session_id=session_id,
                    force=bool(force),
                    config=cfg,
                )
            )
        except Exception as exc:
            manifest = mark_submission_failed(trace_path=submitted_path, exc=exc)
            return {
                "error": True,
                "message": str(exc),
                "type": type(exc).__name__,
                "submitted_trace_path": str(submitted_path),
                "submission_manifest_path": str(manifest.get("manifest_path") or ""),
                "retry_command": str(manifest.get("retry_command") or ""),
                "attempt_count": int(manifest.get("attempt_count") or 0),
            }
        manifest = mark_submission_succeeded(trace_path=submitted_path, result=result)
        return {
            "error": False,
            "trace_id": result.trace_id,
            "session_id": result.session_id,
            "submitted_trace_path": str(submitted_path),
            "submission_manifest_path": str(manifest.get("manifest_path") or ""),
            "retry_command": str(manifest.get("retry_command") or ""),
            "attempt_count": int(manifest.get("attempt_count") or 0),
            "normalized_trace_path": str(result.normalized_trace_path),
            "scope_type": result.scope_identity.scope_type,
            "scope_id": result.scope_identity.scope_id,
            "scope_label": result.scope_identity.label,
            **result.ingest_result,
        }

    @mcp.tool(
        name="lerim_ingest_status",
        description="Return Lerim runtime, queue, ingest, and project status.",
    )
    def lerim_ingest_status(
        scope: str = "all",
        project: str | None = None,
    ) -> dict[str, Any]:
        """Return a compact status snapshot for ingestion and context health."""
        return _run_with_stdout_guard(lambda: api_status(scope=scope, project=project))

    return mcp


def run_mcp_server() -> None:
    """Run Lerim's MCP server over stdio."""
    if any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
        print(_mcp_server_help())
        return
    create_mcp_server().run("stdio")


def _mcp_server_help() -> str:
    """Return help text for the direct MCP server entrypoint."""
    return "\n".join(
        [
            "usage: lerim-mcp [-h]",
            "",
            "Start Lerim's MCP stdio server for agent clients.",
            "",
            "This is the direct entrypoint used in MCP client configs. For the",
            "normal CLI wrapper, run `lerim mcp`.",
            "",
            "options:",
            "  -h, --help  show this help message and exit",
        ]
    )


def _project_ids_for_scope(
    *,
    scope: str,
    project: str | None,
) -> tuple[list[str] | None, list[str]]:
    """Resolve MCP scope arguments to project ids and display names."""
    from lerim.server.api import _context_store, _resolve_selected_projects

    cfg = get_config()
    normalized_scope = "project" if str(scope or "").strip().lower() == "project" else "all"
    selected = _resolve_selected_projects(
        config=cfg,
        scope=normalized_scope,
        project=project,
    )
    store = _context_store(cfg)
    project_ids: list[str] = []
    projects_used: list[str] = []
    for name, path in selected:
        identity = resolve_project_identity(path)
        store.register_project(identity)
        project_ids.append(identity.project_id)
        projects_used.append(name)
    return project_ids, projects_used


def _run_with_stdout_guard(callback: Callable[[], Any]) -> Any:
    """Redirect tool internals away from the MCP JSON-RPC stdout channel."""
    try:
        sys.stdout.fileno()
        sys.stderr.fileno()
    except (AttributeError, OSError):
        with redirect_stdout(sys.stderr):
            return callback()

    sys.stdout.flush()
    stdout_fd = 1
    stderr_fd = 2
    saved_stdout_fd = os.dup(stdout_fd)
    try:
        os.dup2(stderr_fd, stdout_fd)
        with redirect_stdout(sys.stderr):
            return callback()
    finally:
        sys.stdout.flush()
        os.dup2(saved_stdout_fd, stdout_fd)
        os.close(saved_stdout_fd)


def _write_submitted_trace(
    *,
    root: Path,
    trace_text: str,
    source_name: str,
    session_id: str | None,
    filename_hint: str | None,
) -> Path:
    """Persist an MCP-submitted trace for provenance before import."""
    now = datetime.now(timezone.utc)
    safe_source = _safe_filename_part(source_name or "generic")
    safe_hint = _safe_filename_part(filename_hint or session_id or "session")
    suffix = _trace_suffix(trace_text)
    path = (
        root
        / "workspace"
        / "mcp-submissions"
        / now.strftime("%Y")
        / now.strftime("%m")
        / now.strftime("%d")
        / f"{now.strftime('%H%M%S')}-{safe_source}-{safe_hint}{suffix}"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(trace_text if trace_text.endswith("\n") else f"{trace_text}\n", encoding="utf-8")
    return path


def _safe_filename_part(value: str) -> str:
    """Return a small filesystem-safe slug."""
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value))
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return (cleaned or "trace")[:80]


def _trace_suffix(trace_text: str) -> str:
    """Infer a trace filename suffix from the submitted text."""
    text = trace_text.lstrip()
    if text.startswith("{") or text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return ".jsonl"
        return ".json" if isinstance(parsed, (dict, list)) else ".txt"
    return ".txt"


if __name__ == "__main__":
    """Run the MCP server when invoked as a module."""
    run_mcp_server()
