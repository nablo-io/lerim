"""Unit tests for MCP client configuration writers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import tomllib
import yaml

from lerim.integrations.mcp_connect import (
    McpTarget,
    connect_mcp_target,
    doctor_mcp_target,
    installed_mcp_targets,
    known_mcp_targets,
    resolve_mcp_target,
)


PUBLIC_MCP_DOCS = (
    "README.md",
    "docs/cli/mcp.md",
    "docs/cli/connect.md",
    "docs/guides/mcp-quickstart.md",
    "docs/integrations/verification.md",
)


def test_json_mcp_connect_dry_run_does_not_write(tmp_path: Path) -> None:
    """Dry-run JSON MCP install reports intent without creating a file."""
    target = McpTarget(
        name="demo",
        display_name="Demo",
        config_path=tmp_path / "settings.json",
        config_format="json_mcp_servers",
    )
    result = connect_mcp_target(target, dry_run=True)
    assert result.status == "would_create"
    assert result.dry_run is True
    assert not target.config_path.exists()


def test_json_mcp_connect_writes_standard_mcp_servers(tmp_path: Path) -> None:
    """JSON MCP clients receive a standard mcpServers.lerim entry."""
    path = tmp_path / "settings.json"
    path.write_text('{"theme":"dark"}\n', encoding="utf-8")
    target = McpTarget(
        name="demo",
        display_name="Demo",
        config_path=path,
        config_format="json_mcp_servers",
    )
    result = connect_mcp_target(target)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert result.status == "installed"
    assert result.backup_path
    assert data["theme"] == "dark"
    assert data["mcpServers"]["lerim"] == {
        "command": sys.executable,
        "args": ["-m", "lerim.mcp_server"],
    }


def test_nested_json_mcp_connect_preserves_existing_servers(tmp_path: Path) -> None:
    """Claude-style nested MCP configs keep existing server entries."""
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "mcp": {
                    "servers": {
                        "docs": {"command": "docs-server", "args": []},
                    }
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    target = McpTarget(
        name="claude-code",
        display_name="Claude Code",
        config_path=path,
        config_format="json_mcp_nested_servers",
    )
    result = connect_mcp_target(target)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert result.installed is True
    assert data["mcp"]["servers"]["docs"] == {"command": "docs-server", "args": []}
    assert data["mcp"]["servers"]["lerim"] == {
        "command": sys.executable,
        "args": ["-m", "lerim.mcp_server"],
    }


def test_claude_code_connect_writes_user_config_shape(tmp_path: Path) -> None:
    """Claude Code receives the user-level mcpServers shape used by its CLI."""
    path = tmp_path / ".claude.json"
    path.write_text('{"firstStartTime":"2026-05-19T00:00:00.000Z"}\n', encoding="utf-8")
    target = McpTarget(
        name="claude-code",
        display_name="Claude Code",
        config_path=path,
        config_format="json_claude_code",
    )
    result = connect_mcp_target(target)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert result.installed is True
    assert data["firstStartTime"] == "2026-05-19T00:00:00.000Z"
    assert data["mcpServers"]["lerim"] == {
        "type": "stdio",
        "command": sys.executable,
        "args": ["-m", "lerim.mcp_server"],
        "env": {},
    }


def test_opencode_mcp_connect_uses_local_command_array(tmp_path: Path) -> None:
    """OpenCode receives its top-level mcp local-command shape."""
    path = tmp_path / "opencode.json"
    target = McpTarget(
        name="opencode",
        display_name="OpenCode",
        config_path=path,
        config_format="json_opencode",
    )
    result = connect_mcp_target(target)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert result.installed is True
    assert data["mcp"]["lerim"] == {
        "type": "local",
        "command": [sys.executable, "-m", "lerim.mcp_server"],
        "enabled": True,
    }


def test_toml_mcp_connect_writes_codex_shape(tmp_path: Path) -> None:
    """TOML MCP clients receive an mcp_servers.lerim table."""
    path = tmp_path / "config.toml"
    path.write_text('model = "gpt-5.2"\n', encoding="utf-8")
    target = McpTarget(
        name="codex",
        display_name="Codex",
        config_path=path,
        config_format="toml_mcp_servers",
    )
    result = connect_mcp_target(target)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert result.installed is True
    assert data["model"] == "gpt-5.2"
    assert data["mcp_servers"]["lerim"] == {
        "command": sys.executable,
        "args": ["-m", "lerim.mcp_server"],
    }


def test_yaml_mcp_connect_writes_hermes_shape(tmp_path: Path) -> None:
    """YAML MCP clients receive an mcp_servers.lerim mapping."""
    path = tmp_path / "config.yaml"
    target = McpTarget(
        name="hermes",
        display_name="Hermes",
        config_path=path,
        config_format="yaml_mcp_servers",
    )
    result = connect_mcp_target(target)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert result.installed is True
    assert data["mcp_servers"]["lerim"] == {
        "command": sys.executable,
        "args": ["-m", "lerim.mcp_server"],
    }


def test_doctor_reports_configured_target(tmp_path: Path) -> None:
    """Doctor is read-only and reports whether the Lerim entry exists."""
    path = tmp_path / "settings.json"
    target = McpTarget(
        name="demo",
        display_name="Demo",
        config_path=path,
        config_format="json_mcp_servers",
    )
    connect_mcp_target(target)
    status = doctor_mcp_target(target)
    assert status["configured"] is True
    assert status["config_exists"] is True


def test_connect_reports_already_configured_and_force_rewrites(tmp_path: Path) -> None:
    """Repeated installs are idempotent, while force rewrites an existing entry."""
    path = tmp_path / "settings.json"
    target = McpTarget(
        name="demo",
        display_name="Demo",
        config_path=path,
        config_format="json_mcp_servers",
    )
    first = connect_mcp_target(target)
    second = connect_mcp_target(target)
    forced = connect_mcp_target(target, force=True)

    assert first.status == "installed"
    assert second.status == "already_configured"
    assert second.already_configured is True
    assert forced.status == "updated"
    assert forced.backup_path


def test_doctor_reports_parse_error_without_mutating(tmp_path: Path) -> None:
    """Doctor surfaces invalid config parse errors without writing a file."""
    path = tmp_path / "settings.json"
    path.write_text("{not-json", encoding="utf-8")
    target = McpTarget(
        name="demo",
        display_name="Demo",
        config_path=path,
        config_format="json_mcp_servers",
    )

    status = doctor_mcp_target(target)

    assert status["configured"] is False
    assert status["parse_error"]
    assert path.read_text(encoding="utf-8") == "{not-json"


def test_resolve_mcp_target_aliases() -> None:
    """Configured aliases resolve to their canonical MCP targets."""
    assert resolve_mcp_target("codex-cli").name == "codex"
    assert resolve_mcp_target("open-claw").name == "openclaw"
    assert resolve_mcp_target("cline-terminal").name == "cline-cli"
    assert resolve_mcp_target("unknown-target") is None


def test_installed_mcp_targets_uses_config_or_detect_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Installed-target detection honors config paths and detect directories."""
    present_config = McpTarget(
        name="present-config",
        display_name="Present Config",
        config_path=tmp_path / "config.json",
        config_format="json_mcp_servers",
    )
    present_detect = McpTarget(
        name="present-detect",
        display_name="Present Detect",
        config_path=tmp_path / "missing.json",
        config_format="json_mcp_servers",
        detect_paths=(tmp_path / "detect",),
    )
    missing = McpTarget(
        name="missing",
        display_name="Missing",
        config_path=tmp_path / "missing-too.json",
        config_format="json_mcp_servers",
    )
    present_config.config_path.write_text("{}", encoding="utf-8")
    present_detect.detect_paths[0].mkdir()
    monkeypatch.setattr(
        "lerim.integrations.mcp_connect.known_mcp_targets",
        lambda: (present_config, present_detect, missing),
    )

    assert {target.name for target in installed_mcp_targets()} == {
        "present-config",
        "present-detect",
    }


