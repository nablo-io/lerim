"""Unit tests for short-term Working Memory artifacts."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lerim.context import ContextStore, resolve_project_identity
from lerim.context_brief import resolve_context_brief_project
from lerim.server.runtime import LerimRuntime
from lerim.working_memory import (
    WORKING_MEMORY_FILENAME,
    load_working_memory_data,
    render_working_memory_markdown,
    summarize_git_status,
    working_memory_paths,
    working_memory_status,
    working_memory_status_to_dict,
    working_memory_window_start,
)
from tests.helpers import make_config, run_cli, run_cli_json, write_test_config


@pytest.fixture
def mock_embeddings(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch embeddings so context writes remain local and deterministic."""
    provider = MagicMock()
    provider.embedding_dims = 384
    provider.model_id = "test-model"
    provider.embed_document.return_value = [0.1] * 384
    provider.embed_query.return_value = [0.1] * 384
    monkeypatch.setattr("lerim.context.store.get_embedding_provider", lambda: provider)
    return provider


def _register_seeded_project(store: ContextStore, repo: Path) -> str:
    """Register a project plus one source session and return project_id."""
    identity = resolve_project_identity(repo)
    store.register_project(identity)
    store.upsert_session(
        project_id=identity.project_id,
        session_id="sess_working_memory",
        agent_type="test",
        source_trace_ref="trace.jsonl",
        repo_path=str(identity.repo_path),
        cwd=str(identity.repo_path),
        started_at="2026-04-30T00:00:00+00:00",
        model_name="test-model",
        instructions_text=None,
        prompt_text=None,
        metadata={},
    )
    return identity.project_id


def test_working_memory_paths_use_separate_artifact_name(tmp_path):
    """Working Memory writes a separate current artifact from Context Brief."""
    cfg = make_config(tmp_path / ".lerim")
    paths = working_memory_paths(cfg, "proj_demo")

    assert paths.current_file.name == WORKING_MEMORY_FILENAME
    assert paths.current_manifest.name == "WORKING_MEMORY.manifest.json"


def test_render_uses_replacement_as_current_final_decision(tmp_path, mock_embeddings):
    """Superseded decisions are historical while the replacement becomes current."""
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = replace(make_config(tmp_path / ".lerim"), projects={"repo": str(repo)})
    store = ContextStore(cfg.context_db_path)
    project_id = _register_seeded_project(store, repo)
    old = store.create_record(
        project_id=project_id,
        session_id="sess_working_memory",
        kind="decision",
        title="Use Postgres for the project database",
        body="Use Postgres for the project database.",
        decision="Use Postgres.",
        why="It was the earlier choice.",
    )
    replacement_record = store.create_record(
        project_id=project_id,
        session_id="sess_working_memory",
        kind="decision",
        title="Use SQLite for the project database",
        body="Use SQLite for the project database.",
        decision="Use SQLite.",
        why="The current repo uses the local DB-only runtime.",
    )
    store.supersede_record(
        record_id=old["record_id"],
        replacement_record_id=replacement_record["record_id"],
        session_id="sess_working_memory",
        project_ids=[project_id],
        reason="database decision changed",
    )
    data = load_working_memory_data(
        store,
        project_id=project_id,
        since=working_memory_window_start(),
    )
    project = resolve_context_brief_project(config=cfg, cwd=repo)

    markdown = render_working_memory_markdown(
        project=project,
        generated_at="2026-04-30T06:00:00+00:00",
        window_started_at="2026-04-30T00:00:00+00:00",
        previous_generated_at=None,
        generation_trigger="manual",
        db_records_changed_since_previous=3,
        data=data,
    )

    assert "## Current Final Decisions" in markdown
    assert "## If Continuing This Work" in markdown
    assert "## Changed Context" in markdown
    assert "## Recent Changes" not in markdown
    assert "Next Actions" not in markdown
    assert "Use SQLite for the project database" in markdown
    assert "Use Postgres for the project database" in markdown
    assert "was superseded; use `Use SQLite for the project database`" in markdown
    assert "do not reuse" in markdown


