"""Provider builders for PydanticAI pipelines and shared utilities.

Single source of truth for role → model construction. Includes:
- Provider capability registry (env var names, known models)
- Model name normalization per provider
- Role validation
- PydanticAI model builders with HTTP retry + provider fallback

MiniMax routing (2026-04-12):
  PydanticAI uses the Anthropic-compatible endpoint (``/anthropic``) for
  robust tool-use emission.

Provider base URLs are read from the `[providers]` section of `default.toml`
(+ optional `~/.lerim/config.toml` override). API keys are resolved from
environment variables via `Config`. Nothing is hardcoded in this module
beyond the capability registry (which is a facts-about-providers table,
not configuration).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from anthropic import AsyncAnthropic
from httpx import (
    AsyncClient,
    HTTPStatusError,
    Timeout,
    TimeoutException,
    TransportError,
)
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.models import Model, ModelSettings, cached_async_http_client
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

from lerim.config.settings import Config, get_config

RoleName = Literal["agent"]
MINIMAX_TEMPERATURE_FLOOR = 0.01
MODEL_HTTP_TIMEOUT_SECONDS = 180
MODEL_HTTP_CONNECT_TIMEOUT_SECONDS = 10


# ---------------------------------------------------------------------------
# Provider capability registry and validation
# ---------------------------------------------------------------------------

PROVIDER_CAPABILITIES: dict[str, dict] = {
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
        "extra_body_keys": ["top_k"],
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
    api_key_env = caps.get("api_key_env") or ""
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
    """Raise RuntimeError with helpful message if provider+model doesn't support the role."""
    provider = provider.strip().lower()
    caps = PROVIDER_CAPABILITIES.get(provider)
    if caps is None:
        supported = ", ".join(sorted(PROVIDER_CAPABILITIES.keys()))
        raise RuntimeError(
            f"Unknown provider '{provider}'. Supported providers: {supported}"
        )
    if role not in caps["roles"]:
        supported_roles = ", ".join(caps["roles"])
        raise RuntimeError(
            f"Provider '{provider}' does not support role '{role}'. "
            f"Supported roles for {provider}: {supported_roles}"
        )


def normalize_model_name(provider: str, model: str) -> str:
    """Return the canonical model name for a provider.

    Performs case-insensitive matching against the provider's known model list.
    Returns the correctly-cased name if found, otherwise returns the input
    unchanged (for unknown models or providers with open-ended model lists).
    """
    caps = PROVIDER_CAPABILITIES.get(provider.strip().lower(), {})
    known = caps.get("models")
    if not known:
        return model
    lookup = {m.lower(): m for m in known}
    return lookup.get(model.strip().lower(), model)


@dataclass(frozen=True)
class FallbackSpec:
    """Parsed fallback descriptor used for model-chain construction."""

    provider: str
    model: str


def _role_config(config: Config, role: RoleName):
    """Return role config for agent model construction."""
    return config.agent_role


def _default_api_base(provider: str, config: Config | None = None) -> str:
    """Return provider default API base from config's [providers] section."""
    if config is None:
        config = get_config()
    return config.provider_api_bases.get(provider, "")


def _api_key_for_provider(config: Config, provider: str) -> str | None:
    """Resolve API key for a provider from environment-backed config."""
    if provider == "zai":
        return config.zai_api_key
    if provider == "openrouter":
        return config.openrouter_api_key
    if provider == "openai":
        return config.openai_api_key
    if provider == "anthropic":
        return config.anthropic_api_key
    if provider == "minimax":
        return config.minimax_api_key
    if provider == "opencode_go":
        return config.opencode_api_key
    return None


def _normalize_openai_base_url(provider: str, base_url: str) -> str:
    """Apply provider-declared OpenAI-compatible base URL normalization."""
    caps = PROVIDER_CAPABILITIES[provider]
    suffix = caps.get("openai_base_url_suffix")
    if not suffix or not base_url:
        return base_url
    normalized = base_url.rstrip("/")
    if normalized.endswith(suffix):
        return normalized
    return normalized + suffix


