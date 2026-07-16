"""Regression tests tying public benchmark docs to raw artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from benchmarks.scripts.generate_benchmark_summary_svg import build_svg, load_snapshot


ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "benchmarks" / "results" / "raw"
DOCS = ROOT / "docs" / "benchmarks"


def _load_report(name: str) -> dict[str, Any]:
    """Load one public raw benchmark report."""
    return json.loads((RAW / name / "report.json").read_text(encoding="utf-8"))


def _doc(name: str) -> str:
    """Read one public benchmark doc."""
    return (DOCS / name).read_text(encoding="utf-8")


def _pct(value: float) -> str:
    """Format a ratio as the public docs percentage format."""
    return f"{float(value) * 100:.1f}%"


def _metric_pct(value: float) -> str:
    """Format a metric that may already be a percent."""
    numeric = float(value)
    if 0.0 <= numeric <= 1.0:
        numeric *= 100
    return f"{numeric:.2f}%"


def _metric_pct_one_decimal(value: float) -> str:
    """Format a metric as the one-decimal summary percentage."""
    numeric = float(value)
    if 0.0 <= numeric <= 1.0:
        numeric *= 100
    return f"{numeric:.1f}%"


def test_benchmark_summary_svg_is_generated_from_raw_artifacts() -> None:
    """The README benchmark graphic must not drift from raw benchmark reports."""
    actual = (ROOT / "docs" / "assets" / "benchmark-summary.svg").read_text(
        encoding="utf-8"
    )
    expected = build_svg(load_snapshot(RAW))

    assert actual == expected


def test_market_comparison_lists_each_mempalace_tracked_boundary() -> None:
    """Source-reported MemPalace rows should keep full and held-out sets separate."""
    text = _doc("market-comparison.md")

    assert "| MemPalace | Raw ChromaDB full set | 500 | 96.6% |" in text
    assert "| MemPalace | hybrid_v4 no-rerank held-out set | 450 | 98.4% | 99.8% |" in text


def test_readme_launch_links_are_github_ready_without_duplicate_demo_asset() -> None:
    """README launch links should be concrete and avoid duplicate demo media."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert 'href="docs/benchmarks/index.md"' in text
    assert 'href="docs/examples/index.md"' in text
    assert "docs/assets/lerim-context-compiler.svg" in text
    assert "docs/assets/lerim-context-retrieval.svg" in text
    assert "docs/assets/lerim-custom-trace-folder.svg" in text
    assert "docs/assets/lerim-architecture.svg" not in text
    assert "docs/assets/support-boundary.svg" not in text
    assert "docs/assets/lerim-trace-to-answer.gif" not in text
    assert "Install In 60 Seconds" not in text
    assert "real run" not in text.lower()
    assert "AgentMemory" not in text


def test_benchmark_docs_use_repo_relative_artifact_paths_before_release_links() -> None:
    """Benchmark docs should not point at GitHub main paths before release commit."""
    docs = "\n".join(
        [
            _doc("index.md"),
            _doc("market-comparison.md"),
        ]
    )

    assert "benchmarks/results/raw/" in docs
    assert "benchmarks/results/reports/" in docs
    assert "github.com/nablo-io/lerim/tree/main/benchmarks/results" not in docs


def test_public_positioning_keeps_non_coding_custom_traces_available_today() -> None:
    """Business workflow docs should not make all non-coding use cases future-only."""
    text = (ROOT / "docs" / "concepts" / "business-workflows.md").read_text(
        encoding="utf-8"
    )

    assert "Research, revenue, security, and other workflows can use custom clean traces" in text
    assert "today when the source owner handles export, redaction, and retention" in text
    assert "are future signal-pack extensions" not in text


def test_config_docs_label_cloud_endpoint_as_planned() -> None:
    """Config docs should not imply hosted cloud is required for local usage."""
    text = (ROOT / "docs" / "configuration" / "config-toml.md").read_text(
        encoding="utf-8"
    )

    assert "# Planned hosted/team endpoint. Local-only usage does not require cloud auth." in text
    assert 'endpoint = "https://api.lerim.dev"' in text


