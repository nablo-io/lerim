"""Behavior tests for the user-facing CLI/API surface cluster.

These cases focus on:
- CLI rendering of answer debug traces from HTTP API payloads
- CLI JSON passthrough for answer/curate payloads
- Deterministic query behavior against a real temporary context DB
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest

from lerim.context import ContextStore, resolve_project_identity
from tests.e2e.conftest import CLIRunner
from tests.e2e.cli_surface.helpers import load_cli_surface_expectation
from tests.e2e.helpers import parse_json_output
from tests.helpers import write_test_config


@pytest.fixture(autouse=True)
def mock_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use deterministic embeddings while seeding E2E query fixtures."""
    class _Provider:
        """Minimal embedding provider double for context store writes."""

        embedding_dims = 384
        model_id = "test-model"

        def embed_document(self, text: str) -> list[float]:
            """Return a deterministic document embedding."""
            return [0.1] * self.embedding_dims

        def embed_query(self, text: str) -> list[float]:
            """Return a deterministic query embedding."""
            return [0.1] * self.embedding_dims

    provider = _Provider()
    monkeypatch.setattr("lerim.context.store.get_embedding_provider", lambda: provider)
    monkeypatch.setattr("lerim.context.embedding.get_embedding_provider", lambda: provider)


class _SurfaceHTTPServer(HTTPServer):
    """Tiny test server that returns fixed JSON payloads for CLI surface tests."""

    def __init__(self, server_address: tuple[str, int]):
        super().__init__(server_address, _SurfaceHandler)
        self.responses: dict[str, dict[str, Any]] = {}
        self.requests: list[tuple[str, dict[str, Any]]] = []


class _SurfaceHandler(BaseHTTPRequestHandler):
    """Serve fixed JSON payloads and capture request bodies."""

    server: _SurfaceHTTPServer

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        body = json.loads(raw.decode("utf-8") or "{}")
        self.server.requests.append((self.path, body))
        payload = self.server.responses.get(self.path)
        if payload is None:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"not found"}')
            return

        encoded = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *args: object) -> None:
        """Silence request logs during tests."""
        return


@pytest.fixture()
def surface_project(tmp_path: Path) -> Path:
    """Create a temporary git-style project root."""
    project = tmp_path / "surface-project"
    project.mkdir(parents=True)
    (project / ".git").mkdir()
    (project / "README.md").write_text("# Surface Project\n", encoding="utf-8")
    return project


@pytest.fixture()
def surface_server() -> tuple[_SurfaceHTTPServer, int]:
    """Start a tiny HTTP JSON server for CLI answer/curate tests."""
    server = _SurfaceHTTPServer(("127.0.0.1", 0))
    port = int(server.server_address[1])
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, port
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _build_cli_runner(tmp_path: Path, *, port: int, project: Path | None = None) -> CLIRunner:
    """Create a subprocess CLI runner pinned to a temporary config."""
    sections: dict[str, dict[str, Any]] = {
        "server": {"host": "127.0.0.1", "port": port},
    }
    if project is not None:
        sections["projects"] = {project.name: str(project)}
    config_path = write_test_config(tmp_path, **sections)
    env = os.environ.copy()
    env["LERIM_CONFIG"] = str(config_path)
    env["PYTEST_CURRENT_TEST"] = "cli_surface_cases"
    return CLIRunner(env=env)


def _seed_session(
    store: ContextStore,
    *,
    project_id: str,
    session_id: str,
    repo_root: Path,
) -> None:
    """Insert a minimal session provenance row before record writes."""
    store.upsert_session(
        project_id=project_id,
        session_id=session_id,
        agent_type="surface-test",
        source_trace_ref="surface-test",
        repo_path=str(repo_root),
        cwd=str(repo_root),
        started_at=datetime.now(timezone.utc).isoformat(),
        model_name="surface-test",
        instructions_text=None,
        prompt_text=None,
        metadata={},
    )


