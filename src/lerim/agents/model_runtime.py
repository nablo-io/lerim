"""Shared DSPy language-model runtime for Lerim model workflows."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from lerim.agents.dspy_compat import dspy

from lerim.config.providers import (
    MINIMAX_TEMPERATURE_FLOOR,
    ensure_provider_api_key,
    normalize_model_name,
)
from lerim.config.settings import Config, RoleConfig, get_config

_LOCAL_PROVIDERS = {"ollama", "mlx"}


@dataclass(frozen=True)
class ModelRuntime:
    """Resolved model client plus observability metadata for one run."""

    lm: dspy.LM
    label: str
    provider: str
    model: str
    api_base: str


def build_model_runtime(
    *,
    config: Config | None = None,
    role: RoleConfig | None = None,
    provider: str | None = None,
    model_name: str | None = None,
    api_base_url: str | None = None,
    api_key: str | None = None,
    temperature: float | None = None,
) -> ModelRuntime:
    """Build the DSPy LM used by Lerim's model-assisted workflows."""
    cfg = config or get_config()
    cache_dir = cfg.global_data_dir / "cache" / "dspy"
    cache_dir.mkdir(parents=True, exist_ok=True)
    dspy.disable_litellm_logging()
    for logger_name in ("LiteLLM", "litellm"):
        logging.getLogger(logger_name).setLevel(logging.ERROR)
    dspy.configure_cache(
        enable_disk_cache=True,
        enable_memory_cache=True,
        disk_cache_dir=str(cache_dir),
    )
    role_cfg = role or cfg.agent_role
    resolved_provider = (provider or role_cfg.provider).strip().lower()
    resolved_model = normalize_model_name(
        resolved_provider,
        (model_name or role_cfg.model).strip(),
    )
    resolved_base_url = resolve_model_api_base(
        cfg,
        role_cfg=role_cfg,
        provider=resolved_provider,
        override=api_base_url,
    )
    resolved_api_key = api_key or ensure_provider_api_key(
        cfg,
        resolved_provider,
        role_label=f"provider={resolved_provider}",
    )
    resolved_temperature = resolve_model_temperature(
        provider=resolved_provider,
        value=role_cfg.temperature if temperature is None else temperature,
    )
    lite_llm_model = resolve_litellm_model(resolved_provider, resolved_model)
    lm_kwargs: dict[str, object] = {
        "api_base": resolved_base_url,
        "api_key": resolved_api_key,
        "temperature": resolved_temperature,
        "cache": False,
        "num_retries": 1,
    }
    if resolved_provider == "minimax":
        lm_kwargs["extra_body"] = {"reasoning_split": True}
    return ModelRuntime(
        lm=dspy.LM(lite_llm_model, **lm_kwargs),
        label=f"{resolved_provider}/{resolved_model}",
        provider=resolved_provider,
        model=resolved_model,
        api_base=resolved_base_url,
    )


def model_label(
    *,
    config: Config | None = None,
    provider: str | None = None,
    model_name: str | None = None,
) -> str:
    """Return the effective provider/model label for observability."""
    cfg = config or get_config()
    role_cfg = cfg.agent_role
    resolved_provider = (provider or role_cfg.provider).strip().lower()
    resolved_model = normalize_model_name(
        resolved_provider,
        (model_name or role_cfg.model).strip(),
    )
    return f"{resolved_provider}/{resolved_model}"


def resolve_litellm_model(provider: str, model: str) -> str:
    """Return the LiteLLM model string for an OpenAI-compatible endpoint."""
    if provider in {"openrouter", "ollama"}:
        return f"{provider}/{model}"
    return f"openai/{model}"


def resolve_model_api_base(
    config: Config,
    *,
    role_cfg: RoleConfig,
    provider: str,
    override: str | None,
) -> str:
    """Resolve the OpenAI-compatible API base URL for a provider."""
    role_base_url = role_cfg.api_base.strip() if provider == role_cfg.provider else ""
    base_url = (
        (override or "").strip()
        or role_base_url
        or config.provider_api_bases.get(provider, "")
    )
    if not base_url:
        raise RuntimeError(
            f"missing_api_base:no default base URL configured for provider={provider}"
        )
    if provider in _LOCAL_PROVIDERS and not base_url.rstrip("/").endswith("/v1"):
        return f"{base_url.rstrip('/')}/v1"
    return base_url.rstrip("/")


def resolve_model_temperature(*, provider: str, value: float) -> float:
    """Normalize model temperature for provider quirks."""
    temperature = float(value)
    if provider == "minimax":
        return max(MINIMAX_TEMPERATURE_FLOOR, min(1.0, temperature))
    return temperature
