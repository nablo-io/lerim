"""HTTP route tests for DashboardHandler using a real test server.

Starts a lightweight HTTPServer on a random port per test module, mocking
all external dependencies (catalog, runtime, daemon) so no real LLM or
database calls occur.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path
from threading import Thread

import pytest

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
		url, data=data, method="POST",
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
		url, data=data, method="POST",
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


def _seed_sessions(db_path: Path, count: int = 3) -> None:
	"""Insert sample session rows into the test DB."""
	with sqlite3.connect(db_path) as conn:
		for i in range(count):
			run_id = f"run-{i:04d}"
			conn.execute(
				"""INSERT INTO session_docs
				(run_id, agent_type, repo_name, start_time, status,
				 duration_ms, message_count, tool_call_count, error_count,
				 total_tokens, summary_text, session_path, indexed_at)
				VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
				(
					run_id, "claude", "myrepo",
					f"2026-03-{20+i:02d}T10:00:00Z", "completed",
					5000 + i * 100, 10 + i, 3 + i, 0,
					1000 + i * 50, f"Summary for run {i}", "",
					f"2026-03-{20+i:02d}T10:00:00Z",
				),
			)
			# Insert into FTS index
			conn.execute(
				"""INSERT INTO sessions_fts(rowid, run_id, agent_type, repo_name, summary_text)
				VALUES (?, ?, ?, ?, ?)""",
				(i + 1, run_id, "claude", "myrepo", f"Summary for run {i}"),
			)


