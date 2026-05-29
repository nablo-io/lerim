"""Measure Lerim trace-ingestion cost and performance on public source sessions.

The runner uses LongMemEval-S haystack sessions as public source-session input,
normalizes them through Lerim's generic trace envelope, and ingests them through
the same DSPy trace-ingestion path used by the product. It records
wall-clock time, live LLM call counts, and context database growth. Provider
cost is reported only when measured usage data is available.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import sys
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lerim.agents.trace_ingestion import run_trace_ingestion
from lerim.config.settings import get_config
from lerim.context import ContextStore, resolve_scope_identity
from lerim.traces.envelope import load_generic_trace, write_compact_trace

try:
    from benchmarks.lerim_evidence.longmemeval import (
        DATASET_FILENAME,
        DATASET_REPO_ID,
        _dataset_cache_ref,
        _git_value,
        _safe_id,
        _snapshot_from_dataset_path,
        _utc_now,
        filter_entries,
        load_dataset,
        nearest_rank_percentile,
        resolve_dataset_path,
    )
except ModuleNotFoundError:
    sys.path.append(str(Path(__file__).resolve().parents[2]))
    from benchmarks.lerim_evidence.longmemeval import (
        DATASET_FILENAME,
        DATASET_REPO_ID,
        _dataset_cache_ref,
        _git_value,
        _safe_id,
        _snapshot_from_dataset_path,
        _utc_now,
        filter_entries,
        load_dataset,
        nearest_rank_percentile,
        resolve_dataset_path,
    )


BENCHMARK_ID = "longmemeval_s_trace_ingestion_cost_performance"
DEFAULT_OUTPUT_DIR = Path("benchmarks/results/raw/trace-ingestion-cost-longmemeval-s-sample")
COST_UNAVAILABLE_REASON = (
    "Lerim trace-ingestion details expose live LLM call counts, but this runtime "
    "does not expose provider token usage or billed cost for model calls."
)


def _timestamp_for_path() -> str:
    """Return a compact UTC timestamp for default artifact names."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 digest for one artifact file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _db_size(path: Path) -> int:
    """Return the SQLite database size in bytes, or zero when absent."""
    return path.stat().st_size if path.exists() else 0


def _mean(values: list[float]) -> float:
    """Return the arithmetic mean, or zero for an empty list."""
    return statistics.fmean(values) if values else 0.0


def _p95(values: list[float]) -> float:
    """Return nearest-rank p95 for one value list."""
    return nearest_rank_percentile(values, 95)


def _public_git_status(git_status: str | None) -> str:
    """Return a public-safe git status label for report metadata."""
    if not git_status:
        return ""
    return "<dirty worktree; rerun from clean commit before launch>"


def _environment_metadata() -> dict[str, Any]:
    """Build public-safe environment metadata for the benchmark report."""
    git_status = _git_value(["status", "--short"])
    return {
        "git_commit": _git_value(["rev-parse", "HEAD"]),
        "git_dirty": bool(git_status),
        "git_status_short": _public_git_status(git_status),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "hardware": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        },
    }


def _isolated_config(root: Path):
    """Return a Config copy whose runtime files live under a temp root."""
    data_dir = root / ".lerim"
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg = get_config()
    return replace(
        cfg,
        global_data_dir=data_dir,
        sessions_db_path=data_dir / "index" / "sessions.sqlite3",
        context_db_path=data_dir / "context.sqlite3",
        platforms_path=data_dir / "platforms.json",
        embedding_cache_dir=data_dir / "models" / "embeddings",
        mlflow_enabled=False,
        mlflow_tracking_uri="",
        mlflow_required=False,
        agents={},
        projects={},
        project_types={},
    )


def _write_longmemeval_trace(
    *,
    entry: Any,
    session_index: int,
    session_id: str,
    turns: list[dict[str, Any]],
    work_dir: Path,
    source_profile: str,
) -> Path:
    """Write one LongMemEval haystack session as a normalized Lerim trace."""
    raw_path = work_dir / f"{_safe_id(entry.question_id)}_{session_index:03d}.json"
    normalized_path = work_dir / f"{_safe_id(entry.question_id)}_{session_index:03d}.jsonl"
    work_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": f"lme-{_safe_id(entry.question_id)}-{session_index:03d}",
        "metadata": {
            "source": "longmemeval-s",
            "source_profile": source_profile,
            "question_id": entry.question_id,
            "haystack_session_id": session_id,
        },
        "messages": turns,
    }
    raw_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return write_compact_trace(load_generic_trace(raw_path), normalized_path)


