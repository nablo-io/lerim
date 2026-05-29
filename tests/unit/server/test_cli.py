"""CLI parser and command-contract tests."""

from __future__ import annotations

import io
from dataclasses import replace
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from lerim.server import cli
from lerim.config.settings import load_toml_file, reload_config
from lerim.profiles import reload_signal_packs
from lerim.integrations.mcp_connect import McpConnectResult, McpTarget
from tests.helpers import make_config, run_cli, run_cli_json, write_test_config


def _raise_api_error(*_args, **_kwargs) -> None:
    """Raise the explicit CLI API client failure used by command tests."""
    raise cli.ApiClientError(
        kind="unreachable",
        message="Lerim server is not reachable: refused",
    )


def test_help_lists_minimal_commands() -> None:
    parser = cli.build_parser()
    out = io.StringIO()
    with redirect_stdout(out), pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0
    text = out.getvalue()
    for command in (
        "connect",
        "mcp",
        "ingest",
        "curate",
        "daemon",
        "dashboard",
        "answer",
        "context-brief",
        "working-memory",
        "status",
        "memory",
    ):
        assert command in text
    # Verify removed subcommands don't appear in the subcommand list.
    # Check the {connect,ingest,...} subcommand choices section, not the full text
    # (description text may legitimately use these words).
    subcommand_choices = text.split("{")[1].split("}")[0] if "{" in text else ""
    for removed in ("readiness", "admin", "sessions", "config"):
        assert removed not in subcommand_choices, (
            f"removed command '{removed}' still in subcommands"
        )


def test_ingest_parser_accepts_canonical_flags() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(
        ["ingest", "--run-id", "run-1", "--agent", "claude,codex", "--window", "7d"]
    )
    assert args.command == "ingest"
    assert args.run_id == "run-1"
    assert args.agent == "claude,codex"
    assert args.window == "7d"


def test_mcp_parser_is_host_only_command() -> None:
    """The MCP subcommand is available for external clients to launch."""
    parser = cli.build_parser()
    args = parser.parse_args(["mcp"])
    assert args.command == "mcp"
    assert args.func is cli._cmd_mcp


def test_mcp_help_points_client_configs_to_python_module() -> None:
    """MCP help should not tell users to put bare `lerim` in client configs."""
    parser = cli.build_parser()
    out = io.StringIO()
    with redirect_stdout(out), pytest.raises(SystemExit) as exc:
        parser.parse_args(["mcp", "--help"])

    assert exc.value.code == 0
    text = out.getvalue()
    assert "/absolute/path/to/python -m lerim.mcp_server" in text
    assert "Example client command: lerim mcp" not in text


def test_profile_parser_accepts_validate_and_register() -> None:
    """Profile parser exposes custom YAML validation and registration commands."""
    parser = cli.build_parser()
    validate_args = parser.parse_args(["profile", "validate", "research.yaml"])
    register_args = parser.parse_args(["profile", "register", "research.yaml", "--force"])

    assert validate_args.command == "profile"
    assert validate_args.profile_action == "validate"
    assert validate_args.path == "research.yaml"
    assert register_args.profile_action == "register"
    assert register_args.force is True


def test_profile_register_writes_profiles_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`lerim profile register` persists custom source profiles in config."""
    profile_path = tmp_path / "research.yaml"
    profile_path.write_text(
        "\n".join(
            [
                "id: research",
                "display_name: Research Analyst",
                "description: Research and market-analysis agent traces.",
                "focus_rules:",
                "  - Remember durable analyst preferences and source-quality rules.",
                "reject_as_noise:",
                "  - Ignore temporary browsing failures and dead links.",
                "evidence_rules:",
                "  - Keep source URLs, dates, and uncertainty qualifiers.",
                "scope_rules:",
                "  - Use domain scope for reusable research workflow context.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))

    try:
        reload_config()
        reload_signal_packs()
        code, payload = run_cli_json(
            ["--json", "profile", "register", str(profile_path)]
        )

        written = load_toml_file(config_path)

        assert code == 0
        assert payload["source_profile"] == "research"
        assert payload["path"] == str(profile_path.resolve())
        assert written["profiles"]["research"] == str(profile_path.resolve())
    finally:
        reload_config()
        reload_signal_packs()


def test_connect_mcp_list_json_uses_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MCP list mode emits JSON diagnostics for automation."""
    base_cfg = make_config(tmp_path / ".lerim")
    target = McpTarget(
        name="demo-agent",
        display_name="Demo Agent",
        config_path=tmp_path / "demo.json",
        config_format="json_mcp_servers",
    )
    monkeypatch.setattr(cli, "get_config", lambda: base_cfg)
    monkeypatch.setattr(cli, "known_mcp_targets", lambda: (target,))
    code, payload = run_cli_json(["connect", "list", "--mode", "mcp", "--json"])
    assert code == 0
    assert payload[0]["name"] == "demo-agent"
    assert payload[0]["configured"] is False


