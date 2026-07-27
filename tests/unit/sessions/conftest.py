"""Shared fixtures for session catalog tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from lerim.adapters import trajectory_bridge
from lerim.config import settings as config_settings
from tests.helpers import make_config, write_test_config


@pytest.fixture
def sessions_db(tmp_path, monkeypatch):
    """Create and return path to a fresh sessions SQLite database."""
    db_path = tmp_path / "index" / "sessions.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    config = make_config(tmp_path)
    monkeypatch.setattr("lerim.sessions.catalog.get_config", lambda: config)
    from lerim.sessions.catalog import init_sessions_db

    init_sessions_db()
    return db_path


@pytest.fixture
def claude_store(tmp_path, sessions_db, monkeypatch) -> Path:
    """Return a claude-code store wired as the only connected platform.

    Discovery and ingest run the real normalizer over real transcript files, so
    these tests exercise the path a user's machine takes. Only the node install
    is shared with the developer's ``~/.lerim`` — trace caches, the sessions DB
    and the platform registry all live in ``tmp_path``.
    """
    real_node_root = trajectory_bridge.node_root()
    monkeypatch.setenv("LERIM_CONFIG", str(write_test_config(tmp_path)))
    monkeypatch.setattr(trajectory_bridge, "node_root", lambda: real_node_root)
    config_settings.load_config.cache_clear()

    root = tmp_path / "claude-projects"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "lerim.sessions.catalog.adapter_registry.get_connected_platform_paths",
        lambda _path: {"claude": root},
    )
    monkeypatch.setattr(
        "lerim.sessions.catalog.adapter_registry.get_connected_agents",
        lambda _path: ["claude"],
    )
    yield root
    config_settings.load_config.cache_clear()