def test_render_explains_no_continuation_when_recent_window_is_empty(tmp_path):
    """Working Memory avoids generic next actions when no recent records changed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = replace(make_config(tmp_path / ".lerim"), projects={"repo": str(repo)})
    project = resolve_context_brief_project(config=cfg, cwd=repo)

    markdown = render_working_memory_markdown(
        project=project,
        generated_at="2026-04-30T06:00:00+00:00",
        window_started_at="2026-04-30T00:00:00+00:00",
        previous_generated_at=None,
        generation_trigger="manual",
        db_records_changed_since_previous=0,
        data=load_working_memory_data(
            ContextStore(cfg.context_db_path),
            project_id=project.identity.project_id,
            since=working_memory_window_start(),
        ),
    )

    assert "## If Continuing This Work" in markdown
    assert "No continuation-specific handoff was inferred" in markdown
    assert "Next Actions" not in markdown
    assert "Review the latest changed records" not in markdown


def test_render_includes_workspace_snapshot_for_git_repo(tmp_path):
    """Working Memory includes generated-time git state when available."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(("git", "init"), cwd=repo, check=True, capture_output=True)
    subprocess.run(("git", "config", "user.email", "test@example.com"), cwd=repo, check=True)
    subprocess.run(("git", "config", "user.name", "Test User"), cwd=repo, check=True)
    tracked = repo / "README.md"
    tracked.write_text("hello\n", encoding="utf-8")
    subprocess.run(("git", "add", "README.md"), cwd=repo, check=True)
    subprocess.run(("git", "commit", "-m", "initial"), cwd=repo, check=True, capture_output=True)
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    cfg = replace(make_config(tmp_path / ".lerim"), projects={"repo": str(repo)})
    project = resolve_context_brief_project(config=cfg, cwd=repo)

    markdown = render_working_memory_markdown(
        project=project,
        generated_at="2026-04-30T06:00:00+00:00",
        window_started_at="2026-04-30T00:00:00+00:00",
        previous_generated_at=None,
        generation_trigger="manual",
        db_records_changed_since_previous=0,
        data=load_working_memory_data(
            ContextStore(cfg.context_db_path),
            project_id=project.identity.project_id,
            since=working_memory_window_start(),
        ),
    )

    assert "## Workspace Snapshot" in markdown
    assert "Dirty files at generation: 1" in markdown
    assert "`src`: 1" in markdown


def test_summarize_git_status_groups_top_level_paths():
    """Git status rows are grouped by top-level area for compact handoffs."""
    grouped = summarize_git_status(" M src/app.py\n?? docs/new.md\nR  old.py -> tests/new.py\n")

    assert grouped == {"src": 1, "docs": 1, "tests": 1}


def test_runtime_working_memory_refresh_writes_current_artifact(
    tmp_path,
    mock_embeddings,
):
    """Runtime refresh writes dated and stable Working Memory artifacts."""
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = replace(make_config(tmp_path / ".lerim"), projects={"repo": str(repo)})
    store = ContextStore(cfg.context_db_path)
    project_id = _register_seeded_project(store, repo)
    record = store.create_record(
        project_id=project_id,
        session_id="sess_working_memory",
        kind="decision",
        title="Keep a separate Working Memory artifact",
        body="Keep Working Memory separate from Context Brief.",
        decision="Keep Working Memory separate from Context Brief.",
        why="They represent different time scales.",
    )

    runtime = LerimRuntime(default_cwd=str(repo), config=cfg)
    result = runtime.working_memory(repo_root=repo, project_name="repo", force=True)
    paths = working_memory_paths(cfg, project_id)

    assert result["status"] == "generated"
    assert result["records_included"] >= 1
    assert paths.current_file.is_file()
    assert Path(result["run_folder"], WORKING_MEMORY_FILENAME).is_file()
    assert record["record_id"] in paths.current_file.read_text(encoding="utf-8")


