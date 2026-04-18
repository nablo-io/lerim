"""CLI parser and command-contract tests."""

from __future__ import annotations

import argparse
import io
from dataclasses import replace
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from lerim.server import cli
from lerim.config.settings import reload_config
from tests.helpers import make_config, run_cli, run_cli_json, write_test_config


def test_help_lists_minimal_commands() -> None:
    parser = cli.build_parser()
    out = io.StringIO()
    with redirect_stdout(out), pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0
    text = out.getvalue()
    for command in (
        "connect",
        "sync",
        "maintain",
        "daemon",
        "dashboard",
        "ask",
        "status",
    ):
        assert command in text
    # Verify removed subcommands don't appear in the subcommand list.
    # Check the {connect,sync,...} subcommand choices section, not the full text
    # (description text may legitimately use these words).
    subcommand_choices = text.split("{")[1].split("}")[0] if "{" in text else ""
    for removed in ("readiness", "admin", "sessions", "config", "memory"):
        assert removed not in subcommand_choices, (
            f"removed command '{removed}' still in subcommands"
        )


def test_sync_parser_accepts_canonical_flags() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(
        ["sync", "--run-id", "run-1", "--agent", "claude,codex", "--window", "7d"]
    )
    assert isinstance(args, argparse.Namespace)
    assert args.command == "sync"
    assert args.run_id == "run-1"
    assert args.agent == "claude,codex"
    assert args.window == "7d"


def test_sync_help_uses_loaded_config_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_cfg = make_config(tmp_path / ".lerim")
    monkeypatch.setattr(
        cli,
        "get_config",
        lambda: replace(base_cfg, sync_window_days=11, sync_max_sessions=7),
    )
    parser = cli.build_parser()
    out = io.StringIO()
    with redirect_stdout(out), pytest.raises(SystemExit) as exc:
        parser.parse_args(["sync", "--help"])
    assert exc.value.code == 0
    text = out.getvalue()
    assert "currently 11d" in text
    assert "currently 7)" in text


def test_ask_parser_minimal_surface() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["ask", "what failed?"])
    assert args.command == "ask"
    assert args.question == "what failed?"


def test_ask_parser_rejects_limit_flag() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["ask", "what failed?", "--limit", "5"])
    assert exc.value.code == 2


def test_removed_command_rejected() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["sessions"])
    assert exc.value.code == 2


def test_status_json_output_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    reload_config()
    fake_status = {
        "timestamp": "2026-02-28T00:00:00+00:00",
        "connected_agents": ["claude"],
        "platforms": [],
        "record_count": 5,
        "sessions_indexed_count": 10,
        "queue": {"pending": 0},
        "latest_sync": None,
        "latest_maintain": None,
    }
    monkeypatch.setattr(cli, "_api_get", lambda _path: fake_status)
    code, payload = run_cli_json(["status", "--json"])
    assert code == 0
    assert "queue" in payload
    assert "latest_sync" in payload
    assert "latest_maintain" in payload


def test_ask_forwards_to_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ask command posts to /api/ask and prints the answer."""
    fake_response = {
        "answer": "Use bearer tokens.",
        "agent_session_id": "ses-1",
        "projects_used": [],
        "error": False,
    }
    monkeypatch.setattr(cli, "_api_post", lambda _path, _body: fake_response)
    code, payload = run_cli_json(["ask", "how to deploy", "--json"])
    assert code == 0
    assert payload["answer"] == "Use bearer tokens."


def test_ask_verbose_forwards_flag_and_prints_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_post(_path, body):
        captured["body"] = body
        return {
            "answer": "Use bearer tokens.",
            "agent_session_id": "ses-1",
            "projects_used": [],
            "error": False,
            "debug": {
                "messages": [
                    {
                        "message_index": 0,
                        "kind": "request",
                        "parts": [
                            {"part_kind": "system-prompt", "char_count": 123},
                            {"part_kind": "user-prompt", "content": "how to deploy"},
                        ],
                    },
                    {
                        "message_index": 1,
                        "kind": "response",
                        "parts": [
                            {
                                "part_kind": "tool-call",
                                "tool_name": "context_query",
                                "args": {"entity": "records", "mode": "count"},
                            },
                        ],
                    },
                    {
                        "message_index": 2,
                        "kind": "request",
                        "parts": [
                            {
                                "part_kind": "tool-return",
                                "tool_name": "context_query",
                                "content_preview": "{\"count\": 3}",
                            },
                        ],
                    },
                ],
            },
        }

    monkeypatch.setattr(cli, "_api_post", _fake_post)
    code, output = run_cli(["ask", "how to deploy", "--verbose"])
    assert code == 0
    assert captured["body"]["verbose"] is True
    assert "ASK TRACE" in output
    assert "[tool-call] context_query" in output
    assert "[tool-return] context_query -> {\"count\": 3}" in output


def test_ask_returns_nonzero_on_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_response = {
        "answer": "authentication_error: invalid api key",
        "error": True,
    }
    monkeypatch.setattr(cli, "_api_post", lambda _path, _body: fake_response)
    code, _output = run_cli(["ask", "how to deploy"])
    assert code == 1


def test_ask_returns_nonzero_when_server_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_api_post", lambda _path, _body: None)
    code, _output = run_cli(["ask", "how to deploy"])
    assert code == 1


def test_memory_command_removed() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["memory"])
    assert exc.value.code == 2


def test_json_flag_hoisting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """'lerim status --json' and 'lerim --json status' produce same result."""
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    reload_config()
    fake_status = {
        "timestamp": "2026-02-28T00:00:00+00:00",
        "connected_agents": [],
        "platforms": [],
        "record_count": 0,
        "sessions_indexed_count": 0,
        "queue": {},
        "latest_sync": None,
        "latest_maintain": None,
    }
    monkeypatch.setattr(cli, "_api_get", lambda _path: fake_status)
    code1, payload1 = run_cli_json(["status", "--json"])
    code2, payload2 = run_cli_json(["--json", "status"])
    assert code1 == 0
    assert code2 == 0
    # Both should produce valid status dicts with the same keys
    assert set(payload1.keys()) == set(payload2.keys())


def test_memory_list_command_removed() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["memory", "list"])
    assert exc.value.code == 2


def test_up_build_flag_accepted() -> None:
    """The --build flag is accepted by the up subparser."""
    parser = cli.build_parser()
    args = parser.parse_args(["up", "--build"])
    assert args.command == "up"
    assert args.build is True


def test_up_default_has_build_false() -> None:
    """Default 'up' command has build=False."""
    parser = cli.build_parser()
    args = parser.parse_args(["up"])
    assert args.command == "up"
    assert args.build is False
