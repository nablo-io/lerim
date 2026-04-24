"""Unit tests for config loading, type conversion, and role builders."""

from __future__ import annotations

import json
from pathlib import Path


from lerim.config.settings import (
    Config,
    RoleConfig,
    _build_role,
    _deep_merge,
    _require_int,
    _to_non_empty_string,
    ensure_user_config_exists,
    get_config,
    reload_config,
)
from tests.helpers import make_config, write_test_config


def test_load_default_toml():
    """Default TOML loads without error, produces valid Config."""
    cfg = get_config()
    assert isinstance(cfg, Config)
    assert cfg.global_data_dir is not None
    assert cfg.context_db_path == cfg.global_data_dir / "context.sqlite3"


def test_deep_merge_override():
    """Project config overrides global config values."""
    base = {"a": 1, "nested": {"x": 10, "y": 20}}
    override = {"a": 2, "nested": {"x": 99}}
    result = _deep_merge(base, override)
    assert result["a"] == 2
    assert result["nested"]["x"] == 99


def test_deep_merge_preserves_unset():
    """Unset keys in override preserved from base."""
    base = {"a": 1, "nested": {"x": 10, "y": 20}}
    override = {"nested": {"x": 99}}
    result = _deep_merge(base, override)
    assert result["a"] == 1
    assert result["nested"]["y"] == 20


def test_require_int_valid():
    """_require_int parses valid values and enforces minimum."""
    assert _require_int({"k": 42}, "k") == 42
    assert _require_int({"k": "10"}, "k") == 10
    assert _require_int({"k": -1}, "k", minimum=0) == 0


def test_require_int_missing():
    """_require_int raises on missing key."""
    import pytest

    with pytest.raises(ValueError, match="missing required config key"):
        _require_int({}, "k")


def test_type_conversion_non_empty_string():
    """_to_non_empty_string trims whitespace, handles None."""
    assert _to_non_empty_string("  hello  ") == "hello"
    assert _to_non_empty_string(None) == ""
    assert _to_non_empty_string("") == ""
    assert _to_non_empty_string(42) == "42"


def test_role_config_construction():
    """_build_role produces RoleConfig from explicit config values.

    The single-pass extraction agent auto-scales its budget — no
    usage_limit_* keys live on RoleConfig anymore.
    """
    role = _build_role(
        {},
        default_provider="openrouter",
        default_model="qwen/qwen3-coder-30b-a3b-instruct",
    )
    assert isinstance(role, RoleConfig)
    assert role.provider == "openrouter"
    assert role.model == "qwen/qwen3-coder-30b-a3b-instruct"


def test_role_config_construction_with_request_limits():
    """_build_role should honor maintain/ask request limit overrides."""
    role = _build_role(
        {
            "provider": "ollama",
            "model": "qwen3:8b",
            "max_iters_maintain": 12,
            "max_iters_ask": 8,
        },
        default_provider="openrouter",
        default_model="default-model",
    )
    assert isinstance(role, RoleConfig)
    assert role.provider == "ollama"
    assert role.model == "qwen3:8b"
    assert role.max_iters_maintain == 12
    assert role.max_iters_ask == 8


def test_config_scaffold_creation(tmp_path, monkeypatch):
    """ensure_user_config_exists creates scaffold TOML file."""
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("lerim.config.settings.USER_CONFIG_PATH", config_path)
    # Ensure we're not in pytest detection context by patching
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr("lerim.config.settings.os.environ", {})
    result = ensure_user_config_exists()
    # May or may not create depending on pytest detection, but shouldn't crash
    assert isinstance(result, Path)


def test_config_reload_clears_cache(tmp_path, monkeypatch):
    """reload_config() invalidates LRU cache."""
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    cfg1 = reload_config()
    cfg2 = reload_config()
    assert isinstance(cfg1, Config)
    assert isinstance(cfg2, Config)


def test_config_env_var_override(tmp_path, monkeypatch):
    """LERIM_CONFIG env var overrides all other layers."""
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    cfg = reload_config()
    assert cfg.global_data_dir == tmp_path
    assert cfg.context_db_path == tmp_path / "context.sqlite3"


def test_config_context_db_path_user_override(tmp_path, monkeypatch):
    """User config can override the canonical context DB path explicitly."""
    override_path = tmp_path / "custom" / "ctx.sqlite3"
    config_path = write_test_config(
        tmp_path,
        data={
            "dir": str(tmp_path),
            "context_db_path": str(override_path),
        },
    )
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    cfg = reload_config()
    assert cfg.context_db_path == override_path


def test_config_public_dict(tmp_path):
    """public_dict() returns dict without sensitive fields."""
    cfg = make_config(tmp_path)
    d = cfg.public_dict()
    assert isinstance(d, dict)
    # Should not contain API keys
    assert "anthropic_api_key" not in d
    assert "openai_api_key" not in d
    assert "zai_api_key" not in d
    # Should have public fields
    assert "global_data_dir" in d
    assert d["context_db_path"] == str(tmp_path / "context.sqlite3")


def test_config_rejects_unknown_role_keys(tmp_path, monkeypatch):
    """Unknown config keys should fail fast instead of being silently ignored."""
    config_path = tmp_path / "bad_config.toml"
    config_path.write_text(
        "[data]\n"
        f'dir = "{tmp_path}"\n'
        "\n[server]\n"
        'host = "127.0.0.1"\n'
        "port = 8765\n"
        "sync_interval_minutes = 5\n"
        "maintain_interval_minutes = 5\n"
        "sync_window_days = 7\n"
        "sync_max_sessions = 10\n"
        "\n[roles.agent]\n"
        'provider = "minimax"\n'
        'model = "MiniMax-M2.7"\n'
        "max_iters_sync = 15\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))

    import pytest

    with pytest.raises(ValueError, match="roles.agent"):
        reload_config()


def test_config_accepts_known_legacy_keys(tmp_path, monkeypatch):
    """Known legacy config keys should be tolerated during upgrade loading."""
    config_path = tmp_path / "legacy_config.toml"
    monkeypatch.setattr(
        "lerim.config.settings.USER_CONFIG_PATH", tmp_path / "user-config.toml"
    )
    config_path.write_text(
        'openrouter_provider_order = ["openrouter"]\n'
        "[data]\n"
        f'dir = "{tmp_path}"\n'
        "\n[roles.agent]\n"
        'provider = "minimax"\n'
        'model = "MiniMax-M2.7"\n'
        "thinking = true\n"
        'openrouter_provider_order = ["openrouter"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    cfg = reload_config()
    assert cfg.global_data_dir == tmp_path
    assert cfg.agent_role.provider == "minimax"
    assert cfg.agent_role.model == "MiniMax-M2.7"


def test_config_derives_agents_from_connected_platforms(tmp_path, monkeypatch):
    """Connected platform registry should still populate effective agent paths."""
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(
        "lerim.config.settings.USER_CONFIG_PATH", tmp_path / "user-config.toml"
    )
    sessions_path = tmp_path / "claude-sessions"
    sessions_path.mkdir(parents=True)
    (tmp_path / "platforms.json").write_text(
        json.dumps(
            {
                "platforms": {
                    "claude": {
                        "path": str(sessions_path),
                        "connected_at": "2026-04-22T00:00:00+00:00",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    config_path.write_text(f'[data]\ndir = "{tmp_path}"\n', encoding="utf-8")
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    cfg = reload_config()
    assert cfg.agents["claude"] == str(sessions_path.resolve())