def _selected_sessions(entries: list[Any], *, max_traces: int, offset: int) -> list[dict[str, Any]]:
    """Select one LongMemEval haystack session per question for measurement."""
    selected: list[dict[str, Any]] = []
    skipped = 0
    for entry in entries:
        for index, session_id in enumerate(entry.haystack_session_ids):
            turns = entry.haystack_sessions[index]
            if not turns:
                continue
            if skipped < offset:
                skipped += 1
                continue
            selected.append(
                {
                    "entry": entry,
                    "session_index": index,
                    "session_id": session_id,
                    "turns": turns,
                    "started_at": entry.haystack_dates[index],
                }
            )
            if len(selected) >= max_traces:
                return selected
            break
    return selected


def _record_counts(store: ContextStore, *, session_id: str | None = None) -> dict[str, int]:
    """Return record counts for an isolated store, optionally scoped to a session."""
    query_kwargs: dict[str, Any] = {
        "entity": "records",
        "mode": "list",
        "include_archived": True,
        "order_by": "created_at",
        "limit": 500,
    }
    if session_id is not None:
        query_kwargs["source_session_id"] = session_id
    rows = store.query(**query_kwargs)["rows"]
    episode_count = sum(1 for row in rows if str(row.get("kind") or "") == "episode")
    durable_count = sum(1 for row in rows if str(row.get("kind") or "") != "episode")
    return {
        "record_count": len(rows),
        "episode_record_count": episode_count,
        "durable_record_count": durable_count,
    }


def _ingest_one_trace(
    *,
    cfg: Any,
    trace_path: Path,
    session_id: str,
    session_started_at: str,
    source_name: str,
    source_profile: str,
    scope_type: str,
    scope: str,
    scope_label: str,
    max_llm_calls: int | None,
    progress: bool,
) -> tuple[dict[str, Any], float]:
    """Run live trace ingestion once and return row metadata."""
    store = ContextStore(cfg.context_db_path)
    before_counts = _record_counts(store)
    before_size = _db_size(cfg.context_db_path)
    started = time.perf_counter()
    result, details = run_trace_ingestion(
        context_db_path=cfg.context_db_path,
        project_identity=None,
        scope_identity=resolve_scope_identity(
            scope_type=scope_type,
            scope=scope,
            scope_label=scope_label,
        ),
        session_id=session_id,
        trace_path=trace_path,
        config=cfg,
        session_started_at=session_started_at,
        return_details=True,
        max_llm_calls=max_llm_calls,
        progress=progress,
        source_name=source_name,
        source_profile=source_profile,
    )
    ingestion_ms = (time.perf_counter() - started) * 1000
    after_size = _db_size(cfg.context_db_path)
    after_counts = _record_counts(store)
    session_counts = _record_counts(store, session_id=session_id)
    row = {
        "session_id": session_id,
        "status": "pass" if details.done else "fail",
        "done": details.done,
        "completion_summary_present": bool(result.completion_summary),
        "trace_sha256": _sha256_file(trace_path),
        "trace_lines": details.trace_total_lines,
        "trace_bytes": trace_path.stat().st_size,
        "model_name": details.model_name,
        "ingestion_ms": ingestion_ms,
        "llm_calls": details.llm_calls,
        "llm_calls_status": "measured",
        "db_size_before_bytes": before_size,
        "db_size_after_bytes": after_size,
        "db_size_delta_bytes": after_size - before_size,
        "records_before": before_counts["record_count"],
        "records_after": after_counts["record_count"],
        "records_created_delta": after_counts["record_count"] - before_counts["record_count"],
        "episode_record_count": session_counts["episode_record_count"],
        "durable_record_count": session_counts["durable_record_count"],
        "cost_usd": None,
        "cost_availability": "unavailable",
        "cost_source": "not_exposed_by_runtime",
        "unavailable_reason": COST_UNAVAILABLE_REASON,
    }
    return row, ingestion_ms


def summarize(rows: list[dict[str, Any]], *, baseline_schema_bytes: int) -> dict[str, Any]:
    """Aggregate measured ingestion rows into headline metrics."""
    durations = [float(row["ingestion_ms"]) for row in rows]
    llm_calls = [float(row["llm_calls"]) for row in rows]
    db_deltas = [float(row["db_size_delta_bytes"]) for row in rows]
    durable_counts = [float(row["durable_record_count"]) for row in rows]
    return {
        "headline": {
            "trace_count": len(rows),
            "passed_trace_count": sum(1 for row in rows if row.get("status") == "pass"),
            "failed_trace_count": sum(1 for row in rows if row.get("status") != "pass"),
            "avg_ingestion_ms": _mean(durations),
            "p95_ingestion_ms": _p95(durations),
            "avg_llm_calls_per_trace": _mean(llm_calls),
            "total_llm_calls": int(sum(llm_calls)),
            "avg_db_size_delta_bytes": _mean(db_deltas),
            "total_db_size_delta_bytes": int(sum(db_deltas)),
            "baseline_schema_bytes": baseline_schema_bytes,
            "avg_durable_records_per_trace": _mean(durable_counts),
            "cost_usd_available": False,
            "avg_cost_usd_per_trace": None,
        },
        "per_trace": rows,
    }


