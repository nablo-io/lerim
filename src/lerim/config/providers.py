"""Provider capability helpers for BAML-backed Lerim agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lerim.config.settings import Config, get_config

MINIMAX_TEMPERATURE_FLOOR = 0.01


PROVIDER_CAPABILITIES: dict[str, dict[str, Any]] = {
    "minimax": {
        "roles": ["agent"],
        "api_key_env": "MINIMAX_API_KEY",
        "models": [
            "MiniMax-M2.7",
            "MiniMax-M2.7-highspeed",
            "MiniMax-M2.5",
            "MiniMax-M2.1",
            "MiniMax-M2",
        ],
    },
    "opencode_go": {
        "roles": ["agent"],
        "api_key_env": "OPENCODE_API_KEY",
        "models": ["minimax-m2.7", "minimax-m2.5", "kimi-k2.5", "glm-5"],
    },
    "zai": {
        "roles": ["agent"],
        "api_key_env": "ZAI_API_KEY",
        "models": ["glm-4.7", "glm-4.5-air", "glm-4.5"],
    },
    "openai": {
        "roles": ["agent"],
        "api_key_env": "OPENAI_API_KEY",
    },
    "openrouter": {
        "roles": ["agent"],
        "api_key_env": "OPENROUTER_API_KEY",
    },
    "ollama": {
        "roles": ["agent"],
        "api_key_env": None,
        "openai_base_url_suffix": "/v1",
    },
    "mlx": {
        "roles": ["agent"],
        "api_key_env": None,
        "openai_base_url_suffix": "/v1",
    },
}


@dataclass(frozen=True)
class ProviderSetupMetadata:
    """Interactive setup metadata for providers exposed by ``lerim init``."""

    provider_id: str
    api_key_env: str
    display_name: str
    description: str


def _provider_setup_metadata(
    provider_id: str,
    *,
    display_name: str,
    description: str,
) -> ProviderSetupMetadata:
    """Build setup metadata from the capability registry's provider facts."""
    caps = PROVIDER_CAPABILITIES[provider_id]
    api_key_env = str(caps.get("api_key_env") or "")
    return ProviderSetupMetadata(
        provider_id=provider_id,
        api_key_env=api_key_env,
        display_name=display_name,
        description=description,
    )


PROVIDER_SETUP_CHOICES: tuple[ProviderSetupMetadata, ...] = (
    _provider_setup_metadata(
        "opencode_go",
        display_name="OpenCode Go",
        description="Free tier available — opencode.ai",
    ),
    _provider_setup_metadata(
        "openrouter",
        display_name="OpenRouter",
        description="Access 100+ models — openrouter.ai",
    ),
    _provider_setup_metadata(
        "openai",
        display_name="OpenAI",
        description="GPT models — platform.openai.com",
    ),
    _provider_setup_metadata(
        "minimax",
        display_name="MiniMax",
        description="MiniMax models — minimax.io",
    ),
    _provider_setup_metadata(
        "zai",
        display_name="Z.AI",
        description="GLM models — z.ai",
    ),
    _provider_setup_metadata(
        "ollama",
        display_name="Ollama",
        description="Local models — no API key needed",
    ),
)


def validate_provider_for_role(provider: str, role: str, model: str = "") -> None:
    """Raise RuntimeError if provider/model does not support the requested role."""
    del model
    normalized = provider.strip().lower()
    caps = PROVIDER_CAPABILITIES.get(normalized)
    if caps is None:
        supported = ", ".join(sorted(PROVIDER_CAPABILITIES.keys()))
        raise RuntimeError(
            f"Unknown provider '{normalized}'. Supported providers: {supported}"
        )
    if role not in caps["roles"]:
        supported_roles = ", ".join(caps["roles"])
        raise RuntimeError(
            f"Provider '{normalized}' does not support role '{role}'. "
            f"Supported roles for {normalized}: {supported_roles}"
        )


def normalize_model_name(provider: str, model: str) -> str:
    """Return canonical casing for known provider models."""
    caps = PROVIDER_CAPABILITIES.get(provider.strip().lower(), {})
    known = caps.get("models")
    if not known:
        return model
    lookup = {str(item).lower(): str(item) for item in known}
    return lookup.get(model.strip().lower(), model)


def api_key_for_provider(config: Config, provider: str) -> str | None:
    """Resolve the configured API key value for one provider."""
    normalized = provider.strip().lower()
    if normalized == "zai":
        return config.zai_api_key
    if normalized == "openrouter":
        return config.openrouter_api_key
    if normalized == "openai":
        return config.openai_api_key
    if normalized == "minimax":
        return config.minimax_api_key
    if normalized == "opencode_go":
        return config.opencode_api_key
    return None


def api_key_env_for_provider(provider: str) -> str:
    """Return the environment variable name for one provider, or empty text."""
    caps = PROVIDER_CAPABILITIES.get(provider.strip().lower(), {})
    return str(caps.get("api_key_env") or "")


def ensure_provider_api_key(
    config: Config,
    provider: str,
    *,
    role_label: str = "agent",
) -> str:
    """Return API key text or raise for remote providers that require one."""
    normalized = provider.strip().lower()
    env_name = api_key_env_for_provider(normalized)
    if not env_name:
        return normalized
    value = api_key_for_provider(config, normalized)
    if not value:
        raise RuntimeError(f"missing_api_key:{env_name} required for {role_label}")
    return str(value)


def default_api_base(provider: str, config: Config | None = None) -> str:
    """Return provider default API base from config's provider section."""
    cfg = config or get_config()
    return cfg.provider_api_bases.get(provider.strip().lower(), "")


def normalize_openai_base_url(provider: str, base_url: str) -> str:
    """Apply provider-declared OpenAI-compatible base URL normalization."""
    normalized_provider = provider.strip().lower()
    caps = PROVIDER_CAPABILITIES[normalized_provider]
    suffix = caps.get("openai_base_url_suffix")
    if not suffix or not base_url:
        return base_url
    normalized = base_url.rstrip("/")
    if normalized.endswith(str(suffix)):
        return normalized
    return normalized + str(suffix)


def list_provider_models(provider: str) -> list[str]:
    """Return static provider model suggestions for dashboard UI selections."""
    normalized = provider.strip().lower()
    caps = PROVIDER_CAPABILITIES.get(normalized, {})
    if "models" in caps:
        return [str(item) for item in caps["models"]]
    extras: dict[str, list[str]] = {
        "openrouter": [
            "qwen/qwen3-coder-30b-a3b-instruct",
            "anthropic/claude-sonnet-4-5-20250929",
            "anthropic/claude-haiku-4-5-20251001",
        ],
        "openai": ["gpt-5-mini", "gpt-5"],
        "ollama": ["qwen3:8b", "qwen3:4b", "qwen3:14b"],
        "mlx": [
            "mlx-community/Qwen3.5-9B-4bit",
            "mlx-community/Qwen3.5-27B-4bit",
            "mlx-community/Qwen3.5-35B-A3B-4bit",
        ],
    }
    return list(extras.get(normalized, []))


def _self_check() -> None:
    """Run a local provider-helper smoke check."""
    assert normalize_model_name("minimax", "minimax-m2.7") == "MiniMax-M2.7"
    assert list_provider_models("ollama")


if __name__ == "__main__":
    _self_check()
