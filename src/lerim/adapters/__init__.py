"""Native harness session discovery, normalization, and platform registry."""

from lerim.adapters.registry import (
    KNOWN_PLATFORMS,
    connect_platform,
    get_connected_agents,
    get_connected_platform_paths,
    list_platforms,
    load_platforms,
    remove_platform,
    save_platforms,
)
from lerim.adapters.trajectory_source import (
    SOURCE_MAP,
    UNSUPPORTED_PLATFORMS,
    SessionListing,
    UnsupportedPlatformError,
    list_sessions,
    normalize_batch,
)

__all__ = [
    "KNOWN_PLATFORMS",
    "SOURCE_MAP",
    "UNSUPPORTED_PLATFORMS",
    "SessionListing",
    "UnsupportedPlatformError",
    "connect_platform",
    "get_connected_agents",
    "get_connected_platform_paths",
    "list_platforms",
    "list_sessions",
    "load_platforms",
    "normalize_batch",
    "remove_platform",
    "save_platforms",
]
