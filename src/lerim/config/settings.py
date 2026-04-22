"""Central config loading from layered TOML files with role-based LLM settings.

Layers (low to high priority):
1. lerim/config/default.toml
2. ~/.lerim/config.toml
3. LERIM_CONFIG env path (optional explicit override)

API keys are read from environment variables only.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PACKAGE_DIR = Path(__file__).parent
DEFAULT_CONFIG_PATH = PACKAGE_DIR / "default.toml"
USER_CONFIG_PATH = Path.home() / ".lerim" / "config.toml"
GLOBAL_DATA_DIR = Path.home() / ".lerim"

_LAST_CONFIG_SOURCES: list[dict[str, str]] = []


@dataclass(frozen=True)
class RoleConfig:
	"""Configuration for the agent LLM role.

	All fields have defaults so the same class works for any future role.
	"""

	provider: str
	model: str
	api_base: str = ""
	fallback_models: tuple[str, ...] = ()
	# MiniMax-M2 official preset: temperature=1.0, top_p=0.95, top_k=40
	temperature: float = 1.0
	top_p: float = 0.95
	top_k: int = 40
	max_tokens: int = 32000
	parallel_tool_calls: bool = True
	# PydanticAI single-pass sync now auto-scales its request_limit from
	# trace size via lerim.agents.tools.compute_request_budget(trace_path).
	# No static extract-budget field on RoleConfig — the budget is derived
	# at run start from the actual trace's line count, clamped [40, 100].
	# PydanticAI request-turn limits for maintain/ask flows.
	max_iters_maintain: int = 30
	max_iters_ask: int = 30


def load_toml_file(path: Path | None) -> dict[str, Any]:
    """Load TOML file into a dict; return empty dict on failures."""
    if not path or not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge dict values with override precedence."""
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def _expand(value: Any, default: Path) -> Path:
    """Expand user path with fallback to default path."""
    if value in (None, ""):
        return default
    try:
        return Path(str(value)).expanduser()
    except (TypeError, OSError, ValueError):
        return default


def _to_non_empty_string(value: Any) -> str:
    """Convert value to stripped string, defaulting to empty string."""
    if value is None:
        return ""
    return str(value).strip()


def _ensure_dict(data: dict[str, Any], key: str) -> dict[str, Any]:
	"""Get a dict value from data, returning empty dict if missing or wrong type."""
	val = data.get(key, {})
	return val if isinstance(val, dict) else {}


def _require_int(raw: dict[str, Any], key: str, minimum: int = 0) -> int:
    """Read a required integer from config dict. Raises if missing from config."""
    value = raw.get(key)
    if value is None:
        raise ValueError(
            f"missing required config key: {key} (set it in default.toml or user config)"
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"config key {key} must be an integer, got: {value!r}")
    return max(minimum, parsed)


def _to_fallback_models(value: Any) -> tuple[str, ...]:
    """Normalize fallback model list from TOML list/string values."""
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, str):
        parts = [item.strip() for item in value.split(",")]
        return tuple(item for item in parts if item)
    return ()


def get_user_config_path() -> Path:
    """Return canonical user config path."""
    return USER_CONFIG_PATH


def ensure_user_config_exists() -> Path:
    """Create user config scaffold outside pytest if it does not exist."""
    path = USER_CONFIG_PATH
    if path.exists() or os.getenv("PYTEST_CURRENT_TEST"):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """\
# Lerim user overrides
# Override only keys you need.

# [roles.agent]
# provider = "openrouter"
# model = "qwen/qwen3-coder-30b-a3b-instruct"
""",
        encoding="utf-8",
    )
    return path



