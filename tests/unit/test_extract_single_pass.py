"""Unit tests for the simplified single-pass extract agent."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import fields
from pathlib import Path

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from lerim.agents import extract as extract_module
from lerim.agents.extract import (
    ExtractionResult,
    SYSTEM_PROMPT,
    build_extract_agent,
    run_extraction,
)
from lerim.agents.tools import (
    CONTEXT_HARD_PRESSURE_PCT,
    CONTEXT_SOFT_PRESSURE_PCT,
    ContextDeps,
    Finding,
    _require_note_or_prune_before_trace_read,
    compute_request_budget,
    create_record,
    fetch_records,
    note,
    prune,
    search_records,
    trace_read,
    update_record,
)
from lerim.context.project_identity import resolve_project_identity


def test_extraction_result_schema() -> None:
    """ExtractionResult exposes only the completion summary."""
    assert set(ExtractionResult.model_fields.keys()) == {"completion_summary"}


def test_context_deps_fields(tmp_path) -> None:
    """ContextDeps carries DB-era identifiers and mutable run state."""
    field_names = {field.name for field in fields(ContextDeps)}
    assert field_names == {
        "context_db_path",
        "project_identity",
        "session_id",
        "project_ids",
        "trace_path",
        "run_folder",
        "trace_total_lines",
        "read_ranges",
        "notes",
        "pruned_offsets",
        "last_context_tokens",
        "last_context_fill_ratio",
    }
    deps = ContextDeps(
        context_db_path=tmp_path / "context.sqlite3",
        project_identity=resolve_project_identity(tmp_path),
        session_id="sess_test",
    )
    assert deps.trace_total_lines == 0
    assert deps.read_ranges == []
    assert deps.notes == []
    assert deps.pruned_offsets == set()
    assert deps.last_context_tokens == 0
    assert deps.last_context_fill_ratio == 0.0


def test_build_extract_agent_wires_simplified_tool_surface(tmp_path) -> None:
    """The extract agent should expose the simplified extract tools."""
    agent = build_extract_agent(TestModel())
    assert agent.output_type is ExtractionResult
    deps = ContextDeps(
        context_db_path=tmp_path / "context.sqlite3",
        project_identity=resolve_project_identity(tmp_path),
        session_id="sess_test",
    )
    ctx = RunContext(deps=deps, model=TestModel(), usage=RunUsage())
    tool_names = set(asyncio.run(agent._get_toolset().get_tools(ctx)).keys())
    tool_names.discard("final_result")
    assert tool_names == {
        "trace_read",
        "search_records",
        "fetch_records",
        "create_record",
        "update_record",
        "note",
        "prune",
    }


def test_build_extract_agent_has_three_history_processors() -> None:
    """The extract agent still uses context, notes, and prune processors."""
    agent = build_extract_agent(TestModel())
    processors = getattr(agent, "history_processors", None) or getattr(agent, "_history_processors", None)
    assert processors is not None
    names = {processor.__name__ for processor in processors if hasattr(processor, "__name__")}
    assert names == {
        "context_pressure_injector",
        "notes_state_injector",
        "prune_history_processor",
    }


def test_extract_tools_take_contextdeps_runcontext() -> None:
    """Each extract tool should accept RunContext[ContextDeps] first."""
    for fn in (trace_read, search_records, fetch_records, create_record, update_record, note, prune):
        params = list(inspect.signature(fn).parameters.values())
        assert params[0].name == "ctx"
        annotation = str(params[0].annotation)
        assert "RunContext" in annotation
        assert "ContextDeps" in annotation


def test_system_prompt_mentions_simplified_flow() -> None:
    """The extract prompt should teach the episode-plus-durable-record flow."""
    assert "episode" in SYSTEM_PROMPT.lower()
    assert "durable records" in SYSTEM_PROMPT.lower()
    assert "create_record" in SYSTEM_PROMPT
    assert "update_record" in SYSTEM_PROMPT
    assert "graph links" in SYSTEM_PROMPT.lower()
    assert "if you need more than one `trace_read`" in SYSTEM_PROMPT.lower()
    assert "prune older `trace_read` results" in SYSTEM_PROMPT.lower()
    assert "end the run with the `final_result` tool" in SYSTEM_PROMPT.lower()


def test_run_extraction_signature_matches_db_inputs() -> None:
    """The extract runner takes DB path, project identity, and session ID."""
    params = inspect.signature(run_extraction).parameters
    assert "context_db_path" in params
    assert "project_identity" in params
    assert "session_id" in params
    assert "trace_path" in params
    assert "repo_root" not in params
    assert "return_messages" in params


def test_run_extraction_uses_computed_request_budget(monkeypatch, tmp_path) -> None:
    """The runner should scale request limit from trace size before calling the agent."""
    captured: dict[str, object] = {}
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text("x\n" * 500, encoding="utf-8")
    project_root = tmp_path / "repo"
    project_root.mkdir()
    project_identity = resolve_project_identity(project_root)

    class _FakeRunResult:
        def __init__(self) -> None:
            self.output = ExtractionResult(completion_summary="created records")

        def all_messages(self):
            return []

    class _FakeAgent:
        def run_sync(self, prompt, *, deps, usage_limits):
            captured["prompt"] = prompt
            captured["deps"] = deps
            captured["request_limit"] = usage_limits.request_limit
            return _FakeRunResult()

    monkeypatch.setattr(extract_module, "build_extract_agent", lambda _model: _FakeAgent())
    result = run_extraction(
        context_db_path=tmp_path / "context.sqlite3",
        project_identity=project_identity,
        session_id="sess_test",
        trace_path=trace_path,
        model=object(),
        run_folder=tmp_path / "run",
    )

    assert result.completion_summary == "created records"
    assert captured["request_limit"] == compute_request_budget(trace_path)
    assert captured["deps"].session_id == "sess_test"


def test_old_three_pass_symbols_do_not_exist() -> None:
    """Regression guard: the old three-pass extract architecture stays gone."""
    assert not hasattr(extract_module, "run_extraction_three_pass")
    assert not hasattr(extract_module, "FinalizeResult")


def test_soft_pressure_requires_note_before_more_trace_reads(tmp_path) -> None:
    """At soft pressure, another read should be blocked until findings are noted."""
    deps = ContextDeps(
        context_db_path=tmp_path / "context.sqlite3",
        project_identity=resolve_project_identity(tmp_path),
        session_id="sess_test",
        last_context_fill_ratio=CONTEXT_SOFT_PRESSURE_PCT,
        read_ranges=[(0, 100), (100, 200)],
    )
    ctx = RunContext(deps=deps, model=TestModel(), usage=RunUsage())

    with pytest.raises(ModelRetry) as exc_info:
        _require_note_or_prune_before_trace_read(ctx, 200)

    message = str(exc_info.value).lower()
    assert "soft" in message
    assert "note" in message


def test_hard_pressure_requires_prune_before_more_trace_reads(tmp_path) -> None:
    """At hard pressure, another read should be blocked until older reads are pruned."""
    deps = ContextDeps(
        context_db_path=tmp_path / "context.sqlite3",
        project_identity=resolve_project_identity(tmp_path),
        session_id="sess_test",
        last_context_fill_ratio=CONTEXT_HARD_PRESSURE_PCT,
        read_ranges=[(0, 100), (100, 200), (200, 300)],
        notes=[
            Finding(
                theme="state-boundary",
                offset=210,
                quote="authoritative state must be persisted",
                level="decision",
            )
        ],
    )
    ctx = RunContext(deps=deps, model=TestModel(), usage=RunUsage())

    with pytest.raises(ModelRetry) as exc_info:
        _require_note_or_prune_before_trace_read(ctx, 300)

    message = str(exc_info.value).lower()
    assert "hard" in message
    assert "prune" in message
