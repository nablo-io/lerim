"""Validate public benchmark artifacts and benchmark docs.

This is a release guard for benchmark provenance. It does not judge whether a
number is good; it checks whether public claims point back to real artifacts and
whether private/local-only material stayed out of public docs.
"""

from __future__ import annotations

import argparse
import math
import hashlib
import json
import re
import shlex
import subprocess
import sys
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


PUBLIC_TEXT_GLOBS = ("*.md", "*.json", "*.jsonl", "*.svg", "*.toml", "*.yaml", "*.yml")
PUBLIC_TEXT_ROOT_NAMES = (
    "README.md",
    "COMMERCIAL.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "assets",
    "benchmarks/README.md",
    "benchmarks/results/README.md",
    "benchmarks/results/market-sources.json",
    "benchmarks/results/public-reports.json",
    "docs",
    "benchmarks/results/raw",
    "benchmarks/results/reports",
    "src",
    "tests",
)
RELEASE_TRACKING_ROOT_NAMES = (
    ".dockerignore",
    ".github/workflows",
    ".gitignore",
    ".pre-commit-config.yaml",
    ".python-version",
    "assets",
    "benchmarks",
    "CHANGELOG.md",
    "COMMERCIAL.md",
    "CONTRIBUTING.md",
    "Dockerfile",
    "docs",
    "LICENSE",
    "mkdocs.yml",
    "pyproject.toml",
    "README.md",
    "scripts/release_preflight.py",
    "src",
    "tests",
    "uv.lock",
    "vulture_whitelist.py",
)
BENCHMARK_SOURCE_ROOT_NAMES = (
    "benchmarks/competitors",
    "benchmarks/lerim_evidence",
    "benchmarks/scripts",
    "pyproject.toml",
    "src",
    "uv.lock",
)

MARKET_COMPARISON_H2 = frozenset(
    {
        "Where To Look",
        "Current Market Snapshot",
        "LongMemEval-S Retrieval",
        "Extraction Comparison Status",
        "Not-Yet-Comparable Rows",
        "Next Normalization Work",
        "Sources",
    }
)
EXTERNAL_MARKET_SYSTEMS = frozenset(
    {
        "AgentMemory",
        "MemPalace",
        "Mem0",
        "Letta",
        "Cognee",
        "Zep / Graphiti",
        "Supermemory",
        "Khoj / claude-mem / Hippo / other systems",
    }
)

LONGMEMEVAL_RETRIEVAL_BENCHMARKS = frozenset(
    {
        "longmemeval_s_retrieval_only",
        "longmemeval_s_context_budget",
        "longmemeval_s_retrieval_latency",
    }
)
TRACE_INGESTION_COST_BENCHMARKS = frozenset(
    {
        "longmemeval_s_trace_ingestion_cost_performance",
    }
)

EXTRACTION_BENCHMARKS = frozenset(
    {
        "lerim_extraction_quality_minimax_m27_full_47",
        "lerim_false_positive_extraction_minimax_m27_negative_cases",
    }
)

LOCAL_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])("
    r"/(?:Users|home|private/var/folders|var/folders)/[^\s`'\"<>)]+"
    r"|[A-Za-z]:\\Users\\[^\s`'\"<>)]+"
    r")"
)


@dataclass(frozen=True)
class ValidationResult:
    """Validation result with machine-friendly errors."""

    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """Return whether validation passed."""
        return not self.errors


def _is_git_ignored(path: Path, *, cwd: Path) -> bool:
    """Return whether git ignore rules exclude this path."""
    completed = subprocess.run(
        ["git", "check-ignore", "-q", str(path)],
        check=False,
        cwd=cwd,
    )
    return completed.returncode == 0


def _is_git_tracked(path: Path, *, cwd: Path) -> bool:
    """Return whether git already tracks this release-critical file."""
    try:
        rel_path = path.resolve().relative_to(cwd.resolve())
    except ValueError:
        rel_path = path
    completed = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", str(rel_path)],
        check=False,
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def _git_head(repo_root: Path) -> str | None:
    """Return the current git HEAD commit when repo_root is a git checkout."""
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=False,
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if completed.returncode != 0:
        return None
    head = completed.stdout.strip()
    return head or None


def _git_source_changes_since(
    repo_root: Path,
    *,
    commit: str,
    current_head: str,
) -> tuple[str, ...] | None:
    """Return benchmark-source files changed between a report commit and HEAD."""
    completed = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            f"{commit}..{current_head}",
            "--",
            *BENCHMARK_SOURCE_ROOT_NAMES,
        ],
        check=False,
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if completed.returncode != 0:
        return None
    return tuple(line for line in completed.stdout.splitlines() if line.strip())


def _load_report(path: Path, errors: list[str]) -> dict[str, Any] | None:
    """Load one benchmark report, recording parse errors."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        errors.append(f"{path}: could not read report.json: {exc}")
        return None
    except json.JSONDecodeError as exc:
        errors.append(f"{path}: invalid JSON: {exc}")
        return None
    if not isinstance(payload, dict):
        errors.append(f"{path}: report.json must contain a JSON object")
        return None
    return payload


def _has_any_key(payload: dict[str, Any], keys: tuple[str, ...]) -> bool:
    """Return whether a dict has at least one meaningful key value."""
    return any(payload.get(key) not in (None, "") for key in keys)


def _validate_report_metadata(report_path: Path, report: dict[str, Any], errors: list[str]) -> None:
    """Validate metadata common to every public raw benchmark report."""
    for key in ("benchmark", "generated_at", "command"):
        if not report.get(key):
            errors.append(f"{report_path}: missing required `{key}`")

    environment = report.get("environment")
    if not isinstance(environment, dict):
        errors.append(f"{report_path}: missing required `environment` object")
        return

    if not _has_any_key(environment, ("git_commit", "lerim_git_commit")):
        errors.append(f"{report_path}: environment must include git commit provenance")
    elif _report_git_commit(report) is None:
        errors.append(f"{report_path}: git commit provenance must be a non-empty string")

    if not _has_any_key(environment, ("git_dirty", "lerim_git_dirty")):
        errors.append(f"{report_path}: environment must include git dirty provenance")
    elif _report_git_dirty(report) is None:
        errors.append(f"{report_path}: git dirty provenance must be a boolean")


def _command_arg(command: str, flag: str) -> str | None:
    """Return one command-line flag value from a stored report command."""
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    for index, part in enumerate(parts):
        if part == flag and index + 1 < len(parts):
            return parts[index + 1]
        prefix = f"{flag}="
        if part.startswith(prefix):
            return part[len(prefix) :]
    return None


def _sha256_file(path: Path) -> str | None:
    """Return a file SHA-256 digest, or None when the file cannot be read."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _validate_clean_worktree(
    report_path: Path,
    report: dict[str, Any],
    errors: list[str],
    *,
    current_head: str | None,
) -> None:
    """Validate clean-worktree provenance for launch-grade publication."""
    dirty = _report_git_dirty(report)
    if dirty is not False:
        errors.append(f"{report_path}: launch-grade validation requires git_dirty=false")
    commit = _report_git_commit(report)
    if current_head and commit and commit != current_head:
        changed_sources = _git_source_changes_since(
            report_path.parents[4],
            commit=commit,
            current_head=current_head,
        )
        if changed_sources is None:
            errors.append(f"{report_path}: could not compare benchmark source changes since {commit}")
        elif changed_sources:
            preview = ", ".join(changed_sources[:5])
            if len(changed_sources) > 5:
                preview = f"{preview}, ..."
            errors.append(
                f"{report_path}: benchmark source changed since report commit {commit}: {preview}"
            )


def _is_imported_market_baseline(report: dict[str, Any]) -> bool:
    """Return whether a report is a source-imported market baseline."""
    baseline = report.get("agentmemory")
    return (
        report.get("benchmark") == "imported_market_baselines"
        and isinstance(baseline, dict)
        and baseline.get("rerun_in_this_environment") is False
    )


def _report_git_commit(report: dict[str, Any]) -> str | None:
    """Return the git commit provenance from a raw report."""
    environment = report.get("environment")
    if not isinstance(environment, dict):
        return None
    commit = environment.get("git_commit")
    if commit is None:
        commit = environment.get("lerim_git_commit")
    return commit if isinstance(commit, str) and commit else None


def _report_git_dirty(report: dict[str, Any]) -> bool | None:
    """Return the git dirty provenance flag from a raw report."""
    environment = report.get("environment")
    if not isinstance(environment, dict):
        return None
    dirty = environment.get("git_dirty")
    if dirty is None:
        dirty = environment.get("lerim_git_dirty")
    return dirty if isinstance(dirty, bool) else None


def _methodology(report: dict[str, Any]) -> dict[str, Any]:
    """Return report methodology metadata when present."""
    methodology = report.get("methodology")
    return methodology if isinstance(methodology, dict) else {}


