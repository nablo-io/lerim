"""Behavior-driven runtime orchestration integration cases."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from lerim.agents.ask import AskResult
from lerim.agents.extract import ExtractionResult
from lerim.context import ContextStore
from tests.integration.runtime.helpers import (
    build_ordered_ask_messages,
    build_runtime_case_context,
    load_runtime_expectation,
    seed_runtime_session,
    write_sync_trace,
)


def _assert_run_folder_layout(
    run_folder: Path, workspace_root: Path, operation: str
) -> None:
    """Assert date-partitioned runtime artifact layout."""
    assert run_folder.name.startswith(f"{operation}-")
    assert run_folder.parent.name == operation
    day = run_folder.parent.parent
    month = day.parent
    year = month.parent
    assert year.parent == workspace_root
    assert len(year.name) == 4 and year.name.isdigit()
    assert len(month.name) == 2 and month.name.isdigit()
    assert len(day.name) == 2 and day.name.isdigit()


def test_sync_artifact_paths_are_stable_per_flow(
    monkeypatch, live_config, live_repo_root
):
    """Sync writes artifacts only under the canonical sync workspace layout."""
    expectation = load_runtime_expectation("sync_artifact_paths_are_stable_per_flow")[
        "expected"
    ]
    ctx = build_runtime_case_context(
        monkeypatch=monkeypatch,
        live_config=live_config,
        live_repo_root=live_repo_root,
    )
    trace_path = write_sync_trace(live_repo_root, name="sync-artifacts.jsonl")
    monkeypatch.setattr(
        "lerim.server.runtime.build_pydantic_model",
        lambda *args, **kwargs: "fake-model",
    )
    monkeypatch.setattr(
        "lerim.server.runtime.run_extraction",
        lambda **kwargs: (
            ExtractionResult(completion_summary="sync complete"),
            build_ordered_ask_messages()[:1],
        ),
    )

    result = ctx.runtime.sync(trace_path=trace_path, session_id="sync-artifacts-case")

    run_folder = Path(result["run_folder"])
    workspace_root = live_config.global_data_dir / "workspace"
    _assert_run_folder_layout(
        run_folder, workspace_root, str(expectation["workspace_subdir"])
    )
    assert result["workspace_root"] == str(workspace_root)
    assert set(result["artifacts"]) == set(expectation["artifact_names"])
    assert (run_folder / "agent.log").read_text(
        encoding="utf-8"
    ).strip() == "sync complete"
    assert (run_folder / "subagents.log").read_text(encoding="utf-8") == ""
    session_log = json.loads((run_folder / "session.log").read_text(encoding="utf-8"))
    assert session_log["run_id"] == run_folder.name
    assert session_log["trace_path"] == str(trace_path.resolve())
    assert session_log["repo_name"] == live_repo_root.name
    assert (run_folder / "agent_trace.json").exists()
    manifest = json.loads((run_folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == run_folder.name
    assert manifest["mlflow_client_request_id"] == run_folder.name


def test_ask_debug_trace_preserves_ordered_tool_flow(
    monkeypatch, live_config, live_repo_root
):
    """Ask debug payload keeps message order and tool flow intact."""
    expectation = load_runtime_expectation(
        "ask_debug_trace_preserves_ordered_tool_flow"
    )["expected"]
    ctx = build_runtime_case_context(
        monkeypatch=monkeypatch,
        live_config=live_config,
        live_repo_root=live_repo_root,
    )
    monkeypatch.setattr(
        "lerim.server.runtime.build_pydantic_model",
        lambda *args, **kwargs: "fake-model",
    )
    monkeypatch.setattr(
        "lerim.server.runtime.run_ask",
        lambda **kwargs: (
            AskResult(answer="Final answer"),
            build_ordered_ask_messages(),
        ),
    )

    answer, session_id, cost, debug = ctx.runtime.ask(
        "What changed recently?",
        repo_root=live_repo_root,
        include_debug=True,
    )

    assert answer == "Final answer"
    assert session_id.startswith("lerim-")
    assert cost == 0.0
    assert debug is not None
    assert [item["tool_name"] for item in debug["tool_calls"]] == expectation[
        "tool_call_order"
    ]
    assert [item["tool_name"] for item in debug["tool_results"]] == [
        "list_context",
        "get_context",
    ]
    assert debug["message_count"] == 6
    assert [item["kind"] for item in debug["messages"]] == expectation["message_kinds"]
    assert debug["messages"][1]["parts"][0]["part_kind"] == "tool-call"
    assert debug["messages"][1]["parts"][0]["tool_name"] == "list_context"
    assert debug["messages"][2]["parts"][0]["part_kind"] == "tool-return"
    assert len(debug["messages"][2]["parts"][0]["content_preview"]) == 200
    assert debug["messages"][5]["parts"][0]["part_kind"] == "text"
    assert (
        debug["messages"][5]["parts"][0]["content"]
        == "The latest change updated the recent record."
    )


def test_maintain_change_counts_reflect_real_mutations(
    monkeypatch, live_config, live_repo_root
):
    """Maintain payload counts should match the actual store mutations from the run."""
    expectation = load_runtime_expectation(
        "maintain_change_counts_reflect_real_mutations"
    )["expected"]
    ctx = build_runtime_case_context(
        monkeypatch=monkeypatch,
        live_config=live_config,
        live_repo_root=live_repo_root,
    )
    seed_session_id = "runtime-maintain-seed"
    seed_runtime_session(
        ctx.store,
        project_id=ctx.project_id,
        session_id=seed_session_id,
        repo_root=live_repo_root,
        source_trace_ref="maintain-seed",
    )
    seed_fact = ctx.store.create_record(
        project_id=ctx.project_id,
        session_id=seed_session_id,
        kind="fact",
        title="Original retry fact",
        body="Retries happen in the worker loop.",
        change_reason="seed_fact",
    )
    seed_episode = ctx.store.create_record(
        project_id=ctx.project_id,
        session_id=seed_session_id,
        kind="episode",
        title="Routine cleanup",
        body="A short archived candidate.",
        user_intent="Routine cleanup",
        what_happened="Touched a few files.",
        outcomes="No durable context.",
        change_reason="seed_episode",
    )
    monkeypatch.setattr(
        "lerim.server.runtime.build_pydantic_model",
        lambda *args, **kwargs: "fake-model",
    )

    def _fake_run_maintain(**kwargs):
        session_id = kwargs["session_id"]
        store = ctx.store
        store.create_record(
            project_id=ctx.project_id,
            session_id=session_id,
            kind="fact",
            title="Worker retries need bounded backoff",
            body="Bound retry backoff to avoid runaway queue pressure.",
            change_reason="maintain_create",
        )
        store.update_record(
            record_id=str(seed_fact["record_id"]),
            session_id=session_id,
            project_ids=[ctx.project_id],
            changes={
                "body": "Retries in the worker loop should stay bounded and observable."
            },
            change_reason="maintain_update",
        )
        store.archive_record(
            record_id=str(seed_episode["record_id"]),
            session_id=session_id,
            project_ids=[ctx.project_id],
            reason="maintain_archive",
        )
        return (
            SimpleNamespace(completion_summary="maintain complete"),
            build_ordered_ask_messages()[:1],
        )

    monkeypatch.setattr("lerim.server.runtime.run_maintain", _fake_run_maintain)

    result = ctx.runtime.maintain(
        repo_root=live_repo_root, session_id="runtime-maintain-case"
    )

    run_folder = Path(result["run_folder"])
    workspace_root = live_config.global_data_dir / "workspace"
    _assert_run_folder_layout(
        run_folder, workspace_root, str(expectation["workspace_subdir"])
    )
    assert result["artifacts"]["agent_log"] == str(run_folder / "agent.log")
    assert result["artifacts"]["subagents_log"] == str(run_folder / "subagents.log")
    assert result["artifacts"]["manifest"] == str(run_folder / "manifest.json")
    assert result["records_created"] == int(expectation["records_created"])
    assert result["records_updated"] == int(expectation["records_updated"])
    assert result["records_archived"] == int(expectation["records_archived"])
    assert (run_folder / "agent.log").read_text(
        encoding="utf-8"
    ).strip() == "maintain complete"
    archived_episode = ctx.store.fetch_record(
        str(seed_episode["record_id"]),
        project_ids=[ctx.project_id],
        include_versions=True,
    )
    assert archived_episode is not None
    assert archived_episode["status"] == "archived"
    updated_fact = ctx.store.fetch_record(
        str(seed_fact["record_id"]), project_ids=[ctx.project_id], include_versions=True
    )
    assert updated_fact is not None
    assert (
        updated_fact["body"]
        == "Retries in the worker loop should stay bounded and observable."
    )


def test_sync_retries_transient_error_and_then_writes_artifacts(
    monkeypatch, live_config, live_repo_root
):
    """Sync should retry one transient failure and still finish with normal artifacts."""
    expectation = load_runtime_expectation(
        "sync_retries_transient_error_and_then_writes_artifacts"
    )["expected"]
    ctx = build_runtime_case_context(
        monkeypatch=monkeypatch,
        live_config=live_config,
        live_repo_root=live_repo_root,
    )
    trace_path = write_sync_trace(live_repo_root, name="sync-retry.jsonl")
    attempts = {"count": 0}
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        "lerim.server.runtime.build_pydantic_model",
        lambda *args, **kwargs: "fake-model",
    )

    def _flaky_run_extraction(**kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("temporary upstream failure")
        return (
            ExtractionResult(completion_summary="sync recovered"),
            build_ordered_ask_messages()[:1],
        )

    monkeypatch.setattr("lerim.server.runtime.run_extraction", _flaky_run_extraction)

    result = ctx.runtime.sync(trace_path=trace_path, session_id="sync-retry-case")

    run_folder = Path(result["run_folder"])
    assert attempts["count"] == int(expectation["expected_attempts"])
    _assert_run_folder_layout(
        run_folder,
        live_config.global_data_dir / "workspace",
        str(expectation["workspace_subdir"]),
    )
    assert (run_folder / "agent.log").read_text(
        encoding="utf-8"
    ).strip() == "sync recovered"
    assert (run_folder / "session.log").exists()
    assert (run_folder / "agent_trace.json").exists()


def test_runtime_sync_then_maintain_then_ask_with_real_artifacts(
    monkeypatch, live_config, live_repo_root
):
    """A sync-created record should survive maintain and be what ask later answers from."""
    expectation = load_runtime_expectation(
        "runtime_sync_then_maintain_then_ask_with_real_artifacts"
    )["expected"]
    ctx = build_runtime_case_context(
        monkeypatch=monkeypatch,
        live_config=live_config,
        live_repo_root=live_repo_root,
    )
    trace_path = write_sync_trace(live_repo_root, name="sync-chain.jsonl")
    monkeypatch.setattr(
        "lerim.server.runtime.build_pydantic_model",
        lambda *args, **kwargs: "fake-model",
    )

    def _fake_run_extraction(**kwargs):
        store = ContextStore(kwargs["context_db_path"])
        store.create_record(
            project_id=kwargs["project_identity"].project_id,
            session_id=kwargs["session_id"],
            kind="fact",
            title="Worker retries need bounded backoff",
            body="Worker retries need bounded backoff.",
            change_reason="runtime_chain_sync",
        )
        return (
            ExtractionResult(completion_summary="sync wrote initial fact"),
            build_ordered_ask_messages()[:1],
        )

    def _fake_run_maintain(**kwargs):
        store = ContextStore(kwargs["context_db_path"])
        rows = store.query(
            entity="records",
            mode="list",
            project_ids=[kwargs["project_identity"].project_id],
            kind="fact",
            order_by="updated_at",
            limit=10,
            include_total=True,
        )["rows"]
        target_id = str(rows[0]["record_id"])
        store.update_record(
            record_id=target_id,
            session_id=kwargs["session_id"],
            project_ids=[kwargs["project_identity"].project_id],
            changes={
                "title": "Worker retries need bounded backoff",
                "body": "Worker retries need bounded backoff so failures stay observable.",
            },
            change_reason="runtime_chain_maintain",
        )
        return (
            SimpleNamespace(completion_summary="maintain strengthened fact"),
            build_ordered_ask_messages()[:1],
        )

    def _fake_run_ask(**kwargs):
        store = ContextStore(kwargs["context_db_path"])
        rows = store.query(
            entity="records",
            mode="list",
            project_ids=[kwargs["project_identity"].project_id],
            kind="fact",
            order_by="updated_at",
            limit=1,
            include_total=True,
        )["rows"]
        record = store.fetch_record(
            str(rows[0]["record_id"]),
            project_ids=[kwargs["project_identity"].project_id],
        )
        assert record is not None
        answer = str(record["body"])
        return (
            AskResult(answer=answer),
            build_ordered_ask_messages(),
        )

    monkeypatch.setattr("lerim.server.runtime.run_extraction", _fake_run_extraction)
    monkeypatch.setattr("lerim.server.runtime.run_maintain", _fake_run_maintain)
    monkeypatch.setattr("lerim.server.runtime.run_ask", _fake_run_ask)

    sync_result = ctx.runtime.sync(
        trace_path=trace_path, session_id="runtime-sync-chain"
    )
    maintain_result = ctx.runtime.maintain(
        repo_root=live_repo_root, session_id="runtime-maintain-chain"
    )
    answer, _session_id, cost, debug = ctx.runtime.ask(
        "What is true now about worker retries?",
        repo_root=live_repo_root,
        include_debug=True,
    )

    sync_run_folder = Path(sync_result["run_folder"])
    maintain_run_folder = Path(maintain_result["run_folder"])
    _assert_run_folder_layout(
        sync_run_folder,
        live_config.global_data_dir / "workspace",
        str(expectation["sync_workspace_subdir"]),
    )
    _assert_run_folder_layout(
        maintain_run_folder,
        live_config.global_data_dir / "workspace",
        str(expectation["maintain_workspace_subdir"]),
    )
    assert sync_run_folder.exists()
    assert maintain_run_folder.exists()
    assert (sync_run_folder / "agent.log").read_text(
        encoding="utf-8"
    ).strip() == "sync wrote initial fact"
    assert (maintain_run_folder / "agent.log").read_text(
        encoding="utf-8"
    ).strip() == "maintain strengthened fact"
    assert cost == 0.0
    lowered = answer.lower()
    for token in expectation["answer_must_include_all"]:
        assert token in lowered
    for token in expectation["answer_must_not_include"]:
        assert token not in lowered
    assert debug is not None
    assert [item["tool_name"] for item in debug["tool_calls"]] == expectation[
        "ask_tool_call_order"
    ]