def test_connect_parser_accepts_auto_and_plugin_modes() -> None:
    """Connect parser exposes the planned mode names."""
    parser = cli.build_parser()
    auto_args = parser.parse_args(["connect", "codex", "--mode", "auto"])
    plugin_args = parser.parse_args(["connect", "openclaw", "--mode", "plugin"])
    assert auto_args.mode == "auto"
    assert plugin_args.mode == "plugin"


def test_connect_auto_mode_reports_adapter_and_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auto mode composes native adapter and MCP setup without real configs."""
    base_cfg = make_config(tmp_path / ".lerim")
    target = McpTarget(
        name="claude-code",
        display_name="Claude Code",
        config_path=tmp_path / "claude.json",
        config_format="json_mcp_nested_servers",
    )
    adapter_calls: list[tuple[Path, str, str | None]] = []
    mcp_calls: list[tuple[str, bool, bool]] = []

    def _connect_platform(path: Path, name: str, custom_path: str | None = None):
        adapter_calls.append((path, name, custom_path))
        return {
            "name": name,
            "path": str(tmp_path / "sessions"),
            "session_count": 2,
            "status": "connected",
        }

    def _connect_mcp_target(
        target: McpTarget,
        *,
        dry_run: bool = False,
        force: bool = False,
    ) -> McpConnectResult:
        mcp_calls.append((target.name, dry_run, force))
        return McpConnectResult(
            name=target.name,
            display_name=target.display_name,
            config_path=str(target.config_path),
            status="installed",
            installed=True,
            already_configured=False,
            dry_run=dry_run,
            message="test install",
        )

    monkeypatch.setattr(cli, "get_config", lambda: base_cfg)
    monkeypatch.setattr(cli, "connect_platform", _connect_platform)
    monkeypatch.setattr(cli, "resolve_mcp_target", lambda _name: target)
    monkeypatch.setattr(cli, "connect_mcp_target", _connect_mcp_target)

    code, payload = run_cli_json(
        ["connect", "claude-code", "--mode", "auto", "--force", "--json"]
    )
    assert code == 0
    assert adapter_calls == [(base_cfg.platforms_path, "claude", None)]
    assert mcp_calls == [("claude-code", False, True)]
    assert payload["mode"] == "auto"
    assert payload["adapters"][0]["status"] == "connected"
    assert payload["mcp_targets"][0]["status"] == "installed"


def test_connect_plugin_mode_is_pending_not_mcp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plugin mode is explicit pending work and does not run MCP setup."""

    def _unexpected_mcp_call(*_args, **_kwargs):
        raise AssertionError("plugin mode must not call MCP setup")

    monkeypatch.setattr(cli, "get_config", lambda: make_config(tmp_path / ".lerim"))
    monkeypatch.setattr(cli, "connect_mcp_target", _unexpected_mcp_call)
    code, payload = run_cli_json(
        ["connect", "openclaw", "--mode", "plugin", "--json"]
    )
    assert code == 1
    assert payload["name"] == "openclaw"
    assert payload["status"] == "planned_not_implemented"
    assert payload["installed"] is False


def test_ingest_help_uses_loaded_config_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_cfg = make_config(tmp_path / ".lerim")
    monkeypatch.setattr(
        cli,
        "get_config",
        lambda: replace(base_cfg, ingest_window_days=11, ingest_max_sessions=7),
    )
    parser = cli.build_parser()
    out = io.StringIO()
    with redirect_stdout(out), pytest.raises(SystemExit) as exc:
        parser.parse_args(["ingest", "--help"])
    assert exc.value.code == 0
    text = out.getvalue()
    assert "currently 11d" in text
    assert "ingest_max_sessions" in text
    assert "7)" in text


