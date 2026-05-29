"""Local integration benchmark scaffold for Lerim MCP support.

The runner audits every known Lerim MCP target through the real config writer
and doctor code, but points those writes at temporary config paths. That gives
local coverage for target-specific config shapes without mutating real agent
configs or pretending fixtures are installed-agent acceptance.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lerim.integrations.mcp_connect import (
    McpTarget,
    connect_mcp_target,
    doctor_mcp_target,
    known_mcp_targets,
)


EXPECTED_MCP_TOOLS: tuple[str, ...] = (
    "lerim_context_brief",
    "lerim_context_answer",
    "lerim_context_search",
    "lerim_records_list",
    "lerim_trace_submit",
    "lerim_ingest_status",
)

CONFIG_PROBE = "temp_config_writer_doctor"
REAL_DOCTOR_PROBE = "real_config_doctor"
STDIO_TOOLS_PROBE = "stdio_mcp_tools_list"
STDIO_CONTEXT_TOOL_PROBE = "stdio_mcp_context_brief_call"
STDIO_TRACE_SUBMIT_PROBE = "stdio_mcp_trace_submit_duplicate"
STDIO_TRACE_SUBMIT_EXTRACTION_PROBE = "stdio_mcp_trace_submit_extraction"
INSTALLED_CLIENT_PROBE = "real_installed_client_mcp_cli"
TOOL_CALL_PROBE = "real_installed_client_tool_call"
STDIO_TRACE_SUBMIT_PROBES = (
    STDIO_TRACE_SUBMIT_PROBE,
    STDIO_TRACE_SUBMIT_EXTRACTION_PROBE,
)

INSTALLED_CLIENT_PROBES: tuple[dict[str, Any], ...] = (
    {
        "target": "codex",
        "display_name": "Codex CLI",
        "command": ["codex", "mcp", "get", "lerim"],
        "required_markers": ("lerim", "command:", "lerim.mcp_server"),
        "connected_markers": (),
    },
    {
        "target": "claude-code",
        "display_name": "Claude Code",
        "command": ["claude", "mcp", "get", "lerim"],
        "required_markers": ("lerim", "command:", "lerim.mcp_server", "connected"),
        "connected_markers": ("connected",),
    },
    {
        "target": "gemini-cli",
        "display_name": "Gemini CLI",
        "command": ["gemini", "mcp", "list"],
        "required_markers": ("lerim", "connected"),
        "connected_markers": ("connected",),
    },
    {
        "target": "opencode",
        "display_name": "OpenCode",
        "command": ["opencode", "mcp", "list"],
        "required_markers": ("lerim", "connected"),
        "connected_markers": ("connected",),
    },
)

TOOL_CALL_PROMPT = (
    "This is a Lerim MCP acceptance check. Use the Lerim MCP tool "
    "lerim_context_brief for the current project with refresh=false and "
    "max_chars=1000. After the tool returns, print only "
    "LERIM_MCP_TOOL_CALL_ACCEPTED."
)

TOOL_CALL_PROBES: tuple[dict[str, Any], ...] = (
    {
        "target": "claude-code",
        "display_name": "Claude Code",
        "command": [
            "claude",
            "-p",
            TOOL_CALL_PROMPT,
            "--output-format",
            "stream-json",
            "--permission-mode",
            "bypassPermissions",
            "--allowedTools",
            "mcp__lerim__lerim_context_brief",
            "--verbose",
            "--max-budget-usd",
            "{max_budget_usd}",
        ],
        "expected_tool": "lerim_context_brief",
    },
    {
        "target": "gemini-cli",
        "display_name": "Gemini CLI",
        "command": [
            "gemini",
            "-p",
            TOOL_CALL_PROMPT,
            "--approval-mode",
            "yolo",
            "--allowed-mcp-server-names",
            "lerim",
            "--output-format",
            "stream-json",
        ],
        "expected_tool": "lerim_context_brief",
    },
    {
        "target": "opencode",
        "display_name": "OpenCode",
        "command": [
            "opencode",
            "run",
            "--format",
            "json",
            "--title",
            "lerim-mcp-tool-call-acceptance",
            TOOL_CALL_PROMPT,
        ],
        "expected_tool": "lerim_context_brief",
    },
)


def _utc_now() -> str:
    """Return a stable UTC timestamp for report metadata."""
    return datetime.now(timezone.utc).isoformat()


def _timestamp_for_path() -> str:
    """Return a compact UTC timestamp for generated output directories."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _git_commit() -> str:
    """Return the current git commit when available."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        check=False,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_status_short() -> str:
    """Return current short git status when available."""
    result = subprocess.run(
        ["git", "status", "--short"],
        capture_output=True,
        check=False,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _public_git_status(git_status: str) -> str:
    """Return a public-safe git status label for report metadata."""
    if not git_status:
        return ""
    return "<dirty worktree; rerun from clean commit before launch>"


def _environment_metadata() -> dict[str, Any]:
    """Build reproducibility metadata for one local integration run."""
    git_status = _git_status_short()
    return {
        "git_commit": _git_commit(),
        "git_dirty": bool(git_status),
        "git_status_short": _public_git_status(git_status),
        "python": sys.version.split()[0],
        "python_executable": "<python-executable>",
        "platform": platform.platform(),
        "cwd": "<repo-root>",
    }


def _redact_public_string(value: str) -> str:
    """Redact machine-local paths from public benchmark artifacts."""
    text = str(value)
    replacements = {
        sys.executable: "<python-executable>",
        str(Path.cwd().resolve()): "<repo-root>",
        str(Path.home()): "<home>",
    }
    for raw, public in replacements.items():
        if raw:
            text = text.replace(raw, public)
    text = re.sub(r"/var/folders/[^\s\"']+", "<tmp>", text)
    text = re.sub(r"/tmp/[^\s\"']+", "<tmp>", text)
    return text


def _sanitize_public_payload(payload: Any, *, key: str | None = None) -> Any:
    """Return a public-safe copy of report/detail payloads."""
    if isinstance(payload, dict):
        return {
            str(item_key): _sanitize_public_payload(item_value, key=str(item_key))
            for item_key, item_value in payload.items()
        }
    if isinstance(payload, list):
        return [_sanitize_public_payload(item, key=key) for item in payload]
    if isinstance(payload, str):
        if key == "project_id":
            return "<project-id>"
        if key in {"stdout", "stderr"} and payload:
            return "<redacted from public artifact; status fields preserve evidence>"
        return _redact_public_string(payload)
    return payload


def _public_detail_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return a public-safe detail row without local client inventory."""
    sanitized = _sanitize_public_payload(row)
    probe = sanitized.get("probe")
    if probe == REAL_DOCTOR_PROBE:
        return {
            "probe": REAL_DOCTOR_PROBE,
            "target": "<redacted-installed-config-target>",
            "display_name": "<redacted installed config target>",
            "status": sanitized.get("status"),
            "duration_ms": sanitized.get("duration_ms"),
            "acceptance_scope": "real_config_read_only_observation",
            "counts_as_installed_agent_acceptance": False,
        }
    if probe == INSTALLED_CLIENT_PROBE:
        return {
            "probe": INSTALLED_CLIENT_PROBE,
            "target": "<redacted-installed-client-target>",
            "display_name": "<redacted installed client target>",
            "status": sanitized.get("status"),
            "duration_ms": sanitized.get("duration_ms"),
            "connected": sanitized.get("connected"),
            "acceptance_scope": sanitized.get("acceptance_scope"),
            "counts_as_installed_agent_acceptance": False,
            "counts_as_installed_client_connection_acceptance": sanitized.get(
                "counts_as_installed_client_connection_acceptance"
            ),
        }
    return sanitized


def _fixture_target(target: McpTarget, fixture_root: Path) -> McpTarget:
    """Return a target copy whose config path lives under a fixture root."""
    fixture_path = fixture_root / target.name / target.config_path.name
    detect_paths = tuple(fixture_root / target.name / f"detect-{index}" for index, _ in enumerate(target.detect_paths))
    return replace(target, config_path=fixture_path, detect_paths=detect_paths)


