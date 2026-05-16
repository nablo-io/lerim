"""Unit tests for settings.py coverage gaps not covered by test_config.py.

Tests: load_toml_file, strict typed readers, _parse_string_table,
_toml_value, _toml_write_dict, save_config_patch, layer precedence,
port validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lerim.config.settings import (
    _deep_merge,
    _parse_string_table,
    _toml_value,
    _toml_write_dict,
    _build_role,
    get_global_data_dir_path,
    get_trace_cache_dir,
    get_user_config_path,
    get_user_env_path,
    load_toml_file,
    remove_legacy_memory_dir,
    save_config_patch,
    reload_config,
)


# ---------------------------------------------------------------------------
# load_toml_file
# ---------------------------------------------------------------------------


def test_load_toml_file_valid(tmp_path):
    """load_toml_file returns parsed dict from valid TOML file."""
    toml_file = tmp_path / "test.toml"
    toml_file.write_text('[section]\nkey = "value"\n', encoding="utf-8")
    result = load_toml_file(toml_file)
    assert result == {"section": {"key": "value"}}


def test_load_toml_file_missing():
    """load_toml_file returns empty dict for non-existent path."""
    assert load_toml_file(Path("/nonexistent/path.toml")) == {}


def test_load_toml_file_none():
    """load_toml_file returns empty dict when path is None."""
    assert load_toml_file(None) == {}


def test_load_toml_file_invalid(tmp_path):
    """load_toml_file raises for malformed TOML."""
    bad = tmp_path / "bad.toml"
    bad.write_text("this is not valid toml [[[", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid TOML config file"):
        load_toml_file(bad)


def test_get_trace_cache_dir_uses_active_data_root(tmp_path, monkeypatch):
    """Compacted trace caches live under cache/traces/<agent>."""
    explicit = tmp_path / "config.toml"
    data_dir = tmp_path / "data"
    explicit.write_text(f'[data]\ndir = "{data_dir}"\n', encoding="utf-8")
    monkeypatch.setenv("LERIM_CONFIG", str(explicit))

    assert get_trace_cache_dir("codex") == data_dir / "cache" / "traces" / "codex"


# ---------------------------------------------------------------------------
# _parse_string_table
# ---------------------------------------------------------------------------


def test_parse_string_table_simple():
    """_parse_string_table handles name = 'path' entries."""
    raw = {"claude": "~/.claude/projects", "codex": "~/.codex/sessions"}
    result = _parse_string_table(raw, section="agents")
    assert result == {"claude": "~/.claude/projects", "codex": "~/.codex/sessions"}


def test_parse_string_table_rejects_dict_entries():
    """_parse_string_table rejects undocumented object-shaped entries."""
    raw = {"claude": {"path": "/home/user/.claude"}}
    with pytest.raises(ValueError, match="agents.claude must be a string"):
        _parse_string_table(raw, section="agents")


def test_parse_string_table_rejects_non_string_entries():
    """_parse_string_table rejects scalar values that are not strings."""
    raw = {"auto_unload": True}
    with pytest.raises(ValueError, match="providers.auto_unload must be a string"):
        _parse_string_table(raw, section="providers")


def test_parse_string_table_skips_empty():
    """_parse_string_table skips entries with empty/None values."""
    raw = {"good": "/path", "bad": "", "none": None}
    result = _parse_string_table(raw, section="agents")
    assert result == {"good": "/path"}


# ---------------------------------------------------------------------------
# _toml_value
# ---------------------------------------------------------------------------


def test_toml_value_bool():
    """_toml_value serializes booleans to TOML true/false."""
    assert _toml_value(True) == "true"
    assert _toml_value(False) == "false"


def test_toml_value_int():
    """_toml_value serializes integers as plain numbers."""
    assert _toml_value(42) == "42"


def test_toml_value_float():
    """_toml_value serializes floats as plain numbers."""
    assert _toml_value(3.14) == "3.14"


def test_toml_value_string():
    """_toml_value serializes strings with double quotes."""
    assert _toml_value("hello") == '"hello"'


def test_toml_value_string_escapes():
    """_toml_value escapes backslashes and quotes in strings."""
    assert _toml_value('say "hi"') == '"say \\"hi\\""'


def test_toml_value_list():
    """_toml_value serializes lists with brackets."""
    assert _toml_value(["a", "b"]) == '["a", "b"]'


def test_toml_value_tuple():
    """_toml_value serializes tuples like lists."""
    assert _toml_value(("x", "y")) == '["x", "y"]'


# ---------------------------------------------------------------------------
# _toml_write_dict
# ---------------------------------------------------------------------------


def test_toml_write_dict_flat():
    """_toml_write_dict writes scalar key=value lines."""
    lines: list[str] = []
    _toml_write_dict(lines, {"key": "val", "num": 42}, prefix="section")
    text = "".join(lines)
    assert 'key = "val"' in text
    assert "num = 42" in text


def test_toml_write_dict_nested():
    """_toml_write_dict creates [section.subsection] headers for nested dicts."""
    lines: list[str] = []
    _toml_write_dict(lines, {"sub": {"key": "val"}}, prefix="parent")
    text = "".join(lines)
    assert "[parent.sub]" in text
    assert 'key = "val"' in text


# ---------------------------------------------------------------------------
# save_config_patch
# ---------------------------------------------------------------------------


def test_save_config_patch_roundtrip(tmp_path, monkeypatch):
    """save_config_patch writes TOML and reload reads it back."""
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("lerim.config.settings.USER_CONFIG_PATH", config_path)
    cfg = save_config_patch({"server": {"port": 9999}})
    assert cfg.server_port == 9999


def test_save_config_patch_deep_merges(tmp_path, monkeypatch):
    """save_config_patch deep-merges with existing config."""
    config_path = tmp_path / "config.toml"
    config_path.write_text('[server]\nhost = "0.0.0.0"\n', encoding="utf-8")
    monkeypatch.setattr("lerim.config.settings.USER_CONFIG_PATH", config_path)
    cfg = save_config_patch({"server": {"port": 9999}})
    assert cfg.server_host == "0.0.0.0"
    assert cfg.server_port == 9999


def test_explicit_config_path_is_the_writable_target(tmp_path, monkeypatch):
    """Writes should target the explicit override file when LERIM_CONFIG is set."""
    explicit = tmp_path / "explicit.toml"
    monkeypatch.setenv("LERIM_CONFIG", str(explicit))

    assert get_user_config_path() == explicit

    cfg = save_config_patch({"server": {"port": 9876}})
    assert cfg.server_port == 9876
    assert explicit.exists()


def test_user_env_path_tracks_effective_data_dir(tmp_path, monkeypatch):
    """The active .env path should follow the configured global data dir."""
    explicit = tmp_path / "config.toml"
    explicit.write_text(f'[data]\ndir = "{tmp_path / "custom-root"}"\n', encoding="utf-8")
    monkeypatch.setenv("LERIM_CONFIG", str(explicit))

    assert get_global_data_dir_path() == tmp_path / "custom-root"
    assert get_user_env_path() == tmp_path / "custom-root" / ".env"


def test_remove_legacy_memory_dir_ignores_files(tmp_path):
    """Only the retired directory is removed; an unexpected file is left alone."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    legacy_path = data_dir / "memory"
    legacy_path.write_text("not a directory", encoding="utf-8")

    assert remove_legacy_memory_dir(data_dir) is False
    assert legacy_path.is_file()


