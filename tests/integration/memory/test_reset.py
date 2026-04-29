"""Integration coverage for the memory reset command backend."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lerim.config.settings import reload_config
from lerim.context import ContextStore, resolve_project_identity
from lerim.server.api import api_memory_reset
from lerim.sessions import catalog
from tests.helpers import write_test_config


class _FakeEmbeddingProvider:
    embedding_dims = 384
    model_id = "memory-reset-test"

    def embed_document(self, _text: str) -> list[float]:
        return [0.1] * self.embedding_dims

    def embed_query(self, _text: str) -> list[float]:
        return [0.1] * self.embedding_dims


@pytest.mark.integration
def test_project_memory_reset_dry_run_then_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _FakeEmbeddingProvider()
    monkeypatch.setattr("lerim.context.store.get_embedding_provider", lambda: provider)
    monkeypatch.setattr("lerim.context.embedding.get_embedding_provider", lambda: provider)

    repo_root = tmp_path / "memory-project"
    repo_root.mkdir()
    config_path = write_test_config(tmp_path, projects={repo_root.name: str(repo_root)})
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    config = reload_config()
    catalog.init_sessions_db()

    identity = resolve_project_identity(repo_root)
    store = ContextStore(config.context_db_path)
    store.register_project(identity)
    store.upsert_session(
        project_id=identity.project_id,
        session_id="memory-reset-session",
        agent_type="codex",
        source_trace_ref=str(tmp_path / "session.jsonl"),
        repo_path=str(repo_root),
        cwd=str(repo_root),
        started_at="2026-04-01T00:00:00+00:00",
        model_name="test-model",
        instructions_text=None,
        prompt_text=None,
    )
    store.create_record(
        project_id=identity.project_id,
        session_id="memory-reset-session",
        kind="fact",
        title="Memory reset fact",
        body="This record should disappear after project reset.",
    )
    catalog.index_session_for_fts(
        run_id="memory-reset-run",
        agent_type="codex",
        content="Memory reset indexed content",
        repo_path=str(repo_root),
        session_path=str(tmp_path / "session.jsonl"),
        content_hash="memory-reset-hash",
    )
    catalog.enqueue_session_job(
        "memory-reset-run",
        agent_type="codex",
        session_path=str(tmp_path / "session.jsonl"),
        repo_path=str(repo_root),
    )

    preview: dict[str, Any] = api_memory_reset(project=repo_root.name, dry_run=True)
    assert preview["error"] is False
    assert preview["deleted"]["records"] == 1
    assert store.count_project_memory(identity.project_id)["records"] == 1
    assert catalog.fetch_session_doc("memory-reset-run") is not None

    result: dict[str, Any] = api_memory_reset(project=repo_root.name)
    assert result["error"] is False
    assert result["deleted"]["records"] == 1
    assert store.count_project_memory(identity.project_id)["records"] == 0
    assert catalog.fetch_session_doc("memory-reset-run") is None
