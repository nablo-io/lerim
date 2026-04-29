"""Smoke test gate — skip this folder unless LERIM_SMOKE=1 or LERIM_INTEGRATION=1."""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items):
	"""Skip smoke tests unless LERIM_SMOKE or LERIM_INTEGRATION is set."""
	if os.environ.get("LERIM_SMOKE") or os.environ.get("LERIM_INTEGRATION"):
		return
	smoke_dir = os.path.dirname(__file__)
	skip = pytest.mark.skip(reason="LERIM_SMOKE or LERIM_INTEGRATION not set")
	for item in items:
		if str(item.fspath).startswith(smoke_dir):
			item.add_marker(skip)
