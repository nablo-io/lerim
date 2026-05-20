"""Unit tests for public benchmark artifact validation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.scripts import build_report_index, validate_public_artifacts

LOCAL_USERS_PATH = "/" + "Users"


def _write_market_docs(root: Path, *, body: str | None = None) -> None:
    """Write minimal benchmark docs needed by the validator."""
    docs_dir = root / "docs" / "benchmarks"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "market-comparison.md").write_text(
        body
        or "\n".join(
            [
                "# Market Comparison",
                "",
                "## Where To Look",
                "",
                "[Lerim Results](lerim-results.md)",
                "",
                "## Current Market Snapshot",
                "",
                "third-party market-row source: <https://www.agent-memory.dev/>.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_public_report_manifest(root: Path, artifacts: list[str] | None = None) -> None:
    """Write the manifest of raw reports required for public benchmark docs."""
    raw_dir = root / "benchmarks" / "results" / "raw"
    manifest_path = root / "benchmarks" / "results" / "public-reports.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    if artifacts is None:
        artifacts = sorted(path.parent.name for path in raw_dir.glob("*/report.json"))
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "required_reports": [{"artifact": artifact} for artifact in artifacts],
            }
        ),
        encoding="utf-8",
    )


def _write_report_index(root: Path, *, body: str | None = None) -> None:
    """Write a minimal generated report index."""
    reports_dir = root / "benchmarks" / "results" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    if body is not None:
        (reports_dir / "index.md").write_text(body, encoding="utf-8")
        _write_public_report_manifest(root)
        return
    raw_dir = root / "benchmarks" / "results" / "raw"
    (reports_dir / "index.md").write_text(
        build_report_index.render_index(
            build_report_index.collect_reports(raw_dir, repo_root=root),
            raw_dir=raw_dir,
            reports_dir=reports_dir,
        ),
        encoding="utf-8",
    )
    _write_public_report_manifest(root)


def _write_generated_report_copies(root: Path) -> None:
    """Mirror raw report.md files into the generated report directory."""
    raw_dir = root / "benchmarks" / "results" / "raw"
    reports_dir = root / "benchmarks" / "results" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    for report_path in raw_dir.glob("*/report.json"):
        source_md = report_path.parent / "report.md"
        if not source_md.exists():
            continue
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("benchmark") == "imported_market_baselines":
            target_name = "imported-market-baselines.md"
        else:
            target_name = f"{report_path.parent.name}.md"
        (reports_dir / target_name).write_text(
            source_md.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    _write_report_index(root)


def _write_longmemeval_report(root: Path, *, include_report_md: bool = True) -> Path:
    """Write a minimal valid LongMemEval raw artifact."""
    report_dir = root / "benchmarks" / "results" / "raw" / "longmemeval-hybrid-full"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "benchmark": "longmemeval_s_retrieval_only",
        "generated_at": "2026-05-19T00:00:00+00:00",
        "command": "benchmarks/lerim_evidence/longmemeval.py --retrieval-mode hybrid",
        "is_full_filtered_run": True,
        "environment": {
            "git_commit": "abc123",
            "git_dirty": True,
            "python": "3.12.12",
        },
        "dataset": {
            "snapshot": "dataset-sha",
            "evaluated_entries": 1,
        },
        "methodology": {
            "retrieval_only": True,
            "llm_in_loop": False,
            "semantic_judge_in_loop": False,
            "official_longmemeval_qa_score": False,
        },
        "results": {
            "headline": {
                "count": 1,
                "recall_any_at_5": 0.95,
                "recall_any_at_10": 0.986,
                "recall_any_at_20": 0.994,
                "ndcg_at_10": 0.886,
                "mrr": 0.884,
            }
        },
    }
    (report_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (report_dir / "predictions.jsonl").write_text(
        json.dumps(
            {
                "recall_any_at_5": 0.95,
                "recall_any_at_10": 0.986,
                "recall_any_at_20": 0.994,
                "ndcg_at_10": 0.886,
                "mrr": 0.884,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    if include_report_md:
        (report_dir / "report.md").write_text("# Report\n", encoding="utf-8")
    return report_dir / "report.json"


def _judge_model_provider() -> dict[str, object]:
    """Return valid extraction judge metadata for fixture reports."""
    return {
        "agent_model": "MiniMax-M2.7",
        "agent_provider": "minimax",
        "judge_model": "MiniMax-M2.5",
        "llm_in_loop": True,
        "semantic_judge_in_loop": True,
    }


def _extraction_methodology(**extra: object) -> dict[str, object]:
    """Return valid extraction methodology metadata for fixture reports."""
    methodology = {
        "competitor_scores_available": False,
        "not_comparable_to_retrieval_only_scores": True,
    }
    methodology.update(extra)
    return methodology


def _write_valid_extraction_report(root: Path) -> Path:
    """Write a minimal valid extraction-quality raw artifact."""
    report_dir = root / "benchmarks" / "results" / "raw" / "extraction"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "benchmark": "lerim_extraction_quality_minimax_m27_full_47",
        "generated_at": "2026-05-19T00:00:00+00:00",
        "command": "benchmarks/scripts/import_extraction_full.py",
        "public_sanitized": True,
        "source_artifact": {"visibility": "private", "sha256": "abc123"},
        "environment": {"lerim_git_commit": "abc123", "lerim_git_dirty": True},
        "model_provider": _judge_model_provider(),
        "methodology": _extraction_methodology(),
        "required_artifacts": ["report.json", "report.md"],
    }
    (report_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (report_dir / "report.md").write_text("# Extraction\n", encoding="utf-8")
    return report_dir / "report.json"


def _write_trace_ingestion_cost_report(root: Path) -> Path:
    """Write a minimal valid trace-ingestion cost/performance artifact."""
    report_dir = root / "benchmarks" / "results" / "raw" / "trace-ingestion-cost"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "benchmark": "longmemeval_s_trace_ingestion_cost_performance",
        "generated_at": "2026-05-19T00:00:00+00:00",
        "command": "benchmarks/lerim_evidence/trace_ingestion_cost_performance.py",
        "environment": {"git_commit": "abc123", "git_dirty": True},
        "dataset": {"snapshot": "dataset-sha", "evaluated_traces": 1},
        "methodology": {
            "llm_in_loop": True,
            "llm_calls_measured": True,
            "cost_usd_available": False,
            "cost_unavailable_reason": "provider usage unavailable",
        },
        "cost_estimate": {"available": False, "external_llm_usd": None},
        "required_artifacts": ["report.json", "report.md", "details.jsonl"],
        "results": {
            "headline": {
                "trace_count": 1,
                "passed_trace_count": 1,
                "total_llm_calls": 5,
                "avg_llm_calls_per_trace": 5.0,
                "cost_usd_available": False,
                "avg_cost_usd_per_trace": None,
            }
        },
    }
    (report_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (report_dir / "report.md").write_text("# Trace Ingestion Cost\n", encoding="utf-8")
    (report_dir / "details.jsonl").write_text(
        json.dumps(
            {
                "llm_calls": 5,
                "status": "pass",
                "cost_usd": None,
                "cost_availability": "unavailable",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return report_dir / "report.json"


def _write_mcp_report(root: Path, name: str, *, summary: dict[str, object]) -> Path:
    """Write a minimal valid MCP integration artifact."""
    report_dir = root / "benchmarks" / "results" / "raw" / name
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "benchmark": "lerim_mcp_integration",
        "generated_at": "2026-05-19T00:00:00+00:00",
        "command": "benchmarks/lerim_evidence/integration.py",
        "is_full_integration_run": True,
        "environment": {"git_commit": "abc123", "git_dirty": True},
        "summary": summary,
        "required_artifacts": ["report.json", "report.md", "details.jsonl"],
    }
    detail_rows: list[dict[str, object]] = []
    for index in range(int(summary.get("config_probe_count") or summary["known_target_count"])):
        detail_rows.append(
            {
                "probe": "temp_config_writer_doctor",
                "target": f"target-{index}",
                "status": "pass" if index < int(summary["config_passed_count"]) else "fail",
            }
        )
    for _ in range(int(summary.get("stdio_tools_probe_count") or 1)):
        detail_rows.append({"probe": "stdio_mcp_tools_list", "status": "pass"})
    for _ in range(int(summary.get("local_context_tool_call_acceptance_count") or 0)):
        detail_rows.append({"probe": "stdio_mcp_context_brief_call", "status": "pass"})
    for _ in range(int(summary.get("trace_submit_idempotency_acceptance_count") or 0)):
        detail_rows.append({"probe": "stdio_mcp_trace_submit_duplicate", "status": "pass"})
    for _ in range(int(summary.get("trace_submit_extraction_acceptance_count") or 0)):
        detail_rows.append(
            {
                "probe": "stdio_mcp_trace_submit_extraction",
                "status": "pass",
                "input_trace_kind": "synthetic_protocol_acceptance_trace",
            }
        )
    for _ in range(int(summary.get("real_doctor_probe_count") or 0)):
        detail_rows.append(
            {
                "probe": "real_config_doctor",
                "target": "<redacted-installed-config-target>",
                "status": "pass",
            }
        )
    for _ in range(int(summary.get("installed_client_connection_acceptance_count") or 0)):
        detail_rows.append(
            {
                "probe": "real_installed_client_mcp_cli",
                "target": "<redacted-installed-client-target>",
                "status": "pass",
                "acceptance_scope": "real_installed_client_mcp_connection",
            }
        )
    for _ in range(int(summary.get("installed_client_tool_call_acceptance_count") or 0)):
        detail_rows.append({"probe": "real_installed_client_tool_call", "status": "pass"})
    report["summary"]["detail_count"] = len(detail_rows)
    (report_dir / "details.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in detail_rows),
        encoding="utf-8",
    )
    (report_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (report_dir / "report.md").write_text("# MCP\n", encoding="utf-8")
    return report_dir / "report.json"


def _valid_mcp_summary(**extra: object) -> dict[str, object]:
    """Return a valid MCP summary fixture for validator tests."""
    summary: dict[str, object] = {
        "known_target_count": 15,
        "known_targets": [
            "claude-code",
            "claude-desktop",
            "cline",
            "cline-cli",
            "codex",
            "cursor",
            "gemini-cli",
            "goose",
            "hermes",
            "kilo-code",
            "openclaw",
            "opencode",
            "openhuman",
            "roo-code",
            "windsurf",
        ],
        "config_passed_count": 15,
        "config_probe_count": 15,
        "detail_count": 23,
        "failure_count": 0,
        "blocker_count": 0,
        "stdio_tools_probe_count": 1,
        "stdio_passed_count": 1,
        "stdio_context_tool_probe_count": 1,
        "stdio_context_tool_passed_count": 1,
        "local_context_tool_call_acceptance_count": 1,
        "installed_client_connection_acceptance_count": 3,
        "installed_client_probe_count": 3,
        "installed_client_tool_call_acceptance_count": 1,
        "tool_call_probe_count": 1,
        "trace_submit_extraction_acceptance_count": 1,
        "trace_submit_idempotency_acceptance_count": 1,
        "stdio_trace_submit_probe_count": 2,
        "stdio_trace_submit_passed_count": 2,
        "real_doctor_probe_count": 0,
    }
    summary.update(extra)
    return summary


def _validate(root: Path, monkeypatch) -> validate_public_artifacts.ValidationResult:
    """Run validation against a temp repo root."""
    _write_generated_report_copies(root)
    monkeypatch.setattr(validate_public_artifacts, "_is_git_ignored", lambda path, *, cwd: False)
    return validate_public_artifacts.validate_public_artifacts(
        repo_root=root,
        raw_dir=root / "benchmarks" / "results" / "raw",
        reports_dir=root / "benchmarks" / "results" / "reports",
        docs_dir=root / "docs",
    )


def test_validate_public_artifacts_accepts_source_backed_tree(tmp_path: Path, monkeypatch) -> None:
    """A report with provenance, raw artifacts, docs, and index passes."""
    _write_longmemeval_report(tmp_path)
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)

    result = _validate(tmp_path, monkeypatch)

    assert result.ok, result.errors


def test_validate_public_artifacts_requires_generated_report_md(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Public numbers need a Markdown report beside the raw JSON."""
    report_path = _write_longmemeval_report(tmp_path, include_report_md=False)
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert f"{report_path}: missing required artifact `report.md`" in result.errors


