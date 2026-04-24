"""Tests for agent builder functions — construction without LLM calls."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from pydantic_ai.models import Model

from lerim.agents.model_settings import (
    LOW_VARIANCE_AGENT_MODEL_SETTINGS,
    LOW_VARIANCE_AGENT_TEMPERATURE,
)
from lerim.agents.ask import (
    ASK_SYSTEM_PROMPT,
    AskResult,
    build_ask_agent,
)
from lerim.agents.extract import SYSTEM_PROMPT, ExtractionResult, build_extract_agent
from lerim.agents.maintain import (
    MAINTAIN_SYSTEM_PROMPT,
    MaintainResult,
    build_maintain_agent,
)


def _make_model() -> Model:
    """Return a mock that satisfies Agent's model check."""
    model = MagicMock(spec=Model)
    return model


def _get_tool_names(agent) -> set[str]:
    return set(agent._function_toolset.tools.keys())


def test_shared_low_variance_settings_use_positive_nonzero_temperature():
    """Agent settings keep variance down without sending MiniMax-hostile zero."""
    assert LOW_VARIANCE_AGENT_MODEL_SETTINGS["temperature"] == LOW_VARIANCE_AGENT_TEMPERATURE
    assert 0.0 < LOW_VARIANCE_AGENT_TEMPERATURE < 1.0
    assert LOW_VARIANCE_AGENT_MODEL_SETTINGS["top_p"] == 0.9


class TestBuildExtractAgent:
    """Tests for build_extract_agent construction."""

    def test_has_seven_tools(self):
        agent = build_extract_agent(_make_model())
        assert len(agent._function_toolset.tools) == 7

    def test_tool_names(self):
        agent = build_extract_agent(_make_model())
        expected = {
            "trace_read",
            "search_records",
            "fetch_records",
            "create_record",
            "update_record",
            "note",
            "prune",
        }
        assert _get_tool_names(agent) == expected

    def test_has_three_history_processors(self):
        agent = build_extract_agent(_make_model())
        assert len(agent.history_processors) == 3

    def test_retries_five(self):
        agent = build_extract_agent(_make_model())
        assert agent._max_tool_retries == 5

    def test_output_retries_four(self):
        agent = build_extract_agent(_make_model())
        assert agent._max_result_retries == 4

    def test_uses_shared_low_variance_settings(self):
        agent = build_extract_agent(_make_model())
        assert agent.model_settings == LOW_VARIANCE_AGENT_MODEL_SETTINGS

    def test_system_prompt_non_empty(self):
        assert isinstance(SYSTEM_PROMPT, str)
        assert len(SYSTEM_PROMPT.strip()) > 0


class TestBuildMaintainAgent:
    """Tests for build_maintain_agent construction."""

    def test_has_six_tools(self):
        agent = build_maintain_agent(_make_model())
        assert len(agent._function_toolset.tools) == 6

    def test_tool_names(self):
        agent = build_maintain_agent(_make_model())
        expected = {
            "list_records",
            "search_records",
            "fetch_records",
            "update_record",
            "archive_record",
            "supersede_record",
        }
        assert _get_tool_names(agent) == expected

    def test_no_trace_read_tool(self):
        agent = build_maintain_agent(_make_model())
        assert "trace_read" not in _get_tool_names(agent)

    def test_retries_five(self):
        agent = build_maintain_agent(_make_model())
        assert agent._max_tool_retries == 5

    def test_output_retries_two(self):
        agent = build_maintain_agent(_make_model())
        assert agent._max_result_retries == 2

    def test_uses_shared_low_variance_settings(self):
        agent = build_maintain_agent(_make_model())
        assert agent.model_settings == LOW_VARIANCE_AGENT_MODEL_SETTINGS

    def test_system_prompt_non_empty(self):
        assert isinstance(MAINTAIN_SYSTEM_PROMPT, str)
        assert len(MAINTAIN_SYSTEM_PROMPT.strip()) > 0


class TestBuildAskAgent:
    """Tests for build_ask_agent construction."""

    def test_has_four_tools(self):
        agent = build_ask_agent(_make_model())
        assert len(agent._function_toolset.tools) == 4

    def test_tool_names_read_only(self):
        agent = build_ask_agent(_make_model())
        expected = {"context_query", "list_records", "search_records", "fetch_records"}
        assert _get_tool_names(agent) == expected

    def test_no_write_tools(self):
        agent = build_ask_agent(_make_model())
        write_tools = {
            "create_record",
            "update_record",
            "archive_record",
            "supersede_record",
            "note",
            "prune",
        }
        assert _get_tool_names(agent).isdisjoint(write_tools)

    def test_retries_five(self):
        agent = build_ask_agent(_make_model())
        assert agent._max_tool_retries == 5

    def test_output_retries_two(self):
        agent = build_ask_agent(_make_model())
        assert agent._max_result_retries == 2

    def test_uses_shared_low_variance_settings(self):
        agent = build_ask_agent(_make_model())
        assert agent.model_settings == LOW_VARIANCE_AGENT_MODEL_SETTINGS

    def test_system_prompt_non_empty(self):
        assert isinstance(ASK_SYSTEM_PROMPT, str)
        assert len(ASK_SYSTEM_PROMPT.strip()) > 0


class TestExtractionResultSchema:
    """Tests for ExtractionResult model validation."""

    def test_creation(self):
        result = ExtractionResult(completion_summary="done")
        assert result.completion_summary == "done"

    def test_field_required(self):
        with pytest.raises(ValidationError):
            ExtractionResult()


class TestMaintainResultSchema:
    """Tests for MaintainResult model validation."""

    def test_creation(self):
        result = MaintainResult(completion_summary="maintained")
        assert result.completion_summary == "maintained"

    def test_field_required(self):
        with pytest.raises(ValidationError):
            MaintainResult()


class TestAskResultSchema:
    """Tests for AskResult model validation."""

    def test_creation(self):
        result = AskResult(answer="the answer")
        assert result.answer == "the answer"

    def test_field_required(self):
        with pytest.raises(ValidationError):
            AskResult()
