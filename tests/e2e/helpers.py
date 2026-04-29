"""E2E test helpers: assertions and utilities."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import sqlite_vec


def parse_json_output(output: str) -> dict[str, Any]:
	"""Parse JSON output from CLI --json commands."""
	return json.loads(output.strip())


def connect_context_db(db_path: Path) -> sqlite3.Connection:
	"""Open context DB with sqlite-vec loaded."""
	conn = sqlite3.connect(db_path)
	conn.row_factory = sqlite3.Row
	conn.enable_load_extension(True)
	sqlite_vec.load(conn)
	conn.enable_load_extension(False)
	return conn


def count_records(db_path: Path, kind: str | None = None) -> int:
	"""Count records in the context database."""
	with connect_context_db(db_path) as conn:
		if kind:
			return conn.execute(
				"SELECT COUNT(*) FROM records WHERE kind = ?", (kind,)
			).fetchone()[0]
		return conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]


def get_record_titles(db_path: Path, kind: str | None = None) -> list[str]:
	"""Get all record titles from the context database."""
	with connect_context_db(db_path) as conn:
		if kind:
			rows = conn.execute(
				"SELECT title FROM records WHERE kind = ? ORDER BY created_at",
				(kind,),
			).fetchall()
		else:
			rows = conn.execute(
				"SELECT title FROM records ORDER BY created_at"
			).fetchall()
		return [row[0] for row in rows]


def assert_server_healthy(cli, timeout: int = 5) -> None:
	"""Assert the server responds to health check."""
	result = cli.run("status", "--json", timeout=timeout)
	assert result.returncode == 0, f"Server not healthy: {result.stderr}"


def assert_record_exists(db_path: Path, kind: str, title_contains: str) -> None:
	"""Assert a record with the given kind and title substring exists."""
	with connect_context_db(db_path) as conn:
		rows = conn.execute(
			"SELECT title FROM records WHERE kind = ? AND title LIKE ?",
			(kind, f"%{title_contains}%"),
		).fetchall()
	assert rows, f"No {kind} record found with title containing '{title_contains}'"


def wait_for_condition(
	condition: callable,
	timeout: float = 30.0,
	interval: float = 0.5,
	message: str = "Condition not met",
) -> None:
	"""Wait for a condition to become true."""
	import time

	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		if condition():
			return
		time.sleep(interval)
	raise AssertionError(f"{message} (timed out after {timeout}s)")
