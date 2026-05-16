"""Unit tests for LerimRuntime orchestration."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from lerim.agents.context_answerer import ContextAnswerResult
from lerim.agents.trace_ingestion import TraceIngestionEvent, TraceIngestionResult, TraceIngestionRunDetails
from lerim.agents.context_curator import ContextCuratorEvent, ContextCuratorRunDetails
from lerim.agents.mlflow_observability import mlflow_span
from lerim.context import ContextStore, resolve_project_identity
from lerim.server.runtime import (
    LerimRuntime,
    _resolve_runtime_roots,
    _write_agent_trace,
    _write_json_artifact,
    _write_text_with_newline,
)
from lerim.context_brief import MemoryLine, ContextBriefDraft
from tests.helpers import make_config


def _build_runtime(tmp_path, monkeypatch):
    """Construct runtime with provider validation mocked."""
    cfg = replace(make_config(tmp_path), openrouter_api_key="test-key")
    monkeypatch.setattr(
        "lerim.config.providers.validate_provider_for_role",
        lambda *args, **kwargs: None,
    )
    return LerimRuntime(default_cwd=str(tmp_path), config=cfg)


def _extract_details(tmp_path) -> TraceIngestionRunDetails:
    """Return minimal fake extract details for ingest unit tests."""
    return TraceIngestionRunDetails(
        events=[
            TraceIngestionEvent(
                action="read_window",
                ok=True,
                content="read",
                args={},
            )
        ],
        llm_calls=1,
        done=True,
        context_db_path=str(tmp_path / "context.sqlite3"),
        project_id="proj_test",
        session_id="trace",
        model_name="test/model",
        trace_total_lines=1,
    )


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
        messages = [{"kind": "baml_call", "function": "PlanContextRetrieval"}]
        _write_agent_trace(path, messages)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) == 1


class TestContextBriefFlow:
    def test_context_brief_creates_mlflow_root_and_context_brief_span(
        self, tmp_path, monkeypatch
    ):
        rt = _build_runtime(tmp_path, monkeypatch)
        rt.config = replace(rt.config, mlflow_enabled=True)
        project = resolve_project_identity(tmp_path)
        store = ContextStore(rt.config.context_db_path)
        store.initialize()
        store.register_project(project)
        record = store.create_record(
            project_id=project.project_id,
            session_id=None,
            kind="fact",
            title="Context Brief uses durable records",
            body="Context Brief should be generated from durable records.",
            change_reason="test_seed",
        )

        class FakeSpan:
            def __init__(self, name, span_type, attributes):
                self.name = name
                self.span_type = span_type
                self.attributes = dict(attributes)
                self.inputs = None
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

        def _fake_context_brief(**kwargs):
            with mlflow_span(
                "lerim.agent.context_brief_compiler",
                span_type="AGENT",
                attributes={"lerim.agent_name": "context_brief_compiler"},
                inputs={"candidate_count": len(kwargs["candidates"])},
            ):
                pass
            return (
                ContextBriefDraft(
                    summary=(
                        MemoryLine(
                            "Context Brief should be generated from durable records.",
                            (str(record["record_id"]),),
                        ),
                    ),
                ),
                [{"kind": "baml_call", "function": "CompileContextBrief"}],
            )

        monkeypatch.setattr("lerim.server.runtime.compile_context_brief", _fake_context_brief)

        result = rt.context_brief(repo_root=tmp_path, force=True)

        assert result["status"] == "generated"
        assert fake_mlflow.spans[0].name == "lerim.context-brief"
        child = next(
            span
            for span in fake_mlflow.spans
            if span.name == "lerim.agent.context_brief_compiler"
        )
        assert child.span_type == "AGENT"
        assert child.attributes["lerim.agent_name"] == "context_brief_compiler"
        assert child.inputs == {"candidate_count": 1}
        assert fake_mlflow.spans[0].outputs["records_included"] == 1


class TestIngestFlow:
    def test_ingest_missing_trace_file(self, tmp_path, monkeypatch):
        rt = _build_runtime(tmp_path, monkeypatch)
        with pytest.raises(FileNotFoundError, match="trace_path_missing"):
            rt.ingest(trace_path=tmp_path / "missing.jsonl")

    def test_ingest_happy_path(self, tmp_path, monkeypatch):
        rt = _build_runtime(tmp_path, monkeypatch)

        trace = tmp_path / "trace.jsonl"
        trace.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")

        monkeypatch.setattr(
            "lerim.server.runtime.run_trace_ingestion",
            lambda **kwargs: (
                TraceIngestionResult(completion_summary="extracted"),
                _extract_details(tmp_path),
            ),
        )

        result = rt.ingest(trace_path=trace)

        run_folder = Path(result["run_folder"])
        assert run_folder.exists()
        assert run_folder.parent.name == "ingest"
        assert run_folder.name.startswith("ingest-")
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

    def test_ingest_retries_failure_before_record_changes(self, tmp_path, monkeypatch):
        rt = _build_runtime(tmp_path, monkeypatch)
        trace = tmp_path / "trace.jsonl"
        trace.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")
        attempts = {"count": 0}
        monkeypatch.setattr(time, "sleep", lambda *_: None)

        def _flaky_extraction(**kwargs):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("temporary extract")
            return (
                TraceIngestionResult(completion_summary="recovered"),
                _extract_details(tmp_path),
            )

        monkeypatch.setattr("lerim.server.runtime.run_trace_ingestion", _flaky_extraction)

        result = rt.ingest(trace_path=trace)

        assert attempts["count"] == 2
        assert Path(result["run_folder"], "agent.log").read_text(
            encoding="utf-8"
        ).strip() == "recovered"

    def test_ingest_does_not_retry_after_record_changes(self, tmp_path, monkeypatch):
        rt = _build_runtime(tmp_path, monkeypatch)
        trace = tmp_path / "trace.jsonl"
        trace.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")
        attempts = {"count": 0}
        monkeypatch.setattr(time, "sleep", lambda *_: None)
        monkeypatch.setattr(
            "lerim.server.runtime._record_change_counts",
            lambda *args, **kwargs: {"create": 1},
        )

        def _partial_failure(**kwargs):
            attempts["count"] += 1
            raise RuntimeError("failed after write")

        monkeypatch.setattr("lerim.server.runtime.run_trace_ingestion", _partial_failure)

        with pytest.raises(RuntimeError, match="failed after write"):
            rt.ingest(trace_path=trace)

        assert attempts["count"] == 1

    def test_ingest_failure_writes_structured_error_artifacts(
        self, tmp_path, monkeypatch
    ):
        rt = _build_runtime(tmp_path, monkeypatch)
        trace = tmp_path / "trace.jsonl"
        trace.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")
        monkeypatch.setattr(time, "sleep", lambda *_: None)
        monkeypatch.setattr(
            "lerim.server.runtime.run_trace_ingestion",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("broken extract")),
        )

        with pytest.raises(RuntimeError, match="broken extract"):
            rt.ingest(trace_path=trace)

        run_folders = list(
            (rt.config.global_data_dir / "workspace").glob("*/*/*/ingest/*")
        )
        run_folder = run_folders[0]
        manifest = json.loads(
            (run_folder / "manifest.json").read_text(encoding="utf-8")
        )
        error = json.loads((run_folder / "error.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "failed"
        assert error["type"] == "RuntimeError"
        assert "broken extract" in error["message"]

    def test_ingest_postprocessing_failure_marks_run_failed(self, tmp_path, monkeypatch):
        rt = _build_runtime(tmp_path, monkeypatch)
        trace = tmp_path / "trace.jsonl"
        trace.write_text('{"role":"user","content":"hello"}\n', encoding="utf-8")
        monkeypatch.setattr(
            "lerim.server.runtime.run_trace_ingestion",
            lambda **kwargs: (
                TraceIngestionResult(completion_summary="extracted"),
                _extract_details(tmp_path),
            ),
        )
        monkeypatch.setattr(
            "lerim.server.runtime._record_change_counts",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("count failed")),
        )

        with pytest.raises(RuntimeError, match="count failed"):
            rt.ingest(trace_path=trace)

        run_folder = list(
            (rt.config.global_data_dir / "workspace").glob("*/*/*/ingest/*")
        )[0]
        manifest = json.loads(
            (run_folder / "manifest.json").read_text(encoding="utf-8")
        )
        error = json.loads((run_folder / "error.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "failed"
        assert error["message"] == "count failed"


class TestCurateFlow:
    def test_curate_happy_path_and_trace_write(self, tmp_path, monkeypatch):
        rt = _build_runtime(tmp_path, monkeypatch)

        captured: dict[str, object] = {}

        def _fake_run_context_curator(**kwargs):
            captured["max_llm_calls"] = kwargs["max_llm_calls"]
            return (
                SimpleNamespace(completion_summary="curation complete"),
                ContextCuratorRunDetails(
                    events=[
                        ContextCuratorEvent(
                            action="final_result",
                            ok=True,
                            content="curation complete",
                            done=True,
                            completion_summary="curation complete",
                        )
                    ],
                    llm_calls=1,
                    done=True,
                    context_db_path=str(kwargs["context_db_path"]),
                    project_id=kwargs["project_identity"].project_id,
                    session_id=kwargs["session_id"],
                    model_name="test-model",
                ),
            )

        monkeypatch.setattr("lerim.server.runtime.run_context_curator", _fake_run_context_curator)

        result = rt.curate(repo_root=tmp_path)
        run_folder = Path(result["run_folder"])
        assert run_folder.parent.name == "curate"
        assert run_folder.name.startswith("curate-")
        assert (run_folder / "agent.log").read_text(
            encoding="utf-8"
        ).strip() == "curation complete"
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
        assert captured["max_llm_calls"] == rt.config.agent_role.curate_max_llm_calls
        assert result["context_db_path"] == str(rt.config.context_db_path)


class TestAnswerFlow:
    def test_answer_happy_path(self, tmp_path, monkeypatch):
        rt = _build_runtime(tmp_path, monkeypatch)
        captured: dict[str, object] = {}

        def _fake_run_context_answerer(**kwargs):
            captured["question"] = kwargs["question"]
            return ContextAnswerResult(answer="answer text")

        monkeypatch.setattr("lerim.server.runtime.run_context_answerer", _fake_run_context_answerer)
        answer, session_id, cost, debug = rt.answer("what changed?", repo_root=tmp_path)
        assert answer == "answer text"
        assert session_id.startswith("lerim-")
        assert cost == 0.0
        assert debug is None
        assert captured["question"] == "what changed?"

    def test_answer_uses_provided_session_id(self, tmp_path, monkeypatch):
        rt = _build_runtime(tmp_path, monkeypatch)
        monkeypatch.setattr(
            "lerim.server.runtime.run_context_answerer",
            lambda **kwargs: ContextAnswerResult(answer="ok"),
        )
        _, session_id, _, _ = rt.answer("hello", session_id="fixed-id", repo_root=tmp_path)
        assert session_id == "fixed-id"

    def test_answer_does_not_short_circuit_known_phrases(self, tmp_path, monkeypatch):
        rt = _build_runtime(tmp_path, monkeypatch)
        captured: dict[str, object] = {}

        def _fake_run_context_answerer(**kwargs):
            captured["question"] = kwargs["question"]
            return ContextAnswerResult(answer="agent answered")

        monkeypatch.setattr("lerim.server.runtime.run_context_answerer", _fake_run_context_answerer)
        answer, _, _, _ = rt.answer("what is the last memory", repo_root=tmp_path)
        assert answer == "agent answered"
        assert captured["question"] == "what is the last memory"

    def test_answer_can_return_debug_payload(self, tmp_path, monkeypatch):
        rt = _build_runtime(tmp_path, monkeypatch)

        def _fake_run_context_answerer(**kwargs):
            assert kwargs["return_messages"] is True
            return (
                ContextAnswerResult(answer="answer text"),
                [
                    {"kind": "baml_call", "function": "PlanContextRetrieval"},
                    {
                        "kind": "retrieval",
                        "action_type": "count",
                        "result_count": 3,
                    },
                    {"kind": "baml_call", "function": "AnswerFromContext"},
                ],
            )

        monkeypatch.setattr("lerim.server.runtime.run_context_answerer", _fake_run_context_answerer)
        answer, _, _, debug = rt.answer(
            "how many records?", repo_root=tmp_path, include_debug=True
        )
        assert answer == "answer text"
        assert debug is not None
        assert debug["retrieval_actions"][0]["action_type"] == "count"
        assert debug["retrieval_actions"][0]["result_count"] == 3
        assert debug["messages"][0]["parts"][0]["part_kind"] == "PlanContextRetrieval"
        assert debug["messages"][1]["parts"][0]["part_kind"] == "count"

    def test_answer_creates_mlflow_root_trace_without_changing_contract(
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
            "lerim.server.runtime.run_context_answerer",
            lambda **kwargs: ContextAnswerResult(answer="observed answer"),
        )

        answer, session_id, cost, debug = rt.answer("hello", repo_root=tmp_path)

        assert answer == "observed answer"
        assert session_id.startswith("lerim-")
        assert cost == 0.0
        assert debug is None
        root_span = fake_mlflow.spans[0]
        assert root_span.name == "lerim.answer"
        assert root_span.attributes["lerim.operation"] == "answer"
        assert root_span.attributes["lerim.final_status"] == "succeeded"
        assert root_span.status == "OK"
        assert root_span.outputs == {"answer": "observed answer"}
        assert fake_mlflow.trace_updates[0]["client_request_id"].startswith("answer-")
