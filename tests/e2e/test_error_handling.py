"""E2E test: Error handling and edge cases.

Tests that CLI handles errors gracefully:
- Invalid arguments
- Missing resources
- Connection failures
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.conftest import CLIRunner, LerimServer


@pytest.mark.e2e
def test_invalid_command(cli: CLIRunner, e2e_home) -> None:
	"""Invalid command shows usage help."""
	result = cli.run("not-a-real-command")
	assert result.returncode != 0


@pytest.mark.e2e
def test_project_add_nonexistent_path(cli: CLIRunner, e2e_home) -> None:
	"""Adding nonexistent project path fails gracefully."""
	result = cli.run("project", "add", "/nonexistent/path/that/does/not/exist")
	assert result.returncode != 0 or "error" in (result.stdout + result.stderr).lower()


@pytest.mark.e2e
def test_project_remove_nonexistent(cli: CLIRunner, e2e_home) -> None:
	"""Removing nonexistent project fails gracefully."""
	result = cli.run("project", "remove", "nonexistent-project-name")
	assert result.returncode != 0 or "not found" in (result.stdout + result.stderr).lower()


@pytest.mark.e2e
def test_ingest_with_invalid_run_id(
	cli: CLIRunner,
	e2e_server: LerimServer,
	e2e_home,
) -> None:
	"""Ingest with invalid run-id handles error gracefully."""
	result = cli.run("ingest", "--run-id", "nonexistent-session-id-12345")
	assert result.returncode == 0 or "not found" in (result.stdout + result.stderr).lower()


@pytest.mark.e2e
@pytest.mark.llm
def test_answer_empty_question(
	cli: CLIRunner,
	e2e_server: LerimServer,
	e2e_project: Path,
	e2e_home,
) -> None:
	"""Answer with empty question returns error or handles gracefully."""
	result = cli.run("answer", "", timeout=60)
	assert result.returncode == 0 or "error" in (result.stdout + result.stderr).lower() or len(result.stderr.strip()) > 0


@pytest.mark.e2e
def test_retry_nonexistent_job(cli: CLIRunner, e2e_home) -> None:
	"""Retry with nonexistent job ID fails gracefully."""
	result = cli.run("retry", "nonexistent-job-id")
	assert result.returncode != 0 or "not found" in (result.stdout + result.stderr).lower()


@pytest.mark.e2e
def test_skip_nonexistent_job(cli: CLIRunner, e2e_home) -> None:
	"""Skip with nonexistent job ID fails gracefully."""
	result = cli.run("skip", "nonexistent-job-id")
	assert result.returncode != 0 or "not found" in (result.stdout + result.stderr).lower()


@pytest.mark.e2e
def test_version_flag(cli: CLIRunner, e2e_home) -> None:
	"""--version flag shows version info."""
	result = cli.run("--version")
	assert result.returncode == 0
	assert "lerim" in result.stdout.lower() or len(result.stdout.strip()) > 0


@pytest.mark.e2e
def test_help_flag(cli: CLIRunner, e2e_home) -> None:
	"""--help flag shows usage info."""
	result = cli.run("--help")
	assert result.returncode == 0
	assert "usage" in result.stdout.lower() or "lerim" in result.stdout.lower()


@pytest.mark.e2e
def test_subcommand_help(cli: CLIRunner, e2e_home) -> None:
	"""Subcommand help works."""
	for cmd in ["project", "ingest", "curate", "answer", "status", "queue"]:
		result = cli.run(cmd, "--help")
		assert result.returncode == 0


@pytest.mark.e2e
def test_query_invalid_entity(
	cli: CLIRunner,
	e2e_server: LerimServer,
	e2e_home,
) -> None:
	"""Query with invalid entity fails gracefully."""
	result = cli.run("query", "invalid-entity", "count")
	assert result.returncode != 0


@pytest.mark.e2e
def test_connect_remove_nonexistent_platform(cli: CLIRunner, e2e_home) -> None:
	"""Removing nonexistent platform fails gracefully."""
	result = cli.run("connect", "remove", "nonexistent-platform")
	output = (result.stdout + result.stderr).lower()
	assert result.returncode != 0 or "not found" in output or "not connected" in output
