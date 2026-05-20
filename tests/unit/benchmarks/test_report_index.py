"""Unit tests for benchmark report index generation."""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.scripts import build_report_index


def _write_report(path: Path, benchmark: str, **extra: object) -> None:
    """Write a minimal benchmark report fixture."""
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "benchmark": benchmark,
        "generated_at": "2026-05-19T00:00:00+00:00",
        "summary": {
            "config_passed_count": 15,
            "known_target_count": 15,
            "stdio_passed_count": 1,
            "local_context_tool_call_acceptance_count": 1,
            "trace_submit_idempotency_acceptance_count": 1,
            "real_doctor_probe_count": 15,
            "installed_client_connection_acceptance_count": 3,
            "context_tool_call_acceptance_count": 1,
        },
    }
    report.update(extra)
    path.write_text(json.dumps(report), encoding="utf-8")


def test_collect_reports_skips_git_ignored_report_dirs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Report collection does not publish local ignored live artifacts."""
    raw_dir = tmp_path / "raw"
    public_report = raw_dir / "mcp-integration-full" / "report.json"
    live_report = raw_dir / "mcp-integration-live" / "report.json"
    _write_report(public_report, "lerim_mcp_integration")
    _write_report(live_report, "lerim_mcp_integration")

    monkeypatch.setattr(
        build_report_index,
        "_is_git_ignored",
        lambda path: "live" in str(path),
    )

    reports = build_report_index.collect_reports(raw_dir)

    assert [item["path"] for item in reports] == [public_report]


def test_collect_reports_fails_on_invalid_public_report_json(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Public raw artifacts must fail loudly when their report JSON is malformed."""
    raw_dir = tmp_path / "raw"
    report_path = raw_dir / "broken" / "report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(build_report_index, "_is_git_ignored", lambda path: False)

    try:
        build_report_index.collect_reports(raw_dir)
    except ValueError as exc:
        assert f"{report_path}: invalid public benchmark report" in str(exc)
    else:
        raise AssertionError("expected invalid public report to fail collection")


def test_render_index_includes_run_mode_worktree_and_relative_raw_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Rendered report rows disambiguate modes and avoid local absolute path leaks."""
    raw_dir = tmp_path / "benchmarks" / "results" / "raw"
    reports_dir = tmp_path / "benchmarks" / "results" / "reports"
    report_path = raw_dir / "longmemeval-hybrid-full" / "report.json"
    _write_report(
        report_path,
        "longmemeval_s_retrieval_only",
        retrieval_mode="hybrid",
        environment={"git_dirty": False},
    )
    monkeypatch.setattr(build_report_index, "_is_git_ignored", lambda path: False)
    monkeypatch.chdir(tmp_path)

    reports = build_report_index.collect_reports(raw_dir)
    rendered = build_report_index.render_index(
        reports,
        raw_dir=raw_dir.resolve(),
        reports_dir=reports_dir,
    )

    assert "| longmemeval_s_retrieval_only | `hybrid` |" in rendered
    assert "| Benchmark | Run | Scope |" in rendered
    assert "| clean |" in rendered
    assert "Raw directory: `benchmarks/results/raw`" in rendered
    assert str(tmp_path) not in rendered


def test_collect_reports_marks_no_judge_runs_as_retrieval_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Full retrieval coverage is still not an answer-quality benchmark."""
    raw_dir = tmp_path / "raw"
    report_path = raw_dir / "longmemeval-hybrid-full" / "report.json"
    _write_report(
        report_path,
        "longmemeval_s_retrieval_only",
        is_full_filtered_run=True,
        environment={"git_dirty": False},
        methodology={
            "retrieval_only": True,
            "semantic_judge_in_loop": False,
        },
    )
    monkeypatch.setattr(build_report_index, "_is_git_ignored", lambda path: False)

    reports = build_report_index.collect_reports(raw_dir)

    assert reports[0]["full"] is True
    assert reports[0]["scope"] == "retrieval-only"


