"""Smoke test: embeddings are indexed after extraction."""

import pytest

from lerim.agents.extract import run_extraction
from lerim.context import ContextStore, resolve_project_identity
from tests.conftest import EXTRACT_TRACES_DIR


@pytest.mark.smoke
@pytest.mark.llm
@pytest.mark.agent
def test_embeddings_indexed(live_config, live_repo_root):
	"""Verify embeddings are indexed after extracting a decision.

	Uses clear_decision_with_noise fixture which creates a durable record.
	"""
	store = ContextStore(live_config.context_db_path)
	store.initialize()
	identity = resolve_project_identity(live_repo_root)
	store.register_project(identity)

	session_id = "smoke-embeddings"
	trace_path = EXTRACT_TRACES_DIR / "clear_decision_with_noise.jsonl"

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

	with store.connect() as conn:
		count = conn.execute("SELECT COUNT(*) FROM record_embeddings").fetchone()[0]

	assert count > 0, "No embeddings indexed after extraction"