def _load_layers() -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Load and merge all configuration layers in precedence order."""
    merged: dict[str, Any] = {}
    sources: list[dict[str, str]] = []

    layers: list[tuple[str, Path]] = [
        ("package_default", DEFAULT_CONFIG_PATH),
        ("user", USER_CONFIG_PATH),
    ]

    explicit = os.getenv("LERIM_CONFIG")
    if explicit:
        layers.append(("explicit", Path(explicit).expanduser()))

    for source_name, path in layers:
        payload = load_toml_file(path)
        if payload:
            merged = _deep_merge(merged, payload)
            sources.append({"source": source_name, "path": str(path)})

    return merged, sources


def get_config_sources() -> list[dict[str, str]]:
    """Return last-computed config source list."""
    return [dict(item) for item in _LAST_CONFIG_SOURCES]


@dataclass(frozen=True)
class Config:
    """Effective runtime configuration from TOML layers and environment."""

    global_data_dir: Path
    sessions_db_path: Path
    context_db_path: Path
    platforms_path: Path
    embedding_model_id: str
    embedding_cache_dir: Path
    semantic_shortlist_size: int
    lexical_shortlist_size: int

    server_host: str
    server_port: int
    sync_interval_minutes: int
    maintain_interval_minutes: int
    sync_window_days: int
    sync_max_sessions: int

    agent_role: RoleConfig

    mlflow_enabled: bool

    anthropic_api_key: str | None
    openai_api_key: str | None
    zai_api_key: str | None
    openrouter_api_key: str | None
    minimax_api_key: str | None
    opencode_api_key: str | None

    provider_api_bases: dict[str, str]
    auto_unload: bool

    cloud_endpoint: str
    cloud_token: str | None

    agents: dict[str, str]
    projects: dict[str, str]

    def public_dict(self) -> dict[str, Any]:
        """Return safe serialized config for CLI/dashboard visibility."""
        return {
            "global_data_dir": str(self.global_data_dir),
            "sessions_db_path": str(self.sessions_db_path),
            "context_db_path": str(self.context_db_path),
            "platforms_path": str(self.platforms_path),
            "embedding_model_id": self.embedding_model_id,
            "embedding_cache_dir": str(self.embedding_cache_dir),
            "semantic_shortlist_size": self.semantic_shortlist_size,
            "lexical_shortlist_size": self.lexical_shortlist_size,
            "server_host": self.server_host,
            "server_port": self.server_port,
            "sync_interval_minutes": self.sync_interval_minutes,
            "maintain_interval_minutes": self.maintain_interval_minutes,
            "agent_role": {
                "provider": self.agent_role.provider,
                "model": self.agent_role.model,
                "api_base": self.agent_role.api_base,
                "fallback_models": list(self.agent_role.fallback_models),
            },
            "mlflow_enabled": self.mlflow_enabled,
            "provider_api_bases": dict(self.provider_api_bases),
            "auto_unload": self.auto_unload,
            "cloud_endpoint": self.cloud_endpoint,
            "cloud_authenticated": self.cloud_token is not None,
            "agents": dict(self.agents),
            "projects": dict(self.projects),
        }


def _to_string_tuple(value: Any) -> tuple[str, ...]:
    """Normalize a TOML list/string into a tuple of non-empty strings."""
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, str):
        parts = [item.strip() for item in value.split(",")]
        return tuple(item for item in parts if item)
    return ()


def _build_role(
	raw: dict[str, Any], *, default_provider: str, default_model: str
) -> RoleConfig:
	"""Build a role config from TOML payload."""
	from lerim.config.providers import normalize_model_name

	provider = _to_non_empty_string(raw.get("provider")) or default_provider
	model = _to_non_empty_string(raw.get("model")) or default_model
	model = normalize_model_name(provider, model)
	return RoleConfig(
		provider=provider,
		model=model,
		api_base=_to_non_empty_string(raw.get("api_base")),
		fallback_models=_to_fallback_models(raw.get("fallback_models")),
		temperature=float(raw.get("temperature", 1.0)),
		top_p=float(raw.get("top_p", 0.95)),
		top_k=int(raw.get("top_k", 40)),
		max_tokens=int(raw.get("max_tokens", 32000)),
		parallel_tool_calls=bool(raw.get("parallel_tool_calls", True)),
		max_iters_maintain=int(raw.get("max_iters_maintain", 30)),
		max_iters_ask=int(raw.get("max_iters_ask", 30)),
	)


def _build_agent_role(roles: dict[str, Any]) -> RoleConfig:
	"""Build agent role config from TOML roles section."""
	return _build_role(
		_ensure_dict(roles, "agent"),
		default_provider="openrouter",
		default_model="qwen/qwen3-coder-30b-a3b-instruct",
	)


def _parse_string_table(raw: dict[str, Any]) -> dict[str, str]:
    """Parse a TOML table of ``name = "path"`` or ``name = {path = "..."}`` entries."""
    result: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            text = str(value.get("path", "")).strip()
        else:
            text = str(value).strip() if value is not None else ""
        if text:
            result[key] = text
    return result


def _default_context_db_path(global_data_dir: Path) -> Path:
    """Return the canonical global context DB path for the current data root."""
    return global_data_dir / "context.sqlite3"


_TOP_LEVEL_CONFIG_KEYS = {
    "data",
    "server",
    "semantic_search",
    "roles",
    "providers",
    "cloud",
    "agents",
    "projects",
}
_DATA_KEYS = {"dir", "context_db_path"}
_SEMANTIC_SEARCH_KEYS = {
    "embedding_model_id",
    "embedding_cache_dir",
    "semantic_shortlist_size",
    "lexical_shortlist_size",
}
_SERVER_KEYS = {
    "host",
    "port",
    "sync_interval_minutes",
    "maintain_interval_minutes",
    "sync_window_days",
    "sync_max_sessions",
}
_ROLE_KEYS = {
    "provider",
    "model",
    "api_base",
    "fallback_models",
    "temperature",
    "top_p",
    "top_k",
    "max_tokens",
    "parallel_tool_calls",
    "max_iters_maintain",
    "max_iters_ask",
}
_ROLES_KEYS = {"agent"}
_PROVIDER_KEYS = {
    "minimax",
    "minimax_anthropic",
    "zai",
    "openai",
    "openrouter",
    "opencode_go",
    "ollama",
    "mlx",
    "auto_unload",
}
_CLOUD_KEYS = {"endpoint", "token"}
_LEGACY_TOP_LEVEL_KEYS = {"openrouter_provider_order"}
_LEGACY_ROLE_KEYS = {"thinking"}


def _raise_unknown_keys(section: str, raw: dict[str, Any], allowed: set[str]) -> None:
    """Raise a clear error when config contains unsupported keys."""
    unknown = sorted(key for key in raw if key not in allowed)
    if unknown:
        joined = ", ".join(unknown)
        raise ValueError(f"unknown config key(s) in [{section}]: {joined}")


def _normalize_legacy_config_shape(toml_data: dict[str, Any]) -> dict[str, Any]:
    """Drop known legacy keys before strict validation for upgrade safety."""
    normalized = _deep_merge({}, toml_data)
    for key in _LEGACY_TOP_LEVEL_KEYS:
        normalized.pop(key, None)
    roles = _ensure_dict(normalized, "roles")
    for role_payload in roles.values():
        if isinstance(role_payload, dict):
            for key in _LEGACY_ROLE_KEYS:
                role_payload.pop(key, None)
    return normalized


def _platform_agents_fallback(platforms_path: Path) -> dict[str, str]:
    """Load connected platform paths for installs that still rely on platforms.json."""
    from lerim.adapters.registry import load_platforms

    fallback: dict[str, str] = {}
    for name, info in load_platforms(platforms_path).get("platforms", {}).items():
        raw_path = str((info or {}).get("path") or "").strip()
        if not raw_path:
            continue
        path = Path(raw_path).expanduser().resolve()
        if path.exists():
            fallback[str(name).strip()] = str(path)
    return fallback


def _validate_config_shape(toml_data: dict[str, Any]) -> None:
    """Reject unknown sections and unsupported keys in known sections."""
    _raise_unknown_keys("root", toml_data, _TOP_LEVEL_CONFIG_KEYS)
    _raise_unknown_keys("data", _ensure_dict(toml_data, "data"), _DATA_KEYS)
    _raise_unknown_keys("server", _ensure_dict(toml_data, "server"), _SERVER_KEYS)
    _raise_unknown_keys(
        "semantic_search",
        _ensure_dict(toml_data, "semantic_search"),
        _SEMANTIC_SEARCH_KEYS,
    )
    roles = _ensure_dict(toml_data, "roles")
    _raise_unknown_keys("roles", roles, _ROLES_KEYS)
    for role_name, role_payload in roles.items():
        if isinstance(role_payload, dict):
            _raise_unknown_keys(f"roles.{role_name}", role_payload, _ROLE_KEYS)
    _raise_unknown_keys("providers", _ensure_dict(toml_data, "providers"), _PROVIDER_KEYS)
    _raise_unknown_keys("cloud", _ensure_dict(toml_data, "cloud"), _CLOUD_KEYS)


def _ensure_global_infrastructure(global_data_dir: Path) -> None:
    """Create required global runtime directories under ~/.lerim."""
    root = global_data_dir.expanduser()
    for path in (
        root / "workspace",
        root / "index",
        root / "cache",
        root / "logs",
    ):
        path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def load_config() -> Config:
    """Load effective config from TOML layers plus env API keys."""
    # Always load ~/.lerim/.env. Lerim does not load cwd-local env files.
    _lerim_env = Path.home() / ".lerim" / ".env"
    if _lerim_env.is_file():
        load_dotenv(_lerim_env)
    ensure_user_config_exists()
    toml_data, sources = _load_layers()
    toml_data = _normalize_legacy_config_shape(toml_data)
    _validate_config_shape(toml_data)

    global _LAST_CONFIG_SOURCES
    _LAST_CONFIG_SOURCES = sources

    data = toml_data.get("data", {})
    server = toml_data.get("server", {})
    roles = _ensure_dict(toml_data, "roles")
    semantic_search = _ensure_dict(toml_data, "semantic_search")
    global_data_dir = _expand(data.get("dir"), GLOBAL_DATA_DIR)
    context_db_path = _expand(
        data.get("context_db_path"),
        _default_context_db_path(global_data_dir),
    )

    # Infrastructure (workspace, index, locks) always in global dir.
    index_dir = global_data_dir / "index"
    _ensure_global_infrastructure(global_data_dir)

    agent_role = _build_agent_role(roles)

    port = _require_int(server, "port", minimum=1)
    if port > 65535:
        port = 8765

    cloud = _ensure_dict(toml_data, "cloud")

    platforms_path = global_data_dir / "platforms.json"
    agents = {
        **_platform_agents_fallback(platforms_path),
        **_parse_string_table(_ensure_dict(toml_data, "agents")),
    }
    projects = _parse_string_table(_ensure_dict(toml_data, "projects"))

    cloud_endpoint = (
        _to_non_empty_string(os.environ.get("LERIM_CLOUD_ENDPOINT"))
        or _to_non_empty_string(cloud.get("endpoint"))
        or "https://api.lerim.dev"
    )
    cloud_token = (
        _to_non_empty_string(os.environ.get("LERIM_CLOUD_TOKEN"))
        or _to_non_empty_string(cloud.get("token"))
        or None
    )

    return Config(
        global_data_dir=global_data_dir,
        sessions_db_path=global_data_dir / "index" / "sessions.sqlite3",
        context_db_path=context_db_path,
        platforms_path=global_data_dir / "platforms.json",
        embedding_model_id=_to_non_empty_string(
            semantic_search.get("embedding_model_id")
        )
        or "mixedbread-ai/mxbai-embed-xsmall-v1",
        embedding_cache_dir=_expand(
            semantic_search.get("embedding_cache_dir"),
            global_data_dir / "cache" / "embeddings",
        ),
        semantic_shortlist_size=_require_int(
            semantic_search, "semantic_shortlist_size", minimum=1
        ),
        lexical_shortlist_size=_require_int(
            semantic_search, "lexical_shortlist_size", minimum=1
        ),
        server_host=_to_non_empty_string(server.get("host")) or "127.0.0.1",
        server_port=port,
        sync_interval_minutes=_require_int(server, "sync_interval_minutes", minimum=1),
        maintain_interval_minutes=_require_int(
            server, "maintain_interval_minutes", minimum=1
        ),
        sync_window_days=_require_int(server, "sync_window_days", minimum=1),
        sync_max_sessions=_require_int(server, "sync_max_sessions", minimum=1),
        agent_role=agent_role,
        mlflow_enabled=os.getenv("LERIM_MLFLOW", "").strip().lower()
        in ("1", "true", "yes", "on"),
        anthropic_api_key=_to_non_empty_string(os.environ.get("ANTHROPIC_API_KEY"))
        or None,
        openai_api_key=_to_non_empty_string(os.environ.get("OPENAI_API_KEY")) or None,
        zai_api_key=_to_non_empty_string(os.environ.get("ZAI_API_KEY")) or None,
        openrouter_api_key=_to_non_empty_string(os.environ.get("OPENROUTER_API_KEY"))
        or None,
        minimax_api_key=_to_non_empty_string(os.environ.get("MINIMAX_API_KEY")) or None,
        opencode_api_key=_to_non_empty_string(os.environ.get("OPENCODE_API_KEY"))
        or None,
        provider_api_bases=_parse_string_table(_ensure_dict(toml_data, "providers")),
        auto_unload=bool(_ensure_dict(toml_data, "providers").get("auto_unload", True)),
        cloud_endpoint=cloud_endpoint,
        cloud_token=cloud_token,
        agents=agents,
        projects=projects,
    )


def get_config() -> Config:
    """Return cached config from TOML layers + env."""
    return load_config()


def reload_config() -> Config:
    """Clear config cache and return reloaded configuration."""
    load_config.cache_clear()
    return load_config()


def _toml_value(value: Any) -> str:
    """Serialize a Python value to TOML literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, (list, tuple)):
        items = ", ".join(_toml_value(item) for item in value)
        return f"[{items}]"
    return f'"{value}"'