def _validate_no_judge_boundaries(
    report_path: Path,
    report: dict[str, Any],
    errors: list[str],
) -> None:
    """Validate that no-judge retrieval artifacts cannot look semantically passing."""
    benchmark = str(report.get("benchmark") or "")
    methodology = _methodology(report)
    no_semantic_judge = methodology.get("semantic_judge_in_loop") is False

    if benchmark in LONGMEMEVAL_RETRIEVAL_BENCHMARKS:
        if not methodology:
            errors.append(f"{report_path}: LongMemEval retrieval artifact must include methodology")
            return
        if methodology.get("retrieval_only") is not True:
            errors.append(f"{report_path}: LongMemEval retrieval artifact must set retrieval_only=true")
        if methodology.get("llm_in_loop") is not False:
            errors.append(f"{report_path}: LongMemEval retrieval artifact must set llm_in_loop=false")
        if methodology.get("semantic_judge_in_loop") is not False:
            errors.append(
                f"{report_path}: LongMemEval retrieval artifact must set semantic_judge_in_loop=false"
            )
        if methodology.get("official_longmemeval_qa_score") is not False:
            errors.append(
                f"{report_path}: LongMemEval retrieval artifact must set official_longmemeval_qa_score=false"
            )

    if no_semantic_judge:
        if str(report.get("overall_status") or "").lower() == "pass":
            errors.append(f"{report_path}: no-judge artifact cannot use overall_status=pass")
        publication_status = str(report.get("publication_status") or "").lower()
        if "launch_grade" in publication_status or "launch-grade" in publication_status:
            errors.append(f"{report_path}: no-judge artifact cannot be launch-grade")


def _validate_required_artifacts(report_path: Path, report: dict[str, Any], errors: list[str]) -> None:
    """Validate report-declared and convention-required artifact files."""
    report_dir = report_path.parent
    required = set(report.get("required_artifacts") or [])
    required.add("report.json")
    required.add("report.md")

    benchmark = str(report.get("benchmark") or "")
    if benchmark in {"longmemeval_s_retrieval_only", "longmemeval_s_context_budget"}:
        required.add("predictions.jsonl")
    if benchmark == "lerim_mcp_integration":
        required.add("details.jsonl")
    for artifact in sorted(required):
        if "/" in artifact or artifact.startswith("."):
            errors.append(f"{report_path}: unsafe required artifact path `{artifact}`")
            continue
        if not (report_dir / artifact).exists():
            errors.append(f"{report_path}: missing required artifact `{artifact}`")


