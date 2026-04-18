"""Real end-to-end QA for sync, maintain, and ask over the repaired DB-only runtime."""

from __future__ import annotations

from pathlib import Path

import pytest

from lerim.context.store import ContextStore
from tests.conftest import TRACES_DIR
from tests.live_helpers import (
    EXTRACT_TOOL_NAMES,
    FRAMEWORK_TOOL_NAMES,
    MAINTAIN_TOOL_NAMES,
    assert_clean_context_schema,
    assert_no_legacy_tools,
    assert_quality_metrics,
    audit_context_db,
    read_agent_trace_tool_names,
)


@pytest.mark.e2e
@pytest.mark.llm
def test_live_runtime_cycle_sync_then_maintain_then_ask(
    live_config,
    live_repo_root,
    live_runtime,
) -> None:
    """The full runtime flow should produce queryable, high-quality records."""
    sync_payload = live_runtime.sync(
        TRACES_DIR / "mixed_decisions_learnings.jsonl",
        session_id="e2e-runtime-cycle",
        agent_type="e2e",
    )
    maintain_payload = live_runtime.maintain(
        repo_root=live_repo_root,
        session_id="e2e-maintain-cycle",
    )

    sync_tools = read_agent_trace_tool_names(Path(sync_payload["run_folder"]) / "agent_trace.json")
    maintain_tools = read_agent_trace_tool_names(Path(maintain_payload["run_folder"]) / "agent_trace.json")
    assert "trace_read" in sync_tools
    assert "create_record" in sync_tools
    assert set(sync_tools).issubset(EXTRACT_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    assert set(maintain_tools).issubset(MAINTAIN_TOOL_NAMES | FRAMEWORK_TOOL_NAMES)
    assert_no_legacy_tools(sync_tools)
    assert_no_legacy_tools(maintain_tools)

    store = ContextStore(live_config.context_db_path)
    rows = store.query(
        entity="records",
        mode="list",
        project_ids=[sync_payload["project_id"]],
        order_by="created_at",
        limit=50,
        include_total=True,
    )["rows"]
    durable_rows = [row for row in rows if row["kind"] != "episode"]
    assert len(rows) >= 2
    assert durable_rows

    metrics = audit_context_db(live_config.context_db_path)
    assert_clean_context_schema(live_config.context_db_path)
    assert_quality_metrics(metrics)

    count_answer, _, _, _ = live_runtime.ask(
        "how many records are extracted",
        repo_root=live_repo_root,
    )
    assert str(metrics["record_count"]) in count_answer

    latest_learning = durable_rows[0]
    latest_title = str(latest_learning["title"]).strip()
    latest_answer, _, _, _ = live_runtime.ask(
        "what is the last memory",
        repo_root=live_repo_root,
    )
    assert latest_title in latest_answer

    semantic_answer, _, _, _ = live_runtime.ask(
        "What decision was made about logging format?",
        repo_root=live_repo_root,
    )
    semantic_lower = semantic_answer.lower()
    assert "json" in semantic_lower
    assert "logging" in semantic_lower or "structured" in semantic_lower
