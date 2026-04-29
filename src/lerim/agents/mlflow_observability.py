"""Small MLflow tracing helpers for Lerim agent runs."""

from __future__ import annotations

import functools
import inspect
import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator

from pydantic_ai import ModelRetry
from pydantic_ai.exceptions import ToolRetryError

logger = logging.getLogger("lerim.mlflow")

_CURRENT_RUN: ContextVar[dict[str, Any] | None] = ContextVar(
    "lerim_mlflow_run", default=None
)


def _preview(value: Any, max_chars: int = 800) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=True, default=str)
    text = text.strip()
    return text if len(text) <= max_chars else text[: max_chars - 3].rstrip() + "..."


def _attrs(values: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in values.items():
        if value is None:
            out[str(key)] = None
        elif isinstance(value, (str, int, float, bool)):
            out[str(key)] = value
        else:
            out[str(key)] = json.dumps(value, ensure_ascii=True, default=str)
    return out


def _set_status(span: Any, status: str) -> None:
    try:
        span.set_status(status)
    except Exception:
        logger.debug("Failed to set MLflow span status")


def _set_attrs(span: Any, values: dict[str, Any]) -> None:
    try:
        span.set_attributes(_attrs(values))
    except Exception:
        logger.debug("Failed to set MLflow span attributes")


def _set_inputs(span: Any, values: Any) -> None:
    try:
        span.set_inputs(values)
    except Exception:
        logger.debug("Failed to set MLflow span inputs")


def _set_outputs(span: Any, values: Any) -> None:
    try:
        span.set_outputs(values)
    except Exception:
        logger.debug("Failed to set MLflow span outputs")


@contextmanager
def lerim_mlflow_run(
    *,
    enabled: bool,
    operation: str,
    run_id: str,
    session_id: str,
    project_id: str,
    project_name: str,
    run_folder: Any | None = None,
    project_ids: list[str] | None = None,
    request_preview: str = "",
) -> Iterator[dict[str, Any]]:
    """Start one normal MLflow root trace for a Lerim agent run."""
    state: dict[str, Any] = {
        "enabled": False,
        "finished": False,
        "operation": operation,
        "run_id": run_id,
        "session_id": session_id,
        "project_id": project_id,
        "project": project_name,
        "project_ids": project_ids or [project_id],
        "run_folder": None if run_folder is None else str(run_folder),
        "tool_call_count": 0,
        "controlled_retry_count": 0,
        "terminal_error_count": 0,
        "final_result_seen": False,
    }
    if not enabled:
        token = _CURRENT_RUN.set(state)
        try:
            yield state
        finally:
            _CURRENT_RUN.reset(token)
        return

    root_cm = None
    try:
        import mlflow

        state["enabled"] = True
        state["mlflow"] = mlflow
        root_attrs = _base_attrs(state)
        root_cm = mlflow.start_span(
            name=f"lerim.{operation}",
            span_type="CHAIN",
            attributes=root_attrs,
        )
        root_span = root_cm.__enter__()
        state["root_span"] = root_span
        mlflow.update_current_trace(
            client_request_id=run_id,
            tags={
                "lerim.run_id": run_id,
                "lerim.operation": operation,
                "lerim.session_id": session_id,
                "lerim.project_id": project_id,
                "lerim.project": project_name,
            },
            metadata={
                key: str(value)
                for key, value in {
                    "lerim.run_folder": run_folder,
                    "lerim.project_ids": json.dumps(state["project_ids"], ensure_ascii=True),
                }.items()
                if value is not None
            },
            request_preview=request_preview or f"{operation}:{session_id}",
        )
        _set_inputs(
            root_span,
            {
                "operation": operation,
                "session_id": session_id,
                "project_ids": state["project_ids"],
            },
        )
    except Exception as exc:
        logger.warning("[%s] MLflow tracing unavailable: %s", operation, exc)
        state["enabled"] = False
        state.pop("mlflow", None)
        state.pop("root_span", None)

    token = _CURRENT_RUN.set(state)
    try:
        yield state
    except BaseException as exc:
        if not state.get("finished"):
            finish_mlflow_run(
                state,
                final_status="failed",
                terminal_error=exc,
                response_preview=str(exc),
            )
        raise
    finally:
        _CURRENT_RUN.reset(token)
        if root_cm is not None:
            root_cm.__exit__(None, None, None)


def _base_attrs(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "lerim.operation": state["operation"],
        "lerim.run_id": state["run_id"],
        "lerim.session_id": state["session_id"],
        "lerim.project_id": state["project_id"],
        "lerim.project": state["project"],
        "lerim.project_ids": state["project_ids"],
        "lerim.run_folder": state.get("run_folder"),
    }


@contextmanager
def mlflow_span(
    name: str,
    *,
    span_type: str = "UNKNOWN",
    attributes: dict[str, Any] | None = None,
    inputs: Any | None = None,
) -> Iterator[Any]:
    """Start a child span in the current Lerim MLflow run, or no-op."""
    state = _CURRENT_RUN.get()
    if not state or not state.get("enabled"):
        yield None
        return

    mlflow = state["mlflow"]
    span_cm = mlflow.start_span(
        name=name,
        span_type=span_type,
        attributes=_attrs(attributes or {}),
    )
    span = span_cm.__enter__()
    if inputs is not None:
        _set_inputs(span, inputs)
    try:
        yield span
    except BaseException as exc:
        _set_status(span, "ERROR")
        span_cm.__exit__(type(exc), exc, exc.__traceback__)
        raise
    else:
        _set_status(span, "OK")
        span_cm.__exit__(None, None, None)


def finish_mlflow_run(
    state: dict[str, Any],
    *,
    final_status: str,
    response_preview: str = "",
    terminal_error: Exception | str | None = None,
    outputs: Any | None = None,
    records_created: int | None = None,
    records_updated: int | None = None,
    records_archived: int | None = None,
) -> None:
    """Set final root-span fields before leaving ``lerim_mlflow_run``."""
    state["finished"] = True
    if not state.get("enabled"):
        return
    span = state.get("root_span")
    if span is None:
        return
    values = {
        **_base_attrs(state),
        "lerim.final_status": final_status,
        "lerim.tool_call_count": state["tool_call_count"],
        "lerim.controlled_retry_count": state["controlled_retry_count"],
        "lerim.terminal_error_count": state["terminal_error_count"],
        "lerim.final_result_seen": state["final_result_seen"],
        "lerim.terminal_error": None if terminal_error is None else str(terminal_error),
    }
    if records_created is not None:
        values["lerim.records_created"] = int(records_created)
    if records_updated is not None:
        values["lerim.records_updated"] = int(records_updated)
    if records_archived is not None:
        values["lerim.records_archived"] = int(records_archived)
    _set_attrs(span, values)
    if outputs is not None:
        _set_outputs(span, outputs)
    _set_status(span, "OK" if final_status == "succeeded" else "ERROR")
    try:
        state["mlflow"].update_current_trace(
            response_preview=response_preview or final_status,
            state="OK" if final_status == "succeeded" else "ERROR",
        )
    except Exception:
        logger.debug("Failed to update MLflow trace final state")


async def handle_mlflow_event_stream(ctx: Any, events: Any) -> None:
    """Log PydanticAI retry-prompt and final-result events in the current run."""
    async for event in events:
        kind = str(getattr(event, "event_kind", "") or "")
        if kind == "function_tool_result":
            result = getattr(event, "result", None)
            if str(getattr(result, "part_kind", "") or "") == "retry-prompt":
                tool_name = str(getattr(result, "tool_name", "") or "unknown")
                with mlflow_span(
                    f"lerim.retry.{tool_name}",
                    span_type="EVENT",
                    attributes={
                        "lerim.event_type": "retry_prompt",
                        "lerim.tool_name": tool_name,
                        "lerim.tool_call_id": str(getattr(result, "tool_call_id", "") or ""),
                        "lerim.retry_requested": True,
                    },
                    inputs={"content": _preview(getattr(result, "content", ""))},
                ) as span:
                    if span is not None:
                        _set_outputs(span, {"observed": True})
        elif kind == "final_result":
            state = _CURRENT_RUN.get()
            if state is not None:
                state["final_result_seen"] = True
            with mlflow_span(
                "lerim.final_result",
                span_type="EVENT",
                attributes={
                    "lerim.event_type": "final_result",
                    "lerim.final_result_seen": True,
                    "lerim.tool_name": str(getattr(event, "tool_name", "") or ""),
                    "lerim.tool_call_id": str(getattr(event, "tool_call_id", "") or ""),
                },
            ) as span:
                if span is not None:
                    _set_outputs(span, {"observed": True})


def trace_mlflow_tool(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator for normal MLflow tool spans without changing tool schemas."""
    tool_name = getattr(func, "__name__", "tool")
    original_signature = inspect.signature(func)

    @functools.wraps(func)
    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        state = _CURRENT_RUN.get()
        if not state:
            return func(*args, **kwargs)
        state["tool_call_count"] += 1
        if not state.get("enabled"):
            return func(*args, **kwargs)

        try:
            bound = original_signature.bind_partial(*args, **kwargs)
            inputs = {
                key: _preview(value, 500)
                for key, value in bound.arguments.items()
                if key != "ctx"
            }
        except Exception:
            inputs = {"args": _preview(args[1:] if args else []), "kwargs": _preview(kwargs)}

        mlflow = state["mlflow"]
        span_cm = mlflow.start_span(
            name=f"lerim.tool.{tool_name}",
            span_type="TOOL",
            attributes={
                "lerim.tool_name": tool_name,
                "lerim.operation": state["operation"],
                "lerim.tool_call_index": state["tool_call_count"],
            },
        )
        span = span_cm.__enter__()
        _set_inputs(span, inputs)
        started = time.perf_counter()
        try:
            result = func(*args, **kwargs)
        except (ModelRetry, ToolRetryError) as exc:
            state["controlled_retry_count"] += 1
            _set_attrs(
                span,
                {
                    "lerim.outcome": "controlled_retry",
                    "lerim.retry_requested": True,
                    "lerim.terminal_error": False,
                    "lerim.error_type": type(exc).__name__,
                    "lerim.duration_ms": round((time.perf_counter() - started) * 1000, 3),
                },
            )
            _set_outputs(
                span,
                {
                    "outcome": "controlled_retry",
                    "error_type": type(exc).__name__,
                    "message": _preview(str(exc), 500),
                },
            )
            _set_status(span, "OK")
            span_cm.__exit__(None, None, None)
            raise
        except Exception as exc:
            state["terminal_error_count"] += 1
            _set_attrs(
                span,
                {
                    "lerim.outcome": "terminal_error",
                    "lerim.retry_requested": False,
                    "lerim.terminal_error": True,
                    "lerim.error_type": type(exc).__name__,
                    "lerim.duration_ms": round((time.perf_counter() - started) * 1000, 3),
                },
            )
            _set_outputs(
                span,
                {
                    "outcome": "terminal_error",
                    "error_type": type(exc).__name__,
                    "message": _preview(str(exc), 500),
                },
            )
            _set_status(span, "ERROR")
            span_cm.__exit__(type(exc), exc, exc.__traceback__)
            raise
        else:
            _set_attrs(
                span,
                {
                    "lerim.outcome": "succeeded",
                    "lerim.retry_requested": False,
                    "lerim.terminal_error": False,
                    "lerim.duration_ms": round((time.perf_counter() - started) * 1000, 3),
                },
            )
            _set_outputs(span, _preview(result, 1200))
            _set_status(span, "OK")
            span_cm.__exit__(None, None, None)
            return result

    _wrapped.__signature__ = original_signature  # type: ignore[attr-defined]
    return _wrapped
