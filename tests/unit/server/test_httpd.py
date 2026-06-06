"""HTTP route tests for DashboardHandler using a real test server.

Starts a lightweight HTTPServer on a random port per test module, mocking
all external dependencies (catalog, runtime, daemon) so no real LLM or
database calls occur.
"""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import replace
from http.server import HTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest

from lerim.context import resolve_project_identity
from lerim.context.store import ContextStore
from lerim.context_brief import CONTEXT_BRIEF_FILENAME, CONTEXT_BRIEF_OPERATION
from lerim.run_clinic import (
    RUN_CLINIC_FILENAME,
    RUN_CLINIC_OPERATION,
    RUN_CLINIC_REPORT_FILENAME,
)
from lerim.working_memory import WORKING_MEMORY_FILENAME, WORKING_MEMORY_OPERATION
from tests.helpers import make_config


# ── Test server helpers ──────────────────────────────────────────────


def _start_test_server(handler_class, port: int = 0):
    """Start a test HTTP server on a random port, return (server, port)."""
    server = HTTPServer(("127.0.0.1", port), handler_class)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


def _api_get(port: int, path: str) -> tuple[int, dict]:
    """Send GET request and return (status, json_body)."""
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


def _api_get_raw(port: int, path: str) -> tuple[int, bytes, str]:
    """Send GET request and return (status, body_bytes, content_type)."""
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, resp.read(), resp.headers.get("Content-Type", "")


def _api_post(port: int, path: str, body: dict | None = None) -> tuple[int, dict]:
    """Send POST request and return (status, json_body)."""
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


def _api_method(port: int, path: str, method: str) -> tuple[int, dict]:
    """Send arbitrary HTTP method and return (status, json_body)."""
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _api_get_error(port: int, path: str) -> tuple[int, dict]:
    """Send GET and expect an HTTP error; return (status, json_body)."""
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _api_post_error(port: int, path: str, body: dict | None = None) -> tuple[int, dict]:
    """Send POST and expect an HTTP error; return (status, json_body)."""
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


# ── Fixtures ─────────────────────────────────────────────────────────


def _init_test_db(db_path: Path) -> None:
    """Create minimal sessions DB schema for tests."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
			CREATE TABLE IF NOT EXISTS session_docs (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				run_id TEXT UNIQUE,
				agent_type TEXT,
				repo_name TEXT,
				repo_path TEXT,
				start_time TEXT,
				status TEXT,
				duration_ms INTEGER,
				message_count INTEGER,
				tool_call_count INTEGER,
				error_count INTEGER,
				total_tokens INTEGER,
				input_tokens INTEGER,
				output_tokens INTEGER,
				summary_text TEXT,
				session_path TEXT,
				indexed_at TEXT
			)
		""")
        conn.execute("""
			CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts
			USING fts5(
				run_id, agent_type, repo_name, summary_text,
				content='session_docs', content_rowid='id'
			)
		""")
        conn.execute("""
			CREATE TABLE IF NOT EXISTS session_jobs (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				run_id TEXT UNIQUE,
				status TEXT DEFAULT 'pending',
				project TEXT,
				attempts INTEGER DEFAULT 0,
				last_error TEXT,
				created_at TEXT,
				updated_at TEXT
			)
		""")
        conn.execute("""
			CREATE TABLE IF NOT EXISTS service_runs (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				job_type TEXT,
				status TEXT,
				started_at TEXT,
				completed_at TEXT,
				details TEXT
			)
		""")


def _seed_sessions(
    db_path: Path,
    repo_path: Path,
    count: int = 3,
    run_prefix: str = "run",
    repo_name: str = "myrepo",
) -> None:
    """Insert sample session rows into the test DB."""
    with sqlite3.connect(db_path) as conn:
        for i in range(count):
            run_id = f"{run_prefix}-{i:04d}"
            cursor = conn.execute(
                """INSERT INTO session_docs
                (run_id, agent_type, repo_name, repo_path, start_time, status,
                 duration_ms, message_count, tool_call_count, error_count,
				 total_tokens, summary_text, session_path, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    "claude",
                    repo_name,
                    str(repo_path),
                    f"2026-03-{20 + i:02d}T10:00:00Z",
                    "completed",
                    5000 + i * 100,
                    10 + i,
                    3 + i,
                    0,
                    1000 + i * 50,
                    f"Summary for run {i}",
                    "",
                    f"2026-03-{20 + i:02d}T10:00:00Z",
                ),
            )
            # Insert into FTS index
            conn.execute(
                """INSERT INTO sessions_fts(rowid, run_id, agent_type, repo_name, summary_text)
                VALUES (?, ?, ?, ?, ?)""",
                (cursor.lastrowid, run_id, "claude", repo_name, f"Summary for run {i}"),
            )


def _seed_service_runs(db_path: Path) -> None:
    """Insert sample service run rows into the test DB."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO service_runs (job_type, status, started_at, completed_at, details)
			VALUES (?, ?, ?, ?, ?)""",
            (
                "ingest",
                "completed",
                "2026-03-20T10:00:00Z",
                "2026-03-20T10:01:00Z",
                json.dumps({"indexed": 5, "sessions_processed": 3}),
            ),
        )
        conn.execute(
            """INSERT INTO service_runs (job_type, status, started_at, completed_at, details)
			VALUES (?, ?, ?, ?, ?)""",
            (
                "curate",
                "completed",
                "2026-03-20T11:00:00Z",
                "2026-03-20T11:02:00Z",
                json.dumps(
                    {
                        "curate_metrics": {
                            "counts": {"created": 1, "updated": 0, "archived": 0},
                            "records_created": 1,
                            "records_updated": 0,
                            "records_archived": 0,
                        }
                    }
                ),
            ),
        )


def _seed_jobs(db_path: Path) -> None:
    """Insert sample job queue rows into the test DB."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO session_jobs (run_id, status, project, attempts, created_at)
			VALUES (?, ?, ?, ?, ?)""",
            ("run-0000", "pending", "myproject", 0, "2026-03-20T10:00:00Z"),
        )
        conn.execute(
            """INSERT INTO session_jobs (run_id, status, project, attempts, created_at)
			VALUES (?, ?, ?, ?, ?)""",
            ("run-0001", "dead_letter", "myproject", 3, "2026-03-20T10:00:00Z"),
        )


def _seed_context_graph(db_path: Path, project_path: Path) -> None:
    """Insert sample context graph rows into the test context DB."""
    ContextStore(db_path).initialize()
    now = "2026-03-20T10:00:00Z"
    identity = resolve_project_identity(project_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO projects (project_id, project_slug, repo_path, created_at, updated_at)
			VALUES (?, ?, ?, ?, ?)""",
            (identity.project_id, "myproject", str(project_path), now, now),
        )
        conn.execute(
            """INSERT INTO scopes (
				scope_type, scope_id, scope_label, scope_slug, repo_path, created_at, updated_at
				) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                "project",
                identity.project_id,
                "myproject",
                "myproject",
                str(project_path),
                now,
                now,
            ),
        )
        for record_id, kind, title in [
            ("rec_a", "decision", "Use explicit graph endpoints"),
            ("rec_b", "constraint", "Dashboard must show learned edges"),
        ]:
            conn.execute(
                """INSERT INTO records (
					record_id, project_id, scope_type, scope_id, scope_label, source_name,
					source_profile, kind, title, body, status, created_at, updated_at, valid_from
				) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record_id,
                    identity.project_id,
                    "project",
                    identity.project_id,
                    "myproject",
                    "codex",
                    "coding",
                    kind,
                    title,
                    f"{title} body",
                    "active",
                    now,
                    now,
                    now,
                ),
            )
            conn.execute(
                """INSERT INTO context_nodes (
					node_id, project_id, scope_type, scope_id, scope_label, node_type,
					label, summary, status, semantic_cluster, created_at, updated_at
				) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record_id,
                    identity.project_id,
                    "project",
                    identity.project_id,
                    "myproject",
                    kind,
                    title,
                    f"{title} summary",
                    "active",
                    "semantic_dashboard",
                    now,
                    now,
                ),
            )
        conn.execute(
            """INSERT INTO records (
                record_id, project_id, scope_type, scope_id, scope_label, source_name,
                source_profile, kind, title, body, status, created_at, updated_at,
                valid_from, valid_until
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "rec_expired",
                identity.project_id,
                "project",
                identity.project_id,
                "myproject",
                "codex",
                "coding",
                "fact",
                "Expired graph record",
                "This active-status row is outside the current validity window.",
                "active",
                now,
                now,
                "1999-01-01T00:00:00Z",
                "2000-01-01T00:00:00Z",
            ),
        )
        conn.execute(
            """INSERT INTO context_nodes (
                node_id, project_id, scope_type, scope_id, scope_label, node_type,
                label, summary, status, semantic_cluster, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "rec_expired",
                identity.project_id,
                "project",
                identity.project_id,
                "myproject",
                "fact",
                "Expired graph record",
                "Expired graph record summary",
                "active",
                "semantic_dashboard",
                now,
                now,
            ),
        )
        conn.execute(
            """INSERT INTO context_edges (
				edge_id, project_id, scope_type, scope_id, scope_label, source_node_id,
				target_node_id, relation_kind, label, rationale, evidence_record_ids,
				confidence, status, created_at, updated_at
			) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "edge_ab",
                identity.project_id,
                "project",
                identity.project_id,
                "myproject",
                "rec_a",
                "rec_b",
                "supports",
                "Endpoint supports graph UI",
                "Without the endpoint, the dashboard cannot render learned edges.",
                json.dumps(["rec_a", "rec_b"]),
                0.9,
                "active",
                now,
                now,
            ),
        )


