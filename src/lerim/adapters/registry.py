"""Connected platform registry: which harnesses Lerim reads sessions from."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lerim.adapters.trajectory_source import (
    SOURCE_MAP,
    UNSUPPORTED_PLATFORMS,
    default_root,
    list_sessions,
)

# Platforms `lerim connect` accepts as an argument. Unsupported ones stay
# listed so connecting them explains why they are gone instead of reporting an
# unknown name.
KNOWN_PLATFORMS = tuple(SOURCE_MAP) + tuple(UNSUPPORTED_PLATFORMS)


def default_path_for(name: str) -> Path | None:
    """Return the default session store path for a platform."""
    if name not in SOURCE_MAP:
        return None
    return default_root(name)


def _count_sessions(path: Path, name: str) -> int:
    """Count discoverable sessions for a platform at a filesystem path."""
    if name not in SOURCE_MAP:
        return 0
    return len(list_sessions(name, root=path))


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
    for name in SOURCE_MAP:
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
    if name in UNSUPPORTED_PLATFORMS:
        return {
            "name": name,
            "path": None,
            "session_count": 0,
            "connected_at": None,
            "status": "unsupported_platform",
            "message": UNSUPPORTED_PLATFORMS[name],
        }

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

    return {
        "name": name,
        "path": str(resolved),
        "session_count": session_count,
        "connected_at": connected_at,
        "status": "connected",
    }


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
        entry: dict[str, Any] = {
            "name": name,
            "path": info.get("path", ""),
            "connected_at": info.get("connected_at", ""),
            "session_count": session_count,
            "exists": platform_path.exists(),
        }
        if name in UNSUPPORTED_PLATFORMS:
            entry["status"] = "unsupported_platform"
            entry["message"] = UNSUPPORTED_PLATFORMS[name]
        output.append(entry)
    return output


def get_connected_agents(path: Path) -> list[str]:
    """Return the connected platforms Lerim can currently ingest."""
    data = auto_seed(path)
    return [name for name in data.get("platforms", {}) if name in SOURCE_MAP]


def get_connected_platform_paths(path: Path) -> dict[str, Path]:
    """Return ingestible connected platforms mapped to existing resolved paths."""
    data = auto_seed(path)
    results: dict[str, Path] = {}
    for name, info in data.get("platforms", {}).items():
        if name not in SOURCE_MAP:
            continue
        raw = info.get("path")
        if not raw:
            continue
        resolved = Path(raw).expanduser()
        if resolved.exists():
            results[name] = resolved
    return results