def test_lerim_results_doc_matches_longmemeval_and_budget_artifacts() -> None:
    """Lerim results page must stay tied to raw retrieval/context artifacts."""
    text = _doc("lerim-results.md")
    hybrid = _load_report("longmemeval-hybrid-full")
    lexical = _load_report("longmemeval-lexical-full")
    budget = _load_report("context-budget-hybrid-full")

    hybrid_headline = hybrid["results"]["headline"]
    lexical_headline = lexical["results"]["headline"]
    budget_top_10 = budget["results"]["headline"]["top_10"]

    assert (
        f"R@5 {_pct(hybrid_headline['recall_any_at_5'])}, "
        f"R@10 {_pct(hybrid_headline['recall_any_at_10'])}, "
        f"R@20 {_pct(hybrid_headline['recall_any_at_20'])}, "
        f"NDCG@10 {_pct(hybrid_headline['ndcg_at_10'])}, "
        f"MRR {_pct(hybrid_headline['mrr'])}"
    ) in text
    assert (
        f"R@5 {_pct(lexical_headline['recall_any_at_5'])}, "
        f"R@10 {_pct(lexical_headline['recall_any_at_10'])}, "
        f"R@20 {_pct(lexical_headline['recall_any_at_20'])}, "
        f"NDCG@10 {_pct(lexical_headline['ndcg_at_10'])}, "
        f"MRR {_pct(lexical_headline['mrr'])}"
    ) in text
    assert (
        f"{_pct(budget_top_10['avg_reduction_ratio'])} context reduction "
        f"with {_pct(budget_top_10['recall_any'])} recall"
    ) in text


def test_lerim_results_doc_matches_latency_mcp_and_extraction_artifacts() -> None:
    """Lerim results page must not drift from latency/integration/extraction artifacts."""
    text = _doc("lerim-results.md")
    latency = _load_report("retrieval-latency-longmemeval")["results"]
    integration = _load_report("mcp-integration-full")["summary"]
    extraction_report = _load_report("extraction-minimax-m27-full-47")
    extraction = extraction_report["results"]["headline"]
    false_positive = _load_report("false-positive-extraction-minimax-m27-negative-cases")[
        "results"
    ]["headline"]

    assert (
        f"100 records p50 {latency['100']['p50_ms']:.1f} ms, "
        f"p99 {latency['100']['p99_ms']:.1f} ms; "
        f"1,000 records p50 {latency['1000']['p50_ms']:.1f} ms, "
        f"p99 {latency['1000']['p99_ms']:.1f} ms"
    ) in text
    assert (
        f"{integration['config_passed_count']}/{integration['known_target_count']} "
        "config probes"
    ) in text
    assert (
        f"doctor {integration['real_doctor_status_counts'].get('pass', 0)} passed/"
        f"{integration['real_doctor_status_counts'].get('skip', 0)} skipped"
    ) in text
    assert (
        "trace-submit extraction "
        f"{integration['trace_submit_extraction_acceptance_count']} accepted/"
        f"{integration['stdio_trace_submit_failed_count']} failed"
    ) in text
    if integration["trace_submit_extraction_acceptance_count"] == 0:
        assert "synthetic trace-submit extraction probe passed" not in text
        assert "created 1 episode record and 1 durable record" not in text
    assert (
        f"quality {_metric_pct(extraction['quality_avg'])}, "
        f"quality gate {_metric_pct(extraction['quality_gate_rate_pct'])}, "
        f"hard gate {_metric_pct(extraction['hard_gate_pass_rate_pct'])}"
    ) in text
    assert extraction_report["model_provider"]["agent_model"] in text
    assert extraction_report["model_provider"]["judge_model"] in text
    assert "judged by MiniMax M2.7" not in text
    assert (
        f"Negative precision {_metric_pct(false_positive['negative_precision_rate_pct'])}; "
        f"{false_positive['false_positive_case_count']} false-positive cases; "
        f"{false_positive['total_durable_records_on_negative_cases']} durable records created "
        f"across {false_positive['negative_case_count']} negative cases"
    ) in text


def test_lerim_results_doc_matches_gemini_live_mcp_artifact() -> None:
    """The Gemini live acceptance note must stay tied to the raw artifact."""
    text = _doc("lerim-results.md")
    gemini = _load_report("mcp-gemini-live-tool-call")["summary"]

    assert (
        "separate Gemini CLI artifact records "
        f"{gemini['installed_client_connection_acceptance_count']} "
        "installed-client connection and "
        f"{gemini['installed_client_tool_call_acceptance_count']} live "
        "`lerim_context_brief` tool-call acceptance"
    ) in text
    assert "| Gemini CLI live `lerim_context_brief` tool call | accepted |" in text


