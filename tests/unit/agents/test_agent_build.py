"""Tests for agent package contracts without LLM calls."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from lerim.agents.context_answerer import (
    CONTEXT_ANSWERER_SYSTEM_PROMPT,
    ContextAnswerResult,
)
from lerim.agents.trace_ingestion import TraceIngestionResult
from lerim.agents.context_curator import ContextCuratorResult
REPO_ROOT = Path(__file__).resolve().parents[3]


class TestBuildIngestAgent:
    """Tests for trace-ingestion public contract."""

    def test_baml_source_exists(self):
        path = REPO_ROOT / "src" / "lerim" / "agents" / "baml_src" / "trace_ingestion.baml"
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "function ObserveSourceWindow" in content
        assert "function FilterDurableSignal" in content
        assert "function SynthesizeContextRecords" in content


class TestBuildCurateAgent:
    """Tests for context-curator public contract."""

    def test_baml_source_exists(self):
        path = REPO_ROOT / "src" / "lerim" / "agents" / "baml_src" / "context_curator.baml"
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "function CurateContextCluster" in content
        assert "function CurateRecordHealthBatch" in content


class TestBuildAnswerAgent:
    """Tests for context-answerer public contract."""

    def test_system_prompt_non_empty(self):
        assert isinstance(CONTEXT_ANSWERER_SYSTEM_PROMPT, str)
        assert len(CONTEXT_ANSWERER_SYSTEM_PROMPT.strip()) > 0

    def test_baml_source_exists(self):
        path = REPO_ROOT / "src" / "lerim" / "agents" / "baml_src" / "context_answerer.baml"
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "function PlanContextRetrieval" in content
        assert "function AnswerFromContext" in content


class TestBuildContextBriefAgent:
    """Tests for context-brief compiler public contract."""

    def test_baml_source_exists(self):
        path = REPO_ROOT / "src" / "lerim" / "agents" / "baml_src" / "context_brief_compiler.baml"
        assert path.exists()
        assert "function CompileContextBrief" in path.read_text(encoding="utf-8")


class TestTraceIngestionResultSchema:
    """Tests for TraceIngestionResult model validation."""

    def test_creation(self):
        result = TraceIngestionResult(completion_summary="done")
        assert result.completion_summary == "done"

    def test_field_required(self):
        with pytest.raises(ValidationError):
            TraceIngestionResult()


class TestContextCuratorResultSchema:
    """Tests for ContextCuratorResult model validation."""

    def test_creation(self):
        result = ContextCuratorResult(completion_summary="maintained")
        assert result.completion_summary == "maintained"

    def test_field_required(self):
        with pytest.raises(ValidationError):
            ContextCuratorResult()


class TestContextAnswerResultSchema:
    """Tests for ContextAnswerResult model validation."""

    def test_creation(self):
        result = ContextAnswerResult(answer="the answer")
        assert result.answer == "the answer"

    def test_field_required(self):
        with pytest.raises(ValidationError):
            ContextAnswerResult()
