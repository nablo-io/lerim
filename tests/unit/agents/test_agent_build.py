"""Tests for agent package contracts without LLM calls."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lerim.agents.context_answerer import (
    CONTEXT_ANSWERER_SYSTEM_PROMPT,
    ContextAnswerResult,
)
from lerim.agents.context_answerer.signatures import AnswerFromContext, PlanContextRetrieval
from lerim.agents.context_brief.signatures import CompileContextBrief
from lerim.agents.context_curator.signatures import CurateContextCluster, CurateRecordHealthBatch
from lerim.agents.context_graph.signatures import LinkContextRecords, ReviewContextGraphLinks
from lerim.agents.trace_ingestion import TraceIngestionResult
from lerim.agents.trace_ingestion.signatures import (
    FilterDurableSignal,
    ObserveSourceWindow,
    SynthesizeContextRecords,
)
from lerim.agents.context_curator import ContextCuratorResult
from lerim.agents.context_graph import ContextGraphResult


class TestBuildIngestAgent:
    """Tests for trace-ingestion public contract."""

    def test_signatures_exist(self):
        assert ObserveSourceWindow.instructions
        assert FilterDurableSignal.instructions
        assert SynthesizeContextRecords.instructions


class TestBuildCurateAgent:
    """Tests for context-curator public contract."""

    def test_signatures_exist(self):
        assert CurateContextCluster.instructions
        assert CurateRecordHealthBatch.instructions


class TestBuildContextGraphAgent:
    """Tests for context-graph public contract."""

    def test_signatures_exist(self):
        assert LinkContextRecords.instructions
        assert ReviewContextGraphLinks.instructions


class TestBuildAnswerAgent:
    """Tests for context-answerer public contract."""

    def test_system_prompt_non_empty(self):
        assert isinstance(CONTEXT_ANSWERER_SYSTEM_PROMPT, str)
        assert len(CONTEXT_ANSWERER_SYSTEM_PROMPT.strip()) > 0

    def test_signatures_exist(self):
        assert PlanContextRetrieval.instructions
        assert AnswerFromContext.instructions


class TestBuildContextBriefAgent:
    """Tests for context-brief compiler public contract."""

    def test_signature_exists(self):
        assert CompileContextBrief.instructions


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
        result = ContextCuratorResult(completion_summary="curated")
        assert result.completion_summary == "curated"

    def test_field_required(self):
        with pytest.raises(ValidationError):
            ContextCuratorResult()


class TestContextGraphResultSchema:
    """Tests for ContextGraphResult model validation."""

    def test_creation(self):
        result = ContextGraphResult(completion_summary="linked", nodes_written=2, edges_written=1)
        assert result.completion_summary == "linked"
        assert result.edges_written == 1

    def test_field_required(self):
        with pytest.raises(ValidationError):
            ContextGraphResult()


class TestContextAnswerResultSchema:
    """Tests for ContextAnswerResult model validation."""

    def test_creation(self):
        result = ContextAnswerResult(answer="the answer")
        assert result.answer == "the answer"

    def test_field_required(self):
        with pytest.raises(ValidationError):
            ContextAnswerResult()