def test_market_comparison_doc_matches_same_boundary_raw_artifacts() -> None:
    """Market comparison same-boundary rows must match raw reports."""
    text = _doc("market-comparison.md")
    lerim = _load_report("longmemeval-hybrid-full")["results"]["headline"]
    lerim_lexical = _load_report("longmemeval-lexical-full")["results"]["headline"]
    baseline = _load_report("imported-market-baselines")["results"]
    baseline_by_mode = {row["mode"]: row["headline"] for row in baseline if "headline" in row}

    assert (
        f"| Lerim | Source-session context compiler | Hybrid R@5 "
        f"{_pct(lerim['recall_any_at_5'])}, R@10 {_pct(lerim['recall_any_at_10'])}, "
        f"NDCG@10 {_pct(lerim['ndcg_at_10'])}, MRR {_pct(lerim['mrr'])}; lexical R@5 "
        f"{_pct(lerim_lexical['recall_any_at_5'])}, R@10 "
        f"{_pct(lerim_lexical['recall_any_at_10'])}, "
        f"NDCG@10 {_pct(lerim_lexical['ndcg_at_10'])}, "
        f"MRR {_pct(lerim_lexical['mrr'])}"
    ) in text

    assert (
        f"| Lerim | Hybrid | 500 | {_pct(lerim['recall_any_at_5'])} | "
        f"{_pct(lerim['recall_any_at_10'])} | {_pct(lerim['recall_any_at_20'])} | "
        f"{_pct(lerim['ndcg_at_10'])} | {_pct(lerim['mrr'])} |"
    ) in text

    agent_hybrid = baseline_by_mode["hybrid"]
    agent_bm25 = baseline_by_mode["bm25"]
    assert (
        f"| AgentMemory | Local memory engine plus MCP server | Hybrid R@5 "
        f"{_pct(agent_hybrid['recall_any_at_5'])}, R@10 "
        f"{_pct(agent_hybrid['recall_any_at_10'])}, "
        f"NDCG@10 {_pct(agent_hybrid['ndcg_at_10'])}, "
        f"MRR {_pct(agent_hybrid['mrr'])}; BM25 R@5 "
        f"{_pct(agent_bm25['recall_any_at_5'])}, R@10 "
        f"{_pct(agent_bm25['recall_any_at_10'])}, "
        f"NDCG@10 {_pct(agent_bm25['ndcg_at_10'])}, "
        f"MRR {_pct(agent_bm25['mrr'])}"
    ) in text

    assert (
        f"| AgentMemory | BM25+Vector | 500 | {_pct(agent_hybrid['recall_any_at_5'])} | "
        f"{_pct(agent_hybrid['recall_any_at_10'])} | "
        f"{_pct(agent_hybrid['recall_any_at_20'])} | "
        f"{_pct(agent_hybrid['ndcg_at_10'])} | {_pct(agent_hybrid['mrr'])} |"
    ) in text
    assert (
        "| Lerim | Internal diagnostic aggregate exists; see "
        "[Lerim Results](lerim-results.md) for the current extraction-quality "
        "and false-positive numbers. |"
    ) in text
    assert "| AgentMemory | Not available yet; not run on this private eval. |" in text
    assert "| Mem0 | Not available yet; not run on this private eval. |" in text


def test_report_index_extraction_row_is_clearly_diagnostic() -> None:
    """Generated index should not hide extraction's diagnostic/non-launch-grade status."""
    index = (ROOT / "benchmarks" / "results" / "reports" / "index.md").read_text(
        encoding="utf-8"
    )
    extraction = _load_report("extraction-minimax-m27-full-47")["results"]["headline"]
    false_positive = _load_report("false-positive-extraction-minimax-m27-negative-cases")[
        "results"
    ]["headline"]

    assert (
        f"quality {_metric_pct_one_decimal(extraction['quality_avg'])}, "
        f"quality gate {_metric_pct_one_decimal(extraction['quality_gate_rate_pct'])}, "
        f"hard gate {_metric_pct_one_decimal(extraction['hard_gate_pass_rate_pct'])}"
    ) in index
    assert "| lerim_extraction_quality_minimax_m27_full_47 | `extraction-minimax-m27-full-47` | diagnostic |" in index
    assert (
        f"negative precision {_metric_pct_one_decimal(false_positive['negative_precision_rate_pct'])}, "
        f"false-positive cases {false_positive['false_positive_case_count']}, "
        "durable records on negatives "
        f"{false_positive['total_durable_records_on_negative_cases']}"
    ) in index
    assert (
        "| lerim_false_positive_extraction_minimax_m27_negative_cases | "
        "`false-positive-extraction-minimax-m27-negative-cases` | diagnostic |"
    ) in index


def test_report_index_marks_longmemeval_rows_as_retrieval_only() -> None:
    """Generated index should not make no-judge retrieval look answer-quality full."""
    index = (ROOT / "benchmarks" / "results" / "reports" / "index.md").read_text(
        encoding="utf-8"
    )

    assert "| longmemeval_s_retrieval_only | `hybrid` | retrieval-only |" in index
    assert "| longmemeval_s_retrieval_only | `lexical` | retrieval-only |" in index
    assert "| longmemeval_s_context_budget | `hybrid` | retrieval-only |" in index
    assert "| longmemeval_s_retrieval_latency | `hybrid` | retrieval-only |" in index
    assert "| longmemeval_s_retrieval_only | `hybrid` | full |" not in index
