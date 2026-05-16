"""Typed runtime contracts and leaf utilities for orchestration.

This module is a leaf in the import graph -- it must NOT import from
runtime.py or any agent module to avoid circular imports.
"""

from __future__ import annotations

from pydantic import BaseModel


class IngestResultContract(BaseModel):
	"""Stable ingest return payload schema used by CLI and daemon."""

	trace_path: str
	context_db_path: str
	project_id: str | None = None
	scope_type: str = "project"
	scope_id: str = ""
	scope_label: str | None = None
	workspace_root: str
	run_folder: str
	artifacts: dict[str, str]
	records_created: int = 0
	records_updated: int = 0
	records_archived: int = 0
	cost_usd: float = 0.0


class ContextCuratorResultContract(BaseModel):
	"""Stable context-curator return payload schema used by CLI and daemon."""

	context_db_path: str
	project_id: str
	workspace_root: str
	run_folder: str
	artifacts: dict[str, str]
	records_created: int = 0
	records_updated: int = 0
	records_archived: int = 0
	cost_usd: float = 0.0


class ContextBriefResultContract(BaseModel):
	"""Stable context-brief refresh payload schema used by CLI and daemon."""

	status: str
	project: str
	project_id: str
	trigger: str = "manual"
	generated_at: str | None = None
	context_db_path: str
	workspace_root: str
	run_folder: str | None = None
	current_file: str
	current_manifest: str
	records_considered: int = 0
	records_included: int = 0
	records_changed_since_previous: int = 0
	included_record_ids: list[str] = []
	skip_reason: str | None = None
	cost_usd: float = 0.0


if __name__ == "__main__":
	"""Run contract model smoke checks."""
	ingest = IngestResultContract(
		trace_path="/tmp/trace.jsonl",
		context_db_path="/tmp/context.sqlite3",
		project_id="proj_demo",
		workspace_root="/tmp/workspace",
		run_folder="/tmp/workspace/ingest-run",
		artifacts={"agent_log": "/tmp/workspace/ingest-run/agent.log"},
	)
	assert ingest.cost_usd == 0.0

	context_curator = ContextCuratorResultContract(
		context_db_path="/tmp/context.sqlite3",
		project_id="proj_demo",
		workspace_root="/tmp/workspace",
		run_folder="/tmp/workspace/context-curator-run",
		artifacts={"agent_log": "/tmp/workspace/context-curator-run/agent.log"},
	)
	assert context_curator.cost_usd == 0.0

	print("runtime contracts: self-test passed")