def _seed_query_records(base_dir: Path, project: Path) -> tuple[Path, str]:
    """Seed two records into a real temp context DB for query surface tests."""
    db_path = base_dir / "context.sqlite3"
    store = ContextStore(db_path)
    store.initialize()
    identity = resolve_project_identity(project)
    store.register_project(identity)
    _seed_session(
        store,
        project_id=identity.project_id,
        session_id="surface-query-session",
        repo_root=project,
    )
    store.create_record(
        project_id=identity.project_id,
        session_id="surface-query-session",
        kind="fact",
        title="Older storage fact",
        body="Older seeded fact for query count and list tests.",
    )
    store.create_record(
        project_id=identity.project_id,
        session_id="surface-query-session",
        kind="decision",
        title="Latest storage decision",
        body="Latest seeded decision for query ordering tests.",
        decision="Use explicit storage boundaries.",
        why="Boundaries simplify lifecycle handling.",
    )
    return db_path, identity.project_id


@pytest.mark.e2e
def test_cli_answer_verbose_renders_trace_in_order(
    tmp_path: Path,
    surface_project: Path,
    surface_server: tuple[_SurfaceHTTPServer, int],
) -> None:
    """Verbose answer should render the ordered debug trace after the final answer."""
    expectation = load_cli_surface_expectation("cli_answer_verbose_renders_trace_in_order")["expected"]
    server, port = surface_server
    server.responses["/api/answer"] = {
        "answer": "There are 3 records.",
        "agent_session_id": "ses-surface-1",
        "projects_used": [],
        "error": False,
        "debug": {
            "messages": [
                {
                    "message_index": 0,
                    "kind": "baml_call",
                    "parts": [
                        {"part_kind": "PlanContextRetrieval", "content": {}},
                    ],
                },
                {
                    "message_index": 1,
                    "kind": "retrieval",
                    "parts": [
                        {
                            "part_kind": "count",
                            "content": {"action_type": "count", "result_count": 3},
                        }
                    ],
                },
                {
                    "message_index": 2,
                    "kind": "baml_call",
                    "parts": [
                        {"part_kind": "AnswerFromContext", "content": {}}
                    ],
                },
                {
                    "message_index": 3,
                    "kind": "answer",
                    "parts": [
                        {"part_kind": "text", "content": "There are 3 records."},
                    ],
                },
            ]
        },
    }
    cli = _build_cli_runner(tmp_path, port=port, project=surface_project)

    result = cli.run_ok("answer", "how many records?", "--verbose", timeout=30)

    assert server.requests[-1][0] == expectation["endpoint"]
    assert server.requests[-1][1]["verbose"] is True
    output = result.stdout
    assert expectation["answer_text"] in output
    assert expectation["trace_title"] in output
    assert output.index(expectation["answer_text"]) < output.index(expectation["trace_title"])
    assert output.index("--- Message 0 [baml_call] ---") < output.index("--- Message 1 [retrieval] ---")
    assert output.index("--- Message 1 [retrieval] ---") < output.index("--- Message 2 [baml_call] ---")
    assert output.index("--- Message 2 [baml_call] ---") < output.index("--- Message 3 [answer] ---")
    assert "  [baml] PlanContextRetrieval" in output
    assert "  [retrieval] count results=3" in output


