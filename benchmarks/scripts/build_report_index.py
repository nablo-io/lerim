"""Build a Markdown index from raw benchmark report artifacts."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


LONGMEMEVAL_RETRIEVAL_BENCHMARKS = frozenset(
    {
        "longmemeval_s_retrieval_only",
        "longmemeval_s_context_budget",
        "longmemeval_s_retrieval_latency",
    }
)


def _format_percent(value: Any) -> str:
    """Format a numeric ratio as a percentage."""
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _format_metric_percent(value: Any) -> str:
    """Format a metric that may already be either a ratio or a percent."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if 0.0 <= numeric <= 1.0:
        numeric *= 100
    return f"{numeric:.1f}%"


def _headline_metric(report: dict[str, Any]) -> str:
    """Return one compact headline string for a report."""
    benchmark = str(report.get("benchmark") or "")
    results = report.get("results") or {}
    if "retrieval_latency" in benchmark:
        results = report.get("results") or {}
        if isinstance(results, dict) and results:
            largest_size = sorted(results, key=lambda item: int(item))[-1]
            payload = results.get(largest_size) or {}
            if isinstance(payload, dict):
                return (
                    f"{largest_size} records p50 {float(payload.get('p50_ms', 0.0)):.1f} ms, "
                    f"p99 {float(payload.get('p99_ms', 0.0)):.1f} ms"
                )
        return "latency matrix"
    if "trace_ingestion_cost_performance" in benchmark:
        headline = (report.get("results") or {}).get("headline") or {}
        if isinstance(headline, dict):
            return (
                f"{headline.get('trace_count', 0)} traces, "
                f"avg ingestion {float(headline.get('avg_ingestion_ms') or 0.0):.1f} ms, "
                f"avg LLM calls {float(headline.get('avg_llm_calls_per_trace') or 0.0):.1f}, "
                f"avg DB growth {float(headline.get('avg_db_size_delta_bytes') or 0.0):.0f} bytes, "
                "cost unavailable"
            )
        return "trace ingestion cost/performance"
    if "mcp_integration" in benchmark:
        summary = report.get("summary") or {}
        if isinstance(summary, dict):
            tool_call_status_counts = summary.get("tool_call_status_counts") or {}
            skipped_tool_call_count = 0
            if isinstance(tool_call_status_counts, dict):
                skipped_tool_call_count = int(tool_call_status_counts.get("skip") or 0)
            tool_call_probe_count = int(summary.get("tool_call_probe_count") or 0)
            installed_tool_call_headline = (
                "installed-client tool calls not run"
                if tool_call_probe_count > 0 and skipped_tool_call_count == tool_call_probe_count
                else (
                    "installed-client tool calls "
                    f"{summary.get('context_tool_call_acceptance_count', 0)}"
                )
            )
            return (
                f"{summary.get('config_passed_count', 0)}/"
                f"{summary.get('known_target_count', 0)} config probes, "
                f"stdio tools {summary.get('stdio_passed_count', 0)}, "
                f"local context calls "
                f"{summary.get('local_context_tool_call_acceptance_count', 0)}, "
                f"trace submit idempotency "
                f"{summary.get('trace_submit_idempotency_acceptance_count', 0)}, "
                f"trace submit extraction "
                f"{summary.get('trace_submit_extraction_acceptance_count', 0)}, "
                f"installed config validation {summary.get('real_doctor_probe_count', 0)}, "
                f"installed client connections "
                f"{summary.get('installed_client_connection_acceptance_count', 0)}, "
                f"{installed_tool_call_headline}"
            )
    if benchmark == "imported_market_baselines":
        results = report.get("results") or []
        if isinstance(results, list):
            rows: list[str] = []
            for result in results:
                if not isinstance(result, dict):
                    continue
                if result.get("kind") != "longmemeval_s_retrieval_only":
                    continue
                headline = result.get("headline") or {}
                if not isinstance(headline, dict):
                    continue
                rows.append(
                    f"{result.get('mode')} R@5 {_format_percent(headline.get('recall_any_at_5'))}, "
                    f"R@10 {_format_percent(headline.get('recall_any_at_10'))}, "
                    f"MRR {_format_percent(headline.get('mrr'))}"
                )
            if rows:
                return "; ".join(rows)
    headline = results.get("headline") if isinstance(results, dict) else {}
    if not isinstance(headline, dict):
        return "n/a"
    if "extraction_quality" in benchmark:
        return (
            f"quality {_format_metric_percent(headline.get('quality_avg'))}, "
            f"quality gate {_format_metric_percent(headline.get('quality_gate_rate_pct'))}, "
            f"hard gate {_format_metric_percent(headline.get('hard_gate_pass_rate_pct'))}"
        )
    if "false_positive_extraction" in benchmark:
        return (
            "negative precision "
            f"{_format_metric_percent(headline.get('negative_precision_rate_pct'))}, "
            f"false-positive cases {headline.get('false_positive_case_count', 'n/a')}, "
            "durable records on negatives "
            f"{headline.get('total_durable_records_on_negative_cases', 'n/a')}"
        )
    if "longmemeval_s_context_budget" in benchmark:
        top_10 = headline.get("top_10") or {}
        if isinstance(top_10, dict):
            return (
                f"top10 reduction {_format_percent(top_10.get('avg_reduction_ratio'))}, "
                f"recall {_format_percent(top_10.get('recall_any'))}"
            )
    if "recall_any_at_5" in headline:
        return (
            f"R@5 {_format_percent(headline.get('recall_any_at_5'))}, "
            f"R@10 {_format_percent(headline.get('recall_any_at_10'))}, "
            f"MRR {_format_percent(headline.get('mrr'))}"
        )
    return "n/a"


