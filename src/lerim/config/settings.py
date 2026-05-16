"""Central config loading from layered TOML files with role-based LLM settings.

Layers (low to high priority):
1. lerim/config/default.toml
2. ~/.lerim/config.toml
3. LERIM_CONFIG env path (optional explicit override)

API keys are read from environment variables only.
"""

from __future__ import annotations

import os
import shutil
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
_MISSING = object()


@dataclass(frozen=True)
class RoleConfig:
	"""Configuration for the agent LLM role.

	All fields have defaults so the same class works for any future role.
	"""

	provider: str
	model: str
	api_base: str = ""
	temperature: float = 1.0
	# BAML/LangGraph trace ingestion derives its windowing budget from trace size in
	# lerim.agents.trace_ingestion.windowing. There is no static trace-ingestion
	# budget field on RoleConfig.
	curate_max_llm_calls: int = 30
	answer_max_retrieval_actions: int = 30


def load_toml_file(path: Path | None) -> dict[str, Any]:
    """Load TOML file into a dict, failing fast on invalid existing files."""
    if not path or not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except Exception as exc:
        raise ValueError(f"invalid TOML config file: {path}") from exc
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


def _to_non_empty_string(value: Any) -> str:
    """Convert value to stripped string, defaulting to empty string."""
    if value is None:
        return ""
    return str(value).strip()


def _read_optional_string(raw: dict[str, Any], key: str) -> str:
    """Read an optional string config value without coercing other types."""
    value = raw.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"config key {key} must be a string, got: {value!r}")
    return value.strip()


def _read_optional_path(raw: dict[str, Any], key: str, default: Path) -> Path:
    """Read an optional TOML path string and expand it without scalar coercion."""
    value = raw.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"config key {key} must be a string path, got: {value!r}")
    if not value.strip():
        return default
    return Path(value.strip()).expanduser()


def _ensure_dict(data: dict[str, Any], key: str) -> dict[str, Any]:
	"""Get a table value from config data, failing on wrong section types."""
	val = data.get(key, {})
	if val is None:
		return {}
	if not isinstance(val, dict):
		raise ValueError(f"config section [{key}] must be a table")
	return val


def _read_bool(
    raw: dict[str, Any],
    key: str,
    *,
    default: bool | object = _MISSING,
) -> bool:
    """Read a strictly typed boolean config value."""
    value = raw.get(key)
    if value is None:
        if default is not _MISSING:
            return bool(default)
        raise ValueError(
            f"missing required config key: {key} (set it in default.toml or user config)"
        )
    if not isinstance(value, bool):
        raise ValueError(f"config key {key} must be a boolean, got: {value!r}")
    return value


def _read_int(
    raw: dict[str, Any],
    key: str,
    *,
    default: int | object = _MISSING,
    minimum: int = 0,
) -> int:
    """Read a strictly typed integer config value."""
    value = raw.get(key)
    if value is None:
        if default is not _MISSING:
            value = default
        else:
            raise ValueError(
                f"missing required config key: {key} (set it in default.toml or user config)"
            )
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"config key {key} must be an integer, got: {value!r}")
    if value < minimum:
        raise ValueError(f"config key {key} must be >= {minimum}, got: {value!r}")
    return value


def _read_float(
    raw: dict[str, Any],
    key: str,
    *,
    default: float | object = _MISSING,
) -> float:
    """Read a strictly typed floating-point config value."""
    value = raw.get(key)
    if value is None:
        if default is not _MISSING:
            value = default
        else:
            raise ValueError(
                f"missing required config key: {key} (set it in default.toml or user config)"
            )
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"config key {key} must be a float, got: {value!r}")
    return float(value)


def _require_int(raw: dict[str, Any], key: str, minimum: int = 0) -> int:
    """Read a required strictly typed integer from config dict."""
    return _read_int(raw, key, minimum=minimum)