def _seed_service_runs(db_path: Path) -> None:
	"""Insert sample service run rows into the test DB."""
	with sqlite3.connect(db_path) as conn:
		conn.execute(
			"""INSERT INTO service_runs (job_type, status, started_at, completed_at, details)
			VALUES (?, ?, ?, ?, ?)""",
			("sync", "completed", "2026-03-20T10:00:00Z", "2026-03-20T10:01:00Z",
			 json.dumps({"indexed": 5, "sessions_processed": 3})),
		)
		conn.execute(
			"""INSERT INTO service_runs (job_type, status, started_at, completed_at, details)
			VALUES (?, ?, ?, ?, ?)""",
			("maintain", "completed", "2026-03-20T11:00:00Z", "2026-03-20T11:02:00Z",
			 json.dumps({"maintain_metrics": {"counts": {"merged": 1}}})),
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


@pytest.fixture()
def test_server(tmp_path, monkeypatch):
	"""Start a DashboardHandler server with mocked config and catalog.

	Returns (port, config, tmp_path) tuple.
	"""
	config = make_config(tmp_path)
	db_path = config.sessions_db_path
	_init_test_db(db_path)
	_seed_sessions(db_path)
	_seed_service_runs(db_path)
	_seed_jobs(db_path)

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
	monkeypatch.setattr("lerim.server.httpd.get_config_sources", lambda: [
		{"source": "test", "path": str(tmp_path / "config.toml")},
	])
	monkeypatch.setattr("lerim.server.httpd.get_user_config_path", lambda: tmp_path / "config.toml")
	monkeypatch.setattr("lerim.server.httpd.init_sessions_db", lambda: None)

	# Mock api module functions used by GET handlers
	monkeypatch.setattr("lerim.server.httpd.api_health", lambda: {"status": "ok", "version": "0.0.0-test"})
	monkeypatch.setattr("lerim.server.httpd.api_status", lambda: {
		"timestamp": "2026-03-20T10:00:00Z",
		"connected_agents": ["claude"],
		"platforms": [{"name": "claude", "path": "~/.claude/projects"}],
		"record_count": 3,
		"sessions_indexed_count": 100,
		"queue": {"pending": 1, "dead_letter": 1},
		"latest_sync": {"status": "completed"},
		"latest_maintain": {"status": "completed"},
	})
	monkeypatch.setattr("lerim.server.httpd.api_connect_list", lambda: [
		{"name": "claude", "path": "~/.claude/projects"},
	])
	monkeypatch.setattr("lerim.server.httpd.api_project_list", lambda: [
		{"name": "myproject", "path": "/tmp/myproject", "exists": True},
	])
	monkeypatch.setattr("lerim.server.httpd.api_queue_jobs", lambda status=None, project=None: {
		"jobs": [{"run_id": "run-0000", "status": "pending"}],
		"total": 1,
		"queue": {"pending": 1, "dead_letter": 1},
	})
	monkeypatch.setattr("lerim.server.httpd.api_unscoped", lambda limit=50: {
		"items": [{"run_id": "u-1", "agent_type": "cursor", "repo_path": None}],
		"total": 1,
		"count_by_agent": {"cursor": 1},
	})

	# Catalog functions used directly by handler methods
	monkeypatch.setattr("lerim.server.httpd.count_session_jobs_by_status", lambda: {"pending": 1, "dead_letter": 1})
	monkeypatch.setattr("lerim.server.httpd.latest_service_run", lambda job_type: {
		"status": "completed",
		"started_at": "2026-03-20T10:00:00Z",
		"completed_at": "2026-03-20T10:01:00Z",
		"details": {"sessions_processed": 3},
	})
	monkeypatch.setattr("lerim.server.httpd.list_session_jobs", lambda limit=50, status=None: [])
	monkeypatch.setattr("lerim.server.httpd.list_sessions_window", lambda **kw: ([], 0))
	monkeypatch.setattr("lerim.server.httpd.fetch_session_doc", lambda run_id: None)
	monkeypatch.setattr("lerim.server.httpd.list_provider_models", lambda provider: ["model-a", "model-b"])

	# POST action mocks
	monkeypatch.setattr("lerim.server.httpd.api_ask", lambda question, **kwargs: {
		"answer": f"Mocked answer for: {question}",
		"agent_session_id": "test-session-001",
		"projects_used": [],
		"error": False,
		"cost_usd": 0.001,
	})
	monkeypatch.setattr("lerim.server.httpd.api_sync", lambda **kw: {
		"code": 0, "indexed": 5,
	})
	monkeypatch.setattr("lerim.server.httpd.api_maintain", lambda **kw: {
		"code": 0, "maintain_counts": {"merged": 1},
	})
	monkeypatch.setattr("lerim.server.httpd.api_connect", lambda platform, path=None: {
		"name": platform, "connected": True,
	})
	monkeypatch.setattr("lerim.server.httpd.api_project_add", lambda path: {
		"name": "added", "path": path,
	})
	monkeypatch.setattr("lerim.server.httpd.api_project_remove", lambda name: {
		"name": name, "removed": True,
	})
	monkeypatch.setattr("lerim.server.httpd.api_retry_job", lambda run_id: {
		"retried": True, "run_id": run_id, "queue": {"pending": 2},
	})
	monkeypatch.setattr("lerim.server.httpd.api_skip_job", lambda run_id: {
		"skipped": True, "run_id": run_id, "queue": {"pending": 1},
	})
	monkeypatch.setattr("lerim.server.httpd.api_retry_all_dead_letter", lambda: {
		"retried": 1, "queue": {"pending": 2},
	})
	monkeypatch.setattr("lerim.server.httpd.api_skip_all_dead_letter", lambda: {
		"skipped": 1, "queue": {"pending": 1},
	})

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


def test_get_status(test_server):
	"""GET /api/status returns 200 with runtime status fields."""
	port, _, _ = test_server
	status, body = _api_get(port, "/api/status")
	assert status == 200
	assert "connected_agents" in body
	assert "record_count" in body
	assert "queue" in body


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
	assert "sources" in body
	assert "user_config_path" in body
	# Verify effective config structure
	effective = body["effective"]
	assert "server" in effective
	assert "roles" in effective
	assert "data" in effective


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


def test_get_runs_stats(test_server):
	"""GET /api/runs/stats returns aggregate run statistics."""
	port, _, _ = test_server
	status, body = _api_get(port, "/api/runs/stats?scope=all")
	assert status == 200
	assert "totals" in body
	assert "derived" in body


def test_get_refine_status(test_server):
	"""GET /api/refine/status returns queue and latest run info."""
	port, _, _ = test_server
	status, body = _api_get(port, "/api/refine/status")
	assert status == 200
	assert "queue" in body
	assert "sync" in body
	assert "maintain" in body


def test_get_live(test_server):
	"""GET /api/live returns lightweight live-status payload."""
	port, _, _ = test_server
	status, body = _api_get(port, "/api/live")
	assert status == 200
	assert "timestamp" in body
	assert "sync_active" in body
	assert "maintain_active" in body
	assert "queue" in body


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


def test_get_search_without_query(test_server):
	"""GET /api/search (no FTS query) returns keyword-mode results."""
	port, _, _ = test_server
	# Patch list_sessions_window to return rows for the search handler
	status, body = _api_get(port, "/api/search?scope=all")
	assert status == 200
	assert "mode" in body
	assert body["mode"] == "keyword"
	assert "results" in body
	assert "pagination" in body


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


def test_post_ask(test_server):
	"""POST /api/ask returns mocked answer."""
	port, _, _ = test_server
	status, body = _api_post(port, "/api/ask", {"question": "What is Lerim?"})
	assert status == 200
	assert "answer" in body
	assert "Mocked answer" in body["answer"]


def test_post_ask_rejects_limit_field(test_server):
	"""POST /api/ask rejects removed `limit` field."""
	port, _, _ = test_server
	status, body = _api_post_error(
		port,
		"/api/ask",
		{"question": "What is Lerim?", "limit": 5},
	)
	assert status == 400
	assert "error" in body


def test_post_ask_missing_question(test_server):
	"""POST /api/ask without question returns 400."""
	port, _, _ = test_server
	status, body = _api_post_error(port, "/api/ask", {"question": ""})
	assert status == 400
	assert "error" in body


def test_post_sync(test_server):
	"""POST /api/sync starts a sync job and returns started status."""
	port, _, _ = test_server
	status, body = _api_post(port, "/api/sync", {"window": "7d"})
	assert status == 200
	assert body["status"] == "started"
	assert "job_id" in body


def test_post_maintain(test_server):
	"""POST /api/maintain starts a maintain job and returns started status."""
	port, _, _ = test_server
	status, body = _api_post(port, "/api/maintain", {})
	assert status == 200
	assert body["status"] == "started"
	assert "job_id" in body


def test_post_connect(test_server):
	"""POST /api/connect with platform returns connection result."""
	port, _, _ = test_server
	status, body = _api_post(port, "/api/connect", {"platform": "claude"})
	assert status == 200
	assert body["name"] == "claude"
	assert body["connected"] is True


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
	status, body = _api_post_error(port, "/api/memory-graph/query", {
		"query": "",
		"filters": {},
	})
	assert status == 404
	assert "error" in body


def test_post_memory_graph_expand_route_removed(test_server):
	"""POST /api/memory-graph/expand now returns 404 after DB-only cleanup."""
	port, _, _ = test_server
	status, body = _api_post_error(port, "/api/memory-graph/expand", {
		"node_id": "mem:auth_decision",
	})
	assert status == 404
	assert "error" in body


def test_post_config_save(test_server):
	"""POST /api/config with patch saves config."""
	port, _, _ = test_server
	status, body = _api_post(port, "/api/config", {
		"patch": {"server": {"port": 9999}},
	})
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
		url, data=data, method="PATCH",
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
	"""POST /api/ask with empty body returns 400."""
	port, _, _ = test_server
	url = f"http://127.0.0.1:{port}/api/ask"
	req = urllib.request.Request(
		url, data=b"", method="POST",
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
	"""POST /api/ask with invalid JSON falls back to empty body -> 400."""
	port, _, _ = test_server
	url = f"http://127.0.0.1:{port}/api/ask"
	req = urllib.request.Request(
		url, data=b"not json at all", method="POST",
		headers={"Content-Type": "application/json", "Content-Length": "15"},
	)
	try:
		with urllib.request.urlopen(req, timeout=5) as resp:
			status = resp.status
			json.loads(resp.read())
	except urllib.error.HTTPError as exc:
		status = exc.code
		json.loads(exc.read())
	# _read_json_body returns {} on bad JSON, so question will be empty -> 400
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
	assert "data" in result
	assert "mlflow_enabled" in result
	assert result["server"]["port"] == 8765
	assert "agent" in result["roles"]


def test_load_messages_for_run_empty_path():
	"""_load_messages_for_run returns [] when session_path is empty."""
	from lerim.server.httpd import _load_messages_for_run

	result = _load_messages_for_run({"session_path": ""})
	assert result == []