def parse_fallback_spec(
    raw: str, *, default_provider: str | None = None
) -> FallbackSpec:
    """Parse fallback descriptor as ``known_provider:model`` or provider-local model."""
    if not isinstance(raw, str):
        raise RuntimeError(f"fallback_model_invalid:{raw!r}")
    text = raw.strip()
    if not text:
        raise RuntimeError("fallback_model_empty")
    if ":" not in text:
        if default_provider is None:
            raise RuntimeError(f"fallback_model_missing_provider:{raw}")
        model = normalize_model_name(default_provider, text)
        return FallbackSpec(provider=default_provider, model=model)
    provider, model = text.split(":", 1)
    provider = provider.strip().lower()
    model = model.strip()
    if not provider or not model:
        raise RuntimeError(f"fallback_model_invalid:{raw}")
    if provider not in PROVIDER_CAPABILITIES:
        if default_provider:
            return FallbackSpec(
                provider=default_provider,
                model=normalize_model_name(default_provider, text),
            )
        raise RuntimeError(f"fallback_model_unknown_provider:{raw}")
    model = normalize_model_name(provider, model)
    return FallbackSpec(provider=provider, model=model)


# ---------------------------------------------------------------------------
# PydanticAI model builders
# ---------------------------------------------------------------------------


def _make_retrying_http_client(
    max_attempts: int = 5,
    max_wait_seconds: int = 120,
    timeout_seconds: float = MODEL_HTTP_TIMEOUT_SECONDS,
    connect_timeout_seconds: float = MODEL_HTTP_CONNECT_TIMEOUT_SECONDS,
) -> AsyncClient:
    """Build an httpx AsyncClient with tenacity retries for transient errors.

    Retries individual HTTP requests on 429 (honoring Retry-After header),
    5xx server errors, and network errors at the transport layer —
    transparent to the agent loop. A failed model request retries in-place
    instead of crashing the enclosing agent run.

    Does NOT retry on 400 (bad request — won't change), 401/403 (auth),
    or other client errors. Those propagate as ModelHTTPError so a
    FallbackModel wrapper can switch providers.
    """

    def _validate_response(response):
        # Raise HTTPStatusError for retryable status codes so tenacity picks it up.
        if response.status_code in (429, 500, 502, 503, 504):
            response.raise_for_status()

    transport = AsyncTenacityTransport(
        config=RetryConfig(
            retry=retry_if_exception_type(
                (HTTPStatusError, TimeoutException, TransportError)
            ),
            wait=wait_retry_after(
                fallback_strategy=wait_exponential(multiplier=2, min=1, max=60),
                max_wait=max_wait_seconds,
            ),
            stop=stop_after_attempt(max_attempts),
            reraise=True,
        ),
        validate_response=_validate_response,
    )
    return AsyncClient(
        transport=transport,
        timeout=Timeout(
            timeout_seconds,
            connect=connect_timeout_seconds,
        ),
    )


def _build_openai_model_settings(
    cfg: Config, *, provider: str
) -> OpenAIChatModelSettings:
    """Build OpenAI-path model settings from Lerim Config.agent_role.

    Used for non-MiniMax providers that go through the OpenAI-compat path.
    Threads standard settings from config and only includes provider-specific
    extra_body fields when the provider capability registry says they are
    supported.
    """
    role_cfg = cfg.agent_role
    extra_body: dict[str, int] = {}
    caps = PROVIDER_CAPABILITIES.get(provider.strip().lower(), {})
    if "top_k" in caps.get("extra_body_keys", []):
        extra_body["top_k"] = role_cfg.top_k

    settings = OpenAIChatModelSettings(
        temperature=role_cfg.temperature,
        top_p=role_cfg.top_p,
        max_tokens=role_cfg.max_tokens,
        parallel_tool_calls=role_cfg.parallel_tool_calls,
    )
    if extra_body:
        settings["extra_body"] = extra_body
    return settings