def _seed_fixture_config(target: McpTarget) -> None:
    """Create a small existing config so writer merge and backup paths run."""
    target.config_path.parent.mkdir(parents=True, exist_ok=True)
    if target.config_format == "toml_mcp_servers":
        target.config_path.write_text('model = "fixture"\n', encoding="utf-8")
        return
    if target.config_format == "yaml_mcp_servers":
        target.config_path.write_text("theme: fixture\n", encoding="utf-8")
        return
    if target.config_format == "json_mcp_nested_servers":
        payload = {
            "mcp": {
                "servers": {
                    "existing": {
                        "command": "existing-server",
                        "args": [],
                    }
                }
            }
        }
    elif target.config_format == "json_opencode":
        payload = {"theme": "fixture", "mcp": {}}
    else:
        payload = {"theme": "fixture"}
    target.config_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_config_probe(target: McpTarget, fixture_root: Path) -> dict[str, Any]:
    """Run config writer plus doctor against one temporary target fixture."""
    started = time.perf_counter()
    fixture = _fixture_target(target, fixture_root)
    try:
        _seed_fixture_config(fixture)
        result = connect_mcp_target(fixture)
        doctor = doctor_mcp_target(fixture)
        passed = (
            result.installed
            and doctor.get("configured") is True
            and doctor.get("config_exists") is True
            and not doctor.get("parse_error")
        )
        return {
            "probe": CONFIG_PROBE,
            "target": target.name,
            "display_name": target.display_name,
            "config_format": target.config_format,
            "fixture_config_path": str(fixture.config_path),
            "status": "pass" if passed else "fail",
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "result": result.to_dict(),
            "doctor": doctor,
            "backup_created": bool(result.backup_path),
            "acceptance_scope": "temporary_config_fixture",
            "counts_as_installed_agent_acceptance": False,
        }
    except Exception as exc:
        return {
            "probe": CONFIG_PROBE,
            "target": target.name,
            "display_name": target.display_name,
            "config_format": target.config_format,
            "fixture_config_path": str(fixture.config_path),
            "status": "fail",
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "error_type": type(exc).__name__,
            "message": str(exc),
            "acceptance_scope": "temporary_config_fixture",
            "counts_as_installed_agent_acceptance": False,
        }


def run_real_doctor_probe(target: McpTarget) -> dict[str, Any]:
    """Run read-only doctor against one real target config path."""
    started = time.perf_counter()
    try:
        doctor = doctor_mcp_target(target)
        if doctor.get("parse_error"):
            status = "fail"
        elif doctor.get("detected"):
            status = "pass" if doctor.get("configured") else "skip"
        else:
            status = "skip"
        return {
            "probe": REAL_DOCTOR_PROBE,
            "target": target.name,
            "display_name": target.display_name,
            "config_format": target.config_format,
            "real_config_path": str(target.config_path),
            "status": status,
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "doctor": doctor,
            "acceptance_scope": "real_config_read_only_observation",
            "counts_as_installed_agent_acceptance": False,
        }
    except Exception as exc:
        return {
            "probe": REAL_DOCTOR_PROBE,
            "target": target.name,
            "display_name": target.display_name,
            "config_format": target.config_format,
            "real_config_path": str(target.config_path),
            "status": "fail",
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "error_type": type(exc).__name__,
            "message": str(exc),
            "acceptance_scope": "real_config_read_only_observation",
            "counts_as_installed_agent_acceptance": False,
        }


async def _list_mcp_tools_via_stdio(timeout_seconds: float) -> list[str]:
    """Start the real stdio MCP server and list its tools."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "lerim.mcp_server"],
        env=env,
    )

    async def _run() -> list[str]:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return sorted(tool.name for tool in result.tools)

    return await asyncio.wait_for(_run(), timeout=timeout_seconds)


async def _call_context_brief_via_stdio(
    timeout_seconds: float, *, project: str, env_override: dict[str, str] | None = None
) -> dict[str, Any]:
    """Start the real stdio MCP server and call lerim_context_brief."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    env.setdefault("PYTHONUNBUFFERED", "1")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "lerim.mcp_server"],
        env=env,
    )

    async def _run() -> dict[str, Any]:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "lerim_context_brief",
                    {
                        "project": project,
                        "refresh": False,
                        "max_chars": 1000,
                    },
                )
                structured = result.structuredContent
                if not isinstance(structured, dict):
                    structured = {}
                return {
                    "is_error": bool(result.isError),
                    "structured": structured,
                    "content_item_count": len(result.content or []),
                }

    return await asyncio.wait_for(_run(), timeout=timeout_seconds)


