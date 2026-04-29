"""Shared helpers for case-based integration suites."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any

import httpx
import yaml
from pydantic_ai.exceptions import ModelHTTPError

from lerim.context import ContextStore


def load_yaml_expectation(directory: Path, case_name: str) -> dict[str, Any]:
    """Load one YAML expectation file from the given case directory."""
    path = directory / f"{case_name}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def seed_session(
    store: ContextStore,
    *,
    project_id: str,
    session_id: str,
    repo_root: Path,
    agent_type: str,
    source_trace_ref: str,
    model_name: str = "integration-test",
) -> None:
    """Insert one provenance row before seeded writes or agent mutations."""
    store.upsert_session(
        project_id=project_id,
        session_id=session_id,
        agent_type=agent_type,
        source_trace_ref=source_trace_ref,
        repo_path=str(repo_root),
        cwd=str(repo_root),
        started_at=datetime.now(timezone.utc).isoformat(),
        model_name=model_name,
        instructions_text=None,
        prompt_text=None,
        metadata={},
    )


def extract_tool_calls(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract tool-call payloads from serialized message history."""
    calls: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("part_kind") == "tool-call":
                raw_args = value.get("args")
                parsed_args = raw_args
                if isinstance(raw_args, str):
                    try:
                        parsed_args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        parsed_args = raw_args
                calls.append(
                    {
                        "tool_name": str(value.get("tool_name") or "").strip(),
                        "args": parsed_args,
                        "tool_call_id": value.get("tool_call_id"),
                    }
                )
            for item in value.values():
                walk(item)
            return
        if isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return calls


def extract_tool_returns(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract tool-return payloads from serialized message history."""
    returns: list[dict[str, Any]] = []

    def parse_content(raw_content: Any) -> Any:
        if not isinstance(raw_content, str):
            return raw_content
        try:
            return json.loads(raw_content)
        except json.JSONDecodeError:
            return raw_content

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("part_kind") == "tool-return":
                raw_content = value.get("content")
                returns.append(
                    {
                        "tool_name": str(value.get("tool_name") or "").strip(),
                        "content": raw_content,
                        "parsed_content": parse_content(raw_content),
                        "tool_call_id": value.get("tool_call_id"),
                        "is_error": bool(value.get("is_error")),
                    }
                )
            for item in value.values():
                walk(item)
            return
        if isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return returns


def retry_on_overload(callable_fn, *, attempts: int = 5, backoff_seconds: float = 5.0):
    """Retry one live integration call on transient provider overload or transport errors."""
    for attempt in range(attempts):
        try:
            return callable_fn()
        except ModelHTTPError as exc:
            if int(getattr(exc, "status_code", 0)) != 529 or attempt == attempts - 1:
                raise
            time.sleep(backoff_seconds * (2**attempt))
        except (httpx.ReadError, httpx.TimeoutException):
            if attempt == attempts - 1:
                raise
            time.sleep(backoff_seconds * (2**attempt))