def _build_minimax_anthropic_model(
    *,
    model: str,
    api_key: str,
    cfg: Config,
    timeout_seconds: float | None = None,
    connect_timeout_seconds: float | None = None,
    max_retries: int = 5,
) -> AnthropicModel:
    """Build MiniMax model via its Anthropic-compatible endpoint.

    MiniMax M2.7 emits proper Anthropic ``tool_use`` blocks via the
    ``/anthropic`` endpoint. M2.5's tool calling is broken on both
    endpoints. This remains the PydanticAI path for maintain, ask, and
    working-memory flows.

    Uses ``AsyncAnthropic(max_retries=5)`` for HTTP-level retries —
    the Anthropic SDK handles 429/5xx natively.
    Passes PydanticAI's cached HTTPX client explicitly so the SDK does not
    allocate per-run ``AsyncHttpxClientWrapper`` instances with noisy finalizers.

    The Anthropic base URL is resolved from ``[providers].minimax_anthropic``
    in config, falling back to ``https://api.minimax.io/anthropic``.
    """
    base_url = _default_api_base("minimax_anthropic", cfg)
    if not base_url:
        openai_url = _default_api_base("minimax", cfg)
        base_url = (
            openai_url.replace("/v1", "/anthropic")
            if openai_url
            else "https://api.minimax.io/anthropic"
        )

    resolved_timeout = timeout_seconds or MODEL_HTTP_TIMEOUT_SECONDS
    resolved_connect_timeout = connect_timeout_seconds or MODEL_HTTP_CONNECT_TIMEOUT_SECONDS
    client_cache_key = "minimax-anthropic"
    if (
        resolved_timeout != MODEL_HTTP_TIMEOUT_SECONDS
        or resolved_connect_timeout != MODEL_HTTP_CONNECT_TIMEOUT_SECONDS
    ):
        client_cache_key = (
            f"minimax-anthropic-{resolved_timeout:g}-{resolved_connect_timeout:g}"
        )

    client = AsyncAnthropic(
        api_key=api_key,
        base_url=base_url,
        timeout=Timeout(
            resolved_timeout,
            connect=resolved_connect_timeout,
        ),
        max_retries=max_retries,
        http_client=cached_async_http_client(
            provider=client_cache_key,
            timeout=resolved_timeout,
            connect=resolved_connect_timeout,
        ),
    )
    anthropic_provider = AnthropicProvider(anthropic_client=client)
    canonical_model = normalize_model_name("minimax", model)
    role_cfg = cfg.agent_role
    temperature = max(MINIMAX_TEMPERATURE_FLOOR, min(1.0, role_cfg.temperature))
    settings = ModelSettings(
        temperature=temperature,
        max_tokens=role_cfg.max_tokens,
        top_p=role_cfg.top_p,
    )
    return AnthropicModel(
        canonical_model, provider=anthropic_provider, settings=settings
    )


