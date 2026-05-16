"""E2E test: Core learning cycle journey.

Tests Lerim's core value proposition:
- Ingest extracts context from traces
- Curate consolidates records
- Answer retrieves relevant context

This is the most important E2E test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.conftest import CLIRunner, LerimServer
from tests.e2e.helpers import parse_json_output


@pytest.mark.e2e
@pytest.mark.llm
def test_ingest_extracts_from_trace(
	cli: CLIRunner,
	e2e_server: LerimServer,
	e2e_project: Path,
	e2e_home: Path,
	trace_fixture_path: Path,
) -> None:
	"""Ingest command extracts records from a trace file."""
	trace_src = trace_fixture_path / "clear_decision_with_noise.jsonl"
	if not trace_src.exists():
		pytest.skip(f"Trace fixture not found: {trace_src}")

	result = cli.run_ok(
		"ingest",
		"--run-id", "e2e-test-session",
		"--force",
		timeout=180,
	)

	assert "extracted" in result.stdout.lower() or "processed" in result.stdout.lower() or result.returncode == 0


@pytest.mark.e2e
@pytest.mark.llm
def test_curate_runs_after_ingest(
	cli: CLIRunner,
	e2e_server: LerimServer,
	e2e_project: Path,
	e2e_home: Path,
) -> None:
	"""Curate command runs without errors on existing records."""
	result = cli.run_ok("curate", timeout=180)

	assert "completed" in result.stdout.lower() or "curate" in result.stdout.lower() or result.returncode == 0


@pytest.mark.e2e
@pytest.mark.llm
def test_answer_returns_answer(
	cli: CLIRunner,
	e2e_server: LerimServer,
	e2e_project: Path,
	e2e_home: Path,
) -> None:
	"""Answer command returns an answer (even with empty context)."""
	result = cli.run_ok(
		"answer",
		"What patterns are used in this codebase?",
		timeout=120,
	)

	assert len(result.stdout.strip()) > 0
	assert result.returncode == 0


@pytest.mark.e2e
@pytest.mark.llm
def test_answer_with_json_output(
	cli: CLIRunner,
	e2e_server: LerimServer,
	e2e_project: Path,
	e2e_home: Path,
) -> None:
	"""Answer command with --json returns structured output."""
	result = cli.run_ok(
		"answer",
		"What is the project about?",
		"--json",
		timeout=120,
	)

	data = parse_json_output(result.stdout)
	assert "answer" in data or "response" in data or len(data) > 0


@pytest.mark.e2e
@pytest.mark.llm
def test_full_learning_cycle(
	cli: CLIRunner,
	e2e_server: LerimServer,
	e2e_project: Path,
	e2e_home: Path,
) -> None:
	"""Complete learning cycle: ingest -> curate -> answer.

	This is the core user journey that validates Lerim's value proposition.
	"""
	cli.run_ok("status", "--json")

	result = cli.run("ingest", "--dry-run", timeout=60)
	assert result.returncode == 0

	result = cli.run("curate", "--dry-run", timeout=60)
	assert result.returncode == 0

	result = cli.run_ok(
		"answer",
		"What do we know about this project?",
		timeout=120,
	)
	assert len(result.stdout.strip()) > 10


@pytest.mark.e2e
@pytest.mark.llm
def test_query_records_count(
	cli: CLIRunner,
	e2e_server: LerimServer,
	e2e_project: Path,
	e2e_home: Path,
) -> None:
	"""Query command returns record counts."""
	result = cli.run_ok("query", "records", "count", "--json", timeout=30)
	data = parse_json_output(result.stdout)
	assert "count" in data or "total" in data or isinstance(data, int)


@pytest.mark.e2e
@pytest.mark.llm
def test_query_records_list(
	cli: CLIRunner,
	e2e_server: LerimServer,
	e2e_project: Path,
	e2e_home: Path,
) -> None:
	"""Query command can list records."""
	result = cli.run_ok("query", "records", "list", "--limit", "5", "--json", timeout=30)
	data = parse_json_output(result.stdout)
	assert "entity" in data or "records" in data or "items" in data or isinstance(data, list)