def get_user_config_path() -> Path:
    """Return the effective writable config path.

    When ``LERIM_CONFIG`` is set, writes should target that explicit override
    file rather than silently mutating the default user config.
    """
    explicit = os.getenv("LERIM_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    return USER_CONFIG_PATH


def get_global_data_dir_path() -> Path:
    """Return the effective global data root from layered TOML config."""
    toml_data, _sources = _load_layers()
    data = _ensure_dict(toml_data, "data")
    return _read_optional_path(data, "dir", GLOBAL_DATA_DIR)


def get_user_env_path() -> Path:
    """Return the effective ``.env`` path under the active global data dir."""
    return get_global_data_dir_path() / ".env"


def get_trace_cache_dir(agent_name: str) -> Path:
    """Return the compacted trace cache directory for one agent."""
    safe_name = str(agent_name or "").strip().lower()
    if not safe_name:
        raise ValueError("agent_name is required")
    return get_global_data_dir_path() / "cache" / "traces" / safe_name


def ensure_user_config_exists() -> Path:
    """Create user config scaffold outside pytest if it does not exist."""
    path = get_user_config_path()
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
    ingest_interval_minutes: int
    curate_interval_minutes: int
    ingest_window_days: int
    ingest_max_sessions: int

    agent_role: RoleConfig

    mlflow_enabled: bool

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
    project_types: dict[str, str]

    def public_dict(self) -> dict[str, Any]:
        """Return safe serialized config for CLI/dashboard visibility."""
        return {
            "embedding_model_id": self.embedding_model_id,
            "semantic_shortlist_size": self.semantic_shortlist_size,
            "lexical_shortlist_size": self.lexical_shortlist_size,
            "server_host": self.server_host,
            "server_port": self.server_port,
            "ingest_interval_minutes": self.ingest_interval_minutes,
            "curate_interval_minutes": self.curate_interval_minutes,
            "ingest_window_days": self.ingest_window_days,
            "ingest_max_sessions": self.ingest_max_sessions,
            "agent_role": {
                "provider": self.agent_role.provider,
                "model": self.agent_role.model,
            },
            "mlflow_enabled": self.mlflow_enabled,
            "auto_unload": self.auto_unload,
            "cloud_authenticated": self.cloud_token is not None,
            "connected_agents": sorted(self.agents),
            "project_names": sorted(self.projects),
            "project_types": dict(sorted(self.project_types.items())),
        }


def _build_role(
	raw: dict[str, Any], *, default_provider: str, default_model: str
) -> RoleConfig:
	"""Build a role config from TOML payload."""
	from lerim.config.providers import normalize_model_name

	provider = _read_optional_string(raw, "provider") or default_provider
	model = _read_optional_string(raw, "model") or default_model
	model = normalize_model_name(provider, model)
	return RoleConfig(
		provider=provider,
		model=model,
		api_base=_read_optional_string(raw, "api_base"),
		temperature=_read_float(raw, "temperature", default=1.0),
		curate_max_llm_calls=_read_int(raw, "curate_max_llm_calls", default=30),
		answer_max_retrieval_actions=_read_int(
			raw, "answer_max_retrieval_actions", default=30
		),
	)


def _build_agent_role(roles: dict[str, Any]) -> RoleConfig:
	"""Build agent role config from TOML roles section."""
	return _build_role(
		_ensure_dict(roles, "agent"),
		default_provider="openrouter",
		default_model="qwen/qwen3-coder-30b-a3b-instruct",
	)


def _parse_string_table(raw: dict[str, Any], *, section: str = "config") -> dict[str, str]:
    """Parse a TOML table of ``name = "value"`` entries."""
    result: dict[str, str] = {}
    for key, value in raw.items():
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError(f"config key {section}.{key} must be a string, got: {value!r}")
        text = value.strip()
        if text:
            result[key] = text
    return result


PROJECT_TYPE_SUPPORTED = "supported"
PROJECT_TYPE_CUSTOM = "custom"
PROJECT_TYPE_CHOICES = frozenset({PROJECT_TYPE_SUPPORTED, PROJECT_TYPE_CUSTOM})


def normalize_project_type(value: str | None) -> str:
    """Normalize a configured project source type."""
    text = (value or PROJECT_TYPE_SUPPORTED).strip().lower()
    if not text:
        return PROJECT_TYPE_SUPPORTED
    if text not in PROJECT_TYPE_CHOICES:
        allowed = ", ".join(sorted(PROJECT_TYPE_CHOICES))
        raise ValueError(f"project type must be one of: {allowed}")
    return text


def _parse_project_types_table(raw: dict[str, Any]) -> dict[str, str]:
    """Parse project-name to source-type mappings."""
    parsed = _parse_string_table(raw, section="project_types")
    return {name: normalize_project_type(value) for name, value in parsed.items()}


def _default_context_db_path(global_data_dir: Path) -> Path:
    """Return the canonical global context DB path for the current data root."""
    return global_data_dir / "context.sqlite3"


_TOP_LEVEL_CONFIG_KEYS = {
    "data",
    "server",
    "semantic_search",
    "observability",
    "roles",
    "providers",
    "cloud",
    "agents",
    "projects",
    "project_types",
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
    "ingest_interval_minutes",
    "curate_interval_minutes",
    "ingest_window_days",
    "ingest_max_sessions",
}
_OBSERVABILITY_KEYS = {"mlflow_enabled"}
_ROLE_KEYS = {
    "provider",
    "model",
    "api_base",
    "temperature",
	"curate_max_llm_calls",
	"answer_max_retrieval_actions",
}
_ROLES_KEYS = {"agent"}
_PROVIDER_KEYS = {
    "minimax",
    "zai",
    "openai",
    "openrouter",
    "opencode_go",
    "ollama",
    "mlx",
    "auto_unload",
}
_CLOUD_KEYS = {"endpoint", "token"}
def _raise_unknown_keys(section: str, raw: dict[str, Any], allowed: set[str]) -> None:
    """Raise a clear error when config contains unsupported keys."""
    unknown = sorted(key for key in raw if key not in allowed)
    if unknown:
        joined = ", ".join(unknown)
        raise ValueError(f"unknown config key(s) in [{section}]: {joined}")


def _validate_config_shape(toml_data: dict[str, Any]) -> None:
    """Reject unknown sections and unsupported keys in known sections."""
    _raise_unknown_keys("root", toml_data, _TOP_LEVEL_CONFIG_KEYS)
    _raise_unknown_keys("data", _ensure_dict(toml_data, "data"), _DATA_KEYS)
    _raise_unknown_keys("server", _ensure_dict(toml_data, "server"), _SERVER_KEYS)
    _raise_unknown_keys(
        "observability",
        _ensure_dict(toml_data, "observability"),
        _OBSERVABILITY_KEYS,
    )
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
        root / "cache" / "traces" / "claude",
        root / "cache" / "traces" / "codex",
        root / "cache" / "traces" / "cursor",
        root / "cache" / "traces" / "opencode",
        root / "models" / "embeddings",
        root / "models" / "huggingface" / "hub",
        root / "logs",
    ):
        path.mkdir(parents=True, exist_ok=True)