def test_project_add_help_lists_pi_adapter() -> None:
    """Project registration help keeps the native adapter list current."""
    parser = cli.build_parser()
    out = io.StringIO()
    with redirect_stdout(out), pytest.raises(SystemExit) as exc:
        parser.parse_args(["project", "add", "--help"])

    assert exc.value.code == 0
    text = out.getvalue()
    assert "Claude/Codex/Cursor/OpenCode/pi" in text
    assert "adapters" in text


def test_answer_parser_minimal_surface() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["answer", "what failed?"])
    assert args.command == "answer"
    assert args.question == "what failed?"


def test_answer_parser_rejects_limit_flag() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["answer", "what failed?", "--limit", "5"])
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
        "latest_ingest": None,
        "latest_curate": None,
    }
    monkeypatch.setattr(cli, "_api_get", lambda _path: fake_status)
    code, payload = run_cli_json(["status", "--json"])
    assert code == 0
    assert "queue" in payload
    assert "latest_ingest" in payload
    assert "latest_curate" in payload


def test_answer_forwards_to_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer command posts to /api/answer and prints the answer."""
    fake_response = {
        "answer": "Use bearer tokens.",
        "agent_session_id": "ses-1",
        "projects_used": [],
        "error": False,
    }
    monkeypatch.setattr(cli, "_api_post", lambda _path, _body: fake_response)
    code, payload = run_cli_json(["answer", "how to deploy", "--json"])
    assert code == 0
    assert payload["answer"] == "Use bearer tokens."


def test_answer_verbose_forwards_flag_and_prints_debug(monkeypatch: pytest.MonkeyPatch) -> None:
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
                        "kind": "model_step",
                        "parts": [
                            {"part_kind": "PlanContextRetrieval", "content": {}},
                        ],
                    },
                    {
                        "message_index": 1,
                        "kind": "retrieval",
                        "parts": [
                            {
                                "part_kind": "count",
                                "content": {
                                    "kind": "retrieval",
                                    "action_type": "count",
                                    "result_count": 3,
                                },
                            },
                        ],
                    },
                ],
            },
        }

    monkeypatch.setattr(cli, "_api_post", _fake_post)
    code, output = run_cli(["answer", "how to deploy", "--verbose"])
    assert code == 0
    assert captured["body"]["verbose"] is True
    assert "ANSWER TRACE" in output
    assert "[model] PlanContextRetrieval" in output
    assert "[retrieval] count results=3" in output


def test_answer_returns_nonzero_on_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_response = {
        "answer": "authentication_error: invalid api key",
        "error": True,
    }
    monkeypatch.setattr(cli, "_api_post", lambda _path, _body: fake_response)
    code, _output = run_cli(["answer", "how to deploy"])
    assert code == 1


def test_answer_returns_nonzero_when_server_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_api_post", _raise_api_error)
    code, _output = run_cli(["answer", "how to deploy"])
    assert code == 1


def test_memory_command_shows_help() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["memory"])
    assert exc.value.code == 0


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
        "latest_ingest": None,
        "latest_curate": None,
    }
    monkeypatch.setattr(cli, "_api_get", lambda _path: fake_status)
    code1, payload1 = run_cli_json(["status", "--json"])
    code2, payload2 = run_cli_json(["--json", "status"])
    assert code1 == 0
    assert code2 == 0
    # Both should produce valid status dicts with the same keys
    assert set(payload1.keys()) == set(payload2.keys())


def test_memory_reset_requires_scope() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["memory", "reset"])
    assert exc.value.code == 2


def test_up_build_flag_accepted() -> None:
    """The --build flag is accepted by the up subparser."""
    parser = cli.build_parser()
    args = parser.parse_args(["up", "--build"])
    assert args.command == "up"
    assert args.build is True


def test_up_no_build_flag_accepted() -> None:
    """The --no-build flag is accepted by the up subparser."""
    parser = cli.build_parser()
    args = parser.parse_args(["up", "--no-build"])
    assert args.command == "up"
    assert args.no_build is True


def test_up_default_has_build_false() -> None:
    """Default 'up' command has build=False."""
    parser = cli.build_parser()
    args = parser.parse_args(["up"])
    assert args.command == "up"
    assert args.build is False
