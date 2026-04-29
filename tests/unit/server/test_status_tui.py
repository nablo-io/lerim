"""Tests for the status TUI renderer."""

from __future__ import annotations


from rich.console import Console
from rich.table import Table

from lerim.server.status_tui import (
    _format_queue_counts,
    _parse_iso,
    _project_next_action,
    _project_state,
    render_status_output,
)


class TestFormatQueueCounts:
    """Tests for _format_queue_counts."""

    def test_empty_queue(self):
        assert _format_queue_counts({}) == "empty"

    def test_all_zeros(self):
        assert _format_queue_counts({"pending": 0, "done": 0}) == "empty"

    def test_mixed_statuses(self):
        result = _format_queue_counts({"pending": 3, "running": 1, "done": 10})
        assert "1 running" in result
        assert "3 pending" in result
        assert "10 done" in result

    def test_ordered_output(self):
        result = _format_queue_counts({"done": 5, "pending": 2})
        assert result.index("pending") < result.index("done")


class TestProjectState:
    """Tests for _project_state."""

    def test_blocked_with_dead_letter_and_run_id(self):
        project = {
            "queue": {"dead_letter": 1},
            "oldest_blocked_run_id": "run-abc",
        }
        assert _project_state(project) == "blocked"

    def test_not_blocked_without_run_id(self):
        project = {
            "queue": {"dead_letter": 1},
            "oldest_blocked_run_id": "",
        }
        assert _project_state(project) != "blocked"

    def test_running(self):
        project = {"queue": {"running": 2}}
        assert _project_state(project) == "running"

    def test_queued(self):
        project = {"queue": {"pending": 5}}
        assert _project_state(project) == "queued"

    def test_quiet_with_records(self):
        project = {"queue": {}, "record_count": 10, "indexed_sessions_count": 0}
        assert _project_state(project) == "quiet"

    def test_quiet_with_done(self):
        project = {"queue": {"done": 3}, "record_count": 0}
        assert _project_state(project) == "quiet"

    def test_idle(self):
        project = {"queue": {}, "record_count": 0}
        assert _project_state(project) == "idle"


class TestProjectNextAction:
    """Tests for _project_next_action."""

    def test_blocked_with_run_id(self):
        project = {
            "name": "myproject",
            "queue": {"dead_letter": 1},
            "oldest_blocked_run_id": "run-123",
        }
        result = _project_next_action(project)
        assert "lerim retry run-123" in result
        assert "lerim skip run-123" in result

    def test_blocked_without_run_id(self):
        project = {
            "name": "myproject",
            "queue": {"dead_letter": 1},
            "oldest_blocked_run_id": "",
        }
        result = _project_next_action(project)
        assert "lerim retry --project myproject" in result

    def test_running(self):
        project = {"name": "myproject", "queue": {"running": 1}}
        result = _project_next_action(project)
        assert "lerim queue --project myproject" in result

    def test_queued(self):
        project = {"name": "myproject", "queue": {"pending": 3}}
        result = _project_next_action(project)
        assert "lerim queue --project myproject" in result

    def test_idle(self):
        project = {"name": "myproject", "queue": {}}
        assert _project_next_action(project) == "-"


class TestParseIso:
    """Tests for _parse_iso."""

    def test_valid_iso(self):
        result = _parse_iso("2026-04-19T12:30:00+00:00")
        assert result is not None
        assert result.year == 2026
        assert result.month == 4

    def test_z_suffix(self):
        result = _parse_iso("2026-04-19T12:30:00Z")
        assert result is not None
        assert result.tzinfo is not None

    def test_none_input(self):
        assert _parse_iso(None) is None

    def test_empty_string(self):
        assert _parse_iso("") is None

    def test_invalid_string(self):
        assert _parse_iso("not-a-date") is None


class TestRenderStatusOutput:
    """Tests for render_status_output."""

    def _render(self, result) -> str:
        console = Console(width=200, legacy_windows=False)
        with console.capture() as capture:
            console.print(result)
        return capture.get()

    def test_empty_status(self):
        result = render_status_output({}, refreshed_at="2026-04-19T12:00:00Z")
        assert result is not None
        tables = [r for r in result.renderables if isinstance(r, Table)]
        assert not any(t.title == "Project Streams" for t in tables)
        assert not any(t.title == "Blocked Streams" for t in tables)

    def test_with_projects(self):
        payload = {
            "projects": [
                {
                    "name": "proj-a",
                    "queue": {"running": 1},
                    "record_count": 5,
                    "oldest_blocked_run_id": "",
                }
            ],
        }
        result = render_status_output(payload, refreshed_at="now")
        assert result is not None
        tables = [r for r in result.renderables if isinstance(r, Table)]
        assert any(t.title == "Project Streams" for t in tables)
        output = self._render(result)
        assert "proj-a" in output

    def test_with_blocked_project(self):
        payload = {
            "projects": [
                {
                    "name": "blocked-proj",
                    "queue": {"dead_letter": 1},
                    "oldest_blocked_run_id": "run-bad",
                    "record_count": 3,
                    "last_error": "timeout",
                }
            ],
        }
        result = render_status_output(payload, refreshed_at="now")
        assert result is not None
        tables = [r for r in result.renderables if isinstance(r, Table)]
        assert any(t.title == "Blocked Streams" for t in tables)
        output = self._render(result)
        assert "blocked-proj" in output
        assert "blocked" in output.lower()

    def test_with_queue_health_warning(self):
        payload = {
            "queue_health": {
                "degraded": True,
                "advice": "Queue processing is slow",
            },
        }
        result = render_status_output(payload, refreshed_at="now")
        assert result is not None
        output = self._render(result)
        assert "Queue processing is slow" in output

    def test_with_recent_activity(self):
        payload = {
            "recent_activity": [
                {
                    "op_type": "sync",
                    "status": "done",
                    "project_label": "proj-a",
                    "sessions_analyzed": 3,
                    "sessions_extracted": 2,
                    "sessions_failed": 0,
                    "records_created": 1,
                    "records_updated": 0,
                    "records_archived": 0,
                }
            ],
        }
        result = render_status_output(payload, refreshed_at="now")
        assert result is not None
        output = self._render(result)
        assert "proj-a" in output
        assert "sync" in output

    def test_with_schedule(self):
        payload = {
            "schedule": {
                "sync": {
                    "interval_minutes": 20,
                    "running": False,
                    "seconds_until_next": 90,
                    "next_due_at": "2026-04-26T12:30:00+00:00",
                },
                "maintain": {
                    "interval_minutes": 60,
                    "running": True,
                    "seconds_until_next": None,
                    "next_due_at": None,
                },
            }
        }
        result = render_status_output(payload, refreshed_at="now")
        output = self._render(result)
        assert "Sync interval" in output
        assert "every 20m; next in 1m 30s (12:30:00Z)" in output
        assert "Maintain interval" in output
        assert "every 1h; running now" in output

    def test_runtime_identity_is_rendered(self):
        payload = {"runtime": {"source": "local-build", "image": "lerim-lerim:local"}}
        result = render_status_output(payload, refreshed_at="now")
        output = self._render(result)
        assert "Runtime" in output
        assert "local-build (lerim-lerim:local)" in output