def _build_pydantic_model_for_provider(
    *,
    provider: str,
    model: str,
    api_base: str,
    cfg: Config,
    role_label: str,
    http_timeout_seconds: float | None = None,
    http_connect_timeout_seconds: float | None = None,
    http_max_attempts: int | None = None,
) -> Model:
    """Build a single PydanticAI model with HTTP retry.

    MiniMax → AnthropicModel via /anthropic endpoint (proper tool_use).
    All others → OpenAIChatModel via OpenAI-compat endpoint.
    """
    provider = provider.strip().lower()
    validate_provider_for_role(provider, "agent", model)

    caps = PROVIDER_CAPABILITIES[provider]
    env_name = caps.get("api_key_env")
    if env_name is None:
        api_key = provider
    else:
        api_key = _api_key_for_provider(cfg, provider)
        if not api_key:
            raise RuntimeError(f"missing_api_key:{env_name} required for {role_label}")

    # MiniMax: Anthropic-compat endpoint for proper tool calling
    if provider == "minimax":
        return _build_minimax_anthropic_model(
            model=model,
            api_key=api_key,
            cfg=cfg,
            timeout_seconds=http_timeout_seconds,
            connect_timeout_seconds=http_connect_timeout_seconds,
            max_retries=http_max_attempts or 5,
        )

    # All other providers: OpenAI-compat path
    base_url = api_base or _default_api_base(provider, cfg)
    base_url = _normalize_openai_base_url(provider, base_url)
    if not base_url:
        raise RuntimeError(
            f"missing_api_base:no default base URL configured for "
            f"provider={provider} (set [providers].{provider} in default.toml)"
        )

    http_client = _make_retrying_http_client(
        max_attempts=http_max_attempts or 5,
        timeout_seconds=http_timeout_seconds or MODEL_HTTP_TIMEOUT_SECONDS,
        connect_timeout_seconds=(
            http_connect_timeout_seconds or MODEL_HTTP_CONNECT_TIMEOUT_SECONDS
        ),
    )
    openai_provider = OpenAIProvider(
        base_url=base_url,
        api_key=api_key,
        http_client=http_client,
    )
    canonical_model = normalize_model_name(provider, model)
    settings = _build_openai_model_settings(cfg, provider=provider)
    return OpenAIChatModel(canonical_model, provider=openai_provider, settings=settings)


def _wrap_with_fallback(
    primary: Model,
    fallbacks: list[Model],
) -> Model:
    """Return primary alone if no fallbacks, else a FallbackModel wrapping both."""
    if not fallbacks:
        return primary
    return FallbackModel(
        primary,
        *fallbacks,
        fallback_on=(ModelHTTPError, ModelAPIError),
    )


def _build_model_chain(
    *,
    cfg: Config,
    provider: str,
    model: str,
    api_base: str,
    fallback_models: tuple[str, ...] | list[str],
    primary_role_label: str,
    fallback_role_label_prefix: str,
    http_timeout_seconds: float | None = None,
    http_connect_timeout_seconds: float | None = None,
    http_max_attempts: int | None = None,
) -> Model:
    """Build a primary model and optional configured fallback chain."""
    primary = _build_pydantic_model_for_provider(
        provider=provider,
        model=model,
        api_base=api_base,
        cfg=cfg,
        role_label=primary_role_label,
        http_timeout_seconds=http_timeout_seconds,
        http_connect_timeout_seconds=http_connect_timeout_seconds,
        http_max_attempts=http_max_attempts,
    )

    fallbacks = [
        _build_pydantic_model_for_provider(
            provider=spec.provider,
            model=spec.model,
            api_base="",
            cfg=cfg,
            role_label=f"{fallback_role_label_prefix}{spec.provider}:{spec.model}",
            http_timeout_seconds=http_timeout_seconds,
            http_connect_timeout_seconds=http_connect_timeout_seconds,
            http_max_attempts=http_max_attempts,
        )
        for raw in fallback_models
        for spec in (parse_fallback_spec(raw, default_provider=provider),)
    ]
    return _wrap_with_fallback(primary, fallbacks)


