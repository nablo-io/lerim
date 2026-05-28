"""Unit tests for Lerim's MCP server adapter."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from lerim.context import ContextStore, resolve_project_identity
from lerim.context_brief import context_brief_paths
from lerim.mcp_server import (
    _run_with_stdout_guard,
    _safe_filename_part,
    _trace_suffix,
    _write_submitted_trace,
    create_mcp_server,
    run_mcp_server,
)
from lerim.traces.submissions import load_submission_manifest
from lerim.working_memory import working_memory_paths
from tests.helpers import make_config


class _FakeEmbeddingProvider:
    """Deterministic test embedding provider that avoids global model downloads."""

    model_id = "test-embedding"
    embedding_dims = 4

    def embed_query(self, query_text: str) -> list[float]:
        return self._embed(query_text)

    def embed_document(self, document_text: str) -> list[float]:
        return self._embed(document_text)

    def embed_documents(self, document_texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in document_texts]

    def _embed(self, text: str) -> list[float]:
        value = (sum(ord(char) for char in str(text)) % 997) / 997.0
        return [1.0, value, value / 2.0, 0.25]


def test_create_mcp_server_lists_expected_tools() -> None:
    """The MCP server exposes the supported first-cut tool surface."""
    server = create_mcp_server()
    names = {tool.name for tool in server._tool_manager.list_tools()}
    assert {
        "lerim_context_brief",
        "lerim_working_memory",
        "lerim_context_answer",
        "lerim_context_search",
        "lerim_records_list",
        "lerim_trace_submit",
        "lerim_ingest_status",
    }.issubset(names)


def test_direct_mcp_entrypoint_prints_help(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The raw MCP entrypoint has a discoverable help path."""
    called = False

    def _raise_if_started() -> Any:
        nonlocal called
        called = True
        raise AssertionError("MCP stdio server should not start for --help")

    monkeypatch.setattr("lerim.mcp_server.create_mcp_server", _raise_if_started)
    monkeypatch.setattr("sys.argv", ["lerim-mcp", "--help"])

    run_mcp_server()

    output = capsys.readouterr().out
    assert "usage: lerim-mcp [-h]" in output
    assert "Start Lerim's MCP stdio server" in output
    assert called is False


def test_context_search_tool_returns_seeded_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The MCP search tool reaches the real context store retrieval path."""
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    cfg = replace(
        make_config(tmp_path / ".lerim"),
        projects={"repo": str(project_dir)},
    )
    monkeypatch.setattr("lerim.mcp_server.get_config", lambda: cfg)
    monkeypatch.setattr(
        "lerim.context.store.get_embedding_provider",
        lambda: _FakeEmbeddingProvider(),
    )
    monkeypatch.setattr(
        "lerim.context.retrieval.get_config",
        lambda: cfg,
    )
    identity = resolve_project_identity(project_dir)
    store = ContextStore(cfg.context_db_path)
    store.register_project(identity)
    store.upsert_session(
        project_id=identity.project_id,
        session_id="sess-mcp-search",
        agent_type="codex",
        source_trace_ref="trace.jsonl",
        repo_path=str(project_dir),
        cwd=str(project_dir),
        started_at="2026-05-19T00:00:00Z",
        model_name="test-model",
        instructions_text=None,
        prompt_text=None,
        metadata={},
    )
    record = store.create_record(
        project_id=identity.project_id,
        session_id="sess-mcp-search",
        kind="fact",
        title="Lerim MCP search test",
        body="The integration search path returns seeded context records.",
    )
    tool = _tool_fn("lerim_context_search")

    payload = tool(query="seeded context records", scope="project", project="repo")

    assert payload["error"] is False
    assert any(row["record_id"] == record["record_id"] for row in payload["rows"])


def test_context_brief_tool_reads_current_brief(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The MCP context brief tool reads the generated current brief artifact."""
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    cfg = replace(
        make_config(tmp_path / ".lerim"),
        projects={"repo": str(project_dir)},
    )
    monkeypatch.setattr("lerim.mcp_server.get_config", lambda: cfg)
    identity = resolve_project_identity(project_dir)
    ContextStore(cfg.context_db_path).register_project(identity)
    paths = context_brief_paths(cfg, identity.project_id)
    paths.current_file.parent.mkdir(parents=True, exist_ok=True)
    paths.current_file.write_text("Use the MCP brief test context.", encoding="utf-8")
    tool = _tool_fn("lerim_context_brief")

    payload = tool(project="repo", refresh=False, max_chars=200)

    assert payload["error"] is False
    assert payload["project"] == "repo"
    assert payload["content"] == "Use the MCP brief test context."


