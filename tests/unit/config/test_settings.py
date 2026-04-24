"""Unit tests for settings.py coverage gaps not covered by test_config.py.

Tests: load_toml_file, _expand, strict typed readers, _to_fallback_models,
_to_string_tuple, _parse_string_table, _toml_value, _toml_write_dict,
save_config_patch, layer precedence, port validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lerim.config.settings import (
    _deep_merge,
    _expand,
    _to_fallback_models,
    _to_string_tuple,
    _parse_string_table,
    _toml_value,
    _toml_write_dict,
    _build_role,
    get_global_data_dir_path,
    get_user_config_path,
    get_user_env_path,
    load_toml_file,
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


# ---------------------------------------------------------------------------
# _expand
# ---------------------------------------------------------------------------


def test_expand_with_valid_path(tmp_path):
    """_expand resolves a provided path string."""
    result = _expand(str(tmp_path), default=Path("/default"))
    assert result == tmp_path


def test_expand_with_none():
    """_expand returns default when value is None."""
    default = Path("/fallback")
    assert _expand(None, default) == default


def test_expand_with_empty_string():
    """_expand returns default when value is empty string."""
    default = Path("/fallback")
    assert _expand("", default) == default


def test_expand_with_tilde():
    """_expand expands ~ to home directory."""
    result = _expand("~/test", default=Path("/default"))
    assert result == Path.home() / "test"


# ---------------------------------------------------------------------------
# _to_fallback_models
# ---------------------------------------------------------------------------


def test_fallback_models_from_list():
    """_to_fallback_models parses a list of model strings."""
    result = _to_fallback_models(["model-a", "model-b"])
    assert result == ("model-a", "model-b")


def test_fallback_models_from_csv_string():
    """_to_fallback_models parses comma-separated string."""
    result = _to_fallback_models("model-a, model-b, model-c")
    assert result == ("model-a", "model-b", "model-c")


def test_fallback_models_filters_blanks():
    """_to_fallback_models strips whitespace and filters empty items."""
    result = _to_fallback_models(["model-a", "  ", "", "model-b"])
    assert result == ("model-a", "model-b")


def test_fallback_models_non_list_non_string():
    """_to_fallback_models returns empty tuple for unsupported types."""
    assert _to_fallback_models(42) == ()
    assert _to_fallback_models(None) == ()


# ---------------------------------------------------------------------------
# _to_string_tuple
# ---------------------------------------------------------------------------


def test_string_tuple_from_list():
    """_to_string_tuple normalizes a list into a tuple of strings."""
    result = _to_string_tuple(["nebius", "together"])
    assert result == ("nebius", "together")


def test_string_tuple_from_csv():
    """_to_string_tuple parses comma-separated string."""
    result = _to_string_tuple("nebius, together")
    assert result == ("nebius", "together")


def test_string_tuple_filters_blanks():
    """_to_string_tuple strips empty items."""
    result = _to_string_tuple(["nebius", "", "  "])
    assert result == ("nebius",)


def test_string_tuple_unsupported_type():
    """_to_string_tuple returns empty tuple for non-list/string."""
    assert _to_string_tuple(123) == ()


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


def test_config_rejects_quoted_boolean_role_value(tmp_path, monkeypatch):
    """Quoted booleans stay strings and must not be coerced truthy."""
    explicit = tmp_path / "quoted_bool.toml"
    explicit.write_text(
        '[roles.agent]\nparallel_tool_calls = "false"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("LERIM_CONFIG", str(explicit))

    with pytest.raises(ValueError, match="parallel_tool_calls must be a boolean"):
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
            "provider": "anthropic",
            "model": "claude-3",
        },
        default_provider="openrouter",
        default_model="default-model",
    )
    assert role.provider == "anthropic"
    assert role.model == "claude-3"


def test_agent_role_explicit_request_limits():
    """_build_role uses explicit maintain/ask request limits when set."""
    role = _build_role(
        {
            "provider": "ollama",
            "model": "qwen3:8b",
            "max_iters_maintain": 15,
            "max_iters_ask": 6,
        },
        default_provider="openrouter",
        default_model="default",
    )
    assert role.max_iters_maintain == 15
    assert role.max_iters_ask == 6