# ---------------------------------------------------------------------------
# Layer precedence
# ---------------------------------------------------------------------------


def test_layer_precedence_explicit_overrides(tmp_path, monkeypatch):
    """LERIM_CONFIG env var layer overrides all other layers."""
    explicit = tmp_path / "explicit.toml"
    explicit.write_text("[server]\nport = 1234\n", encoding="utf-8")
    monkeypatch.setenv("LERIM_CONFIG", str(explicit))
    cfg = reload_config()
    assert cfg.server_port == 1234


def test_layer_precedence_explicit_context_db_path_override(tmp_path, monkeypatch):
    """Explicit config layer can override the canonical context DB path."""
    explicit = tmp_path / "explicit.toml"
    explicit.write_text(
        "[data]\n"
        f'dir = "{tmp_path}"\n'
        f'context_db_path = "{tmp_path / "db" / "ctx.sqlite3"}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("LERIM_CONFIG", str(explicit))
    cfg = reload_config()
    assert cfg.context_db_path == tmp_path / "db" / "ctx.sqlite3"


def test_observability_mlflow_enabled_from_config(tmp_path, monkeypatch):
    """MLflow tracing can be enabled persistently from config.toml."""
    explicit = tmp_path / "observability.toml"
    explicit.write_text(
        f'[data]\ndir = "{tmp_path}"\n\n'
        "[observability]\nmlflow_enabled = true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LERIM_CONFIG", str(explicit))
    monkeypatch.delenv("LERIM_MLFLOW", raising=False)

    cfg = reload_config()

    assert cfg.mlflow_enabled is True


def test_mlflow_env_override_takes_precedence_over_config(tmp_path, monkeypatch):
    """LERIM_MLFLOW remains a one-off override over the persistent setting."""
    explicit = tmp_path / "observability.toml"
    explicit.write_text(
        f'[data]\ndir = "{tmp_path}"\n\n'
        "[observability]\nmlflow_enabled = true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LERIM_CONFIG", str(explicit))
    monkeypatch.setenv("LERIM_MLFLOW", "false")

    cfg = reload_config()

    assert cfg.mlflow_enabled is False


def test_observability_mlflow_enabled_rejects_quoted_boolean(tmp_path, monkeypatch):
    """Observability booleans must be native TOML booleans."""
    explicit = tmp_path / "bad_observability.toml"
    explicit.write_text(
        f'[data]\ndir = "{tmp_path}"\n\n'
        '[observability]\nmlflow_enabled = "true"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("LERIM_CONFIG", str(explicit))
    monkeypatch.delenv("LERIM_MLFLOW", raising=False)

    with pytest.raises(ValueError, match="mlflow_enabled must be a boolean"):
        reload_config()


def test_config_rejects_removed_parallel_tool_calls_key(tmp_path, monkeypatch):
    """Removed role keys should fail loudly instead of being ignored."""
    explicit = tmp_path / "quoted_bool.toml"
    explicit.write_text(
        '[roles.agent]\nparallel_tool_calls = "false"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("LERIM_CONFIG", str(explicit))

    with pytest.raises(ValueError, match="unknown config key"):
        reload_config()


def test_config_rejects_quoted_provider_boolean(tmp_path, monkeypatch):
    """Provider booleans must be native TOML booleans, not strings."""
    explicit = tmp_path / "quoted_provider_bool.toml"
    explicit.write_text(
        '[providers]\nauto_unload = "false"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("LERIM_CONFIG", str(explicit))

    with pytest.raises(ValueError, match="auto_unload must be a boolean"):
        reload_config()


@pytest.mark.parametrize("key", ["provider", "model", "api_base"])
def test_config_rejects_invalid_role_string_scalars(tmp_path, monkeypatch, key):
    """Role provider/model/api_base values must be native TOML strings."""
    explicit = tmp_path / f"bad_{key}.toml"
    explicit.write_text(
        f"[roles.agent]\n{key} = 42\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LERIM_CONFIG", str(explicit))

    with pytest.raises(ValueError, match=f"{key} must be a string"):
        reload_config()


def test_config_rejects_removed_fallback_models_key(tmp_path, monkeypatch):
    """Removed fallback configuration should fail loudly."""
    explicit = tmp_path / "bad_fallback_item.toml"
    explicit.write_text(
        '[roles.agent]\nfallback_models = ["openrouter:x-ai/grok-4.1-fast"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("LERIM_CONFIG", str(explicit))

    with pytest.raises(ValueError, match="unknown config key"):
        reload_config()


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("data", "dir", "42"),
        ("data", "context_db_path", "42"),
        ("server", "host", "42"),
        ("semantic_search", "embedding_model_id", "42"),
        ("semantic_search", "embedding_cache_dir", "42"),
        ("cloud", "endpoint", "42"),
        ("cloud", "token", "42"),
    ],
)
def test_config_rejects_invalid_string_and_path_scalars(
    tmp_path,
    monkeypatch,
    section,
    key,
    value,
):
    """String/path TOML fields must be native TOML strings, not coerced scalars."""
    explicit = tmp_path / f"bad_{section}_{key}.toml"
    explicit.write_text(
        f"[{section}]\n{key} = {value}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LERIM_CONFIG", str(explicit))

    with pytest.raises(ValueError, match=f"{key} must be a string"):
        reload_config()


@pytest.mark.parametrize(
    ("env_name", "section", "key", "env_value", "expected_attr"),
    [
        (
            "LERIM_CLOUD_ENDPOINT",
            "cloud",
            "endpoint",
            "https://cloud.example.test",
            "cloud_endpoint",
        ),
        ("LERIM_CLOUD_TOKEN", "cloud", "token", "token-from-env", "cloud_token"),
    ],
)
def test_cloud_env_overrides_do_not_parse_shadowed_toml_scalars(
    tmp_path,
    monkeypatch,
    env_name,
    section,
    key,
    env_value,
    expected_attr,
):
    """Documented cloud env overrides stay authoritative over TOML values."""
    explicit = tmp_path / f"env_{key}.toml"
    explicit.write_text(
        f"[{section}]\n{key} = 42\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LERIM_CONFIG", str(explicit))
    monkeypatch.setenv(env_name, env_value)

    cfg = reload_config()

    assert getattr(cfg, expected_attr) == env_value


def test_provider_auto_unload_is_not_parsed_as_api_base(tmp_path, monkeypatch):
    """Provider URL parsing ignores the documented auto_unload boolean."""
    explicit = tmp_path / "provider_bool.toml"
    explicit.write_text(
        "[providers]\nauto_unload = false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LERIM_CONFIG", str(explicit))

    cfg = reload_config()

    assert cfg.auto_unload is False
    assert "auto_unload" not in cfg.provider_api_bases


def test_config_rejects_retired_agent_role_keys(tmp_path, monkeypatch):
    """Retired role keys should fail loudly instead of being ignored."""
    explicit = tmp_path / "retired_role_keys.toml"
    explicit.write_text(
        "[roles.agent]\n"
        'openrouter_provider_order = "x-ai/grok-4.1-fast, openai/gpt-5.2"\n'
        "thinking = true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LERIM_CONFIG", str(explicit))

    with pytest.raises(ValueError, match="unknown config key"):
        reload_config()


def test_deep_merge_adds_new_keys():
    """_deep_merge adds keys from override that don't exist in base."""
    base = {"a": 1}
    override = {"b": 2}
    result = _deep_merge(base, override)
    assert result == {"a": 1, "b": 2}


def test_deep_merge_replaces_non_dict_with_dict():
    """_deep_merge replaces scalar with dict when override has dict."""
    base = {"a": 1}
    override = {"a": {"nested": True}}
    result = _deep_merge(base, override)
    assert result == {"a": {"nested": True}}


# ---------------------------------------------------------------------------
# Port validation
# ---------------------------------------------------------------------------


def test_port_over_65535_raises(tmp_path, monkeypatch):
    """Port > 65535 is rejected."""
    explicit = tmp_path / "bad_port.toml"
    explicit.write_text("[server]\nport = 99999\n", encoding="utf-8")
    monkeypatch.setenv("LERIM_CONFIG", str(explicit))
    with pytest.raises(ValueError, match="port must be <= 65535"):
        reload_config()


# ---------------------------------------------------------------------------
# Role builder edge cases
# ---------------------------------------------------------------------------


def test_agent_role_explicit_overrides():
    """_build_role uses explicit values over defaults.

    Usage limits no longer live on RoleConfig — the single-pass extraction
    agent auto-scales its budget from trace size, so this test only checks
    the provider/model override path.
    """
    role = _build_role(
        {
            "provider": "openrouter",
            "model": "claude-3",
        },
        default_provider="openrouter",
        default_model="default-model",
    )
    assert role.provider == "openrouter"
    assert role.model == "claude-3"


def test_agent_role_explicit_agent_budgets():
    """_build_role uses explicit curate/answer budgets when set."""
    role = _build_role(
        {
            "provider": "ollama",
            "model": "qwen3:8b",
            "curate_max_llm_calls": 15,
            "answer_max_retrieval_actions": 6,
        },
        default_provider="openrouter",
        default_model="default",
    )
    assert role.curate_max_llm_calls == 15
    assert role.answer_max_retrieval_actions == 6
