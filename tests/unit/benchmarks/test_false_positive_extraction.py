"""Unit tests for sanitized false-positive extraction diagnostics."""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.lerim_evidence.false_positive_extraction import (
    build_report,
    render_markdown,
    write_outputs,
    _read_source_report,
)


def _source_payload() -> dict[str, object]:
    """Build a small full-run-like extraction source fixture with negative cases."""
    return {
        "timestamp": "20260519_045951",
        "framework": "dspy",
        "agent_model": "MiniMax-M2.7",
        "agent_provider": "minimax",
        "judge_model": "MiniMax-M2.5",
        "num_traces": 3,
        "partial": False,
        "publishable": False,
        "summary": {
            "dataset_case_count": 3,
            "full_dataset_case_count": 3,
        },
        "cases": [
            {
                "name": "positive_case",
                "category": "coding",
                "source_profile": "coding",
                "scope_type": "project",
                "record_count": 2,
                "episode_count": 1,
                "durable_record_count": 2,
                "scores": {"quality_gate": 1.0},
            },
            {
                "name": "negative_clean",
                "category": "negative",
                "source_profile": "generic",
                "scope_type": "domain",
                "scope_id": "private-scope",
                "record_count": 1,
                "episode_count": 1,
                "durable_record_count": 0,
                "llm_calls": 2,
                "records": [{"body": "private extracted record body"}],
                "tool_calls": [{"content": "private tool payload"}],
                "judge_result": {"reasoning": "private judge details"},
                "assertions": {
                    "must_extract": [],
                    "must_not_extract": [{"concept": "private forbidden concept"}],
                    "min_durable_records": 0,
                    "max_durable_records": 0,
                    "expected_episode_count": 1,
                },
                "scores": {
                    "negative_precision": 1.0,
                    "forbidden_concept_rate": 1.0,
                    "signal_filtering": 1.0,
                    "quality_gate": True,
                    "hard_gate_pass": True,
                    "faithfulness": 0.25,
                },
            },
            {
                "name": "negative_false_positive",
                "category": "negative",
                "source_profile": "coding",
                "scope_type": "project",
                "record_count": 3,
                "episode_count": 1,
                "durable_record_count": 2,
                "llm_calls": 3,
                "assertions": {
                    "must_extract": [],
                    "must_not_extract": [{"concept": "private concept"}],
                    "min_durable_records": 0,
                    "max_durable_records": 0,
                    "expected_episode_count": 1,
                },
                "scores": {
                    "negative_precision": 0.0,
                    "forbidden_concept_rate": 0.5,
                    "signal_filtering": 0.0,
                    "quality_gate": False,
                    "hard_gate_pass": False,
                },
            },
        ],
    }


def test_build_report_uses_only_negative_cases_and_sanitizes_private_fields(
    tmp_path: Path,
) -> None:
    """False-positive diagnostics filter negative cases and exclude private text."""
    report = build_report(
        source_report=tmp_path / "source.json",
        source_payload=_source_payload(),
        source_sha256="abc123",
        generated_at="2026-05-19T00:00:00+00:00",
    )
    text = json.dumps(report)
    headline = report["results"]["headline"]

    assert report["benchmark"] == "lerim_false_positive_extraction_minimax_m27_negative_cases"
    assert report["public_sanitized"] is True
    assert report["dataset"]["source_total_cases"] == 3
    assert report["dataset"]["negative_cases"] == 2
    assert report["methodology"]["per_case_numeric_metrics_included"] is False
    assert "case_metrics" not in report
    assert "source_publishable_flag" not in report
    assert headline["negative_case_count"] == 2
    assert headline["no_durable_case_count"] == 1
    assert headline["false_positive_case_count"] == 1
    assert headline["negative_precision_rate_pct"] == 50.0
    assert headline["total_durable_records_on_negative_cases"] == 2
    assert "negative_clean" not in text
    assert "negative_false_positive" not in text
    assert "private extracted record body" not in text
    assert "private tool payload" not in text
    assert "private judge details" not in text
    assert "private forbidden concept" not in text
    assert "private-scope" not in text


def test_write_outputs_emits_false_positive_artifact_set(tmp_path: Path) -> None:
    """Importer output has the expected aggregate report files."""
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
    assert "False-Positive Extraction Diagnostic" in render_markdown(report)
    assert "Competitors have not been run" in render_markdown(report)


def test_read_source_report_rejects_missing_negative_cases(tmp_path: Path) -> None:
    """The diagnostic must not silently report over an empty negative slice."""
    payload = _source_payload()
    cases = payload["cases"]
    assert isinstance(cases, list)
    for case in cases:
        if isinstance(case, dict):
            case["category"] = "coding"
    source = tmp_path / "source.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    payload_read, digest = _read_source_report(source)

    try:
        build_report(
            source_report=source,
            source_payload=payload_read,
            source_sha256=digest,
            generated_at="2026-05-19T00:00:00+00:00",
        )
    except ValueError as exc:
        assert str(exc) == "false_positive_source_has_no_negative_cases"
    else:
        raise AssertionError("expected empty negative slice to fail")


def test_read_source_report_rejects_case_count_mismatch(tmp_path: Path) -> None:
    """Source case counts must stay internally consistent."""
    payload = _source_payload()
    payload["num_traces"] = 4
    source = tmp_path / "source.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    try:
        _read_source_report(source)
    except ValueError as exc:
        assert str(exc) == "false_positive_source_num_traces_mismatch"
    else:
        raise AssertionError("expected mismatched num_traces to fail")
