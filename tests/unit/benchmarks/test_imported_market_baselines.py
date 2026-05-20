"""Unit tests for imported market-baseline normalization."""

from __future__ import annotations

import json

import pytest

from benchmarks.competitors.imported_market_baselines import (
    build_latency_boundary,
    metric_delta_pp,
    normalize_load_result,
    normalize_longmemeval_result,
    render_markdown,
    sha256_text,
)


def test_normalize_longmemeval_result_extracts_headline_metrics() -> None:
    """AgentMemory LongMemEval JSON is normalized without changing metrics."""
    result = normalize_longmemeval_result(
        "benchmark/data/longmemeval_results_hybrid.json",
        {
            "mode": "hybrid",
            "questions": 500,
            "recall_any_at_5": 0.952,
            "recall_any_at_10": 0.986,
            "recall_any_at_20": 0.994,
            "ndcg_at_10": 0.878,
            "mrr": 0.882,
            "per_type": {"single-session-user": {"count": 70}},
            "per_question": [{"question_id": "q1"}],
        },
    )

    assert result["kind"] == "longmemeval_s_retrieval_only"
    assert result["mode"] == "hybrid"
    assert result["questions"] == 500
    assert result["headline"]["recall_any_at_5"] == 0.952
    assert result["per_question_count"] == 1


def test_normalize_longmemeval_result_requires_agentmemory_fields() -> None:
    """Imported source parsing fails loudly for malformed source artifacts."""
    with pytest.raises(ValueError, match="agentmemory_longmemeval_missing_fields"):
        normalize_longmemeval_result("source.json", {"mode": "hybrid"})


def test_normalize_load_result_extracts_endpoint_cells() -> None:
    """AgentMemory load JSON keeps endpoint latency and throughput fields."""
    result = normalize_load_result(
        "benchmark/results/load.json",
        {
            "generated_at": "2026-05-13T00:00:00Z",
            "git_sha": "96c0ed0",
            "matrix": {"N": [1000], "C": [10]},
            "ops_per_cell": 200,
            "cells": [
                {
                    "endpoint": "POST /agentmemory/smart-search",
                    "N": 1000,
                    "C": 10,
                    "ops": 200,
                    "errors": 0,
                    "throughput_per_sec": 61.26,
                    "p50_ms": 160.064,
                    "p90_ms": 185.608,
                    "p99_ms": 224.354,
                }
            ],
        },
    )

    assert result["kind"] == "agentmemory_http_load"
    assert result["cells"][0]["endpoint"] == "POST /agentmemory/smart-search"
    assert result["cells"][0]["p99_ms"] == 224.354


def test_metric_delta_pp_reports_lerim_minus_agentmemory() -> None:
    """Metric deltas are represented in percentage points."""
    assert metric_delta_pp(0.824, 0.952) == pytest.approx(-12.8)
    assert metric_delta_pp(None, 0.952) is None


def test_latency_boundary_is_not_direct_comparison() -> None:
    """Latency comparison remains guarded when boundaries differ."""
    boundary = build_latency_boundary(
        [{"kind": "agentmemory_http_load", "cells": []}],
    )

    assert boundary["comparison_status"] == "not_directly_comparable"
    assert "HTTP endpoint" in boundary["agentmemory_measure"]
    assert "ContextStore.search" in boundary["lerim_measure"]


def test_render_markdown_includes_pinned_artifact_warning() -> None:
    """Markdown report makes the non-rerun boundary visible."""
    report = {
        "generated_at": "2026-05-19T00:00:00+00:00",
        "agentmemory": {
            "repo": "https://github.com/rohitg00/agentmemory",
            "commit": "abc123",
            "baseline_type": "pinned_upstream_raw_artifacts",
            "rerun_in_this_environment": False,
        },
        "source_artifacts": [
            {
                "source_path": "benchmark/data/longmemeval_results_hybrid.json",
                "sha256": sha256_text(json.dumps({"ok": True})),
            }
        ],
        "results": [
            {
                "kind": "longmemeval_s_retrieval_only",
                "mode": "hybrid",
                "questions": 500,
                "headline": {
                    "recall_any_at_5": 0.952,
                    "recall_any_at_10": 0.986,
                    "recall_any_at_20": 0.994,
                    "ndcg_at_10": 0.878,
                    "mrr": 0.882,
                },
            }
        ],
        "comparisons": {
            "longmemeval": [
                {
                    "agentmemory_mode": "hybrid",
                    "comparison_status": "pinned_upstream_competitor_row_for_market_table",
                    "lerim_available": True,
                    "warning": "Pinned upstream market row, not a fresh competitor rerun.",
                }
            ]
        },
        "publication_rules": [
            "Say pinned upstream competitor artifact, not fresh local competitor rerun."
        ],
    }

    markdown = render_markdown(report)

    assert "# Imported Market Baselines" in markdown
    assert "Current imported source rows cover the pinned upstream artifacts" in markdown
    assert "- Source system: `AgentMemory`" in markdown
    assert "Rerun in this environment: `False`" in markdown
    assert "Market Table Usage" in markdown
    assert "| System | Mode | Questions | R@5 |" in markdown
    assert "pinned_upstream_competitor_row_for_market_table" in markdown
    assert "-12.8 pp" not in markdown
    assert "not fresh local competitor rerun" in markdown
