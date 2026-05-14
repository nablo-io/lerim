"""Smoke test: extraction completes without crashing."""

import pytest

from lerim.agents.extract import run_extraction
from lerim.context import ContextStore, resolve_project_identity
from tests.conftest import EXTRACT_TRACES_DIR


@pytest.mark.smoke
@pytest.mark.llm
@pytest.mark.agent
def test_extract_completes(live_config, live_repo_root):
	"""Verify extraction completes without crashing.

	Uses the smallest fixture: routine_operational_no_durable_record.jsonl (5 lines).
	This trace contains routine cleanup work with no durable record.
	"""
	store = ContextStore(live_config.context_db_path)
	store.initialize()
	identity = resolve_project_identity(live_repo_root)
	store.register_project(identity)

	session_id = "smoke-extract"
	trace_path = EXTRACT_TRACES_DIR / "routine_operational_no_durable_record.jsonl"

	store.upsert_session(
		project_id=identity.project_id,
		session_id=session_id,
		agent_type="smoke-test",
		source_trace_ref=str(trace_path),
		repo_path=str(live_repo_root),
		cwd=str(live_repo_root),
		started_at="2026-01-01T00:00:00Z",
		model_name="smoke",
		instructions_text=None,
		prompt_text=None,
		metadata={},
	)

	result = run_extraction(
		context_db_path=live_config.context_db_path,
		project_identity=identity,
		session_id=session_id,
		trace_path=trace_path,
		config=live_config,
	)

	assert result is not None, "Extraction returned no result"
	assert result.completion_summary, "Extraction returned empty summary"
