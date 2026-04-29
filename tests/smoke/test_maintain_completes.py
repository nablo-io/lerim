"""Smoke test: maintain agent runs without crashing."""

import pytest

from lerim.agents.maintain import run_maintain
from lerim.config.providers import build_pydantic_model
from lerim.context import ContextStore, resolve_project_identity


@pytest.mark.smoke
@pytest.mark.llm
@pytest.mark.agent
def test_maintain_completes(live_config, live_repo_root):
	"""Verify maintain agent runs without crashing on seeded records."""
	store = ContextStore(live_config.context_db_path)
	store.initialize()
	identity = resolve_project_identity(live_repo_root)
	store.register_project(identity)

	store.upsert_session(
		project_id=identity.project_id,
		session_id="smoke-maintain-seed",
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

	store.upsert_session(
		project_id=identity.project_id,
		session_id="smoke-maintain",
		agent_type="maintain",
		source_trace_ref="smoke",
		repo_path=str(live_repo_root),
		cwd=str(live_repo_root),
		started_at="2026-01-01T00:00:00Z",
		model_name="smoke",
		instructions_text=None,
		prompt_text=None,
		metadata={},
	)

	for i in range(2):
		store.create_record(
			project_id=identity.project_id,
			session_id="smoke-maintain-seed",
			kind="decision",
			title="Use tabs for indentation",
			body=f"Version {i}: All code must use tabs, not spaces.",
			decision="Use tabs instead of spaces for indentation.",
			why=f"Version {i}: Tabs allow configurable display width.",
		)

	model = build_pydantic_model("agent", config=live_config)
	result = run_maintain(
		context_db_path=live_config.context_db_path,
		project_identity=identity,
		session_id="smoke-maintain",
		model=model,
	)

	assert result is not None, "Maintain returned no result"
	assert result.completion_summary, "Maintain returned empty summary"
