"""Unit tests for api.py job queue functions and dashboard.py queue routes."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import lerim.server.api as api_mod
from lerim.server.api import api_queue_jobs, api_retry_job, api_skip_job
from tests.helpers import make_config


QUEUE_COUNTS = {"pending": 2, "running": 0, "done": 5, "failed": 1, "dead_letter": 0}


# ── api_retry_job ────────────────────────────────────────────────────


def test_api_retry_job_success(monkeypatch):
	"""retry_session_job returning True -> retried=True in result."""
	monkeypatch.setattr(api_mod, "retry_session_job", lambda run_id, **kw: True)
	monkeypatch.setattr(api_mod, "count_session_jobs_by_status", lambda: QUEUE_COUNTS)

	result = api_retry_job("abc-123")

	assert result["retried"] is True
	assert result["run_id"] == "abc-123"
	assert result["queue"] == QUEUE_COUNTS


def test_api_retry_job_not_found(monkeypatch):
	"""retry_session_job returning False -> retried=False in result."""
	monkeypatch.setattr(api_mod, "retry_session_job", lambda run_id, **kw: False)
	monkeypatch.setattr(api_mod, "count_session_jobs_by_status", lambda: QUEUE_COUNTS)

	result = api_retry_job("nonexistent-id")

	assert result["retried"] is False
	assert result["run_id"] == "nonexistent-id"
	assert "queue" in result


# ── api_skip_job ─────────────────────────────────────────────────────


def test_api_skip_job_success(monkeypatch):
	"""skip_session_job returning True -> skipped=True in result."""
	monkeypatch.setattr(api_mod, "skip_session_job", lambda run_id, **kw: True)
	monkeypatch.setattr(api_mod, "count_session_jobs_by_status", lambda: QUEUE_COUNTS)

	result = api_skip_job("abc-123")

	assert result["skipped"] is True
	assert result["run_id"] == "abc-123"
	assert result["queue"] == QUEUE_COUNTS


def test_api_skip_job_not_found(monkeypatch):
	"""skip_session_job returning False -> skipped=False in result."""
	monkeypatch.setattr(api_mod, "skip_session_job", lambda run_id, **kw: False)
	monkeypatch.setattr(api_mod, "count_session_jobs_by_status", lambda: QUEUE_COUNTS)

	result = api_skip_job("nonexistent-id")

	assert result["skipped"] is False
	assert result["run_id"] == "nonexistent-id"


# ── api_queue_jobs ───────────────────────────────────────────────────


def test_api_queue_jobs_returns_structure(monkeypatch):
	"""Result dict has jobs, total, queue keys with correct values."""
	sample_jobs = [
		{"run_id": "r1", "status": "pending"},
		{"run_id": "r2", "status": "failed"},
	]
	monkeypatch.setattr(
		api_mod, "list_queue_jobs",
		lambda **kw: sample_jobs,
	)
	monkeypatch.setattr(api_mod, "count_session_jobs_by_status", lambda: QUEUE_COUNTS)

	result = api_queue_jobs()

	assert result["jobs"] == sample_jobs
	assert result["total"] == 2
	assert result["queue"] == QUEUE_COUNTS


def test_api_queue_jobs_passes_filters(monkeypatch):
	"""status and registered project params are forwarded to list_queue_jobs."""
	captured = {}
	cfg = replace(
		make_config(Path("/tmp/test-api-queue-filters")),
		projects={"my-proj": "/tmp/repos/my-proj"},
	)

	def fake_list(**kwargs):
		captured.update(kwargs)
		return []

	monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
	monkeypatch.setattr(api_mod, "list_queue_jobs", fake_list)
	monkeypatch.setattr(api_mod, "count_session_jobs_by_status", lambda: QUEUE_COUNTS)

	api_queue_jobs(status="failed", project="my-proj")

	assert captured["status_filter"] == "failed"
	assert Path(str(captured["project_filter"])).resolve() == Path("/tmp/repos/my-proj").resolve()
	assert captured["project_exact"] is True
	assert captured["failed_only"] is True


def test_api_queue_jobs_no_filters_passes_none(monkeypatch):
	"""When called without filters, None is forwarded for both."""
	captured = {}

	def fake_list(**kwargs):
		captured.update(kwargs)
		return []

	monkeypatch.setattr(api_mod, "list_queue_jobs", fake_list)
	monkeypatch.setattr(api_mod, "count_session_jobs_by_status", lambda: QUEUE_COUNTS)

	api_queue_jobs()

	assert captured["status_filter"] is None
	assert captured["project_filter"] is None
	assert captured["failed_only"] is False


def test_api_queue_jobs_dead_letter_filter(monkeypatch):
	"""status='dead_letter' passes status_filter but failed_only=False."""
	captured = {}

	def fake_list(**kwargs):
		captured.update(kwargs)
		return [{"run_id": "dl1", "status": "dead_letter"}]

	monkeypatch.setattr(api_mod, "list_queue_jobs", fake_list)
	monkeypatch.setattr(api_mod, "count_session_jobs_by_status", lambda: QUEUE_COUNTS)

	result = api_queue_jobs(status="dead_letter")

	assert captured["status_filter"] == "dead_letter"
	assert captured["failed_only"] is False
	assert result["total"] == 1


def test_api_queue_jobs_resolves_exact_project_path(monkeypatch):
	"""project name resolves to exact repo_path filter for queue reads."""
	captured = {}
	cfg = replace(
		make_config(Path("/tmp/test-api-queue")),
		projects={"myproj": "/tmp/repos/myproj"},
	)

	def fake_list(**kwargs):
		captured.update(kwargs)
		return []

	monkeypatch.setattr(api_mod, "get_config", lambda: cfg)
	monkeypatch.setattr(api_mod, "list_queue_jobs", fake_list)
	monkeypatch.setattr(api_mod, "count_session_jobs_by_status", lambda: QUEUE_COUNTS)

	api_queue_jobs(project="myproj")

	assert Path(str(captured["project_filter"])).resolve() == Path("/tmp/repos/myproj").resolve()
	assert captured["project_exact"] is True