@pytest.mark.e2e
def test_cli_answer_json_contains_debug_payload(
    tmp_path: Path,
    surface_project: Path,
    surface_server: tuple[_SurfaceHTTPServer, int],
) -> None:
    """Answer JSON output should preserve the debug payload when verbose is requested."""
    expectation = load_cli_surface_expectation("cli_answer_json_contains_debug_payload")["expected"]
    server, port = surface_server
    server.responses["/api/answer"] = {
        "answer": "Latest record is Latest storage decision.",
        "agent_session_id": "ses-surface-2",
        "projects_used": [],
        "error": False,
        "debug": {
            "messages": [
                {
                    "message_index": 0,
                    "kind": "baml_call",
                    "parts": [
                        {"part_kind": "PlanContextRetrieval", "content": {}}
                    ],
                }
            ],
            "retrieval_actions": [{"action_type": "count", "result_count": 2}],
        },
    }
    cli = _build_cli_runner(tmp_path, port=port, project=surface_project)

    result = cli.run_ok("answer", "what is the latest record?", "--json", "--verbose", timeout=30)

    payload = parse_json_output(result.stdout)
    assert server.requests[-1][1]["verbose"] is True
    assert server.requests[-1][0] == expectation["endpoint"]
    assert payload["answer"] == expectation["answer_text"]
    assert payload["debug"]["messages"][0]["parts"][0]["part_kind"] == "PlanContextRetrieval"
    assert payload["debug"]["retrieval_actions"][0]["action_type"] == "count"


@pytest.mark.e2e
def test_cli_curate_json_reports_changes(
    tmp_path: Path,
    surface_server: tuple[_SurfaceHTTPServer, int],
) -> None:
    """Curate JSON output should preserve change-report payloads from the API surface."""
    expectation = load_cli_surface_expectation("cli_curate_json_reports_changes")["expected"]
    server, port = surface_server
    server.responses["/api/curate"] = {
        "code": 0,
        "projects": {
            "surface-project": {
                "curate_counts": {
                    "created": 1,
                    "archived": 2,
                    "updated": 1,
                }
            }
        },
        "queue_health": {"degraded": False},
    }
    cli = _build_cli_runner(tmp_path, port=port)

    result = cli.run_ok("curate", "--json", timeout=30)

    payload = parse_json_output(result.stdout)
    assert server.requests[-1][0] == expectation["endpoint"]
    assert payload["projects"]["surface-project"]["curate_counts"]["created"] == int(expectation["created"])
    assert payload["projects"]["surface-project"]["curate_counts"]["updated"] == int(expectation["updated"])
    assert payload["projects"]["surface-project"]["curate_counts"]["archived"] == int(expectation["archived"])
    assert payload["queue_health"]["degraded"] is False


@pytest.mark.e2e
def test_cli_query_records_count_matches_db(
    tmp_path: Path,
    surface_project: Path,
) -> None:
    """Query count should match the seeded records in the real temp context DB."""
    expectation = load_cli_surface_expectation("cli_query_records_count_matches_db")["expected"]
    db_path, _project_id = _seed_query_records(tmp_path, surface_project)
    cli = _build_cli_runner(tmp_path, port=18765, project=surface_project)

    result = cli.run_ok(
        "query",
        "records",
        "count",
        "--scope",
        "project",
        "--project",
        surface_project.name,
        "--json",
        timeout=30,
    )

    payload = parse_json_output(result.stdout)
    assert db_path.exists()
    assert payload["count"] == int(expectation["count"])
    assert payload["entity"] == expectation["entity"]
    assert payload["scope"] == expectation["scope"]


@pytest.mark.e2e
def test_cli_query_records_latest_matches_db(
    tmp_path: Path,
    surface_project: Path,
) -> None:
    """Query list limit=1 should return the latest seeded record."""
    expectation = load_cli_surface_expectation("cli_query_records_latest_matches_db")["expected"]
    _seed_query_records(tmp_path, surface_project)
    cli = _build_cli_runner(tmp_path, port=18765, project=surface_project)

    result = cli.run_ok(
        "query",
        "records",
        "list",
        "--scope",
        "project",
        "--project",
        surface_project.name,
        "--limit",
        "1",
        "--json",
        timeout=30,
    )

    payload = parse_json_output(result.stdout)
    assert payload["count"] == int(expectation["count"])
    assert payload["rows"][0]["title"] == expectation["title"]
    assert payload["rows"][0]["kind"] == expectation["kind"]
