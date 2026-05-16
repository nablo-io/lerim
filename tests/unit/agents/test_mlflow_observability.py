"""Unit tests for Lerim-owned MLflow observability helpers."""

from __future__ import annotations

import sys

import pytest

from lerim.agents.mlflow_observability import (
    finish_mlflow_run,
    lerim_mlflow_run,
    mlflow_span,
)


class FakeSpan:
    def __init__(self, name: str, span_type: str, attributes: dict):
        self.name = name
        self.span_type = span_type
        self.attributes = dict(attributes)
        self.inputs = None
        self.outputs = None
        self.status = None
        self.exit_type = None

    def set_inputs(self, inputs):
        self.inputs = inputs

    def set_outputs(self, outputs):
        self.outputs = outputs

    def set_attributes(self, attributes):
        self.attributes.update(attributes)

    def set_status(self, status):
        self.status = status


class FakeSpanContext:
    def __init__(self, fake_mlflow, name: str, span_type: str, attributes: dict):
        self.fake_mlflow = fake_mlflow
        self.span = FakeSpan(name=name, span_type=span_type, attributes=attributes)

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

    def start_span(self, name="span", span_type="UNKNOWN", attributes=None, **kwargs):
        return FakeSpanContext(self, name, span_type, attributes or {})

    def update_current_trace(self, **kwargs):
        self.trace_updates.append(kwargs)


def _run_kwargs() -> dict:
    return {
        "enabled": True,
        "operation": "ingest",
        "run_id": "ingest-run",
        "session_id": "session",
        "project_id": "proj",
        "project_name": "project",
    }


def test_mlflow_span_logs_success(monkeypatch):
    fake_mlflow = FakeMlflow()
    monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)

    with lerim_mlflow_run(**_run_kwargs()) as mlflow_run:
        with mlflow_span(
            "lerim.agent.trace_ingestion",
            span_type="AGENT",
            attributes={"lerim.agent_name": "trace_ingestion"},
            inputs={"trace": "sample"},
        ):
            pass
        finish_mlflow_run(
            mlflow_run,
            final_status="succeeded",
            outputs={"records_created": 1},
            records_created=1,
        )

    child = next(span for span in fake_mlflow.spans if span.name == "lerim.agent.trace_ingestion")
    assert child.span_type == "AGENT"
    assert child.attributes["lerim.agent_name"] == "trace_ingestion"
    assert child.inputs == {"trace": "sample"}
    assert child.status == "OK"

    root = fake_mlflow.spans[0]
    assert root.attributes["lerim.records_created"] == 1
    assert root.outputs == {"records_created": 1}
    assert root.status == "OK"


def test_mlflow_span_marks_errors(monkeypatch):
    fake_mlflow = FakeMlflow()
    monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)

    with pytest.raises(RuntimeError, match="boom"):
        with lerim_mlflow_run(**_run_kwargs()):
            with mlflow_span("lerim.agent.context_curator", span_type="AGENT"):
                raise RuntimeError("boom")

    child = next(span for span in fake_mlflow.spans if span.name == "lerim.agent.context_curator")
    assert child.status == "ERROR"
    assert child.exit_type is RuntimeError
    root = fake_mlflow.spans[0]
    assert root.status == "ERROR"


def test_mlflow_span_noops_when_run_disabled() -> None:
    with lerim_mlflow_run(enabled=False, **{k: v for k, v in _run_kwargs().items() if k != "enabled"}) as mlflow_run:
        with mlflow_span("lerim.agent.context_answerer") as span:
            assert span is None
        finish_mlflow_run(mlflow_run, final_status="succeeded")

    assert mlflow_run["finished"] is True
