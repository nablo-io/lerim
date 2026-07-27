"""Shared test fixtures for Lerim's maintained test suite.

This file only supports the DB-only runtime.
It provides temporary global Lerim roots, trace fixture paths, and the
trajectory-v1 validators every trace-writing path is checked against.
"""

import json
import os
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from loguru import logger

from lerim.adapters import trajectory_bridge
from lerim.adapters.common import _LINE_BREAK_ESCAPES
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


def trajectory_schema_path() -> Path:
    """Return the schema shipped by the installed trajectory package."""
    return (
        trajectory_bridge.node_root()
        / "node_modules"
        / "@letta-ai"
        / "trajectory"
        / "schema"
        / "trajectory-v1.schema.json"
    )


@pytest.fixture(scope="session")
def trajectory_validator() -> Draft202012Validator:
    """Return a validator built from the installed trajectory-v1 schema.

    The schema is read from the npm package rather than vendored, so a version
    bump that changes the format fails these tests instead of passing against a
    stale copy. Installing it is the bridge's own bootstrap, so a machine
    without node fails loudly here.
    """
    trajectory_bridge.ensure_trajectory_installed()
    schema = json.loads(trajectory_schema_path().read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


@pytest.fixture
def assert_valid_trajectory(
    trajectory_validator: Draft202012Validator,
) -> Callable[[Sequence[dict[str, Any]]], None]:
    """Return a callable asserting a record list is valid trajectory-v1."""

    def _assert(records: Sequence[dict[str, Any]]) -> None:
        """Fail with the schema's own message when a record is off-format."""
        errors = sorted(
            trajectory_validator.iter_errors(list(records)), key=lambda e: e.path
        )
        assert not errors, "\n".join(
            f"{list(error.path)}: {error.message}" for error in errors
        )

    return _assert


@pytest.fixture
def assert_valid_trace_file(
    assert_valid_trajectory: Callable[[Sequence[dict[str, Any]]], None],
) -> Callable[[Path], list[dict[str, Any]]]:
    """Return a callable checking a written trace file's bytes and its records.

    Every path that writes a trace cache — the trajectory bridge for harness
    sessions and :mod:`lerim.traces.envelope` for generic ones — is held to this
    one check. The line assertion reads raw file bytes rather than re-parsing,
    because the invariant under test is textual: ``trace_ingestion/windowing.py``
    numbers lines and extracted context cites evidence as ``line:<N>``, so a
    record that spans two lines silently rebinds every later citation.
    """

    def _assert(path: Path) -> list[dict[str, Any]]:
        """Assert one file is one compact record per line and valid, then return it."""
        raw = path.read_text(encoding="utf-8")
        assert raw.endswith("\n"), f"{path} does not end with a newline"
        # splitlines() breaks on U+2028/U+2029/U+0085 as well as \n, so agreeing
        # with a plain \n split proves no record carries a raw line separator.
        lines = raw.splitlines()
        assert lines == raw.split("\n")[:-1], (
            f"{path} contains a record split across lines"
        )
        records = [json.loads(line) for line in lines]
        assert records[0]["role"] == "meta", f"{path} does not open with meta"
        assert raw == "".join(
            f"{json.dumps(record, ensure_ascii=False, separators=(',', ':'))}\n"
            for record in records
        ).translate(_LINE_BREAK_ESCAPES), f"{path} is not written compactly"
        assert_valid_trajectory(records)
        return records

    return _assert


@pytest.fixture
def warning_log() -> Iterator[list[str]]:
    """Collect Lerim's loguru warnings for the duration of one test.

    Lerim logs through loguru, which does not reach pytest's ``caplog``, so
    skip-and-log behavior needs its own sink to be observable at all. Staying
    at WARNING is the point: a test using this fixture asserts that a skip is
    loud, and stays failing if the message is later demoted.
    """
    messages: list[str] = []
    sink_id = logger.add(
        lambda message: messages.append(str(message)), level="WARNING", format="{message}"
    )
    try:
        yield messages
    finally:
        logger.remove(sink_id)


@pytest.fixture
def leveled_log() -> Iterator[list[str]]:
    """Collect Lerim's loguru output from DEBUG up, each line prefixed by level.

    Deliberately quiet paths cannot be checked with :func:`warning_log`, which
    cannot see below WARNING. Lines arrive as ``"<LEVEL>|<message>"`` so a test
    can assert both that something was recorded and at which level.
    """
    messages: list[str] = []
    sink_id = logger.add(
        lambda message: messages.append(str(message)),
        level="DEBUG",
        format="{level}|{message}",
    )
    try:
        yield messages
    finally:
        logger.remove(sink_id)


def skip_unless_env(var_name):
    """Skip test unless environment variable is set."""
    return pytest.mark.skipif(
        not os.environ.get(var_name),
        reason=f"{var_name} not set",
    )