def test_context_brief_tool_truncates_with_floor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The brief tool clamps tiny budgets and marks truncated content."""
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    cfg = replace(
        make_config(tmp_path / ".lerim"),
        projects={"repo": str(project_dir)},
    )
    monkeypatch.setattr("lerim.mcp_server.get_config", lambda: cfg)
    identity = resolve_project_identity(project_dir)
    ContextStore(cfg.context_db_path).register_project(identity)
    paths = context_brief_paths(cfg, identity.project_id)
    paths.current_file.parent.mkdir(parents=True, exist_ok=True)
    paths.current_file.write_text("x" * 1100, encoding="utf-8")
    tool = _tool_fn("lerim_context_brief")

    payload = tool(project="repo", refresh=False, max_chars=10)

    assert payload["error"] is False
    assert payload["truncated"] is True
    assert payload["content"].endswith("[truncated]")


def test_working_memory_tool_reads_current_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The MCP Working Memory tool reads the generated current artifact."""
    project_dir = tmp_path / "repo"
    project_dir.mkdir()
    cfg = replace(
        make_config(tmp_path / ".lerim"),
        projects={"repo": str(project_dir)},
    )
    monkeypatch.setattr("lerim.mcp_server.get_config", lambda: cfg)
    identity = resolve_project_identity(project_dir)
    ContextStore(cfg.context_db_path).register_project(identity)
    paths = working_memory_paths(cfg, identity.project_id)
    paths.current_file.parent.mkdir(parents=True, exist_ok=True)
    paths.current_file.write_text("Use the MCP working memory.", encoding="utf-8")
    tool = _tool_fn("lerim_working_memory")

    payload = tool(project="repo", refresh=False, max_chars=200)

    assert payload["error"] is False
    assert payload["project"] == "repo"
    assert payload["content"] == "Use the MCP working memory."


def test_trace_submit_tool_uses_importer_and_force_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The MCP trace-submit tool persists provenance and calls the importer."""
    cfg = make_config(tmp_path / ".lerim")
    monkeypatch.setattr("lerim.mcp_server.get_config", lambda: cfg)
    captured: dict[str, Any] = {}

    class _Result:
        """Small import result object matching the importer contract."""

        trace_id = "trace_test"
        normalized_trace_path = tmp_path / "normalized.jsonl"
        session_id = "sess-test"
        scope_identity = type(
            "Scope",
            (),
            {
                "scope_type": "domain",
                "scope_id": "scope_test",
                "label": "Support",
            },
        )()
        ingest_result = {
            "status": "duplicate_skipped",
            "records_created": 0,
        }

    def _fake_import_trace_file(**kwargs: Any) -> _Result:
        captured.update(kwargs)
        return _Result()

    monkeypatch.setattr("lerim.mcp_server.import_trace_file", _fake_import_trace_file)
    tool = _tool_fn("lerim_trace_submit")

    payload = tool(
        trace_text='{"messages":[{"role":"user","content":"hello"}]}',
        source_name="support-bot",
        source_profile="support",
        scope_type="domain",
        scope="support",
        scope_label="Support",
        session_id="sess-test",
        filename_hint="session.json",
        force=True,
    )

    assert payload["error"] is False
    assert payload["status"] == "duplicate_skipped"
    assert payload["attempt_count"] == 1
    assert Path(payload["submission_manifest_path"]).is_file()
    assert payload["retry_command"].startswith("lerim trace retry ")
    assert captured["force"] is True
    assert captured["trace_path"].is_file()
    manifest = load_submission_manifest(captured["trace_path"])
    assert manifest["status"] == "duplicate_skipped"
    assert manifest["attempt_count"] == 1


def test_trace_submit_tool_records_failed_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed MCP trace submissions remain observable and retryable."""
    cfg = make_config(tmp_path / ".lerim")
    monkeypatch.setattr("lerim.mcp_server.get_config", lambda: cfg)

    def _raise_import_trace_file(**_kwargs: Any) -> Any:
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr("lerim.mcp_server.import_trace_file", _raise_import_trace_file)
    tool = _tool_fn("lerim_trace_submit")

    payload = tool(
        trace_text='{"messages":[{"role":"user","content":"hello"}]}',
        source_name="support-bot",
        source_profile="support",
        scope_type="domain",
        scope="support",
        session_id="sess-fail",
    )

    assert payload["error"] is True
    assert payload["type"] == "RuntimeError"
    assert payload["attempt_count"] == 1
    assert Path(payload["submitted_trace_path"]).is_file()
    assert Path(payload["submission_manifest_path"]).is_file()
    assert payload["retry_command"].startswith("lerim trace retry ")
    manifest = load_submission_manifest(Path(payload["submitted_trace_path"]))
    assert manifest["status"] == "failed"
    assert manifest["last_error"] == {
        "type": "RuntimeError",
        "message": "provider unavailable",
    }