async def _call_trace_submit_via_stdio(
    *,
    timeout_seconds: float,
    env: dict[str, str],
    trace_text: str,
    session_id: str,
    source_name: str = "mcp-benchmark",
    source_profile: str = "coding",
    scope_type: str = "domain",
    scope: str = "mcp-benchmark",
    scope_label: str = "MCP Benchmark",
    filename_hint: str = "duplicate-session.json",
    force: bool = False,
) -> dict[str, Any]:
    """Start the real stdio MCP server and call lerim_trace_submit."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "lerim.mcp_server"],
        env=env,
    )

    async def _run() -> dict[str, Any]:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "lerim_trace_submit",
                    {
                        "trace_text": trace_text,
                        "source_name": source_name,
                        "source_profile": source_profile,
                        "scope_type": scope_type,
                        "scope": scope,
                        "scope_label": scope_label,
                        "session_id": session_id,
                        "filename_hint": filename_hint,
                        "force": force,
                    },
                )
                structured = result.structuredContent
                if not isinstance(structured, dict):
                    structured = {}
                return {
                    "is_error": bool(result.isError),
                    "structured": structured,
                    "content_item_count": len(result.content or []),
                }

    return await asyncio.wait_for(_run(), timeout=timeout_seconds)


def run_stdio_tools_probe(timeout_seconds: float) -> dict[str, Any]:
    """Run a real stdio MCP tools/list probe."""
    started = time.perf_counter()
    command = f"{sys.executable} -m lerim.mcp_server"
    try:
        tools = asyncio.run(_list_mcp_tools_via_stdio(timeout_seconds))
        missing = sorted(set(EXPECTED_MCP_TOOLS) - set(tools))
        return {
            "probe": STDIO_TOOLS_PROBE,
            "target": "lerim-mcp-stdio",
            "display_name": "Lerim MCP stdio server",
            "status": "pass" if not missing else "fail",
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "command": command,
            "expected_tools": list(EXPECTED_MCP_TOOLS),
            "tools": tools,
            "missing_tools": missing,
            "acceptance_scope": "local_stdio_server_tools_list_probe",
            "counts_as_installed_agent_acceptance": False,
        }
    except Exception as exc:
        return {
            "probe": STDIO_TOOLS_PROBE,
            "target": "lerim-mcp-stdio",
            "display_name": "Lerim MCP stdio server",
            "status": "fail",
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "command": command,
            "expected_tools": list(EXPECTED_MCP_TOOLS),
            "tools": [],
            "missing_tools": list(EXPECTED_MCP_TOOLS),
            "error_type": type(exc).__name__,
            "message": str(exc),
            "acceptance_scope": "local_stdio_server_tools_list_probe",
            "counts_as_installed_agent_acceptance": False,
        }


def run_stdio_context_tool_probe(
    timeout_seconds: float, *, context_project: str | None = None
) -> dict[str, Any]:
    """Run a real stdio MCP context tool-call probe."""
    started = time.perf_counter()
    command = f"{sys.executable} -m lerim.mcp_server"
    project_path = Path(context_project).expanduser().resolve() if context_project else Path.cwd()
    project = str(project_path)
    try:
        with tempfile.TemporaryDirectory(prefix="lerim-mcp-context-") as raw_root:
            root = Path(raw_root)
            config_path = root / "config.toml"
            data_dir = root / ".lerim"
            data_dir.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                "[data]\n"
                f'dir = "{data_dir}"\n'
                "\n"
                "[server]\n"
                "port = 8766\n"
                "\n"
                "[projects]\n"
                f'mcp-benchmark = "{project_path}"\n',
                encoding="utf-8",
            )
            result = asyncio.run(
                _call_context_brief_via_stdio(
                    timeout_seconds,
                    project=project,
                    env_override={"LERIM_CONFIG": str(config_path)},
                )
            )
        structured = result.get("structured") if isinstance(result, dict) else {}
        if not isinstance(structured, dict):
            structured = {}
        passed = (
            result.get("is_error") is False
            and structured.get("error") is False
            and bool(structured.get("project_id"))
            and bool(structured.get("repo_path"))
        )
        return {
            "probe": STDIO_CONTEXT_TOOL_PROBE,
            "target": "lerim-mcp-stdio",
            "display_name": "Lerim MCP stdio server",
            "status": "pass" if passed else "fail",
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "command": command,
            "tool": "lerim_context_brief",
            "arguments": {
                "project": project,
                "refresh": False,
                "max_chars": 1000,
            },
            "is_error": result.get("is_error"),
            "project": structured.get("project"),
            "project_id": structured.get("project_id"),
            "repo_path": structured.get("repo_path"),
            "availability": (structured.get("status") or {}).get("availability")
            if isinstance(structured.get("status"), dict)
            else None,
            "truncated": structured.get("truncated"),
            "content_chars": len(str(structured.get("content") or "")),
            "content_item_count": result.get("content_item_count"),
            "acceptance_scope": "local_stdio_server_context_tool_call",
            "counts_as_installed_agent_acceptance": False,
            "counts_as_local_context_tool_call_acceptance": passed,
        }
    except Exception as exc:
        return {
            "probe": STDIO_CONTEXT_TOOL_PROBE,
            "target": "lerim-mcp-stdio",
            "display_name": "Lerim MCP stdio server",
            "status": "fail",
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "command": command,
            "tool": "lerim_context_brief",
            "arguments": {
                "project": project,
                "refresh": False,
                "max_chars": 1000,
            },
            "error_type": type(exc).__name__,
            "message": str(exc),
            "acceptance_scope": "local_stdio_server_context_tool_call",
            "counts_as_installed_agent_acceptance": False,
            "counts_as_local_context_tool_call_acceptance": False,
        }


def run_stdio_trace_submit_duplicate_probe(timeout_seconds: float) -> dict[str, Any]:
    """Run a real stdio MCP trace-submit idempotency check without extraction."""
    from lerim.context import ContextStore, resolve_scope_identity
    from lerim.traces.envelope import load_generic_trace, write_compact_trace

    started = time.perf_counter()
    command = f"{sys.executable} -m lerim.mcp_server"
    session_id = "mcp-trace-submit-duplicate"
    trace_text = json.dumps(
        {
            "session_id": session_id,
            "messages": [
                {
                    "role": "user",
                    "content": "We chose a source-session context compiler architecture.",
                },
                {
                    "role": "assistant",
                    "content": "Persist only durable decisions with evidence.",
                },
            ],
        },
        sort_keys=True,
    )
    try:
        with tempfile.TemporaryDirectory(prefix="lerim-mcp-trace-submit-") as raw_root:
            root = Path(raw_root)
            config_path = root / "config.toml"
            data_dir = root / ".lerim"
            data_dir.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                "[data]\n"
                f'dir = "{data_dir}"\n'
                "\n"
                "[server]\n"
                "port = 8765\n",
                encoding="utf-8",
            )
            seed_trace_path = root / "seed-trace.json"
            seed_trace_path.write_text(trace_text + "\n", encoding="utf-8")
            normalized = load_generic_trace(seed_trace_path)
            scope = resolve_scope_identity(
                scope_type="domain",
                scope="mcp-benchmark",
                scope_label="MCP Benchmark",
            )
            normalized_path = (
                data_dir
                / "workspace"
                / "imports"
                / scope.scope_type
                / scope.scope_id
                / f"{normalized.trace_id}.jsonl"
            )
            write_compact_trace(normalized, normalized_path)
            store = ContextStore(data_dir / "context.sqlite3")
            store.upsert_session(
                project_id=None,
                session_id=session_id,
                agent_type="mcp-benchmark",
                source_trace_ref=str(normalized_path),
                repo_path=None,
                cwd=None,
                started_at=normalized.started_at,
                model_name=None,
                instructions_text=None,
                prompt_text=None,
                scope_identity=scope,
                source_name="mcp-benchmark",
                source_profile="coding",
            )
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["LERIM_CONFIG"] = str(config_path)
            result = asyncio.run(
                _call_trace_submit_via_stdio(
                    timeout_seconds=timeout_seconds,
                    env=env,
                    trace_text=trace_text,
                    session_id=session_id,
                )
            )
        structured = result.get("structured") if isinstance(result, dict) else {}
        if not isinstance(structured, dict):
            structured = {}
        passed = (
            result.get("is_error") is False
            and structured.get("error") is False
            and structured.get("status") == "duplicate_skipped"
            and structured.get("session_id") == session_id
            and bool(structured.get("submitted_trace_path"))
            and bool(structured.get("normalized_trace_path"))
        )
        return {
            "probe": STDIO_TRACE_SUBMIT_PROBE,
            "target": "lerim-mcp-stdio",
            "display_name": "Lerim MCP stdio server",
            "status": "pass" if passed else "fail",
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "command": command,
            "tool": "lerim_trace_submit",
            "result_status": structured.get("status"),
            "session_id": structured.get("session_id"),
            "scope_type": structured.get("scope_type"),
            "scope_id_present": bool(structured.get("scope_id")),
            "submitted_trace_path_present": bool(structured.get("submitted_trace_path")),
            "normalized_trace_path_present": bool(structured.get("normalized_trace_path")),
            "acceptance_scope": "local_stdio_server_trace_submit_duplicate_path",
            "counts_as_installed_agent_acceptance": False,
            "counts_as_trace_submit_idempotency_acceptance": passed,
            "counts_as_trace_submit_extraction_acceptance": False,
        }
    except Exception as exc:
        return {
            "probe": STDIO_TRACE_SUBMIT_PROBE,
            "target": "lerim-mcp-stdio",
            "display_name": "Lerim MCP stdio server",
            "status": "fail",
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "command": command,
            "tool": "lerim_trace_submit",
            "error_type": type(exc).__name__,
            "message": str(exc),
            "acceptance_scope": "local_stdio_server_trace_submit_duplicate_path",
            "counts_as_installed_agent_acceptance": False,
            "counts_as_trace_submit_idempotency_acceptance": False,
            "counts_as_trace_submit_extraction_acceptance": False,
        }


def run_stdio_trace_submit_extraction_probe(timeout_seconds: float) -> dict[str, Any]:
    """Run a real stdio MCP trace-submit check that executes extraction."""
    from lerim.context import ContextStore

    started = time.perf_counter()
    command = f"{sys.executable} -m lerim.mcp_server"
    session_id = f"mcp-trace-submit-extraction-{_timestamp_for_path().lower()}"
    scope = "mcp-benchmark-extraction"
    trace_text = json.dumps(
        {
            "session_id": session_id,
            "metadata": {
                "source": "mcp-benchmark",
                "source_profile": "support",
                "scope": scope,
            },
            "messages": [
                {
                    "role": "user",
                    "timestamp": "2026-05-19T09:00:00Z",
                    "content": (
                        "Support operations update: make this a standing rule for "
                        "future billing-refund agents. Before escalating or promising "
                        "a refund, verify entitlement in Stripe and inspect the latest "
                        "invoice. If either check is missing, ask for the missing "
                        "evidence instead of promising an outcome."
                    ),
                },
                {
                    "role": "assistant",
                    "timestamp": "2026-05-19T09:01:00Z",
                    "content": (
                        "Understood. I will treat the Stripe entitlement check and the "
                        "latest-invoice inspection as required prerequisites before "
                        "refund escalation or customer-facing promises."
                    ),
                },
                {
                    "role": "user",
                    "timestamp": "2026-05-19T09:02:00Z",
                    "content": (
                        "The reason is that missing either check caused prior wrong "
                        "refund promises. Keep the rule domain-scoped for support "
                        "operations, not tied to this single ticket."
                    ),
                },
            ],
        },
        sort_keys=True,
    )
    try:
        with tempfile.TemporaryDirectory(prefix="lerim-mcp-trace-extract-") as raw_root:
            root = Path(raw_root)
            config_path = root / "config.toml"
            data_dir = root / ".lerim"
            data_dir.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                "[data]\n"
                f'dir = "{data_dir}"\n'
                "\n"
                "[server]\n"
                "port = 8766\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["LERIM_CONFIG"] = str(config_path)
            result = asyncio.run(
                _call_trace_submit_via_stdio(
                    timeout_seconds=timeout_seconds,
                    env=env,
                    trace_text=trace_text,
                    session_id=session_id,
                    source_name="mcp-benchmark",
                    source_profile="support",
                    scope_type="domain",
                    scope=scope,
                    scope_label="MCP Benchmark Extraction",
                    filename_hint="extraction-session.json",
                    force=True,
                )
            )
            store = ContextStore(data_dir / "context.sqlite3")
            records = store.query(
                entity="records",
                mode="list",
                source_session_id=session_id,
                include_archived=True,
                order_by="created_at",
                limit=20,
            )["rows"]
        structured = result.get("structured") if isinstance(result, dict) else {}
        if not isinstance(structured, dict):
            structured = {}
        durable_record_count = sum(
            1 for row in records if str(row.get("kind") or "") != "episode"
        )
        episode_record_count = sum(
            1 for row in records if str(row.get("kind") or "") == "episode"
        )
        records_created = int(structured.get("records_created") or 0)
        records_updated = int(structured.get("records_updated") or 0)
        records_archived = int(structured.get("records_archived") or 0)
        passed = (
            result.get("is_error") is False
            and structured.get("error") is False
            and structured.get("session_id") == session_id
            and episode_record_count == 1
            and durable_record_count >= 1
            and records_created >= 2
        )
        return {
            "probe": STDIO_TRACE_SUBMIT_EXTRACTION_PROBE,
            "target": "lerim-mcp-stdio",
            "display_name": "Lerim MCP stdio server",
            "status": "pass" if passed else "fail",
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "command": command,
            "tool": "lerim_trace_submit",
            "result_status": structured.get("status") or "ingested",
            "session_id": structured.get("session_id"),
            "scope_type": structured.get("scope_type"),
            "scope_id_present": bool(structured.get("scope_id")),
            "submitted_trace_path_present": bool(structured.get("submitted_trace_path")),
            "normalized_trace_path_present": bool(structured.get("normalized_trace_path")),
            "records_created": records_created,
            "records_updated": records_updated,
            "records_archived": records_archived,
            "record_count": len(records),
            "episode_record_count": episode_record_count,
            "durable_record_count": durable_record_count,
            "input_trace_kind": "synthetic_protocol_acceptance_trace",
            "input_trace_disclosure": (
                "Synthetic submitted trace fixture; MCP protocol submission and "
                "DSPy extraction path are real."
            ),
            "acceptance_scope": "local_stdio_server_trace_submit_extraction_path",
            "counts_as_installed_agent_acceptance": False,
            "counts_as_trace_submit_idempotency_acceptance": False,
            "counts_as_trace_submit_extraction_acceptance": passed,
        }
    except Exception as exc:
        return {
            "probe": STDIO_TRACE_SUBMIT_EXTRACTION_PROBE,
            "target": "lerim-mcp-stdio",
            "display_name": "Lerim MCP stdio server",
            "status": "fail",
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "command": command,
            "tool": "lerim_trace_submit",
            "error_type": type(exc).__name__,
            "message": str(exc),
            "input_trace_kind": "synthetic_protocol_acceptance_trace",
            "acceptance_scope": "local_stdio_server_trace_submit_extraction_path",
            "counts_as_installed_agent_acceptance": False,
            "counts_as_trace_submit_idempotency_acceptance": False,
            "counts_as_trace_submit_extraction_acceptance": False,
        }


def run_installed_client_probe(
    spec: dict[str, Any], *, timeout_seconds: float
) -> dict[str, Any]:
    """Run one installed-client MCP management command."""
    started = time.perf_counter()
    command = [str(part) for part in spec["command"]]
    binary = command[0]
    if shutil.which(binary) is None:
        return {
            "probe": INSTALLED_CLIENT_PROBE,
            "target": spec["target"],
            "display_name": spec["display_name"],
            "status": "skip",
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "command": command,
            "message": f"client binary not found: {binary}",
            "acceptance_scope": "real_installed_client_mcp_cli",
            "counts_as_installed_agent_acceptance": False,
            "counts_as_installed_client_connection_acceptance": False,
        }
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "probe": INSTALLED_CLIENT_PROBE,
            "target": spec["target"],
            "display_name": spec["display_name"],
            "status": "fail",
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "command": command,
            "message": f"client command timed out after {timeout_seconds:.1f}s",
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "acceptance_scope": "real_installed_client_mcp_cli",
            "counts_as_installed_agent_acceptance": False,
            "counts_as_installed_client_connection_acceptance": False,
        }
    output = f"{completed.stdout}\n{completed.stderr}"
    output_lower = output.lower()
    missing_markers = [
        marker
        for marker in spec.get("required_markers", ())
        if str(marker).lower() not in output_lower
    ]
    connected = any(
        str(marker).lower() in output_lower
        for marker in spec.get("connected_markers", ())
    )
    status = "pass" if completed.returncode == 0 and not missing_markers else "fail"
    return {
        "probe": INSTALLED_CLIENT_PROBE,
        "target": spec["target"],
        "display_name": spec["display_name"],
        "status": status,
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "missing_markers": missing_markers,
        "connected": connected,
        "acceptance_scope": (
            "real_installed_client_mcp_connection"
            if connected
            else "real_installed_client_mcp_config_listing"
        ),
        "counts_as_installed_agent_acceptance": False,
        "counts_as_installed_client_connection_acceptance": status == "pass"
        and connected,
    }


def run_tool_call_probe(
    spec: dict[str, Any],
    *,
    allow_live_client_tool_calls: bool,
    timeout_seconds: float,
    max_budget_usd: float,
) -> dict[str, Any]:
    """Run one opt-in installed-client tool-call probe."""
    started = time.perf_counter()
    command = _render_tool_call_command(spec, max_budget_usd=max_budget_usd)
    binary = command[0]
    base = {
        "probe": TOOL_CALL_PROBE,
        "target": spec["target"],
        "display_name": spec["display_name"],
        "command": command,
        "expected_tool": spec["expected_tool"],
        "acceptance_scope": "real_installed_client_tool_call",
    }
    if not allow_live_client_tool_calls:
        return {
            **base,
            "status": "skip",
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "message": "live client tool-call probes require explicit opt-in",
            "observed_tools": [],
            "counts_as_installed_agent_acceptance": False,
            "counts_as_context_tool_call_acceptance": False,
        }
    if shutil.which(binary) is None:
        return {
            **base,
            "status": "skip",
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "message": f"client binary not found: {binary}",
            "observed_tools": [],
            "counts_as_installed_agent_acceptance": False,
            "counts_as_context_tool_call_acceptance": False,
        }
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            **base,
            "status": "fail",
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "message": f"client tool-call command timed out after {timeout_seconds:.1f}s",
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "observed_tools": [],
            "counts_as_installed_agent_acceptance": False,
            "counts_as_context_tool_call_acceptance": False,
        }
    observed_tools = sorted(
        _extract_tool_names_from_text(f"{completed.stdout}\n{completed.stderr}")
    )
    blocker = _extract_structured_client_blocker(
        f"{completed.stdout}\n{completed.stderr}"
    )
    expected_tool = str(spec["expected_tool"])
    passed = (
        completed.returncode == 0
        and any(_tool_name_matches(observed, expected_tool) for observed in observed_tools)
    )
    if passed:
        status = "pass"
    elif blocker:
        status = "blocked"
    else:
        status = "fail"
    return {
        **base,
        "status": status,
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "observed_tools": observed_tools,
        "blocker": blocker,
        "counts_as_installed_agent_acceptance": passed,
        "counts_as_context_tool_call_acceptance": passed,
    }


def _render_tool_call_command(
    spec: dict[str, Any], *, max_budget_usd: float
) -> list[str]:
    """Render one client tool-call command from a probe spec."""
    return [
        str(part).format(max_budget_usd=f"{max_budget_usd:.4f}")
        for part in spec["command"]
    ]


def parse_target_filter(value: str | None) -> set[str] | None:
    """Parse a comma-separated target filter for optional live probes."""
    if value is None:
        return None
    targets = {item.strip() for item in str(value).split(",") if item.strip()}
    return targets or None


def _filter_probe_specs(
    specs: tuple[dict[str, Any], ...],
    target_filter: set[str] | None,
    *,
    label: str,
) -> list[dict[str, Any]]:
    """Return probe specs selected by target name with validation."""
    if target_filter is None:
        return list(specs)
    known = {str(spec["target"]) for spec in specs}
    unknown = sorted(target_filter - known)
    if unknown:
        raise SystemExit(
            f"Unknown {label} target(s): {', '.join(unknown)}. "
            f"Known targets: {', '.join(sorted(known))}"
        )
    return [spec for spec in specs if str(spec["target"]) in target_filter]


def _extract_tool_names_from_text(text: str) -> set[str]:
    """Extract MCP tool names from JSONL/JSON client output."""
    observed: set[str] = set()
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        _collect_tool_names(payload, observed)
    return observed


def _extract_structured_client_blocker(text: str) -> dict[str, Any] | None:
    """Extract structured external-client blockers from JSONL/JSON output."""
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        blocker = _structured_client_blocker(payload)
        if blocker:
            return blocker
    return None


def _structured_client_blocker(payload: Any) -> dict[str, Any] | None:
    """Return a normalized blocker when client output contains API errors."""
    if isinstance(payload, list):
        for item in payload:
            blocker = _structured_client_blocker(item)
            if blocker:
                return blocker
        return None
    if not isinstance(payload, dict):
        return None

    status_code = payload.get("api_error_status") or payload.get("error_status")
    if status_code in {401, 402, 403, 429}:
        return {
            "reason": "external_client_api_unavailable",
            "client_error_name": str(
                payload.get("error") or payload.get("subtype") or "client_api_error"
            ),
            "status_code": status_code,
        }

    error_payload = payload.get("error")
    if isinstance(error_payload, dict):
        blocker = _blocker_from_error_payload(error_payload)
        if blocker:
            return blocker

    response_body = payload.get("responseBody")
    if isinstance(response_body, str):
        blocker = _blocker_from_response_body(response_body)
        if blocker:
            return blocker

    for value in payload.values():
        blocker = _structured_client_blocker(value)
        if blocker:
            return blocker
    return None


def _blocker_from_error_payload(error_payload: dict[str, Any]) -> dict[str, Any] | None:
    """Classify structured client API errors as benchmark blockers."""
    data = error_payload.get("data")
    if not isinstance(data, dict):
        data = {}
    status_code = data.get("statusCode") or error_payload.get("statusCode")
    error_name = str(error_payload.get("name") or data.get("name") or "")
    nested = _blocker_from_response_body(data.get("responseBody"))
    if nested:
        return {
            **nested,
            "client_error_name": error_name or nested.get("client_error_name"),
            "status_code": status_code or nested.get("status_code"),
        }
    if error_name and status_code in {401, 402, 403, 429}:
        return {
            "reason": "external_client_api_unavailable",
            "client_error_name": error_name,
            "status_code": status_code,
        }
    return None


def _blocker_from_response_body(response_body: Any) -> dict[str, Any] | None:
    """Classify nested structured response bodies without using text matching."""
    if not isinstance(response_body, str) or not response_body.strip():
        return None
    try:
        payload = json.loads(response_body)
    except json.JSONDecodeError:
        return None
    error_payload = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error_payload, dict):
        return None
    error_type = str(error_payload.get("type") or "")
    if error_type:
        return {
            "reason": "external_client_error",
            "client_error_type": error_type,
        }
    return None


def _collect_tool_names(payload: Any, observed: set[str]) -> None:
    """Collect tool names from recognized structured tool-use events."""
    if isinstance(payload, dict):
        if _looks_like_tool_event(payload):
            for key in ("name", "tool", "tool_name", "toolName"):
                value = payload.get(key)
                if isinstance(value, str) and any(
                    _tool_name_matches(value, expected) for expected in EXPECTED_MCP_TOOLS
                ):
                    observed.add(value)
        for value in payload.values():
            _collect_tool_names(value, observed)
        return
    if isinstance(payload, list):
        for item in payload:
            _collect_tool_names(item, observed)


def _looks_like_tool_event(payload: dict[str, Any]) -> bool:
    """Return whether a structured payload is a real tool-call event."""
    event_type = str(
        payload.get("type")
        or payload.get("event")
        or payload.get("role")
        or payload.get("kind")
        or ""
    ).strip().lower()
    if event_type in {
        "function_call",
        "mcp_tool_call",
        "tool",
        "tool-call",
        "tool_call",
        "tool_use",
    }:
        return True
    return any(
        key in payload
        for key in (
            "toolCallId",
            "toolUseId",
            "tool_call_id",
            "tool_use_id",
        )
    )


def _tool_name_matches(observed: str, expected: str) -> bool:
    """Return whether an observed client tool name refers to an expected MCP tool."""
    raw = str(observed or "").strip()
    target = str(expected or "").strip()
    return raw == target or raw.endswith(f"__{target}") or raw.endswith(f".{target}")


def build_report(
    details: list[dict[str, Any]],
    *,
    known_target_names: list[str],
    generated_at: str | None = None,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the source-of-truth JSON report from raw detail rows."""
    generated = generated_at or _utc_now()
    details = [
        _sanitize_public_payload(row)
        for row in details
        if isinstance(row, dict)
    ]
    config_rows = [row for row in details if row.get("probe") == CONFIG_PROBE]
    stdio_rows = [row for row in details if row.get("probe") == STDIO_TOOLS_PROBE]
    stdio_context_rows = [
        row for row in details if row.get("probe") == STDIO_CONTEXT_TOOL_PROBE
    ]
    stdio_trace_rows = [
        row for row in details if row.get("probe") in STDIO_TRACE_SUBMIT_PROBES
    ]
    real_rows = [row for row in details if row.get("probe") == REAL_DOCTOR_PROBE]
    installed_rows = [
        row for row in details if row.get("probe") == INSTALLED_CLIENT_PROBE
    ]
    tool_call_rows = [row for row in details if row.get("probe") == TOOL_CALL_PROBE]
    config_targets = sorted({str(row.get("target")) for row in config_rows})
    failures = [row for row in details if row.get("status") == "fail"]
    status_counts = Counter(str(row.get("status")) for row in details)
    config_status_counts = Counter(str(row.get("status")) for row in config_rows)
    stdio_status_counts = Counter(str(row.get("status")) for row in stdio_rows)
    stdio_context_status_counts = Counter(
        str(row.get("status")) for row in stdio_context_rows
    )
    stdio_trace_status_counts = Counter(str(row.get("status")) for row in stdio_trace_rows)
    real_status_counts = Counter(str(row.get("status")) for row in real_rows)
    installed_status_counts = Counter(str(row.get("status")) for row in installed_rows)
    tool_call_status_counts = Counter(str(row.get("status")) for row in tool_call_rows)
    blocked_count = status_counts.get("blocked", 0)

    config_failed_count = config_status_counts.get("fail", 0)
    stdio_failed_count = stdio_status_counts.get("fail", 0)
    stdio_context_failed_count = stdio_context_status_counts.get("fail", 0)
    stdio_trace_failed_count = stdio_trace_status_counts.get("fail", 0)
    installed_failed_count = installed_status_counts.get("fail", 0)
    tool_call_failed_count = tool_call_status_counts.get("fail", 0)
    config_complete = sorted(config_targets) == sorted(known_target_names)
    stdio_tools_accepted = stdio_status_counts.get("pass", 0) > 0
    local_context_accepted = any(
        row.get("counts_as_local_context_tool_call_acceptance") is True
        for row in stdio_context_rows
    )
    trace_submit_idempotency_accepted = any(
        row.get("counts_as_trace_submit_idempotency_acceptance") is True
        for row in stdio_trace_rows
    )
    full_integration = (
        config_complete
        and stdio_tools_accepted
        and local_context_accepted
        and trace_submit_idempotency_accepted
    )
    if (
        config_failed_count
        or stdio_failed_count
        or stdio_context_failed_count
        or stdio_trace_failed_count
        or installed_failed_count
        or tool_call_failed_count
    ):
        overall_status = "fail"
    elif not full_integration or blocked_count:
        overall_status = "partial"
    else:
        overall_status = "pass"

    return {
        "benchmark": "lerim_mcp_integration",
        "generated_at": generated,
        "command": " ".join(sys.argv),
        "mode": "local-integration",
        "artifact_scope": "full_mcp_integration" if full_integration else "partial_mcp_integration",
        "is_full_integration_run": full_integration,
        "overall_status": overall_status,
        "environment": _sanitize_public_payload(environment or _environment_metadata()),
        "summary": {
            "known_target_count": len(known_target_names),
            "known_targets": sorted(known_target_names),
            "config_targets_checked": config_targets,
            "all_known_targets_checked": config_complete,
            "detail_count": len(details),
            "status_counts": dict(sorted(status_counts.items())),
            "config_probe_count": len(config_rows),
            "config_passed_count": config_status_counts.get("pass", 0),
            "config_failed_count": config_failed_count,
            "stdio_tools_probe_count": len(stdio_rows),
            "stdio_passed_count": stdio_status_counts.get("pass", 0),
            "stdio_failed_count": stdio_failed_count,
            "stdio_context_tool_probe_count": len(stdio_context_rows),
            "stdio_context_tool_status_counts": dict(
                sorted(stdio_context_status_counts.items())
            ),
            "stdio_context_tool_passed_count": stdio_context_status_counts.get(
                "pass", 0
            ),
            "stdio_context_tool_failed_count": stdio_context_failed_count,
            "local_context_tool_call_acceptance_count": sum(
                1
                for row in stdio_context_rows
                if row.get("counts_as_local_context_tool_call_acceptance") is True
            ),
            "stdio_trace_submit_probe_count": len(stdio_trace_rows),
            "stdio_trace_submit_status_counts": dict(
                sorted(stdio_trace_status_counts.items())
            ),
            "stdio_trace_submit_passed_count": stdio_trace_status_counts.get("pass", 0),
            "stdio_trace_submit_failed_count": stdio_trace_failed_count,
            "trace_submit_idempotency_acceptance_count": sum(
                1
                for row in stdio_trace_rows
                if row.get("counts_as_trace_submit_idempotency_acceptance") is True
            ),
            "trace_submit_extraction_acceptance_count": sum(
                1
                for row in stdio_trace_rows
                if row.get("counts_as_trace_submit_extraction_acceptance") is True
            ),
            "real_doctor_probe_count": len(real_rows),
            "real_doctor_status_counts": dict(sorted(real_status_counts.items())),
            "installed_client_probe_count": len(installed_rows),
            "installed_client_status_counts": dict(
                sorted(installed_status_counts.items())
            ),
            "installed_client_connection_acceptance_count": sum(
                1
                for row in installed_rows
                if row.get("counts_as_installed_client_connection_acceptance") is True
            ),
            "tool_call_probe_count": len(tool_call_rows),
            "tool_call_status_counts": dict(sorted(tool_call_status_counts.items())),
            "context_tool_call_acceptance_count": sum(
                1
                for row in tool_call_rows
                if row.get("counts_as_context_tool_call_acceptance") is True
            ),
            "installed_client_tool_call_acceptance_count": sum(
                1
                for row in tool_call_rows
                if row.get("counts_as_context_tool_call_acceptance") is True
            ),
            "installed_agent_acceptance_count": sum(
                1 for row in details if row.get("counts_as_installed_agent_acceptance") is True
            ),
            "failure_count": len(failures),
            "blocker_count": blocked_count,
        },
        "required_artifacts": [
            "report.json",
            "report.md",
            "details.jsonl",
        ],
        "limitations": [
            "Temporary config fixtures exercise Lerim writer and validation code paths but do not prove an agent is installed or can launch Lerim.",
            "The stdio tools-list probe starts Lerim's MCP server directly and lists tools; it does not prove every external MCP client can launch the command.",
            "The stdio context tool-call probe calls lerim_context_brief through the MCP protocol and proves Lerim's local tool path; it does not prove an external client selected the tool.",
            "The default stdio trace-submit probe calls lerim_trace_submit through the MCP protocol on an idempotent duplicate trace; it proves submission and normalization plumbing but not LLM extraction quality.",
            "The opt-in stdio trace-submit extraction probe calls the same MCP tool on a synthetic submitted trace and requires DSPy extraction to create one episode record plus at least one durable record.",
            "The opt-in stdio trace-submit extraction probe uses a synthetic submitted trace fixture; the MCP submission and DSPy extraction path are real, but this is not organic client-session evidence.",
            "Installed-client MCP CLI probes prove client config/connection visibility only; they do not prove context tool-call behavior unless a client actually calls lerim_context_brief.",
            "Public artifacts preserve aggregate installed-client counts and statuses but omit per-machine installed-client inventory from detail rows.",
            "Live client tool-call probes may spend model/subscription credits and are skipped unless explicitly enabled.",
            "Installed-agent context tool-call acceptance still needs an installed-client invocation of lerim_context_brief.",
        ],
        "failures": [
            {
                "probe": row.get("probe"),
                "target": row.get("target"),
                "status": row.get("status"),
                "message": row.get("message") or row.get("error_type") or "",
            }
            for row in failures
        ],
        "blockers": [
            {
                "probe": row.get("probe"),
                "target": row.get("target"),
                "status": row.get("status"),
                "blocker": row.get("blocker"),
            }
            for row in details
            if row.get("status") == "blocked"
        ],
        "details": [_public_detail_row(row) for row in details],
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Render a generated Markdown summary from one report payload."""
    summary = report["summary"]
    tool_call_status_counts = summary.get("tool_call_status_counts") or {}
    skipped_tool_call_count = 0
    if isinstance(tool_call_status_counts, dict):
        skipped_tool_call_count = int(tool_call_status_counts.get("skip") or 0)
    tool_call_probe_count = int(summary.get("tool_call_probe_count") or 0)
    installed_tool_call_summary = (
        "not run in this artifact"
        if tool_call_probe_count > 0 and skipped_tool_call_count == tool_call_probe_count
        else str(summary["context_tool_call_acceptance_count"])
    )
    lines = [
        "# Lerim MCP Integration Benchmark",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Command: `{report.get('command', '')}`",
        f"- Mode: `{report['mode']}`",
        f"- Overall status: `{report['overall_status']}`",
        f"- Known MCP targets: `{summary['known_target_count']}`",
        f"- Config targets checked: `{len(summary['config_targets_checked'])}`",
        f"- Config probe pass/fail: `{summary['config_passed_count']}` / `{summary['config_failed_count']}`",
        f"- Stdio tools-list pass/fail: `{summary['stdio_passed_count']}` / `{summary['stdio_failed_count']}`",
        f"- Stdio context tool-call pass/fail: `{summary['stdio_context_tool_passed_count']}` / `{summary['stdio_context_tool_failed_count']}`",
        f"- Local context tool-call acceptances: `{summary['local_context_tool_call_acceptance_count']}`",
        f"- Stdio trace-submit pass/fail: `{summary['stdio_trace_submit_passed_count']}` / `{summary['stdio_trace_submit_failed_count']}`",
        f"- Trace-submit idempotency acceptances: `{summary['trace_submit_idempotency_acceptance_count']}`",
        f"- Trace-submit extraction acceptances: `{summary['trace_submit_extraction_acceptance_count']}`",
        f"- Real config validation probes: `{summary['real_doctor_probe_count']}`",
        f"- Real config validation statuses: `{summary['real_doctor_status_counts']}`",
        f"- Installed client probes: `{summary['installed_client_probe_count']}`",
        f"- Installed client statuses: `{summary['installed_client_status_counts']}`",
        f"- Installed client connection acceptances: `{summary['installed_client_connection_acceptance_count']}`",
        f"- Tool-call probes: `{summary['tool_call_probe_count']}`",
        f"- Tool-call statuses: `{summary['tool_call_status_counts']}`",
        f"- Installed-client context tool-call validation: `{installed_tool_call_summary}`",
        f"- Installed-client context tool-call acceptances: `{summary.get('installed_client_tool_call_acceptance_count', summary['context_tool_call_acceptance_count'])}`",
        f"- Blockers: `{summary['blocker_count']}`",
        "",
        "## Acceptance Boundary",
        "",
    ]
    for limitation in report["limitations"]:
        lines.append(f"- {limitation}")
    lines.extend(["", "## Target Config Probes", ""])
    lines.append("| Target | Status | Format | Backup | Configured |")
    lines.append("| --- | --- | --- | --- | --- |")
    for row in report["details"]:
        if row.get("probe") != CONFIG_PROBE:
            continue
        doctor = row.get("doctor") or {}
        lines.append(
            "| {target} | `{status}` | `{fmt}` | `{backup}` | `{configured}` |".format(
                target=row.get("target", ""),
                status=row.get("status", ""),
                fmt=row.get("config_format", ""),
                backup="yes" if row.get("backup_created") else "no",
                configured=doctor.get("configured", ""),
            )
        )
    lines.extend(["", "## MCP Stdio Tools Probe", ""])
    for row in report["details"]:
        if row.get("probe") != STDIO_TOOLS_PROBE:
            continue
        tools = ", ".join(row.get("tools") or [])
        missing = ", ".join(row.get("missing_tools") or [])
        lines.extend(
            [
                f"- Status: `{row.get('status')}`",
                f"- Command: `{row.get('command')}`",
                f"- Tools: `{tools}`",
                f"- Missing tools: `{missing or 'none'}`",
                "",
            ]
        )
    lines.extend(["## MCP Stdio Context Tool Call", ""])
    for row in report["details"]:
        if row.get("probe") != STDIO_CONTEXT_TOOL_PROBE:
            continue
        lines.extend(
            [
                f"- Status: `{row.get('status')}`",
                f"- Command: `{row.get('command')}`",
                f"- Tool: `{row.get('tool')}`",
                "- Project: `<configured benchmark project>`",
                f"- Availability: `{row.get('availability')}`",
                f"- Content chars returned: `{row.get('content_chars')}`",
                "",
            ]
        )
    lines.extend(["## MCP Stdio Trace Submit", ""])
    for row in report["details"]:
        if row.get("probe") not in STDIO_TRACE_SUBMIT_PROBES:
            continue
        lines.extend(
            [
                f"- Status: `{row.get('status')}`",
                f"- Probe: `{row.get('probe')}`",
                f"- Command: `{row.get('command')}`",
                f"- Tool: `{row.get('tool')}`",
                f"- Result status: `{row.get('result_status')}`",
                f"- Session id: `{row.get('session_id')}`",
                f"- Scope type: `{row.get('scope_type')}`",
                f"- Records created: `{row.get('records_created')}`",
                f"- Durable records: `{row.get('durable_record_count')}`",
                f"- Extraction acceptance: `{row.get('counts_as_trace_submit_extraction_acceptance')}`",
                f"- Input trace: `{row.get('input_trace_kind') or 'not declared'}`",
                "",
            ]
        )
    real_rows = [row for row in report["details"] if row.get("probe") == REAL_DOCTOR_PROBE]
    if real_rows:
        lines.extend(
            [
                "## Installed Config Doctor Probe Summary",
                "",
                f"- Probe count: `{summary['real_doctor_probe_count']}`",
                f"- Status counts: `{summary['real_doctor_status_counts']}`",
                "- Per-client local inventory is omitted from the public Markdown report.",
                "",
            ]
        )
    installed_rows = [
        row for row in report["details"] if row.get("probe") == INSTALLED_CLIENT_PROBE
    ]
    if installed_rows:
        lines.extend(
            [
                "## Installed Client MCP CLI Probe Summary",
                "",
                f"- Probe count: `{summary['installed_client_probe_count']}`",
                f"- Status counts: `{summary['installed_client_status_counts']}`",
                f"- Connection acceptances: `{summary['installed_client_connection_acceptance_count']}`",
                "- Per-client local inventory is omitted from the public Markdown report.",
                "",
            ]
        )
    tool_call_rows = [
        row for row in report["details"] if row.get("probe") == TOOL_CALL_PROBE
    ]
    if tool_call_rows:
        observed_tools = sorted(
            {
                str(tool)
                for row in tool_call_rows
                for tool in (row.get("observed_tools") or [])
                if tool
            }
        )
        lines.extend(
            [
                "## Installed Client Tool-Call Probe Summary",
                "",
                f"- Probe count: `{summary['tool_call_probe_count']}`",
                f"- Status counts: `{summary['tool_call_status_counts']}`",
                f"- Context tool-call acceptances: `{summary.get('installed_client_tool_call_acceptance_count', summary['context_tool_call_acceptance_count'])}`",
                f"- Observed Lerim tools: `{', '.join(observed_tools) or 'none'}`",
                "- Per-client local inventory is omitted from the public Markdown report.",
                "",
            ]
        )
    if report.get("failures"):
        lines.extend(["## Failures", ""])
        for failure in report["failures"]:
            message = str(failure.get("message") or failure.get("error_type") or "no message")
            lines.append(
                f"- `{failure.get('probe')}` / `{failure.get('target')}`: {message}"
            )
        lines.append("")
    if report.get("blockers"):
        lines.extend(["## Blockers", ""])
        for blocker in report["blockers"]:
            lines.append(
                f"- `{blocker.get('probe')}` / `{blocker.get('target')}`: `{blocker.get('blocker')}`"
            )
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def write_outputs(
    output_dir: Path,
    report: dict[str, Any],
    details: list[dict[str, Any]],
) -> None:
    """Write report.json, report.md, and details.jsonl artifacts."""
    output_dir.mkdir(parents=True, exist_ok=True)
    public_details = report.get("details")
    if not isinstance(public_details, list):
        public_details = details
    public_details = [_public_detail_row(row) for row in public_details]
    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    details_text = "".join(
        json.dumps(row, sort_keys=True, default=str) + "\n" for row in public_details
    )
    (output_dir / "details.jsonl").write_text(details_text, encoding="utf-8")


def run_benchmark(
    output_dir: Path | None,
    *,
    include_stdio_tools_probe: bool,
    include_stdio_context_tool_call: bool,
    include_stdio_trace_submit: bool,
    include_stdio_trace_submit_extraction: bool,
    include_real_doctor: bool,
    include_installed_client_probes: bool,
    include_tool_call_probes: bool,
    allow_live_client_tool_calls: bool,
    installed_client_targets: set[str] | None,
    tool_call_targets: set[str] | None,
    stdio_timeout_seconds: float,
    stdio_extraction_timeout_seconds: float,
    context_project: str | None,
    installed_client_timeout_seconds: float,
    tool_call_timeout_seconds: float,
    max_tool_call_budget_usd: float,
) -> Path:
    """Run the local MCP integration benchmark and return the output directory."""
    if output_dir is None:
        report_dir = Path(tempfile.mkdtemp(prefix="lerim-evidence-integration-"))
    else:
        report_dir = output_dir.expanduser().resolve()
        report_dir.mkdir(parents=True, exist_ok=True)

    targets = list(known_mcp_targets())
    details: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="lerim-mcp-config-fixtures-") as raw_fixture_dir:
        fixture_root = Path(raw_fixture_dir)
        for target in targets:
            details.append(run_config_probe(target, fixture_root))

    if include_stdio_tools_probe:
        details.append(run_stdio_tools_probe(stdio_timeout_seconds))
    if include_stdio_context_tool_call:
        details.append(
            run_stdio_context_tool_probe(
                stdio_timeout_seconds,
                context_project=context_project,
            )
        )
    if include_stdio_trace_submit:
        details.append(run_stdio_trace_submit_duplicate_probe(stdio_timeout_seconds))
    if include_stdio_trace_submit_extraction:
        details.append(
            run_stdio_trace_submit_extraction_probe(stdio_extraction_timeout_seconds)
        )
    if include_real_doctor:
        for target in targets:
            details.append(run_real_doctor_probe(target))
    if include_installed_client_probes:
        for spec in _filter_probe_specs(
            INSTALLED_CLIENT_PROBES,
            installed_client_targets,
            label="installed-client probe",
        ):
            details.append(
                run_installed_client_probe(
                    spec,
                    timeout_seconds=installed_client_timeout_seconds,
                )
            )
    if include_tool_call_probes:
        for spec in _filter_probe_specs(
            TOOL_CALL_PROBES,
            tool_call_targets,
            label="tool-call probe",
        ):
            details.append(
                run_tool_call_probe(
                    spec,
                    allow_live_client_tool_calls=allow_live_client_tool_calls,
                    timeout_seconds=tool_call_timeout_seconds,
                    max_budget_usd=max_tool_call_budget_usd,
                )
            )

    report = build_report(
        details,
        known_target_names=[target.name for target in targets],
        environment=_environment_metadata(),
    )
    write_outputs(report_dir, report, details)
    return report_dir


def parse_args() -> argparse.Namespace:
    """Parse integration benchmark CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Run Lerim's local MCP integration benchmark scaffold.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Directory for report.json, report.md, and details.jsonl. "
            "Defaults to a temporary directory."
        ),
    )
    parser.add_argument(
        "--skip-stdio-tools-list",
        action="store_true",
        help="Skip the real stdio MCP tools-list probe.",
    )
    parser.add_argument(
        "--skip-stdio-context-tool-call",
        action="store_true",
        help="Skip the real stdio MCP lerim_context_brief call check.",
    )
    parser.add_argument(
        "--skip-stdio-trace-submit",
        action="store_true",
        help="Skip the real stdio MCP lerim_trace_submit duplicate-path check.",
    )
    parser.add_argument(
        "--include-stdio-trace-submit-extraction",
        action="store_true",
        help=(
            "Also submit a synthetic trace through lerim_trace_submit and run "
            "real DSPy extraction. This may spend LLM credits."
        ),
    )
    parser.add_argument(
        "--include-real-doctor",
        action="store_true",
        help="Also run read-only doctor checks against configured target paths.",
    )
    parser.add_argument(
        "--include-installed-client-probes",
        action="store_true",
        help="Also run installed-client MCP list/get commands where safe.",
    )
    parser.add_argument(
        "--installed-client-targets",
        default=None,
        help=(
            "Comma-separated installed-client probe targets to run. "
            "Defaults to every known installed-client probe."
        ),
    )
    parser.add_argument(
        "--include-tool-call-probes",
        action="store_true",
        help="Add installed-client context tool-call probes to the report.",
    )
    parser.add_argument(
        "--tool-call-targets",
        default=None,
        help=(
            "Comma-separated tool-call probe targets to run. "
            "Defaults to every known tool-call probe."
        ),
    )
    parser.add_argument(
        "--allow-live-client-tool-calls",
        action="store_true",
        help=(
            "Actually run live installed-client tool-call prompts. "
            "This may spend model/subscription credits."
        ),
    )
    parser.add_argument(
        "--stdio-timeout-seconds",
        type=float,
        default=10.0,
        help="Timeout for stdio MCP probes.",
    )
    parser.add_argument(
        "--stdio-extraction-timeout-seconds",
        type=float,
        default=240.0,
        help="Timeout for the opt-in stdio MCP trace-submit extraction probe.",
    )
    parser.add_argument(
        "--context-project",
        default=None,
        help=(
            "Project slug/path passed to the stdio lerim_context_brief probe. "
            "Defaults to the current working directory."
        ),
    )
    parser.add_argument(
        "--installed-client-timeout-seconds",
        type=float,
        default=20.0,
        help="Timeout for each installed client MCP CLI probe.",
    )
    parser.add_argument(
        "--tool-call-timeout-seconds",
        type=float,
        default=120.0,
        help="Timeout for each live installed client tool-call probe.",
    )
    parser.add_argument(
        "--max-tool-call-budget-usd",
        type=float,
        default=0.25,
        help="Maximum per-client budget passed to clients that support it.",
    )
    return parser.parse_args()


