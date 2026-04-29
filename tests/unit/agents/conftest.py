"""Shared fixtures for agent tool tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lerim.agents.tools import ContextDeps
from lerim.context import ContextStore, resolve_project_identity


@pytest.fixture
def project_identity(tmp_path, monkeypatch):
    """Deterministic project identity."""
    monkeypatch.setattr(
        "lerim.config.project_scope.git_root_for",
        lambda _p=None: tmp_path,
    )
    return resolve_project_identity(tmp_path)


@pytest.fixture
def deps(tmp_path, project_identity):
    """ContextDeps with a real temp DB and no trace."""
    db_path = tmp_path / "context.sqlite3"
    store = ContextStore(db_path)
    store.initialize()
    store.register_project(project_identity)
    return ContextDeps(
        context_db_path=db_path,
        project_identity=project_identity,
        session_id="sess_test",
        project_ids=[project_identity.project_id],
        trace_path=None,
        trace_total_lines=0,
        read_ranges=[],
        notes=[],
        pruned_offsets=set(),
    )


@pytest.fixture
def deps_with_trace(tmp_path, project_identity):
    """ContextDeps with a real trace file."""
    trace_path = tmp_path / "trace.jsonl"
    lines = []
    for i in range(10):
        lines.append(f'{{"type":"message","role":"user","content":"line {i}"}}')
    trace_path.write_text("\n".join(lines))

    db_path = tmp_path / "context.sqlite3"
    store = ContextStore(db_path)
    store.initialize()
    store.register_project(project_identity)
    return ContextDeps(
        context_db_path=db_path,
        project_identity=project_identity,
        session_id="sess_test",
        project_ids=[project_identity.project_id],
        trace_path=trace_path,
        trace_total_lines=10,
        read_ranges=[],
        notes=[],
        pruned_offsets=set(),
    )


def make_run_context(deps):
    """Build a mock RunContext wrapping ContextDeps."""
    ctx = MagicMock()
    ctx.deps = deps
    return ctx
