"""Shared fixtures for context store tests."""

from __future__ import annotations


import pytest

from lerim.context import ContextStore, resolve_project_identity
from tests.helpers import make_config


@pytest.fixture
def store(tmp_path):
    """Fresh ContextStore with initialized schema."""
    db_path = tmp_path / "context.sqlite3"
    s = ContextStore(db_path)
    s.initialize()
    return s


@pytest.fixture
def project_identity(tmp_path, monkeypatch):
    """Deterministic project identity for tests."""
    monkeypatch.setattr(
        "lerim.config.project_scope.git_root_for",
        lambda _p=None: tmp_path,
    )
    return resolve_project_identity(tmp_path)


@pytest.fixture
def seeded_store(store, project_identity):
    """Store with a registered project and one seeded session."""
    store.register_project(project_identity)
    store.upsert_session(
        project_id=project_identity.project_id,
        session_id="sess_test",
        agent_type="test",
        source_trace_ref="test.jsonl",
        repo_path=str(project_identity.repo_path),
        cwd=str(project_identity.repo_path),
        started_at="2026-01-01T00:00:00Z",
        model_name="test-model",
        instructions_text=None,
        prompt_text=None,
        metadata={},
    )
    return store


@pytest.fixture
def tmp_config(tmp_path):
    """Config pointing at tmp_path."""
    return make_config(tmp_path)