def _seed_single_graph_record(
    db_path: Path,
    project_path: Path,
    *,
    project_name: str,
    record_id: str,
    title: str,
) -> None:
    """Insert one graph node for a registered test project."""
    ContextStore(db_path).initialize()
    now = "2026-03-20T10:00:00Z"
    identity = resolve_project_identity(project_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO projects (project_id, project_slug, repo_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)""",
            (identity.project_id, project_name, str(project_path), now, now),
        )
        conn.execute(
            """INSERT OR IGNORE INTO scopes (
                scope_type, scope_id, scope_label, scope_slug, repo_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                "project",
                identity.project_id,
                project_name,
                project_name,
                str(project_path),
                now,
                now,
            ),
        )
        conn.execute(
            """INSERT INTO records (
                record_id, project_id, scope_type, scope_id, scope_label, source_name,
                source_profile, kind, title, body, status, created_at, updated_at, valid_from
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record_id,
                identity.project_id,
                "project",
                identity.project_id,
                project_name,
                "codex",
                "coding",
                "fact",
                title,
                f"{title} body",
                "active",
                now,
                now,
                now,
            ),
        )
        conn.execute(
            """INSERT INTO context_nodes (
                node_id, project_id, scope_type, scope_id, scope_label, node_type,
                label, summary, status, semantic_cluster, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record_id,
                identity.project_id,
                "project",
                identity.project_id,
                project_name,
                "fact",
                title,
                f"{title} summary",
                "active",
                "semantic_dashboard",
                now,
                now,
            ),
        )


