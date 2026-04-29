"""Unit tests for Lerim-owned MLflow observability helpers."""

from __future__ import annotations

import inspect
import sys
from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.messages import FinalResultEvent, RetryPromptPart

from lerim.agents.mlflow_observability import (
    finish_mlflow_run,
    handle_mlflow_event_stream,
    lerim_mlflow_run,
    trace_mlflow_tool,
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
        "operation": "sync",
        "run_id": "sync-run",
        "session_id": "session",
        "project_id": "proj",
        "project_name": "project",
    }


async def _events(*items):
    for item in items:
        yield item


def test_trace_mlflow_tool_preserves_original_signature():
    def sample_tool(ctx, name: str, limit: int = 3) -> str:
        """Sample tool."""
        return name * limit

    wrapped = trace_mlflow_tool(sample_tool)

    assert wrapped.__name__ == "sample_tool"
    assert wrapped.__doc__ == "Sample tool."
    assert inspect.signature(wrapped) == inspect.signature(sample_tool)


def test_tool_wrapper_logs_success_and_controlled_retry(monkeypatch):
    fake_mlflow = FakeMlflow()
    monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)

    def flaky_tool(ctx, value: str) -> str:
        if value == "retry":
            raise ModelRetry("try again")
        return f"ok:{value}"

    wrapped = trace_mlflow_tool(flaky_tool)
    ctx = SimpleNamespace(deps=SimpleNamespace())

    with lerim_mlflow_run(**_run_kwargs()) as mlflow_run:
        assert wrapped(ctx, "done") == "ok:done"
        with pytest.raises(ModelRetry):
            wrapped(ctx, "retry")
        finish_mlflow_run(mlflow_run, final_status="succeeded")

    tool_spans = [span for span in fake_mlflow.spans if span.name == "lerim.tool.flaky_tool"]
    assert tool_spans[0].attributes["lerim.outcome"] == "succeeded"
    assert tool_spans[0].status == "OK"
    assert tool_spans[1].attributes["lerim.outcome"] == "controlled_retry"
    assert tool_spans[1].attributes["lerim.retry_requested"] is True
    assert tool_spans[1].attributes["lerim.terminal_error"] is False
    assert tool_spans[1].status == "OK"
    assert tool_spans[1].exit_type is None
    root_span = fake_mlflow.spans[0]
    assert root_span.attributes["lerim.tool_call_count"] == 2
    assert root_span.attributes["lerim.controlled_retry_count"] == 1


def test_tool_wrapper_logs_terminal_error(monkeypatch):
    fake_mlflow = FakeMlflow()
    monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)

    def broken_tool(ctx) -> str:
        raise RuntimeError("boom")

    wrapped = trace_mlflow_tool(broken_tool)
    ctx = SimpleNamespace(deps=SimpleNamespace())

    with pytest.raises(RuntimeError, match="boom"):
        with lerim_mlflow_run(**_run_kwargs()):
            wrapped(ctx)

    tool_span = next(span for span in fake_mlflow.spans if span.name == "lerim.tool.broken_tool")
    assert tool_span.attributes["lerim.outcome"] == "terminal_error"
    assert tool_span.attributes["lerim.terminal_error"] is True
    assert tool_span.status == "ERROR"
    assert tool_span.exit_type is RuntimeError


def test_event_handler_logs_retry_prompt_and_final_result(monkeypatch):
    fake_mlflow = FakeMlflow()
    monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)

    retry_event = SimpleNamespace(
        event_kind="function_tool_result",
        result=RetryPromptPart(
            content="add required fields",
            tool_name="save_context",
            tool_call_id="call-1",
        ),
    )
    final_event = FinalResultEvent(tool_name="final_result", tool_call_id="call-2")

    with lerim_mlflow_run(**_run_kwargs()) as mlflow_run:
        import asyncio

        asyncio.run(handle_mlflow_event_stream(None, _events(retry_event, final_event)))
        finish_mlflow_run(mlflow_run, final_status="succeeded")

    assert any(span.name == "lerim.retry.save_context" for span in fake_mlflow.spans)
    assert any(span.name == "lerim.final_result" for span in fake_mlflow.spans)
    root_span = fake_mlflow.spans[0]
    assert root_span.attributes["lerim.final_result_seen"] is True
