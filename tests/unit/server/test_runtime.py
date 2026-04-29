"""Unit tests for LerimRuntime orchestration (PydanticAI-only)."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import RateLimitError
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from lerim.agents.ask import AskResult
from lerim.agents.extract import ExtractionResult
from lerim.server.runtime import (
    LerimRuntime,
    _resolve_runtime_roots,
    _write_agent_trace,
    _mlflow_run_span,
    _write_json_artifact,
    _write_text_with_newline,
)
from tests.helpers import make_config


def _make_rate_limit_error() -> RateLimitError:
    """Build a real OpenAI RateLimitError for retry/fallback tests."""
    return RateLimitError(
        message="rate limited",
        response=httpx.Response(
            429,
            request=httpx.Request("POST", "https://test.local"),
        ),
        body=None,
    )


def _build_runtime(tmp_path, monkeypatch):
    """Construct runtime with provider validation mocked."""
    cfg = replace(make_config(tmp_path), openrouter_api_key="test-key")
    monkeypatch.setattr(
        "lerim.config.providers.validate_provider_for_role",
        lambda *args, **kwargs: None,
    )
    return LerimRuntime(default_cwd=str(tmp_path), config=cfg)


class TestHelpers:
    def test_resolve_runtime_roots_defaults(self, tmp_path):
        cfg = make_config(tmp_path)
        ws = _resolve_runtime_roots(config=cfg)
        assert ws == cfg.global_data_dir / "workspace"

    def test_write_json_artifact(self, tmp_path):
        path = tmp_path / "artifact.json"
        _write_json_artifact(path, {"k": "v"})
        text = path.read_text(encoding="utf-8")
        assert text.endswith("\n")
        assert json.loads(text) == {"k": "v"}

    def test_write_text_with_newline(self, tmp_path):
        path = tmp_path / "artifact.log"
        _write_text_with_newline(path, "hello")
        assert path.read_text(encoding="utf-8") == "hello\n"

    def test_write_agent_trace_serializes_messages(self, tmp_path):
        path = tmp_path / "agent_trace.json"
        messages = [ModelRequest(parts=[SystemPromptPart(content="system")])]
        _write_agent_trace(path, messages)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) == 1

    def test_mlflow_run_span_preserves_body_exception(self, tmp_path, monkeypatch):
        class FakeSpan:
            def __init__(self):
                self.exit_type = None

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                self.exit_type = exc_type
                return False

        span = FakeSpan()
        fake_mlflow = SimpleNamespace(
            start_span=lambda **kwargs: span,
            update_current_trace=lambda **kwargs: None,
        )
        monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)

        with pytest.raises(ValueError, match="body failed"):
            with _mlflow_run_span(
                enabled=True,
                operation="sync",
                run_id="sync-run",
                session_id="session",
                project_id="proj",
                project_name="project",
                run_folder=tmp_path,
            ):
                raise ValueError("body failed")

        assert span.exit_type is ValueError


class TestRunWithFallback:
    def test_success_primary(self, tmp_path, monkeypatch):
        rt = _build_runtime(tmp_path, monkeypatch)
        seen = []

        def call(model):
            seen.append(model)
            return "ok"

        result = rt._run_with_fallback(
            flow="test",
            callable_fn=call,
            model_builders=[lambda: "primary", lambda: "fallback"],
        )
        assert result == "ok"
        assert seen == ["primary"]

    def test_retry_transient_error_same_model(self, tmp_path, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda *_: None)
        rt = _build_runtime(tmp_path, monkeypatch)
        attempts = 0

        def call(_model):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RuntimeError("500 temporary")
            return "recovered"

        result = rt._run_with_fallback(
            flow="test",
            callable_fn=call,
            model_builders=[lambda: "primary"],
            max_attempts=3,
        )
        assert result == "recovered"
        assert attempts == 3

    def test_quota_switches_to_fallback(self, tmp_path, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda *_: None)
        rt = _build_runtime(tmp_path, monkeypatch)
        seen = []

        def call(model):
            seen.append(model)
            if model == "primary":
                raise _make_rate_limit_error()
            return "fallback-ok"

        result = rt._run_with_fallback(
            flow="test",
            callable_fn=call,
            model_builders=[lambda: "primary", lambda: "fallback"],
        )
        assert result == "fallback-ok"
        assert seen == ["primary", "fallback"]

    def test_usage_limit_short_circuit(self, tmp_path, monkeypatch):
        rt = _build_runtime(tmp_path, monkeypatch)
        count = 0

        def call(_model):
            nonlocal count
            count += 1
            raise UsageLimitExceeded("request_limit")

        with pytest.raises(UsageLimitExceeded):
            rt._run_with_fallback(
                flow="test",
                callable_fn=call,
                model_builders=[lambda: "primary", lambda: "fallback"],
            )
        assert count == 1

    def test_value_error_is_non_retryable(self, tmp_path, monkeypatch):
        rt = _build_runtime(tmp_path, monkeypatch)
        count = 0

        def call(_model):
            nonlocal count
            count += 1
            raise ValueError("decision_requires_decision_and_why")

        with pytest.raises(ValueError, match="decision_requires_decision_and_why"):
            rt._run_with_fallback(
                flow="test",
                callable_fn=call,
                model_builders=[lambda: "primary"],
                max_attempts=3,
            )
        assert count == 1

    def test_exhausted_models_raises_runtime_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda *_: None)
        rt = _build_runtime(tmp_path, monkeypatch)

        def call(_model):
            raise RuntimeError("still broken")

        with pytest.raises(RuntimeError, match="Failed after trying"):
            rt._run_with_fallback(
                flow="test",
                callable_fn=call,
                model_builders=[lambda: "primary"],
                max_attempts=2,
            )


class TestSyncFlow:
    def test_sync_missing_trace_file(self, tmp_path, monkeypatch):
        rt = _build_runtime(tmp_path, monkeypatch)
        with pytest.raises(FileNotFoundError, match="trace_path_missing"):
            rt.sync(trace_path=tmp_path / "missing.jsonl")

    def test_sync_happy_path(self, tmp_path, monkeypatch):
        rt = _build_runtime(tmp_path, monkeypatch)

        trace = tmp_path / "trace.jsonl"
        trace.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")

        monkeypatch.setattr(
            "lerim.server.runtime.build_pydantic_model",
            lambda *args, **kwargs: "fake-model",
        )
        monkeypatch.setattr(
            "lerim.server.runtime.run_extraction",
            lambda **kwargs: (
                ExtractionResult(completion_summary="extracted"),
                [ModelRequest(parts=[SystemPromptPart(content="extract")])],
            ),
        )

        result = rt.sync(trace_path=trace)

        run_folder = Path(result["run_folder"])
        assert run_folder.exists()
        assert run_folder.parent.name == "sync"
        assert run_folder.name.startswith("sync-")
        assert (run_folder / "agent.log").read_text(
            encoding="utf-8"
        ).strip() == "extracted"
        trace_data = json.loads(
            (run_folder / "agent_trace.json").read_text(encoding="utf-8")
        )
        assert isinstance(trace_data, list)
        manifest = json.loads(
            (run_folder / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["run_id"] == run_folder.name
        assert manifest["mlflow_client_request_id"] == run_folder.name
        assert manifest["status"] == "succeeded"
        events = (run_folder / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert [json.loads(line)["event"] for line in events] == [
            "started",
            "succeeded",
        ]
        assert result["trace_path"] == str(trace.resolve())
        assert result["context_db_path"] == str(rt.config.context_db_path)
        assert result["project_id"].startswith("proj_")

    def test_sync_failure_writes_structured_error_artifacts(
        self, tmp_path, monkeypatch
    ):
        rt = _build_runtime(tmp_path, monkeypatch)
        trace = tmp_path / "trace.jsonl"
        trace.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")
        monkeypatch.setattr(time, "sleep", lambda *_: None)
        monkeypatch.setattr(
            "lerim.server.runtime.build_pydantic_model",
            lambda *args, **kwargs: "fake-model",
        )
        monkeypatch.setattr(
            "lerim.server.runtime.run_extraction",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("broken extract")),
        )

        with pytest.raises(RuntimeError, match="broken extract"):
            rt.sync(trace_path=trace)

        run_folders = list(
            (rt.config.global_data_dir / "workspace").glob("*/*/*/sync/*")
        )
        run_folder = run_folders[0]
        manifest = json.loads(
            (run_folder / "manifest.json").read_text(encoding="utf-8")
        )
        error = json.loads((run_folder / "error.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "failed"
        assert error["type"] == "RuntimeError"
        assert "broken extract" in error["message"]

    def test_sync_postprocessing_failure_marks_run_failed(self, tmp_path, monkeypatch):
        rt = _build_runtime(tmp_path, monkeypatch)
        trace = tmp_path / "trace.jsonl"
        trace.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")
        monkeypatch.setattr(
            "lerim.server.runtime.build_pydantic_model",
            lambda *args, **kwargs: "fake-model",
        )
        monkeypatch.setattr(
            "lerim.server.runtime.run_extraction",
            lambda **kwargs: (
                ExtractionResult(completion_summary="extracted"),
                [ModelRequest(parts=[SystemPromptPart(content="extract")])],
            ),
        )
        monkeypatch.setattr(
            "lerim.server.runtime._record_change_counts",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("count failed")),
        )

        with pytest.raises(RuntimeError, match="count failed"):
            rt.sync(trace_path=trace)

        run_folder = list(
            (rt.config.global_data_dir / "workspace").glob("*/*/*/sync/*")
        )[0]
        manifest = json.loads(
            (run_folder / "manifest.json").read_text(encoding="utf-8")
        )
        error = json.loads((run_folder / "error.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "failed"
        assert error["message"] == "count failed"


class TestMaintainFlow:
    def test_maintain_happy_path_and_trace_write(self, tmp_path, monkeypatch):
        rt = _build_runtime(tmp_path, monkeypatch)

        captured: dict[str, object] = {}

        monkeypatch.setattr(
            "lerim.server.runtime.build_pydantic_model",
            lambda *args, **kwargs: "fake-model",
        )

        def _fake_run_maintain(**kwargs):
            captured["request_limit"] = kwargs["request_limit"]
            return (
                SimpleNamespace(completion_summary="maintenance complete"),
                [ModelRequest(parts=[SystemPromptPart(content="maintain")])],
            )

        monkeypatch.setattr("lerim.server.runtime.run_maintain", _fake_run_maintain)

        result = rt.maintain(repo_root=tmp_path)
        run_folder = Path(result["run_folder"])
        assert run_folder.parent.name == "maintain"
        assert run_folder.name.startswith("maintain-")
        assert (run_folder / "agent.log").read_text(
            encoding="utf-8"
        ).strip() == "maintenance complete"
        trace_data = json.loads(
            (run_folder / "agent_trace.json").read_text(encoding="utf-8")
        )
        assert isinstance(trace_data, list)
        manifest = json.loads(
            (run_folder / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["run_id"] == run_folder.name
        assert manifest["mlflow_client_request_id"] == run_folder.name
        assert manifest["status"] == "succeeded"
        assert captured["request_limit"] == rt.config.agent_role.max_iters_maintain
        assert result["context_db_path"] == str(rt.config.context_db_path)


class TestAskFlow:
    def test_ask_happy_path(self, tmp_path, monkeypatch):
        rt = _build_runtime(tmp_path, monkeypatch)
        captured: dict[str, object] = {}
        monkeypatch.setattr(
            "lerim.server.runtime.build_pydantic_model",
            lambda *args, **kwargs: "fake-model",
        )

        def _fake_run_ask(**kwargs):
            captured["request_limit"] = kwargs["request_limit"]
            captured["question"] = kwargs["question"]
            return AskResult(answer="answer text")

        monkeypatch.setattr("lerim.server.runtime.run_ask", _fake_run_ask)
        answer, session_id, cost, debug = rt.ask("what changed?", repo_root=tmp_path)
        assert answer == "answer text"
        assert session_id.startswith("lerim-")
        assert cost == 0.0
        assert debug is None
        assert captured["question"] == "what changed?"
        assert captured["request_limit"] == rt.config.agent_role.max_iters_ask

    def test_ask_uses_provided_session_id(self, tmp_path, monkeypatch):
        rt = _build_runtime(tmp_path, monkeypatch)
        monkeypatch.setattr(
            "lerim.server.runtime.build_pydantic_model",
            lambda *args, **kwargs: "fake-model",
        )
        monkeypatch.setattr(
            "lerim.server.runtime.run_ask",
            lambda **kwargs: AskResult(answer="ok"),
        )
        _, session_id, _, _ = rt.ask("hello", session_id="fixed-id", repo_root=tmp_path)
        assert session_id == "fixed-id"

    def test_ask_does_not_short_circuit_known_phrases(self, tmp_path, monkeypatch):
        rt = _build_runtime(tmp_path, monkeypatch)
        captured: dict[str, object] = {}
        monkeypatch.setattr(
            "lerim.server.runtime.build_pydantic_model",
            lambda *args, **kwargs: "fake-model",
        )

        def _fake_run_ask(**kwargs):
            captured["question"] = kwargs["question"]
            return AskResult(answer="agent answered")

        monkeypatch.setattr("lerim.server.runtime.run_ask", _fake_run_ask)
        answer, _, _, _ = rt.ask("what is the last memory", repo_root=tmp_path)
        assert answer == "agent answered"
        assert captured["question"] == "what is the last memory"

    def test_ask_can_return_debug_payload(self, tmp_path, monkeypatch):
        rt = _build_runtime(tmp_path, monkeypatch)
        monkeypatch.setattr(
            "lerim.server.runtime.build_pydantic_model",
            lambda *args, **kwargs: "fake-model",
        )

        def _fake_run_ask(**kwargs):
            assert kwargs["return_messages"] is True
            return (
                AskResult(answer="answer text"),
                [
                    ModelRequest(
                        parts=[
                            SystemPromptPart(content="system"),
                            UserPromptPart(content="how many records?"),
                        ]
                    ),
                    ModelResponse(
                        parts=[
                            ToolCallPart(
                                tool_name="count_context",
                                args={},
                                tool_call_id="call-1",
                            )
                        ]
                    ),
                    ModelRequest(
                        parts=[
                            ToolReturnPart(
                                tool_name="count_context",
                                content='{"count": 3}',
                                tool_call_id="call-1",
                            )
                        ]
                    ),
                ],
            )

        monkeypatch.setattr("lerim.server.runtime.run_ask", _fake_run_ask)
        answer, _, _, debug = rt.ask(
            "how many records?", repo_root=tmp_path, include_debug=True
        )
        assert answer == "answer text"
        assert debug is not None
        assert debug["tool_calls"][0]["tool_name"] == "count_context"
        assert debug["tool_results"][0]["tool_name"] == "count_context"
        assert debug["messages"][0]["parts"][0]["part_kind"] == "system-prompt"
        assert debug["messages"][1]["parts"][0]["part_kind"] == "tool-call"

    def test_ask_creates_mlflow_root_trace_without_changing_contract(
        self, tmp_path, monkeypatch
    ):
        rt = _build_runtime(tmp_path, monkeypatch)
        rt.config = replace(rt.config, mlflow_enabled=True)

        class FakeSpan:
            def __init__(self, name, span_type, attributes):
                self.name = name
                self.span_type = span_type
                self.attributes = dict(attributes)
                self.outputs = None
                self.status = None

            def set_inputs(self, inputs):
                self.inputs = inputs

            def set_outputs(self, outputs):
                self.outputs = outputs

            def set_attributes(self, attrs):
                self.attributes.update(attrs)

            def set_status(self, status):
                self.status = status

        class FakeSpanContext:
            def __init__(self, fake_mlflow, name, span_type, attributes):
                self.fake_mlflow = fake_mlflow
                self.span = FakeSpan(name, span_type, attributes)

            def __enter__(self):
                self.fake_mlflow.spans.append(self.span)
                return self.span

            def __exit__(self, exc_type, exc, tb):
                self.span.exit_type = exc_type
                return False

        class FakeMlflow:
            def __init__(self):
                self.spans = []
                self.trace_updates = []

            def start_span(self, name="span", span_type="UNKNOWN", attributes=None):
                return FakeSpanContext(self, name, span_type, attributes or {})

            def update_current_trace(self, **kwargs):
                self.trace_updates.append(kwargs)

        fake_mlflow = FakeMlflow()
        monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)
        monkeypatch.setattr(
            "lerim.server.runtime.build_pydantic_model",
            lambda *args, **kwargs: "fake-model",
        )
        monkeypatch.setattr(
            "lerim.server.runtime.run_ask",
            lambda **kwargs: AskResult(answer="observed answer"),
        )

        answer, session_id, cost, debug = rt.ask("hello", repo_root=tmp_path)

        assert answer == "observed answer"
        assert session_id.startswith("lerim-")
        assert cost == 0.0
        assert debug is None
        root_span = fake_mlflow.spans[0]
        assert root_span.name == "lerim.ask"
        assert root_span.attributes["lerim.operation"] == "ask"
        assert root_span.attributes["lerim.final_status"] == "succeeded"
        assert root_span.status == "OK"
        assert root_span.outputs == {"answer": "observed answer"}
        assert fake_mlflow.trace_updates[0]["client_request_id"].startswith("ask-")
