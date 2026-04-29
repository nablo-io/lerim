"""Unit test fixtures — autouse dummy API key so provider constructors work."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _ensure_api_key(monkeypatch):
    """Set a dummy OPENROUTER_API_KEY for unit tests that construct providers."""
    if not os.environ.get("OPENROUTER_API_KEY"):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-dummy-key")
