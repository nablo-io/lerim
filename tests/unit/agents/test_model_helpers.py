"""Tests for shared model workflow helpers."""

from __future__ import annotations

from enum import Enum
from typing import Literal

import pytest
from pydantic import BaseModel, ValidationError

from lerim.agents.model_helpers import (
    call_model_step,
    instruction_with_validation_feedback,
    is_recoverable_model_error,
    model_retry_observation,
    prediction_payload,
    plain_value,
)


class AdapterParseError(Exception):
    """Fake recoverable structured-output error for retry tests."""


class NonRecoverableError(Exception):
    """Fake non-recoverable error for retry tests."""


class ExampleEnum(Enum):
    """Enum-like value used to verify JSON-ish conversion."""

    FACT = "fact"


class ExampleModel:
    """Minimal Pydantic-like object."""

    def model_dump(self, *, exclude_none: bool) -> dict[str, object | None]:
        """Return a Pydantic-like payload."""
        assert exclude_none is True
        payload = {"kind": ExampleEnum.FACT, "empty": None, "tags": [ExampleEnum.FACT]}
        return {key: value for key, value in payload.items() if value is not None}


class StrictKindModel(BaseModel):
    """Model with a narrow enum for validation-error retry coverage."""

    kind: Literal["fact"]


def test_model_payload_converts_generated_models_and_removes_none() -> None:
    payload = prediction_payload(ExampleModel())

    assert payload == {"kind": "fact", "tags": ["fact"]}


def test_plain_value_converts_nested_enums() -> None:
    payload = plain_value({"kind": ExampleEnum.FACT, "items": [ExampleEnum.FACT]})

    assert payload == {"kind": "fact", "items": ["fact"]}


def test_recoverable_error_detection_uses_model_error_names() -> None:
    assert is_recoverable_model_error(AdapterParseError("bad output")) is True
    assert is_recoverable_model_error(NonRecoverableError("boom")) is False


def test_recoverable_error_detection_includes_pydantic_validation_error() -> None:
    with pytest.raises(ValidationError) as exc:
        StrictKindModel.model_validate({"kind": "constraint}"})

    assert is_recoverable_model_error(exc.value) is True


def test_model_retry_observation_is_compact_json_guidance() -> None:
    content = model_retry_observation(AdapterParseError("bad\nshape"))

    assert "valid structured output" in content
    assert "<think>" in content
    assert "\n" not in content


def test_instruction_with_validation_feedback_appends_retry_guidance() -> None:
    instruction = instruction_with_validation_feedback(
        "Keep records compact.",
        "missing record_id",
        validation_retry_target="complete corrected action plan",
    )

    assert instruction.startswith("Keep records compact.")
    assert "missing record_id" in instruction
    assert "complete corrected action plan" in instruction


def test_call_model_step_retries_recoverable_model_errors() -> None:
    calls = 0

    def flaky_call() -> dict[str, str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise AdapterParseError("bad shape")
        return {"ok": "yes"}

    result, observations, attempts = call_model_step(
        flaky_call,
        stage="scan_window",
        progress=False,
        progress_label="trace-ingestion",
    )

    assert result == {"ok": "yes"}
    assert attempts == 2
    assert observations[0]["action"] == "model_retry"


def test_call_model_step_adds_semantic_feedback_to_instruction() -> None:
    calls: list[str] = []

    def fake_call(instruction: str) -> dict[str, str]:
        calls.append(instruction)
        return {"ok": "yes"}

    result, observations, attempts = call_model_step(
        fake_call,
        stage="review_health",
        progress=False,
        progress_label="context-curator",
        run_instruction="Keep records compact.",
        validate_result=lambda _result: None if len(calls) > 1 else "missing record_id",
        validation_retry_target="complete corrected action plan",
    )

    assert result == {"ok": "yes"}
    assert attempts == 2
    assert len(observations) == 1
    assert "missing record_id" in calls[1]


def test_call_model_step_does_not_retry_non_recoverable_errors() -> None:
    with pytest.raises(NonRecoverableError):
        call_model_step(
            lambda: (_ for _ in ()).throw(NonRecoverableError("boom")),
            stage="scan_window",
            progress=False,
            progress_label="trace-ingestion",
        )