def _write_dashboard_trace(path: Path) -> None:
    """Write a tiny trace with message, model, and tool metadata."""
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "role": "user",
                        "content": "Show me the run details.",
                        "timestamp": "2026-03-20T10:00:01Z",
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "model": "claude-test-model",
                            "content": [
                                {"type": "tool_use", "name": "Bash", "input": {}}
                            ],
                        },
                        "timestamp": "2026-03-20T10:00:02Z",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_workspace_artifact(
    *,
    root: Path,
    operation: str,
    run_id: str,
    project_id: str,
    filename: str,
    content: str,
    generated_at: str,
    report_filename: str | None = None,
    report: dict[str, Any] | None = None,
) -> None:
    """Write one historical generated artifact under the dated workspace layout."""
    run_dir = root / "workspace" / "2026" / "03" / "20" / operation / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / filename).write_text(content, encoding="utf-8")
    if report_filename:
        (run_dir / report_filename).write_text(
            json.dumps(report or {"status": "ok"}),
            encoding="utf-8",
        )
    manifest = {
        "run_id": run_id,
        "project_id": project_id,
        "generated_at": generated_at,
        "trigger": "test",
        "status": "succeeded",
        "run_folder": str(run_dir),
        "records_included": 1,
        "records_considered": 1,
        "recent_versions_considered": 1,
        "sessions_considered": 1,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


@pytest.fixture()
def test_server(tmp_path, monkeypatch):
    """Start a DashboardHandler server with mocked config and catalog.

    Returns (port, config, tmp_path) tuple.
    """
    config = make_config(tmp_path)
    project_path = tmp_path / "myproject"
    project_path.mkdir()
    config = replace(config, projects={"myproject": str(project_path)})
    db_path = config.sessions_db_path
    _init_test_db(db_path)
    _seed_sessions(db_path, project_path)
    child_project_path = project_path / "packages" / "worker"
    child_project_path.mkdir(parents=True)
    _seed_sessions(
        db_path,
        child_project_path,
        count=1,
        run_prefix="child",
        repo_name="worker",
    )
    other_project_path = tmp_path / "otherproject"
    other_project_path.mkdir()
    _seed_sessions(
        db_path, other_project_path, count=2, run_prefix="other", repo_name="otherrepo"
    )
    _seed_service_runs(db_path)
    _seed_jobs(db_path)
    _seed_context_graph(config.context_db_path, project_path)

    # Platforms file
    platforms_data = {
        "platforms": {
            "claude": {"path": "~/.claude/projects", "connected_at": "2026-03-20"},
        }
    }
    config.platforms_path.parent.mkdir(parents=True, exist_ok=True)
    config.platforms_path.write_text(json.dumps(platforms_data), encoding="utf-8")

    # Mock config and catalog functions at the httpd module level
    monkeypatch.setattr("lerim.server.httpd.get_config", lambda: config)
    monkeypatch.setattr("lerim.server.api.get_config", lambda: config)
    monkeypatch.setattr("lerim.server.httpd.init_sessions_db", lambda: None)
    monkeypatch.setattr("lerim.sessions.catalog.get_config", lambda: config)
    monkeypatch.setattr("lerim.sessions.catalog.init_sessions_db", lambda: None)

    # Mock api module functions used by GET handlers
    monkeypatch.setattr(
        "lerim.server.httpd.api_health",
        lambda: {"status": "ok", "version": "0.0.0-test"},
    )
    def fake_api_status(*, scope="all", project=None):
        """Return fixture status payloads for unscoped and scoped route tests."""
        if scope == "project" and project not in {None, "myproject"}:
            return {
                "timestamp": "2026-03-20T10:00:00Z",
                "error": f"Project not found: {project}",
                "projects": [],
                "scope": {"mode": "project"},
            }
        return {
            "timestamp": "2026-03-20T10:00:00Z",
            "connected_agents": ["claude"],
            "platforms": [{"name": "claude", "path": "~/.claude/projects"}],
            "record_count": 3,
            "sessions_indexed_count": 100,
            "queue": {"pending": 1, "dead_letter": 1},
            "latest_ingest": {"status": "completed"},
            "latest_curate": {"status": "completed"},
        }

    monkeypatch.setattr("lerim.server.httpd.api_status", fake_api_status)
    monkeypatch.setattr(
        "lerim.server.httpd.api_connect_list",
        lambda: [
            {"name": "claude", "path": "~/.claude/projects"},
        ],
    )

    def fake_api_project_list(*, include_paths=True):
        project = {"name": "myproject", "exists": True}
        if include_paths:
            project["path"] = str(project_path)
        return [project]

    monkeypatch.setattr("lerim.server.httpd.api_project_list", fake_api_project_list)
    monkeypatch.setattr(
        "lerim.server.httpd.api_queue_jobs",
        lambda status=None, project=None: {
            "jobs": [{"run_id": "run-0000", "status": "pending"}],
            "total": 1,
            "queue": {"pending": 1, "dead_letter": 1},
        },
    )
    monkeypatch.setattr(
        "lerim.server.httpd.api_unscoped",
        lambda limit=50: {
            "items": [{"run_id": "u-1", "agent_type": "cursor", "repo_path": None}],
            "total": 1,
            "count_by_agent": {"cursor": 1},
        },
    )

    # Catalog functions used directly by handler methods
    monkeypatch.setattr(
        "lerim.server.httpd.count_session_jobs_by_status",
        lambda: {"pending": 1, "dead_letter": 1},
    )
    monkeypatch.setattr(
        "lerim.server.httpd.latest_service_run",
        lambda job_type: {
            "status": "completed",
            "started_at": "2026-03-20T10:00:00Z",
            "completed_at": "2026-03-20T10:01:00Z",
            "details": {"sessions_processed": 3},
        },
    )
    monkeypatch.setattr(
        "lerim.server.httpd.list_session_jobs", lambda limit=50, status=None: []
    )
    monkeypatch.setattr(
        "lerim.server.httpd.list_provider_models",
        lambda provider: ["model-a", "model-b"],
    )

    # POST action mocks
    monkeypatch.setattr(
        "lerim.server.httpd.api_answer",
        lambda question, **kwargs: {
            "answer": f"Mocked answer for: {question}",
            "agent_session_id": "test-session-001",
            "projects_used": [],
            "error": False,
            "cost_usd": 0.001,
        },
    )
    monkeypatch.setattr(
        "lerim.server.httpd.api_ingest",
        lambda **kw: {
            "code": 0,
            "indexed": 5,
        },
    )
    monkeypatch.setattr(
        "lerim.server.httpd.api_curate",
        lambda **kw: {
            "code": 0,
            "curate_counts": {"created": 1},
        },
    )
    monkeypatch.setattr(
        "lerim.server.httpd.api_connect",
        lambda platform, path=None: {
            "name": platform,
            "status": "connected",
            "session_count": 7,
            "exists": True,
        },
    )
    monkeypatch.setattr(
        "lerim.server.httpd.api_project_add",
        lambda path, **kwargs: {
            "name": "added",
            "path": path,
        },
    )
    monkeypatch.setattr(
        "lerim.server.httpd.api_project_remove",
        lambda name: {
            "name": name,
            "removed": True,
        },
    )
    monkeypatch.setattr(
        "lerim.server.httpd.api_retry_job",
        lambda run_id: {
            "retried": True,
            "run_id": run_id,
            "queue": {"pending": 2},
        },
    )
    monkeypatch.setattr(
        "lerim.server.httpd.api_skip_job",
        lambda run_id: {
            "skipped": True,
            "run_id": run_id,
            "queue": {"pending": 1},
        },
    )
    monkeypatch.setattr(
        "lerim.server.httpd.api_retry_all_dead_letter",
        lambda: {
            "retried": 1,
            "queue": {"pending": 2},
        },
    )
    monkeypatch.setattr(
        "lerim.server.httpd.api_skip_all_dead_letter",
        lambda: {
            "skipped": 1,
            "queue": {"pending": 1},
        },
    )
    monkeypatch.setattr(
        "lerim.server.httpd.api_query",
        lambda **kwargs: {
            "error": False,
            "items": [],
            "total": 0,
            "query": kwargs,
        },
    )

    # Mock save_config_patch to avoid writing real config
    monkeypatch.setattr("lerim.server.httpd.save_config_patch", lambda patch: config)

    from lerim.server.httpd import DashboardHandler

    server, port = _start_test_server(DashboardHandler)
    yield port, config, tmp_path
    server.shutdown()


# ── GET route tests ──────────────────────────────────────────────────


def test_get_health(test_server):
    """GET /api/health returns 200 with status ok."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/health")
    assert status == 200
    assert body["status"] == "ok"
    assert "version" in body


def test_get_record_detail_respects_project_scope(test_server):
    """GET /api/records/<id> returns a record only inside the selected project."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/records/rec_a?project=myproject")
    assert status == 200
    assert body["record_id"] == "rec_a"
    assert body["scope_label"] == "myproject"

    status, body = _api_get_error(port, "/api/records/rec_a?project=missing")
    assert status == 400
    assert body["error"] == "Project not found: missing"


def test_get_status(test_server):
    """GET /api/status returns 200 with runtime status fields."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/status")
    assert status == 200
    assert "connected_agents" in body
    assert "record_count" in body
    assert "queue" in body


def test_get_status_rejects_unknown_project(test_server):
    """GET /api/status returns 400 for unknown project scope."""
    port, _, _ = test_server
    status, body = _api_get_error(port, "/api/status?scope=project&project=missing")
    assert status == 400
    assert body["error"] == "Project not found: missing"


def test_get_unscoped(test_server):
    """GET /api/unscoped returns unscoped session summary."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/unscoped?limit=10")
    assert status == 200
    assert "items" in body
    assert "count_by_agent" in body