def main() -> int:
    """Run the integration benchmark CLI."""
    args = parse_args()
    if args.stdio_timeout_seconds <= 0:
        raise SystemExit("--stdio-timeout-seconds must be > 0")
    if args.stdio_extraction_timeout_seconds <= 0:
        raise SystemExit("--stdio-extraction-timeout-seconds must be > 0")
    if args.installed_client_timeout_seconds <= 0:
        raise SystemExit("--installed-client-timeout-seconds must be > 0")
    if args.tool_call_timeout_seconds <= 0:
        raise SystemExit("--tool-call-timeout-seconds must be > 0")
    if args.max_tool_call_budget_usd < 0:
        raise SystemExit("--max-tool-call-budget-usd must be >= 0")
    output_dir = run_benchmark(
        args.output_dir,
        include_stdio_tools_probe=not args.skip_stdio_tools_list,
        include_stdio_context_tool_call=not args.skip_stdio_context_tool_call,
        include_stdio_trace_submit=not args.skip_stdio_trace_submit,
        include_stdio_trace_submit_extraction=args.include_stdio_trace_submit_extraction,
        include_real_doctor=args.include_real_doctor,
        include_installed_client_probes=args.include_installed_client_probes,
        include_tool_call_probes=args.include_tool_call_probes,
        allow_live_client_tool_calls=args.allow_live_client_tool_calls,
        installed_client_targets=parse_target_filter(args.installed_client_targets),
        tool_call_targets=parse_target_filter(args.tool_call_targets),
        stdio_timeout_seconds=args.stdio_timeout_seconds,
        stdio_extraction_timeout_seconds=args.stdio_extraction_timeout_seconds,
        context_project=args.context_project,
        installed_client_timeout_seconds=args.installed_client_timeout_seconds,
        tool_call_timeout_seconds=args.tool_call_timeout_seconds,
        max_tool_call_budget_usd=args.max_tool_call_budget_usd,
    )
    print(f"Lerim MCP integration benchmark report written to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