def test_validate_public_artifacts_accepts_trace_ingestion_cost_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Trace-ingestion cost artifacts may report measured calls and unavailable cost."""
    _write_trace_ingestion_cost_report(tmp_path)
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)

    result = _validate(tmp_path, monkeypatch)

    assert result.ok, result.errors


def test_validate_public_artifacts_rejects_stale_support_boundary_svg(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The support-boundary graphic must stay tied to MCP raw artifacts."""
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    _write_mcp_report(
        tmp_path,
        "mcp-integration-full",
        summary=_valid_mcp_summary(installed_client_tool_call_acceptance_count=0),
    )
    _write_mcp_report(
        tmp_path,
        "mcp-gemini-live-tool-call",
        summary=_valid_mcp_summary(
            installed_client_connection_acceptance_count=1,
            trace_submit_extraction_acceptance_count=0,
        ),
    )
    docs_assets = tmp_path / "docs" / "assets"
    docs_assets.mkdir(parents=True, exist_ok=True)
    (docs_assets / "support-boundary.svg").write_text(
        "<svg>stale</svg>\n",
        encoding="utf-8",
    )

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any("support boundary SVG must be regenerated" in error for error in result.errors)


def test_validate_public_artifacts_rejects_inferred_trace_ingestion_cost(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Trace-ingestion cost artifacts cannot use zero as fake provider cost."""
    report_path = _write_trace_ingestion_cost_report(tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["results"]["headline"]["avg_cost_usd_per_trace"] = 0.0
    report_path.write_text(json.dumps(report), encoding="utf-8")
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert f"{report_path}: trace-ingestion headline must not infer avg cost" in result.errors


def test_validate_public_artifacts_rejects_longmemeval_without_no_judge_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Retrieval-only LongMemEval reports must state that they have no judge."""
    report_path = _write_longmemeval_report(tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report.pop("methodology")
    report_path.write_text(json.dumps(report), encoding="utf-8")
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert f"{report_path}: LongMemEval retrieval artifact must include methodology" in result.errors


def test_validate_public_artifacts_rejects_no_judge_report_marked_pass(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A no-judge report cannot look like a passing semantic evaluation."""
    report_path = _write_longmemeval_report(tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["overall_status"] = "pass"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert f"{report_path}: no-judge artifact cannot use overall_status=pass" in result.errors


def test_validate_public_artifacts_rejects_unexpected_market_sections(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The market comparison page must keep its approved market-wide structure."""
    _write_longmemeval_report(tmp_path)
    _write_report_index(tmp_path)
    _write_market_docs(
        tmp_path,
        body="# Market Comparison\n\n## Current Market Snapshot\n\n## One Competitor\n",
    )

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any("unexpected market comparison section" in error for error in result.errors)


def test_validate_public_artifacts_rejects_competitor_organizing_section(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Market comparison should stay market-wide, not competitor-organized."""
    _write_longmemeval_report(tmp_path)
    _write_report_index(tmp_path)
    _write_market_docs(
        tmp_path,
        body="# Market Comparison\n\n## Named Vendor\n\n## Current Market Snapshot\n\nLerim Results\n",
    )

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any("unexpected market comparison section" in error for error in result.errors)


def test_validate_public_artifacts_requires_manifest_for_external_market_numbers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Numeric third-party market rows need source-manifest coverage."""
    _write_longmemeval_report(tmp_path)
    _write_report_index(tmp_path)
    _write_market_docs(
        tmp_path,
        body="\n".join(
            [
                "# Market Comparison",
                "",
                "## Where To Look",
                "",
                "[Lerim Results](lerim-results.md)",
                "",
                "## Current Market Snapshot",
                "",
                "| System | Number |",
                "| --- | ---: |",
                "| AgentMemory | 95.2% |",
            ]
        ),
    )

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any("missing market source manifest" in error for error in result.errors)


def test_validate_public_artifacts_checks_market_source_claim_text(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Market source manifests must point at text present in the public page."""
    _write_longmemeval_report(tmp_path)
    _write_report_index(tmp_path)
    _write_market_docs(
        tmp_path,
        body="\n".join(
            [
                "# Market Comparison",
                "",
                "## Where To Look",
                "",
                "[Lerim Results](lerim-results.md)",
                "",
                "## Current Market Snapshot",
                "",
                "| System | Number |",
                "| --- | ---: |",
                "| AgentMemory | 95.2% |",
            ]
        ),
    )
    manifest_path = tmp_path / "benchmarks" / "results" / "market-sources.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sources": [
                    {
                        "id": "source",
                        "source_type": "pinned_public_docs",
                        "url": "https://example.com/benchmark",
                        "accessed": "2026-05-20",
                        "claims": [
                            {
                                "system": "AgentMemory",
                                "metric": "Retrieval R@5",
                                "value": "95.2%",
                                "market_page_text": "| AgentMemory | 94.0% |",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any("claim text not found" in error for error in result.errors)


def test_validate_public_artifacts_rejects_private_path_leaks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Public docs must not expose local machine paths."""
    _write_longmemeval_report(tmp_path)
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    (tmp_path / "docs" / "benchmarks" / "lerim-results.md").write_text(
        f"{LOCAL_USERS_PATH}/example/private/path\n",
        encoding="utf-8",
    )

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any("private/local-only path leaked" in error for error in result.errors)


def test_validate_public_artifacts_rejects_dirty_readme_benchmark_chart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """README cannot present polished benchmark art while reports are dirty."""
    _write_longmemeval_report(tmp_path)
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    (tmp_path / "README.md").write_text(
        '<img src="docs/assets/benchmark-summary.svg" alt="Benchmark chart">\n',
        encoding="utf-8",
    )

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any("do not embed benchmark-summary.svg" in error for error in result.errors)


def test_validate_public_artifacts_rejects_root_private_path_leaks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Release-public root docs are scanned for private local paths."""
    _write_longmemeval_report(tmp_path)
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    (tmp_path / "README.md").write_text(
        f"debug path: {LOCAL_USERS_PATH}/example/private/repo\n",
        encoding="utf-8",
    )

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any("README.md: private/local-only path leaked" in error for error in result.errors)


def test_validate_public_artifacts_rejects_test_fixture_private_path_leaks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Public test fixtures are scanned for private local paths."""
    _write_longmemeval_report(tmp_path)
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    fixture = tmp_path / "tests" / "fixtures" / "trace.jsonl"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text(
        f'{{"cwd": "{LOCAL_USERS_PATH}/example/private/repo"}}\n',
        encoding="utf-8",
    )

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any("trace.jsonl: private/local-only path leaked" in error for error in result.errors)


def test_validate_public_artifacts_rejects_missing_referenced_raw_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Benchmark docs cannot keep numbers after referenced raw reports disappear."""
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    docs_dir = tmp_path / "docs" / "benchmarks"
    (docs_dir / "lerim-results.md").write_text(
        "`benchmarks/results/raw/longmemeval-hybrid-full/report.json` R@5 95.0%\n",
        encoding="utf-8",
    )

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any("referenced raw report is missing" in error for error in result.errors)


def test_validate_public_artifacts_rejects_missing_manifest_required_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The public report manifest prevents launch-critical raw artifacts disappearing."""
    _write_longmemeval_report(tmp_path)
    _write_generated_report_copies(tmp_path)
    _write_public_report_manifest(
        tmp_path,
        ["longmemeval-hybrid-full", "context-budget-hybrid-full"],
    )
    _write_market_docs(tmp_path)
    monkeypatch.setattr(validate_public_artifacts, "_is_git_ignored", lambda path, *, cwd: False)

    result = validate_public_artifacts.validate_public_artifacts(
        repo_root=tmp_path,
        raw_dir=tmp_path / "benchmarks" / "results" / "raw",
        reports_dir=tmp_path / "benchmarks" / "results" / "reports",
        docs_dir=tmp_path / "docs",
    )

    assert not result.ok
    assert any("required raw report is missing" in error for error in result.errors)


def test_validate_public_artifacts_rejects_missing_generated_report_copy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Every raw report.md needs a generated public copy."""
    _write_longmemeval_report(tmp_path)
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    monkeypatch.setattr(validate_public_artifacts, "_is_git_ignored", lambda path, *, cwd: False)

    result = validate_public_artifacts.validate_public_artifacts(
        repo_root=tmp_path,
        raw_dir=tmp_path / "benchmarks" / "results" / "raw",
        reports_dir=tmp_path / "benchmarks" / "results" / "reports",
        docs_dir=tmp_path / "docs",
    )

    assert not result.ok
    assert any("missing generated report copy" in error for error in result.errors)


def test_validate_public_artifacts_rejects_stale_generated_report_copy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Generated report copies must mirror raw report.md files."""
    _write_longmemeval_report(tmp_path)
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    reports_dir = tmp_path / "benchmarks" / "results" / "reports"
    (reports_dir / "longmemeval-hybrid-full.md").write_text(
        "# Stale\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(validate_public_artifacts, "_is_git_ignored", lambda path, *, cwd: False)

    result = validate_public_artifacts.validate_public_artifacts(
        repo_root=tmp_path,
        raw_dir=tmp_path / "benchmarks" / "results" / "raw",
        reports_dir=reports_dir,
        docs_dir=tmp_path / "docs",
    )

    assert not result.ok
    assert any("generated report copy is stale" in error for error in result.errors)


@pytest.mark.parametrize(
    "relative_path",
    [
        "COMMERCIAL.md",
        "assets/README.md",
        "docs/assets/leak.svg",
        "benchmarks/README.md",
        "benchmarks/results/market-sources.json",
        "benchmarks/results/reports/leak.md",
    ],
)
def test_validate_public_artifacts_rejects_private_path_leaks_on_public_surfaces(
    tmp_path: Path,
    monkeypatch,
    relative_path: str,
) -> None:
    """Every public launch surface is scanned for local machine paths."""
    _write_longmemeval_report(tmp_path)
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    leak_path = tmp_path / relative_path
    leak_path.parent.mkdir(parents=True, exist_ok=True)
    leak_path.write_text(f"{LOCAL_USERS_PATH}/example/private/repo\n", encoding="utf-8")

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any(f"{leak_path}: private/local-only path leaked" in error for error in result.errors)


def test_validate_public_artifacts_rejects_stale_generated_report_index(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Generated report index content must mirror raw reports."""
    _write_longmemeval_report(tmp_path)
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    _write_generated_report_copies(tmp_path)
    reports_dir = tmp_path / "benchmarks" / "results" / "reports"
    (reports_dir / "index.md").write_text("# Stale index\n", encoding="utf-8")
    monkeypatch.setattr(validate_public_artifacts, "_is_git_ignored", lambda path, *, cwd: False)

    result = validate_public_artifacts.validate_public_artifacts(
        repo_root=tmp_path,
        raw_dir=tmp_path / "benchmarks" / "results" / "raw",
        reports_dir=reports_dir,
        docs_dir=tmp_path / "docs",
    )

    assert not result.ok
    assert any("generated benchmark index is stale" in error for error in result.errors)


def test_validate_public_artifacts_rejects_report_metric_drift_from_predictions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Headline metrics must recompute from prediction artifacts."""
    report_path = _write_longmemeval_report(tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["results"]["headline"]["recall_any_at_5"] = 0.50
    report_path.write_text(json.dumps(report), encoding="utf-8")
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any("headline `recall_any_at_5` does not match raw details" in error for error in result.errors)


def test_validate_public_artifacts_rejects_non_boolean_dirty_provenance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Dirty-worktree provenance must be typed, not string-shaped."""
    report_path = _write_longmemeval_report(tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["environment"]["git_dirty"] = "false"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert f"{report_path}: git dirty provenance must be a boolean" in result.errors


def test_validate_public_artifacts_accepts_clean_reports_from_source_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Launch-grade validation accepts a later artifact commit when sources did not change."""
    report_path = _write_longmemeval_report(tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["environment"]["git_commit"] = "source-head"
    report["environment"]["git_dirty"] = False
    report_path.write_text(json.dumps(report), encoding="utf-8")
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    _write_generated_report_copies(tmp_path)
    monkeypatch.setattr(validate_public_artifacts, "_is_git_ignored", lambda path, *, cwd: False)
    monkeypatch.setattr(validate_public_artifacts, "_git_head", lambda repo_root: "artifact-head")
    monkeypatch.setattr(
        validate_public_artifacts,
        "_git_source_changes_since",
        lambda repo_root, *, commit, current_head: (),
    )

    result = validate_public_artifacts.validate_public_artifacts(
        repo_root=tmp_path,
        raw_dir=tmp_path / "benchmarks" / "results" / "raw",
        reports_dir=tmp_path / "benchmarks" / "results" / "reports",
        docs_dir=tmp_path / "docs",
        require_clean=True,
    )

    assert result.ok, result.errors


def test_validate_public_artifacts_rejects_source_changes_after_clean_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Launch-grade validation rejects source changes after the benchmark source commit."""
    report_path = _write_longmemeval_report(tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["environment"]["git_commit"] = "source-head"
    report["environment"]["git_dirty"] = False
    report_path.write_text(json.dumps(report), encoding="utf-8")
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    _write_generated_report_copies(tmp_path)
    monkeypatch.setattr(validate_public_artifacts, "_is_git_ignored", lambda path, *, cwd: False)
    monkeypatch.setattr(validate_public_artifacts, "_git_head", lambda repo_root: "artifact-head")
    monkeypatch.setattr(
        validate_public_artifacts,
        "_git_source_changes_since",
        lambda repo_root, *, commit, current_head: ("src/lerim/context/store.py",),
    )

    result = validate_public_artifacts.validate_public_artifacts(
        repo_root=tmp_path,
        raw_dir=tmp_path / "benchmarks" / "results" / "raw",
        reports_dir=tmp_path / "benchmarks" / "results" / "reports",
        docs_dir=tmp_path / "docs",
        require_clean=True,
    )

    assert not result.ok
    assert any("benchmark source changed since report commit source-head" in error for error in result.errors)


def test_validate_public_artifacts_rejects_stale_lerim_result_number(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Hand-written Lerim benchmark docs must match the raw report values."""
    _write_longmemeval_report(tmp_path)
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    (tmp_path / "docs" / "benchmarks" / "lerim-results.md").write_text(
        "\n".join(
            [
                "# Lerim Results",
                "",
                "| Surface | Current result | Source |",
                "| --- | --- | --- |",
                (
                    "| LongMemEval-S retrieval, hybrid | R@5 90.0%, R@10 98.6%, "
                    "R@20 99.4%, NDCG@10 88.6%, MRR 88.4% | "
                    "`benchmarks/results/raw/longmemeval-hybrid-full/report.json` |"
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any("Lerim LongMemEval-S hybrid is stale" in error for error in result.errors)


def test_validate_public_artifacts_rejects_pinned_baseline_claiming_rerun(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Imported market baselines cannot silently become fresh local runs."""
    _write_longmemeval_report(tmp_path)
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    report_dir = tmp_path / "benchmarks" / "results" / "raw" / "market-baseline"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "benchmark": "imported_market_baselines",
        "generated_at": "2026-05-19T00:00:00+00:00",
        "command": "benchmarks/scripts/run_imported_market_baselines.py",
        "environment": {"lerim_git_commit": "abc123", "lerim_git_dirty": True},
        "agentmemory": {"rerun_in_this_environment": True},
    }
    (report_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (report_dir / "report.md").write_text("# Market Baseline\n", encoding="utf-8")

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any("must not claim local rerun" in error for error in result.errors)


def test_validate_public_artifacts_rejects_pinned_baseline_without_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Imported market baselines need source artifact provenance."""
    _write_longmemeval_report(tmp_path)
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    report_dir = tmp_path / "benchmarks" / "results" / "raw" / "market-baseline"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "benchmark": "imported_market_baselines",
        "generated_at": "2026-05-19T00:00:00+00:00",
        "command": "benchmarks/scripts/run_imported_market_baselines.py",
        "environment": {"lerim_git_commit": "abc123", "lerim_git_dirty": True},
        "agentmemory": {"rerun_in_this_environment": False},
        "results": [],
    }
    (report_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (report_dir / "report.md").write_text("# Market Baseline\n", encoding="utf-8")

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any("must include source_artifacts" in error for error in result.errors)


def test_validate_public_artifacts_rejects_pinned_baseline_digest_mismatch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Imported source digests must match the saved local source file."""
    _write_longmemeval_report(tmp_path)
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    report_dir = tmp_path / "benchmarks" / "results" / "raw" / "market-baseline"
    source_dir = report_dir / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "source.json").write_text('{"ok": true}\n', encoding="utf-8")
    report = {
        "benchmark": "imported_market_baselines",
        "generated_at": "2026-05-19T00:00:00+00:00",
        "command": "benchmarks/scripts/run_imported_market_baselines.py",
        "environment": {"lerim_git_commit": "abc123", "lerim_git_dirty": True},
        "agentmemory": {"rerun_in_this_environment": False},
        "source_artifacts": [
            {
                "source_path": "benchmark/data/source.json",
                "raw_url": "https://example.invalid/source.json",
                "local_path": "sources/source.json",
                "sha256": "not-the-file-digest",
            }
        ],
        "results": [{"source_path": "benchmark/data/source.json"}],
    }
    (report_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (report_dir / "report.md").write_text("# Market Baseline\n", encoding="utf-8")

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any("sha256 does not match local file" in error for error in result.errors)


def test_validate_public_artifacts_require_clean_allows_pinned_imported_baseline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Clean-worktree validation applies to local runs, not pinned imported baselines."""
    longmem_report_path = _write_longmemeval_report(tmp_path)
    longmem_report = json.loads(longmem_report_path.read_text(encoding="utf-8"))
    longmem_report["environment"]["git_dirty"] = False
    longmem_report_path.write_text(json.dumps(longmem_report), encoding="utf-8")
    report_dir = tmp_path / "benchmarks" / "results" / "raw" / "market-baseline"
    source_dir = report_dir / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    source_file = source_dir / "source.json"
    source_file.write_text('{"ok": true}\n', encoding="utf-8")
    report = {
        "benchmark": "imported_market_baselines",
        "generated_at": "2026-05-19T00:00:00+00:00",
        "command": "benchmarks/scripts/run_imported_market_baselines.py",
        "environment": {"lerim_git_commit": "abc123", "lerim_git_dirty": True},
        "agentmemory": {"rerun_in_this_environment": False},
        "source_artifacts": [
            {
                "source_path": "benchmark/data/source.json",
                "raw_url": "https://example.invalid/source.json",
                "local_path": "sources/source.json",
                "sha256": hashlib.sha256(source_file.read_bytes()).hexdigest(),
            }
        ],
        "results": [{"source_path": "benchmark/data/source.json"}],
    }
    (report_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (report_dir / "report.md").write_text("# Market Baseline\n", encoding="utf-8")
    _write_generated_report_copies(tmp_path)
    _write_market_docs(tmp_path)
    monkeypatch.setattr(validate_public_artifacts, "_is_git_ignored", lambda path, *, cwd: False)

    result = validate_public_artifacts.validate_public_artifacts(
        repo_root=tmp_path,
        raw_dir=tmp_path / "benchmarks" / "results" / "raw",
        reports_dir=tmp_path / "benchmarks" / "results" / "reports",
        docs_dir=tmp_path / "docs",
        require_clean=True,
    )

    assert not any(
        str(report_dir / "report.json") in error
        and "launch-grade validation requires git_dirty=false" in error
        for error in result.errors
    )


def test_validate_public_artifacts_rejects_extraction_source_report_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Public extraction artifacts must redact private source-report paths."""
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    report_dir = (
        tmp_path
        / "benchmarks"
        / "results"
        / "raw"
        / "false-positive-extraction-minimax-m27-negative-cases"
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "benchmark": "lerim_false_positive_extraction_minimax_m27_negative_cases",
        "generated_at": "2026-05-19T00:00:00+00:00",
        "command": "script.py --source-report /private/evals/full.json",
        "public_sanitized": True,
        "source_artifact": {"visibility": "private", "sha256": "abc123"},
        "environment": {"lerim_git_commit": "abc123", "lerim_git_dirty": True},
        "model_provider": _judge_model_provider(),
        "methodology": _extraction_methodology(competitor_scores_available=False),
        "dataset": {"negative_cases": 2},
        "required_artifacts": ["report.json", "report.md"],
    }
    (report_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (report_dir / "report.md").write_text("# False Positive\n", encoding="utf-8")

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any("extraction command must redact --source-report path" in error for error in result.errors)


def test_validate_public_artifacts_accepts_false_positive_extraction_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The false-positive extraction diagnostic is valid only with clear boundaries."""
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    report_dir = (
        tmp_path
        / "benchmarks"
        / "results"
        / "raw"
        / "false-positive-extraction-minimax-m27-negative-cases"
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "benchmark": "lerim_false_positive_extraction_minimax_m27_negative_cases",
        "generated_at": "2026-05-19T00:00:00+00:00",
        "command": "benchmarks/scripts/import_false_positive_extraction.py",
        "public_sanitized": True,
        "source_artifact": {"visibility": "private", "sha256": "abc123"},
        "environment": {"lerim_git_commit": "abc123", "lerim_git_dirty": True},
        "model_provider": _judge_model_provider(),
        "methodology": _extraction_methodology(competitor_scores_available=False),
        "dataset": {"negative_cases": 2},
        "required_artifacts": ["report.json", "report.md"],
    }
    (report_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (report_dir / "report.md").write_text("# False Positive\n", encoding="utf-8")

    result = _validate(tmp_path, monkeypatch)

    assert result.ok, result.errors


def test_validate_public_artifacts_rejects_false_positive_competitor_scores(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Private false-positive diagnostics cannot imply competitor results."""
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    report_dir = (
        tmp_path
        / "benchmarks"
        / "results"
        / "raw"
        / "false-positive-extraction-minimax-m27-negative-cases"
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "benchmark": "lerim_false_positive_extraction_minimax_m27_negative_cases",
        "generated_at": "2026-05-19T00:00:00+00:00",
        "command": "benchmarks/scripts/import_false_positive_extraction.py",
        "public_sanitized": True,
        "source_artifact": {"visibility": "private", "sha256": "abc123"},
        "environment": {"lerim_git_commit": "abc123", "lerim_git_dirty": True},
        "model_provider": _judge_model_provider(),
        "methodology": _extraction_methodology(competitor_scores_available=True),
        "dataset": {"negative_cases": 2},
        "required_artifacts": ["report.json", "report.md"],
    }
    (report_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (report_dir / "report.md").write_text("# False Positive\n", encoding="utf-8")

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any("must not imply competitor scores" in error for error in result.errors)


def test_validate_public_artifacts_rejects_extraction_competitor_scores(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Private extraction diagnostics cannot imply competitor results."""
    report_path = _write_valid_extraction_report(tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["methodology"]["competitor_scores_available"] = True
    report_path.write_text(json.dumps(report), encoding="utf-8")
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any("must not imply competitor scores" in error for error in result.errors)


def test_validate_public_artifacts_rejects_private_extraction_source_path_fields(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Public extraction artifacts keep private source provenance to digest metadata."""
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    report_dir = tmp_path / "benchmarks" / "results" / "raw" / "extraction"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "benchmark": "lerim_extraction_quality_minimax_m27_full_47",
        "generated_at": "2026-05-19T00:00:00+00:00",
        "command": "benchmarks/scripts/import_extraction_full.py",
        "public_sanitized": True,
        "source_artifact": {
            "visibility": "private",
            "sha256": "abc123",
            "path": "private/eval/report.json",
        },
        "environment": {"lerim_git_commit": "abc123", "lerim_git_dirty": True},
        "model_provider": _judge_model_provider(),
        "methodology": _extraction_methodology(),
        "required_artifacts": ["report.json", "report.md"],
    }
    (report_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (report_dir / "report.md").write_text("# Extraction\n", encoding="utf-8")

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any("exposes private path fields" in error for error in result.errors)


def test_validate_public_artifacts_rejects_extraction_without_judge_model(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Extraction-quality artifacts must record the semantic judge model."""
    report_path = _write_valid_extraction_report(tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["model_provider"].pop("judge_model")
    report_path.write_text(json.dumps(report), encoding="utf-8")
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert f"{report_path}: extraction artifact must include judge_model" in result.errors


def test_validate_public_artifacts_rejects_extraction_without_semantic_judge(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Extraction-quality artifacts cannot be published as judge-backed without a judge."""
    report_path = _write_valid_extraction_report(tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["model_provider"]["semantic_judge_in_loop"] = False
    report_path.write_text(json.dumps(report), encoding="utf-8")
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert (
        f"{report_path}: extraction artifact must set semantic_judge_in_loop=true"
        in result.errors
    )


def test_validate_public_artifacts_rejects_full_mcp_without_context_acceptance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A full MCP artifact must include the local context tool-call probe."""
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    report_dir = tmp_path / "benchmarks" / "results" / "raw" / "mcp-integration-full"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "benchmark": "lerim_mcp_integration",
        "generated_at": "2026-05-19T00:00:00+00:00",
        "command": "benchmarks/lerim_evidence/integration.py",
        "is_full_integration_run": True,
        "environment": {"git_commit": "abc123", "git_dirty": True},
        "summary": {
            "known_target_count": 15,
            "config_passed_count": 15,
            "stdio_tools_probe_count": 1,
            "stdio_passed_count": 1,
            "local_context_tool_call_acceptance_count": 0,
            "trace_submit_idempotency_acceptance_count": 1,
        },
        "required_artifacts": ["report.json", "report.md", "details.jsonl"],
    }
    (report_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (report_dir / "report.md").write_text("# MCP\n", encoding="utf-8")
    (report_dir / "details.jsonl").write_text("{}\n", encoding="utf-8")

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any("local context tool-call acceptance" in error for error in result.errors)


def test_validate_public_artifacts_rejects_full_mcp_without_trace_submit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A full MCP artifact must include trace-submit idempotency acceptance."""
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    report_dir = tmp_path / "benchmarks" / "results" / "raw" / "mcp-integration-full"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "benchmark": "lerim_mcp_integration",
        "generated_at": "2026-05-19T00:00:00+00:00",
        "command": "benchmarks/lerim_evidence/integration.py",
        "is_full_integration_run": True,
        "environment": {"git_commit": "abc123", "git_dirty": True},
        "summary": {
            "known_target_count": 15,
            "config_passed_count": 15,
            "stdio_tools_probe_count": 1,
            "stdio_passed_count": 1,
            "local_context_tool_call_acceptance_count": 1,
            "trace_submit_idempotency_acceptance_count": 0,
        },
        "required_artifacts": ["report.json", "report.md", "details.jsonl"],
    }
    (report_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (report_dir / "report.md").write_text("# MCP\n", encoding="utf-8")
    (report_dir / "details.jsonl").write_text("{}\n", encoding="utf-8")

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any("trace-submit idempotency acceptance" in error for error in result.errors)


def test_validate_public_artifacts_rejects_mcp_local_inventory_details(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Public MCP detail rows must not publish local installed-client inventory."""
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    report_path = _write_mcp_report(
        tmp_path,
        "mcp-integration-full",
        summary=_valid_mcp_summary(real_doctor_probe_count=1),
    )
    rows = [
        json.loads(line)
        for line in (report_path.parent / "details.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    for row in rows:
        if row.get("probe") == "real_config_doctor":
            row["target"] = "opencode"
            row["doctor"] = {"config_path": "/Users/example/.config/opencode/opencode.json"}
        if row.get("probe") == "real_installed_client_mcp_cli":
            row["target"] = "claude-code"
            row["command"] = ["claude", "mcp", "get", "lerim"]
    (report_path.parent / "details.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any("local inventory" in error for error in result.errors)


def test_validate_public_artifacts_rejects_undisclosed_synthetic_mcp_extraction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Trace-submit extraction acceptance must disclose the synthetic input trace."""
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    report_path = _write_mcp_report(
        tmp_path,
        "mcp-integration-full",
        summary=_valid_mcp_summary(installed_client_tool_call_acceptance_count=0),
    )
    rows = [
        json.loads(line)
        for line in (report_path.parent / "details.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    for row in rows:
        if row.get("probe") == "stdio_mcp_trace_submit_extraction":
            row.pop("input_trace_kind", None)
    (report_path.parent / "details.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    result = _validate(tmp_path, monkeypatch)

    assert not result.ok
    assert any("must disclose synthetic input" in error for error in result.errors)


def test_validate_public_artifacts_can_require_clean_worktree(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Launch-grade validation can reject dirty development artifacts."""
    report_path = _write_longmemeval_report(tmp_path)
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    monkeypatch.setattr(validate_public_artifacts, "_is_git_ignored", lambda path, *, cwd: False)

    result = validate_public_artifacts.validate_public_artifacts(
        repo_root=tmp_path,
        raw_dir=tmp_path / "benchmarks" / "results" / "raw",
        reports_dir=tmp_path / "benchmarks" / "results" / "reports",
        docs_dir=tmp_path / "docs",
        require_clean=True,
    )

    assert not result.ok
    assert f"{report_path}: launch-grade validation requires git_dirty=false" in result.errors


def test_validate_public_artifacts_can_require_tracked_public_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Release validation can reject release-critical files not added to git."""
    _write_longmemeval_report(tmp_path)
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    untracked_path = tmp_path / "docs" / "untracked.md"
    untracked_path.write_text("public doc\n", encoding="utf-8")
    monkeypatch.setattr(validate_public_artifacts, "_is_git_ignored", lambda path, *, cwd: False)
    monkeypatch.setattr(
        validate_public_artifacts,
        "_is_git_tracked",
        lambda path, *, cwd: path.name != "untracked.md",
    )

    result = validate_public_artifacts.validate_public_artifacts(
        repo_root=tmp_path,
        raw_dir=tmp_path / "benchmarks" / "results" / "raw",
        reports_dir=tmp_path / "benchmarks" / "results" / "reports",
        docs_dir=tmp_path / "docs",
        require_tracked_public_files=True,
    )

    assert not result.ok
    assert f"{untracked_path}: release-critical file is not tracked by git" in result.errors


def test_validate_public_artifacts_requires_tracked_release_preflight(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Release validation catches an untracked workflow helper script."""
    _write_longmemeval_report(tmp_path)
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    workflow = tmp_path / ".github" / "workflows" / "publish.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text("name: publish\n", encoding="utf-8")
    script = tmp_path / "scripts" / "release_preflight.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text('"""Release helper."""\n', encoding="utf-8")
    monkeypatch.setattr(validate_public_artifacts, "_is_git_ignored", lambda path, *, cwd: False)
    monkeypatch.setattr(
        validate_public_artifacts,
        "_is_git_tracked",
        lambda path, *, cwd: path.name != "release_preflight.py",
    )

    result = validate_public_artifacts.validate_public_artifacts(
        repo_root=tmp_path,
        raw_dir=tmp_path / "benchmarks" / "results" / "raw",
        reports_dir=tmp_path / "benchmarks" / "results" / "reports",
        docs_dir=tmp_path / "docs",
        require_tracked_public_files=True,
    )

    assert not result.ok
    assert f"{script}: release-critical file is not tracked by git" in result.errors


def test_validate_public_artifacts_requires_tracked_docker_release_inputs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Release validation catches untracked Docker inputs used by publishing."""
    _write_longmemeval_report(tmp_path)
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.12-slim\n", encoding="utf-8")
    dockerignore = tmp_path / ".dockerignore"
    dockerignore.write_text(".venv/\n", encoding="utf-8")
    monkeypatch.setattr(validate_public_artifacts, "_is_git_ignored", lambda path, *, cwd: False)
    monkeypatch.setattr(
        validate_public_artifacts,
        "_is_git_tracked",
        lambda path, *, cwd: path.name not in {"Dockerfile", ".dockerignore"},
    )

    result = validate_public_artifacts.validate_public_artifacts(
        repo_root=tmp_path,
        raw_dir=tmp_path / "benchmarks" / "results" / "raw",
        reports_dir=tmp_path / "benchmarks" / "results" / "reports",
        docs_dir=tmp_path / "docs",
        require_tracked_public_files=True,
    )

    assert not result.ok
    assert f"{dockerfile}: release-critical file is not tracked by git" in result.errors
    assert f"{dockerignore}: release-critical file is not tracked by git" in result.errors


def test_validate_public_artifacts_ignores_gitignored_release_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Ignored generated files do not become false release blockers."""
    _write_longmemeval_report(tmp_path)
    _write_report_index(tmp_path)
    _write_market_docs(tmp_path)
    _write_generated_report_copies(tmp_path)
    ignored_cache = tmp_path / "tests" / "__pycache__" / "ignored.pyc"
    ignored_cache.parent.mkdir(parents=True, exist_ok=True)
    ignored_cache.write_bytes(b"cache")
    monkeypatch.setattr(
        validate_public_artifacts,
        "_is_git_ignored",
        lambda path, *, cwd: path == ignored_cache,
    )
    monkeypatch.setattr(
        validate_public_artifacts,
        "_is_git_tracked",
        lambda path, *, cwd: path != ignored_cache,
    )

    result = validate_public_artifacts.validate_public_artifacts(
        repo_root=tmp_path,
        raw_dir=tmp_path / "benchmarks" / "results" / "raw",
        reports_dir=tmp_path / "benchmarks" / "results" / "reports",
        docs_dir=tmp_path / "docs",
        require_tracked_public_files=True,
    )

    assert result.ok, result.errors
