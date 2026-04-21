"""Shared helpers for case-based integration suites."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import yaml

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