def _read_jsonl(path: Path, errors: list[str]) -> list[dict[str, Any]] | None:
    """Read a JSONL artifact as object rows."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        errors.append(f"{path}: could not read JSONL artifact: {exc}")
        return None
    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path}:{line_number}: invalid JSONL row: {exc}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"{path}:{line_number}: JSONL row must be an object")
            continue
        rows.append(payload)
    return rows


def _mean(values: list[float]) -> float:
    """Return the arithmetic mean, or zero for empty input."""
    return statistics.fmean(values) if values else 0.0


def _nearest_rank_percentile(samples: list[float], percentile: float) -> float:
    """Return a nearest-rank percentile value."""
    if not samples:
        return 0.0
    ordered = sorted(samples)
    rank = max(1, math.ceil((percentile / 100.0) * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def _compare_number(
    *,
    report_path: Path,
    label: str,
    expected: Any,
    actual: Any,
    errors: list[str],
    tolerance: float = 1e-6,
) -> None:
    """Compare numeric artifact values with a small floating-point tolerance."""
    try:
        expected_number = float(expected)
        actual_number = float(actual)
    except (TypeError, ValueError):
        errors.append(f"{report_path}: integrity value `{label}` is not numeric")
        return
    if abs(expected_number - actual_number) > tolerance:
        errors.append(
            f"{report_path}: headline `{label}` does not match raw details "
            f"({expected_number} != {actual_number})"
        )


def _validate_longmemeval_integrity(
    report_path: Path,
    report: dict[str, Any],
    errors: list[str],
) -> None:
    """Recompute LongMemEval retrieval headline metrics from predictions."""
    rows = _read_jsonl(report_path.parent / "predictions.jsonl", errors)
    if rows is None:
        return
    headline = (report.get("results") or {}).get("headline")
    if not isinstance(headline, dict):
        errors.append(f"{report_path}: LongMemEval artifact must include results.headline")
        return
    _compare_number(
        report_path=report_path,
        label="count",
        expected=headline.get("count"),
        actual=len(rows),
        errors=errors,
    )
    dataset = report.get("dataset")
    if isinstance(dataset, dict) and dataset.get("evaluated_entries") is not None:
        _compare_number(
            report_path=report_path,
            label="dataset.evaluated_entries",
            expected=dataset.get("evaluated_entries"),
            actual=len(rows),
            errors=errors,
        )
    for key in (
        "recall_any_at_1",
        "recall_any_at_3",
        "recall_any_at_5",
        "recall_any_at_10",
        "recall_any_at_20",
        "ndcg_at_10",
        "mrr",
    ):
        if key in headline:
            _compare_number(
                report_path=report_path,
                label=key,
                expected=headline[key],
                actual=_mean([float(row.get(key) or 0.0) for row in rows]),
                errors=errors,
            )
    for key, percentile in (
        ("indexing_p50_ms", 50),
        ("indexing_p95_ms", 95),
        ("indexing_p99_ms", 99),
        ("retrieval_p50_ms", 50),
        ("retrieval_p95_ms", 95),
        ("retrieval_p99_ms", 99),
    ):
        if key not in headline:
            continue
        source_key = "indexing_ms" if key.startswith("indexing_") else "retrieval_ms"
        _compare_number(
            report_path=report_path,
            label=key,
            expected=headline[key],
            actual=_nearest_rank_percentile(
                [float(row.get(source_key) or 0.0) for row in rows],
                percentile,
            ),
            errors=errors,
        )


def _validate_context_budget_integrity(
    report_path: Path,
    report: dict[str, Any],
    errors: list[str],
) -> None:
    """Recompute context-budget headline metrics from predictions."""
    rows = _read_jsonl(report_path.parent / "predictions.jsonl", errors)
    if rows is None:
        return
    headline = (report.get("results") or {}).get("headline")
    if not isinstance(headline, dict):
        errors.append(f"{report_path}: context-budget artifact must include results.headline")
        return
    _compare_number(
        report_path=report_path,
        label="count",
        expected=headline.get("count"),
        actual=len(rows),
        errors=errors,
    )
    _compare_number(
        report_path=report_path,
        label="avg_full_haystack_tokens",
        expected=headline.get("avg_full_haystack_tokens"),
        actual=_mean([float(row.get("full_haystack_tokens") or 0.0) for row in rows]),
        errors=errors,
    )
    for selection in ("top_1", "top_3", "top_5", "top_10", "top_20"):
        expected = headline.get(selection)
        if not isinstance(expected, dict):
            errors.append(f"{report_path}: context-budget headline missing `{selection}`")
            continue
        selected_rows = [
            row["selected_by_k"][selection]
            for row in rows
            if isinstance(row.get("selected_by_k"), dict)
            and isinstance(row["selected_by_k"].get(selection), dict)
        ]
        if len(selected_rows) != len(rows):
            errors.append(f"{report_path}: predictions missing selected_by_k.{selection}")
            continue
        for label, source_key in (
            ("avg_selected_tokens", "selected_tokens"),
            ("avg_tokens_reduced", "tokens_reduced"),
            ("avg_reduction_ratio", "reduction_ratio"),
            ("recall_any", "recall_any"),
        ):
            _compare_number(
                report_path=report_path,
                label=f"{selection}.{label}",
                expected=expected.get(label),
                actual=_mean([float(row.get(source_key) or 0.0) for row in selected_rows]),
                errors=errors,
            )


def _validate_latency_integrity(
    report_path: Path,
    report: dict[str, Any],
    errors: list[str],
) -> None:
    """Recompute retrieval-latency headline rows from details."""
    rows = _read_jsonl(report_path.parent / "details.jsonl", errors)
    if rows is None:
        return
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("corpus_records"))].append(row)
    results = report.get("results")
    if not isinstance(results, dict):
        errors.append(f"{report_path}: latency artifact must include results object")
        return
    for size, expected in results.items():
        if not isinstance(expected, dict):
            continue
        size_rows = grouped.get(str(size), [])
        samples = [float(row.get("latency_ms") or 0.0) for row in size_rows]
        hit_counts = [float(row.get("hit_count") or 0.0) for row in size_rows]
        recomputed = {
            "ops": len(size_rows),
            "p50_ms": _nearest_rank_percentile(samples, 50),
            "p90_ms": _nearest_rank_percentile(samples, 90),
            "p95_ms": _nearest_rank_percentile(samples, 95),
            "p99_ms": _nearest_rank_percentile(samples, 99),
            "min_ms": min(samples) if samples else 0.0,
            "max_ms": max(samples) if samples else 0.0,
            "avg_ms": _mean(samples),
            "avg_hit_count": _mean(hit_counts),
        }
        for key, value in recomputed.items():
            if key in expected:
                _compare_number(
                    report_path=report_path,
                    label=f"{size}.{key}",
                    expected=expected[key],
                    actual=value,
                    errors=errors,
                )


def _validate_trace_ingestion_integrity(
    report_path: Path,
    report: dict[str, Any],
    errors: list[str],
) -> None:
    """Recompute trace-ingestion cost/performance headline rows from details."""
    rows = _read_jsonl(report_path.parent / "details.jsonl", errors)
    if rows is None:
        return
    headline = (report.get("results") or {}).get("headline")
    if not isinstance(headline, dict):
        errors.append(f"{report_path}: trace-ingestion artifact must include results.headline")
        return
    durations = [float(row.get("ingestion_ms") or 0.0) for row in rows]
    llm_calls = [float(row.get("llm_calls") or 0.0) for row in rows]
    db_deltas = [float(row.get("db_size_delta_bytes") or 0.0) for row in rows]
    durable_counts = [float(row.get("durable_record_count") or 0.0) for row in rows]
    expected_values = {
        "trace_count": len(rows),
        "passed_trace_count": sum(1 for row in rows if row.get("status") == "pass"),
        "failed_trace_count": sum(1 for row in rows if row.get("status") != "pass"),
        "avg_ingestion_ms": _mean(durations),
        "p95_ingestion_ms": _nearest_rank_percentile(durations, 95),
        "avg_llm_calls_per_trace": _mean(llm_calls),
        "total_llm_calls": int(sum(llm_calls)),
        "avg_db_size_delta_bytes": _mean(db_deltas),
        "total_db_size_delta_bytes": int(sum(db_deltas)),
        "avg_durable_records_per_trace": _mean(durable_counts),
    }
    for key, value in expected_values.items():
        if key in headline:
            _compare_number(
                report_path=report_path,
                label=key,
                expected=headline[key],
                actual=value,
                errors=errors,
            )


def _count_statuses(rows: list[dict[str, Any]], *, probe: str | None = None) -> Counter[str]:
    """Return status counts for details rows, optionally filtered by probe."""
    return Counter(
        str(row.get("status"))
        for row in rows
        if probe is None or row.get("probe") == probe
    )


def _validate_mcp_integrity(
    report_path: Path,
    report: dict[str, Any],
    errors: list[str],
) -> None:
    """Recompute MCP integration summary counts from details."""
    rows = _read_jsonl(report_path.parent / "details.jsonl", errors)
    if rows is None:
        return
    for row in rows:
        probe = row.get("probe")
        if probe == "real_config_doctor":
            if row.get("target") != "<redacted-installed-config-target>":
                errors.append(f"{report_path}: real config detail target must be redacted")
            forbidden_keys = {"doctor", "real_config_path", "config_format", "command"}
            leaked_keys = forbidden_keys & set(row)
            if leaked_keys:
                errors.append(
                    f"{report_path}: real config detail exposes local inventory keys"
                )
        elif probe == "real_installed_client_mcp_cli":
            if row.get("target") != "<redacted-installed-client-target>":
                errors.append(
                    f"{report_path}: installed-client detail target must be redacted"
                )
            forbidden_keys = {"command", "stdout", "stderr", "missing_markers"}
            leaked_keys = forbidden_keys & set(row)
            if leaked_keys:
                errors.append(
                    f"{report_path}: installed-client detail exposes local inventory keys"
                )
        elif probe == "stdio_mcp_trace_submit_extraction" and row.get("status") == "pass":
            if row.get("input_trace_kind") != "synthetic_protocol_acceptance_trace":
                errors.append(
                    f"{report_path}: trace-submit extraction detail must disclose synthetic input"
                )
    summary = report.get("summary")
    if not isinstance(summary, dict):
        return
    status_counts = _count_statuses(rows)
    expected_values: dict[str, Any] = {
        "detail_count": len(rows),
        "failure_count": sum(1 for row in rows if row.get("status") == "fail"),
        "blocker_count": sum(1 for row in rows if row.get("status") == "blocked"),
        "config_probe_count": sum(
            1 for row in rows if row.get("probe") == "temp_config_writer_doctor"
        ),
        "config_passed_count": _count_statuses(
            rows,
            probe="temp_config_writer_doctor",
        ).get("pass", 0),
        "stdio_tools_probe_count": sum(
            1 for row in rows if row.get("probe") == "stdio_mcp_tools_list"
        ),
        "stdio_passed_count": _count_statuses(rows, probe="stdio_mcp_tools_list").get(
            "pass",
            0,
        ),
        "stdio_context_tool_probe_count": sum(
            1 for row in rows if row.get("probe") == "stdio_mcp_context_brief_call"
        ),
        "stdio_context_tool_passed_count": _count_statuses(
            rows,
            probe="stdio_mcp_context_brief_call",
        ).get("pass", 0),
        "local_context_tool_call_acceptance_count": sum(
            1
            for row in rows
            if row.get("probe") == "stdio_mcp_context_brief_call"
            and row.get("status") == "pass"
        ),
        "stdio_trace_submit_probe_count": sum(
            1
            for row in rows
            if str(row.get("probe") or "").startswith("stdio_mcp_trace_submit")
        ),
        "stdio_trace_submit_passed_count": sum(
            1
            for row in rows
            if str(row.get("probe") or "").startswith("stdio_mcp_trace_submit")
            and row.get("status") == "pass"
        ),
        "trace_submit_idempotency_acceptance_count": sum(
            1
            for row in rows
            if row.get("probe") == "stdio_mcp_trace_submit_duplicate"
            and row.get("status") == "pass"
        ),
        "trace_submit_extraction_acceptance_count": sum(
            1
            for row in rows
            if row.get("probe") == "stdio_mcp_trace_submit_extraction"
            and row.get("status") == "pass"
        ),
        "real_doctor_probe_count": sum(
            1 for row in rows if row.get("probe") == "real_config_doctor"
        ),
        "installed_client_probe_count": sum(
            1 for row in rows if row.get("probe") == "real_installed_client_mcp_cli"
        ),
        "installed_client_connection_acceptance_count": sum(
            1
            for row in rows
            if row.get("probe") == "real_installed_client_mcp_cli"
            and row.get("status") == "pass"
            and row.get("acceptance_scope") == "real_installed_client_mcp_connection"
        ),
        "tool_call_probe_count": sum(
            1 for row in rows if row.get("probe") == "real_installed_client_tool_call"
        ),
        "installed_client_tool_call_acceptance_count": sum(
            1
            for row in rows
            if row.get("probe") == "real_installed_client_tool_call"
            and row.get("status") == "pass"
        ),
    }
    for key, value in expected_values.items():
        if key in summary:
            _compare_number(
                report_path=report_path,
                label=f"summary.{key}",
                expected=summary[key],
                actual=value,
                errors=errors,
            )
    if "status_counts" in summary and summary["status_counts"] != dict(status_counts):
        errors.append(f"{report_path}: summary.status_counts does not match details")


def _validate_raw_metric_integrity(
    report_path: Path,
    report: dict[str, Any],
    errors: list[str],
) -> None:
    """Validate that headline metrics can be recomputed from raw artifacts."""
    benchmark = str(report.get("benchmark") or "")
    if benchmark == "longmemeval_s_retrieval_only":
        _validate_longmemeval_integrity(report_path, report, errors)
    elif benchmark == "longmemeval_s_context_budget":
        _validate_context_budget_integrity(report_path, report, errors)
    elif benchmark == "longmemeval_s_retrieval_latency":
        _validate_latency_integrity(report_path, report, errors)
    elif benchmark == "longmemeval_s_trace_ingestion_cost_performance":
        _validate_trace_ingestion_integrity(report_path, report, errors)
    elif benchmark == "lerim_mcp_integration":
        _validate_mcp_integrity(report_path, report, errors)


def _validate_known_report_contracts(report_path: Path, report: dict[str, Any], errors: list[str]) -> None:
    """Validate schema contracts for known public benchmark types."""
    benchmark = str(report.get("benchmark") or "")

    if benchmark in LONGMEMEVAL_RETRIEVAL_BENCHMARKS | TRACE_INGESTION_COST_BENCHMARKS:
        dataset = report.get("dataset")
        if not isinstance(dataset, dict):
            errors.append(f"{report_path}: LongMemEval artifact must include dataset metadata")
        elif not dataset.get("snapshot") or not dataset.get("evaluated_entries"):
            evaluated = dataset.get("evaluated_entries") or dataset.get("evaluated_traces")
            if not dataset.get("snapshot") or not evaluated:
                errors.append(
                    f"{report_path}: LongMemEval artifact must include snapshot and evaluated count"
                )

    if benchmark in {"longmemeval_s_retrieval_only", "longmemeval_s_context_budget"}:
        if report.get("is_full_filtered_run") is not True:
            errors.append(f"{report_path}: public full retrieval/context artifact must set is_full_filtered_run=true")

    if benchmark == "lerim_mcp_integration":
        if report.get("is_full_integration_run") is not True:
            errors.append(f"{report_path}: MCP artifact must set is_full_integration_run=true")
        summary = report.get("summary")
        if not isinstance(summary, dict):
            errors.append(f"{report_path}: MCP artifact must include summary object")
        else:
            if summary.get("known_target_count") != summary.get("config_passed_count"):
                errors.append(f"{report_path}: MCP config pass count must match known target count")
            if int(summary.get("stdio_tools_probe_count") or 0) < 1:
                errors.append(f"{report_path}: MCP artifact must include stdio tools-list probe")
            if int(summary.get("stdio_passed_count") or 0) < 1:
                errors.append(f"{report_path}: MCP artifact must pass stdio tools-list probe")
            if int(summary.get("local_context_tool_call_acceptance_count") or 0) < 1:
                errors.append(f"{report_path}: MCP artifact must include local context tool-call acceptance")
            if int(summary.get("trace_submit_idempotency_acceptance_count") or 0) < 1:
                errors.append(f"{report_path}: MCP artifact must include trace-submit idempotency acceptance")

    if benchmark in EXTRACTION_BENCHMARKS:
        if report.get("public_sanitized") is not True:
            errors.append(f"{report_path}: extraction artifact must be public_sanitized=true")
        if "source_publishable_flag" in report:
            errors.append(f"{report_path}: extraction artifact must not expose source_publishable_flag")
        if "case_metrics" in report:
            errors.append(f"{report_path}: extraction artifact must not expose per-case metrics")
        if (report_path.parent / "case_metrics.jsonl").exists():
            errors.append(f"{report_path}: extraction artifact must not publish case_metrics.jsonl")
        dataset = report.get("dataset")
        if isinstance(dataset, dict):
            for private_key in (
                "case_metrics_included",
                "source_profile_counts",
                "full_source_profile_counts",
                "required_source_profiles",
                "covered_source_profiles",
                "missing_source_profiles",
                "required_guardrail_cases",
                "missing_guardrail_cases",
            ):
                if private_key in dataset:
                    errors.append(
                        f"{report_path}: extraction dataset must not expose `{private_key}`"
                    )
        model_provider = report.get("model_provider")
        if not isinstance(model_provider, dict):
            errors.append(f"{report_path}: extraction artifact must include model_provider")
        else:
            if not model_provider.get("judge_model"):
                errors.append(f"{report_path}: extraction artifact must include judge_model")
            if model_provider.get("semantic_judge_in_loop") is not True:
                errors.append(
                    f"{report_path}: extraction artifact must set semantic_judge_in_loop=true"
                )
            if model_provider.get("llm_in_loop") is not True:
                errors.append(f"{report_path}: extraction artifact must set llm_in_loop=true")
        methodology = _methodology(report)
        if methodology.get("competitor_scores_available") is not False:
            errors.append(f"{report_path}: extraction artifact must not imply competitor scores")
        if methodology.get("not_comparable_to_retrieval_only_scores") is not True:
            errors.append(
                f"{report_path}: extraction artifact must say it is not comparable to retrieval-only scores"
            )
        source_artifact = report.get("source_artifact")
        if not isinstance(source_artifact, dict):
            errors.append(f"{report_path}: extraction artifact must include source_artifact")
        else:
            if source_artifact.get("visibility") != "private":
                errors.append(f"{report_path}: extraction source artifact visibility must be private")
            private_path_keys = {"repo", "path", "basename", "config_file"} & set(
                source_artifact
            )
            if private_path_keys:
                errors.append(
                    f"{report_path}: extraction source artifact exposes private path fields"
                )
        command_source_report = _command_arg(str(report.get("command") or ""), "--source-report")
        if command_source_report not in (None, "<private-source-report>"):
            errors.append(f"{report_path}: extraction command must redact --source-report path")
    if benchmark == "lerim_false_positive_extraction_minimax_m27_negative_cases":
        methodology = report.get("methodology")
        if not isinstance(methodology, dict):
            errors.append(f"{report_path}: false-positive extraction artifact must include methodology")
        dataset = report.get("dataset")
        if not isinstance(dataset, dict) or int(dataset.get("negative_cases") or 0) < 1:
            errors.append(f"{report_path}: false-positive extraction must include negative cases")

    if benchmark in TRACE_INGESTION_COST_BENCHMARKS:
        methodology = _methodology(report)
        if methodology.get("llm_in_loop") is not True:
            errors.append(f"{report_path}: trace-ingestion cost artifact must set llm_in_loop=true")
        if methodology.get("llm_calls_measured") is not True:
            errors.append(f"{report_path}: trace-ingestion cost artifact must measure llm calls")
        if methodology.get("cost_usd_available") is not False:
            errors.append(f"{report_path}: trace-ingestion cost artifact must not imply measured cost")
        if not methodology.get("cost_unavailable_reason"):
            errors.append(f"{report_path}: trace-ingestion cost artifact must explain unavailable cost")
        cost_estimate = report.get("cost_estimate")
        if not isinstance(cost_estimate, dict):
            errors.append(f"{report_path}: trace-ingestion cost artifact must include cost_estimate")
        elif cost_estimate.get("available") is not False:
            errors.append(f"{report_path}: trace-ingestion cost artifact cost_estimate.available must be false")
        results = report.get("results")
        headline = results.get("headline") if isinstance(results, dict) else {}
        if not isinstance(headline, dict):
            errors.append(f"{report_path}: trace-ingestion cost artifact must include headline")
        else:
            if headline.get("cost_usd_available") is not False:
                errors.append(f"{report_path}: trace-ingestion headline must keep cost unavailable")
            if headline.get("avg_cost_usd_per_trace") is not None:
                errors.append(f"{report_path}: trace-ingestion headline must not infer avg cost")
            if int(headline.get("total_llm_calls") or 0) < 1:
                errors.append(f"{report_path}: trace-ingestion artifact must include measured LLM calls")
            if int(headline.get("trace_count") or 0) < 1:
                errors.append(f"{report_path}: trace-ingestion artifact must include at least one trace")
        details_path = report_path.parent / "details.jsonl"
        if not details_path.exists():
            errors.append(f"{report_path}: trace-ingestion artifact must include details.jsonl")
        else:
            for line_number, raw_line in enumerate(details_path.read_text(encoding="utf-8").splitlines(), start=1):
                if not raw_line.strip():
                    continue
                try:
                    detail = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    errors.append(f"{details_path}:{line_number}: invalid detail JSON: {exc}")
                    continue
                if detail.get("cost_usd") is not None:
                    errors.append(f"{details_path}:{line_number}: cost_usd must be null when unavailable")
                if detail.get("cost_availability") != "unavailable":
                    errors.append(f"{details_path}:{line_number}: cost_availability must be unavailable")
                if int(detail.get("llm_calls") or 0) < 1:
                    errors.append(f"{details_path}:{line_number}: llm_calls must be measured")

    if benchmark == "imported_market_baselines":
        baseline = report.get("agentmemory")
        if not isinstance(baseline, dict):
            errors.append(f"{report_path}: market baseline must include source baseline metadata")
        elif baseline.get("rerun_in_this_environment") is not False:
            errors.append(f"{report_path}: pinned market baseline must not claim local rerun")
        _validate_imported_baseline_sources(report_path, report, errors)


def _validate_imported_baseline_sources(
    report_path: Path,
    report: dict[str, Any],
    errors: list[str],
) -> None:
    """Validate source files and digests for imported competitor baselines."""
    source_artifacts = report.get("source_artifacts")
    if not isinstance(source_artifacts, list) or not source_artifacts:
        errors.append(f"{report_path}: imported baseline must include source_artifacts")
        return

    artifacts_by_source: dict[str, dict[str, Any]] = {}
    for index, artifact in enumerate(source_artifacts):
        if not isinstance(artifact, dict):
            errors.append(f"{report_path}: source_artifacts[{index}] must be an object")
            continue
        source_path = artifact.get("source_path")
        raw_url = artifact.get("raw_url")
        local_path = artifact.get("local_path")
        sha256 = artifact.get("sha256")
        if not all(isinstance(value, str) and value for value in (source_path, raw_url, local_path, sha256)):
            errors.append(
                f"{report_path}: source_artifacts[{index}] must include source_path, raw_url, local_path, and sha256"
            )
            continue
        local_file = (report_path.parent / local_path).resolve()
        try:
            local_file.relative_to(report_path.parent.resolve())
        except ValueError:
            errors.append(f"{report_path}: source_artifacts[{index}] local_path escapes report directory")
            continue
        digest = _sha256_file(local_file)
        if digest is None:
            errors.append(f"{report_path}: source_artifacts[{index}] local file missing")
            continue
        if digest != sha256:
            errors.append(f"{report_path}: source_artifacts[{index}] sha256 does not match local file")
        artifacts_by_source[source_path] = artifact

    for index, result in enumerate(report.get("results") or []):
        if not isinstance(result, dict):
            continue
        source_path = result.get("source_path")
        if not source_path:
            continue
        if source_path not in artifacts_by_source:
            errors.append(
                f"{report_path}: results[{index}] source_path is not listed in source_artifacts"
            )


def _iter_public_text_files(root: Path) -> list[Path]:
    """Return text-like files under a public tree."""
    files: list[Path] = []
    for glob in PUBLIC_TEXT_GLOBS:
        files.extend(root.rglob(glob))
    return sorted(path for path in files if path.is_file())


def _validate_public_text(root: Path, errors: list[str]) -> None:
    """Scan public text files for local machine path leaks."""
    if not root.exists():
        return
    for path in _iter_public_text_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if LOCAL_ABSOLUTE_PATH_RE.search(text):
            errors.append(f"{path}: private/local-only path leaked")


def _validate_public_text_surfaces(repo_root: Path, errors: list[str]) -> None:
    """Scan release-public text surfaces for local machine path leaks."""
    for name in PUBLIC_TEXT_ROOT_NAMES:
        path = repo_root / name
        if not path.exists():
            continue
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if LOCAL_ABSOLUTE_PATH_RE.search(text):
                errors.append(f"{path}: private/local-only path leaked")
            continue
        _validate_public_text(path, errors)


def _validate_market_comparison(docs_dir: Path, errors: list[str]) -> None:
    """Validate the market comparison page boundaries."""
    market_page = docs_dir / "benchmarks" / "market-comparison.md"
    if not market_page.exists():
        errors.append(f"{market_page}: missing market comparison page")
        return
    text = market_page.read_text(encoding="utf-8")
    lower_text = text.lower()
    if (
        "https://www.agent-memory.dev/" in text
        and "public market-row source" not in lower_text
        and "third-party market-row source" not in lower_text
        and "competitor-maintained market-row source" not in lower_text
    ):
        errors.append(f"{market_page}: website numbers need explicit source provenance")
    if "/blob/develop/" in text:
        errors.append(f"{market_page}: competitor benchmark links must be pinned, not live branch URLs")
    headings = {
        match.group(1).strip()
        for match in re.finditer(r"^##\s+(.+)$", text, flags=re.MULTILINE)
    }
    unexpected_headings = headings - MARKET_COMPARISON_H2
    if unexpected_headings:
        errors.append(f"{market_page}: unexpected market comparison section")
    if "Lerim Results" not in text or "Current Market Snapshot" not in text:
        errors.append(f"{market_page}: missing expected market-wide navigation/summary sections")


def _row_has_numeric_claim(text: str, system: str) -> bool:
    """Return whether a market table row for a system contains a number."""
    prefix = f"| {system} |"
    return any(
        line.startswith(prefix) and re.search(r"\d", line)
        for line in text.splitlines()
    )


def _validate_market_source_manifest(
    *,
    repo_root: Path,
    docs_dir: Path,
    errors: list[str],
) -> None:
    """Validate source metadata for third-party market rows."""
    market_page = docs_dir / "benchmarks" / "market-comparison.md"
    if not market_page.exists():
        return
    text = market_page.read_text(encoding="utf-8")
    external_numeric_systems = sorted(
        system for system in EXTERNAL_MARKET_SYSTEMS if _row_has_numeric_claim(text, system)
    )
    manifest_path = repo_root / "benchmarks" / "results" / "market-sources.json"
    if not manifest_path.exists():
        if external_numeric_systems:
            errors.append(
                f"{manifest_path}: missing market source manifest for numeric external market rows"
            )
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{manifest_path}: invalid JSON: {exc}")
        return
    if manifest.get("schema_version") != 1:
        errors.append(f"{manifest_path}: schema_version must be 1")
    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append(f"{manifest_path}: sources must be a non-empty list")
        return
    systems_with_claims: set[str] = set()
    seen_ids: set[str] = set()
    for source_index, source in enumerate(sources):
        if not isinstance(source, dict):
            errors.append(f"{manifest_path}: sources[{source_index}] must be an object")
            continue
        source_id = source.get("id")
        source_type = source.get("source_type")
        url = source.get("url")
        accessed = source.get("accessed")
        for key, value in {
            "id": source_id,
            "source_type": source_type,
            "url": url,
            "accessed": accessed,
        }.items():
            if not isinstance(value, str) or not value:
                errors.append(f"{manifest_path}: sources[{source_index}].{key} is required")
        if isinstance(source_id, str):
            if source_id in seen_ids:
                errors.append(f"{manifest_path}: duplicate source id {source_id!r}")
            seen_ids.add(source_id)
        if isinstance(url, str) and "/blob/develop/" in url:
            errors.append(f"{manifest_path}: sources[{source_index}] URL must be pinned")
        if isinstance(accessed, str) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", accessed):
            errors.append(f"{manifest_path}: sources[{source_index}].accessed must be YYYY-MM-DD")
        claims = source.get("claims")
        if not isinstance(claims, list) or not claims:
            errors.append(f"{manifest_path}: sources[{source_index}].claims must be non-empty")
            continue
        for claim_index, claim in enumerate(claims):
            if not isinstance(claim, dict):
                errors.append(
                    f"{manifest_path}: sources[{source_index}].claims[{claim_index}] must be an object"
                )
                continue
            system = claim.get("system")
            metric = claim.get("metric")
            value = claim.get("value")
            page_text = claim.get("market_page_text")
            for key, item in {
                "system": system,
                "metric": metric,
                "value": value,
                "market_page_text": page_text,
            }.items():
                if not isinstance(item, str) or not item:
                    errors.append(
                        f"{manifest_path}: sources[{source_index}].claims[{claim_index}].{key} is required"
                    )
            if isinstance(system, str):
                systems_with_claims.add(system)
            if isinstance(page_text, str) and page_text not in text:
                errors.append(
                    f"{manifest_path}: claim text not found in market comparison: {page_text!r}"
                )
    for system in external_numeric_systems:
        if system not in systems_with_claims:
            errors.append(f"{manifest_path}: missing source claim for {system}")


def _validate_generated_index(
    *,
    raw_dir: Path,
    reports_dir: Path,
    repo_root: Path,
    errors: list[str],
) -> None:
    """Validate generated public benchmark index naming."""
    index_path = reports_dir / "index.md"
    if not index_path.exists():
        errors.append(f"{index_path}: missing generated benchmark index")
        return
    text = index_path.read_text(encoding="utf-8")
    if "agentmemory_pinned_upstream_baseline" in text or "agentmemory-pinned-baseline" in text:
        errors.append(f"{index_path}: public index should use general market baseline label")
    imported_baseline_exists = (raw_dir / "imported-market-baselines" / "report.json").exists()
    if imported_baseline_exists and "imported_market_baselines" not in text:
        errors.append(f"{index_path}: missing general imported baseline row")
    from benchmarks.scripts.build_report_index import collect_reports, render_index

    expected = render_index(
        collect_reports(raw_dir, repo_root=repo_root),
        raw_dir=raw_dir,
        reports_dir=reports_dir,
    )
    if text != expected:
        errors.append(f"{index_path}: generated benchmark index is stale")


def _report_markdown_filename(report_path: Path, report: dict[str, Any]) -> str:
    """Return the generated report filename for one raw report."""
    benchmark = str(report.get("benchmark") or "")
    if benchmark == "imported_market_baselines":
        return "imported-market-baselines.md"
    return f"{report_path.parent.name}.md"


def _validate_generated_report_copies(
    *,
    raw_dir: Path,
    reports_dir: Path,
    repo_root: Path,
    errors: list[str],
) -> None:
    """Validate generated report Markdown mirrors raw report.md sources."""
    expected_reports: set[Path] = set()
    for report_path in sorted(raw_dir.glob("*/report.json")):
        if _is_git_ignored(report_path, cwd=repo_root):
            continue
        report = _load_report(report_path, errors)
        if report is None:
            continue
        source_md = report_path.parent / "report.md"
        if not source_md.exists():
            continue
        target_md = reports_dir / _report_markdown_filename(report_path, report)
        expected_reports.add(target_md.resolve())
        if not target_md.exists():
            errors.append(f"{target_md}: missing generated report copy")
            continue
        if target_md.read_text(encoding="utf-8") != source_md.read_text(encoding="utf-8"):
            errors.append(f"{target_md}: generated report copy is stale")

    for report_md in sorted(reports_dir.glob("*.md")):
        if report_md.name == "index.md":
            continue
        if report_md.resolve() not in expected_reports:
            errors.append(f"{report_md}: stale generated report has no raw source")


def _validate_public_report_manifest(
    *,
    repo_root: Path,
    raw_dir: Path,
    errors: list[str],
) -> None:
    """Validate the manifest of public raw reports that must not vanish silently."""
    manifest_path = repo_root / "benchmarks" / "results" / "public-reports.json"
    if not manifest_path.exists():
        if any(raw_dir.glob("*/report.json")):
            errors.append(f"{manifest_path}: missing public report manifest")
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{manifest_path}: invalid JSON: {exc}")
        return
    if not isinstance(manifest, dict):
        errors.append(f"{manifest_path}: public report manifest must be a JSON object")
        return
    if manifest.get("schema_version") != 1:
        errors.append(f"{manifest_path}: schema_version must be 1")
    required_reports = manifest.get("required_reports")
    if not isinstance(required_reports, list):
        errors.append(f"{manifest_path}: required_reports must be a list")
        return
    seen: set[str] = set()
    for index, item in enumerate(required_reports):
        if isinstance(item, str):
            artifact = item
        elif isinstance(item, dict):
            artifact = item.get("artifact")
        else:
            errors.append(f"{manifest_path}: required_reports[{index}] must be a string or object")
            continue
        if not isinstance(artifact, str) or not artifact:
            errors.append(f"{manifest_path}: required_reports[{index}].artifact is required")
            continue
        if "/" in artifact or artifact.startswith("."):
            errors.append(f"{manifest_path}: unsafe required report artifact `{artifact}`")
            continue
        if artifact in seen:
            errors.append(f"{manifest_path}: duplicate required report artifact `{artifact}`")
        seen.add(artifact)
        report_path = raw_dir / artifact / "report.json"
        if not report_path.exists():
            errors.append(f"{manifest_path}: required raw report is missing: {report_path}")


def _validate_benchmark_summary_svg(
    *,
    docs_dir: Path,
    raw_dir: Path,
    errors: list[str],
) -> None:
    """Validate that public benchmark art is generated from raw artifacts."""
    svg_path = docs_dir / "assets" / "benchmark-summary.svg"
    if not svg_path.exists():
        return
    from benchmarks.scripts.generate_benchmark_summary_svg import build_svg, load_snapshot

    expected = build_svg(load_snapshot(raw_dir))
    actual = svg_path.read_text(encoding="utf-8")
    if actual != expected:
        errors.append(
            f"{svg_path}: benchmark summary SVG must be regenerated from raw artifacts"
        )


def _validate_support_boundary_svg(
    *,
    docs_dir: Path,
    raw_dir: Path,
    errors: list[str],
) -> None:
    """Validate that public support-boundary art is generated from raw artifacts."""
    svg_path = docs_dir / "assets" / "support-boundary.svg"
    if not svg_path.exists():
        return
    from benchmarks.scripts.generate_support_boundary_svg import build_svg, load_snapshot

    expected = build_svg(load_snapshot(raw_dir))
    actual = svg_path.read_text(encoding="utf-8")
    if actual != expected:
        errors.append(
            f"{svg_path}: support boundary SVG must be regenerated from raw artifacts"
        )


def _load_optional_report(raw_dir: Path, artifact_name: str) -> dict[str, Any] | None:
    """Load a raw report if that artifact exists."""
    path = raw_dir / artifact_name / "report.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _load_doc_report(
    *,
    raw_dir: Path,
    artifact_name: str,
    page: Path,
    text: str,
    errors: list[str],
) -> dict[str, Any] | None:
    """Load a report required by a benchmark doc reference."""
    report_path = raw_dir / artifact_name / "report.json"
    reference = f"benchmarks/results/raw/{artifact_name}/report.json"
    if reference not in text and f"{artifact_name}/report.json" not in text:
        return _load_optional_report(raw_dir, artifact_name)
    if not report_path.exists():
        errors.append(f"{page}: referenced raw report is missing: {report_path}")
        return None
    return _load_report(report_path, errors)


def _as_percent(value: Any, *, decimals: int = 1) -> str:
    """Format a ratio or percent value the same way public docs do."""
    numeric = float(value)
    if 0.0 <= numeric <= 1.0:
        numeric *= 100.0
    return f"{numeric:.{decimals}f}%"


def _rows_matching(text: str, needles: tuple[str, ...]) -> list[str]:
    """Return Markdown table rows that include every required needle."""
    return [
        line
        for line in text.splitlines()
        if line.lstrip().startswith("|") and all(needle in line for needle in needles)
    ]


def _expect_doc_row_values(
    *,
    page: Path,
    text: str,
    label: str,
    row_needles: tuple[str, ...],
    expected_values: tuple[str, ...],
    errors: list[str],
) -> None:
    """Validate a source-backed Markdown row against raw artifact values."""
    rows = _rows_matching(text, row_needles)
    if not rows:
        errors.append(f"{page}: missing source-backed benchmark row for {label}")
        return
    row = rows[0]
    missing = [value for value in expected_values if value not in row]
    if missing:
        joined = ", ".join(missing)
        errors.append(f"{page}: benchmark row for {label} is stale; missing {joined}")


def _expect_doc_values(
    *,
    page: Path,
    text: str,
    label: str,
    expected_values: tuple[str, ...],
    errors: list[str],
) -> None:
    """Validate that a public doc section contains every value from an artifact."""
    missing = [value for value in expected_values if value not in text]
    if missing:
        joined = ", ".join(missing)
        errors.append(f"{page}: benchmark values for {label} are stale; missing {joined}")


def _longmemeval_summary_values(report: dict[str, Any]) -> tuple[str, ...]:
    """Return public LongMemEval summary-row values from a Lerim report."""
    headline = report["results"]["headline"]
    return (
        _as_percent(headline["recall_any_at_5"]),
        _as_percent(headline["recall_any_at_10"]),
        _as_percent(headline["recall_any_at_20"]),
        _as_percent(headline["ndcg_at_10"]),
        _as_percent(headline["mrr"]),
    )


def _longmemeval_snapshot_values(report: dict[str, Any]) -> tuple[str, ...]:
    """Return LongMemEval metrics used in the market snapshot row."""
    headline = report["results"]["headline"]
    return (
        _as_percent(headline["recall_any_at_5"]),
        _as_percent(headline["recall_any_at_10"]),
        _as_percent(headline["ndcg_at_10"]),
        _as_percent(headline["mrr"]),
    )


def _longmemeval_detailed_values(report: dict[str, Any]) -> tuple[str, ...]:
    """Return every public LongMemEval detail-row metric from a Lerim report."""
    headline = report["results"]["headline"]
    keys = (
        "recall_any_at_1",
        "recall_any_at_3",
        "recall_any_at_5",
        "recall_any_at_10",
        "recall_any_at_20",
        "ndcg_at_10",
        "mrr",
    )
    return tuple(_as_percent(headline[key]) for key in keys if key in headline)


def _context_budget_values(report: dict[str, Any], *, selection: str) -> tuple[str, ...]:
    """Return public context-budget row values for one selection."""
    headline = report["results"]["headline"]
    if selection == "full":
        return (
            f"{float(headline['avg_full_haystack_tokens']):,.0f}",
            "0",
            "0.0%",
            "100.0%",
        )
    row = headline[selection]
    return (
        f"{float(row['avg_selected_tokens']):,.0f}",
        f"{float(row['avg_tokens_reduced']):,.0f}",
        _as_percent(row["avg_reduction_ratio"]),
        _as_percent(row["recall_any"]),
    )


def _latency_values(report: dict[str, Any], *, size: str) -> tuple[str, ...]:
    """Return public retrieval-latency row values for one corpus size."""
    row = report["results"][size]
    return (
        str(int(row["ops"])),
        f"{float(row['avg_hit_count']):.1f}",
        f"{float(row['p50_ms']):.1f} ms",
        f"{float(row['p90_ms']):.1f} ms",
        f"{float(row['p95_ms']):.1f} ms",
        f"{float(row['p99_ms']):.1f} ms",
    )


def _extraction_values(report: dict[str, Any]) -> tuple[str, ...]:
    """Return public extraction-quality table values from a sanitized report."""
    headline = report["results"]["headline"]
    dataset = report.get("dataset") or {}
    return (
        str(int(dataset["cases"])),
        str(int(dataset["case_failures"])),
        _as_percent(headline["task_completion_rate_pct"], decimals=2),
        _as_percent(headline["quality_avg"], decimals=2),
        _as_percent(headline["quality_gate_rate_pct"], decimals=2),
        _as_percent(headline["hard_gate_pass_rate_pct"], decimals=2),
        _as_percent(headline["concept_recall_avg"], decimals=2),
        _as_percent(headline["required_concept_coverage_rate_pct"], decimals=2),
        _as_percent(headline["kind_alignment_rate_pct"], decimals=2),
        _as_percent(headline["record_precision_avg"], decimals=2),
        _as_percent(headline["faithfulness_avg"], decimals=2),
        _as_percent(headline["claim_faithfulness_rate_pct"], decimals=2),
        _as_percent(headline["negative_precision_rate_pct"], decimals=2),
        _as_percent(headline["signal_filtering_rate_pct"], decimals=2),
        _as_percent(headline["evidence_coverage_rate_pct"], decimals=2),
        _as_percent(headline["evidence_validity_rate_pct"], decimals=2),
    )


def _false_positive_values(report: dict[str, Any]) -> tuple[str, ...]:
    """Return public false-positive extraction table values."""
    headline = report["results"]["headline"]
    return (
        str(int(headline["negative_case_count"])),
        str(int(headline["no_durable_case_count"])),
        str(int(headline["false_positive_case_count"])),
        _as_percent(headline["negative_precision_rate_pct"], decimals=2),
        _as_percent(headline["false_positive_case_rate_pct"], decimals=2),
        str(int(headline["total_durable_records_on_negative_cases"])),
        _as_percent(headline["forbidden_concept_score_avg"], decimals=2),
        _as_percent(headline["signal_filtering_score_avg"], decimals=2),
    )


def _trace_ingestion_cost_values(report: dict[str, Any]) -> tuple[str, ...]:
    """Return public trace-ingestion cost/performance values."""
    headline = report["results"]["headline"]
    return (
        str(int(headline["trace_count"])),
        str(int(headline["passed_trace_count"])),
        f"{float(headline['avg_ingestion_ms']):,.1f} ms",
        f"{float(headline['p95_ingestion_ms']):,.1f} ms",
        f"{float(headline['avg_llm_calls_per_trace']):.1f}",
        str(int(headline["total_llm_calls"])),
        f"{float(headline['avg_db_size_delta_bytes']):,.0f} bytes",
        f"{float(headline['avg_durable_records_per_trace']):.1f}",
        "not available",
    )


def _baseline_summary_values(report: dict[str, Any], *, mode: str) -> tuple[str, ...]:
    """Return public LongMemEval summary values from an imported baseline report."""
    result = next(
        (
            item
            for item in report.get("results", [])
            if isinstance(item, dict) and item.get("mode") == mode
        ),
        None,
    )
    if not isinstance(result, dict):
        return ()
    headline = result["headline"]
    return (
        _as_percent(headline["recall_any_at_5"]),
        _as_percent(headline["recall_any_at_10"]),
        _as_percent(headline["recall_any_at_20"]),
        _as_percent(headline["ndcg_at_10"]),
        _as_percent(headline["mrr"]),
    )


def _baseline_snapshot_values(report: dict[str, Any], *, mode: str) -> tuple[str, ...]:
    """Return imported baseline metrics used in the market snapshot row."""
    result = next(
        (
            item
            for item in report.get("results", [])
            if isinstance(item, dict) and item.get("mode") == mode
        ),
        None,
    )
    if not isinstance(result, dict):
        return ()
    headline = result["headline"]
    return (
        _as_percent(headline["recall_any_at_5"]),
        _as_percent(headline["recall_any_at_10"]),
        _as_percent(headline["ndcg_at_10"]),
        _as_percent(headline["mrr"]),
    )


def _baseline_detailed_values(report: dict[str, Any], *, mode: str) -> tuple[str, ...]:
    """Return public LongMemEval detail values from an imported baseline report."""
    result = next(
        (
            item
            for item in report.get("results", [])
            if isinstance(item, dict) and item.get("mode") == mode
        ),
        None,
    )
    if not isinstance(result, dict):
        return ()
    headline = result["headline"]
    return (
        _as_percent(headline["recall_any_at_5"]),
        _as_percent(headline["recall_any_at_10"]),
        _as_percent(headline["recall_any_at_20"]),
        _as_percent(headline["ndcg_at_10"]),
        _as_percent(headline["mrr"]),
    )


def _validate_benchmark_doc_numbers(
    *,
    docs_dir: Path,
    raw_dir: Path,
    errors: list[str],
) -> None:
    """Validate hand-written benchmark docs against raw report artifacts."""
    lerim_page = docs_dir / "benchmarks" / "lerim-results.md"
    if lerim_page.exists():
        text = lerim_page.read_text(encoding="utf-8")

        hybrid = _load_doc_report(
            raw_dir=raw_dir,
            artifact_name="longmemeval-hybrid-full",
            page=lerim_page,
            text=text,
            errors=errors,
        )
        if hybrid is not None:
            _expect_doc_row_values(
                page=lerim_page,
                text=text,
                label="Lerim LongMemEval-S hybrid",
                row_needles=("longmemeval-hybrid-full/report.json",),
                expected_values=_longmemeval_summary_values(hybrid),
                errors=errors,
            )
            _expect_doc_row_values(
                page=lerim_page,
                text=text,
                label="Lerim LongMemEval-S hybrid detail",
                row_needles=("| Hybrid |", "500"),
                expected_values=_longmemeval_detailed_values(hybrid),
                errors=errors,
            )

        lexical = _load_doc_report(
            raw_dir=raw_dir,
            artifact_name="longmemeval-lexical-full",
            page=lerim_page,
            text=text,
            errors=errors,
        )
        if lexical is not None:
            _expect_doc_row_values(
                page=lerim_page,
                text=text,
                label="Lerim LongMemEval-S lexical",
                row_needles=("longmemeval-lexical-full/report.json",),
                expected_values=_longmemeval_summary_values(lexical),
                errors=errors,
            )
            _expect_doc_row_values(
                page=lerim_page,
                text=text,
                label="Lerim LongMemEval-S lexical detail",
                row_needles=("| Lexical |", "500"),
                expected_values=_longmemeval_detailed_values(lexical),
                errors=errors,
            )

        budget = _load_doc_report(
            raw_dir=raw_dir,
            artifact_name="context-budget-hybrid-full",
            page=lerim_page,
            text=text,
            errors=errors,
        )
        if budget is not None:
            top_10 = budget["results"]["headline"]["top_10"]
            _expect_doc_row_values(
                page=lerim_page,
                text=text,
                label="Lerim context budget",
                row_needles=("context-budget-hybrid-full/report.json",),
                expected_values=(
                    _as_percent(top_10["avg_reduction_ratio"]),
                    _as_percent(top_10["recall_any"]),
                ),
                errors=errors,
            )
            for selection, label in (
                ("full", "Full haystack"),
                ("top_1", "Top 1"),
                ("top_3", "Top 3"),
                ("top_5", "Top 5"),
                ("top_10", "Top 10"),
                ("top_20", "Top 20"),
            ):
                _expect_doc_row_values(
                    page=lerim_page,
                    text=text,
                    label=f"Lerim context budget {label}",
                    row_needles=(f"| {label} |",),
                    expected_values=_context_budget_values(budget, selection=selection),
                    errors=errors,
                )

        latency = _load_doc_report(
            raw_dir=raw_dir,
            artifact_name="retrieval-latency-longmemeval",
            page=lerim_page,
            text=text,
            errors=errors,
        )
        if latency is not None:
            latency_1k = latency["results"]["1000"]
            _expect_doc_row_values(
                page=lerim_page,
                text=text,
                label="Lerim retrieval latency",
                row_needles=("retrieval-latency-longmemeval/report.json",),
                expected_values=(
                    f"{float(latency_1k['p50_ms']):.1f} ms",
                    f"{float(latency_1k['p99_ms']):.1f} ms",
                ),
                errors=errors,
            )
            for size, label in (("100", "100 records"), ("1000", "1,000 records")):
                _expect_doc_row_values(
                    page=lerim_page,
                    text=text,
                    label=f"Lerim retrieval latency {label}",
                    row_needles=(f"| {label} |",),
                    expected_values=_latency_values(latency, size=size),
                    errors=errors,
                )

        trace_ingestion = _load_doc_report(
            raw_dir=raw_dir,
            artifact_name="trace-ingestion-cost-longmemeval-s-sample",
            page=lerim_page,
            text=text,
            errors=errors,
        )
        if trace_ingestion is not None:
            headline = trace_ingestion["results"]["headline"]
            _expect_doc_row_values(
                page=lerim_page,
                text=text,
                label="Lerim trace ingestion cost/performance",
                row_needles=("trace-ingestion-cost-longmemeval-s-sample/report.json",),
                expected_values=(
                    f"{float(headline['avg_ingestion_ms']):,.1f} ms",
                    f"{float(headline['avg_llm_calls_per_trace']):.1f}",
                    f"{float(headline['avg_db_size_delta_bytes']):,.0f} bytes",
                    "not available",
                ),
                errors=errors,
            )
            _expect_doc_values(
                page=lerim_page,
                text=text,
                label="Lerim trace ingestion cost/performance table",
                expected_values=_trace_ingestion_cost_values(trace_ingestion),
                errors=errors,
            )

        extraction = _load_doc_report(
            raw_dir=raw_dir,
            artifact_name="extraction-minimax-m27-full-47",
            page=lerim_page,
            text=text,
            errors=errors,
        )
        if extraction is not None:
            headline = extraction["results"]["headline"]
            _expect_doc_row_values(
                page=lerim_page,
                text=text,
                label="Lerim extraction quality",
                row_needles=("extraction-minimax-m27-full-47/report.json",),
                expected_values=(
                    _as_percent(headline["quality_avg"], decimals=2),
                    _as_percent(headline["quality_gate_rate_pct"], decimals=2),
                    _as_percent(headline["hard_gate_pass_rate_pct"], decimals=2),
                ),
                errors=errors,
            )
            _expect_doc_values(
                page=lerim_page,
                text=text,
                label="Lerim extraction quality table",
                expected_values=_extraction_values(extraction),
                errors=errors,
            )

        false_positive = _load_doc_report(
            raw_dir=raw_dir,
            artifact_name="false-positive-extraction-minimax-m27-negative-cases",
            page=lerim_page,
            text=text,
            errors=errors,
        )
        if false_positive is not None:
            headline = false_positive["results"]["headline"]
            _expect_doc_row_values(
                page=lerim_page,
                text=text,
                label="Lerim false-positive extraction",
                row_needles=(
                    "false-positive-extraction-minimax-m27-negative-cases/report.json",
                ),
                expected_values=(
                    _as_percent(headline["negative_precision_rate_pct"], decimals=2),
                    str(int(headline["false_positive_case_count"])),
                    str(int(headline["total_durable_records_on_negative_cases"])),
                ),
                errors=errors,
            )
            _expect_doc_values(
                page=lerim_page,
                text=text,
                label="Lerim false-positive extraction table",
                expected_values=_false_positive_values(false_positive),
                errors=errors,
            )

    market_page = docs_dir / "benchmarks" / "market-comparison.md"
    if not market_page.exists():
        return
    text = market_page.read_text(encoding="utf-8")
    if "## LongMemEval-S Retrieval" not in text:
        return

    hybrid = _load_doc_report(
        raw_dir=raw_dir,
        artifact_name="longmemeval-hybrid-full",
        page=market_page,
        text=text,
        errors=errors,
    )
    if hybrid is not None:
        _expect_doc_row_values(
            page=market_page,
            text=text,
            label="market snapshot Lerim hybrid",
            row_needles=(
                "| Lerim | Source-session context compiler |",
                "longmemeval-hybrid-full/report.json",
            ),
            expected_values=_longmemeval_snapshot_values(hybrid),
            errors=errors,
        )
        _expect_doc_row_values(
            page=market_page,
            text=text,
            label="market Lerim hybrid",
            row_needles=("| Lerim | Hybrid |", "longmemeval-hybrid-full/report.json"),
            expected_values=_longmemeval_summary_values(hybrid),
            errors=errors,
        )

    lexical = _load_doc_report(
        raw_dir=raw_dir,
        artifact_name="longmemeval-lexical-full",
        page=market_page,
        text=text,
        errors=errors,
    )
    if lexical is not None:
        _expect_doc_row_values(
            page=market_page,
            text=text,
            label="market Lerim lexical",
            row_needles=("| Lerim | Lexical |", "longmemeval-lexical-full/report.json"),
            expected_values=_longmemeval_summary_values(lexical),
            errors=errors,
        )

    baseline = _load_doc_report(
        raw_dir=raw_dir,
        artifact_name="imported-market-baselines",
        page=market_page,
        text=text,
        errors=errors,
    )
    if baseline is not None:
        _expect_doc_row_values(
            page=market_page,
            text=text,
            label="market snapshot imported baseline hybrid",
            row_needles=(
                "| AgentMemory | Local memory engine plus MCP server |",
                "Pinned upstream raw artifact",
            ),
            expected_values=_baseline_snapshot_values(baseline, mode="hybrid"),
            errors=errors,
        )
        _expect_doc_row_values(
            page=market_page,
            text=text,
            label="market imported baseline hybrid",
            row_needles=(
                "| AgentMemory | BM25+Vector |",
                "imported-market-baselines/report.json",
            ),
            expected_values=_baseline_detailed_values(baseline, mode="hybrid"),
            errors=errors,
        )
        _expect_doc_row_values(
            page=market_page,
            text=text,
            label="market imported baseline BM25",
            row_needles=(
                "| AgentMemory | BM25-only |",
                "imported-market-baselines/report.json",
            ),
            expected_values=_baseline_detailed_values(baseline, mode="bm25"),
            errors=errors,
        )


def _iter_release_tracking_files(repo_root: Path) -> list[Path]:
    """Return existing release-critical files that must be tracked before release."""
    files: list[Path] = []
    for name in RELEASE_TRACKING_ROOT_NAMES:
        path = repo_root / name
        if not path.exists():
            continue
        if path.is_file():
            files.append(path)
            continue
        files.extend(child for child in path.rglob("*") if child.is_file())
    return sorted(files)


def _validate_tracked_public_files(repo_root: Path, errors: list[str]) -> None:
    """Validate that release-critical files are tracked in git."""
    for path in _iter_release_tracking_files(repo_root):
        if _is_git_ignored(path, cwd=repo_root):
            continue
        if not _is_git_tracked(path, cwd=repo_root):
            errors.append(f"{path}: release-critical file is not tracked by git")


def _validate_readme_benchmark_chart_gate(
    repo_root: Path,
    *,
    any_dirty_report: bool,
    errors: list[str],
) -> None:
    """Prevent polished benchmark art in README while reports are dirty."""
    readme = repo_root / "README.md"
    if not readme.exists() or not any_dirty_report:
        return
    text = readme.read_text(encoding="utf-8")
    if "docs/assets/benchmark-summary.svg" in text:
        errors.append(
            f"{readme}: do not embed benchmark-summary.svg while raw reports have git_dirty=true"
        )


def validate_public_artifacts(
    *,
    repo_root: Path,
    raw_dir: Path,
    reports_dir: Path,
    docs_dir: Path,
    require_clean: bool = False,
    require_tracked_public_files: bool = False,
) -> ValidationResult:
    """Validate public benchmark artifacts and docs."""
    errors: list[str] = []
    repo_root = repo_root.resolve()
    raw_dir = raw_dir.resolve()
    reports_dir = reports_dir.resolve()
    docs_dir = docs_dir.resolve()
    any_dirty_report = False
    current_head = _git_head(repo_root) if require_clean else None

    for report_path in sorted(raw_dir.glob("*/report.json")):
        if _is_git_ignored(report_path, cwd=repo_root):
            continue
        report = _load_report(report_path, errors)
        if report is None:
            continue
        any_dirty_report = any_dirty_report or _report_git_dirty(report) is True
        _validate_report_metadata(report_path, report, errors)
        if require_clean and not _is_imported_market_baseline(report):
            _validate_clean_worktree(
                report_path,
                report,
                errors,
                current_head=current_head,
            )
        _validate_required_artifacts(report_path, report, errors)
        _validate_no_judge_boundaries(report_path, report, errors)
        _validate_known_report_contracts(report_path, report, errors)
        _validate_raw_metric_integrity(report_path, report, errors)

    if any(raw_dir.glob("*market-feature*")) or any(reports_dir.glob("*market-feature*")):
        errors.append("obsolete standalone market-feature artifact path exists")

    _validate_public_text_surfaces(repo_root, errors)
    _validate_generated_index(
        raw_dir=raw_dir,
        reports_dir=reports_dir,
        repo_root=repo_root,
        errors=errors,
    )
    _validate_generated_report_copies(
        raw_dir=raw_dir,
        reports_dir=reports_dir,
        repo_root=repo_root,
        errors=errors,
    )
    _validate_public_report_manifest(
        repo_root=repo_root,
        raw_dir=raw_dir,
        errors=errors,
    )
    _validate_benchmark_summary_svg(docs_dir=docs_dir, raw_dir=raw_dir, errors=errors)
    _validate_support_boundary_svg(docs_dir=docs_dir, raw_dir=raw_dir, errors=errors)
    _validate_benchmark_doc_numbers(docs_dir=docs_dir, raw_dir=raw_dir, errors=errors)
    _validate_market_comparison(docs_dir, errors)
    _validate_market_source_manifest(repo_root=repo_root, docs_dir=docs_dir, errors=errors)
    _validate_readme_benchmark_chart_gate(
        repo_root,
        any_dirty_report=any_dirty_report,
        errors=errors,
    )
    if require_tracked_public_files:
        _validate_tracked_public_files(repo_root, errors)
    return ValidationResult(errors=tuple(errors))


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Validate public benchmark artifacts.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--raw-dir", type=Path, default=Path("benchmarks/results/raw"))
    parser.add_argument("--reports-dir", type=Path, default=Path("benchmarks/results/reports"))
    parser.add_argument("--docs-dir", type=Path, default=Path("docs"))
    parser.add_argument(
        "--require-clean",
        action="store_true",
        help="Fail if public artifacts were generated from a dirty worktree.",
    )
    parser.add_argument(
        "--require-tracked-public-files",
        action="store_true",
        help=(
            "Fail if release-critical README/docs/benchmark/source/test/workflow/"
            "Docker/package/tooling files exist but are not tracked by git."
        ),
    )
    return parser.parse_args()


def main() -> None:
    """Validate public artifacts from the command line."""
    args = parse_args()
    result = validate_public_artifacts(
        repo_root=args.repo_root,
        raw_dir=args.raw_dir,
        reports_dir=args.reports_dir,
        docs_dir=args.docs_dir,
        require_clean=args.require_clean,
        require_tracked_public_files=args.require_tracked_public_files,
    )
    if result.ok:
        print("Public benchmark artifacts validated.")
        return

    print("Public benchmark artifact validation failed:")
    for error in result.errors:
        print(f"- {error}")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