def test_working_memory_status_goes_stale_when_records_change(
    tmp_path,
    mock_embeddings,
):
    """Working Memory status reports staleness after newer DB changes."""
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = replace(make_config(tmp_path / ".lerim"), projects={"repo": str(repo)})
    store = ContextStore(cfg.context_db_path)
    project_id = _register_seeded_project(store, repo)
    paths = working_memory_paths(cfg, project_id)
    paths.current_dir.mkdir(parents=True)
    paths.current_file.write_text("# Working Memory\n", encoding="utf-8")
    paths.current_manifest.write_text(
        json.dumps(
            {
                "generated_at": "2026-04-30T00:00:00+00:00",
                "window_started_at": "2026-04-29T18:00:00+00:00",
                "window_hours": 6,
                "records_included": 0,
                "recent_versions_considered": 0,
            }
        ),
        encoding="utf-8",
    )
    store.create_record(
        project_id=project_id,
        session_id="sess_working_memory",
        kind="decision",
        title="New working-memory status decision",
        body="New working-memory status decision body.",
        decision="New working-memory status decision",
        why="Because status should notice newer versions.",
        created_at="2026-04-30T01:00:00+00:00",
        updated_at="2026-04-30T01:00:00+00:00",
    )
    project = resolve_context_brief_project(config=cfg, cwd=repo)

    payload = working_memory_status_to_dict(
        working_memory_status(config=cfg, store=store, project=project)
    )

    assert payload["availability"] == "stale"
    assert payload["records_changed_since_generation"] == 1


def test_cli_working_memory_show_reads_existing_artifact(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    mock_embeddings,
) -> None:
    """CLI show prints Working Memory freshness before the current file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = write_test_config(tmp_path, projects={"repo": str(repo)})
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    from lerim.config.settings import reload_config

    cfg = reload_config()
    store = ContextStore(cfg.context_db_path)
    project_id = _register_seeded_project(store, repo)
    paths = working_memory_paths(cfg, project_id)
    paths.current_dir.mkdir(parents=True)
    paths.current_file.write_text("# Working Memory\n\nhello\n", encoding="utf-8")
    paths.current_manifest.write_text(
        json.dumps(
            {
                "generated_at": "2100-01-01T00:00:00+00:00",
                "window_started_at": "2099-12-31T18:00:00+00:00",
                "window_hours": 6,
                "records_included": 0,
                "recent_versions_considered": 0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        "lerim.server.cli.run_working_memory_for_project",
        lambda **_kwargs: pytest.fail("show must not refresh"),
    )

    code, output = run_cli(["working-memory", "show"])

    assert code == 0
    assert "Working Memory Live Status:" in output
    assert "hello" in output


def test_cli_working_memory_status_json(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    mock_embeddings,
) -> None:
    """CLI status JSON exposes Working Memory freshness metadata."""
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = write_test_config(tmp_path, projects={"repo": str(repo)})
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    from lerim.config.settings import reload_config

    cfg = reload_config()
    store = ContextStore(cfg.context_db_path)
    project_id = _register_seeded_project(store, repo)
    paths = working_memory_paths(cfg, project_id)
    paths.current_dir.mkdir(parents=True)
    paths.current_file.write_text("# Working Memory\n", encoding="utf-8")
    paths.current_manifest.write_text(
        json.dumps(
            {
                "generated_at": "2100-01-01T00:00:00+00:00",
                "window_started_at": "2099-12-31T18:00:00+00:00",
                "window_hours": 6,
                "records_included": 0,
                "recent_versions_considered": 0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(repo)

    code, payload = run_cli_json(["working-memory", "status", "--json"])

    assert code == 0
    assert payload["availability"] == "available"
    assert payload["window_hours"] == 6