def test_known_targets_cover_confirmed_agent_batches() -> None:
    """Known MCP targets include every confirmed first- and second-batch agent."""
    expected = {
        "codex",
        "claude-code",
        "cursor",
        "opencode",
        "gemini-cli",
        "cline",
        "cline-cli",
        "claude-desktop",
        "openclaw",
        "hermes",
        "goose",
        "roo-code",
        "kilo-code",
        "windsurf",
        "openhuman",
    }

    assert {target.name for target in known_mcp_targets()} == expected


def test_known_target_paths_formats_and_docs_are_stable() -> None:
    """Known target metadata stays aligned with the public integration matrix."""
    expected = {
        "codex": (".codex/config.toml", "toml_mcp_servers", "https://developers.openai.com/codex"),
        "claude-code": (".claude.json", "json_claude_code", "https://docs.anthropic.com/en/docs/claude-code"),
        "cursor": (".cursor/mcp.json", "json_mcp_servers", "https://docs.cursor.com/tools/mcp"),
        "opencode": (".config/opencode/opencode.json", "json_opencode", "https://opencode.ai/docs/mcp-servers/"),
        "gemini-cli": (".gemini/settings.json", "json_mcp_servers", "https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md"),
        "cline": ("cline_mcp_settings.json", "json_mcp_servers", "https://docs.cline.bot/mcp/configuring-mcp-servers"),
        "cline-cli": (".cline/mcp.json", "json_mcp_servers", "https://docs.cline.bot/mcp/configuring-mcp-servers"),
        "claude-desktop": ("claude_desktop_config.json", "json_mcp_servers", "https://modelcontextprotocol.io/quickstart/user"),
        "openclaw": (".openclaw/openclaw.json", "json_mcp_nested_servers", "https://docs.openclaw.ai/cli/mcp"),
        "hermes": (".hermes/config.yaml", "yaml_mcp_servers", "https://docs.opencomputer.dev/agents/cores/hermes"),
        "goose": (".config/goose/config.yaml", "yaml_mcp_servers", "https://block.github.io/goose/"),
        "roo-code": ("mcp_settings.json", "json_mcp_servers", "https://docs.roocode.com/features/mcp/using-mcp-in-roo"),
        "kilo-code": ("mcp_settings.json", "json_mcp_servers", "https://kilocode.ai/docs/features/mcp/using-mcp-in-kilo-code"),
        "windsurf": (".codeium/windsurf/mcp_config.json", "json_mcp_servers", "https://docs.windsurf.com/windsurf/cascade/mcp"),
        "openhuman": (".openhuman/mcp.json", "json_mcp_servers", "https://github.com/tinyhumansai/openhuman"),
    }
    targets = {target.name: target for target in known_mcp_targets()}

    for name, (suffix, config_format, docs_url) in expected.items():
        target = targets[name]
        assert str(target.config_path).endswith(suffix), name
        assert target.config_format == config_format, name
        assert target.docs_url == docs_url, name