def test_context_answer_tool_requires_question() -> None:
    """Blank questions are rejected before reaching the answer API."""
    tool = _tool_fn("lerim_context_answer")

    assert tool(question="   ") == {"error": True, "message": "question_required"}


def test_context_answer_tool_calls_answer_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """The answer tool delegates to the real API boundary with scope args."""
    captured: dict[str, Any] = {}

    def fake_answer(question: str, **kwargs: Any) -> dict[str, Any]:
        captured["question"] = question
        captured.update(kwargs)
        return {"error": False, "answer": "Grounded answer."}

    monkeypatch.setattr("lerim.mcp_server.api_answer", fake_answer)
    tool = _tool_fn("lerim_context_answer")

    payload = tool(question="What changed?", scope="project", project="repo", verbose=True)

    assert payload == {"error": False, "answer": "Grounded answer."}
    assert captured == {
        "question": "What changed?",
        "scope": "project",
        "project": "repo",
        "verbose": True,
    }


def test_context_search_tool_requires_query() -> None:
    """Blank search queries are rejected before reaching the store."""
    tool = _tool_fn("lerim_context_search")

    assert tool(query="\n\t") == {"error": True, "message": "query_required"}


def test_records_list_tool_clamps_limit_and_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Records list passes bounded pagination to the deterministic query API."""
    captured: dict[str, Any] = {}

    def fake_query(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"error": False, "rows": []}

    monkeypatch.setattr("lerim.mcp_server.api_query", fake_query)
    tool = _tool_fn("lerim_records_list")

    payload = tool(limit=1000, offset=-5, kind="decision", status=None)

    assert payload == {"error": False, "rows": []}
    assert captured["entity"] == "records"
    assert captured["mode"] == "list"
    assert captured["limit"] == 100
    assert captured["offset"] == 0
    assert captured["kind"] == "decision"
    assert captured["status"] is None


def test_ingest_status_tool_delegates_to_status_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ingest status exposes the compact API status snapshot."""
    captured: dict[str, Any] = {}

    def fake_status(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr("lerim.mcp_server.api_status", fake_status)
    tool = _tool_fn("lerim_ingest_status")

    assert tool(scope="project", project="repo") == {"ok": True}
    assert captured == {"scope": "project", "project": "repo"}


def test_trace_submit_tool_requires_trace_text() -> None:
    """Blank trace submissions are rejected before persistence."""
    tool = _tool_fn("lerim_trace_submit")

    assert tool(trace_text="", source_name="agent") == {
        "error": True,
        "message": "trace_text_required",
    }


def test_write_submitted_trace_preserves_payload(tmp_path: Path) -> None:
    """Submitted traces are written under a provenance-preserving workspace path."""
    payload = json.dumps([{"role": "user", "content": "remember this decision"}])
    path = _write_submitted_trace(
        root=tmp_path,
        trace_text=payload,
        source_name="codex",
        session_id="sess-1",
        filename_hint=None,
    )
    assert path.is_file()
    assert "mcp-submissions" in path.parts
    assert path.suffix == ".json"
    assert path.read_text(encoding="utf-8").strip() == payload


def test_trace_suffix_detects_json_jsonl_and_text() -> None:
    """Trace suffix inference keeps submitted artifacts inspectable."""
    assert _trace_suffix('[{"role":"user"}]') == ".json"
    assert _trace_suffix('{"messages": []}') == ".json"
    assert _trace_suffix('{"role":"user"}\n{"role":"assistant"}') == ".jsonl"
    assert _trace_suffix("plain transcript") == ".txt"


def test_safe_filename_part_limits_and_sanitizes() -> None:
    """Source names are converted into small safe filename components."""
    assert _safe_filename_part("Open Claw/Agent!") == "Open-Claw-Agent"
    assert _safe_filename_part("") == "trace"
    assert len(_safe_filename_part("x" * 200)) == 80


def test_stdout_guard_keeps_mcp_stdout_clean(capfd: pytest.CaptureFixture[str]) -> None:
    """Tool internals can be noisy without corrupting MCP stdout."""

    def _noisy() -> dict[str, bool]:
        print("debug noise")
        os.write(1, b"fd noise\n")
        return {"ok": True}

    assert _run_with_stdout_guard(_noisy) == {"ok": True}
    captured = capfd.readouterr()
    assert captured.out == ""
    assert "debug noise" in captured.err
    assert "fd noise" in captured.err


def _tool_fn(name: str) -> Any:
    """Return one registered FastMCP tool function by name."""
    server = create_mcp_server()
    for tool in server._tool_manager.list_tools():
        if tool.name == name:
            return tool.fn
    raise AssertionError(f"missing tool: {name}")
