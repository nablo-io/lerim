"""Shared BAML runtime construction for Lerim agents."""

from __future__ import annotations

from baml_py import ClientRegistry

from lerim.agents.baml_client.sync_client import b
from lerim.config.providers import (
    MINIMAX_TEMPERATURE_FLOOR,
    ensure_provider_api_key,
    normalize_model_name,
)
from lerim.config.settings import Config, RoleConfig, get_config

BAML_HTTP_CONNECT_TIMEOUT_MS = 10_000
BAML_HTTP_TIME_TO_FIRST_TOKEN_TIMEOUT_MS = 120_000
BAML_HTTP_IDLE_TIMEOUT_MS = 30_000
BAML_HTTP_REQUEST_TIMEOUT_MS = 300_000

_LOCAL_PROVIDERS = {"ollama", "mlx"}


def build_baml_client_for_role(
    *,
    config: Config | None = None,
    role: RoleConfig | None = None,
    provider: str | None = None,
    model_name: str | None = None,
    api_base_url: str | None = None,
    api_key: str | None = None,
    temperature: float | None = None,
):
    """Build a generated BAML client for one configured Lerim agent role."""
    cfg = config or get_config()
    role_cfg = role or cfg.agent_role
    resolved_provider = (provider or role_cfg.provider).strip().lower()
    resolved_model = normalize_model_name(
        resolved_provider,
        (model_name or role_cfg.model).strip(),
    )
    resolved_base_url = _resolve_base_url(
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
    resolved_temperature = _resolve_temperature(
        provider=resolved_provider,
        value=role_cfg.temperature if temperature is None else temperature,
    )

    registry = ClientRegistry()
    registry.add_llm_client(
        name="RuntimeAgentModel",
        provider="openai-generic",
        options={
            "base_url": resolved_base_url,
            "api_key": resolved_api_key,
            "model": resolved_model,
            "temperature": resolved_temperature,
            "http": {
                "connect_timeout_ms": BAML_HTTP_CONNECT_TIMEOUT_MS,
                "time_to_first_token_timeout_ms": BAML_HTTP_TIME_TO_FIRST_TOKEN_TIMEOUT_MS,
                "idle_timeout_ms": BAML_HTTP_IDLE_TIMEOUT_MS,
                "request_timeout_ms": BAML_HTTP_REQUEST_TIMEOUT_MS,
            },
        },
        retry_policy="RuntimeAgentRetry",
    )
    registry.set_primary("RuntimeAgentModel")
    return b.with_options(client_registry=registry)


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


def _resolve_base_url(
    config: Config,
    *,
    role_cfg: RoleConfig,
    provider: str,
    override: str | None,
) -> str:
    """Resolve the OpenAI-compatible base URL used by BAML."""
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


def _resolve_temperature(*, provider: str, value: float) -> float:
    """Normalize model temperature for provider quirks."""
    temperature = float(value)
    if provider == "minimax":
        return max(MINIMAX_TEMPERATURE_FLOOR, min(1.0, temperature))
    return temperature


def _self_check() -> None:
    """Run a small import-time construction check without network calls."""
    assert _LOCAL_PROVIDERS == {"ollama", "mlx"}


if __name__ == "__main__":
    _self_check()