def test_integration_matrix_lists_every_known_mcp_target() -> None:
    """Public integration docs must not drift from known target names."""
    repo_root = Path(__file__).resolve().parents[3]
    matrix = (repo_root / "docs" / "integrations" / "matrix.md").read_text(
        encoding="utf-8"
    )

    for target in known_mcp_targets():
        assert f"`lerim connect {target.name} --mode mcp`" in matrix
        assert target.docs_url in matrix

    assert "Custom trace folder" in matrix
    assert "Generic trace import / MCP submit" in matrix


def test_integration_matrix_keeps_public_support_boundaries_clear() -> None:
    """Public matrix wording should not imply unavailable client/tool evidence."""
    repo_root = Path(__file__).resolve().parents[3]
    matrix = (repo_root / "docs" / "integrations" / "matrix.md").read_text(
        encoding="utf-8"
    )

    assert "Checked-in live tool-call artifact" not in matrix
    assert "MCP config support is not claimed" in matrix
    assert "Gemini CLI live `lerim_context_brief` tool-call artifact" in matrix
    assert (
        "Per-client live tool-call artifact is not available yet; "
        "session-end hook not shipped"
    ) in matrix
    assert (
        "Per-client live tool-call artifact is not available yet; "
        "current-client capture artifact not available yet"
    ) in matrix


