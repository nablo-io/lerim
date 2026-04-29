"""Shared test fixtures for Lerim's maintained test suite.

This file only supports the DB-only runtime.
It provides temporary global Lerim roots and trace fixture paths.
"""

import os
from pathlib import Path

import pytest

from lerim.server.runtime import LerimRuntime
from tests.live_helpers import build_live_config
from tests.helpers import make_config


FIXTURES_DIR = Path(__file__).parent / "fixtures"
TRACES_DIR = FIXTURES_DIR / "traces"
EXTRACT_TRACES_DIR = TRACES_DIR / "extract"
EXPECTATIONS_DIR = FIXTURES_DIR / "expectations"
ASK_EXPECTATIONS_DIR = EXPECTATIONS_DIR / "ask"
EXTRACT_EXPECTATIONS_DIR = EXPECTATIONS_DIR / "extract"
MAINTAIN_EXPECTATIONS_DIR = EXPECTATIONS_DIR / "maintain"
RUNTIME_EXPECTATIONS_DIR = EXPECTATIONS_DIR / "runtime"
SCOPE_EXPECTATIONS_DIR = EXPECTATIONS_DIR / "scope"
CLOUD_EXPECTATIONS_DIR = EXPECTATIONS_DIR / "cloud"
QUEUE_EXPECTATIONS_DIR = EXPECTATIONS_DIR / "queue"
CLI_SURFACE_EXPECTATIONS_DIR = EXPECTATIONS_DIR / "cli_surface"
TEST_CONFIG_PATH = Path(__file__).parent / "test_config.toml"


@pytest.fixture
def tmp_lerim_root(tmp_path):
    """Temporary global Lerim root with canonical folder structure."""
    (tmp_path / "workspace").mkdir()
    (tmp_path / "index").mkdir()
    return tmp_path


@pytest.fixture
def tmp_config(tmp_path, tmp_lerim_root):
    """Temporary config pointing at tmp_lerim_root."""
    return make_config(tmp_lerim_root)


@pytest.fixture
def live_lerim_root(tmp_path):
    """Temporary global Lerim root for live smoke, integration, and e2e tests."""
    return tmp_path / ".lerim"


@pytest.fixture
def live_config(live_lerim_root):
    """Temporary live config that preserves current provider settings but isolates state."""
    return build_live_config(live_lerim_root)


@pytest.fixture
def live_repo_root(tmp_path):
    """Temporary project root used for live runtime tests."""
    repo_root = tmp_path / "live-project"
    repo_root.mkdir(parents=True, exist_ok=True)
    return repo_root


@pytest.fixture
def live_runtime(live_config, live_repo_root):
    """Live runtime pointing at the temporary project root and isolated global state."""
    return LerimRuntime(default_cwd=str(live_repo_root), config=live_config)


def skip_unless_env(var_name):
    """Skip test unless environment variable is set."""
    return pytest.mark.skipif(
        not os.environ.get(var_name),
        reason=f"{var_name} not set",
    )