def test_get_config(test_server):
    """GET /api/config returns effective config payload."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/config")
    assert status == 200
    assert "effective" in body
    # Verify effective config structure
    effective = body["effective"]
    assert "server" in effective
    assert "roles" in effective
    assert "data" not in effective


def test_get_config_models(test_server):
    """GET /api/config/models returns model list for provider."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/config/models?provider=openrouter")
    assert status == 200
    assert "models" in body
    assert isinstance(body["models"], list)


def test_get_connect(test_server):
    """GET /api/connect returns connected platforms list."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/connect")
    assert status == 200
    assert "platforms" in body
    assert isinstance(body["platforms"], list)


def test_get_project_list(test_server):
    """GET /api/project/list returns registered projects."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/project/list")
    assert status == 200
    assert "projects" in body
    assert len(body["projects"]) >= 1
    assert "path" not in body["projects"][0]


def test_get_jobs_queue(test_server):
    """GET /api/jobs/queue returns job queue status."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/jobs/queue")
    assert status == 200
    assert "jobs" in body
    assert "queue" in body


def test_get_runs(test_server):
    """GET /api/runs returns paginated run list."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/runs?scope=all")
    assert status == 200
    assert "runs" in body
    assert "pagination" in body
    assert body["pagination"]["total"] == 6


def test_get_runs_filters_registered_project(test_server):
    """GET /api/runs?project includes sessions under the registered repo path."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/runs?scope=all&project=myproject")
    assert status == 200
    assert body["pagination"]["total"] == 4
    assert {run["project"] for run in body["runs"]} == {"myproject"}


def test_get_runs_degrades_when_session_catalog_unavailable(test_server, monkeypatch):
    """GET /api/runs returns stable JSON when the session catalog is unhealthy."""
    port, _, _ = test_server

    def fail_list_sessions_window(**_kwargs):
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr("lerim.server.httpd.list_sessions_window", fail_list_sessions_window)

    status, body = _api_get(port, "/api/runs?scope=all&project=myproject")

    assert status == 200
    assert body["catalog_available"] is False
    assert "database disk image is malformed" in body["error"]
    assert body["runs"] == []
    assert body["pagination"] == {"offset": 0, "total": 0, "has_more": False}


def test_get_runs_rejects_unknown_project(test_server):
    """GET /api/runs rejects unknown project names instead of widening scope."""
    port, _, _ = test_server
    status, body = _api_get_error(port, "/api/runs?scope=all&project=missing")
    assert status == 400
    assert body["error"] == "Project not found: missing"


def test_get_runs_stats(test_server):
    """GET /api/runs/stats returns aggregate run statistics."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/runs/stats?scope=all")
    assert status == 200
    assert "totals" in body
    assert "derived" in body
    assert body["totals"]["runs"] == 6


def test_get_runs_stats_filters_registered_project(test_server):
    """GET /api/runs/stats?project computes totals for one registered project."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/runs/stats?scope=all&project=myproject")
    assert status == 200
    assert body["totals"]["runs"] == 4


def test_get_runs_stats_degrades_when_session_catalog_unavailable(
    test_server, monkeypatch
):
    """GET /api/runs/stats returns empty stats when the session catalog fails."""
    port, _, _ = test_server

    def fail_sqlite_rows(*_args, **_kwargs):
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr("lerim.server.httpd._sqlite_rows", fail_sqlite_rows)

    status, body = _api_get(port, "/api/runs/stats?scope=all&project=myproject")

    assert status == 200
    assert body["catalog_available"] is False
    assert "database disk image is malformed" in body["error"]
    assert body["totals"]["runs"] == 0


def test_get_runs_stats_reads_session_details(test_server):
    """GET /api/runs/stats reads model and tool details from session_path."""
    port, config, tmp_path = test_server
    trace_path = tmp_path / "dashboard_trace.jsonl"
    _write_dashboard_trace(trace_path)
    with sqlite3.connect(config.sessions_db_path) as conn:
        conn.execute(
            "UPDATE session_docs SET session_path = ? WHERE run_id = ?",
            (str(trace_path), "run-0000"),
        )

    status, body = _api_get(port, "/api/runs/stats?scope=all")

    assert status == 200
    assert body["model_usage"]["claude-test-model"]["total"] == 1000
    assert body["tool_usage"]["Bash"] == 1


def test_get_refine_status(test_server):
    """GET /api/refine/status returns queue and latest run info."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/refine/status")
    assert status == 200
    assert "queue" in body
    assert "ingest" in body
    assert "curate" in body


def test_get_live(test_server):
    """GET /api/live returns lightweight live-status payload."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/live")
    assert status == 200
    assert "timestamp" in body
    assert "ingest_active" in body
    assert "curate_active" in body
    assert "queue" in body


def test_get_live_degrades_when_session_catalog_unavailable(test_server, monkeypatch):
    """GET /api/live returns a visible degraded state for catalog DB errors."""
    port, _, _ = test_server

    def fail_count_session_jobs_by_status():
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(
        "lerim.server.httpd.count_session_jobs_by_status",
        fail_count_session_jobs_by_status,
    )

    status, body = _api_get(port, "/api/live")

    assert status == 200
    assert body["reachable"] is False
    assert body["catalog_available"] is False
    assert "database disk image is malformed" in body["error"]
    assert body["queue"] == {
        "pending": 0,
        "running": 0,
        "failed": 0,
        "dead_letter": 0,
        "done": 0,
    }


def test_get_memory_graph_options_route_removed(test_server):
    """GET /api/memory-graph/options now returns 404 after DB-only cleanup."""
    port, _, _ = test_server
    status, body = _api_get_error(port, "/api/memory-graph/options")
    assert status == 404
    assert "error" in body


def test_get_refine_report(test_server):
    """GET /api/refine/report returns extraction report."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/refine/report")
    assert status == 200
    assert "aggregates" in body
    assert body["aggregates"]["totals"]["sessions"] == 6


