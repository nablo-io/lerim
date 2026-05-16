"""Unit tests for CLI command handlers, helpers, and parser in lerim.server.cli.

Covers _cmd_* handlers, _emit/_emit_structured helpers, _wait_for_ready,
_hoist_global_json_flag, _parse_since, _fmt_log_line, _relative_time,
_format_queue_counts, _dead_letter_action (retry/skip), build_parser
subcommand parsing, and main() dispatch.

All external calls (HTTP, subprocess, filesystem, catalog imports) are mocked.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from lerim.server import cli
from tests.helpers import make_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ns(**kwargs: Any) -> argparse.Namespace:
    """Build an argparse.Namespace with defaults that most handlers expect."""
    defaults: dict[str, Any] = {"json": False}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _raise_api_error(*_args: Any, **_kwargs: Any) -> None:
    """Raise the explicit CLI API client failure used by command tests."""
    raise cli.ApiClientError(
        kind="unreachable",
        message="Lerim server is not reachable: refused",
    )


def _dated_log_file(root: Path) -> Path:
    """Return a test log path in Lerim's dated log layout."""
    path = root / "2026" / "03" / "01" / "lerim.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


# ===================================================================
# _emit / _emit_structured
# ===================================================================


class TestEmit:
    """Tests for the _emit output helper."""

    def test_emit_writes_to_stdout(self) -> None:
        """_emit writes a single line to stdout by default."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli._emit("hello world")
        assert buf.getvalue() == "hello world\n"

    def test_emit_writes_empty_line(self) -> None:
        """_emit with no message writes a blank line."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli._emit()
        assert buf.getvalue() == "\n"

    def test_emit_writes_to_custom_file(self) -> None:
        """_emit respects the file= keyword argument."""
        buf = io.StringIO()
        cli._emit("error msg", file=buf)
        assert buf.getvalue() == "error msg\n"


class TestEmitStructured:
    """Tests for _emit_structured (JSON vs human output)."""

    def test_json_mode(self) -> None:
        """When as_json=True, output is valid JSON."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli._emit_structured(title="T", payload={"a": 1}, as_json=True)
        parsed = json.loads(buf.getvalue())
        assert parsed == {"a": 1}

    def test_human_mode(self) -> None:
        """When as_json=False, output is title + key/value lines."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli._emit_structured(title="Status", payload={"x": 42}, as_json=False)
        lines = buf.getvalue().strip().split("\n")
        assert lines[0] == "Status"
        assert "- x: 42" in lines[1]


# ===================================================================
# _api_request_failed
# ===================================================================


class TestApiRequestFailed:
    """Tests for explicit API client failure rendering."""

    def test_returns_one(self) -> None:
        """_api_request_failed returns exit code 1."""
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._api_request_failed(
                cli.ApiClientError(
                    kind="unreachable", message="Lerim server is not reachable"
                )
            )
        assert code == 1

    def test_prints_to_stderr(self) -> None:
        """_api_request_failed writes the explicit client error to stderr."""
        buf = io.StringIO()
        with redirect_stderr(buf):
            cli._api_request_failed(
                cli.ApiClientError(
                    kind="unreachable", message="Lerim server is not reachable"
                )
            )
        assert "not reachable" in buf.getvalue().lower()


# ===================================================================
# _hoist_global_json_flag
# ===================================================================


class TestHoistGlobalJsonFlag:
    """Tests for the --json flag normaliser."""

    def test_no_json_flag(self) -> None:
        """Without --json the argv is returned unchanged."""
        raw = ["status"]
        assert cli._hoist_global_json_flag(raw) == ["status"]

    def test_json_after_subcommand(self) -> None:
        """--json after the subcommand is hoisted to the front."""
        raw = ["status", "--json"]
        result = cli._hoist_global_json_flag(raw)
        assert result[0] == "--json"
        assert "--json" not in result[1:]

    def test_json_already_first(self) -> None:
        """--json already at the front produces exactly one --json."""
        raw = ["--json", "status"]
        result = cli._hoist_global_json_flag(raw)
        assert result.count("--json") == 1
        assert result[0] == "--json"

    def test_multiple_json_flags_collapsed(self) -> None:
        """Multiple --json tokens are collapsed to one at the front."""
        raw = ["--json", "status", "--json"]
        result = cli._hoist_global_json_flag(raw)
        assert result.count("--json") == 1


# ===================================================================
# _relative_time
# ===================================================================


class TestRelativeTime:
    """Tests for the ISO -> relative-time formatter."""

    def test_seconds_ago(self) -> None:
        """A timestamp a few seconds old returns 'Ns ago'."""
        from datetime import datetime, timezone, timedelta

        ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        result = cli._relative_time(ts)
        assert result.endswith("s ago")

    def test_minutes_ago(self) -> None:
        """A timestamp a few minutes old returns 'Nm ago'."""
        from datetime import datetime, timezone, timedelta

        ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        result = cli._relative_time(ts)
        assert result.endswith("m ago")

    def test_hours_ago(self) -> None:
        """A timestamp a few hours old returns 'Nh ago'."""
        from datetime import datetime, timezone, timedelta

        ts = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        result = cli._relative_time(ts)
        assert result.endswith("h ago")

    def test_days_ago(self) -> None:
        """A timestamp several days old returns 'Nd ago'."""
        from datetime import datetime, timezone, timedelta

        ts = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        result = cli._relative_time(ts)
        assert result.endswith("d ago")

    def test_invalid_timestamp(self) -> None:
        """Invalid ISO strings return '?'."""
        assert cli._relative_time("not-a-date") == "?"

    def test_future_timestamp(self) -> None:
        """A future timestamp returns 'just now'."""
        from datetime import datetime, timezone, timedelta

        ts = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        result = cli._relative_time(ts)
        assert result == "just now"


# ===================================================================
# _format_queue_counts
# ===================================================================


class TestFormatQueueCounts:
    """Tests for the queue status summary formatter."""

    def test_empty_counts(self) -> None:
        """All-zero counts return 'empty'."""
        assert cli._format_queue_counts({}) == "empty"

    def test_single_status(self) -> None:
        """A single non-zero status is formatted."""
        result = cli._format_queue_counts({"pending": 3})
        assert result == "3 pending"

    def test_multiple_statuses(self) -> None:
        """Multiple statuses are joined with commas in canonical order."""
        result = cli._format_queue_counts({"done": 5, "pending": 2, "failed": 1})
        assert "2 pending" in result
        assert "5 done" in result
        assert "1 failed" in result

    def test_order_is_canonical(self) -> None:
        """Statuses appear in pending, running, done, failed, dead_letter order."""
        counts = {"dead_letter": 1, "pending": 2, "running": 3}
        result = cli._format_queue_counts(counts)
        parts = result.split(", ")
        labels = [p.split(" ")[1] for p in parts]
        assert labels == ["pending", "running", "dead_letter"]


# ===================================================================
# _parse_since
# ===================================================================


class TestParseSince:
    """Tests for the duration string parser."""

    def test_seconds(self) -> None:
        """Parsing '30s' returns 30."""
        assert cli._parse_since("30s") == 30.0

    def test_minutes(self) -> None:
        """Parsing '5m' returns 300."""
        assert cli._parse_since("5m") == 300.0

    def test_hours(self) -> None:
        """Parsing '2h' returns 7200."""
        assert cli._parse_since("2h") == 7200.0

    def test_days(self) -> None:
        """Parsing '1d' returns 86400."""
        assert cli._parse_since("1d") == 86400.0

    def test_invalid_format_raises(self) -> None:
        """Invalid duration strings raise ValueError."""
        with pytest.raises(ValueError, match="Invalid --since"):
            cli._parse_since("abc")

    def test_whitespace_tolerance(self) -> None:
        """Leading/trailing whitespace is tolerated."""
        assert cli._parse_since("  3h  ") == 10800.0


# ===================================================================
# _fmt_log_line
# ===================================================================


class TestFmtLogLine:
    """Tests for the log line formatter."""

    def test_plain_format(self) -> None:
        """Without color, output is 'HH:MM:SS | LEVEL | message'."""
        entry = {"ts": "2026-03-01T12:34:56Z", "level": "info", "message": "hello"}
        result = cli._fmt_log_line(entry, color=False)
        assert "12:34:56" in result
        assert "INFO" in result
        assert "hello" in result

    def test_color_format_contains_ansi(self) -> None:
        """With color, output contains ANSI escape codes."""
        entry = {"ts": "2026-03-01T12:34:56Z", "level": "error", "message": "boom"}
        result = cli._fmt_log_line(entry, color=True)
        assert "\033[" in result
        assert "boom" in result

    def test_missing_fields(self) -> None:
        """Missing fields default to empty strings."""
        entry = {}
        result = cli._fmt_log_line(entry, color=False)
        assert "|" in result


# ===================================================================
# _cmd_ingest
# ===================================================================