def _format_ms(value: float) -> str:
    """Format a millisecond value for Markdown."""
    return f"{value:.1f} ms"


def _format_bytes(value: float) -> str:
    """Format a byte count compactly for Markdown."""
    return f"{value:,.0f} bytes"


def render_markdown(report: dict[str, Any]) -> str:
    """Render the ingestion cost/performance report as Markdown."""
    headline = report["results"]["headline"]
    lines = [
        "# Lerim Trace Ingestion Cost/Performance Benchmark",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Command: `{report.get('command', '')}`",
        f"- Dataset snapshot: `{report['dataset']['snapshot'] or report['dataset']['requested_revision']}`",
        f"- Source profile: `{report['source_profile']}`",
        f"- Traces evaluated: `{headline['trace_count']}`",
        f"- Model: `{report['model_provider']['llm_model']}`",
        "",
        "## Headline",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Passed traces | {headline['passed_trace_count']} / {headline['trace_count']} |",
        f"| Avg ingestion time | {_format_ms(float(headline['avg_ingestion_ms']))} |",
        f"| p95 ingestion time | {_format_ms(float(headline['p95_ingestion_ms']))} |",
        f"| Avg LLM calls per trace | {float(headline['avg_llm_calls_per_trace']):.1f} |",
        f"| Total LLM calls | {headline['total_llm_calls']} |",
        f"| Avg DB growth per trace | {_format_bytes(float(headline['avg_db_size_delta_bytes']))} |",
        f"| Cost per trace | {'not available' if not headline['cost_usd_available'] else headline['avg_cost_usd_per_trace']} |",
        "",
        "## Methodology Notes",
        "",
        "- Input traces are public LongMemEval-S haystack sessions normalized through Lerim's generic trace envelope.",
        "- Ingestion uses Lerim's DSPy trace-ingestion path with live LLM calls.",
        "- LLM call counts come from `TraceIngestionRunDetails.llm_calls`.",
        "- Database growth excludes empty schema initialization, then measures cumulative SQLite file-size deltas around each trace.",
        "- Cost is not inferred. It stays unavailable unless provider usage or billing data is measured.",
        "",
    ]
    return "\n".join(lines)