def collect_reports(raw_dir: Path, *, repo_root: Path | None = None) -> list[dict[str, Any]]:
    """Collect report metadata from raw benchmark output folders."""
    reports: list[dict[str, Any]] = []
    for report_path in sorted(raw_dir.glob("*/report.json")):
        try:
            ignored = _is_git_ignored(report_path, repo_root=repo_root)
        except TypeError:
            # Compatibility for tests that monkeypatch the old one-arg helper.
            ignored = _is_git_ignored(report_path)
        if ignored:
            continue
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"{report_path}: invalid public benchmark report") from exc
        benchmark = str(report.get("benchmark") or "")
        reports.append(
            {
                "path": report_path,
                "dir": report_path.parent,
                "benchmark": benchmark,
                "display_benchmark": _display_benchmark_label(benchmark),
                "run": _run_label(report, report_path.parent),
                "generated_at": str(report.get("generated_at") or ""),
                "full": _is_full_report(report, benchmark),
                "scope": _scope_label(report, benchmark),
                "questions": _question_count(report),
                "snapshot": _snapshot_label(report),
                "worktree": _worktree_label(report),
                "evidence_status": _evidence_status(report, benchmark),
                "headline": _headline_metric(report),
            }
        )
    reports.sort(key=_report_sort_key)
    return reports


def _report_sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
    """Sort first-party rows before imported market baselines."""
    benchmark = str(item.get("benchmark") or "")
    if benchmark == "imported_market_baselines":
        return (2, benchmark, str(item.get("run") or ""))
    return (0, benchmark, str(item.get("run") or ""))


def _is_full_report(report: dict[str, Any], benchmark: str) -> bool:
    """Return whether a report represents its declared full public scope."""
    return (
        bool(report.get("is_full_filtered_run"))
        or bool(report.get("is_full_integration_run"))
        or bool(report.get("public_sanitized"))
        or benchmark == "imported_market_baselines"
    )


def _is_no_judge_retrieval_report(report: dict[str, Any], benchmark: str) -> bool:
    """Return whether a report is explicitly retrieval-only with no semantic judge."""
    if benchmark in LONGMEMEVAL_RETRIEVAL_BENCHMARKS:
        return True
    methodology = report.get("methodology")
    if not isinstance(methodology, dict):
        return False
    return (
        methodology.get("retrieval_only") is True
        and methodology.get("semantic_judge_in_loop") is False
    )


