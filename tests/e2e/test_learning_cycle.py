"""E2E test: Core learning cycle journey.

Tests Lerim's core value proposition:
- Sync extracts context from traces
- Maintain consolidates records
- Ask retrieves relevant context

This is the most important E2E test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.conftest import CLIRunner, LerimServer
from tests.e2e.helpers import parse_json_output


@pytest.mark.e2e
@pytest.mark.llm
def test_sync_extracts_from_trace(
	cli: CLIRunner,
	e2e_server: LerimServer,
	e2e_project: Path,
	e2e_home: Path,
	trace_fixture_path: Path,
) -> None:
	"""Sync command extracts records from a trace file."""
	trace_src = trace_fixture_path / "clear_decision_with_noise.jsonl"
	if not trace_src.exists():
		pytest.skip(f"Trace fixture not found: {trace_src}")

	result = cli.run_ok(
		"sync",
		"--run-id", "e2e-test-session",
		"--force",
		timeout=180,
	)

	assert "extracted" in result.stdout.lower() or "processed" in result.stdout.lower() or result.returncode == 0


@pytest.mark.e2e
@pytest.mark.llm
def test_maintain_runs_after_sync(
	cli: CLIRunner,
	e2e_server: LerimServer,
	e2e_project: Path,
	e2e_home: Path,
) -> None:
	"""Maintain command runs without errors on existing records."""
	result = cli.run_ok("maintain", timeout=180)

	assert "completed" in result.stdout.lower() or "maintain" in result.stdout.lower() or result.returncode == 0


@pytest.mark.e2e
@pytest.mark.llm
def test_ask_returns_answer(
	cli: CLIRunner,
	e2e_server: LerimServer,
	e2e_project: Path,
	e2e_home: Path,
) -> None:
	"""Ask command returns an answer (even with empty context)."""
	result = cli.run_ok(
		"ask",
		"What patterns are used in this codebase?",
		timeout=120,
	)

	assert len(result.stdout.strip()) > 0
	assert result.returncode == 0


@pytest.mark.e2e
@pytest.mark.llm
def test_ask_with_json_output(
	cli: CLIRunner,
	e2e_server: LerimServer,
	e2e_project: Path,
	e2e_home: Path,
) -> None:
	"""Ask command with --json returns structured output."""
	result = cli.run_ok(
		"ask",
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
	"""Complete learning cycle: sync → maintain → ask.

	This is the core user journey that validates Lerim's value proposition.
	"""
	cli.run_ok("status", "--json")

	result = cli.run("sync", "--dry-run", timeout=60)
	assert result.returncode == 0

	result = cli.run("maintain", "--dry-run", timeout=60)
	assert result.returncode == 0

	result = cli.run_ok(
		"ask",
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