def test_get_refine_report_filters_registered_project(test_server):
    """GET /api/refine/report?project filters extraction report sessions."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/refine/report?project=myproject")
    assert status == 200
    assert body["aggregates"]["totals"]["sessions"] == 4


def test_get_refine_report_degrades_when_unavailable(test_server, monkeypatch):
    """GET /api/refine/report returns a stable empty report on catalog failure."""
    port, _, _ = test_server

    def broken_report(**_kwargs):
        """Simulate session catalog failure during report generation."""
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr("lerim.server.httpd.build_extract_report", broken_report)

    status, body = _api_get(port, "/api/refine/report?project=myproject")

    assert status == 200
    assert body["catalog_available"] is False
    assert body["aggregates"]["totals"]["sessions"] == 0
    assert "database disk image is malformed" in body["error"]


def test_get_refine_report_rejects_unknown_project(test_server):
    """GET /api/refine/report rejects unknown project names."""
    port, _, _ = test_server
    status, body = _api_get_error(port, "/api/refine/report?project=missing")
    assert status == 400
    assert body["error"] == "Project not found: missing"


def test_get_memory_artifacts_filters_history_by_project(test_server):
    """GET /api/memory-artifacts returns only selected-project artifact history."""
    port, config, tmp_path = test_server
    project_id = resolve_project_identity(tmp_path / "myproject").project_id
    other_project_id = resolve_project_identity(tmp_path / "otherproject").project_id
    _write_workspace_artifact(
        root=config.global_data_dir,
        operation=CONTEXT_BRIEF_OPERATION,
        run_id=f"{CONTEXT_BRIEF_OPERATION}-mine",
        project_id=project_id,
        filename=CONTEXT_BRIEF_FILENAME,
        content="my project context brief",
        generated_at="2026-03-20T10:00:00Z",
    )
    _write_workspace_artifact(
        root=config.global_data_dir,
        operation=CONTEXT_BRIEF_OPERATION,
        run_id=f"{CONTEXT_BRIEF_OPERATION}-other",
        project_id=other_project_id,
        filename=CONTEXT_BRIEF_FILENAME,
        content="other project context brief",
        generated_at="2026-03-20T11:00:00Z",
    )
    _write_workspace_artifact(
        root=config.global_data_dir,
        operation=WORKING_MEMORY_OPERATION,
        run_id=f"{WORKING_MEMORY_OPERATION}-mine",
        project_id=project_id,
        filename=WORKING_MEMORY_FILENAME,
        content="my project working memory",
        generated_at="2026-03-20T12:00:00Z",
    )
    _write_workspace_artifact(
        root=config.global_data_dir,
        operation=WORKING_MEMORY_OPERATION,
        run_id=f"{WORKING_MEMORY_OPERATION}-other",
        project_id=other_project_id,
        filename=WORKING_MEMORY_FILENAME,
        content="other project working memory",
        generated_at="2026-03-20T13:00:00Z",
    )

    status, body = _api_get(port, "/api/memory-artifacts?project=myproject")

    assert status == 200
    brief_versions = body["artifacts"]["context_brief"]["versions"]
    memory_versions = body["artifacts"]["working_memory"]["versions"]
    assert [version["content"] for version in brief_versions] == [
        "my project context brief"
    ]
    assert [version["content"] for version in memory_versions] == [
        "my project working memory"
    ]


def test_get_memory_artifacts_rejects_unknown_project(test_server):
    """GET /api/memory-artifacts rejects unknown project names."""
    port, _, _ = test_server
    status, body = _api_get_error(port, "/api/memory-artifacts?project=missing")
    assert status == 400
    assert body["error"] == "Project not found: missing"


def test_get_clinic_filters_history_by_project(test_server):
    """GET /api/clinic returns only selected-project Run Clinic history."""
    port, config, tmp_path = test_server
    project_id = resolve_project_identity(tmp_path / "myproject").project_id
    other_project_id = resolve_project_identity(tmp_path / "otherproject").project_id
    _write_workspace_artifact(
        root=config.global_data_dir,
        operation=RUN_CLINIC_OPERATION,
        run_id=f"{RUN_CLINIC_OPERATION}-mine",
        project_id=project_id,
        filename=RUN_CLINIC_FILENAME,
        content="my project run clinic",
        generated_at="2026-03-20T10:00:00Z",
        report_filename=RUN_CLINIC_REPORT_FILENAME,
        report={"project": "myproject"},
    )
    _write_workspace_artifact(
        root=config.global_data_dir,
        operation=RUN_CLINIC_OPERATION,
        run_id=f"{RUN_CLINIC_OPERATION}-other",
        project_id=other_project_id,
        filename=RUN_CLINIC_FILENAME,
        content="other project run clinic",
        generated_at="2026-03-20T11:00:00Z",
        report_filename=RUN_CLINIC_REPORT_FILENAME,
        report={"project": "otherproject"},
    )

    status, body = _api_get(port, "/api/clinic?project=myproject")

    assert status == 200
    assert [version["content"] for version in body["versions"]] == [
        "my project run clinic"
    ]
    assert [version["report"]["project"] for version in body["versions"]] == [
        "myproject"
    ]
    assert body["active_record_count"] == 2
    assert body["total_record_count"] == 3


def test_get_clinic_rejects_unknown_project(test_server):
    """GET /api/clinic rejects unknown project names."""
    port, _, _ = test_server
    status, body = _api_get_error(port, "/api/clinic?project=missing")
    assert status == 400
    assert body["error"] == "Project not found: missing"


def test_get_record_filters_route_scopes_project(test_server):
    """GET /api/records/filters accepts the dashboard project query."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/records/filters?project=myproject")

    assert status == 200
    assert body["types"] == ["constraint", "decision"]
    assert body["projects"] == ["myproject"]


def test_get_search_without_query(test_server):
    """GET /api/search (no FTS query) returns keyword-mode results."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/search?scope=all")
    assert status == 200
    assert "mode" in body
    assert body["mode"] == "keyword"
    assert "results" in body
    assert "pagination" in body


def test_get_search_filters_registered_project(test_server):
    """GET /api/search?project includes keyword results under one registered project."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/search?scope=all&project=myproject")
    assert status == 200
    assert body["mode"] == "keyword"
    assert body["pagination"]["total"] == 4
    assert {result["project"] for result in body["results"]} == {"myproject"}


def test_get_unknown_api_route(test_server):
    """GET /api/nonexistent returns 404."""
    port, _, _ = test_server
    status, body = _api_get_error(port, "/api/nonexistent")
    assert status == 404
    assert "error" in body


def test_get_root_serves_html(test_server):
    """GET / returns HTML content (cloud stub when no dashboard assets)."""
    port, _, _ = test_server
    status, body_bytes, content_type = _api_get_raw(port, "/")
    assert status == 200
    assert "text/html" in content_type
    assert b"Lerim" in body_bytes


def test_get_session_redirect(test_server):
    """GET /session/<id> redirects to /?tab=runs."""
    port, _, _ = test_server
    url = f"http://127.0.0.1:{port}/session/test-123"
    urllib.request.Request(url)
    # Disable redirect following to check the 302
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", "/session/test-123")
    resp = conn.getresponse()
    assert resp.status == 302
    assert "tab=runs" in (resp.getheader("Location") or "")
    conn.close()


# ── POST route tests ─────────────────────────────────────────────────


def test_post_answer(test_server):
    """POST /api/answer returns mocked answer."""
    port, _, _ = test_server
    status, body = _api_post(port, "/api/answer", {"question": "What is Lerim?"})
    assert status == 200
    assert "answer" in body
    assert "Mocked answer" in body["answer"]


