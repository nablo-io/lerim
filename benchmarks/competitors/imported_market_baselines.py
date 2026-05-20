"""Import pinned upstream market benchmark artifacts for comparison.

The current imported source is AgentMemory. This runner does not rerun that
system. It downloads the upstream raw benchmark JSON files at an explicit git
commit, stores them as source artifacts, and emits a normalized report that can
be compared with Lerim-first reports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


AGENTMEMORY_REPO = "https://github.com/rohitg00/agentmemory"
DEFAULT_AGENTMEMORY_COMMIT = "68fddd418e1bbcc41d32a1c61b7a78d91eb7c4dc"
DEFAULT_SOURCE_FILES: tuple[str, ...] = (
    "benchmark/data/longmemeval_results_hybrid.json",
    "benchmark/data/longmemeval_results_bm25.json",
    "benchmark/results/load-100k-96c0ed0.json",
)
LERIM_HYBRID_REPORT = Path("benchmarks/results/raw/longmemeval-hybrid-full/report.json")
LERIM_LEXICAL_REPORT = Path("benchmarks/results/raw/longmemeval-lexical-full/report.json")
LERIM_LATENCY_REPORT = Path("benchmarks/results/raw/retrieval-latency-longmemeval/report.json")


def utc_now() -> str:
    """Return a UTC timestamp for report metadata."""
    return datetime.now(timezone.utc).isoformat()


def git_value(args: list[str]) -> str | None:
    """Read one git value from the local Lerim checkout."""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip() or None


def public_git_status(git_status: str | None) -> str:
    """Return a public-safe git status label for report metadata."""
    if not git_status:
        return ""
    return "<dirty worktree; rerun from clean commit before launch>"


def source_url(commit: str, path: str) -> str:
    """Build a raw GitHub URL for one AgentMemory source artifact."""
    return f"https://raw.githubusercontent.com/rohitg00/agentmemory/{commit}/{path}"


def sha256_text(text: str) -> str:
    """Return the SHA-256 digest for UTF-8 text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fetch_source_text(*, commit: str, path: str, timeout_seconds: float) -> str:
    """Fetch one AgentMemory source artifact from raw GitHub."""
    url = source_url(commit, path)
    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            raw = response.read()
    except URLError as exc:
        raise RuntimeError(f"agentmemory_source_fetch_failed:{path}:{exc}") from exc
    return raw.decode("utf-8")


def parse_json_source(*, path: str, text: str) -> dict[str, Any]:
    """Parse one JSON source artifact as an object."""
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError(f"agentmemory_source_must_be_object:{path}")
    return payload


def normalize_longmemeval_result(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize AgentMemory LongMemEval result JSON."""
    required = (
        "mode",
        "questions",
        "recall_any_at_5",
        "recall_any_at_10",
        "recall_any_at_20",
        "ndcg_at_10",
        "mrr",
    )
    missing = [field for field in required if field not in payload]
    if missing:
        raise ValueError(f"agentmemory_longmemeval_missing_fields:{path}:{','.join(missing)}")
    return {
        "kind": "longmemeval_s_retrieval_only",
        "source_path": path,
        "mode": str(payload["mode"]),
        "questions": int(payload["questions"]),
        "headline": {
            "recall_any_at_5": float(payload["recall_any_at_5"]),
            "recall_any_at_10": float(payload["recall_any_at_10"]),
            "recall_any_at_20": float(payload["recall_any_at_20"]),
            "ndcg_at_10": float(payload["ndcg_at_10"]),
            "mrr": float(payload["mrr"]),
        },
        "per_type": payload.get("per_type") if isinstance(payload.get("per_type"), dict) else {},
        "per_question_count": len(payload.get("per_question") or []),
    }


def normalize_load_result(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize AgentMemory load benchmark result JSON."""
    cells = payload.get("cells")
    if not isinstance(cells, list):
        raise ValueError(f"agentmemory_load_missing_cells:{path}")
    normalized_cells: list[dict[str, Any]] = []
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict):
            raise ValueError(f"agentmemory_load_cell_must_be_object:{path}:{index}")
        normalized_cells.append(
            {
                "endpoint": str(cell.get("endpoint") or ""),
                "N": int(cell.get("N") or 0),
                "C": int(cell.get("C") or 0),
                "ops": int(cell.get("ops") or 0),
                "errors": int(cell.get("errors") or 0),
                "throughput_per_sec": float(cell.get("throughput_per_sec") or 0.0),
                "p50_ms": float(cell.get("p50_ms") or 0.0),
                "p90_ms": float(cell.get("p90_ms") or 0.0),
                "p99_ms": float(cell.get("p99_ms") or 0.0),
            }
        )
    return {
        "kind": "agentmemory_http_load",
        "source_path": path,
        "generated_at": str(payload.get("generated_at") or ""),
        "git_sha": str(payload.get("git_sha") or ""),
        "matrix": payload.get("matrix") if isinstance(payload.get("matrix"), dict) else {},
        "ops_per_cell": payload.get("ops_per_cell"),
        "cells": normalized_cells,
        "notes": str(payload.get("notes") or ""),
        "comparison_boundary": (
            "AgentMemory load cells are HTTP endpoint measurements; Lerim's current "
            "latency artifact is local ContextStore.search, so they are not direct "
            "apples-to-apples latency comparisons."
        ),
    }


