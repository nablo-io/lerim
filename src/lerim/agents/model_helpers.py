"""Shared helpers for DSPy-backed Lerim model workflows."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from lerim.agents.dspy_compat import dspy

MAX_MODEL_ATTEMPTS = 3
RECOVERABLE_DSPY_ERROR_NAMES = {
    "AdapterParseError",
    "LMError",
}


def call_model_step(
    call: Callable[..., Any],
    *,
    stage: str,
    progress: bool,
    progress_label: str,
    run_instruction: str | None = None,
    validate_result: Callable[[Any], str | None] | None = None,
    make_observation: Callable[[str, bool, str, dict[str, Any]], dict[str, Any]]
    | None = None,
    semantic_retry_content: Callable[[str], str] | None = None,
    validation_retry_target: str = "complete corrected output",
    raise_on_validation_failure: bool = True,
) -> tuple[Any, list[dict[str, Any]], int]:
    """Run one model step with parse, provider, and semantic-validation retries."""
    observations: list[dict[str, Any]] = []
    attempts = 0
    validation_feedback = ""

    def event(action: str, ok: bool, content: str, args: dict[str, Any]) -> dict[str, Any]:
        if make_observation is None:
            return {"action": action, "ok": ok, "content": content, **args}
        return make_observation(action, ok, content, args)

    while True:
        attempts += 1
        try:
            if run_instruction is None:
                result = call()
            else:
                result = call(
                    instruction_with_validation_feedback(
                        run_instruction,
                        validation_feedback,
                        validation_retry_target=validation_retry_target,
                    )
                )
        except Exception as exc:
            if not is_recoverable_model_error(exc) or attempts >= MAX_MODEL_ATTEMPTS:
                raise
            observations.append(event("model_retry", False, model_retry_observation(exc), {"stage": stage, "attempt": attempts}))
            if progress:
                print(f"  {progress_label} retry {stage} attempt={attempts}", flush=True)
            continue
        if validate_result is None:
            return result, observations, attempts
        validation_error = validate_result(result)
        if not validation_error:
            return result, observations, attempts
        retry_content = (
            semantic_retry_content(validation_error)
            if semantic_retry_content
            else f"model_validation_failed: {validation_error}"
        )
        observations.append(
            event("model_retry", False, retry_content, {"stage": stage, "attempt": attempts})
        )
        if attempts >= MAX_MODEL_ATTEMPTS:
            if raise_on_validation_failure:
                raise RuntimeError(
                    f"invalid {stage} output after {MAX_MODEL_ATTEMPTS} attempts: "
                    f"{validation_error}"
                )
            return result, observations, attempts
        validation_feedback = validation_error
        if progress:
            print(f"  {progress_label} retry {stage} attempt={attempts}", flush=True)


def prediction_payload(value: Any, *, output_field: str | None = None) -> dict[str, Any]:
    """Convert DSPy predictions, Pydantic models, and dicts into plain dicts."""
    if output_field and hasattr(value, output_field):
        return prediction_payload(getattr(value, output_field))
    if hasattr(value, "model_dump"):
        return plain_value(value.model_dump(exclude_none=True))
    if isinstance(value, dict):
        return plain_value({key: item for key, item in value.items() if item is not None})
    if value is None:
        return {}
    return plain_value(
        {
            key: item
            for key, item in getattr(value, "__dict__", {}).items()
            if not key.startswith("_") and item is not None
        }
    )


def plain_value(value: Any) -> Any:
    """Convert enums and nested model-ish values into JSON-compatible values."""
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return enum_value
    if isinstance(value, dict):
        return {key: plain_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [plain_value(item) for item in value]
    return value


def is_recoverable_model_error(exc: Exception) -> bool:
    """Return whether a model/provider/parsing failure should be retried."""
    if isinstance(exc, ValidationError):
        return True
    if isinstance(exc, dspy.LMError):
        return True
    return type(exc).__name__ in RECOVERABLE_DSPY_ERROR_NAMES


def model_retry_observation(exc: Exception) -> str:
    """Render a compact retry note for model-visible diagnostics."""
    message = str(exc).replace("\n", " ")[:1200]
    return (
        "The previous model step did not produce valid structured output. "
        "Retry and return exactly one JSON object matching the requested schema. "
        "Do not include <think> tags, hidden reasoning, markdown, or prose before "
        f"the JSON. Error: {type(exc).__name__}: {message}"
    )


def instruction_with_validation_feedback(
    run_instruction: str,
    validation_feedback: str,
    *,
    validation_retry_target: str,
) -> str:
    """Add compact validation feedback to a retry instruction."""
    if not validation_feedback:
        return run_instruction
    return (
        f"{run_instruction}\n\n"
        "Previous structured output was unsafe or incomplete. "
        f"Fix this validation error and return a {validation_retry_target}: "
        f"{validation_feedback}"
    )
