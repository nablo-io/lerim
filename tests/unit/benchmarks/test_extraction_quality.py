"""Unit tests for sanitized extraction-quality benchmark import."""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.lerim_evidence.extraction_quality import (
    build_report,
    render_markdown,
    write_outputs,
    _read_source_report,
)


def _source_payload() -> dict[str, object]:
    """Build a small full-run-like extraction source fixture."""
    return {
        "timestamp": "20260519_045951",
        "framework": "dspy",
        "config_file": "evals/configs/bench_minimax_m27.toml",
        "agent_model": "MiniMax-M2.7",
        "agent_provider": "minimax",
        "judge_model": "MiniMax-M2.5",
        "num_traces": 1,
        "partial": False,
        "publishable": False,
        "summary": {
            "framework": "dspy",
            "task_completion_rate_pct": 100.0,
            "quality_avg": 0.75,
            "quality_gate_rate_pct": 50.0,
            "hard_gate_pass_rate_pct": 25.0,
            "concept_recall_avg": 0.8,
            "required_concept_coverage_rate_pct": 80.0,
            "kind_alignment_rate_pct": 100.0,
            "record_precision_avg": 0.9,
            "faithfulness_avg": 0.85,
            "claim_faithfulness_rate_pct": 75.0,
            "negative_precision_rate_pct": 50.0,
            "signal_filtering_rate_pct": 50.0,
            "evidence_coverage_rate_pct": 100.0,
            "evidence_validity_rate_pct": 100.0,
            "dataset_case_count": 1,
            "full_dataset_case_count": 1,
            "dataset_coverage_rate_pct": 100.0,
            "source_profile_counts": {"coding": 1},
            "required_source_profiles": ["coding"],
            "covered_source_profiles": ["coding"],
            "missing_source_profiles": [],
            "required_guardrail_cases": ["case_1"],
            "missing_guardrail_cases": [],
            "case_failures": 0,
            "case_failure_rate_pct": 0.0,
        },
        "cases": [
            {
                "name": "case_1",
                "category": "coding",
                "source_profile": "coding",
                "scope_type": "project",
                "scope_id": "private-project",
                "record_count": 2,
                "episode_count": 1,
                "durable_record_count": 1,
                "llm_calls": 3,
                "tool_call_errors": 0,
                "records": [{"body": "private extracted record body"}],
                "tool_calls": [{"content": "private tool payload"}],
                "judge_result": {"reasoning": "private judge details"},
                "scores": {"quality": 0.75, "hard_gate_pass": False},
            }
        ],
    }


def test_build_report_keeps_only_public_safe_extraction_fields(tmp_path: Path) -> None:
    """Sanitized reports exclude private trace, record, tool, and judge details."""
    report = build_report(
        source_report=tmp_path / "source.json",
        source_payload=_source_payload(),
        source_sha256="abc123",
        generated_at="2026-05-19T00:00:00+00:00",
    )
    text = json.dumps(report)

    assert report["benchmark"] == "lerim_extraction_quality_minimax_m27_full_47"
    assert report["public_sanitized"] is True
    assert report["dataset"]["cases"] == 1
    assert report["results"]["headline"]["task_completion_rate_pct"] == 100.0
    assert report["methodology"]["per_case_numeric_metrics_included"] is False
    assert "case_metrics" not in report
    assert "source_publishable_flag" not in report
    assert "required_guardrail_cases" not in json.dumps(report["dataset"])
    assert "private extracted record body" not in text
    assert "private tool payload" not in text
    assert "private judge details" not in text
    assert "private-project" not in text


def test_write_outputs_emits_report_markdown_only(tmp_path: Path) -> None:
    """Importer output has the expected public artifact set."""
    report = build_report(
        source_report=tmp_path / "source.json",
        source_payload=_source_payload(),
        source_sha256="abc123",
        generated_at="2026-05-19T00:00:00+00:00",
    )

    write_outputs(report, tmp_path)

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "report.json",
        "report.md",
    ]
    assert "Extraction Quality Benchmark" in render_markdown(report)


def test_read_source_report_rejects_num_traces_case_mismatch(tmp_path: Path) -> None:
    """The sanitizer should not normalize inconsistent source summaries."""
    payload = _source_payload()
    payload["num_traces"] = 2
    source = tmp_path / "source.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    try:
        _read_source_report(source)
    except ValueError as exc:
        assert str(exc) == "extraction_report_num_traces_mismatch"
    else:
        raise AssertionError("expected mismatched num_traces to fail")


def test_read_source_report_rejects_summary_case_count_mismatch(tmp_path: Path) -> None:
    """Dataset coverage counts must match the included case rows."""
    payload = _source_payload()
    summary = payload["summary"]
    assert isinstance(summary, dict)
    summary["dataset_case_count"] = 2
    source = tmp_path / "source.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    try:
        _read_source_report(source)
    except ValueError as exc:
        assert str(exc) == "extraction_report_dataset_case_count_mismatch"
    else:
        raise AssertionError("expected mismatched dataset_case_count to fail")


def test_read_source_report_rejects_malformed_case_rows(tmp_path: Path) -> None:
    """Malformed case rows cannot be silently skipped."""
    payload = _source_payload()
    payload["cases"] = ["not-a-case-object"]
    source = tmp_path / "source.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    try:
        _read_source_report(source)
    except ValueError as exc:
        assert str(exc) == "extraction_report_case_0_must_be_object"
    else:
        raise AssertionError("expected malformed case row to fail")
