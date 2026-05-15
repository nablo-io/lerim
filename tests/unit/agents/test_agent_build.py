"""Tests for agent builder functions — construction without LLM calls."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from lerim.agents.model_settings import (
    LOW_VARIANCE_AGENT_MODEL_SETTINGS,
    LOW_VARIANCE_AGENT_TEMPERATURE,
)
from lerim.agents.toolsets import CURRENT_AGENT_TOOL_NAMES
from lerim.agents.ask import (
    ASK_SYSTEM_PROMPT,
    AskResult,
)
from lerim.agents.extract import ExtractionResult
from lerim.agents.maintain import MaintainResult
AUTHORITATIVE_TOOL_DOCS = (
    "README.md",
    "src/lerim/README.md",
    "tests/README.md",
)
REPO_ROOT = Path(__file__).resolve().parents[3]


def test_shared_low_variance_settings_use_positive_nonzero_temperature():
    """Agent settings keep variance down without sending MiniMax-hostile zero."""
    assert LOW_VARIANCE_AGENT_MODEL_SETTINGS["temperature"] == LOW_VARIANCE_AGENT_TEMPERATURE
    assert 0.0 < LOW_VARIANCE_AGENT_TEMPERATURE < 1.0
    assert LOW_VARIANCE_AGENT_MODEL_SETTINGS["top_p"] == 0.9


def test_authoritative_docs_match_current_agent_tool_contract():
    """Authoritative docs list current tools."""
    for relative_path in AUTHORITATIVE_TOOL_DOCS:
        content = (REPO_ROOT / relative_path).read_text(encoding="utf-8")

        missing_current = [
            tool_name
            for tool_name in sorted(CURRENT_AGENT_TOOL_NAMES)
            if f"`{tool_name}`" not in content
        ]

        assert missing_current == [], relative_path


class TestBuildExtractAgent:
    """Tests for extract-agent public contract."""

    def test_baml_source_exists(self):
        path = REPO_ROOT / "src" / "lerim" / "agents" / "baml_src" / "extract_react.baml"
        assert path.exists()
        assert "function ScanTraceWindow" in path.read_text(encoding="utf-8")


class TestBuildMaintainAgent:
    """Tests for maintain-agent public contract."""

    def test_baml_source_exists(self):
        path = REPO_ROOT / "src" / "lerim" / "agents" / "baml_src" / "maintain.baml"
        assert path.exists()
        assert "function ReviewMaintainCluster" in path.read_text(encoding="utf-8")


class TestBuildAskAgent:
    """Tests for ask-agent public contract."""

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
