"""Connected platform registry for session adapters."""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_ADAPTER_MODULES: dict[str, str] = {
    "claude": "lerim.adapters.claude",
    "codex": "lerim.adapters.codex",
    "opencode": "lerim.adapters.opencode",
    "cursor": "lerim.adapters.cursor",
}

_AUTO_SEED_PLATFORMS = ("claude", "codex", "opencode", "cursor")

KNOWN_PLATFORMS = tuple(_ADAPTER_MODULES.keys())


def get_adapter(name: str):
    """Return adapter module for a known platform name."""
    module_path = _ADAPTER_MODULES.get(name)
    if not module_path:
        return None
    return importlib.import_module(module_path)


def default_path_for(name: str) -> Path | None:
    """Return adapter default traces path for a platform."""
    adapter = get_adapter(name)
    if not adapter:
        return None
    return adapter.default_path()


def _count_sessions(path: Path, name: str) -> int:
    """Count sessions for a platform at a specific filesystem path."""
    adapter = get_adapter(name)
    if not adapter:
        return 0
    return adapter.count_sessions(path)


def load_platforms(path: Path) -> dict[str, Any]:
    """Load connected platform registry data from JSON."""
    if not path.exists():
        return {"platforms": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"platforms": {}}
    if not isinstance(data, dict):
        return {"platforms": {}}
    platforms = data.get("platforms")
    if not isinstance(platforms, dict):
        return {"platforms": {}}
    return {"platforms": platforms}


def save_platforms(path: Path, data: dict[str, Any]) -> None:
    """Persist connected platform registry data to JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )


def auto_seed(path: Path) -> dict[str, Any]:
    """Auto-seed default connected platforms when registry is missing."""
    if path.exists():
        return load_platforms(path)

    data: dict[str, Any] = {"platforms": {}}
    for name in _AUTO_SEED_PLATFORMS:
        default = default_path_for(name)
        if default and default.exists():
            data["platforms"][name] = {
                "path": str(default),
                "connected_at": datetime.now(timezone.utc).isoformat(),
            }
    if data["platforms"]:
        save_platforms(path, data)
    return data


def connect_platform(
    path: Path, name: str, custom_path: str | None = None
) -> dict[str, Any]:
    """Connect a platform path and return connection metadata."""
    data = load_platforms(path)

    if custom_path:
        resolved = Path(custom_path).expanduser().resolve()
    else:
        resolved = default_path_for(name)
        if resolved is None:
            return {
                "name": name,
                "path": None,
                "session_count": 0,
                "connected_at": None,
                "status": "unknown_platform",
            }

    if not resolved.exists():
        return {
            "name": name,
            "path": str(resolved),
            "session_count": 0,
            "connected_at": None,
            "status": "path_not_found",
        }

    session_count = _count_sessions(resolved, name)
    connected_at = datetime.now(timezone.utc).isoformat()
    data["platforms"][name] = {
        "path": str(resolved),
        "connected_at": connected_at,
    }
    save_platforms(path, data)

    result: dict[str, Any] = {
        "name": name,
        "path": str(resolved),
        "session_count": session_count,
        "connected_at": connected_at,
        "status": "connected",
    }

    # Attach adapter health-check info when available (advisory, non-blocking)
    adapter = get_adapter(name)
    if adapter and hasattr(adapter, "validate_connection"):
        result["validation"] = adapter.validate_connection(resolved)

    return result


def remove_platform(path: Path, name: str) -> bool:
    """Remove a platform entry from the connection registry."""
    data = load_platforms(path)
    if name not in data["platforms"]:
        return False
    del data["platforms"][name]
    save_platforms(path, data)
    return True


def list_platforms(path: Path, with_counts: bool = True) -> list[dict[str, Any]]:
    """List connected platforms with optional live session counts."""
    data = load_platforms(path)
    output: list[dict[str, Any]] = []
    for name, info in data["platforms"].items():
        platform_path = Path(str(info.get("path") or "")).expanduser()
        session_count = _count_sessions(platform_path, name) if with_counts else 0
        output.append(
            {
                "name": name,
                "path": info.get("path", ""),
                "connected_at": info.get("connected_at", ""),
                "session_count": session_count,
                "exists": platform_path.exists(),
            }
        )
    return output


def get_connected_agents(path: Path) -> list[str]:
    """Return names of currently connected platform agents."""
    data = auto_seed(path)
    return list(data.get("platforms", {}).keys())


def get_connected_platform_paths(path: Path) -> dict[str, Path]:
    """Return connected platform names mapped to existing resolved paths."""
    data = auto_seed(path)
    results: dict[str, Path] = {}
    for name, info in data.get("platforms", {}).items():
        raw = info.get("path")
        if not raw:
            continue
        p = Path(raw).expanduser()
        if p.exists():
            results[name] = p
    return results