def test_post_answer_reports_internal_failure(test_server, monkeypatch):
    """POST /api/answer reports answerer exceptions instead of timing out."""
    port, _, _ = test_server

    def fail_answer(question, **kwargs):
        """Simulate a provider or database failure from the answerer."""
        raise RuntimeError("provider request failed")

    monkeypatch.setattr("lerim.server.httpd.api_answer", fail_answer)

    status, body = _api_post_error(
        port,
        "/api/answer",
        {"question": "What is Lerim?"},
    )

    assert status == 500
    assert body["error"] == "Answer failed: RuntimeError: provider request failed"


def test_post_answer_times_out_after_configured_deadline(test_server, monkeypatch):
    """POST /api/answer uses the configured answer timeout deadline."""
    port, _, _ = test_server
    monkeypatch.setattr("lerim.server.httpd.ANSWER_REQUEST_TIMEOUT_SECONDS", 0.01)

    def slow_answer(question, **kwargs):
        """Simulate an answerer call that exceeds the endpoint deadline."""
        time.sleep(0.2)
        return {"answer": "too late", "error": False}

    monkeypatch.setattr("lerim.server.httpd.api_answer", slow_answer)

    status, body = _api_post_error(
        port,
        "/api/answer",
        {"question": "What is Lerim?"},
    )

    assert status == 504
    assert body["error"] == "Answer timed out after 0.01 seconds"


def test_post_answer_rejects_unknown_field(test_server):
    """POST /api/answer rejects unsupported request fields."""
    port, _, _ = test_server
    status, body = _api_post_error(
        port,
        "/api/answer",
        {"question": "What is Lerim?", "limit": 5},
    )
    assert status == 400
    assert "error" in body
    assert "Unsupported field" in body["error"]


def test_post_answer_missing_question(test_server):
    """POST /api/answer without question returns 400."""
    port, _, _ = test_server
    status, body = _api_post_error(port, "/api/answer", {"question": ""})
    assert status == 400
    assert "error" in body


def test_post_ingest(test_server):
    """POST /api/ingest starts a ingest job and returns started status."""
    port, _, _ = test_server
    status, body = _api_post(port, "/api/ingest", {"window": "7d"})
    assert status == 200
    assert body["status"] == "started"
    assert "job_id" in body
    assert body["mode"] == "async"


def test_post_ingest_blocking(test_server):
    """POST /api/ingest with blocking=true returns the completed ingest payload."""
    port, _, _ = test_server
    status, body = _api_post(port, "/api/ingest", {"window": "7d", "blocking": True})
    assert status == 200
    assert body["code"] == 0
    assert body["indexed"] == 5
    assert "job_id" not in body


def test_post_ingest_rejects_ignore_lock(test_server):
    """POST /api/ingest rejects unsupported ignore_lock payloads."""
    port, _, _ = test_server
    status, body = _api_post_error(port, "/api/ingest", {"ignore_lock": True})
    assert status == 400
    assert "ignore_lock" in body["error"]


def test_post_curate(test_server):
    """POST /api/curate starts a curate job and returns started status."""
    port, _, _ = test_server
    status, body = _api_post(port, "/api/curate", {})
    assert status == 200
    assert body["status"] == "started"
    assert "job_id" in body
    assert body["mode"] == "async"


def test_post_curate_blocking(test_server):
    """POST /api/curate with blocking=true returns the completed payload."""
    port, _, _ = test_server
    status, body = _api_post(port, "/api/curate", {"blocking": True})
    assert status == 200
    assert body["code"] == 0
    assert body["curate_counts"]["created"] == 1
    assert "job_id" not in body


def test_post_curate_rejects_force(test_server):
    """POST /api/curate rejects unsupported force rather than ignoring it."""
    port, _, _ = test_server
    status, body = _api_post_error(port, "/api/curate", {"force": True})
    assert status == 400
    assert "force" in body["error"]


def test_post_connect(test_server):
    """POST /api/connect with platform returns connection result."""
    port, _, _ = test_server
    status, body = _api_post(port, "/api/connect", {"platform": "claude"})
    assert status == 200
    assert body["name"] == "claude"
    assert body["status"] == "connected"
    assert body["session_count"] == 7
    assert body["exists"] is True
    assert "path" not in body


def test_post_connect_missing_platform(test_server):
    """POST /api/connect without platform returns 400."""
    port, _, _ = test_server
    status, body = _api_post_error(port, "/api/connect", {})
    assert status == 400
    assert "error" in body


def test_post_project_add(test_server):
    """POST /api/project/add with path returns added project."""
    port, _, _ = test_server
    status, body = _api_post(port, "/api/project/add", {"path": "/tmp/testproj"})
    assert status == 200
    assert body["name"] == "added"


def test_post_project_add_missing_path(test_server):
    """POST /api/project/add without path returns 400."""
    port, _, _ = test_server
    status, body = _api_post_error(port, "/api/project/add", {})
    assert status == 400
    assert "error" in body


def test_post_project_remove(test_server):
    """POST /api/project/remove with name returns removal result."""
    port, _, _ = test_server
    status, body = _api_post(port, "/api/project/remove", {"name": "myproject"})
    assert status == 200
    assert body["removed"] is True


def test_post_project_remove_missing_name(test_server):
    """POST /api/project/remove without name returns 400."""
    port, _, _ = test_server
    status, body = _api_post_error(port, "/api/project/remove", {})
    assert status == 400
    assert "error" in body


def test_post_jobs_retry_all(test_server):
    """POST /api/jobs/retry-all retries dead_letter jobs."""
    port, _, _ = test_server
    status, body = _api_post(port, "/api/jobs/retry-all", {})
    assert status == 200
    assert "retried" in body


def test_post_jobs_skip_all(test_server):
    """POST /api/jobs/skip-all skips dead_letter jobs."""
    port, _, _ = test_server
    status, body = _api_post(port, "/api/jobs/skip-all", {})
    assert status == 200
    assert "skipped" in body


def test_post_graph_query_returns_learned_edges(test_server):
    """POST /api/graph/query returns persisted context graph edges."""
    port, _, _ = test_server
    status, body = _api_post(
        port,
        "/api/graph/query",
        {
            "max_nodes": 20,
            "max_edges": 20,
            "connected_only": True,
        },
    )
    assert status == 200
    assert body["graph_mode"] == "learned_graph"
    assert body["used_record_fallback"] is False
    assert body["total_records"] == 2
    assert body["graph_node_count"] == 2
    assert body["returned_nodes"] == 2
    assert body["returned_edges"] == 1
    assert body["active_edge_count"] == 1
    assert body["edges"][0]["source"] == "rec_a"
    assert body["edges"][0]["target"] == "rec_b"
    assert body["edges"][0]["kind"] == "supports"
    assert body["edges"][0]["evidence_record_ids"] == ["rec_a", "rec_b"]


