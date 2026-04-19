"""Smoke tests — quick sanity checks with real LLM calls."""

import pytest

from lerim.agents.ask import run_ask
from lerim.agents.maintain import run_maintain
from lerim.config.providers import build_pydantic_model
from lerim.context import ContextStore, resolve_project_identity
from tests.integration.extract_helpers import run_extract_case

pytestmark = [
	pytest.mark.smoke,
	pytest.mark.llm,
	pytest.mark.agent,
]


def test_smoke_extract_completes(live_config, live_repo_root):
	"""Verify extraction completes without crashing.

	Uses the smallest fixture: routine_operational_no_memory.jsonl (5 lines).
	This trace contains routine cleanup work with no durable memory.
	"""
	outcome = run_extract_case(
		case_name="routine_operational_no_memory",
		live_config=live_config,
		live_repo_root=live_repo_root,
	)

	assert outcome.result is not None, "Extraction returned no result"
	assert outcome.result.completion_summary, "Extraction returned empty summary"


def test_smoke_embeddings_indexed(live_config, live_repo_root):
	"""Verify embeddings are indexed after extracting a decision.

	Uses clear_decision_with_noise fixture which creates a durable record.
	"""
	outcome = run_extract_case(
		case_name="clear_decision_with_noise",
		live_config=live_config,
		live_repo_root=live_repo_root,
	)

	assert outcome.result is not None, "Extraction returned no result"

	store = ContextStore(live_config.context_db_path)
	with store.connect() as conn:
		count = conn.execute("SELECT COUNT(*) FROM record_embeddings").fetchone()[0]

	assert count > 0, "No embeddings indexed after extraction"


def test_smoke_ask_returns_answer(live_config, live_repo_root):
	"""Verify ask agent can retrieve and answer from seeded records."""
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
		change_reason="smoke_test",
		record_type="decision",
		scope="project",
		title="Use tabs for indentation",
		body="All code files must use tabs, not spaces, for indentation.",
	)

	model = build_pydantic_model("agent", config=live_config)
	result = run_ask(
		context_db_path=live_config.context_db_path,
		project_identity=identity,
		project_ids=[identity.project_id],
		session_id="smoke-ask",
		model=model,
		question="What indentation style should I use?",
	)

	assert result is not None, "Ask returned no result"
	assert result.answer, "Ask returned empty answer"


def test_smoke_maintain_completes(live_config, live_repo_root):
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

	for i in range(2):
		store.create_record(
			project_id=identity.project_id,
			session_id="smoke-maintain-seed",
			change_reason="smoke_test",
			record_type="decision",
			scope="project",
			title="Use tabs for indentation",
			body=f"Version {i}: All code must use tabs, not spaces.",
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
