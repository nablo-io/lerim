"""Domain fixtures for the trajectory-v1 adapter tests.

The trajectory-v1 validators (``trajectory_validator``,
``assert_valid_trajectory``, ``assert_valid_trace_file``) and the loguru
``warning_log`` sink live in ``tests/conftest.py`` because the generic-trace
path in :mod:`lerim.traces.envelope` is held to the same format contract.
What is local here is the fixture catalogue and a Lerim data root in
``tmp_path`` that still reaches the real node install, so tests do not re-run
``npm install`` per test.

The Apache-2.0 fixtures under ``tests/fixtures/trajectory/`` are upstream's own,
copied verbatim from tag ``v0.2.0`` — the version
:data:`lerim.adapters.trajectory_bridge.TRAJECTORY_VERSION` pins — so parity
tests compare Lerim's output against the reference implementation's, not
against a Lerim-authored expectation.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from lerim.adapters import trajectory_bridge
from lerim.config import settings as config_settings
from tests.helpers import write_test_config

TRAJECTORY_FIXTURES_DIR = Path(__file__).parents[2] / "fixtures" / "trajectory"

# Every upstream fixture case, as ``<source>/<case>``. Sources Lerim maps in
# SOURCE_MAP only: hermes/openhands/deepagents cannot be listed as transcript
# files, so their fixtures would test a path Lerim never takes.
FIXTURE_CASES = (
    "claude-code/tool-call",
    "claude-code/cleanup",
    "codex/tool-calls",
    "codex/cleanup",
    "letta-code/tool-calls",
    "letta-code/cleanup",
    "openclaw/tool-calls",
    "openclaw/cleanup",
)


@pytest.fixture
def trajectory_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point Lerim's data root at ``tmp_path`` while keeping the real node install.

    Trace caches, the sessions DB and ``platforms.json`` then live in the test's
    own directory, but ``node_root()`` still resolves to the developer's
    ``~/.lerim/node``, so the pinned package is installed once for the whole run
    instead of once per test.
    """
    real_node_root = trajectory_bridge.node_root()
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    monkeypatch.setattr(trajectory_bridge, "node_root", lambda: real_node_root)
    config_settings.load_config.cache_clear()
    yield tmp_path
    config_settings.load_config.cache_clear()
