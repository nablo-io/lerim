"""E2E test: Multi-project isolation journey.

Tests that multiple projects have isolated context:
- Projects are registered separately
- Records are scoped to projects
- Answer can filter by project scope

Limitation: `project add/remove` commands write to ~/.lerim/config.toml
directly, not respecting LERIM_CONFIG. Tests that modify project
registration are skipped to avoid polluting user config.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.conftest import CLIRunner, LerimServer
from tests.e2e.helpers import parse_json_output


@pytest.mark.e2e
@pytest.mark.skip(reason="project add writes to global ~/.lerim/config.toml, not isolated config")
def test_add_multiple_projects(
	cli: CLIRunner,
	e2e_home: Path,
	tmp_path: Path,
) -> None:
	"""Multiple projects can be registered."""
	project_a = tmp_path / "project-alpha"
	project_b = tmp_path / "project-beta"
	project_a.mkdir()
	project_b.mkdir()

	cli.run_ok("project", "add", str(project_a))
	cli.run_ok("project", "add", str(project_b))

	result = cli.run_ok("project", "list")
	assert "alpha" in result.stdout.lower()
	assert "beta" in result.stdout.lower()


@pytest.mark.e2e
def test_project_list_json(
	cli: CLIRunner,
	e2e_project: Path,
	e2e_home: Path,
) -> None:
	"""Project list --json returns structured data."""
	result = cli.run_ok("project", "list", "--json")
	data = parse_json_output(result.stdout)
	assert len(data) >= 1 or "projects" in data


@pytest.mark.e2e
@pytest.mark.skip(reason="project add/remove writes to global ~/.lerim/config.toml, not isolated config")
def test_remove_one_project_keeps_other(
	cli: CLIRunner,
	e2e_home: Path,
	tmp_path: Path,
) -> None:
	"""Removing one project doesn't affect the other."""
	project_a = tmp_path / "project-alpha"
	project_b = tmp_path / "project-beta"
	project_a.mkdir()
	project_b.mkdir()

	cli.run_ok("project", "add", str(project_a))
	cli.run_ok("project", "add", str(project_b))
	cli.run_ok("project", "remove", project_a.name)

	result = cli.run_ok("project", "list")
	assert "alpha" not in result.stdout.lower()
	assert "beta" in result.stdout.lower()


@pytest.mark.e2e
@pytest.mark.llm
def test_status_with_project_scope(
	cli: CLIRunner,
	e2e_server: LerimServer,
	e2e_project: Path,
	e2e_home: Path,
) -> None:
	"""Status can be scoped to a specific project."""
	result = cli.run_ok(
		"status",
		"--scope", "project",
		"--project", e2e_project.name,
		"--json",
	)
	assert result.returncode == 0


@pytest.mark.e2e
@pytest.mark.llm
def test_answer_with_project_scope(
	cli: CLIRunner,
	e2e_server: LerimServer,
	e2e_project: Path,
	e2e_home: Path,
) -> None:
	"""Answer can be scoped to a specific project."""
	result = cli.run_ok(
		"answer",
		"What do we know?",
		"--scope", "project",
		"--project", e2e_project.name,
		timeout=120,
	)
	assert result.returncode == 0
	assert len(result.stdout.strip()) > 0


@pytest.mark.e2e
@pytest.mark.llm
def test_query_with_project_scope(
	cli: CLIRunner,
	e2e_server: LerimServer,
	e2e_project: Path,
	e2e_home: Path,
) -> None:
	"""Query can be scoped to a specific project."""
	result = cli.run_ok(
		"query", "records", "count",
		"--scope", "project",
		"--project", e2e_project.name,
		"--json",
	)
	assert result.returncode == 0
