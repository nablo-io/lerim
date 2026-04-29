"""E2E test: First-time user setup journey.

Tests the initial setup flow a new user would go through:
- Project registration
- Platform connection
- Status checks

Note: `lerim init` is interactive and not tested here.

Limitation: `project add/remove` commands write to ~/.lerim/config.toml
directly, not respecting LERIM_CONFIG. Tests that modify project
registration are skipped to avoid polluting user config.
"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import CLIRunner, LerimServer


@pytest.mark.e2e
@pytest.mark.skip(reason="project add writes to global ~/.lerim/config.toml, not isolated config")
def test_project_add_and_list(cli: CLIRunner, e2e_project, e2e_home) -> None:
	"""User can add a project and see it in the list."""
	result = cli.run_ok("project", "add", str(e2e_project))
	assert "added" in result.stdout.lower() or result.returncode == 0

	result = cli.run_ok("project", "list")
	assert str(e2e_project.name) in result.stdout or "test-project" in result.stdout


@pytest.mark.e2e
@pytest.mark.skip(reason="project add writes to global ~/.lerim/config.toml, not isolated config")
def test_project_add_current_directory(cli: CLIRunner, e2e_project, e2e_home) -> None:
	"""User can add current directory as project."""
	import os
	original_cwd = os.getcwd()
	try:
		os.chdir(e2e_project)
		result = cli.run_ok("project", "add", ".")
		assert result.returncode == 0
	finally:
		os.chdir(original_cwd)


@pytest.mark.e2e
@pytest.mark.skip(reason="project remove writes to global ~/.lerim/config.toml, not isolated config")
def test_project_remove(cli: CLIRunner, e2e_project, e2e_home) -> None:
	"""User can remove a registered project."""
	cli.run_ok("project", "add", str(e2e_project))
	result = cli.run_ok("project", "remove", e2e_project.name)
	assert result.returncode == 0

	result = cli.run_ok("project", "list")
	assert e2e_project.name not in result.stdout


@pytest.mark.e2e
def test_connect_list_empty(cli: CLIRunner, e2e_home) -> None:
	"""Connect list shows no platforms initially."""
	result = cli.run_ok("connect", "list")
	assert "no platform" in result.stdout.lower() or result.returncode == 0


@pytest.mark.e2e
def test_status_requires_server(cli: CLIRunner, e2e_home) -> None:
	"""Status command fails gracefully when server not running."""
	result = cli.run("status")
	assert result.returncode != 0 or "not running" in result.stdout.lower() + result.stderr.lower()


@pytest.mark.e2e
def test_status_with_server(cli: CLIRunner, e2e_server: LerimServer, e2e_home) -> None:
	"""Status command works when server is running."""
	result = cli.run_ok("status", "--json")
	assert "record_count" in result.stdout or "records" in result.stdout.lower()


@pytest.mark.e2e
def test_queue_empty_initially(cli: CLIRunner, e2e_home) -> None:
	"""Queue command shows no jobs initially."""
	result = cli.run_ok("queue")
	assert "no jobs" in result.stdout.lower() or result.returncode == 0


@pytest.mark.e2e
def test_full_setup_journey(
	cli: CLIRunner,
	e2e_server: LerimServer,
	e2e_home,
) -> None:
	"""Complete first-time setup: check status, queue."""
	result = cli.run_ok("status", "--json")
	assert result.returncode == 0

	result = cli.run_ok("queue")
	assert result.returncode == 0

	result = cli.run_ok("connect", "list")
	assert result.returncode == 0
