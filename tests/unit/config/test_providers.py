"""Unit tests for provider builders (PydanticAI-only)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.fallback import FallbackModel

from lerim.agents.model_settings import (
    LOW_VARIANCE_AGENT_MODEL_SETTINGS,
    LOW_VARIANCE_AGENT_TEMPERATURE,
)
from lerim.config.providers import (
    MINIMAX_TEMPERATURE_FLOOR,
    MODEL_HTTP_CONNECT_TIMEOUT_SECONDS,
    MODEL_HTTP_TIMEOUT_SECONDS,
    _api_key_for_provider,
    _make_retrying_http_client,
    _build_openai_model_settings,
    build_pydantic_model,
    build_pydantic_model_from_provider,
    list_provider_models,
    normalize_model_name,
    parse_fallback_spec,
)
from lerim.config.settings import RoleConfig
from tests.helpers import make_config


def test_parse_fallback_spec_with_provider() -> None:
    spec = parse_fallback_spec("zai:glm-4.7")
    assert spec.provider == "zai"
    assert spec.model == "glm-4.7"


def test_parse_fallback_spec_without_provider_requires_default() -> None:
    with pytest.raises(RuntimeError, match="fallback_model_missing_provider"):
        parse_fallback_spec("x-ai/grok-4.1-fast")


def test_parse_fallback_spec_without_provider_uses_explicit_default() -> None:
    spec = parse_fallback_spec("x-ai/grok-4.1-fast", default_provider="openrouter")
    assert spec.provider == "openrouter"
    assert spec.model == "x-ai/grok-4.1-fast"


def test_parse_fallback_spec_openrouter_colon_suffix_uses_default_provider() -> None:
    """OpenRouter model IDs can contain colon suffixes like ``:free``."""
    spec = parse_fallback_spec(
        "deepseek/deepseek-r1-0528:free",
        default_provider="openrouter",
    )
    assert spec.provider == "openrouter"
    assert spec.model == "deepseek/deepseek-r1-0528:free"


def test_parse_fallback_spec_known_prefix_wins_over_default_provider() -> None:
    """Only known provider prefixes are parsed as explicit fallback providers."""
    spec = parse_fallback_spec("zai:glm-4.7", default_provider="openrouter")
    assert spec.provider == "zai"
    assert spec.model == "glm-4.7"


def test_parse_fallback_spec_normalizes_known_model_casing() -> None:
    spec = parse_fallback_spec("minimax:minimax-m2.5")
    assert spec.provider == "minimax"
    assert spec.model == "MiniMax-M2.5"


def test_normalize_model_name_known_and_unknown() -> None:
    assert normalize_model_name("minimax", "minimax-m2.7") == "MiniMax-M2.7"
    assert normalize_model_name("openrouter", "any/model") == "any/model"


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
    assert _api_key_for_provider(cfg, "zai") == "z-key"
    assert _api_key_for_provider(cfg, "openrouter") == "or-key"
    assert _api_key_for_provider(cfg, "openai") == "oa-key"
    assert _api_key_for_provider(cfg, "minimax") == "mm-key"
    assert _api_key_for_provider(cfg, "opencode_go") == "oc-key"
    assert _api_key_for_provider(cfg, "ollama") is None
    assert _api_key_for_provider(cfg, "mlx") is None


def test_build_pydantic_model_missing_api_key_raises(tmp_path) -> None:
    cfg = make_config(tmp_path)
    cfg = replace(
        cfg,
        agent_role=RoleConfig(provider="openrouter", model="x-ai/grok-4.1-fast"),
        openrouter_api_key=None,
    )
    with pytest.raises(RuntimeError, match="missing_api_key"):
        build_pydantic_model("agent", config=cfg)


def test_build_pydantic_model_ollama_no_key(tmp_path) -> None:
    cfg = make_config(tmp_path)
    cfg = replace(
        cfg,
        agent_role=RoleConfig(provider="ollama", model="qwen3:8b"),
    )
    model = build_pydantic_model("agent", config=cfg)
    assert model is not None


def test_build_pydantic_model_mlx_no_key(tmp_path) -> None:
    cfg = make_config(tmp_path)
    cfg = replace(
        cfg,
        agent_role=RoleConfig(
            provider="mlx",
            model="mlx-community/Qwen3.5-9B-4bit",
        ),
    )
    model = build_pydantic_model("agent", config=cfg)
    assert model is not None


def test_build_pydantic_model_minimax_agent_settings_keep_positive_temperature(
    tmp_path,
) -> None:
    """MiniMax request prep must preserve the provider floor when agent settings omit temperature."""
    cfg = make_config(tmp_path)
    cfg = replace(
        cfg,
        agent_role=RoleConfig(
            provider="minimax",
            model="MiniMax-M2.7",
            temperature=0.0,
        ),
        minimax_api_key="mm-key",
    )
    model = build_pydantic_model("agent", config=cfg)

    prepared_settings, _ = model.prepare_request(
        LOW_VARIANCE_AGENT_MODEL_SETTINGS,
        ModelRequestParameters(),
    )

    assert prepared_settings is not None
    assert prepared_settings["temperature"] == LOW_VARIANCE_AGENT_TEMPERATURE
    assert prepared_settings["temperature"] > 0.0
    assert prepared_settings["temperature"] > MINIMAX_TEMPERATURE_FLOOR
    assert prepared_settings["top_p"] == LOW_VARIANCE_AGENT_MODEL_SETTINGS["top_p"]


def test_build_pydantic_model_minimax_uses_explicit_http_client(tmp_path) -> None:
    """MiniMax must not rely on the Anthropic SDK's noisy wrapper finalizer."""
    cfg = make_config(tmp_path)
    cfg = replace(
        cfg,
        agent_role=RoleConfig(provider="minimax", model="MiniMax-M2.7"),
        minimax_api_key="mm-key",
    )
    model = build_pydantic_model("agent", config=cfg)

    client = model.client

    assert client.max_retries == 5
    assert type(client._client).__name__ != "AsyncHttpxClientWrapper"
    assert client.timeout.read == MODEL_HTTP_TIMEOUT_SECONDS
    assert client.timeout.connect == MODEL_HTTP_CONNECT_TIMEOUT_SECONDS


