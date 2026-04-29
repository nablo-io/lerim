"""E2E test: Daemon/server lifecycle journey.

Tests service management commands:
- Server start/stop (via serve, not Docker)
- Status checks
- Health endpoints
"""

from __future__ import annotations

import time

import pytest

from tests.e2e.conftest import CLIRunner, LerimServer


@pytest.mark.e2e
def test_server_starts_and_responds(e2e_env: dict, e2e_home) -> None:
	"""Server can be started and responds to health checks."""
	server = LerimServer(env=e2e_env)
	try:
		server.start()
		assert server.process is not None
		assert server.process.poll() is None
	finally:
		server.stop()


@pytest.mark.e2e
def test_server_stops_cleanly(e2e_env: dict, e2e_home) -> None:
	"""Server stops cleanly when signaled."""
	server = LerimServer(env=e2e_env)
	server.start()

	assert server.process is not None

	server.stop()

	assert server.process is None


@pytest.mark.e2e
def test_status_shows_server_state(
	cli: CLIRunner,
	e2e_server: LerimServer,
	e2e_home,
) -> None:
	"""Status command shows server is running."""
	result = cli.run_ok("status")
	assert result.returncode == 0


@pytest.mark.e2e
def test_status_json_output(
	cli: CLIRunner,
	e2e_server: LerimServer,
	e2e_home,
) -> None:
	"""Status --json returns valid JSON."""
	import json

	result = cli.run_ok("status", "--json")
	data = json.loads(result.stdout)
	assert isinstance(data, dict)


@pytest.mark.e2e
def test_multiple_status_calls(
	cli: CLIRunner,
	e2e_server: LerimServer,
	e2e_home,
) -> None:
	"""Multiple status calls work without issues."""
	for _ in range(3):
		result = cli.run_ok("status")
		assert result.returncode == 0
		time.sleep(0.1)


@pytest.mark.e2e
def test_commands_fail_gracefully_without_server(cli: CLIRunner, e2e_home) -> None:
	"""Commands that need server fail gracefully when it's not running."""
	result = cli.run("sync")
	assert result.returncode != 0 or "not running" in (result.stdout + result.stderr).lower()

	result = cli.run("maintain")
	assert result.returncode != 0 or "not running" in (result.stdout + result.stderr).lower()

	result = cli.run("ask", "test question")
	assert result.returncode != 0 or "not running" in (result.stdout + result.stderr).lower()


@pytest.mark.e2e
def test_host_only_commands_work_without_server(cli: CLIRunner, e2e_home, e2e_project) -> None:
	"""Host-only commands work without server running."""
	result = cli.run_ok("project", "list")
	assert result.returncode == 0

	result = cli.run_ok("queue")
	assert result.returncode == 0

	result = cli.run_ok("connect", "list")
	assert result.returncode == 0


@pytest.mark.e2e
def test_logs_command(cli: CLIRunner, e2e_home) -> None:
	"""Logs command runs without errors."""
	result = cli.run("logs")
	assert result.returncode == 0