def _scope_label(report: dict[str, Any], benchmark: str) -> str:
    """Return a public scope label for the generated index."""
    if benchmark == "imported_market_baselines":
        return "pinned upstream retrieval-only"
    if benchmark == "longmemeval_s_trace_ingestion_cost_performance":
        return "sample"
    if _is_no_judge_retrieval_report(report, benchmark):
        return "retrieval-only"
    if benchmark in {
        "lerim_extraction_quality_minimax_m27_full_47",
        "lerim_false_positive_extraction_minimax_m27_negative_cases",
    }:
        return "diagnostic"
    return "full" if _is_full_report(report, benchmark) else "partial"


def _question_count(report: dict[str, Any]) -> Any:
    """Return a report question count when the schema has one."""
    dataset_count = (report.get("dataset") or {}).get("evaluated_entries", "")
    if dataset_count:
        return dataset_count
    trace_count = (report.get("dataset") or {}).get("evaluated_traces", "")
    if trace_count:
        return trace_count
    case_count = (report.get("dataset") or {}).get("cases", "")
    if case_count:
        return case_count
    negative_cases = (report.get("dataset") or {}).get("negative_cases", "")
    if negative_cases:
        return negative_cases
    results = report.get("results") or []
    if isinstance(results, list):
        counts = {
            result.get("questions")
            for result in results
            if isinstance(result, dict) and result.get("questions")
        }
        if len(counts) == 1:
            return counts.pop()
    return ""


def _display_benchmark_label(benchmark: str) -> str:
    """Return a user-facing benchmark label for generated report tables."""
    if benchmark == "imported_market_baselines":
        return "imported_market_baselines"
    return benchmark


def _markdown_report_filename(item: dict[str, Any]) -> str:
    """Return the generated Markdown filename for one report row."""
    if item["benchmark"] == "imported_market_baselines":
        return "imported-market-baselines.md"
    if item["display_benchmark"] != item["benchmark"]:
        return f"{item['display_benchmark'].replace('_', '-')}.md"
    return f"{item['dir'].name}.md"


def _snapshot_label(report: dict[str, Any]) -> str:
    """Return a dataset snapshot or pinned competitor commit label."""
    dataset_snapshot = (report.get("dataset") or {}).get("snapshot", "")
    if dataset_snapshot:
        return str(dataset_snapshot)
    agentmemory = report.get("agentmemory") or {}
    if isinstance(agentmemory, dict):
        return str(agentmemory.get("commit") or "")
    return ""


def _run_label(report: dict[str, Any], report_dir: Path) -> str:
    """Return the specific run/mode label for an artifact row."""
    mode = report.get("retrieval_mode")
    if mode:
        return str(mode)
    agentmemory = report.get("agentmemory") or {}
    if isinstance(agentmemory, dict):
        baseline_type = agentmemory.get("baseline_type")
        if baseline_type:
            return str(baseline_type)
    return report_dir.name


def _worktree_label(report: dict[str, Any]) -> str:
    """Return a compact worktree provenance label."""
    agentmemory = report.get("agentmemory") or {}
    if (
        isinstance(agentmemory, dict)
        and agentmemory.get("rerun_in_this_environment") is False
    ):
        return "imported"
    environment = report.get("environment") or {}
    if not isinstance(environment, dict):
        return "unknown"
    dirty = environment.get("git_dirty")
    if dirty is None:
        dirty = environment.get("lerim_git_dirty")
    if dirty is True:
        return "dirty"
    if dirty is False:
        return "clean"
    return "unknown"


