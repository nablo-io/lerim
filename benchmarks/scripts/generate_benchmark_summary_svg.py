"""Generate the public benchmark summary SVG from raw benchmark artifacts."""

from __future__ import annotations

import argparse
import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT = Path("docs/assets/benchmark-summary.svg")


@dataclass(frozen=True)
class BenchmarkSnapshot:
    """Public benchmark metrics used by the README summary image."""

    question_count: int
    recall_at_5: float
    recall_at_10: float
    recall_at_20: float
    context_reduction: float
    context_recall: float
    latency_record_count: str
    latency_p50_ms: float
    latency_p99_ms: float
    mcp_config_passed: int
    mcp_config_total: int
    gemini_live_tool_calls: int
    trace_submit_extraction_acceptances: int
    dirty_worktree: bool


def _load_report(raw_dir: Path, name: str) -> dict[str, Any]:
    """Load one raw public benchmark report by artifact directory name."""
    path = raw_dir / name / "report.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _dirty(report: dict[str, Any]) -> bool:
    """Return whether report provenance came from a dirty worktree."""
    environment = report.get("environment")
    if not isinstance(environment, dict):
        return False
    dirty = environment.get("git_dirty")
    if dirty is None:
        dirty = environment.get("lerim_git_dirty")
    return dirty is True


def _ratio_pct(value: float) -> float:
    """Convert a ratio metric into percent."""
    return float(value) * 100.0


def _fmt_pct_one(value: float) -> str:
    """Format a percentage with one decimal place."""
    return f"{float(value):.1f}%"


def load_snapshot(raw_dir: Path) -> BenchmarkSnapshot:
    """Build the README benchmark snapshot from raw report.json files."""
    retrieval = _load_report(raw_dir, "longmemeval-hybrid-full")
    budget = _load_report(raw_dir, "context-budget-hybrid-full")
    latency = _load_report(raw_dir, "retrieval-latency-longmemeval")
    integration = _load_report(raw_dir, "mcp-integration-full")
    gemini = _load_report(raw_dir, "mcp-gemini-live-tool-call")

    retrieval_headline = retrieval["results"]["headline"]
    budget_top_10 = budget["results"]["headline"]["top_10"]
    latency_1k = latency["results"]["1000"]
    integration_summary = integration["summary"]
    gemini_summary = gemini["summary"]

    reports = [
        retrieval,
        budget,
        latency,
        integration,
        gemini,
    ]
    return BenchmarkSnapshot(
        question_count=int(retrieval_headline["count"]),
        recall_at_5=_ratio_pct(retrieval_headline["recall_any_at_5"]),
        recall_at_10=_ratio_pct(retrieval_headline["recall_any_at_10"]),
        recall_at_20=_ratio_pct(retrieval_headline["recall_any_at_20"]),
        context_reduction=_ratio_pct(budget_top_10["avg_reduction_ratio"]),
        context_recall=_ratio_pct(budget_top_10["recall_any"]),
        latency_record_count="1,000",
        latency_p50_ms=float(latency_1k["p50_ms"]),
        latency_p99_ms=float(latency_1k["p99_ms"]),
        mcp_config_passed=int(integration_summary["config_passed_count"]),
        mcp_config_total=int(integration_summary["known_target_count"]),
        gemini_live_tool_calls=int(
            gemini_summary["installed_client_tool_call_acceptance_count"]
        ),
        trace_submit_extraction_acceptances=int(
            integration_summary["trace_submit_extraction_acceptance_count"]
        ),
        dirty_worktree=any(_dirty(report) for report in reports),
    )


def _bar(
    width_pct: float,
    *,
    x: int,
    y: int,
    max_width: int,
    fill: str = "#28d4c3",
) -> str:
    """Render a rounded SVG bar for a percent value."""
    width = round(max(0.0, min(100.0, width_pct)) / 100.0 * max_width)
    return (
        f'<rect x="{x}" y="{y}" width="{width}" height="26" rx="7" '
        f'fill="{fill}"/>'
    )


