"""Shared fixtures for session catalog tests."""

from __future__ import annotations


import pytest

from tests.helpers import make_config


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
