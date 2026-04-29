"""Typed runtime contracts and leaf utilities for orchestration.

This module is a leaf in the import graph -- it must NOT import from
runtime.py, tools.py, or any agent module to avoid circular imports.
"""

from __future__ import annotations

from pydantic import BaseModel


class SyncResultContract(BaseModel):
	"""Stable sync return payload schema used by CLI and daemon."""

	trace_path: str
	context_db_path: str
	project_id: str
	workspace_root: str
	run_folder: str
	artifacts: dict[str, str]
	records_created: int = 0
	records_updated: int = 0
	records_archived: int = 0
	cost_usd: float = 0.0


class MaintainResultContract(BaseModel):
	"""Stable maintain return payload schema used by CLI and daemon."""

	context_db_path: str
	project_id: str
	workspace_root: str
	run_folder: str
	artifacts: dict[str, str]
	records_created: int = 0
	records_updated: int = 0
	records_archived: int = 0
	cost_usd: float = 0.0


if __name__ == "__main__":
	"""Run contract model smoke checks."""
	sync = SyncResultContract(
		trace_path="/tmp/trace.jsonl",
		context_db_path="/tmp/context.sqlite3",
		project_id="proj_demo",
		workspace_root="/tmp/workspace",
		run_folder="/tmp/workspace/sync-run",
		artifacts={"agent_log": "/tmp/workspace/sync-run/agent.log"},
	)
	assert sync.cost_usd == 0.0

	maintain = MaintainResultContract(
		context_db_path="/tmp/context.sqlite3",
		project_id="proj_demo",
		workspace_root="/tmp/workspace",
		run_folder="/tmp/workspace/maintain-run",
		artifacts={"agent_log": "/tmp/workspace/maintain-run/agent.log"},
	)
	assert maintain.cost_usd == 0.0

	print("runtime contracts: self-test passed")