def build_svg(snapshot: BenchmarkSnapshot) -> str:
    """Render the benchmark snapshot SVG."""
    subtitle = (
        "Raw artifacts; dirty tree. Rerun clean before launch-grade claims."
        if snapshot.dirty_worktree
        else "Raw artifacts from a clean release candidate."
    )
    recall_5 = _fmt_pct_one(snapshot.recall_at_5)
    recall_10 = _fmt_pct_one(snapshot.recall_at_10)
    recall_20 = _fmt_pct_one(snapshot.recall_at_20)
    reduction = _fmt_pct_one(snapshot.context_reduction)
    context_recall = _fmt_pct_one(snapshot.context_recall)
    p50 = f"{snapshot.latency_p50_ms:.1f} ms"
    p99 = f"{snapshot.latency_p99_ms:.1f} ms"

    return "\n".join(
        [
            '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="760" '
            'viewBox="0 0 1200 760" role="img" aria-labelledby="title desc">',
            '  <title id="title">Lerim benchmark snapshot from raw artifacts</title>',
            (
                '  <desc id="desc">LongMemEval-S retrieval, context budget, latency, '
                'and MCP integration evidence.</desc>'
            ),
            '  <rect width="1200" height="760" fill="#070a0f"/>',
            '  <rect x="40" y="36" width="1120" height="688" rx="18" fill="#101720" stroke="#223044" stroke-width="2"/>',
            '  <text x="78" y="94" font-family="Arial, Helvetica, sans-serif" font-size="36" font-weight="700" fill="#f4f8fb">Lerim benchmark snapshot</text>',
            f'  <text x="78" y="132" font-family="Arial, Helvetica, sans-serif" font-size="18" fill="#9ba8b8">{html.escape(subtitle)}</text>',
            "",
            '  <g transform="translate(78 176)">',
            '    <rect width="500" height="236" rx="12" fill="#0c1119" stroke="#223044" stroke-width="2"/>',
            '    <text x="26" y="42" font-family="Arial, Helvetica, sans-serif" font-size="24" font-weight="700" fill="#f4f8fb">LongMemEval-S retrieval-only</text>',
            f'    <text x="26" y="74" font-family="Arial, Helvetica, sans-serif" font-size="17" fill="#9ba8b8">Hybrid search over {snapshot.question_count} questions.</text>',
            f"    {_bar(snapshot.recall_at_5, x=26, y=104, max_width=320)}",
            f'    <text x="366" y="124" font-family="Arial, Helvetica, sans-serif" font-size="21" font-weight="700" fill="#f4f8fb">R@5 {recall_5}</text>',
            f"    {_bar(snapshot.recall_at_10, x=26, y=148, max_width=320)}",
            f'    <text x="366" y="168" font-family="Arial, Helvetica, sans-serif" font-size="21" font-weight="700" fill="#f4f8fb">R@10 {recall_10}</text>',
            f"    {_bar(snapshot.recall_at_20, x=26, y=192, max_width=320)}",
            f'    <text x="366" y="212" font-family="Arial, Helvetica, sans-serif" font-size="21" font-weight="700" fill="#f4f8fb">R@20 {recall_20}</text>',
            '    <text x="26" y="232" font-family="Arial, Helvetica, sans-serif" font-size="11" fill="#768396">Source: longmemeval-hybrid-full/report.json</text>',
            "  </g>",
            "",
            '  <g transform="translate(606 176)">',
            '    <rect width="516" height="236" rx="12" fill="#0c1119" stroke="#223044" stroke-width="2"/>',
            '    <text x="26" y="42" font-family="Arial, Helvetica, sans-serif" font-size="24" font-weight="700" fill="#f4f8fb">Context budget</text>',
            '    <text x="26" y="74" font-family="Arial, Helvetica, sans-serif" font-size="17" fill="#9ba8b8">Top-10 sessions, not full haystack replay.</text>',
            f"    {_bar(snapshot.context_reduction, x=26, y=112, max_width=330, fill='#4f8cff')}",
            f'    <text x="378" y="135" font-family="Arial, Helvetica, sans-serif" font-size="30" font-weight="700" fill="#f4f8fb">{reduction}</text>',
            f'    <text x="26" y="184" font-family="Arial, Helvetica, sans-serif" font-size="18" fill="#9ba8b8">Token reduction with {context_recall} recall.</text>',
            '    <text x="26" y="212" font-family="Arial, Helvetica, sans-serif" font-size="15" fill="#768396">Source: context-budget-hybrid-full/report.json</text>',
            "  </g>",
            "",
            '  <g transform="translate(78 440)">',
            '    <rect width="322" height="156" rx="12" fill="#0c1119" stroke="#223044" stroke-width="2"/>',
            '    <text x="24" y="40" font-family="Arial, Helvetica, sans-serif" font-size="23" font-weight="700" fill="#f4f8fb">Retrieval latency</text>',
            f'    <text x="24" y="82" font-family="Arial, Helvetica, sans-serif" font-size="20" fill="#9ba8b8">{snapshot.latency_record_count} records, local search path</text>',
            f'    <text x="24" y="124" font-family="Arial, Helvetica, sans-serif" font-size="27" font-weight="700" fill="#f4f8fb">p50 {p50}</text>',
            f'    <text x="216" y="124" font-family="Arial, Helvetica, sans-serif" font-size="18" fill="#9ba8b8">p99 {p99}</text>',
            '    <text x="24" y="146" font-family="Arial, Helvetica, sans-serif" font-size="13" fill="#768396">Source: retrieval-latency-longmemeval/report.json</text>',
            "  </g>",
            "",
            '  <g transform="translate(426 440)">',
            '    <rect width="696" height="156" rx="12" fill="#0c1119" stroke="#223044" stroke-width="2"/>',
            '    <text x="24" y="40" font-family="Arial, Helvetica, sans-serif" font-size="23" font-weight="700" fill="#f4f8fb">MCP integration</text>',
            f'    <text x="24" y="88" font-family="Arial, Helvetica, sans-serif" font-size="31" font-weight="700" fill="#f4f8fb">{snapshot.mcp_config_passed}/{snapshot.mcp_config_total}</text>',
            '    <text x="128" y="88" font-family="Arial, Helvetica, sans-serif" font-size="18" fill="#9ba8b8">config probes passed.</text>',
            f'    <text x="344" y="88" font-family="Arial, Helvetica, sans-serif" font-size="18" fill="#9ba8b8">Gemini live tool-call: {snapshot.gemini_live_tool_calls}</text>',
            f'    <text x="24" y="124" font-family="Arial, Helvetica, sans-serif" font-size="17" fill="#9ba8b8">Synthetic trace-submit extraction: {snapshot.trace_submit_extraction_acceptances}</text>',
            '    <text x="24" y="146" font-family="Arial, Helvetica, sans-serif" font-size="13" fill="#768396">Sources: mcp-integration-full + mcp-gemini-live-tool-call</text>',
            "  </g>",
            "",
            '  <line x1="78" y1="642" x2="1122" y2="642" stroke="#223044" stroke-width="2"/>',
            '  <text x="78" y="674" font-family="Arial, Helvetica, sans-serif" font-size="15" fill="#768396">Artifacts: retrieval, context budget, latency, MCP config/protocol checks, and Gemini live tool-call.</text>',
            '  <text x="78" y="700" font-family="Arial, Helvetica, sans-serif" font-size="15" fill="#768396">Use as engineering evidence until clean-release benchmark validation passes.</text>',
            "</svg>",
            "",
        ]
    )


def generate(*, repo_root: Path, output: Path = DEFAULT_OUTPUT) -> Path:
    """Generate the benchmark summary SVG and return the output path."""
    repo_root = repo_root.resolve()
    raw_dir = repo_root / "benchmarks" / "results" / "raw"
    output_path = output if output.is_absolute() else repo_root / output
    snapshot = load_snapshot(raw_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_svg(snapshot), encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Generate docs/assets/benchmark-summary.svg from raw artifacts."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    """Generate the benchmark summary SVG from the command line."""
    args = parse_args()
    output_path = generate(repo_root=args.repo_root, output=args.output)
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
