"""Smoke test: answer agent can retrieve and answer."""

import pytest

from lerim.agents.context_answerer import run_context_answerer
from lerim.context import ContextStore, resolve_project_identity


@pytest.mark.smoke
@pytest.mark.llm
@pytest.mark.agent
def test_answer_returns_answer(live_config, live_repo_root):
	"""Verify answer agent can retrieve and answer from seeded records."""
	store = ContextStore(live_config.context_db_path)
	store.initialize()
	identity = resolve_project_identity(live_repo_root)
	store.register_project(identity)

	store.upsert_session(
		project_id=identity.project_id,
		session_id="smoke-seed",
		agent_type="smoke-test",
		source_trace_ref="smoke",
		repo_path=str(live_repo_root),
		cwd=str(live_repo_root),
		started_at="2026-01-01T00:00:00Z",
		model_name="smoke",
		instructions_text=None,
		prompt_text=None,
		metadata={},
	)

	store.create_record(
		project_id=identity.project_id,
		session_id="smoke-seed",
		kind="decision",
		title="Use tabs for indentation",
		body="All code files must use tabs, not spaces, for indentation.",
		decision="Use tabs instead of spaces for indentation in all code files.",
		why="Tabs allow configurable display width and match project conventions.",
	)

	result = run_context_answerer(
		context_db_path=live_config.context_db_path,
		project_identity=identity,
		project_ids=[identity.project_id],
		session_id="smoke-answer",
		config=live_config,
		question="What indentation style should I use?",
	)

	assert result is not None, "Answer returned no result"
	assert result.answer, "Answer returned empty answer"