def normalize_source(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize one AgentMemory source artifact by path and shape."""
    if "longmemeval_results" in path:
        return normalize_longmemeval_result(path, payload)
    if "load-" in path:
        return normalize_load_result(path, payload)
    raise ValueError(f"unsupported_agentmemory_source:{path}")


def load_json_file(path: Path) -> dict[str, Any] | None:
    """Load one local JSON report if it exists."""
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def lerim_headline(report: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return Lerim headline metrics from a benchmark report."""
    if not report:
        return None
    results = report.get("results")
    if not isinstance(results, dict):
        return None
    headline = results.get("headline")
    return headline if isinstance(headline, dict) else None


def metric_delta_pp(lerim_value: Any, agentmemory_value: Any) -> float | None:
    """Return percentage-point delta for two ratio metrics."""
    try:
        return (float(lerim_value) - float(agentmemory_value)) * 100.0
    except (TypeError, ValueError):
        return None


def build_longmemeval_comparisons(
    normalized_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build provenance rows for the market-wide LongMemEval table."""
    lerim_reports = {
        "hybrid": load_json_file(LERIM_HYBRID_REPORT),
        "bm25": load_json_file(LERIM_LEXICAL_REPORT),
    }
    comparisons: list[dict[str, Any]] = []
    for result in normalized_results:
        if result.get("kind") != "longmemeval_s_retrieval_only":
            continue
        mode = str(result.get("mode") or "")
        lerim_mode = "hybrid" if mode == "hybrid" else "bm25" if mode == "bm25" else ""
        lerim_report = lerim_reports.get(lerim_mode)
        comparisons.append(
            {
                "surface": "longmemeval_s_retrieval_only",
                "agentmemory_mode": mode,
                "lerim_report": str(
                    LERIM_HYBRID_REPORT if mode == "hybrid" else LERIM_LEXICAL_REPORT
                ),
                "lerim_available": lerim_report is not None,
                "agentmemory_questions": result["questions"],
                "lerim_questions": (lerim_report.get("dataset") or {}).get("evaluated_entries")
                if lerim_report
                else None,
                "comparison_status": (
                    "pinned_upstream_competitor_row_for_market_table"
                    if lerim_report
                    else "pinned_competitor_lerim_missing"
                ),
                "warning": (
                    "The competitor was not rerun in this environment. Treat this as a "
                    "pinned upstream market row, not a fresh competitor rerun."
                ),
            }
        )
    return comparisons


def build_latency_boundary(normalized_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a boundary note for AgentMemory load and Lerim latency artifacts."""
    agentmemory_load = next(
        (result for result in normalized_results if result.get("kind") == "agentmemory_http_load"),
        None,
    )
    lerim_latency = load_json_file(LERIM_LATENCY_REPORT)
    return {
        "surface": "retrieval_latency",
        "agentmemory_artifact_available": agentmemory_load is not None,
        "lerim_artifact_available": lerim_latency is not None,
        "comparison_status": "not_directly_comparable",
        "agentmemory_measure": "HTTP endpoint load harness",
        "lerim_measure": "local ContextStore.search latency",
        "warning": (
            "Do not publish latency winner claims from these two artifacts until both "
            "products are measured through the same boundary."
        ),
    }


def fetch_and_write_sources(
    *,
    commit: str,
    source_files: tuple[str, ...],
    output_dir: Path,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    """Fetch AgentMemory source files, persist them, and return metadata."""
    source_dir = output_dir / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for path in source_files:
        text = fetch_source_text(
            commit=commit,
            path=path,
            timeout_seconds=timeout_seconds,
        )
        target = source_dir / Path(path).name
        stored_text = text if text.endswith("\n") else f"{text}\n"
        target.write_text(stored_text, encoding="utf-8")
        payload = parse_json_source(path=path, text=text)
        normalized = normalize_source(path, payload)
        rows.append(
            {
                "source_path": path,
                "raw_url": source_url(commit, path),
                "local_path": str(target.relative_to(output_dir)),
                "sha256": sha256_text(stored_text),
                "normalized": normalized,
            }
        )
    return rows


def build_report(
    *,
    commit: str,
    source_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the normalized imported market-baseline report."""
    normalized_results = [row["normalized"] for row in source_rows]
    git_status = git_value(["status", "--short"])
    return {
        "schema_version": 1,
        "benchmark": "imported_market_baselines",
        "generated_at": utc_now(),
        "command": " ".join(sys.argv),
        "agentmemory": {
            "repo": AGENTMEMORY_REPO,
            "commit": commit,
            "rerun_in_this_environment": False,
            "baseline_type": "pinned_upstream_raw_artifacts",
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "machine": platform.machine(),
            "lerim_git_commit": git_value(["rev-parse", "HEAD"]),
            "lerim_git_dirty": bool(git_status),
            "lerim_git_status_short": public_git_status(git_status),
        },
        "source_artifacts": [
            {
                "source_path": row["source_path"],
                "raw_url": row["raw_url"],
                "local_path": row["local_path"],
                "sha256": row["sha256"],
            }
            for row in source_rows
        ],
        "results": normalized_results,
        "comparisons": {
            "longmemeval": build_longmemeval_comparisons(normalized_results),
            "latency": build_latency_boundary(normalized_results),
        },
        "publication_rules": [
            "Say pinned upstream competitor artifact, not fresh local competitor rerun.",
            "Do not compare latency winner claims across HTTP and local-store boundaries.",
            "Do not claim Lerim beats a competitor until the benchmark boundary and provenance are visible.",
        ],
    }


def format_percent(value: Any) -> str:
    """Format a ratio as a percentage string."""
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def format_pp(value: Any) -> str:
    """Format a percentage-point delta."""
    if value is None:
        return "n/a"
    return f"{float(value):+.1f} pp"


def render_markdown(report: dict[str, Any]) -> str:
    """Render the pinned market baseline as Markdown."""
    lines = [
        "# Imported Market Baselines",
        "",
        "This generated audit artifact supports the market-wide comparison page. "
        "It is not a standalone competitor comparison or win/loss claim.",
        "",
        "Current imported source rows cover the pinned upstream artifacts listed "
        "below. Add more market systems here only when their source artifacts "
        "and provenance are pinned with the same care.",
        "",
        f"- Generated: `{report['generated_at']}`",
        "- Source system: `AgentMemory`",
        f"- Source repository: `{report['agentmemory']['repo']}`",
        f"- Source commit: `{report['agentmemory']['commit']}`",
        f"- Baseline type: `{report['agentmemory']['baseline_type']}`",
        f"- Rerun in this environment: `{report['agentmemory']['rerun_in_this_environment']}`",
        "",
        "## Source Artifacts",
        "",
        "| System | Source | SHA-256 |",
        "|---|---|---|",
    ]
    for source in report["source_artifacts"]:
        lines.append(
            f"| AgentMemory | `{source['source_path']}` | `{source['sha256']}` |"
        )

    lines.extend(["", "## LongMemEval-S Retrieval", ""])
    lines.append("| System | Mode | Questions | R@5 | R@10 | R@20 | NDCG@10 | MRR |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for result in report["results"]:
        if result.get("kind") != "longmemeval_s_retrieval_only":
            continue
        headline = result["headline"]
        lines.append(
            "| "
            f"AgentMemory | {result['mode']} | {result['questions']} | "
            f"{format_percent(headline['recall_any_at_5'])} | "
            f"{format_percent(headline['recall_any_at_10'])} | "
            f"{format_percent(headline['recall_any_at_20'])} | "
            f"{format_percent(headline['ndcg_at_10'])} | "
            f"{format_percent(headline['mrr'])} |"
        )

    lines.extend(["", "## Market Table Usage", ""])
    lines.append("| System | Mode | Status | Lerim artifact available | Warning |")
    lines.append("|---|---|---|---:|---|")
    for comparison in report["comparisons"]["longmemeval"]:
        lines.append(
            "| "
            "AgentMemory | "
            f"{comparison['agentmemory_mode']} | `{comparison['comparison_status']}` | "
            f"{comparison['lerim_available']} | "
            f"{comparison['warning']} |"
        )

    lines.extend(["", "## Imported Load Artifact", ""])
    for result in report["results"]:
        if result.get("kind") != "agentmemory_http_load":
            continue
        lines.append("| System | Endpoint | N | C | Ops | Errors | p50 | p90 | p99 |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
        for cell in result["cells"]:
            lines.append(
                "| "
                f"AgentMemory | {cell['endpoint']} | {cell['N']} | {cell['C']} | "
                f"{cell['ops']} | {cell['errors']} | "
                f"{cell['p50_ms']:.1f} ms | {cell['p90_ms']:.1f} ms | "
                f"{cell['p99_ms']:.1f} ms |"
            )
        lines.append("")
        lines.append(result["comparison_boundary"])

    lines.extend(["", "## Publication Rules", ""])
    for rule in report["publication_rules"]:
        lines.append(f"- {rule}")
    lines.append("")
    return "\n".join(lines)


def write_outputs(report: dict[str, Any], output_dir: Path) -> None:
    """Write normalized report artifacts."""
    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")


def run(args: argparse.Namespace) -> Path:
    """Fetch, normalize, and write imported market-baseline artifacts."""
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source_rows = fetch_and_write_sources(
        commit=args.agentmemory_commit,
        source_files=tuple(args.source_files or DEFAULT_SOURCE_FILES),
        output_dir=output_dir,
        timeout_seconds=args.timeout_seconds,
    )
    report = build_report(commit=args.agentmemory_commit, source_rows=source_rows)
    write_outputs(report, output_dir)
    return output_dir


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the imported market-baseline runner."""
    parser = argparse.ArgumentParser(
        description="Pin upstream market benchmark artifacts.",
    )
    parser.add_argument(
        "--agentmemory-commit",
        default=DEFAULT_AGENTMEMORY_COMMIT,
        help="AgentMemory git commit to fetch raw benchmark artifacts from.",
    )
    parser.add_argument(
        "--source-files",
        nargs="*",
        default=list(DEFAULT_SOURCE_FILES),
        help="AgentMemory source artifact paths to fetch.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmarks/results/raw/imported-market-baselines"),
    )
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    return parser.parse_args()


def main() -> None:
    """Run the imported market-baseline CLI."""
    output_dir = run(parse_args())
    print(f"Imported market baseline report written to {output_dir}")


if __name__ == "__main__":
    main()
