"""Unit tests for provider helpers used by BAML-backed agents."""

from __future__ import annotations

from dataclasses import replace

import pytest

from lerim.agents.baml_runtime import (
    MINIMAX_TEMPERATURE_FLOOR,
    _resolve_base_url,
    _resolve_temperature,
    model_label,
)
from lerim.config.providers import (
    api_key_env_for_provider,
    api_key_for_provider,
    ensure_provider_api_key,
    list_provider_models,
    normalize_model_name,
    normalize_openai_base_url,
    validate_provider_for_role,
)
from lerim.config.settings import RoleConfig
from tests.helpers import make_config


def test_normalize_model_name_known_and_unknown() -> None:
    assert normalize_model_name("minimax", "minimax-m2.7") == "MiniMax-M2.7"
    assert normalize_model_name("openrouter", "any/model") == "any/model"


def test_validate_provider_for_role_rejects_unknown_provider() -> None:
    with pytest.raises(RuntimeError, match="Unknown provider"):
        validate_provider_for_role("unknown", "agent")


def test_api_key_resolution(tmp_path) -> None:
    cfg = make_config(tmp_path)
    cfg = replace(
        cfg,
        zai_api_key="z-key",
        openrouter_api_key="or-key",
        openai_api_key="oa-key",
        minimax_api_key="mm-key",
        opencode_api_key="oc-key",
    )
    assert api_key_for_provider(cfg, "zai") == "z-key"
    assert api_key_for_provider(cfg, "openrouter") == "or-key"
    assert api_key_for_provider(cfg, "openai") == "oa-key"
    assert api_key_for_provider(cfg, "minimax") == "mm-key"
    assert api_key_for_provider(cfg, "opencode_go") == "oc-key"
    assert api_key_for_provider(cfg, "ollama") is None
    assert api_key_for_provider(cfg, "mlx") is None


def test_ensure_provider_api_key_requires_remote_key(tmp_path) -> None:
    cfg = make_config(tmp_path)
    cfg = replace(cfg, openrouter_api_key=None)

    with pytest.raises(RuntimeError, match="missing_api_key:OPENROUTER_API_KEY"):
        ensure_provider_api_key(cfg, "openrouter")


def test_ensure_provider_api_key_allows_local_provider_without_key(tmp_path) -> None:
    cfg = make_config(tmp_path)

    assert ensure_provider_api_key(cfg, "ollama") == "ollama"
    assert api_key_env_for_provider("ollama") == ""


def test_resolve_base_url_prefers_role_api_base_for_matching_provider(tmp_path) -> None:
    cfg = make_config(tmp_path)
    role = RoleConfig(
        provider="ollama",
        model="qwen3:8b",
        api_base="http://127.0.0.1:11434",
    )

    assert (
        _resolve_base_url(cfg, role_cfg=role, provider="ollama", override=None)
        == "http://127.0.0.1:11434/v1"
    )


def test_resolve_base_url_rejects_provider_without_default_base(tmp_path) -> None:
    cfg = make_config(tmp_path)
    role = RoleConfig(provider="unknown", model="model")

    with pytest.raises(RuntimeError, match="missing_api_base"):
        _resolve_base_url(cfg, role_cfg=role, provider="unknown", override=None)


def test_resolve_temperature_applies_minimax_floor() -> None:
    assert _resolve_temperature(provider="minimax", value=0.0) == MINIMAX_TEMPERATURE_FLOOR
    assert _resolve_temperature(provider="openai", value=0.0) == 0.0


def test_model_label_normalizes_known_model_casing(tmp_path) -> None:
    cfg = make_config(tmp_path)
    cfg = replace(
        cfg,
        agent_role=RoleConfig(provider="minimax", model="minimax-m2.7"),
    )

    assert model_label(config=cfg) == "minimax/MiniMax-M2.7"


def test_normalize_openai_base_url_adds_local_v1_suffix() -> None:
    assert normalize_openai_base_url("ollama", "http://127.0.0.1:11434") == (
        "http://127.0.0.1:11434/v1"
    )
    assert normalize_openai_base_url("ollama", "http://127.0.0.1:11434/v1") == (
        "http://127.0.0.1:11434/v1"
    )


def test_list_provider_models_known_and_unknown() -> None:
    for provider in ("zai", "openrouter", "openai", "ollama", "mlx", "minimax"):
        assert list_provider_models(provider)
    assert list_provider_models("unknown") == []