def build_pydantic_model(
    role: RoleName = "agent",
    *,
    config: Config | None = None,
) -> Model:
    """Build a robust PydanticAI model for the given role from Config.

    Reads provider/model/fallbacks from `Config` (from `default.toml` +
    `~/.lerim/config.toml`) — this is the runtime-side builder used by
    `LerimRuntime.sync()` and by agent `__main__` self-tests. For the eval
    harness, where each eval cell specifies its own provider/model in an
    eval TOML, use `build_pydantic_model_from_provider` instead.

    Returns a `FallbackModel` wrapping:

    - Primary: the role's configured provider/model with HTTP-level retry
      (`AsyncTenacityTransport` — handles 429/5xx/network in place)
    - Fallbacks: every entry in `role.fallback_models` (e.g. `"zai:glm-4.7"`),
      each with its own retry transport

    The FallbackModel switches to the next model when the current one
    raises `ModelHTTPError` or `ModelAPIError` — without restarting the
    agent run. If no fallback models are configured, returns the bare
    retrying primary model (which still has HTTP retry).
    """
    cfg = config or get_config()
    role_cfg = _role_config(cfg, role)

    return _build_model_chain(
        cfg=cfg,
        provider=role_cfg.provider,
        model=role_cfg.model,
        api_base=role_cfg.api_base,
        fallback_models=role_cfg.fallback_models,
        primary_role_label=f"roles.{role}.provider={role_cfg.provider}",
        fallback_role_label_prefix=f"roles.{role}.fallback=",
    )


def build_pydantic_model_from_provider(
    provider: str,
    model: str,
    *,
    fallback_models: tuple[str, ...] | list[str] | None = None,
    config: Config | None = None,
    http_timeout_seconds: float | None = None,
    http_connect_timeout_seconds: float | None = None,
    http_max_attempts: int | None = None,
) -> Model:
    """Build a robust PydanticAI model from explicit provider/model args.

    Used by the eval harness (each eval TOML cell specifies its own
    provider/model/fallbacks that override the default Lerim Config) and
    by the eval judge (which may use a different model than the agent role).

    Unlike `build_pydantic_model`, this does NOT read provider/model from
    Lerim's `Config.agent_role`. It still uses `Config` to resolve API keys
    from environment variables and base URLs from `[providers]`, so the
    config file stays the single source of truth for endpoints and keys.

    Args:
            provider: Provider name (e.g. "minimax", "zai", "openai", "ollama").
            model: Model name for the provider.
            fallback_models: Optional sequence of `"provider:model"` strings
                    (same format as `default.toml` `fallback_models`). None means
                    no fallback — just the primary with HTTP-level retry.
            config: Optional Config override (defaults to `get_config()`).
            http_timeout_seconds: Optional request timeout override for evals.
            http_connect_timeout_seconds: Optional connect timeout override for evals.
            http_max_attempts: Optional HTTP retry attempt override for evals.

    Returns:
            FallbackModel if fallbacks are configured and their API keys
            are available, else a bare retrying OpenAIChatModel primary.
    """
    cfg = config or get_config()

    return _build_model_chain(
        cfg=cfg,
        provider=provider,
        model=model,
        api_base="",
        fallback_models=fallback_models or (),
        primary_role_label=f"explicit_provider={provider}",
        fallback_role_label_prefix="explicit_fallback=",
        http_timeout_seconds=http_timeout_seconds,
        http_connect_timeout_seconds=http_connect_timeout_seconds,
        http_max_attempts=http_max_attempts,
    )


def list_provider_models(provider: str) -> list[str]:
    """Return static provider model suggestions for dashboard UI selections."""
    normalized = str(provider).strip().lower()
    caps = PROVIDER_CAPABILITIES.get(normalized, {})
    if "models" in caps:
        return list(caps["models"])
    # Open-ended providers: curated suggestions only
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


if __name__ == "__main__":
    """Run provider-layer self-test for shared utilities and model builders."""
    cfg = get_config()

    # -- shared utility tests --
    spec = parse_fallback_spec("openrouter:anthropic/claude-sonnet-4-5-20250929")
    assert spec.provider == "openrouter"
    assert spec.model == "anthropic/claude-sonnet-4-5-20250929"

    spec_default = parse_fallback_spec("some-model", default_provider="openrouter")
    assert spec_default.provider == "openrouter"
    assert spec_default.model == "some-model"

    assert isinstance(list_provider_models("ollama"), list)

    model = build_pydantic_model("agent", config=cfg)
    assert model is not None

    print(
        f"""\
providers: \
agent={cfg.agent_role.provider}/{cfg.agent_role.model}"""
    )