def test_public_integration_docs_do_not_overclaim_installed_client_acceptance() -> None:
    """MCP setup docs should separate exposed tools from installed-client proof."""
    repo_root = Path(__file__).resolve().parents[3]
    integration_docs = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((repo_root / "docs" / "integrations").glob("*.md"))
    )
    docs = (
        (repo_root / "docs" / "concepts" / "mcp-vs-native-adapters.md").read_text(
            encoding="utf-8"
        )
        + "\n"
        + (repo_root / "docs" / "guides" / "connecting-agents.md").read_text(
            encoding="utf-8"
        )
        + "\n"
        + (repo_root / "docs" / "integrations" / "gemini-cli.md").read_text(
            encoding="utf-8"
        )
    )

    assert "client starts\n`lerim mcp`" not in docs
    assert "-m lerim.mcp_server" in docs
    assert "only claimed as live-tool-call\n  accepted" in docs
    assert "does not claim an installed Gemini\n`lerim_trace_submit` call yet" in docs
    assert "When Cline loads this config, it can access Lerim context tools" not in integration_docs
    assert "it can access Lerim context tools through MCP" not in integration_docs


def test_every_known_target_writer_installs_and_doctor_validates(tmp_path: Path) -> None:
    """Every named MCP target can be written and validated with a temp config path."""
    for target in known_mcp_targets():
        config_path = tmp_path / target.name / target.config_path.name
        temp_target = McpTarget(
            name=target.name,
            display_name=target.display_name,
            config_path=config_path,
            config_format=target.config_format,
            aliases=target.aliases,
            detect_paths=(),
            docs_url=target.docs_url,
        )

        result = connect_mcp_target(temp_target)
        status = doctor_mcp_target(temp_target)

        assert result.installed is True, target.name
        assert status["configured"] is True, target.name
        assert status["parse_error"] == "", target.name


def test_public_mcp_docs_do_not_recommend_bare_lerim_client_configs() -> None:
    """Public MCP docs must avoid the bare command that fails in small PATH envs."""
    repo_root = Path(__file__).resolve().parents[3]
    forbidden = (
        'command: "lerim"',
        '"command": "lerim"',
        "command = \"lerim\"",
        "Example client command: lerim mcp",
        "which lerim",
        "client starts that command",
    )

    for relative_path in PUBLIC_MCP_DOCS:
        text = (repo_root / relative_path).read_text(encoding="utf-8")
        for phrase in forbidden:
            assert phrase not in text, f"{relative_path} contains stale MCP advice: {phrase}"

    quickstart = (repo_root / "docs/guides/mcp-quickstart.md").read_text(
        encoding="utf-8"
    )
    assert '["-m", "lerim.mcp_server"]' in quickstart
    assert "/absolute/path/to/python" in quickstart


def test_public_docs_explain_mcp_retrieval_vs_capture_boundary() -> None:
    """Docs must not blur MCP tool access with automatic trace capture."""
    repo_root = Path(__file__).resolve().parents[3]
    docs = {
        relative_path: (repo_root / relative_path).read_text(encoding="utf-8").lower()
        for relative_path in (
            "docs/cli/mcp.md",
            "docs/guides/mcp-quickstart.md",
            "docs/guides/connecting-agents.md",
            "docs/concepts/supported-agents.md",
            "docs/integrations/openclaw.md",
        )
    }

    assert "read tools" in docs["docs/cli/mcp.md"]
    assert "submit tool" in docs["docs/cli/mcp.md"]
    assert "not automatic" in docs["docs/cli/mcp.md"]
    assert "does not automatically import" in docs["docs/guides/mcp-quickstart.md"]
    assert "scope_type" in docs["docs/guides/mcp-quickstart.md"]
    assert "retrieval and explicit-submit path" in docs[
        "docs/guides/connecting-agents.md"
    ]
    assert "stable exporter" in docs["docs/concepts/supported-agents.md"]
    assert "not implied by mcp config support" in docs[
        "docs/concepts/supported-agents.md"
    ]
    assert "does not make lerim read openclaw" in docs[
        "docs/integrations/openclaw.md"
    ]
    assert "canonical jsonl" in docs["docs/integrations/openclaw.md"]
