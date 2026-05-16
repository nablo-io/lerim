"""Shared helpers for case-based integration suites."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any

import httpx
import yaml

from lerim.context import ContextStore


def load_yaml_expectation(directory: Path, case_name: str) -> dict[str, Any]:
    """Load one YAML expectation file from the given case directory."""
    path = directory / f"{case_name}.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    expected = payload.get("expected") if isinstance(payload, dict) else None
    if isinstance(expected, dict):
        for key in (
            "must_use_tools",
            "must_not_use_tools",
            "must_use_events",
            "must_not_use_events",
        ):
            if expected.get(key) is None:
                expected[key] = []
    return payload


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
    """Extract retrieval/action payloads from serialized BAML event history."""
    calls: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("kind") == "retrieval":
                calls.append(
                    {
                        "tool_name": str(value.get("action_type") or "").strip(),
                        "args": dict(value),
                        "tool_call_id": value.get("index"),
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
    """Extract retrieval return summaries from serialized BAML event history."""
    returns: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("kind") == "retrieval":
                returns.append(
                    {
                        "tool_name": str(value.get("action_type") or "").strip(),
                        "content": value,
                        "parsed_content": value,
                        "tool_call_id": value.get("index"),
                        "is_error": False,
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
        except (httpx.ReadError, httpx.TimeoutException):
            if attempt == attempts - 1:
                raise
            time.sleep(backoff_seconds * (2**attempt))
        except Exception as exc:
            status_code = int(getattr(exc, "status_code", 0) or 0)
            if status_code != 529 or attempt == attempts - 1:
                raise
            time.sleep(backoff_seconds * (2**attempt))
