"""E2E test fixtures: isolated environment, server lifecycle, CLI runner.

E2E tests run CLI commands via subprocess against a real server.
Unlike integration tests which call Python APIs directly, E2E tests
validate the full user journey from CLI through HTTP to database.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Generator

import pytest

from tests.conftest import TRACE_INGESTION_TRACES_DIR
from tests.live_helpers import require_live_agent_config


def pytest_collection_modifyitems(config, items):
	"""Skip E2E tests unless LERIM_E2E is set."""
	if os.environ.get("LERIM_E2E"):
		return
	e2e_dir = os.path.dirname(__file__)
	skip = pytest.mark.skip(reason="LERIM_E2E not set")
	for item in items:
		if str(item.fspath).startswith(e2e_dir):
			item.add_marker(skip)


@pytest.fixture(scope="function")
def e2e_home(tmp_path: Path) -> Path:
	"""Create an isolated ~/.lerim directory for E2E tests."""
	lerim_home = tmp_path / ".lerim"
	lerim_home.mkdir(parents=True)
	for subdir in (
		"cache/traces",
		"index",
		"logs",
		"models/embeddings",
		"models/huggingface/hub",
		"workspace/ingest",
		"workspace/curate",
	):
		(lerim_home / subdir).mkdir(parents=True, exist_ok=True)
	(lerim_home / "platforms.json").write_text("{}\n", encoding="utf-8")
	return lerim_home


@pytest.fixture(scope="function")
def e2e_project(tmp_path: Path) -> Path:
	"""Create a temporary project directory for E2E tests."""
	project = tmp_path / "test-project"
	project.mkdir(parents=True)
	(project / ".git").mkdir()
	(project / "README.md").write_text("# Test Project\n", encoding="utf-8")
	return project


@pytest.fixture(scope="function")
def e2e_config(e2e_home: Path, e2e_project: Path) -> Path:
	"""Create a config.toml pointing at the isolated home with pre-registered project."""
	source_config = require_live_agent_config()
	config_path = e2e_home / "config.toml"
	config_lines = [
		"[data]",
		f'dir = "{e2e_home}"',
		"",
		"[server]",
		'host = "127.0.0.1"',
		"port = 18765",
		"ingest_interval_minutes = 60",
		"curate_interval_minutes = 60",
		"ingest_window_days = 7",
		"ingest_max_sessions = 50",
		"",
		"[semantic_search]",
		'embedding_model_id = "mixedbread-ai/mxbai-embed-xsmall-v1"',
		f'embedding_cache_dir = "{e2e_home}/models/embeddings"',
		"semantic_shortlist_size = 40",
		"lexical_shortlist_size = 40",
		"",
		"[roles.agent]",
			f'provider = "{source_config.agent_role.provider}"',
			f'model = "{source_config.agent_role.model}"',
			"temperature = 1.0",
			"curate_max_llm_calls = 30",
			"answer_max_retrieval_actions = 20",
		"",
		"[providers]",
		'minimax = "https://api.minimax.io/v1"',
		'zai = "https://api.z.ai/api/coding/paas/v4"',
		'openai = "https://api.openai.com/v1"',
		'openrouter = "https://openrouter.ai/api/v1"',
		"",
		"[cloud]",
		'endpoint = "https://api.lerim.dev"',
		"",
		"[agents]",
		"",
		"[projects]",
		f'{e2e_project.name} = "{e2e_project}"',
	]
	config_path.write_text("\n".join(config_lines), encoding="utf-8")
	return config_path


@pytest.fixture(scope="function")
def e2e_env(e2e_home: Path, e2e_config: Path) -> dict[str, str]:
	"""Build environment dict for E2E subprocess calls."""
	env = os.environ.copy()
	env["LERIM_CONFIG"] = str(e2e_config)
	env["PYTEST_CURRENT_TEST"] = "e2e"
	for key in list(env.keys()):
		if key.startswith("LERIM_") and key not in ("LERIM_CONFIG", "LERIM_E2E"):
			if key in ("LERIM_MLFLOW",):
				env[key] = ""
	return env


class LerimServer:
	"""Context manager for the Lerim server process."""

	def __init__(self, env: dict[str, str], port: int = 18765, timeout: int = 30):
		self.env = env
		self.port = port
		self.timeout = timeout
		self.process: subprocess.Popen | None = None

	def start(self) -> None:
		"""Start the server in a subprocess."""
		self.process = subprocess.Popen(
			[sys.executable, "-m", "lerim.server.cli", "serve", "--port", str(self.port)],
			env=self.env,
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
		)
		if not self._wait_for_ready():
			self.stop()
			raise RuntimeError(f"Server failed to start within {self.timeout}s")

	def _wait_for_ready(self) -> bool:
		"""Poll /api/health until the server responds."""
		import urllib.request
		import urllib.error

		url = f"http://127.0.0.1:{self.port}/api/health"
		deadline = time.monotonic() + self.timeout
		while time.monotonic() < deadline:
			try:
				req = urllib.request.Request(url, method="GET")
				with urllib.request.urlopen(req, timeout=2) as resp:
					if resp.status == 200:
						return True
			except (urllib.error.URLError, OSError):
				pass
			time.sleep(0.5)
		return False

	def stop(self) -> None:
		"""Stop the server gracefully."""
		if self.process is None:
			return
		try:
			self.process.send_signal(signal.SIGTERM)
			self.process.wait(timeout=5)
		except subprocess.TimeoutExpired:
			self.process.kill()
			self.process.wait()
		finally:
			self.process = None

	def __enter__(self) -> "LerimServer":
		self.start()
		return self

	def __exit__(self, *args) -> None:
		self.stop()


@pytest.fixture(scope="function")
def e2e_server(e2e_env: dict[str, str]) -> Generator[LerimServer, None, None]:
	"""Start and stop a Lerim server for the test."""
	server = LerimServer(env=e2e_env)
	server.start()
	yield server
	server.stop()


class CLIRunner:
	"""Helper for running CLI commands in E2E tests."""

	def __init__(self, env: dict[str, str]):
		self.env = env

	def run(
		self,
		*args: str,
		check: bool = False,
		timeout: int = 120,
	) -> subprocess.CompletedProcess:
		"""Run a lerim CLI command."""
		cmd = [sys.executable, "-m", "lerim.server.cli", *args]
		return subprocess.run(
			cmd,
			env=self.env,
			capture_output=True,
			text=True,
			timeout=timeout,
			check=check,
		)

	def run_ok(self, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
		"""Run a lerim CLI command and assert it succeeds."""
		result = self.run(*args, timeout=timeout)
		if result.returncode != 0:
			raise AssertionError(
				f"CLI command failed: lerim {' '.join(args)}\n"
				f"Exit code: {result.returncode}\n"
				f"Stdout: {result.stdout}\n"
				f"Stderr: {result.stderr}"
			)
		return result


@pytest.fixture(scope="function")
def cli(e2e_env: dict[str, str]) -> CLIRunner:
	"""CLI runner with isolated environment."""
	return CLIRunner(env=e2e_env)


@pytest.fixture(scope="function")
def trace_fixture_path() -> Path:
	"""Path to the extract trace fixtures directory."""
	return TRACE_INGESTION_TRACES_DIR