def test_post_graph_query_all_scope_excludes_unregistered_projects(test_server):
    """POST /api/graph/query without project stays inside registered projects."""
    port, config, tmp_path = test_server
    unregistered_path = tmp_path / "unregistered"
    unregistered_path.mkdir()
    _seed_single_graph_record(
        config.context_db_path,
        unregistered_path,
        project_name="unregistered",
        record_id="rec_unregistered",
        title="Unregistered graph record",
    )

    status, body = _api_post(
        port,
        "/api/graph/query",
        {"max_nodes": 20, "max_edges": 20},
    )

    assert status == 200
    assert {node["id"] for node in body["nodes"]} == {"rec_a", "rec_b"}
    assert {node["project"] for node in body["nodes"]} == {"myproject"}


def test_post_graph_query_filters_registered_project(test_server):
    """POST /api/graph/query filters learned graph rows by project id."""
    port, _, tmp_path = test_server
    status, body = _api_post(
        port,
        "/api/graph/query",
        {
            "project": str(tmp_path / "myproject" / "packages" / "worker"),
            "max_nodes": 20,
            "max_edges": 20,
            "connected_only": True,
        },
    )
    assert status == 200
    assert body["selected_project"] == "myproject"
    assert body["total_records"] == 2
    assert body["graph_node_count"] == 2
    assert body["returned_nodes"] == 2
    assert body["returned_edges"] == 1
    assert {node["project"] for node in body["nodes"]} == {"myproject"}


def test_post_graph_query_switches_between_registered_projects(test_server):
    """POST /api/graph/query returns different nodes for different projects."""
    port, config, tmp_path = test_server
    beta_path = tmp_path / "betaproject"
    beta_path.mkdir()
    config.projects["betaproject"] = str(beta_path)
    _seed_single_graph_record(
        config.context_db_path,
        beta_path,
        project_name="betaproject",
        record_id="rec_beta",
        title="Beta-only graph record",
    )

    alpha_status, alpha = _api_post(
        port,
        "/api/graph/query",
        {"project": "myproject", "max_nodes": 20, "max_edges": 20},
    )
    beta_status, beta = _api_post(
        port,
        "/api/graph/query",
        {"project": "betaproject", "max_nodes": 20, "max_edges": 20},
    )

    assert alpha_status == 200
    assert beta_status == 200
    assert alpha["selected_project"] == "myproject"
    assert beta["selected_project"] == "betaproject"
    assert {node["id"] for node in alpha["nodes"]} == {"rec_a", "rec_b"}
    assert {node["id"] for node in beta["nodes"]} == {"rec_beta"}
    assert {node["project"] for node in beta["nodes"]} == {"betaproject"}


def test_post_graph_query_rejects_unknown_project(test_server):
    """POST /api/graph/query rejects unknown project names."""
    port, _, _ = test_server
    status, body = _api_post_error(port, "/api/graph/query", {"project": "missing"})
    assert status == 400
    assert body["error"] == "Project not found: missing"


def test_post_job_retry(test_server):
    """POST /api/jobs/<run_id>/retry retries a specific job."""
    port, _, _ = test_server
    status, body = _api_post(port, "/api/jobs/run-0001/retry", {})
    assert status == 200
    assert body["retried"] is True
    assert body["run_id"] == "run-0001"


def test_post_job_skip(test_server):
    """POST /api/jobs/<run_id>/skip skips a specific job."""
    port, _, _ = test_server
    status, body = _api_post(port, "/api/jobs/run-0001/skip", {})
    assert status == 200
    assert body["skipped"] is True
    assert body["run_id"] == "run-0001"


def test_post_memory_graph_query_route_removed(test_server):
    """POST /api/memory-graph/query now returns 404 after DB-only cleanup."""
    port, _, _ = test_server
    status, body = _api_post_error(
        port,
        "/api/memory-graph/query",
        {
            "query": "",
            "filters": {},
        },
    )
    assert status == 404
    assert "error" in body


def test_post_memory_graph_expand_route_removed(test_server):
    """POST /api/memory-graph/expand now returns 404 after DB-only cleanup."""
    port, _, _ = test_server
    status, body = _api_post_error(
        port,
        "/api/memory-graph/expand",
        {
            "node_id": "mem:auth_decision",
        },
    )
    assert status == 404
    assert "error" in body


def test_post_query_invalid_limit_returns_400(test_server):
    """POST /api/query rejects non-integer limit with a JSON 400."""
    port, _, _ = test_server
    status, body = _api_post_error(
        port,
        "/api/query",
        {
            "entity": "records",
            "mode": "list",
            "limit": "not-a-number",
        },
    )
    assert status == 400
    assert body["error"] == "limit and offset must be integers"


def test_post_query_invalid_offset_returns_400(test_server):
    """POST /api/query rejects non-integer offset with a JSON 400."""
    port, _, _ = test_server
    status, body = _api_post_error(
        port,
        "/api/query",
        {
            "entity": "records",
            "mode": "list",
            "offset": "not-a-number",
        },
    )
    assert status == 400
    assert body["error"] == "limit and offset must be integers"


def test_post_config_save(test_server):
    """POST /api/config with patch saves config."""
    port, _, _ = test_server
    status, body = _api_post(
        port,
        "/api/config",
        {
            "patch": {"server": {"port": 9999}},
        },
    )
    assert status == 200
    assert "effective" in body


def test_post_config_save_missing_patch(test_server):
    """POST /api/config without patch returns 400."""
    port, _, _ = test_server
    status, body = _api_post_error(port, "/api/config", {})
    assert status == 400
    assert "error" in body


def test_post_unknown_route(test_server):
    """POST /api/nonexistent returns 404."""
    port, _, _ = test_server
    status, body = _api_post_error(port, "/api/nonexistent", {})
    assert status == 404
    assert "error" in body


# ── Read-only enforcement tests ──────────────────────────────────────


def test_post_refine_run_read_only(test_server):
    """POST /api/refine/run is blocked with read-only message."""
    port, _, _ = test_server
    status, body = _api_post_error(port, "/api/refine/run", {})
    assert status == 403
    assert "read-only" in body["error"].lower() or "Read-only" in body["error"]


def test_post_reflect_read_only(test_server):
    """POST /api/reflect is blocked with read-only message."""
    port, _, _ = test_server
    status, body = _api_post_error(port, "/api/reflect", {})
    assert status == 403


def test_put_rejected(test_server):
    """PUT requests are rejected with 403."""
    port, _, _ = test_server
    status, body = _api_method(port, "/api/anything", "PUT")
    assert status == 403
    assert "error" in body


def test_delete_rejected(test_server):
    """DELETE requests are rejected with 403."""
    port, _, _ = test_server
    status, body = _api_method(port, "/api/anything", "DELETE")
    assert status == 403
    assert "error" in body


def test_patch_config_allowed(test_server):
    """PATCH /api/config is allowed (config save)."""
    port, _, _ = test_server
    url = f"http://127.0.0.1:{port}/api/config"
    data = json.dumps({"patch": {"server": {"port": 9999}}}).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method="PATCH",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200


