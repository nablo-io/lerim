"""Shared test fixtures for Lerim's maintained test suite.

This file only supports the DB-only runtime.
It provides temporary global Lerim roots and trace fixture paths.
"""

import os
from pathlib import Path

import pytest

from lerim.config import settings as config_settings
from lerim.server.runtime import LerimRuntime
from tests.live_helpers import build_live_config
from tests.helpers import make_config


FIXTURES_DIR = Path(__file__).parent / "fixtures"
TRACES_DIR = FIXTURES_DIR / "traces"
TRACE_INGESTION_TRACES_DIR = TRACES_DIR / "trace_ingestion"
EXPECTATIONS_DIR = FIXTURES_DIR / "expectations"
ANSWER_EXPECTATIONS_DIR = EXPECTATIONS_DIR / "answer"
TRACE_INGESTION_EXPECTATIONS_DIR = EXPECTATIONS_DIR / "trace_ingestion"
CURATE_EXPECTATIONS_DIR = EXPECTATIONS_DIR / "curate"
RUNTIME_EXPECTATIONS_DIR = EXPECTATIONS_DIR / "runtime"
SCOPE_EXPECTATIONS_DIR = EXPECTATIONS_DIR / "scope"
CLOUD_EXPECTATIONS_DIR = EXPECTATIONS_DIR / "cloud"
QUEUE_EXPECTATIONS_DIR = EXPECTATIONS_DIR / "queue"
CLI_SURFACE_EXPECTATIONS_DIR = EXPECTATIONS_DIR / "cli_surface"
TEST_CONFIG_PATH = Path(__file__).parent / "test_config.toml"


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path, monkeypatch):
    """Keep tests independent from the developer machine user config."""
    monkeypatch.setattr(
        config_settings,
        "USER_CONFIG_PATH",
        tmp_path / "empty-user-config.toml",
    )
    config_settings.load_config.cache_clear()
    yield
    config_settings.load_config.cache_clear()


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