def _evidence_status(report: dict[str, Any], benchmark: str) -> str:
    """Return a compact publication-status label for a report row."""
    if benchmark == "imported_market_baselines":
        return "imported; pinned upstream; not local rerun"
    if benchmark in {
        "lerim_extraction_quality_minimax_m27_full_47",
        "lerim_false_positive_extraction_minimax_m27_negative_cases",
    }:
        status = "diagnostic; aggregate-only; not launch-grade"
    elif benchmark == "longmemeval_s_trace_ingestion_cost_performance":
        status = "sample; live LLM calls; cost unavailable"
    elif _is_no_judge_retrieval_report(report, benchmark):
        status = "retrieval-only; not QA score"
    else:
        status = "public artifact"
    if _worktree_label(report) == "dirty":
        status = f"{status}; dirty provenance"
    return status


def _is_git_ignored(path: Path, *, repo_root: Path | None = None) -> bool:
    """Return whether git ignore rules exclude this path."""
    completed = subprocess.run(
        ["git", "check-ignore", "-q", str(path)],
        check=False,
        cwd=repo_root or Path.cwd(),
    )
    return completed.returncode == 0


def render_index(reports: list[dict[str, Any]], *, raw_dir: Path, reports_dir: Path) -> str:
    """Render benchmark report metadata as Markdown."""
    repo_root = reports_dir.parents[2] if len(reports_dir.parents) > 2 else Path.cwd()
    raw_dir_label = os.path.relpath(raw_dir, repo_root) if raw_dir.is_absolute() else str(raw_dir)
    lines = [
        "# Benchmark Reports",
        "",
        "Generated from public, non-ignored raw `report.json` artifacts. Do not edit benchmark numbers here by hand.",
        "",
        "Unpublished live-client artifacts should stay ignored; this index only includes public, non-ignored raw reports.",
        "",
        "| Benchmark | Run | Scope | Questions | Snapshot | Worktree | Evidence status | Headline | Raw report | Markdown |",
        "|---|---|---:|---:|---|---|---|---|---|---|",
    ]
    for item in reports:
        rel_json = Path(os.path.relpath(item["path"], reports_dir))
        source_md = item["dir"] / "report.md"
        rel_md = Path(_markdown_report_filename(item)) if source_md.exists() else ""
        snapshot = str(item["snapshot"] or "")[:12]
        lines.append(
            "| "
            f"{item['display_benchmark']} | `{item['run']}` | {item['scope']} | {item['questions']} | "
            f"`{snapshot}` | {item['worktree']} | {item['evidence_status']} | {item['headline']} | "
            f"[report.json]({rel_json}) | "
            f"{'[report.md](' + str(rel_md) + ')' if rel_md else ''} |"
        )
    if not reports:
        lines.append("| No reports yet | | | | | | | | | |")
    lines.extend(["", f"Raw directory: `{raw_dir_label}`", ""])
    return "\n".join(lines)


def build_index(raw_dir: Path, reports_dir: Path) -> Path:
    """Build the report index and copy generated Markdown reports."""
    raw_dir = raw_dir.expanduser().resolve()
    reports_dir = reports_dir.expanduser().resolve()
    reports_dir.mkdir(parents=True, exist_ok=True)
    reports = collect_reports(raw_dir, repo_root=reports_dir.parents[2])
    for item in reports:
        source_md = item["dir"] / "report.md"
        if source_md.exists():
            target_md = reports_dir / _markdown_report_filename(item)
            shutil.copyfile(source_md, target_md)
    index_path = reports_dir / "index.md"
    index_path.write_text(
        render_index(reports, raw_dir=raw_dir, reports_dir=reports_dir),
        encoding="utf-8",
    )
    return index_path


def parse_args() -> argparse.Namespace:
    """Parse report-index CLI arguments."""
    parser = argparse.ArgumentParser(description="Build benchmark report index.")
    parser.add_argument("--raw-dir", type=Path, default=Path("benchmarks/results/raw"))
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=Path("benchmarks/results/reports"),
    )
    return parser.parse_args()


def main() -> None:
    """Build the report index from the command line."""
    args = parse_args()
    index_path = build_index(args.raw_dir, args.reports_dir)
    print(f"Benchmark report index written to {index_path}")


if __name__ == "__main__":
    main()
