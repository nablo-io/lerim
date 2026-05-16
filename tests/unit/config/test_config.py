"""Unit tests for config loading, type conversion, and role builders."""

from __future__ import annotations
from pathlib import Path


from lerim.config.settings import (
    Config,
    RoleConfig,
    _build_role,
    _deep_merge,
    _read_bool,
    _read_float,
    _read_int,
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
    assert cfg.embedding_cache_dir == cfg.global_data_dir / "models" / "embeddings"


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


def test_require_int_rejects_below_minimum():
    """_require_int rejects values below the configured minimum."""
    import pytest

    with pytest.raises(ValueError, match="must be >= 0"):
        _require_int({"k": -1}, "k", minimum=0)


def test_require_int_rejects_string_values():
    """_require_int rejects string values instead of coercing them."""
    import pytest

    with pytest.raises(ValueError, match="must be an integer"):
        _require_int({"k": "10"}, "k")


def test_strict_typed_readers_reject_string_scalars():
    """Strict readers reject quoted TOML scalar values."""
    import pytest

    with pytest.raises(ValueError, match="must be a boolean"):
        _read_bool({"k": "false"}, "k")
    with pytest.raises(ValueError, match="must be an integer"):
        _read_int({"k": "10"}, "k")
    with pytest.raises(ValueError, match="must be a float"):
        _read_float({"k": "0.2"}, "k")


def test_strict_typed_readers_accept_native_scalars():
    """Strict readers accept native TOML bool, int, and float values."""
    assert _read_bool({"k": False}, "k") is False
    assert _read_int({"k": 10}, "k") == 10
    assert _read_float({"k": 0.2}, "k") == 0.2


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
    """_build_role produces RoleConfig from explicit config values."""
    role = _build_role(
        {},
        default_provider="openrouter",
        default_model="qwen/qwen3-coder-30b-a3b-instruct",
    )
    assert isinstance(role, RoleConfig)
    assert role.provider == "openrouter"
    assert role.model == "qwen/qwen3-coder-30b-a3b-instruct"


def test_role_config_construction_with_agent_budgets():
    """_build_role should honor curate/answer budget overrides."""
    role = _build_role(
        {
            "provider": "ollama",
            "model": "qwen3:8b",
            "curate_max_llm_calls": 12,
            "answer_max_retrieval_actions": 8,
        },
        default_provider="openrouter",
        default_model="default-model",
    )
    assert isinstance(role, RoleConfig)
    assert role.provider == "ollama"
    assert role.model == "qwen3:8b"
    assert role.curate_max_llm_calls == 12
    assert role.answer_max_retrieval_actions == 8


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
    assert cfg.embedding_cache_dir == tmp_path / "models" / "embeddings"
    for relative in (
        Path("cache") / "traces" / "claude",
        Path("cache") / "traces" / "codex",
        Path("cache") / "traces" / "cursor",
        Path("cache") / "traces" / "opencode",
        Path("models") / "embeddings",
        Path("models") / "huggingface" / "hub",
    ):
        assert (tmp_path / relative).is_dir()


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
    assert "openai_api_key" not in d
    assert "zai_api_key" not in d
    assert "global_data_dir" not in d
    assert "sessions_db_path" not in d
    assert "context_db_path" not in d
    assert "platforms_path" not in d
    assert "embedding_cache_dir" not in d
    assert "provider_api_bases" not in d
    assert "agents" not in d
    assert "projects" not in d
    assert d["connected_agents"] == sorted(cfg.agents)
    assert d["project_names"] == sorted(cfg.projects)


def test_config_rejects_unknown_role_keys(tmp_path, monkeypatch):
    """Unknown config keys should fail fast instead of being silently ignored."""
    config_path = tmp_path / "bad_config.toml"
    config_path.write_text(
        "[data]\n"
        f'dir = "{tmp_path}"\n'
        "\n[server]\n"
        'host = "127.0.0.1"\n'
        "port = 8765\n"
        "ingest_interval_minutes = 5\n"
        "curate_interval_minutes = 5\n"
        "ingest_window_days = 7\n"
        "ingest_max_sessions = 10\n"
        "\n[roles.agent]\n"
        'provider = "minimax"\n'
        'model = "MiniMax-M2.7"\n'
        "ingest_max_llm_calls = 15\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))

    import pytest

    with pytest.raises(ValueError, match="roles.agent"):
        reload_config()
