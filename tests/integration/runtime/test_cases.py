"""Behavior-driven runtime orchestration integration cases."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from lerim.agents.ask import AskResult
from lerim.agents.extract import ExtractionEvent, ExtractionResult, ExtractionRunDetails
from lerim.agents.maintain import MaintainEvent, MaintainRunDetails
from lerim.context import ContextStore
from lerim.working_memory import (
    MemoryLine,
    MemorySection,
    WorkingMemoryDraft,
    working_memory_paths,
)
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


def _extract_details(kwargs, *, summary: str) -> ExtractionRunDetails:
    """Build graph-style extraction details for sync runtime test doubles."""
    return ExtractionRunDetails(
        events=[
            ExtractionEvent(
                action="final_result",
                ok=True,
                content=summary,
                args={},
                done=True,
                completion_summary=summary,
            )
        ],
        llm_calls=1,
        done=True,
        context_db_path=str(kwargs["context_db_path"]),
        project_id=kwargs["project_identity"].project_id,
        session_id=kwargs["session_id"],
        model_name="test-model",
        trace_total_lines=1,
    )


def _maintain_details(kwargs, *, summary: str) -> MaintainRunDetails:
    """Build graph-style maintain details for runtime test doubles."""
    return MaintainRunDetails(
        events=[
            MaintainEvent(
                action="final_result",
                ok=True,
                content=summary,
                args={},
                done=True,
                completion_summary=summary,
            )
        ],
        llm_calls=1,
        done=True,
        context_db_path=str(kwargs["context_db_path"]),
        project_id=kwargs["project_identity"].project_id,
        session_id=kwargs["session_id"],
        model_name="test-model",
    )


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
            _extract_details(kwargs, summary="sync complete"),
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
            _maintain_details(kwargs, summary="maintain complete"),
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
            _extract_details(kwargs, summary="sync recovered"),
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
            _extract_details(kwargs, summary="sync wrote initial fact"),
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
            _maintain_details(kwargs, summary="maintain strengthened fact"),
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


def test_working_memory_refresh_writes_dated_and_current_artifacts(
    monkeypatch, live_config, live_repo_root
):
    """Working Memory generation should write dated artifacts plus stable current copies."""
    expectation = load_runtime_expectation(
        "working_memory_refresh_writes_dated_and_current_artifacts"
    )["expected"]
    ctx = build_runtime_case_context(
        monkeypatch=monkeypatch,
        live_config=live_config,
        live_repo_root=live_repo_root,
    )
    seed_session_id = "runtime-working-memory-seed"
    seed_runtime_session(
        ctx.store,
        project_id=ctx.project_id,
        session_id=seed_session_id,
        repo_root=live_repo_root,
        source_trace_ref="working-memory-seed",
    )
    decision = ctx.store.create_record(
        project_id=ctx.project_id,
        session_id=seed_session_id,
        kind="decision",
        title="Use generated Working Memory at startup",
        body="Use generated Working Memory at startup so agents get fast context.",
        decision="Use generated Working Memory at startup.",
        why="Startup must stay fast and avoid live synthesis.",
        change_reason="working_memory_seed_decision",
    )
    constraint = ctx.store.create_record(
        project_id=ctx.project_id,
        session_id=seed_session_id,
        kind="constraint",
        title="Markdown is not canonical memory",
        body="Markdown Working Memory is a derived view; SQLite remains canonical.",
        change_reason="working_memory_seed_constraint",
    )
    monkeypatch.setattr(
        "lerim.server.runtime.build_pydantic_model",
        lambda *args, **kwargs: "fake-model",
    )
    monkeypatch.setattr(
        "lerim.server.runtime.run_working_memory_synthesis",
        lambda **kwargs: (
            WorkingMemoryDraft(
                summary=(
                    MemoryLine(
                        "Use generated Working Memory at startup.",
                        (str(decision["record_id"]),),
                    ),
                ),
                sections=(
                    MemorySection(
                        "Startup Context",
                        (
                            MemoryLine(
                                "Keep Markdown derived from SQLite.",
                                (str(constraint["record_id"]),),
                            ),
                        ),
                    ),
                ),
            ),
            build_ordered_ask_messages()[:1],
        ),
    )

    result = ctx.runtime.working_memory(
        repo_root=live_repo_root,
        project_name="runtime-project",
        force=True,
    )

    run_folder = Path(str(result["run_folder"]))
    paths = working_memory_paths(live_config, ctx.project_id)
    _assert_run_folder_layout(
        run_folder,
        live_config.global_data_dir / "workspace",
        str(expectation["workspace_subdir"]),
    )
    assert result["status"] == "generated"
    assert result["records_considered"] == int(expectation["records_considered"])
    assert result["records_included"] == int(expectation["records_included"])
    assert paths.current_file.is_file()
    assert paths.current_manifest.is_file()
    assert (run_folder / "WORKING_MEMORY.md").is_file()
    assert (run_folder / "agent.log").read_text(encoding="utf-8").strip()
    manifest = json.loads((run_folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["operation"] == "working-memory"
    assert manifest["status"] == "succeeded"
    assert manifest["current_file"] == str(paths.current_file)
    assert sorted(manifest["included_record_ids"]) == sorted(result["included_record_ids"])
    current_markdown = paths.current_file.read_text(encoding="utf-8")
    for record_id in result["included_record_ids"]:
        assert record_id in current_markdown
    assert "derived view, not the source of truth" in current_markdown


def test_working_memory_refresh_skips_when_records_unchanged(
    monkeypatch, live_config, live_repo_root
):
    """A second unchanged refresh should skip before creating a run folder or model call."""
    expectation = load_runtime_expectation(
        "working_memory_refresh_skips_when_records_unchanged"
    )["expected"]
    ctx = build_runtime_case_context(
        monkeypatch=monkeypatch,
        live_config=live_config,
        live_repo_root=live_repo_root,
    )
    seed_session_id = "runtime-working-memory-skip-seed"
    seed_runtime_session(
        ctx.store,
        project_id=ctx.project_id,
        session_id=seed_session_id,
        repo_root=live_repo_root,
        source_trace_ref="working-memory-skip-seed",
    )
    decision = ctx.store.create_record(
        project_id=ctx.project_id,
        session_id=seed_session_id,
        kind="decision",
        title="Skip unchanged Working Memory refresh",
        body="Skip Working Memory refresh when no records changed.",
        decision="Skip unchanged Working Memory refresh.",
        why="Avoid unnecessary model cost.",
        change_reason="working_memory_skip_seed",
    )
    calls = {"synthesis": 0}
    monkeypatch.setattr(
        "lerim.server.runtime.build_pydantic_model",
        lambda *args, **kwargs: "fake-model",
    )

    def _fake_synthesis(**kwargs):
        calls["synthesis"] += 1
        if calls["synthesis"] > 1:
            raise AssertionError("unchanged refresh should not synthesize")
        return (
            WorkingMemoryDraft(
                summary=(
                    MemoryLine(
                        "Skip unchanged Working Memory refresh.",
                        (str(decision["record_id"]),),
                    ),
                ),
                sections=(),
            ),
            build_ordered_ask_messages()[:1],
        )

    monkeypatch.setattr(
        "lerim.server.runtime.run_working_memory_synthesis",
        _fake_synthesis,
    )
    first = ctx.runtime.working_memory(
        repo_root=live_repo_root,
        project_name="runtime-project",
        force=True,
    )
    operation_dir = Path(str(first["run_folder"])).parent
    before = sorted(path.name for path in operation_dir.iterdir())

    second = ctx.runtime.working_memory(
        repo_root=live_repo_root,
        project_name="runtime-project",
        force=False,
    )
    after = sorted(path.name for path in operation_dir.iterdir())

    assert calls["synthesis"] == 1
    assert second["status"] == "skipped"
    assert second["run_folder"] is None
    assert second["skip_reason"] == expectation["skip_reason"]
    assert second["records_changed_since_previous"] == 0
    assert before == after


def test_working_memory_refresh_writes_empty_state_without_model_call(
    monkeypatch, live_config, live_repo_root
):
    """Projects with no active records should get an empty-state artifact without synthesis."""
    expectation = load_runtime_expectation(
        "working_memory_refresh_writes_empty_state_without_model_call"
    )["expected"]
    ctx = build_runtime_case_context(
        monkeypatch=monkeypatch,
        live_config=live_config,
        live_repo_root=live_repo_root,
    )
    monkeypatch.setattr(
        "lerim.server.runtime.build_pydantic_model",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("empty-state refresh should not build a model")
        ),
    )
    monkeypatch.setattr(
        "lerim.server.runtime.run_working_memory_synthesis",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("empty-state refresh should not synthesize")
        ),
    )

    result = ctx.runtime.working_memory(
        repo_root=live_repo_root,
        project_name="runtime-project",
        force=True,
    )

    run_folder = Path(str(result["run_folder"]))
    paths = working_memory_paths(live_config, ctx.project_id)
    _assert_run_folder_layout(
        run_folder,
        live_config.global_data_dir / "workspace",
        str(expectation["workspace_subdir"]),
    )
    assert result["status"] == "generated"
    assert result["records_considered"] == 0
    assert result["records_included"] == 0
    text = paths.current_file.read_text(encoding="utf-8")
    for token in expectation["empty_state_must_include"]:
        assert token in text
    manifest = json.loads(paths.current_manifest.read_text(encoding="utf-8"))
    assert manifest["records_considered"] == 0
    assert manifest["included_record_ids"] == []