def test_retrying_http_client_has_bounded_timeout() -> None:
    """OpenAI-compatible providers should not wait indefinitely per request."""
    client = _make_retrying_http_client()

    assert client.timeout.read == MODEL_HTTP_TIMEOUT_SECONDS
    assert client.timeout.connect == MODEL_HTTP_CONNECT_TIMEOUT_SECONDS


def test_openai_provider_settings_do_not_send_top_k(tmp_path) -> None:
    """OpenAI rejects top_k, so it must not be sent in extra_body."""
    cfg = make_config(tmp_path)
    cfg = replace(
        cfg,
        agent_role=RoleConfig(provider="openai", model="gpt-5-mini", top_k=40),
    )

    settings = _build_openai_model_settings(cfg, provider="openai")

    assert "extra_body" not in settings


def test_openrouter_provider_settings_include_supported_extra_body(tmp_path) -> None:
    """Provider-supported nonstandard request fields stay in extra_body."""
    cfg = make_config(tmp_path)
    cfg = replace(
        cfg,
        agent_role=RoleConfig(
            provider="openrouter", model="x-ai/grok-4.1-fast", top_k=40
        ),
    )

    settings = _build_openai_model_settings(cfg, provider="openrouter")

    assert settings["extra_body"] == {"top_k": 40}


def test_build_pydantic_model_requires_available_fallback_keys(tmp_path) -> None:
    """Configured fallback models must be buildable."""
    cfg = make_config(tmp_path)
    cfg = replace(
        cfg,
        agent_role=RoleConfig(
            provider="ollama",
            model="qwen3:8b",
            fallback_models=("openrouter:x-ai/grok-4.1-fast",),
        ),
        openrouter_api_key=None,
    )
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        build_pydantic_model("agent", config=cfg)


def test_build_pydantic_model_with_fallback_chain(tmp_path) -> None:
    cfg = make_config(tmp_path)
    cfg = replace(
        cfg,
        agent_role=RoleConfig(
            provider="openrouter",
            model="x-ai/grok-4.1-fast",
            fallback_models=("zai:glm-4.7",),
        ),
        openrouter_api_key="or-key",
        zai_api_key="z-key",
    )
    model = build_pydantic_model("agent", config=cfg)
    assert isinstance(model, FallbackModel)
    assert len(model.models) == 2


def test_build_pydantic_model_unqualified_fallback_uses_primary_provider(
    tmp_path,
) -> None:
    cfg = make_config(tmp_path)
    cfg = replace(
        cfg,
        agent_role=RoleConfig(
            provider="ollama",
            model="qwen3:8b",
            fallback_models=("qwen3:14b",),
        ),
    )

    model = build_pydantic_model("agent", config=cfg)

    assert isinstance(model, FallbackModel)
    assert len(model.models) == 2


def test_build_pydantic_model_from_provider(tmp_path) -> None:
    cfg = make_config(tmp_path)
    cfg = replace(cfg, openrouter_api_key="or-key")
    model = build_pydantic_model_from_provider(
        "openrouter",
        "x-ai/grok-4.1-fast",
        config=cfg,
    )
    assert model is not None


def test_build_pydantic_model_from_provider_with_fallbacks(tmp_path) -> None:
    cfg = make_config(tmp_path)
    cfg = replace(cfg, openrouter_api_key="or-key", zai_api_key="z-key")
    model = build_pydantic_model_from_provider(
        "openrouter",
        "x-ai/grok-4.1-fast",
        fallback_models=["zai:glm-4.7"],
        config=cfg,
    )
    assert isinstance(model, FallbackModel)
    assert len(model.models) == 2


def test_list_provider_models_known_and_unknown() -> None:
    for provider in ("zai", "openrouter", "openai", "ollama", "mlx", "minimax"):
        assert list_provider_models(provider)
    assert list_provider_models("unknown") == []