class TestCmdIngest:
    """Tests for the ingest command handler."""

    def test_ingest_success_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ingest with --json emits JSON response from API."""
        fake = {"indexed": 5, "extracted": 3}
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            cli,
            "_api_post",
            lambda path, body: captured.update({"path": path, "body": body}) or fake,
        )
        args = _ns(
            command="ingest",
            json=True,
            agent=None,
            window=None,
            max_sessions=None,
            force=False,
            dry_run=False,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_ingest(args)
        assert code == 0
        parsed = json.loads(buf.getvalue())
        assert parsed["indexed"] == 5
        assert captured["path"] == "/api/ingest"
        assert captured["body"]["blocking"] is True
        assert "ignore_lock" not in captured["body"]

    def test_ingest_success_human(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ingest without --json emits human-readable output."""
        fake = {"indexed": 2}
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            cli,
            "_api_post",
            lambda path, body: captured.update({"path": path, "body": body}) or fake,
        )
        args = _ns(
            command="ingest",
            json=False,
            agent="claude",
            window="7d",
            max_sessions=10,
            force=True,
            dry_run=False,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_ingest(args)
        assert code == 0
        assert "Ingest:" in buf.getvalue()
        assert captured["body"]["force"] is True
        assert captured["body"]["blocking"] is True

    def test_ingest_not_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Ingest returns 1 when the server is unreachable."""
        monkeypatch.setattr(cli, "_api_post", _raise_api_error)
        args = _ns(
            command="ingest",
            json=False,
            agent=None,
            window=None,
            max_sessions=None,
            force=False,
            dry_run=False,
        )
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_ingest(args)
        assert code == 1

    def test_ingest_prints_degraded_queue_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Human ingest output prints degraded queue warning block."""
        fake = {
            "indexed": 1,
            "queue_health": {"degraded": True, "advice": "run `lerim queue --failed`"},
        }
        monkeypatch.setattr(cli, "_api_post", lambda _p, _b: fake)
        args = _ns(
            command="ingest",
            json=False,
            agent=None,
            window=None,
            max_sessions=None,
            force=False,
            dry_run=False,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_ingest(args)
        assert code == 0
        text = buf.getvalue()
        assert "Queue degraded" in text
        assert "lerim queue --failed" in text


# ===================================================================
# _cmd_curate
# ===================================================================


class TestCmdCurate:
    """Tests for the curate command handler."""

    def test_curate_success_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Curate with --json emits JSON response."""
        fake = {"records_created": 1, "records_archived": 0}
        captured: dict[str, Any] = {}
        monkeypatch.setattr(
            cli,
            "_api_post",
            lambda path, body: captured.update({"path": path, "body": body}) or fake,
        )
        args = _ns(command="curate", json=True, force=False, dry_run=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_curate(args)
        assert code == 0
        parsed = json.loads(buf.getvalue())
        assert "records_created" in parsed
        assert captured["path"] == "/api/curate"
        assert captured["body"] == {"dry_run": False, "blocking": True}

    def test_curate_not_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Curate returns 1 when the server is unreachable."""
        monkeypatch.setattr(cli, "_api_post", _raise_api_error)
        args = _ns(command="curate", json=False, force=False, dry_run=False)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_curate(args)
        assert code == 1

    def test_curate_prints_degraded_queue_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Human curate output prints degraded queue warning block."""
        fake = {
            "projects": {},
            "queue_health": {"degraded": True, "advice": "run `lerim queue --failed`"},
        }
        monkeypatch.setattr(cli, "_api_post", lambda _p, _b: fake)
        args = _ns(command="curate", json=False, force=False, dry_run=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_curate(args)
        assert code == 0
        text = buf.getvalue()
        assert "Queue degraded" in text
        assert "lerim queue --failed" in text


# ===================================================================
# _cmd_dashboard
# ===================================================================


class TestCmdDashboard:
    """Tests for the dashboard command handler."""

    def test_dashboard_output(self) -> None:
        """Dashboard prints the cloud transition message and returns 0."""
        args = _ns(command="dashboard", port=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_dashboard(args)
        assert code == 0
        text = buf.getvalue()
        assert "lerim.dev" in text.lower() or "cloud" in text.lower()


# ===================================================================
# _cmd_answer
# ===================================================================


class TestCmdAnswer:
    """Tests for the answer command handler."""

    def test_answer_success_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Answer with --json emits the full response dict."""
        fake = {"answer": "Use JWT.", "error": False, "projects_used": []}
        monkeypatch.setattr(cli, "_api_post", lambda _p, _b: fake)
        args = _ns(command="answer", json=True, question="how?", limit=5)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_answer(args)
        assert code == 0
        parsed = json.loads(buf.getvalue())
        assert parsed["answer"] == "Use JWT."

    def test_answer_success_human(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Answer without --json prints the answer text only."""
        fake = {"answer": "Bearer tokens.", "error": False}
        monkeypatch.setattr(cli, "_api_post", lambda _p, _b: fake)
        args = _ns(command="answer", json=False, question="auth?", limit=5)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_answer(args)
        assert code == 0
        assert "Bearer tokens." in buf.getvalue()

    def test_answer_error_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Answer returns 1 when the API signals an error."""
        fake = {"answer": "auth error", "error": True}
        monkeypatch.setattr(cli, "_api_post", lambda _p, _b: fake)
        args = _ns(command="answer", json=False, question="q", limit=5)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_answer(args)
        assert code == 1

    def test_answer_not_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Answer returns 1 when server is unreachable."""
        monkeypatch.setattr(cli, "_api_post", _raise_api_error)
        args = _ns(command="answer", json=False, question="q", limit=5)
        code = cli._cmd_answer(args)
        assert code == 1


# ===================================================================
# _cmd_status
# ===================================================================


class TestCmdStatus:
    """Tests for the status command handler."""

    def test_status_live_delegates_to_live_handler(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Status --live delegates to the internal live status handler."""
        calls: list[argparse.Namespace] = []

        def _fake_status_live(ns: argparse.Namespace) -> int:
            calls.append(ns)
            return 0

        monkeypatch.setattr(cli, "_cmd_status_live", _fake_status_live)
        args = _ns(
            command="status",
            json=False,
            scope="project",
            project="lerim-cli",
            live=True,
            interval=1.5,
        )
        code = cli._cmd_status(args)
        assert code == 0
        assert len(calls) == 1
        assert calls[0].interval == 1.5
        assert calls[0].scope == "project"
        assert calls[0].project == "lerim-cli"

    def test_status_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Status with --json emits the full API payload as JSON."""
        fake = {
            "connected_agents": ["claude"],
            "record_count": 10,
            "sessions_indexed_count": 20,
            "queue": {"pending": 2, "done": 5},
        }
        monkeypatch.setattr(cli, "_api_get", lambda _p: fake)
        args = _ns(command="status", json=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_status(args)
        assert code == 0
        parsed = json.loads(buf.getvalue())
        assert parsed["record_count"] == 10

    def test_status_human(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Status without --json prints a human-readable summary."""
        fake = {
            "connected_agents": ["claude", "codex"],
            "record_count": 5,
            "sessions_indexed_count": 8,
            "queue": {"pending": 0},
        }
        monkeypatch.setattr(cli, "_api_get", lambda _p: fake)
        args = _ns(command="status", json=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_status(args)
        assert code == 0
        text = buf.getvalue()
        assert "Connected agents" in text
        assert "Context records" in text
        assert "What These Terms Mean" in text

    def test_status_dead_letter_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Status with dead_letter jobs prints a warning hint."""
        fake = {
            "connected_agents": [],
            "record_count": 0,
            "sessions_indexed_count": 0,
            "queue": {"dead_letter": 3},
        }
        monkeypatch.setattr(cli, "_api_get", lambda _p: fake)
        args = _ns(command="status", json=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_status(args)
        assert code == 0
        assert "dead_letter" in buf.getvalue()

    def test_status_queue_health_warning(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Status prints queue-health degraded block and advice."""
        fake = {
            "connected_agents": [],
            "record_count": 0,
            "sessions_indexed_count": 0,
            "queue": {},
            "scope": {"skipped_unscoped": 2},
            "queue_health": {
                "degraded": True,
                "stale_running_count": 1,
                "dead_letter_count": 2,
                "advice": "run `lerim queue --failed`",
            },
        }
        monkeypatch.setattr(cli, "_api_get", lambda _p: fake)
        args = _ns(command="status", json=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_status(args)
        assert code == 0
        text = buf.getvalue()
        assert "Queue health" in text
        assert "run `lerim queue --failed`" in text
        assert "What To Do Next" in text

    def test_status_not_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Status returns 1 when server is unreachable."""
        monkeypatch.setattr(cli, "_api_get", _raise_api_error)
        args = _ns(command="status", json=False)
        code = cli._cmd_status(args)
        assert code == 1


# ===================================================================
# _cmd_status_live
# ===================================================================


class TestCmdStatusLive:
    """Tests for the internal live status handler."""

    def test_status_render_output_human(self) -> None:
        """Shared status renderer includes key human-facing sections."""
        fake = {
            "connected_agents": ["claude", "codex"],
            "record_count": 5,
            "sessions_indexed_count": 10,
            "ingest_window_days": 7,
            "queue": {"pending": 2, "dead_letter": 1},
            "unscoped_sessions": {"total": 3, "by_agent": {"cursor": 3}},
            "queue_health": {"degraded": True, "advice": "inspect failed queue"},
            "projects": [
                {
                    "name": "lerim-cli",
                    "record_count": 0,
                    "indexed_sessions_count": 1,
                    "queue": {"done": 1},
                    "oldest_blocked_run_id": "agent-abc123456789",
                }
            ],
        }
        from rich.console import Console

        buf = io.StringIO()
        from lerim.server.status_tui import render_status_output

        Console(file=buf, width=120).print(
            render_status_output(fake, refreshed_at="2026-04-13 07:00:00Z")
        )
        text = buf.getvalue()
        assert "Lerim Status (" in text
        assert "Ingest window" in text
        assert "quiet" in text
        assert "Project Streams" in text
        assert "What These Terms Mean" in text
        assert "What To Do Next" in text

    def test_status_live_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Live status with --json emits a single status payload as JSON."""
        fake = {"queue": {"pending": 1}, "projects": []}
        monkeypatch.setattr(cli, "_api_get", lambda _p: fake)
        args = _ns(command="status", json=True, interval=2.0, scope="all", project=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_status_live(args)
        assert code == 0
        parsed = json.loads(buf.getvalue())
        assert parsed["queue"]["pending"] == 1

    def test_status_live_not_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Live status returns 1 when server is unreachable."""
        monkeypatch.setattr(cli, "_api_get", _raise_api_error)
        args = _ns(
            command="status", json=False, interval=1.0, scope="all", project=None
        )
        code = cli._cmd_status_live(args)
        assert code == 1


# ===================================================================
# _cmd_connect
# ===================================================================


class TestCmdConnect:
    """Tests for the connect command handler."""

    @pytest.fixture(autouse=True)
    def _disable_real_docker_restart(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cli, "is_docker_container_running", lambda: False)

    def test_connect_list_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Connect list with no platforms prints 'No platforms connected.'"""
        cfg = make_config(Path("/tmp/fake"))
        monkeypatch.setattr(cli, "get_config", lambda: cfg)
        monkeypatch.setattr(cli, "list_platforms", lambda _p: [])

        args = _ns(command="connect", platform_name="list")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_connect(args)
        assert code == 0
        assert "no platforms" in buf.getvalue().lower()

    def test_connect_list_with_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Connect list with platforms prints a summary."""
        cfg = make_config(Path("/tmp/fake"))
        monkeypatch.setattr(cli, "get_config", lambda: cfg)
        monkeypatch.setattr(
            cli,
            "list_platforms",
            lambda _p: [
                {
                    "name": "claude",
                    "path": "/home/.claude",
                    "session_count": 12,
                    "exists": True,
                },
            ],
        )

        args = _ns(command="connect", platform_name="list")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_connect(args)
        assert code == 0
        text = buf.getvalue()
        assert "claude" in text
        assert "12 sessions" in text

    def test_connect_none_action(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Connect with no platform_name defaults to list."""
        cfg = make_config(Path("/tmp/fake"))
        monkeypatch.setattr(cli, "get_config", lambda: cfg)
        monkeypatch.setattr(cli, "list_platforms", lambda _p: [])

        args = _ns(command="connect", platform_name=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_connect(args)
        assert code == 0

    def test_connect_auto(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Connect auto calls connect_platform for each known platform."""
        cfg = make_config(Path("/tmp/fake"))
        monkeypatch.setattr(cli, "get_config", lambda: cfg)
        monkeypatch.setattr(
            cli,
            "connect_platform",
            lambda _p, _n, custom_path=None: {"status": "connected"},
        )

        args = _ns(command="connect", platform_name="auto")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_connect(args)
        assert code == 0
        assert "auto connected" in buf.getvalue().lower()

    def test_connect_auto_restarts_running_container(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Connect auto restarts Docker when mounts changed."""
        cfg = make_config(Path("/tmp/fake"))
        monkeypatch.setattr(cli, "get_config", lambda: cfg)
        monkeypatch.setattr(
            cli,
            "connect_platform",
            lambda _p, _n, custom_path=None: {"status": "connected"},
        )
        monkeypatch.setattr(cli, "is_docker_container_running", lambda: True)
        monkeypatch.setattr(cli, "current_compose_uses_local_build", lambda: True)
        up_calls = []
        monkeypatch.setattr(cli, "api_up", lambda **kw: up_calls.append(kw) or {})

        args = _ns(command="connect", platform_name="auto")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_connect(args)
        assert code == 0
        assert up_calls == [{"build_local": True}]

    def test_connect_remove_no_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Connect remove without a name returns exit code 2."""
        cfg = make_config(Path("/tmp/fake"))
        monkeypatch.setattr(cli, "get_config", lambda: cfg)

        args = _ns(command="connect", platform_name="remove", extra_arg=None)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_connect(args)
        assert code == 2

    def test_connect_remove_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Connect remove with a valid name returns 0."""
        cfg = make_config(Path("/tmp/fake"))
        monkeypatch.setattr(cli, "get_config", lambda: cfg)
        monkeypatch.setattr(cli, "remove_platform", lambda _p, _n: True)

        args = _ns(command="connect", platform_name="remove", extra_arg="claude")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_connect(args)
        assert code == 0
        assert "removed" in buf.getvalue().lower()

    def test_connect_remove_restarts_running_container(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Removing a connected platform restarts Docker mounts when needed."""
        cfg = make_config(Path("/tmp/fake"))
        monkeypatch.setattr(cli, "get_config", lambda: cfg)
        monkeypatch.setattr(cli, "remove_platform", lambda _p, _n: True)
        monkeypatch.setattr(cli, "is_docker_container_running", lambda: True)
        monkeypatch.setattr(cli, "current_compose_uses_local_build", lambda: True)
        up_calls = []
        monkeypatch.setattr(cli, "api_up", lambda **kw: up_calls.append(kw) or {})

        args = _ns(command="connect", platform_name="remove", extra_arg="claude")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_connect(args)
        assert code == 0
        assert up_calls == [{"build_local": True}]

    def test_connect_remove_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Connect remove with unknown name returns 0 but says 'not connected'."""
        cfg = make_config(Path("/tmp/fake"))
        monkeypatch.setattr(cli, "get_config", lambda: cfg)
        monkeypatch.setattr(cli, "remove_platform", lambda _p, _n: False)

        args = _ns(command="connect", platform_name="remove", extra_arg="bogus")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_connect(args)
        assert code == 0
        assert "not connected" in buf.getvalue().lower()

    def test_connect_unknown_platform(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Connecting an unknown platform name returns 2."""
        cfg = make_config(Path("/tmp/fake"))
        monkeypatch.setattr(cli, "get_config", lambda: cfg)

        args = _ns(command="connect", platform_name="bogus_agent", path=None)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_connect(args)
        assert code == 2
        assert "unknown platform" in buf.getvalue().lower()

    def test_connect_known_platform_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Connecting a known platform prints success."""
        cfg = make_config(Path("/tmp/fake"))
        monkeypatch.setattr(cli, "get_config", lambda: cfg)
        monkeypatch.setattr(cli, "load_platforms", lambda _p: {"platforms": {}})
        monkeypatch.setattr(
            cli,
            "connect_platform",
            lambda _p, _n, custom_path=None: {
                "status": "connected",
                "path": "/home/.claude",
                "session_count": 5,
            },
        )

        args = _ns(command="connect", platform_name="claude", path=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_connect(args)
        assert code == 0
        assert "connected" in buf.getvalue().lower()

    def test_connect_known_platform_restarts_running_container_when_path_changes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Connecting a platform with a changed path should restart Docker mounts."""
        cfg = make_config(Path("/tmp/fake"))
        monkeypatch.setattr(cli, "get_config", lambda: cfg)
        monkeypatch.setattr(cli, "load_platforms", lambda _p: {"platforms": {}})
        monkeypatch.setattr(
            cli,
            "connect_platform",
            lambda _p, _n, custom_path=None: {
                "status": "connected",
                "path": "/home/.claude",
                "session_count": 5,
            },
        )
        monkeypatch.setattr(cli, "is_docker_container_running", lambda: True)
        monkeypatch.setattr(cli, "current_compose_uses_local_build", lambda: True)
        up_calls = []
        monkeypatch.setattr(cli, "api_up", lambda **kw: up_calls.append(kw) or {})

        args = _ns(command="connect", platform_name="claude", path=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_connect(args)
        assert code == 0
        assert up_calls == [{"build_local": True}]

    def test_connect_path_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Connect returns 1 when the platform path does not exist."""
        cfg = make_config(Path("/tmp/fake"))
        monkeypatch.setattr(cli, "get_config", lambda: cfg)
        monkeypatch.setattr(cli, "load_platforms", lambda _p: {"platforms": {}})
        monkeypatch.setattr(
            cli,
            "connect_platform",
            lambda _p, _n, custom_path=None: {
                "status": "path_not_found",
                "path": "/no/such/path",
            },
        )

        args = _ns(command="connect", platform_name="claude", path=None)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_connect(args)
        assert code == 1

    def test_connect_unknown_platform_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Connect returns 1 when connect_platform reports unknown_platform."""
        cfg = make_config(Path("/tmp/fake"))
        monkeypatch.setattr(cli, "get_config", lambda: cfg)
        monkeypatch.setattr(cli, "load_platforms", lambda _p: {"platforms": {}})
        monkeypatch.setattr(
            cli,
            "connect_platform",
            lambda _p, _n, custom_path=None: {"status": "unknown_platform"},
        )

        args = _ns(command="connect", platform_name="claude", path=None)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_connect(args)
        assert code == 1


# ===================================================================
# _cmd_queue
# ===================================================================


class TestCmdQueue:
    """Tests for the queue command handler."""

    def test_queue_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Queue with --json emits structured output."""
        jobs = [{"run_id": "abc123", "status": "pending"}]
        counts = {"pending": 1}
        monkeypatch.setattr(
            "lerim.sessions.catalog.list_queue_jobs",
            lambda **_kw: jobs,
        )
        monkeypatch.setattr(
            "lerim.sessions.catalog.count_session_jobs_by_status",
            lambda: counts,
        )

        args = _ns(
            command="queue",
            json=True,
            status=None,
            project=None,
            failed=False,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_queue(args)
        assert code == 0
        parsed = json.loads(buf.getvalue())
        assert parsed["total"] == 1
        assert "queue" in parsed

    def test_queue_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Queue with no jobs prints 'no jobs'."""
        monkeypatch.setattr(
            "lerim.sessions.catalog.list_queue_jobs",
            lambda **_kw: [],
        )
        monkeypatch.setattr(
            "lerim.sessions.catalog.count_session_jobs_by_status",
            lambda: {},
        )

        args = _ns(
            command="queue",
            json=False,
            status=None,
            project=None,
            failed=False,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_queue(args)
        assert code == 0
        assert "no jobs" in buf.getvalue().lower()

    def test_queue_with_rich_table(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Queue with jobs and no --json uses rich table output."""
        from datetime import datetime, timezone

        jobs = [
            {
                "run_id": "abcdef123456",
                "status": "failed",
                "repo_path": "/home/user/project",
                "agent_type": "claude",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "error": "rate limit exceeded",
            }
        ]
        counts = {"failed": 1}
        monkeypatch.setattr(
            "lerim.sessions.catalog.list_queue_jobs",
            lambda **_kw: jobs,
        )
        monkeypatch.setattr(
            "lerim.sessions.catalog.count_session_jobs_by_status",
            lambda: counts,
        )

        args = _ns(
            command="queue",
            json=False,
            status=None,
            project=None,
            failed=False,
        )
        # Rich writes to its own Console, just verify no crash and return 0
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_queue(args)
        assert code == 0


# ===================================================================
# _dead_letter_action / _cmd_retry / _cmd_skip
# ===================================================================


class TestDeadLetterAction:
    """Tests for the retry/skip dead-letter dispatch."""

    def test_retry_all_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Retry --all with no dead_letter jobs prints 'no jobs'."""
        monkeypatch.setattr(
            "lerim.sessions.catalog.retry_all_dead_letter_jobs",
            lambda: 0,
        )
        monkeypatch.setattr(
            "lerim.sessions.catalog.count_session_jobs_by_status",
            lambda: {},
        )
        monkeypatch.setattr(
            "lerim.sessions.catalog.resolve_run_id_prefix",
            lambda _p: None,
        )

        args = _ns(command="retry", run_id=None, project=None, all=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_retry(args)
        assert code == 0
        assert "no dead_letter" in buf.getvalue().lower()

    def test_retry_all_with_jobs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Retry --all uses the uncapped bulk helper."""
        monkeypatch.setattr(
            "lerim.sessions.catalog.list_queue_jobs",
            lambda **_kw: pytest.fail("--all retry should not list paginated jobs"),
        )
        monkeypatch.setattr(
            "lerim.sessions.catalog.count_session_jobs_by_status",
            lambda: {"pending": 2},
        )
        monkeypatch.setattr(
            "lerim.sessions.catalog.retry_all_dead_letter_jobs",
            lambda: 2,
        )

        args = _ns(command="retry", run_id=None, project=None, all=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_retry(args)
        assert code == 0
        assert "2" in buf.getvalue()

    def test_retry_by_project(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Retry --project dispatches to project_fn."""
        cfg = make_config(Path("/tmp/fake"))
        cfg = replace(cfg, projects={"myproj": "/home/user/myproj"})
        monkeypatch.setattr(cli, "get_config", lambda: cfg)
        monkeypatch.setattr(
            "lerim.sessions.catalog.retry_project_jobs",
            lambda _rp: 3,
        )
        monkeypatch.setattr(
            "lerim.sessions.catalog.count_session_jobs_by_status",
            lambda: {"pending": 3},
        )

        args = _ns(command="retry", run_id=None, project="myproj", all=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_retry(args)
        assert code == 0
        assert "3" in buf.getvalue()

    def test_retry_project_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Retry --project with unknown project returns 1."""
        cfg = make_config(Path("/tmp/fake"))
        cfg = replace(cfg, projects={})
        monkeypatch.setattr(cli, "get_config", lambda: cfg)

        args = _ns(command="retry", run_id=None, project="nonexistent", all=False)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_retry(args)
        assert code == 1
        assert "not found" in buf.getvalue().lower()

    def test_retry_no_args(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Retry with no run_id, project, or --all returns 2."""
        args = _ns(command="retry", run_id=None, project=None, all=False)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_retry(args)
        assert code == 2

    def test_retry_prefix_too_short(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Run ID prefix under 6 chars returns 2."""
        args = _ns(command="retry", run_id="abc", project=None, all=False)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_retry(args)
        assert code == 2
        assert "at least 6" in buf.getvalue().lower()

    def test_retry_prefix_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unresolvable run ID prefix returns 1."""
        monkeypatch.setattr(
            "lerim.sessions.catalog.resolve_run_id_prefix",
            lambda _p: None,
        )
        monkeypatch.setattr(
            "lerim.sessions.catalog.list_queue_jobs",
            lambda **_kw: [],
        )
        monkeypatch.setattr(
            "lerim.sessions.catalog.count_session_jobs_by_status",
            lambda: {},
        )

        args = _ns(command="retry", run_id="abcdef", project=None, all=False)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_retry(args)
        assert code == 1

    def test_retry_single_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Single run_id retry resolves and succeeds."""
        monkeypatch.setattr(
            "lerim.sessions.catalog.resolve_run_id_prefix",
            lambda _p: "abcdef123456full",
        )
        monkeypatch.setattr(
            "lerim.sessions.catalog.retry_session_job",
            lambda _id: True,
        )
        monkeypatch.setattr(
            "lerim.sessions.catalog.count_session_jobs_by_status",
            lambda: {"pending": 1},
        )
        monkeypatch.setattr(
            "lerim.sessions.catalog.list_queue_jobs",
            lambda **_kw: [],
        )

        args = _ns(command="retry", run_id="abcdef", project=None, all=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_retry(args)
        assert code == 0
        assert "retried" in buf.getvalue().lower()

    def test_retry_single_not_dead_letter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Single run_id that is not in dead_letter returns 1."""
        monkeypatch.setattr(
            "lerim.sessions.catalog.resolve_run_id_prefix",
            lambda _p: "abcdef123456full",
        )
        monkeypatch.setattr(
            "lerim.sessions.catalog.retry_session_job",
            lambda _id: False,
        )
        monkeypatch.setattr(
            "lerim.sessions.catalog.count_session_jobs_by_status",
            lambda: {},
        )
        monkeypatch.setattr(
            "lerim.sessions.catalog.list_queue_jobs",
            lambda **_kw: [],
        )

        args = _ns(command="retry", run_id="abcdef", project=None, all=False)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_retry(args)
        assert code == 1

    def test_skip_single_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Single run_id skip resolves and succeeds with '-> done' suffix."""
        monkeypatch.setattr(
            "lerim.sessions.catalog.resolve_run_id_prefix",
            lambda _p: "abcdef123456full",
        )
        monkeypatch.setattr(
            "lerim.sessions.catalog.skip_session_job",
            lambda _id: True,
        )
        monkeypatch.setattr(
            "lerim.sessions.catalog.count_session_jobs_by_status",
            lambda: {"done": 1},
        )
        monkeypatch.setattr(
            "lerim.sessions.catalog.list_queue_jobs",
            lambda **_kw: [],
        )

        args = _ns(command="skip", run_id="abcdef", project=None, all=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_skip(args)
        assert code == 0
        text = buf.getvalue().lower()
        assert "skipped" in text
        assert "done" in text


# ===================================================================
# _cmd_project
# ===================================================================


class TestCmdProject:
    """Tests for the project command handler."""

    def test_project_no_action(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Project with no subcommand returns 2."""
        args = _ns(command="project", json=False, project_action=None)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_project(args)
        assert code == 2

    def test_project_list_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Project list with no projects prints 'No projects'."""
        monkeypatch.setattr(cli, "api_project_list", lambda: [])
        args = _ns(command="project", json=False, project_action="list")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_project(args)
        assert code == 0
        assert "no projects" in buf.getvalue().lower()

    def test_project_list_with_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Project list with entries prints summary."""
        monkeypatch.setattr(
            cli,
            "api_project_list",
            lambda: [
                {"name": "myapp", "path": "/home/user/myapp", "exists": True},
            ],
        )
        args = _ns(command="project", json=False, project_action="list")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_project(args)
        assert code == 0
        text = buf.getvalue()
        assert "myapp" in text
        assert "supported" in text
        assert ".lerim" not in text

    def test_project_list_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Project list --json emits JSON array."""
        projects = [{"name": "x", "path": "/x", "exists": True}]
        monkeypatch.setattr(cli, "api_project_list", lambda: projects)
        args = _ns(command="project", json=True, project_action="list")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_project(args)
        assert code == 0
        parsed = json.loads(buf.getvalue())
        assert len(parsed) == 1

    def test_project_add_no_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Project add without a path returns 2."""
        args = _ns(command="project", json=False, project_action="add", path=None)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_project(args)
        assert code == 2

    def test_project_add_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Project add with a valid path prints success."""
        monkeypatch.setattr(
            cli,
            "api_project_add",
            lambda p, **kwargs: {
                "name": "myapp",
                "path": p,
                "type": kwargs.get("project_type", "supported"),
                "error": None,
            },
        )
        monkeypatch.setattr(cli, "is_docker_container_running", lambda: False)
        args = _ns(
            command="project", json=False, project_action="add", path="/home/user/myapp"
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_project(args)
        assert code == 0
        text = buf.getvalue()
        assert "myapp" in text
        assert "type=supported" in text
        assert ".lerim" not in text

    def test_project_add_custom_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Project add forwards the requested custom source type."""
        captured: dict[str, str] = {}

        def fake_project_add(path: str, **kwargs):
            captured["path"] = path
            captured["project_type"] = kwargs["project_type"]
            return {
                "name": "support-traces",
                "path": path,
                "type": kwargs["project_type"],
                "error": None,
            }

        monkeypatch.setattr(cli, "api_project_add", fake_project_add)
        monkeypatch.setattr(cli, "is_docker_container_running", lambda: False)
        args = _ns(
            command="project",
            json=False,
            project_action="add",
            path="/home/user/support-traces",
            project_type="custom",
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_project(args)
        assert code == 0
        assert captured == {
            "path": "/home/user/support-traces",
            "project_type": "custom",
        }
        assert "type=custom" in buf.getvalue()

    def test_project_add_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Project add with error response returns 1."""
        monkeypatch.setattr(
            cli,
            "api_project_add",
            lambda p, **_kwargs: {
                "error": "path does not exist",
            },
        )
        args = _ns(
            command="project", json=False, project_action="add", path="/nonexistent"
        )
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_project(args)
        assert code == 1

    def test_project_add_restarts_container(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Project add restarts container when it is running."""
        monkeypatch.setattr(
            cli,
            "api_project_add",
            lambda p, **kwargs: {
                "name": "myapp",
                "path": p,
                "type": kwargs.get("project_type", "supported"),
                "error": None,
            },
        )
        monkeypatch.setattr(cli, "is_docker_container_running", lambda: True)
        up_calls = []
        monkeypatch.setattr(cli, "current_compose_uses_local_build", lambda: True)
        monkeypatch.setattr(cli, "api_up", lambda **kw: up_calls.append(kw) or {})
        args = _ns(
            command="project", json=False, project_action="add", path="/home/user/myapp"
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_project(args)
        assert code == 0
        assert up_calls == [{"build_local": True}]

    def test_project_add_reports_restart_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Project add returns 1 if the Docker restart fails."""
        monkeypatch.setattr(
            cli,
            "api_project_add",
            lambda p, **kwargs: {
                "name": "myapp",
                "path": p,
                "type": kwargs.get("project_type", "supported"),
                "error": None,
            },
        )
        monkeypatch.setattr(cli, "is_docker_container_running", lambda: True)
        monkeypatch.setattr(cli, "current_compose_uses_local_build", lambda: True)
        monkeypatch.setattr(
            cli,
            "api_up",
            lambda **kw: {"error": "docker compose failed"},
        )
        args = _ns(
            command="project", json=False, project_action="add", path="/home/user/myapp"
        )
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli._cmd_project(args)
        assert code == 1
        assert "docker compose failed" in err.getvalue()

    def test_project_remove_no_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Project remove without a name returns 2."""
        args = _ns(command="project", json=False, project_action="remove", name=None)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_project(args)
        assert code == 2

    def test_project_remove_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Project remove with valid name succeeds."""
        monkeypatch.setattr(cli, "api_project_remove", lambda n: {"error": None})
        monkeypatch.setattr(cli, "is_docker_container_running", lambda: False)
        args = _ns(command="project", json=False, project_action="remove", name="myapp")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_project(args)
        assert code == 0
        assert "removed" in buf.getvalue().lower()

    def test_project_remove_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Project remove with error response returns 1."""
        monkeypatch.setattr(
            cli,
            "api_project_remove",
            lambda n: {
                "error": "project not found",
            },
        )
        args = _ns(command="project", json=False, project_action="remove", name="bogus")
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_project(args)
        assert code == 1

    def test_project_remove_restarts_with_existing_runtime_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Project remove preserves local-build mode when restarting."""
        monkeypatch.setattr(cli, "api_project_remove", lambda n: {"error": None})
        monkeypatch.setattr(cli, "is_docker_container_running", lambda: True)
        monkeypatch.setattr(cli, "current_compose_uses_local_build", lambda: True)
        up_calls = []
        monkeypatch.setattr(cli, "api_up", lambda **kw: up_calls.append(kw) or {})
        args = _ns(command="project", json=False, project_action="remove", name="myapp")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_project(args)
        assert code == 0
        assert up_calls == [{"build_local": True}]

    def test_project_unknown_action(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Project with an unknown action returns 2."""
        args = _ns(command="project", json=False, project_action="rename")
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_project(args)
        assert code == 2


class TestCmdMemory:
    """Tests for the memory command handler."""

    def test_memory_no_action(self) -> None:
        args = _ns(command="memory", json=False, memory_action=None)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_memory(args)
        assert code == 2

    def test_memory_reset_requires_one_scope(self) -> None:
        args = _ns(
            command="memory",
            json=False,
            memory_action="reset",
            project=None,
            all=False,
            yes=True,
        )
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_memory(args)
        assert code == 2

    def test_memory_reset_project_yes_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict[str, Any]] = []

        def fake_reset(**kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "error": False,
                "scope": "project",
                "project": "myapp",
                "dry_run": kwargs.get("dry_run"),
                "deleted": {"records": 1},
                "kept": ["config"],
                "notes": [],
            }

        monkeypatch.setattr(cli, "api_memory_reset", fake_reset)
        args = _ns(
            command="memory",
            json=True,
            memory_action="reset",
            project="myapp",
            all=False,
            yes=True,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_memory(args)

        assert code == 0
        assert calls == [
            {"project": "myapp", "all_projects": False, "dry_run": True},
            {"project": "myapp", "all_projects": False, "dry_run": False},
        ]
        assert json.loads(buf.getvalue())["deleted"]["records"] == 1

    def test_memory_reset_json_requires_yes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(
            cli, "api_memory_reset", lambda **kwargs: calls.append(kwargs)
        )
        args = _ns(
            command="memory",
            json=True,
            memory_action="reset",
            project=None,
            all=True,
            yes=False,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_memory(args)

        assert code == 2
        assert calls == []
        assert json.loads(buf.getvalue())["error"] is True

    def test_memory_reset_cancelled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            cli,
            "api_memory_reset",
            lambda **kwargs: {
                "error": False,
                "scope": "all",
                "dry_run": kwargs.get("dry_run"),
                "deleted": {"records": 1},
                "kept": ["config"],
                "notes": [],
            },
        )
        monkeypatch.setattr("builtins.input", lambda _prompt: "n")
        args = _ns(
            command="memory",
            json=False,
            memory_action="reset",
            project=None,
            all=True,
            yes=False,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_memory(args)

        assert code == 1
        assert "cancelled" in buf.getvalue().lower()

    # ===================================================================
    # _cmd_up / _cmd_down
    # ===================================================================


class TestCmdUp:
    """Tests for the up command handler."""

    def test_up_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Up starts container and waits for ready."""
        cfg = make_config(Path("/tmp/fake"))
        cfg = replace(cfg, projects={"p": "/p"}, agents={"claude": {}})
        monkeypatch.setattr(cli, "get_config", lambda: cfg)
        monkeypatch.setattr(cli, "api_up", lambda build_local=False: {})
        monkeypatch.setattr(cli, "current_compose_uses_local_build", lambda: False)
        monkeypatch.setattr(cli, "_wait_for_ready", lambda port: True)

        args = _ns(command="up", build=False)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_up(args)
        assert code == 0
        assert "running" in buf.getvalue().lower()

    def test_up_api_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Up returns 1 when api_up reports an error."""
        cfg = make_config(Path("/tmp/fake"))
        monkeypatch.setattr(cli, "get_config", lambda: cfg)
        monkeypatch.setattr(cli, "current_compose_uses_local_build", lambda: False)
        monkeypatch.setattr(
            cli, "api_up", lambda build_local=False: {"error": "docker not found"}
        )

        args = _ns(command="up", build=False)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_up(args)
        assert code == 1

    def test_up_server_not_ready(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Up returns 1 when the server does not become ready."""
        cfg = make_config(Path("/tmp/fake"))
        monkeypatch.setattr(cli, "get_config", lambda: cfg)
        monkeypatch.setattr(cli, "api_up", lambda build_local=False: {})
        monkeypatch.setattr(cli, "current_compose_uses_local_build", lambda: False)
        monkeypatch.setattr(cli, "_wait_for_ready", lambda port: False)

        args = _ns(command="up", build=False)
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_up(args)
        assert code == 1

    def test_up_preserves_existing_local_build(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plain up keeps a local-build compose in local-build mode."""
        cfg = make_config(Path("/tmp/fake"))
        monkeypatch.setattr(cli, "get_config", lambda: cfg)
        monkeypatch.setattr(cli, "current_compose_uses_local_build", lambda: True)
        monkeypatch.setattr(cli, "_wait_for_ready", lambda port: True)
        calls: list[bool] = []
        monkeypatch.setattr(
            cli,
            "api_up",
            lambda build_local=False: calls.append(build_local) or {},
        )

        args = _ns(command="up", build=False)
        assert cli._cmd_up(args) == 0
        assert calls == [True]


class TestCmdDown:
    """Tests for the down command handler."""

    def test_down_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Down stops a running container."""
        monkeypatch.setattr(cli, "api_down", lambda: {"was_running": True})
        args = _ns(command="down")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_down(args)
        assert code == 0
        assert "stopped" in buf.getvalue().lower()

    def test_down_not_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Down when container is not running prints appropriate message."""
        monkeypatch.setattr(cli, "api_down", lambda: {"status": "not_running"})
        args = _ns(command="down")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_down(args)
        assert code == 0
        assert "not running" in buf.getvalue().lower()

    def test_down_cleanup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Down when container was already gone cleans up."""
        monkeypatch.setattr(cli, "api_down", lambda: {"was_running": False})
        args = _ns(command="down")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_down(args)
        assert code == 0
        assert "cleaned up" in buf.getvalue().lower()

    def test_down_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Down returns 1 on error."""
        monkeypatch.setattr(cli, "api_down", lambda: {"error": "permission denied"})
        args = _ns(command="down")
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_down(args)
        assert code == 1


# ===================================================================
# _cmd_logs
# ===================================================================


class TestCmdLogs:
    """Tests for the logs command handler."""

    def test_logs_no_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Logs returns a successful empty-state message when no log file exists."""
        monkeypatch.setattr("lerim.config.logging.LOG_DIR", tmp_path)
        args = _ns(
            command="logs",
            follow=False,
            level=None,
            since=None,
            raw_json=False,
            json=False,
        )
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_logs(args)
        assert code == 0
        assert "no log file" in buf.getvalue().lower()

    def test_logs_reads_entries(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Logs reads and displays JSONL entries."""
        monkeypatch.setattr("lerim.config.logging.LOG_DIR", tmp_path)
        log_file = _dated_log_file(tmp_path)
        entries = [
            json.dumps(
                {"ts": "2026-03-01T12:00:00Z", "level": "info", "message": "started"}
            ),
            json.dumps(
                {"ts": "2026-03-01T12:01:00Z", "level": "error", "message": "boom"}
            ),
        ]
        log_file.write_text("\n".join(entries) + "\n")

        args = _ns(
            command="logs",
            follow=False,
            level=None,
            since=None,
            raw_json=False,
            json=False,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_logs(args)
        assert code == 0
        text = buf.getvalue()
        assert "started" in text
        assert "boom" in text

    def test_logs_level_filter(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Logs with --level filters to matching entries only."""
        monkeypatch.setattr("lerim.config.logging.LOG_DIR", tmp_path)
        log_file = _dated_log_file(tmp_path)
        entries = [
            json.dumps(
                {"ts": "2026-03-01T12:00:00Z", "level": "info", "message": "ok"}
            ),
            json.dumps(
                {"ts": "2026-03-01T12:01:00Z", "level": "error", "message": "fail"}
            ),
        ]
        log_file.write_text("\n".join(entries) + "\n")

        args = _ns(
            command="logs",
            follow=False,
            level="error",
            since=None,
            raw_json=False,
            json=False,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_logs(args)
        assert code == 0
        text = buf.getvalue()
        assert "fail" in text
        assert "ok" not in text

    def test_logs_raw_json(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Logs with --json outputs raw JSONL."""
        monkeypatch.setattr("lerim.config.logging.LOG_DIR", tmp_path)
        log_file = _dated_log_file(tmp_path)
        entry = {"ts": "2026-03-01T12:00:00Z", "level": "info", "message": "hello"}
        log_file.write_text(json.dumps(entry) + "\n")

        args = _ns(
            command="logs",
            follow=False,
            level=None,
            since=None,
            raw_json=True,
            json=False,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_logs(args)
        assert code == 0
        parsed = json.loads(buf.getvalue().strip())
        assert parsed["message"] == "hello"

    def test_logs_since_filter(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Logs with --since filters old entries."""
        monkeypatch.setattr("lerim.config.logging.LOG_DIR", tmp_path)
        log_file = _dated_log_file(tmp_path)
        from datetime import datetime, timezone, timedelta

        recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
        entries = [
            json.dumps({"ts": old, "level": "info", "message": "ancient"}),
            json.dumps({"ts": recent, "level": "info", "message": "fresh"}),
        ]
        log_file.write_text("\n".join(entries) + "\n")

        args = _ns(
            command="logs",
            follow=False,
            level=None,
            since="1h",
            raw_json=False,
            json=False,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_logs(args)
        assert code == 0
        text = buf.getvalue()
        assert "fresh" in text
        assert "ancient" not in text


# ===================================================================
# _cmd_skill
# ===================================================================


class TestCmdSkill:
    """Tests for the skill command handler."""

    def test_skill_no_action(self) -> None:
        """Skill with no subcommand returns 2."""
        args = _ns(command="skill", skill_action=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_skill(args)
        assert code == 2

    def test_skill_install_success(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Skill install copies files to target directories."""
        # Create fake skill source files
        skills_dir = tmp_path / "skills_src"
        skills_dir.mkdir()
        (skills_dir / "SKILL.md").write_text("# Skill")
        (skills_dir / "cli-reference.md").write_text("# CLI Ref")
        monkeypatch.setattr("lerim.skills.SKILLS_DIR", skills_dir)

        # Redirect install targets to tmp_path
        targets = {
            "agents": tmp_path / ".agents" / "skills" / "lerim",
            "claude": tmp_path / ".claude" / "skills" / "lerim",
        }
        monkeypatch.setattr(cli, "_SKILL_TARGETS", targets)

        args = _ns(command="skill", skill_action="install")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_skill(args)
        assert code == 0
        assert (targets["agents"] / "SKILL.md").exists()
        assert (targets["claude"] / "cli-reference.md").exists()

    def test_skill_install_missing_files(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Skill install returns 1 when source files are missing."""
        skills_dir = tmp_path / "empty_skills"
        skills_dir.mkdir()
        monkeypatch.setattr("lerim.skills.SKILLS_DIR", skills_dir)

        args = _ns(command="skill", skill_action="install")
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_skill(args)
        assert code == 1


# ===================================================================
# _cmd_auth_dispatch
# ===================================================================


class TestCmdAuthDispatch:
    """Tests for the auth dispatch handler."""

    def test_auth_login_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Bare 'lerim auth' dispatches to cmd_auth (login)."""
        monkeypatch.setattr(cli, "cmd_auth", lambda a: 0)
        args = _ns(command="auth", auth_command=None, token=None)
        code = cli._cmd_auth_dispatch(args)
        assert code == 0

    def test_auth_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """'lerim auth status' dispatches to cmd_auth_status."""
        monkeypatch.setattr(cli, "cmd_auth_status", lambda a: 0)
        args = _ns(command="auth", auth_command="status")
        code = cli._cmd_auth_dispatch(args)
        assert code == 0

    def test_auth_logout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """'lerim auth logout' dispatches to cmd_auth_logout."""
        monkeypatch.setattr(cli, "cmd_auth_logout", lambda a: 0)
        args = _ns(command="auth", auth_command="logout")
        code = cli._cmd_auth_dispatch(args)
        assert code == 0


# ===================================================================
# _wait_for_ready
# ===================================================================


class TestWaitForReady:
    """Tests for the HTTP health polling helper."""

    def test_immediate_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns True when the first request succeeds."""

        class FakeResp:
            """Mock HTTP response."""

            status = 200

            def read(self):
                """Return empty body."""
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=5: FakeResp(),
        )
        assert cli._wait_for_ready(8765, timeout=2) is True

    def test_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns False when the server never responds within timeout."""

        def _fail(*_a, **_kw):
            """Always raise connection error."""
            raise urllib.error.URLError("refused")

        import urllib.error

        monkeypatch.setattr("urllib.request.urlopen", _fail)
        monkeypatch.setattr("time.sleep", lambda _s: None)
        assert cli._wait_for_ready(8765, timeout=0) is False


# ===================================================================
# _resolve_project_repo_path
# ===================================================================


class TestResolveProjectRepoPath:
    """Tests for the project name -> repo_path resolver."""

    def test_exact_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Exact project name match resolves correctly."""
        cfg = make_config(Path("/tmp/fake"))
        cfg = replace(cfg, projects={"myapp": "/home/user/myapp"})
        monkeypatch.setattr(cli, "get_config", lambda: cfg)
        result = cli._resolve_project_repo_path("myapp")
        assert result is not None
        assert "myapp" in result

    def test_substring_match_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Substring project lookup is rejected (exact match only)."""
        cfg = make_config(Path("/tmp/fake"))
        cfg = replace(cfg, projects={"my-cool-app": "/home/user/my-cool-app"})
        monkeypatch.setattr(cli, "get_config", lambda: cfg)
        result = cli._resolve_project_repo_path("cool")
        assert result is None

    def test_no_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No matching project returns None."""
        cfg = make_config(Path("/tmp/fake"))
        cfg = replace(cfg, projects={})
        monkeypatch.setattr(cli, "get_config", lambda: cfg)
        result = cli._resolve_project_repo_path("nonexistent")
        assert result is None


# ===================================================================
# build_parser — subcommand validation
# ===================================================================


class TestBuildParser:
    """Tests for build_parser subcommand acceptance."""

    def test_all_subcommands_parse(self) -> None:
        """Every known subcommand is accepted without error."""
        parser = cli.build_parser()
        subcommands = [
            ["connect"],
            ["ingest"],
            [
                "trace",
                "import",
                "trace.jsonl",
                "--source-name",
                "support-bot",
                "--source-profile",
                "support",
                "--scope-type",
                "domain",
                "--scope",
                "support",
            ],
            ["curate"],
            ["context-brief"],
            ["dashboard"],
            ["answer", "question text"],
            ["status"],
            ["queue"],
            ["retry", "abcdef"],
            ["skip", "abcdef"],
            ["init"],
            ["project", "list"],
            ["project", "add", "/path"],
            ["project", "remove", "name"],
            ["up"],
            ["up", "--build"],
            ["down"],
            ["logs"],
            ["logs", "-f"],
            ["logs", "--level", "error"],
            ["logs", "--since", "1h"],
            ["serve"],
            ["serve", "--host", "0.0.0.0", "--port", "9999"],
            ["skill"],
            ["skill", "install"],
            ["auth"],
            ["auth", "login"],
            ["auth", "status"],
            ["auth", "logout"],
            ["auth", "--token", "tok123"],
        ]
        for argv in subcommands:
            args = parser.parse_args(argv)
            assert args.command is not None, f"Failed to parse: {argv}"

    def test_ingest_flags(self) -> None:
        """Ingest parser accepts all flags."""
        parser = cli.build_parser()
        args = parser.parse_args(
            [
                "ingest",
                "--run-id",
                "r1",
                "--agent",
                "claude",
                "--window",
                "30d",
                "--since",
                "2026-01-01",
                "--until",
                "2026-02-01",
                "--max-sessions",
                "100",
                "--no-extract",
                "--force",
                "--dry-run",
            ]
        )
        assert args.run_id == "r1"
        assert args.agent == "claude"
        assert args.max_sessions == 100
        assert args.no_extract is True
        assert args.force is True
        assert args.dry_run is True

    def test_curate_flags(self) -> None:
        """Curate parser accepts --dry-run."""
        parser = cli.build_parser()
        args = parser.parse_args(["curate", "--dry-run"])
        assert args.dry_run is True

    def test_trace_import_flags(self) -> None:
        """Trace import parser accepts source and scope flags."""
        parser = cli.build_parser()
        args = parser.parse_args(
            [
                "trace",
                "import",
                "trace.jsonl",
                "--source-name",
                "support-bot",
                "--source-profile",
                "support",
                "--scope-type",
                "domain",
                "--scope",
                "support",
                "--scope-label",
                "Support",
                "--session-id",
                "sess_support",
            ]
        )
        assert args.command == "trace"
        assert args.trace_action == "import"
        assert args.source_name == "support-bot"
        assert args.scope_type == "domain"
        assert args.scope_label == "Support"
        assert args.session_id == "sess_support"

    def test_queue_flags(self) -> None:
        """Queue parser accepts --failed, --status, and --project."""
        parser = cli.build_parser()
        args = parser.parse_args(
            ["queue", "--failed", "--status", "pending", "--project", "my"]
        )
        assert args.failed is True
        assert args.status == "pending"
        assert args.project == "my"

    def test_status_scope_flags(self) -> None:
        """Status parser accepts --scope, --project, --live, --interval."""
        parser = cli.build_parser()
        args = parser.parse_args(
            [
                "status",
                "--scope",
                "project",
                "--project",
                "myproj",
                "--live",
                "--interval",
                "1.5",
            ]
        )
        assert args.scope == "project"
        assert args.project == "myproj"
        assert args.live is True
        assert args.interval == 1.5

    def test_answer_scope_flags(self) -> None:
        """Answer parser accepts --scope and optional --project."""
        parser = cli.build_parser()
        args = parser.parse_args(
            ["answer", "hello", "--scope", "project", "--project", "myproj"]
        )
        assert args.scope == "project"
        assert args.project == "myproj"

    def test_retry_skip_args(self) -> None:
        """Retry and skip parsers accept run_id, --project, --all."""
        parser = cli.build_parser()
        for cmd in ("retry", "skip"):
            args = parser.parse_args([cmd, "--all"])
            assert getattr(args, "all") is True
            args = parser.parse_args([cmd, "--project", "myproj"])
            assert args.project == "myproj"
            args = parser.parse_args([cmd, "abc123"])
            assert args.run_id == "abc123"

    def test_version_flag(self) -> None:
        """--version exits with code 0."""
        parser = cli.build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["--version"])
        assert exc.value.code == 0


# ===================================================================
# main() dispatch
# ===================================================================


class TestMain:
    """Tests for the main() entry point dispatch."""

    def test_no_command_prints_help(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Main with no args prints help and returns 0."""
        monkeypatch.setattr(cli, "configure_logging", lambda: None)
        monkeypatch.setattr(cli, "configure_tracing", lambda _cfg: None)
        # main([]) falls through to sys.argv because empty list is falsy;
        # monkeypatch sys.argv so argparse sees no subcommand.
        monkeypatch.setattr(sys, "argv", ["lerim"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main([])
        assert code == 0
        assert "lerim" in buf.getvalue().lower()

    def test_dispatches_to_handler(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Main dispatches to the command handler function."""
        monkeypatch.setattr(cli, "configure_logging", lambda: None)
        monkeypatch.setattr(cli, "configure_tracing", lambda _cfg: None)
        fake_status = {
            "connected_agents": [],
            "record_count": 0,
            "sessions_indexed_count": 0,
            "queue": {},
            "latest_ingest": None,
            "latest_curate": None,
        }
        monkeypatch.setattr(cli, "_api_get", lambda _p: fake_status)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["status", "--json"])
        assert code == 0

    def test_skips_tracing_for_lightweight_commands(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Lightweight commands (queue, logs, auth) skip tracing init."""
        tracing_called = []
        monkeypatch.setattr(cli, "configure_logging", lambda: None)
        monkeypatch.setattr(
            cli, "configure_tracing", lambda _cfg: tracing_called.append(1)
        )

        # Queue is in _SKIP_TRACING_COMMANDS
        monkeypatch.setattr(
            "lerim.sessions.catalog.list_queue_jobs",
            lambda **_kw: [],
        )
        monkeypatch.setattr(
            "lerim.sessions.catalog.count_session_jobs_by_status",
            lambda: {},
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(["queue", "--json"])
        assert len(tracing_called) == 0

    def test_skips_tracing_for_http_client_commands(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HTTP client commands must not initialize local tracing."""
        tracing_called = []
        monkeypatch.setattr(cli, "configure_logging", lambda: None)
        monkeypatch.setattr(
            cli, "configure_tracing", lambda _cfg: tracing_called.append(1)
        )
        monkeypatch.setattr(
            cli,
            "_api_post",
            lambda _path, _body: {"answer": "ok", "error": False},
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["answer", "hello"])
        assert code == 0
        assert len(tracing_called) == 0

    def test_enables_tracing_for_serve(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Direct in-process server mode owns tracing initialization."""
        tracing_called = []
        monkeypatch.setattr(cli, "configure_logging", lambda: None)
        monkeypatch.setattr(
            cli, "configure_tracing", lambda _cfg: tracing_called.append(1)
        )
        monkeypatch.setattr(
            cli,
            "_cmd_serve",
            lambda _args: 0,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(["serve"])
        assert code == 0
        assert len(tracing_called) == 1

    def test_help_skips_tracing_for_trace_commands(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Subcommand help must not initialize tracing side effects."""
        tracing_called = []
        monkeypatch.setattr(cli, "configure_logging", lambda: None)
        monkeypatch.setattr(
            cli, "configure_tracing", lambda _cfg: tracing_called.append(1)
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            with pytest.raises(SystemExit) as exc:
                cli.main(["curate", "--help"])
        assert exc.value.code == 0
        assert len(tracing_called) == 0

    def test_unknown_command_rejected(self) -> None:
        """Unknown commands exit with code 2."""
        with pytest.raises(SystemExit) as exc:
            cli.main(["nonexistent_command"])
        assert exc.value.code == 2

    def test_project_no_subcommand_shows_help(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """'lerim project' with no subcommand shows help and exits 0."""
        monkeypatch.setattr(cli, "configure_logging", lambda: None)
        monkeypatch.setattr(cli, "configure_tracing", lambda _cfg: None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            with pytest.raises(SystemExit) as exc:
                cli.main(["project"])
        assert exc.value.code == 0

    def test_skill_no_subcommand_shows_help(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """'lerim skill' with no subcommand shows help and exits 0."""
        monkeypatch.setattr(cli, "configure_logging", lambda: None)
        monkeypatch.setattr(cli, "configure_tracing", lambda _cfg: None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            with pytest.raises(SystemExit) as exc:
                cli.main(["skill"])
        assert exc.value.code == 0


# ===================================================================
# _cmd_logs edge cases
# ===================================================================


class TestCmdLogsEdgeCases:
    """Additional edge case tests for the logs command."""

    def test_logs_malformed_jsonl(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Malformed JSONL lines are silently skipped."""
        monkeypatch.setattr("lerim.config.logging.LOG_DIR", tmp_path)
        log_file = _dated_log_file(tmp_path)
        lines = [
            "not valid json",
            json.dumps(
                {"ts": "2026-03-01T12:00:00Z", "level": "info", "message": "ok"}
            ),
            "",
            "{broken",
        ]
        log_file.write_text("\n".join(lines) + "\n")

        args = _ns(
            command="logs",
            follow=False,
            level=None,
            since=None,
            raw_json=False,
            json=False,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_logs(args)
        assert code == 0
        assert "ok" in buf.getvalue()

    def test_logs_empty_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Empty log file produces no output, returns 0."""
        monkeypatch.setattr("lerim.config.logging.LOG_DIR", tmp_path)
        log_file = _dated_log_file(tmp_path)
        log_file.write_text("")

        args = _ns(
            command="logs",
            follow=False,
            level=None,
            since=None,
            raw_json=False,
            json=False,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_logs(args)
        assert code == 0
        assert buf.getvalue().strip() == ""

    def test_logs_with_global_json_flag(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Logs with the global --json flag (via raw_json=False, json=True) outputs JSONL."""
        monkeypatch.setattr("lerim.config.logging.LOG_DIR", tmp_path)
        log_file = _dated_log_file(tmp_path)
        entry = {"ts": "2026-03-01T12:00:00Z", "level": "info", "message": "test"}
        log_file.write_text(json.dumps(entry) + "\n")

        # The global --json flag on args.json triggers raw output
        args = _ns(
            command="logs",
            follow=False,
            level=None,
            since=None,
            raw_json=False,
            json=True,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_logs(args)
        assert code == 0
        parsed = json.loads(buf.getvalue().strip())
        assert parsed["message"] == "test"


# ===================================================================
# _cmd_connect edge case: path unchanged
# ===================================================================


class TestCmdConnectPathUnchanged:
    """Test connect with existing path unchanged."""

    def test_connect_path_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Connecting a platform with the same path prints 'unchanged'."""
        cfg = make_config(Path("/tmp/fake"))
        monkeypatch.setattr(cli, "get_config", lambda: cfg)
        monkeypatch.setattr(
            cli,
            "load_platforms",
            lambda _p: {
                "platforms": {"claude": {"path": "/home/.claude"}},
            },
        )
        monkeypatch.setattr(
            cli,
            "connect_platform",
            lambda _p, _n, custom_path=None: {
                "status": "connected",
                "path": "/home/.claude",
                "session_count": 10,
            },
        )

        args = _ns(command="connect", platform_name="claude", path=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_connect(args)
        assert code == 0
        assert "unchanged" in buf.getvalue().lower()


# ===================================================================
# _cmd_queue dead_letter hint
# ===================================================================


class TestCmdQueueDeadLetterHint:
    """Test queue displays dead_letter retry hint."""

    def test_queue_dead_letter_shows_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Queue with dead_letter jobs shows retry hint."""
        from datetime import datetime, timezone

        jobs = [
            {
                "run_id": "abc123def456",
                "status": "dead_letter",
                "repo_path": "/proj",
                "agent_type": "claude",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "error": "timeout",
            }
        ]
        counts = {"dead_letter": 1}
        monkeypatch.setattr(
            "lerim.sessions.catalog.list_queue_jobs",
            lambda **_kw: jobs,
        )
        monkeypatch.setattr(
            "lerim.sessions.catalog.count_session_jobs_by_status",
            lambda: counts,
        )

        args = _ns(
            command="queue",
            json=False,
            status=None,
            project=None,
            failed=False,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli._cmd_queue(args)
        assert code == 0
        text = buf.getvalue()
        assert "retry" in text.lower()


# ===================================================================
# _cmd_logs: OS error reading log file
# ===================================================================


class TestCmdLogsOsError:
    """Test logs command when reading the file raises an OS error."""

    def test_logs_os_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Logs returns 1 when reading the file raises OSError."""
        monkeypatch.setattr("lerim.config.logging.LOG_DIR", tmp_path)
        log_file = _dated_log_file(tmp_path)
        log_file.write_text("dummy")

        original_open = open

        def _broken_open(path, *a, **kw):
            """Raise OSError when opening the log file."""
            if str(path) == str(log_file):
                raise OSError("permission denied")
            return original_open(path, *a, **kw)

        monkeypatch.setattr("builtins.open", _broken_open)

        args = _ns(
            command="logs",
            follow=False,
            level=None,
            since=None,
            raw_json=False,
            json=False,
        )
        buf = io.StringIO()
        with redirect_stderr(buf):
            code = cli._cmd_logs(args)
        assert code == 1
        assert "error" in buf.getvalue().lower()


# ===================================================================
# _api_get / _api_post (actual HTTP calls)
# ===================================================================


class TestApiGetPost:
    """Tests for the _api_get and _api_post HTTP wrappers."""

    def test_api_get_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_api_get returns parsed JSON on success."""
        cfg = make_config(Path("/tmp/fake"))
        monkeypatch.setattr(cli, "get_config", lambda: cfg)

        class FakeResp:
            """Mock HTTP response."""

            status = 200

            def read(self):
                """Return JSON body."""
                return json.dumps({"ok": True}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        monkeypatch.setattr(
            "urllib.request.urlopen", lambda req, timeout=30: FakeResp()
        )
        result = cli._api_get("/api/status")
        assert result == {"ok": True}

    def test_api_get_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_api_get raises a classified error when server is unreachable."""
        import urllib.error

        cfg = make_config(Path("/tmp/fake"))
        monkeypatch.setattr(cli, "get_config", lambda: cfg)
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=30: (_ for _ in ()).throw(
                urllib.error.URLError("refused")
            ),
        )
        with pytest.raises(cli.ApiClientError) as exc:
            cli._api_get("/api/status")
        assert exc.value.kind == "unreachable"

    def test_api_post_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_api_post returns parsed JSON on success."""
        cfg = make_config(Path("/tmp/fake"))
        monkeypatch.setattr(cli, "get_config", lambda: cfg)

        class FakeResp:
            """Mock HTTP response."""

            status = 200

            def read(self):
                """Return JSON body."""
                return json.dumps({"created": True}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        monkeypatch.setattr(
            "urllib.request.urlopen", lambda req, timeout=300: FakeResp()
        )
        result = cli._api_post("/api/ingest", {"force": True})
        assert result == {"created": True}

    def test_api_post_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_api_post raises a classified error when server is unreachable."""
        import urllib.error

        cfg = make_config(Path("/tmp/fake"))
        monkeypatch.setattr(cli, "get_config", lambda: cfg)
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda req, timeout=300: (_ for _ in ()).throw(
                urllib.error.URLError("refused")
            ),
        )
        with pytest.raises(cli.ApiClientError) as exc:
            cli._api_post("/api/ingest", {})
        assert exc.value.kind == "unreachable"

    def test_api_get_json_decode_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_api_get raises a classified error on malformed JSON response."""
        cfg = make_config(Path("/tmp/fake"))
        monkeypatch.setattr(cli, "get_config", lambda: cfg)

        class FakeResp:
            """Mock HTTP response with invalid JSON."""

            status = 200

            def read(self):
                """Return non-JSON body."""
                return b"not json"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        monkeypatch.setattr(
            "urllib.request.urlopen", lambda req, timeout=30: FakeResp()
        )
        with pytest.raises(cli.ApiClientError) as exc:
            cli._api_get("/api/status")
        assert exc.value.kind == "invalid_json"


# ===================================================================
# _wait_for_ready actual polling
# ===================================================================


class TestWaitForReadyPolling:
    """Tests for _wait_for_ready with retries."""

    def test_succeeds_after_retries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Returns True when server responds after initial failures."""
        import urllib.error

        call_count = 0

        class FakeResp:
            """Mock HTTP response."""

            status = 200

            def read(self):
                """Return empty body."""
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        def _urlopen(req, timeout=5):
            """Fail twice, then succeed."""
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise urllib.error.URLError("refused")
            return FakeResp()

        monkeypatch.setattr("urllib.request.urlopen", _urlopen)
        monkeypatch.setattr("time.sleep", lambda _s: None)
        assert cli._wait_for_ready(8765, timeout=10) is True