def remove_legacy_memory_dir(global_data_dir: Path) -> bool:
    """Remove the retired file-backed memory directory, if present."""
    legacy_dir = global_data_dir.expanduser() / "memory"
    if not legacy_dir.exists():
        return False
    if not legacy_dir.is_dir():
        return False
    shutil.rmtree(legacy_dir)
    return True


@lru_cache(maxsize=1)
def load_config() -> Config:
    """Load effective config from TOML layers plus env API keys."""
    ensure_user_config_exists()
    toml_data, sources = _load_layers()
    _validate_config_shape(toml_data)

    global _LAST_CONFIG_SOURCES
    _LAST_CONFIG_SOURCES = sources

    data = toml_data.get("data", {})
    server = toml_data.get("server", {})
    observability = _ensure_dict(toml_data, "observability")
    roles = _ensure_dict(toml_data, "roles")
    semantic_search = _ensure_dict(toml_data, "semantic_search")
    global_data_dir = _read_optional_path(data, "dir", GLOBAL_DATA_DIR)
    env_path = global_data_dir / ".env"
    if env_path.is_file():
        load_dotenv(env_path)
    context_db_path = _read_optional_path(
        data,
        "context_db_path",
        _default_context_db_path(global_data_dir),
    )

    # Infrastructure (workspace, index, locks) always in global dir.
    _ensure_global_infrastructure(global_data_dir)

    agent_role = _build_agent_role(roles)

    port = _require_int(server, "port", minimum=1)
    if port > 65535:
        raise ValueError("config key port must be <= 65535")

    cloud = _ensure_dict(toml_data, "cloud")

    agents = _parse_string_table(_ensure_dict(toml_data, "agents"), section="agents")
    projects = _parse_string_table(_ensure_dict(toml_data, "projects"), section="projects")
    project_types = _parse_project_types_table(_ensure_dict(toml_data, "project_types"))

    cloud_endpoint = _to_non_empty_string(os.environ.get("LERIM_CLOUD_ENDPOINT"))
    if not cloud_endpoint:
        cloud_endpoint = _read_optional_string(cloud, "endpoint") or "https://api.lerim.dev"
    cloud_token = _to_non_empty_string(os.environ.get("LERIM_CLOUD_TOKEN"))
    if not cloud_token:
        cloud_token = _read_optional_string(cloud, "token")

    return Config(
        global_data_dir=global_data_dir,
        sessions_db_path=global_data_dir / "index" / "sessions.sqlite3",
        context_db_path=context_db_path,
        platforms_path=global_data_dir / "platforms.json",
        embedding_model_id=_read_optional_string(
            semantic_search, "embedding_model_id"
        )
        or "mixedbread-ai/mxbai-embed-xsmall-v1",
        embedding_cache_dir=_read_optional_path(
            semantic_search,
            "embedding_cache_dir",
            global_data_dir / "models" / "embeddings",
        ),
        semantic_shortlist_size=_require_int(
            semantic_search, "semantic_shortlist_size", minimum=1
        ),
        lexical_shortlist_size=_require_int(
            semantic_search, "lexical_shortlist_size", minimum=1
        ),
        server_host=_read_optional_string(server, "host") or "127.0.0.1",
        server_port=port,
        ingest_interval_minutes=_require_int(server, "ingest_interval_minutes", minimum=1),
        curate_interval_minutes=_require_int(
            server, "curate_interval_minutes", minimum=1
        ),
        ingest_window_days=_require_int(server, "ingest_window_days", minimum=1),
        ingest_max_sessions=_require_int(server, "ingest_max_sessions", minimum=1),
        agent_role=agent_role,
        mlflow_enabled=(
            os.getenv("LERIM_MLFLOW", "").strip().lower() in ("1", "true", "yes", "on")
            if os.getenv("LERIM_MLFLOW") is not None
            else _read_bool(observability, "mlflow_enabled", default=False)
        ),
        openai_api_key=_to_non_empty_string(os.environ.get("OPENAI_API_KEY")) or None,
        zai_api_key=_to_non_empty_string(os.environ.get("ZAI_API_KEY")) or None,
        openrouter_api_key=_to_non_empty_string(os.environ.get("OPENROUTER_API_KEY"))
        or None,
        minimax_api_key=_to_non_empty_string(os.environ.get("MINIMAX_API_KEY")) or None,
        opencode_api_key=_to_non_empty_string(os.environ.get("OPENCODE_API_KEY"))
        or None,
        provider_api_bases=_parse_string_table(
            {
                key: value
                for key, value in _ensure_dict(toml_data, "providers").items()
                if key != "auto_unload"
            },
            section="providers",
        ),
        auto_unload=_read_bool(
            _ensure_dict(toml_data, "providers"), "auto_unload", default=True
        ),
        cloud_endpoint=cloud_endpoint,
        cloud_token=cloud_token or None,
        agents=agents,
        projects=projects,
        project_types=project_types,
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
    user_path = get_user_config_path()
    user_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, Any] = {}
    if user_path.exists():
        existing = load_toml_file(user_path)

    merged = _deep_merge(existing, patch)
    return _write_config_full(merged)


def _write_config_full(data: dict[str, Any]) -> Config:
    """Write complete config dict to user TOML and return reloaded Config."""
    user_path = get_user_config_path()
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
    assert isinstance(cfg.mlflow_enabled, bool)
    assert isinstance(cfg.agents, dict)
    assert isinstance(cfg.projects, dict)
    payload = cfg.public_dict()
    assert "agent_role" in payload
    assert "connected_agents" in payload
    assert "project_names" in payload
    print(
        f"""\
Config loaded: \
global_data_dir={cfg.global_data_dir}, \
agent={cfg.agent_role.provider}/{cfg.agent_role.model}, \
agents={list(cfg.agents.keys())}, \
projects={list(cfg.projects.keys())}"""
    )