def test_patch_other_rejected(test_server):
    """PATCH to non-config routes is rejected with 403."""
    port, _, _ = test_server
    status, body = _api_method(port, "/api/anything", "PATCH")
    assert status == 403


# ── Edge cases ───────────────────────────────────────────────────────


def test_run_messages_not_found(test_server):
    """GET /api/runs/<id>/messages for nonexistent run returns 404."""
    port, _, _ = test_server
    status, body = _api_get_error(port, "/api/runs/nonexistent/messages")
    assert status == 404
    assert "error" in body


def test_run_detail_reads_exact_run(test_server):
    """GET /api/runs/<id> returns an exact indexed run."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/runs/child-0000?project=myproject")
    assert status == 200
    assert body["run_id"] == "child-0000"
    assert body["project"] == "myproject"


def test_run_detail_rejects_sibling_project_scope(test_server):
    """GET /api/runs/<id>?project rejects runs outside the selected project."""
    port, _, _ = test_server
    status, body = _api_get_error(port, "/api/runs/other-0000?project=myproject")
    assert status == 404
    assert "error" in body


def test_run_detail_not_found(test_server):
    """GET /api/runs/<id> returns 404 for nonexistent runs."""
    port, _, _ = test_server
    status, body = _api_get_error(port, "/api/runs/nonexistent")
    assert status == 404
    assert "error" in body


def test_run_messages_reads_session_path(test_server, monkeypatch):
    """GET /api/runs/<id>/messages loads normalized messages from session_path."""
    port, _, tmp_path = test_server
    trace_path = tmp_path / "dashboard_messages.jsonl"
    _write_dashboard_trace(trace_path)
    monkeypatch.setattr(
        "lerim.server.httpd.fetch_session_doc",
        lambda run_id: {"run_id": run_id, "session_path": str(trace_path)},
    )

    status, body = _api_get(port, "/api/runs/run-0000/messages")

    assert status == 200
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][0]["content"] == "Show me the run details."


def test_run_messages_rejects_sibling_project_scope(test_server):
    """GET /api/runs/<id>/messages?project rejects sibling project runs."""
    port, _, _ = test_server
    status, body = _api_get_error(
        port,
        "/api/runs/other-0000/messages?project=myproject",
    )
    assert status == 404
    assert "error" in body


def test_search_with_fts_query(test_server):
    """GET /api/search?query=Summary performs FTS search."""
    port, config, tmp_path = test_server
    # Override the search handler to use the real DB with seeded data
    # The search handler queries config.sessions_db_path directly
    status, body = _api_get(port, "/api/search?query=Summary&scope=all")
    assert status == 200
    assert body["mode"] == "fts"
    assert "results" in body


def test_post_empty_body(test_server):
    """POST /api/answer with empty body returns 400."""
    port, _, _ = test_server
    url = f"http://127.0.0.1:{port}/api/answer"
    req = urllib.request.Request(
        url,
        data=b"",
        method="POST",
        headers={"Content-Type": "application/json", "Content-Length": "0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = resp.status
            json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        status = exc.code
        json.loads(exc.read())
    assert status == 400


def test_post_invalid_json(test_server):
    """POST /api/answer with invalid JSON returns 400."""
    port, _, _ = test_server
    url = f"http://127.0.0.1:{port}/api/answer"
    req = urllib.request.Request(
        url,
        data=b"not json at all",
        method="POST",
        headers={"Content-Type": "application/json", "Content-Length": "15"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = resp.status
            json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        status = exc.code
        json.loads(exc.read())
    assert status == 400


def test_get_jobs_queue_with_filters(test_server):
    """GET /api/jobs/queue?status=pending filters by status."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/jobs/queue?status=pending")
    assert status == 200
    assert "jobs" in body


def test_get_runs_with_pagination(test_server):
    """GET /api/runs with limit and offset returns paginated results."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/runs?scope=all&limit=1&offset=0")
    assert status == 200
    assert "pagination" in body


def test_get_search_with_filters(test_server):
    """GET /api/search with status and repo filters."""
    port, _, _ = test_server
    status, body = _api_get(port, "/api/search?scope=all&status=completed&repo=myrepo")
    assert status == 200
    assert "results" in body


# ── Helper function unit tests ───────────────────────────────────────


def test_serialize_run_extracts_project_from_repo_path():
    """_serialize_run extracts project name from repo_path."""
    from lerim.server.httpd import _serialize_run

    row = {
        "run_id": "run-001",
        "agent_type": "claude",
        "status": "completed",
        "start_time": "2026-03-20T10:00:00Z",
        "duration_ms": 5000,
        "message_count": 10,
        "tool_call_count": 3,
        "error_count": 0,
        "total_tokens": 1000,
        "repo_name": "",
        "repo_path": "/home/user/projects/myapp",
        "summary_text": "Test summary",
        "session_path": "",
    }
    result = _serialize_run(row)
    assert result["project"] == "myapp"
    assert result["run_id"] == "run-001"


def test_serialize_run_extracts_project_from_claude_path():
    """_serialize_run extracts project from Claude session path."""
    from lerim.server.httpd import _serialize_run

    row = {
        "run_id": "run-002",
        "agent_type": "claude",
        "status": "completed",
        "start_time": "2026-03-20T10:00:00Z",
        "duration_ms": 5000,
        "message_count": 10,
        "tool_call_count": 3,
        "error_count": 0,
        "total_tokens": 1000,
        "repo_name": "",
        "repo_path": "",
        "summary_text": "",
        "session_path": "~/.claude/projects/-Users-test-myapp/abc123.jsonl",
    }
    result = _serialize_run(row)
    assert result["project"] == "myapp"


def test_iso_now_returns_string():
    """_iso_now returns a non-empty ISO timestamp string."""
    from lerim.server.httpd import _iso_now

    result = _iso_now()
    assert isinstance(result, str)
    assert "T" in result


def test_query_param_default():
    """_query_param returns default when key is missing."""
    from lerim.server.httpd import _query_param

    assert _query_param({}, "missing", "fallback") == "fallback"


def test_query_param_extracts_first():
    """_query_param returns first value from list."""
    from lerim.server.httpd import _query_param

    assert _query_param({"key": ["first", "second"]}, "key", "") == "first"


def test_serialize_full_config(tmp_path):
    """_serialize_full_config produces expected nested structure."""
    from lerim.server.httpd import _serialize_full_config

    config = make_config(tmp_path)
    result = _serialize_full_config(config)
    assert "server" in result
    assert "roles" in result
    assert "embedding" in result
    assert "mlflow_enabled" in result
    assert "data" not in result
    assert "global_data_dir" not in result
    assert result["server"]["port"] == 8765
    assert "agent" in result["roles"]


def test_load_messages_for_run_empty_path():
    """_load_messages_for_run returns [] when session_path is empty."""
    from lerim.server.httpd import _load_messages_for_run

    result = _load_messages_for_run({"session_path": ""})
    assert result == []