def write_outputs(report: dict[str, Any], output_dir: Path) -> None:
    """Write ingestion cost/performance benchmark artifacts."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    detail_lines = [
        json.dumps(row, sort_keys=True)
        for row in report["results"]["per_trace"]
    ]
    (output_dir / "details.jsonl").write_text(
        "\n".join(detail_lines) + ("\n" if detail_lines else ""),
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")


def run_benchmark(args: argparse.Namespace) -> Path:
    """Run the trace-ingestion cost/performance benchmark."""
    started_at = time.perf_counter()
    if args.limit <= 0:
        raise ValueError("limit_must_be_positive")
    dataset_path = resolve_dataset_path(args)
    raw_entries = load_dataset(dataset_path)
    filtered_entries, abstention_excluded_count = filter_entries(
        raw_entries,
        question_type=args.question_type,
    )
    selected = _selected_sessions(
        filtered_entries,
        max_traces=args.limit,
        offset=args.offset,
    )
    if len(selected) < args.limit:
        raise ValueError(f"not_enough_longmemeval_sessions:{len(selected)}:{args.limit}")

    output_dir = args.output_dir
    if output_dir is None:
        output_dir = Path("benchmarks/results/raw") / f"trace-ingestion-cost-{_timestamp_for_path()}"
    output_dir = output_dir.expanduser().resolve()

    with tempfile.TemporaryDirectory(prefix="lerim-ingestion-cost-") as raw_root:
        root = Path(raw_root)
        cfg = _isolated_config(root)
        ContextStore(cfg.context_db_path).initialize()
        baseline_schema_bytes = _db_size(cfg.context_db_path)
        trace_dir = root / "traces"
        rows: list[dict[str, Any]] = []
        for index, item in enumerate(selected):
            entry = item["entry"]
            trace_path = _write_longmemeval_trace(
                entry=entry,
                session_index=int(item["session_index"]),
                session_id=str(item["session_id"]),
                turns=list(item["turns"]),
                work_dir=trace_dir,
                source_profile=args.source_profile,
            )
            session_id = f"lme-ingest-{_safe_id(entry.question_id)}-{int(item['session_index']):03d}"
            row, _elapsed = _ingest_one_trace(
                cfg=cfg,
                trace_path=trace_path,
                session_id=session_id,
                session_started_at=str(item["started_at"]),
                source_name="longmemeval-s",
                source_profile=args.source_profile,
                scope_type=args.scope_type,
                scope=args.scope,
                scope_label=args.scope_label,
                max_llm_calls=args.max_llm_calls,
                progress=args.progress,
            )
            row.update(
                {
                    "case_id": f"longmemeval_s:{entry.question_id}:{int(item['session_index']):03d}",
                    "question_id": entry.question_id,
                    "question_type": entry.question_type,
                    "haystack_session_index": int(item["session_index"]),
                    "haystack_session_id_hash": hashlib.sha256(
                        str(item["session_id"]).encode("utf-8")
                    ).hexdigest()[:16],
                    "source_profile": args.source_profile,
                    "scope_type": args.scope_type,
                    "scope_label": args.scope_label,
                }
            )
            rows.append(row)
            print(
                f"[{index + 1}/{len(selected)}] {row['status']} "
                f"llm_calls={row['llm_calls']} ingestion_ms={row['ingestion_ms']:.1f}",
                flush=True,
            )

    cfg = get_config()
    report = {
        "schema_version": 1,
        "benchmark": BENCHMARK_ID,
        "generated_at": _utc_now(),
        "command": " ".join(sys.argv),
        "offset": args.offset,
        "limit": args.limit,
        "question_type": args.question_type,
        "source_profile": args.source_profile,
        "scope_type": args.scope_type,
        "scope_label": args.scope_label,
        "dataset": {
            "repo_id": args.dataset_repo,
            "filename": args.dataset_file,
            "requested_revision": args.dataset_revision,
            "cache_ref": _dataset_cache_ref(dataset_path),
            "snapshot": _snapshot_from_dataset_path(dataset_path),
            "raw_entries": len(raw_entries),
            "filtered_entries": len(filtered_entries),
            "evaluated_traces": len(rows),
            "abstention_excluded": abstention_excluded_count,
        },
        "methodology": {
            "public_dataset": True,
            "trace_ingestion_path": "dspy",
            "llm_in_loop": True,
            "llm_calls_measured": True,
            "cost_usd_available": False,
            "cost_unavailable_reason": COST_UNAVAILABLE_REASON,
            "official_longmemeval_qa_score": False,
            "quality_score": False,
            "schema_initialization_excluded_from_trace_growth": True,
        },
        "model_provider": {
            "llm_provider": cfg.agent_role.provider,
            "llm_model": cfg.agent_role.model,
            "embedding_model": cfg.embedding_model_id,
        },
        "cost_estimate": {
            "available": False,
            "external_llm_usd": None,
            "notes": COST_UNAVAILABLE_REASON,
        },
        "environment": _environment_metadata(),
        "runtime": {
            "wall_clock_ms": (time.perf_counter() - started_at) * 1000,
            "failure_count": sum(1 for row in rows if row.get("status") != "pass"),
        },
        "required_artifacts": ["report.json", "report.md", "details.jsonl"],
        "results": summarize(rows, baseline_schema_bytes=baseline_schema_bytes),
    }
    write_outputs(report, output_dir)
    return output_dir


def parse_args() -> argparse.Namespace:
    """Parse trace-ingestion cost/performance benchmark arguments."""
    parser = argparse.ArgumentParser(
        description="Measure Lerim trace-ingestion cost and performance on LongMemEval-S traces."
    )
    parser.add_argument("--dataset-path", type=Path, default=None)
    parser.add_argument("--dataset-repo", default=DATASET_REPO_ID)
    parser.add_argument("--dataset-file", default=DATASET_FILENAME)
    parser.add_argument("--dataset-revision", default=None)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--question-type", default=None)
    parser.add_argument("--source-profile", default="support")
    parser.add_argument("--scope-type", default="domain")
    parser.add_argument("--scope", default="longmemeval-s-ingestion-cost")
    parser.add_argument("--scope-label", default="LongMemEval-S Ingestion Cost")
    parser.add_argument("--max-llm-calls", type=int, default=None)
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    """Run the benchmark from the command line."""
    output_dir = run_benchmark(parse_args())
    print(f"Trace ingestion cost/performance report written to {output_dir}")


if __name__ == "__main__":
    main()
