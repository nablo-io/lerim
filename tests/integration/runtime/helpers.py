"""Shared helpers for behavior-driven runtime orchestration integration tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

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


def write_ingest_trace(repo_root: Path, *, name: str = "runtime-trace.jsonl") -> Path:
    """Create a tiny ingest trace fixture inside the temp repo root."""
    trace_path = repo_root / name
    trace_path.write_text(
        "\n".join(
            [
                json.dumps({"role": "user", "content": "Investigate the runtime issue and keep only durable context."}),
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


def build_ordered_answer_messages() -> list[dict[str, object]]:
    """Build an ordered context-answerer event trace."""
    return [
        {
            "kind": "baml_call",
            "function": "PlanContextRetrieval",
            "action_count": 2,
            "rationale": "Inspect recent context and then search the relevant record.",
        },
        {
            "kind": "retrieval",
            "index": 1,
            "action_type": "list",
            "result_count": 1,
            "rationale": "Check the most recent context.",
        },
        {
            "kind": "retrieval",
            "index": 2,
            "action_type": "search",
            "result_count": 1,
            "rationale": "Find the matching context record.",
        },
        {
            "kind": "baml_call",
            "function": "AnswerFromContext",
            "supporting_record_ids": ["rec_1"],
        },
    ]
