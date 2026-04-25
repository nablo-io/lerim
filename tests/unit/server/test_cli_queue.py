"""Tests for queue/retry/skip CLI commands and their helper functions.

Covers parser registrations, helper functions (_relative_time, _format_queue_counts),
and command handlers (_cmd_queue, _cmd_retry, _cmd_skip) with mocked catalog.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

import pytest

from lerim.server import cli


QUEUE_COUNTS = {"pending": 0, "running": 0, "done": 0, "failed": 0, "dead_letter": 0}


def _get_subparser_names() -> set[str]:
	"""Extract subcommand names from build_parser()."""
	parser = cli.build_parser()
	for action in parser._subparsers._actions:
		if isinstance(action, argparse._SubParsersAction):
			return set(action.choices.keys())
	return set()


# ── Parser registration tests ─────────────────────────────────────────


def test_queue_parser_exists() -> None:
	"""'queue' is registered as a subcommand in build_parser()."""
	assert "queue" in _get_subparser_names()


def test_retry_parser_exists() -> None:
	"""'retry' is registered as a subcommand in build_parser()."""
	assert "retry" in _get_subparser_names()


def test_skip_parser_exists() -> None:
	"""'skip' is registered as a subcommand in build_parser()."""
	assert "skip" in _get_subparser_names()


def test_queue_flags() -> None:
	"""Queue subparser recognises --failed, --status, --project."""
	parser = cli.build_parser()
	args = parser.parse_args(["queue", "--failed", "--status", "pending", "--project", "foo"])
	assert args.command == "queue"
	assert args.failed is True
	assert args.status == "pending"
	assert args.project == "foo"


def test_retry_flags() -> None:
	"""Retry subparser recognises run_id positional, --project, --all."""
	parser = cli.build_parser()
	args = parser.parse_args(["retry", "abc123def456"])
	assert args.command == "retry"
	assert args.run_id == "abc123def456"

	args_proj = parser.parse_args(["retry", "--project", "my-proj"])
	assert args_proj.project == "my-proj"

	args_all = parser.parse_args(["retry", "--all"])
	assert getattr(args_all, "all") is True


def test_skip_flags() -> None:
	"""Skip subparser recognises run_id positional, --project, --all."""
	parser = cli.build_parser()
	args = parser.parse_args(["skip", "abc123def456"])
	assert args.command == "skip"
	assert args.run_id == "abc123def456"

	args_proj = parser.parse_args(["skip", "--project", "my-proj"])
	assert args_proj.project == "my-proj"

	args_all = parser.parse_args(["skip", "--all"])
	assert getattr(args_all, "all") is True


# ── Helper function tests ────────────────────────────────────────────


def test_relative_time_seconds() -> None:
	"""Timestamps under 60s ago format as '<N>s ago'."""
	ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
	result = cli._relative_time(ts)
	assert result.endswith("s ago")
	num = int(result.replace("s ago", ""))
	assert 25 <= num <= 35


def test_relative_time_minutes() -> None:
	"""Timestamps 1-59 minutes ago format as '<N>m ago'."""
	ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
	result = cli._relative_time(ts)
	assert result.endswith("m ago")
	num = int(result.replace("m ago", ""))
	assert 4 <= num <= 6


def test_relative_time_hours() -> None:
	"""Timestamps 1-23 hours ago format as '<N>h ago'."""
	ts = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
	result = cli._relative_time(ts)
	assert result.endswith("h ago")
	num = int(result.replace("h ago", ""))
	assert 2 <= num <= 4


def test_relative_time_days() -> None:
	"""Timestamps 1+ days ago format as '<N>d ago'."""
	ts = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
	result = cli._relative_time(ts)
	assert result.endswith("d ago")
	num = int(result.replace("d ago", ""))
	assert 6 <= num <= 8


def test_relative_time_invalid() -> None:
	"""Garbage input returns '?'."""
	assert cli._relative_time("not-a-timestamp") == "?"
	assert cli._relative_time("") == "?"


def test_format_queue_counts() -> None:
	"""Non-zero counts are formatted as comma-separated pairs."""
	counts = {"pending": 3, "running": 1, "done": 10, "failed": 0, "dead_letter": 2}
	result = cli._format_queue_counts(counts)
	assert "3 pending" in result
	assert "1 running" in result
	assert "10 done" in result
	assert "2 dead_letter" in result
	# zero-value statuses are omitted
	assert "failed" not in result


def test_format_queue_counts_empty() -> None:
	"""All-zero counts return 'empty'."""
	assert cli._format_queue_counts(QUEUE_COUNTS) == "empty"
	assert cli._format_queue_counts({}) == "empty"


def test_format_queue_counts_order() -> None:
	"""Counts follow the fixed order: pending, running, done, failed, dead_letter."""
	counts = {"dead_letter": 1, "pending": 2, "done": 3, "running": 4, "failed": 5}
	result = cli._format_queue_counts(counts)
	parts = result.split(", ")
	labels = [p.split(" ", 1)[1] for p in parts]
	assert labels == ["pending", "running", "done", "failed", "dead_letter"]


# ── Command behaviour tests (_cmd_queue) ─────────────────────────────


def test_cmd_queue_empty(monkeypatch: pytest.MonkeyPatch) -> None:
	"""No jobs produces 'no jobs' message."""
	output: list[str] = []
	monkeypatch.setattr("lerim.server.cli._emit", lambda *a, **kw: output.append(str(a[0])))
	monkeypatch.setattr("lerim.sessions.catalog.list_queue_jobs", lambda **kw: [])
	monkeypatch.setattr("lerim.sessions.catalog.count_session_jobs_by_status", lambda: QUEUE_COUNTS)

	args = argparse.Namespace(json=False, failed=False, status=None, project=None)
	rc = cli._cmd_queue(args)

	assert rc == 0
	assert any("no jobs" in line for line in output)


def test_cmd_queue_json(monkeypatch: pytest.MonkeyPatch) -> None:
	"""With --json and jobs, output is valid JSON with jobs/total/queue keys."""
	fake_jobs = [
		{
			"run_id": "abc123def456",
			"status": "dead_letter",
			"repo_path": "/tmp/proj",
			"agent_type": "claude",
			"updated_at": "2026-03-25T10:00:00+00:00",
			"error": "timeout",
		},
	]
	output: list[str] = []
	monkeypatch.setattr("lerim.server.cli._emit", lambda *a, **kw: output.append(str(a[0])))
	monkeypatch.setattr("lerim.sessions.catalog.list_queue_jobs", lambda **kw: fake_jobs)
	monkeypatch.setattr(
		"lerim.sessions.catalog.count_session_jobs_by_status",
		lambda: {"pending": 0, "running": 0, "done": 0, "failed": 0, "dead_letter": 1},
	)

	args = argparse.Namespace(json=True, failed=False, status=None, project=None)
	rc = cli._cmd_queue(args)

	assert rc == 0
	blob = json.loads("\n".join(output))
	assert blob["total"] == 1
	assert len(blob["jobs"]) == 1
	assert "queue" in blob


# ── Command behaviour tests (_cmd_retry) ──────────────────────────────


def test_cmd_retry_no_args(monkeypatch: pytest.MonkeyPatch) -> None:
	"""No run_id, --project, or --all returns exit 2."""
	output: list[str] = []
	monkeypatch.setattr("lerim.server.cli._emit", lambda *a, **kw: output.append(str(a[0])))

	args = argparse.Namespace(run_id=None, project=None, all=False)
	rc = cli._cmd_retry(args)

	assert rc == 2


def test_cmd_retry_prefix_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
	"""Unresolvable prefix returns exit 1."""
	output: list[str] = []
	monkeypatch.setattr("lerim.server.cli._emit", lambda *a, **kw: output.append(str(a[0])))
	monkeypatch.setattr("lerim.sessions.catalog.resolve_run_id_prefix", lambda p: None)

	args = argparse.Namespace(run_id="abc123def456", project=None, all=False)
	rc = cli._cmd_retry(args)

	assert rc == 1
	assert any("not found" in line.lower() or "ambiguous" in line.lower() for line in output)


def test_cmd_retry_prefix_too_short(monkeypatch: pytest.MonkeyPatch) -> None:
	"""Run ID under 6 chars returns exit 2."""
	output: list[str] = []
	monkeypatch.setattr("lerim.server.cli._emit", lambda *a, **kw: output.append(str(a[0])))

	args = argparse.Namespace(run_id="abc", project=None, all=False)
	rc = cli._cmd_retry(args)

	assert rc == 2
	assert any("6 char" in line for line in output)


def test_cmd_retry_success(monkeypatch: pytest.MonkeyPatch) -> None:
	"""Valid prefix + retry succeeds returns exit 0."""
	output: list[str] = []
	monkeypatch.setattr("lerim.server.cli._emit", lambda *a, **kw: output.append(str(a[0])))
	monkeypatch.setattr("lerim.sessions.catalog.resolve_run_id_prefix", lambda p: "abc123def456full")
	monkeypatch.setattr("lerim.sessions.catalog.retry_session_job", lambda rid: True)
	monkeypatch.setattr("lerim.sessions.catalog.count_session_jobs_by_status", lambda: QUEUE_COUNTS)

	args = argparse.Namespace(run_id="abc123", project=None, all=False)
	rc = cli._cmd_retry(args)

	assert rc == 0
	assert any("retried" in line.lower() for line in output)


def test_cmd_retry_all(monkeypatch: pytest.MonkeyPatch) -> None:
	"""--all retries dead_letter jobs through the uncapped catalog helper."""
	output: list[str] = []
	monkeypatch.setattr("lerim.server.cli._emit", lambda *a, **kw: output.append(str(a[0])))
	monkeypatch.setattr(
		"lerim.sessions.catalog.list_queue_jobs",
		lambda **kw: pytest.fail("--all retry should not list paginated jobs"),
	)
	monkeypatch.setattr(
		"lerim.sessions.catalog.retry_all_dead_letter_jobs",
		lambda: 55,
	)
	monkeypatch.setattr("lerim.sessions.catalog.count_session_jobs_by_status", lambda: QUEUE_COUNTS)

	args = argparse.Namespace(run_id=None, project=None, all=True)
	rc = cli._cmd_retry(args)

	assert rc == 0
	assert any("55" in line for line in output)


def test_cmd_retry_not_dead_letter(monkeypatch: pytest.MonkeyPatch) -> None:
	"""Retry returns False (job not dead_letter) gives exit 1."""
	output: list[str] = []
	monkeypatch.setattr("lerim.server.cli._emit", lambda *a, **kw: output.append(str(a[0])))
	monkeypatch.setattr("lerim.sessions.catalog.resolve_run_id_prefix", lambda p: "abc123full")
	monkeypatch.setattr("lerim.sessions.catalog.retry_session_job", lambda rid: False)

	args = argparse.Namespace(run_id="abc123", project=None, all=False)
	rc = cli._cmd_retry(args)

	assert rc == 1
	assert any("not in dead_letter" in line for line in output)


def test_cmd_retry_project(monkeypatch: pytest.MonkeyPatch) -> None:
	"""--project retries all dead_letter jobs for a resolved project."""
	output: list[str] = []
	monkeypatch.setattr("lerim.server.cli._emit", lambda *a, **kw: output.append(str(a[0])))
	monkeypatch.setattr("lerim.server.cli._resolve_project_repo_path", lambda n: "/tmp/my-proj")
	monkeypatch.setattr("lerim.sessions.catalog.retry_project_jobs", lambda rp: 3)
	monkeypatch.setattr("lerim.sessions.catalog.count_session_jobs_by_status", lambda: QUEUE_COUNTS)

	args = argparse.Namespace(run_id=None, project="my-proj", all=False)
	rc = cli._cmd_retry(args)

	assert rc == 0
	assert any("3" in line for line in output)


def test_cmd_retry_project_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
	"""--project with unknown project returns exit 1."""
	output: list[str] = []
	monkeypatch.setattr("lerim.server.cli._emit", lambda *a, **kw: output.append(str(a[0])))
	monkeypatch.setattr("lerim.server.cli._resolve_project_repo_path", lambda n: None)

	args = argparse.Namespace(run_id=None, project="nope", all=False)
	rc = cli._cmd_retry(args)

	assert rc == 1
	assert any("not found" in line.lower() for line in output)


# ── Command behaviour tests (_cmd_skip) ──────────────────────────────


def test_cmd_skip_no_args(monkeypatch: pytest.MonkeyPatch) -> None:
	"""No run_id, --project, or --all returns exit 2."""
	output: list[str] = []
	monkeypatch.setattr("lerim.server.cli._emit", lambda *a, **kw: output.append(str(a[0])))

	args = argparse.Namespace(run_id=None, project=None, all=False)
	rc = cli._cmd_skip(args)

	assert rc == 2


def test_cmd_skip_success(monkeypatch: pytest.MonkeyPatch) -> None:
	"""Valid prefix + skip succeeds returns exit 0."""
	output: list[str] = []
	monkeypatch.setattr("lerim.server.cli._emit", lambda *a, **kw: output.append(str(a[0])))
	monkeypatch.setattr("lerim.sessions.catalog.resolve_run_id_prefix", lambda p: "abc123def456full")
	monkeypatch.setattr("lerim.sessions.catalog.skip_session_job", lambda rid: True)
	monkeypatch.setattr("lerim.sessions.catalog.count_session_jobs_by_status", lambda: QUEUE_COUNTS)

	args = argparse.Namespace(run_id="abc123", project=None, all=False)
	rc = cli._cmd_skip(args)

	assert rc == 0
	assert any("skipped" in line.lower() for line in output)


def test_cmd_skip_all(monkeypatch: pytest.MonkeyPatch) -> None:
	"""--all skips dead_letter jobs through the uncapped catalog helper."""
	output: list[str] = []
	monkeypatch.setattr("lerim.server.cli._emit", lambda *a, **kw: output.append(str(a[0])))
	monkeypatch.setattr(
		"lerim.sessions.catalog.list_queue_jobs",
		lambda **kw: pytest.fail("--all skip should not list paginated jobs"),
	)
	monkeypatch.setattr(
		"lerim.sessions.catalog.skip_all_dead_letter_jobs",
		lambda: 55,
	)
	monkeypatch.setattr("lerim.sessions.catalog.count_session_jobs_by_status", lambda: QUEUE_COUNTS)

	args = argparse.Namespace(run_id=None, project=None, all=True)
	rc = cli._cmd_skip(args)

	assert rc == 0
	assert any("55" in line for line in output)


def test_cmd_skip_prefix_too_short(monkeypatch: pytest.MonkeyPatch) -> None:
	"""Run ID under 6 chars returns exit 2."""
	output: list[str] = []
	monkeypatch.setattr("lerim.server.cli._emit", lambda *a, **kw: output.append(str(a[0])))

	args = argparse.Namespace(run_id="abc", project=None, all=False)
	rc = cli._cmd_skip(args)

	assert rc == 2
	assert any("6 char" in line for line in output)


def test_cmd_skip_prefix_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
	"""Unresolvable prefix returns exit 1."""
	output: list[str] = []
	monkeypatch.setattr("lerim.server.cli._emit", lambda *a, **kw: output.append(str(a[0])))
	monkeypatch.setattr("lerim.sessions.catalog.resolve_run_id_prefix", lambda p: None)

	args = argparse.Namespace(run_id="abc123def456", project=None, all=False)
	rc = cli._cmd_skip(args)

	assert rc == 1
	assert any("not found" in line.lower() or "ambiguous" in line.lower() for line in output)


def test_cmd_skip_not_dead_letter(monkeypatch: pytest.MonkeyPatch) -> None:
	"""Skip returns False (job not dead_letter) gives exit 1."""
	output: list[str] = []
	monkeypatch.setattr("lerim.server.cli._emit", lambda *a, **kw: output.append(str(a[0])))
	monkeypatch.setattr("lerim.sessions.catalog.resolve_run_id_prefix", lambda p: "abc123full")
	monkeypatch.setattr("lerim.sessions.catalog.skip_session_job", lambda rid: False)

	args = argparse.Namespace(run_id="abc123", project=None, all=False)
	rc = cli._cmd_skip(args)

	assert rc == 1
	assert any("not in dead_letter" in line for line in output)


def test_cmd_skip_project(monkeypatch: pytest.MonkeyPatch) -> None:
	"""--project skips all dead_letter jobs for a resolved project."""
	output: list[str] = []
	monkeypatch.setattr("lerim.server.cli._emit", lambda *a, **kw: output.append(str(a[0])))
	monkeypatch.setattr("lerim.server.cli._resolve_project_repo_path", lambda n: "/tmp/my-proj")
	monkeypatch.setattr("lerim.sessions.catalog.skip_project_jobs", lambda rp: 2)
	monkeypatch.setattr("lerim.sessions.catalog.count_session_jobs_by_status", lambda: QUEUE_COUNTS)

	args = argparse.Namespace(run_id=None, project="my-proj", all=False)
	rc = cli._cmd_skip(args)

	assert rc == 0
	assert any("2" in line for line in output)


def test_cmd_skip_project_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
	"""--project with unknown project returns exit 1."""
	output: list[str] = []
	monkeypatch.setattr("lerim.server.cli._emit", lambda *a, **kw: output.append(str(a[0])))
	monkeypatch.setattr("lerim.server.cli._resolve_project_repo_path", lambda n: None)

	args = argparse.Namespace(run_id=None, project="nope", all=False)
	rc = cli._cmd_skip(args)

	assert rc == 1
	assert any("not found" in line.lower() for line in output)