def _toml_write_dict(lines: list[str], data: dict[str, Any], prefix: str) -> None:
    """Write a dict as TOML lines. Handles nested tables and basic types."""
    scalars = {}
    tables = {}
    for key, value in data.items():
        if isinstance(value, dict):
            tables[key] = value
        else:
            scalars[key] = value
    for key, value in scalars.items():
        lines.append(f"{key} = {_toml_value(value)}\n")
    for key, value in tables.items():
        section = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        lines.append(f"\n[{section}]\n")
        _toml_write_dict(lines, value, section)


def save_config_patch(patch: dict[str, Any]) -> Config:
    """Apply config patch to user config TOML and return reloaded Config.

    Reads existing ~/.lerim/config.toml, deep-merges the patch, writes back,
    then reloads the cached config.
    """
    user_path = USER_CONFIG_PATH
    user_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, Any] = {}
    if user_path.exists():
        existing = load_toml_file(user_path)

    merged = _deep_merge(existing, patch)
    return _write_config_full(merged)


def _write_config_full(data: dict[str, Any]) -> Config:
    """Write complete config dict to user TOML and return reloaded Config."""
    user_path = USER_CONFIG_PATH
    user_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Lerim user config\n"]
    _toml_write_dict(lines, data, prefix="")
    user_path.write_text("".join(lines), encoding="utf-8")
    return reload_config()


if __name__ == "__main__":
    """Run a real-path config smoke test and role validation checks."""
    cfg = load_config()
    assert cfg.global_data_dir
    assert cfg.sessions_db_path.name == "sessions.sqlite3"
    assert cfg.context_db_path.name == "context.sqlite3"
    assert cfg.agent_role.provider
    assert cfg.agent_role.model
    assert isinstance(cfg.agent_role.fallback_models, tuple)
    assert isinstance(cfg.mlflow_enabled, bool)
    assert isinstance(cfg.agents, dict)
    assert isinstance(cfg.projects, dict)
    payload = cfg.public_dict()
    assert "agent_role" in payload
    assert "agents" in payload
    assert "projects" in payload
    print(
        f"""\
Config loaded: \
global_data_dir={cfg.global_data_dir}, \
agent={cfg.agent_role.provider}/{cfg.agent_role.model}, \
agents={list(cfg.agents.keys())}, \
projects={list(cfg.projects.keys())}"""
    )
