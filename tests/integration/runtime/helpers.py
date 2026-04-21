"""Shared helpers for behavior-driven runtime orchestration integration tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from lerim.context import ContextStore, resolve_project_identity
from lerim.server.runtime import LerimRuntime
from tests.integration.common_helpers import seed_session
from tests.conftest import RUNTIME_EXPECTATIONS_DIR
from tests.integration.common_helpers import load_yaml_expectation


@dataclass
class RuntimeCaseContext:
    """Shared runtime case context with isolated config and project scope."""

    runtime: LerimRuntime
    store: ContextStore
    project_id: str
    repo_root: Path


def load_runtime_expectation(case_name: str) -> dict[str, object]:
    """Load one YAML expectation file for a runtime case."""
    return load_yaml_expectation(RUNTIME_EXPECTATIONS_DIR, case_name)


def build_runtime_case_context(*, monkeypatch, live_config, live_repo_root: Path) -> RuntimeCaseContext:
    """Build runtime + store context with provider validation disabled for tests."""
    monkeypatch.setattr(
        "lerim.config.providers.validate_provider_for_role",
        lambda *args, **kwargs: None,
    )
    runtime = LerimRuntime(default_cwd=str(live_repo_root), config=live_config)
    store = ContextStore(live_config.context_db_path)
    store.initialize()
    identity = resolve_project_identity(live_repo_root)
    store.register_project(identity)
    return RuntimeCaseContext(
        runtime=runtime,
        store=store,
        project_id=identity.project_id,
        repo_root=live_repo_root,
    )


def write_sync_trace(repo_root: Path, *, name: str = "runtime-trace.jsonl") -> Path:
    """Create a tiny sync trace fixture inside the temp repo root."""
    trace_path = repo_root / name
    trace_path.write_text(
        "\n".join(
            [
                json.dumps({"role": "user", "content": "Investigate the runtime issue and keep only durable memory."}),
                json.dumps({"role": "assistant", "content": "I will inspect, patch, and write the durable result."}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return trace_path


def seed_runtime_session(
    store: ContextStore,
    *,
    project_id: str,
    session_id: str,
    repo_root: Path,
    source_trace_ref: str,
    agent_type: str = "integration-seed",
) -> None:
    """Seed one provenance row so record writes have a valid source session."""
    seed_session(
        store,
        project_id=project_id,
        session_id=session_id,
        repo_root=repo_root,
        agent_type=agent_type,
        source_trace_ref=source_trace_ref,
    )


def build_ordered_ask_messages() -> list[ModelRequest | ModelResponse]:
    """Build an ordered ask trace with two tool turns and a final answer."""
    long_result = json.dumps({"rows": [{"record_id": "rec_1", "body": "x" * 260}]})
    return [
        ModelRequest(parts=[SystemPromptPart(content="system prompt"), UserPromptPart(content="What changed recently?")]),
        ModelResponse(parts=[ToolCallPart(tool_name="list_records", args={"limit": 5, "order_by": "updated_at"}, tool_call_id="call-1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="list_records", content=long_result, tool_call_id="call-1")]),
        ModelResponse(parts=[ToolCallPart(tool_name="fetch_records", args={"record_ids": ["rec_1"]}, tool_call_id="call-2")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="fetch_records", content=json.dumps({"records": [{"record_id": "rec_1", "title": "Recent change"}]}), tool_call_id="call-2")]),
        ModelResponse(parts=[TextPart(content="The latest change updated the recent record.")]),
    ]