def test_collect_reports_keeps_known_retrieval_ids_retrieval_only_without_methodology(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Known retrieval-only benchmark ids stay conservative if metadata regresses."""
    raw_dir = tmp_path / "raw"
    report_path = raw_dir / "longmemeval-hybrid-full" / "report.json"
    _write_report(
        report_path,
        "longmemeval_s_retrieval_only",
        is_full_filtered_run=True,
        environment={"git_dirty": False},
    )
    monkeypatch.setattr(build_report_index, "_is_git_ignored", lambda path: False)

    reports = build_report_index.collect_reports(raw_dir)

    assert reports[0]["full"] is True
    assert reports[0]["scope"] == "retrieval-only"


def test_collect_reports_labels_trace_ingestion_cost_sample(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Trace-ingestion cost rows are samples, not retrieval-only rows."""
    raw_dir = tmp_path / "raw"
    report_path = raw_dir / "trace-ingestion-cost" / "report.json"
    _write_report(
        report_path,
        "longmemeval_s_trace_ingestion_cost_performance",
        environment={"git_dirty": False},
        dataset={"evaluated_traces": 3, "snapshot": "dataset-sha"},
        results={
            "headline": {
                "trace_count": 3,
                "avg_ingestion_ms": 1000.0,
                "avg_llm_calls_per_trace": 5.0,
                "avg_db_size_delta_bytes": 2048.0,
            }
        },
    )
    monkeypatch.setattr(build_report_index, "_is_git_ignored", lambda path: False)

    reports = build_report_index.collect_reports(raw_dir)

    assert reports[0]["scope"] == "sample"
    assert reports[0]["questions"] == 3
    assert "avg LLM calls 5.0" in reports[0]["headline"]


def test_render_index_uses_general_market_label_for_competitor_baselines(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Generated report index keeps the benchmark surface market-wide."""
    raw_dir = tmp_path / "benchmarks" / "results" / "raw"
    reports_dir = tmp_path / "benchmarks" / "results" / "reports"
    report_path = raw_dir / "imported-market-baselines" / "report.json"
    _write_report(
        report_path,
        "imported_market_baselines",
        agentmemory={"baseline_type": "pinned_upstream_raw_artifacts"},
        environment={"lerim_git_dirty": True},
    )
    monkeypatch.setattr(build_report_index, "_is_git_ignored", lambda path: False)
    monkeypatch.chdir(tmp_path)

    reports = build_report_index.collect_reports(raw_dir)
    rendered = build_report_index.render_index(
        reports,
        raw_dir=raw_dir.resolve(),
        reports_dir=reports_dir,
    )

    assert "| imported_market_baselines | `pinned_upstream_raw_artifacts` |" in rendered
    assert "agentmemory_pinned_upstream_baseline" not in rendered
    assert "agentmemory-pinned-baseline" not in rendered


def test_build_index_copies_market_baseline_to_general_report_name(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Generated reports do not use a single competitor as the public headline."""
    raw_dir = tmp_path / "benchmarks" / "results" / "raw"
    reports_dir = tmp_path / "benchmarks" / "results" / "reports"
    report_dir = raw_dir / "imported-market-baselines"
    report_path = report_dir / "report.json"
    _write_report(
        report_path,
        "imported_market_baselines",
        agentmemory={"baseline_type": "pinned_upstream_raw_artifacts"},
    )
    report_dir.joinpath("report.md").write_text("# Imported Baseline\n", encoding="utf-8")
    monkeypatch.setattr(build_report_index, "_is_git_ignored", lambda path: False)
    monkeypatch.chdir(tmp_path)

    build_report_index.build_index(raw_dir, reports_dir)

    assert (reports_dir / "imported-market-baselines.md").exists()
    assert not (reports_dir / "imported-baseline-retrieval.md").exists()
    assert not (reports_dir / "agentmemory-pinned-baseline.md").exists()


def test_collect_reports_sorts_imported_baselines_after_lerim_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Generated index should start with first-party rows, not imported baselines."""
    raw_dir = tmp_path / "benchmarks" / "results" / "raw"
    imported = raw_dir / "imported-market-baselines" / "report.json"
    lerim = raw_dir / "longmemeval-hybrid-full" / "report.json"
    _write_report(
        imported,
        "imported_market_baselines",
        agentmemory={"baseline_type": "pinned_upstream_raw_artifacts"},
    )
    _write_report(
        lerim,
        "longmemeval_s_retrieval_only",
        retrieval_mode="hybrid",
        is_full_filtered_run=True,
    )
    monkeypatch.setattr(build_report_index, "_is_git_ignored", lambda path: False)

    reports = build_report_index.collect_reports(raw_dir)

    assert [item["benchmark"] for item in reports] == [
        "longmemeval_s_retrieval_only",
        "imported_market_baselines",
    ]


def test_headline_metric_mentions_local_and_installed_context_calls() -> None:
    """MCP headline keeps local and installed-client tool calls separate."""
    headline = build_report_index._headline_metric(
        {
            "benchmark": "lerim_mcp_integration",
            "summary": {
                "config_passed_count": 15,
                "known_target_count": 15,
                "stdio_passed_count": 1,
                "local_context_tool_call_acceptance_count": 1,
                "trace_submit_idempotency_acceptance_count": 1,
                "real_doctor_probe_count": 15,
                "installed_client_connection_acceptance_count": 3,
                "context_tool_call_acceptance_count": 1,
            },
        }
    )

    assert "local context calls 1" in headline
    assert "trace submit idempotency 1" in headline
    assert "installed-client tool calls 1" in headline


def test_headline_metric_marks_skipped_installed_tool_calls_as_not_run() -> None:
    """MCP headline does not make skipped live tool-call probes look like failures."""
    headline = build_report_index._headline_metric(
        {
            "benchmark": "lerim_mcp_integration",
            "summary": {
                "config_passed_count": 15,
                "known_target_count": 15,
                "stdio_passed_count": 1,
                "local_context_tool_call_acceptance_count": 1,
                "trace_submit_idempotency_acceptance_count": 1,
                "real_doctor_probe_count": 15,
                "installed_client_connection_acceptance_count": 3,
                "tool_call_probe_count": 3,
                "tool_call_status_counts": {"skip": 3},
                "context_tool_call_acceptance_count": 0,
            },
        }
    )

    assert "installed-client tool calls not run" in headline
    assert "installed-client tool calls 0" not in headline


def test_collect_reports_marks_full_mcp_matrix_as_full(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The index recognizes full MCP matrix artifacts, not only retrieval runs."""
    raw_dir = tmp_path / "raw"
    report_path = raw_dir / "mcp-integration-full" / "report.json"
    _write_report(
        report_path,
        "lerim_mcp_integration",
        is_full_integration_run=True,
        environment={"git_dirty": False},
    )
    monkeypatch.setattr(build_report_index, "_is_git_ignored", lambda path: False)

    reports = build_report_index.collect_reports(raw_dir)

    assert reports[0]["full"] is True
    assert reports[0]["scope"] == "full"


def test_collect_reports_marks_non_publishable_extraction_as_diagnostic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Diagnostic sanitized extraction reports should not render as plain full."""
    raw_dir = tmp_path / "raw"
    report_path = raw_dir / "extraction-minimax-m27-full-47" / "report.json"
    _write_report(
        report_path,
        "lerim_extraction_quality_minimax_m27_full_47",
        public_sanitized=True,
        environment={"git_dirty": False},
    )
    monkeypatch.setattr(build_report_index, "_is_git_ignored", lambda path: False)

    reports = build_report_index.collect_reports(raw_dir)

    assert reports[0]["full"] is True
    assert reports[0]["scope"] == "diagnostic"


def test_collect_reports_marks_false_positive_extraction_as_diagnostic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """False-positive extraction reports are diagnostic, not launch-grade rows."""
    raw_dir = tmp_path / "raw"
    report_path = (
        raw_dir
        / "false-positive-extraction-minimax-m27-negative-cases"
        / "report.json"
    )
    _write_report(
        report_path,
        "lerim_false_positive_extraction_minimax_m27_negative_cases",
        public_sanitized=True,
        environment={"git_dirty": False},
    )
    monkeypatch.setattr(build_report_index, "_is_git_ignored", lambda path: False)

    reports = build_report_index.collect_reports(raw_dir)

    assert reports[0]["full"] is True
    assert reports[0]["scope"] == "diagnostic"


def test_headline_metric_formats_extraction_quality_metrics() -> None:
    """Extraction-quality reports show diagnostic gate metrics in the index."""
    headline = build_report_index._headline_metric(
        {
            "benchmark": "lerim_extraction_quality_minimax_m27_full_47",
            "results": {
                "headline": {
                    "quality_avg": 0.6007,
                    "quality_gate_rate_pct": 51.06,
                    "hard_gate_pass_rate_pct": 19.15,
                }
            },
        }
    )

    assert headline == "quality 60.1%, quality gate 51.1%, hard gate 19.1%"


def test_headline_metric_formats_false_positive_extraction_metrics() -> None:
    """False-positive diagnostics show negative precision and durable leakage."""
    headline = build_report_index._headline_metric(
        {
            "benchmark": "lerim_false_positive_extraction_minimax_m27_negative_cases",
            "results": {
                "headline": {
                    "negative_precision_rate_pct": 28.57,
                    "false_positive_case_count": 10,
                    "total_durable_records_on_negative_cases": 65,
                }
            },
        }
    )

    assert (
        headline
        == "negative precision 28.6%, false-positive cases 10, durable records on negatives 65"
    )
