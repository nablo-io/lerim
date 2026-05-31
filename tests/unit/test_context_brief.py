"""Unit tests for generated Context Brief behavior."""

from __future__ import annotations

import json
from dataclasses import fields, replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lerim.context import ContextStore, resolve_project_identity
from lerim.server.runtime import LerimRuntime
from lerim.context_brief import (
    MemoryLine,
    MemorySection,
    ContextBriefDraft,
    count_changed_records_since,
    load_candidate_records,
    render_context_brief_markdown,
    resolve_context_brief_project,
    validate_draft,
    context_brief_status,
    context_brief_paths,
    sanitize_draft_section_kinds,
    trim_context_brief_draft,
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
        session_id="sess_context_brief",
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


def _create_record(
    store: ContextStore,
    *,
    project_id: str,
    kind: str,
    title: str,
) -> dict:
    """Create one active context record for tests."""
    return store.create_record(
        project_id=project_id,
        session_id="sess_context_brief",
        kind=kind,
        title=title,
        body=f"{title} body.",
        decision=title if kind == "decision" else None,
        why="Because it is the project choice." if kind == "decision" else None,
        user_intent="Understand recent work." if kind == "episode" else None,
        what_happened="A useful implementation detail was captured."
        if kind == "episode"
        else None,
    )


def _markdown_heading_index(markdown: str, heading: str) -> int:
    """Return the index of a markdown heading, failing clearly when absent."""
    marker = f"\n{heading}\n"
    assert marker in markdown
    return markdown.index(marker)


def test_resolve_context_brief_project_uses_most_specific_registered_path(tmp_path):
    """Cwd project resolution chooses the deepest registered project path."""
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    cfg = replace(
        make_config(tmp_path / ".lerim"),
        projects={"parent": str(parent), "child": str(child)},
    )

    resolved = resolve_context_brief_project(config=cfg, cwd=child / "src")

    assert resolved.name == "child"
    assert resolved.identity.repo_path == child.resolve()


def test_candidate_loading_prioritizes_durable_records(tmp_path, mock_embeddings):
    """Candidate ordering prefers decisions and constraints before episodes."""
    repo = tmp_path / "repo"
    repo.mkdir()
    store = ContextStore(tmp_path / "context.sqlite3")
    project_id = _register_seeded_project(store, repo)
    episode = _create_record(
        store,
        project_id=project_id,
        kind="episode",
        title="Debugged the workflow",
    )
    store.create_record(
        project_id=project_id,
        session_id="sess_context_brief",
        kind="decision",
        title="Older generated Context Brief decision",
        body="Older generated Context Brief decision body.",
        decision="Older generated Context Brief decision",
        why="Because older durable choices should follow newer ones.",
        created_at="2026-04-30T00:00:00+00:00",
        updated_at="2026-04-30T00:00:00+00:00",
    )
    newer_decision = store.create_record(
        project_id=project_id,
        session_id="sess_context_brief",
        kind="decision",
        title="Newer generated Context Brief decision",
        body="Newer generated Context Brief decision body.",
        decision="Newer generated Context Brief decision",
        why="Because the latest durable choice should lead.",
        created_at="2026-04-30T01:00:00+00:00",
        updated_at="2026-04-30T01:00:00+00:00",
    )

    candidates = load_candidate_records(store, project_id=project_id)

    assert candidates[0]["record_id"] == newer_decision["record_id"]
    assert candidates[-1]["record_id"] == episode["record_id"]


def test_candidate_loading_prefers_newer_records_within_kind(tmp_path, mock_embeddings):
    """Candidate ordering uses latest updated_at within the same record kind."""
    repo = tmp_path / "repo"
    repo.mkdir()
    store = ContextStore(tmp_path / "context.sqlite3")
    project_id = _register_seeded_project(store, repo)
    old_fact = store.create_record(
        project_id=project_id,
        session_id="sess_context_brief",
        kind="fact",
        title="Older fact",
        body="Older fact body.",
        created_at="2026-04-30T00:00:00+00:00",
        updated_at="2026-04-30T00:00:00+00:00",
    )
    new_fact = store.create_record(
        project_id=project_id,
        session_id="sess_context_brief",
        kind="fact",
        title="Newer fact",
        body="Newer fact body.",
        created_at="2026-04-30T01:00:00+00:00",
        updated_at="2026-04-30T01:00:00+00:00",
    )

    candidates = load_candidate_records(store, project_id=project_id)

    fact_ids = [row["record_id"] for row in candidates if row["kind"] == "fact"]
    assert fact_ids[:2] == [new_fact["record_id"], old_fact["record_id"]]


def test_context_brief_draft_has_fixed_sections_with_sections_fallback():
    """Draft contract exposes fixed sections plus legacy section fallback."""
    assert [field.name for field in fields(ContextBriefDraft)] == [
        "summary",
        "start_here",
        "current_handoff",
        "decisions",
        "constraints_preferences",
        "operational_context",
        "project_facts",
        "open_risks",
        "follow_up_queries",
        "sections",
    ]


def test_validate_draft_rejects_wrong_kind_in_fixed_section():
    """Context brief sections must cite records whose kinds match the section."""
    draft = ContextBriefDraft(
        summary=(),
        decisions=(MemoryLine("Refund policy requires evidence", ("rec_fact",)),)
    )

    with pytest.raises(
        ValueError,
        match="context_brief_line_wrong_section:decisions:rec_fact:fact",
    ):
        validate_draft(
            draft,
            allowed_record_ids={"rec_fact"},
            record_kinds={"rec_fact": "fact"},
        )


def test_validate_draft_accepts_matching_fixed_section_kinds():
    """Decision, constraint/preference, and fact sections accept matching kinds."""
    draft = ContextBriefDraft(
        summary=(),
        decisions=(MemoryLine("Use SQLite", ("rec_decision",)),),
        constraints_preferences=(
            MemoryLine("Keep outputs concise", ("rec_preference",)),
            MemoryLine("Never store secrets", ("rec_constraint",)),
        ),
        project_facts=(
            MemoryLine("CLI code lives under src", ("rec_fact",)),
        ),
    )

    validate_draft(
        draft,
        allowed_record_ids={
            "rec_decision",
            "rec_preference",
            "rec_constraint",
            "rec_fact",
        },
        record_kinds={
            "rec_decision": "decision",
            "rec_preference": "preference",
            "rec_constraint": "constraint",
            "rec_fact": "fact",
        },
    )


def test_sanitize_draft_section_kinds_drops_mixed_kind_fixed_lines():
    """Compiler cleanup removes section lines that mix incompatible record kinds."""
    draft = ContextBriefDraft(
        summary=(),
        constraints_preferences=(
            MemoryLine("Constraint mixed with fact", ("rec_constraint", "rec_fact")),
            MemoryLine("Constraint only", ("rec_constraint",)),
        ),
        project_facts=(MemoryLine("Fact only", ("rec_fact",)),),
    )

    sanitized = sanitize_draft_section_kinds(
        draft,
        record_kinds={
            "rec_constraint": "constraint",
            "rec_fact": "fact",
        },
    )

    assert sanitized.constraints_preferences == (
        MemoryLine("Constraint only", ("rec_constraint",)),
    )
    assert sanitized.project_facts == (MemoryLine("Fact only", ("rec_fact",)),)


def test_trim_context_brief_draft_keeps_startup_brief_compact():
    """Compiler cleanup caps section size before manifest and markdown rendering."""
    draft = ContextBriefDraft(
        summary=tuple(MemoryLine(f"Summary {idx}", (f"rec_s{idx}",)) for idx in range(4)),
        start_here=tuple(MemoryLine(f"Start {idx}", (f"rec_st{idx}",)) for idx in range(7)),
        decisions=tuple(MemoryLine(f"Decision {idx}", (f"rec_d{idx}",)) for idx in range(20)),
        constraints_preferences=tuple(
            MemoryLine(f"Constraint {idx}", (f"rec_c{idx}",)) for idx in range(20)
        ),
        operational_context=tuple(
            MemoryLine(f"Workflow {idx}", (f"rec_o{idx}",)) for idx in range(20)
        ),
        project_facts=tuple(MemoryLine(f"Fact {idx}", (f"rec_f{idx}",)) for idx in range(20)),
        sections=(
            MemorySection(
                "Legacy",
                tuple(MemoryLine(f"Legacy {idx}", (f"rec_l{idx}",)) for idx in range(10)),
            ),
        ),
    )

    trimmed = trim_context_brief_draft(draft)

    assert len(trimmed.summary) == 2
    assert len(trimmed.start_here) == 4
    assert len(trimmed.decisions) == 8
    assert len(trimmed.constraints_preferences) == 8
    assert len(trimmed.operational_context) == 6
    assert len(trimmed.project_facts) == 6
    assert len(trimmed.sections[0].lines) == 6


def test_rendered_markdown_uses_fixed_section_order_and_sections_fallback(tmp_path):
    """Renderer emits fixed sections in order before legacy fallback sections."""
    repo = tmp_path / "repo"
    repo.mkdir()
    project = resolve_context_brief_project(
        config=replace(make_config(tmp_path / ".lerim"), projects={"repo": str(repo)}),
        cwd=repo,
    )
    draft = ContextBriefDraft(
        summary=(MemoryLine("Summary line", ("rec_summary",)),),
        start_here=(MemoryLine("Use the package directory", ("rec_start",)),),
        current_handoff=(MemoryLine("Resume the handoff", ("rec_handoff",)),),
        decisions=(MemoryLine("Keep SQLite canonical", ("rec_decision",)),),
        constraints_preferences=(
            MemoryLine("Respect the no-fallback rule", ("rec_constraint",)),
        ),
        operational_context=(
            MemoryLine("Rerun graph projection after changing graph payloads", ("rec_workflow",)),
        ),
        project_facts=(MemoryLine("CLI code lives under src", ("rec_fact",)),),
        open_risks=(MemoryLine("Review queue still has risk", ("rec_risk",)),),
        follow_up_queries=(MemoryLine("Query the newest records", ("rec_query",)),),
        sections=(
            MemorySection(
                "Legacy Fallback",
                (MemoryLine("Render older agent sections last", ("rec_legacy",)),),
            ),
        ),
    )
    candidate_records = [
        {
            "record_id": record_id,
            "kind": kind,
            "title": title,
            "updated_at": "2026-04-30T00:00:00+00:00",
        }
        for record_id, kind, title in (
            ("rec_summary", "decision", "Summary line"),
            ("rec_start", "fact", "Use the package directory"),
            ("rec_handoff", "episode", "Resume the handoff"),
            ("rec_decision", "decision", "Keep SQLite canonical"),
            ("rec_constraint", "constraint", "Respect the no-fallback rule"),
            ("rec_workflow", "fact", "Rerun graph projection after changing graph payloads"),
            ("rec_fact", "fact", "CLI code lives under src"),
            ("rec_risk", "episode", "Review queue still has risk"),
            ("rec_query", "fact", "Query the newest records"),
            ("rec_legacy", "episode", "Render older agent sections last"),
        )
    ]

    markdown = render_context_brief_markdown(
        project=project,
        generated_at="2026-04-30T00:00:00+00:00",
        previous_generated_at=None,
        generation_trigger="manual",
        records_considered=len(candidate_records),
        records_included=len(candidate_records),
        db_records_changed_since_previous=0,
        draft=draft,
        candidate_records=candidate_records,
    )

    headings = [
        "## Start Here",
        "## Summary",
        "## Continuation Handoff",
        "## Decisions",
        "## Constraints & Preferences",
        "## Reusable Workflows & Gotchas",
        "## Project Facts",
        "## Open Risks / Review Queue",
        "## Follow-up Queries",
        "## Legacy Fallback",
        "## Sources",
    ]
    positions = [_markdown_heading_index(markdown, heading) for heading in headings]
    assert positions == sorted(positions)
    assert "Rerun graph projection after changing graph payloads [rec_workflow]" in markdown
    assert "Render older agent sections last [rec_legacy]" in markdown


def test_rendered_markdown_contains_freshness_and_citations(tmp_path):
    """Renderer includes freshness fields and record citations."""
    repo = tmp_path / "repo"
    repo.mkdir()
    project = resolve_context_brief_project(
        config=replace(make_config(tmp_path / ".lerim"), projects={"repo": str(repo)}),
        cwd=repo,
    )
    draft = ContextBriefDraft(
        summary=(MemoryLine("Use generated Context Brief", ("rec_123",)),),
        sections=(
            MemorySection(
                "Decisions",
                (MemoryLine("Keep SQLite canonical", ("rec_456",)),),
            ),
        ),
    )

    markdown = render_context_brief_markdown(
        project=project,
        generated_at="2026-04-30T00:00:00+00:00",
        previous_generated_at="2026-04-29T00:00:00+00:00",
        generation_trigger="manual",
        records_considered=4,
        records_included=2,
        db_records_changed_since_previous=3,
        draft=draft,
        candidate_records=[
            {
                "record_id": "rec_123",
                "kind": "decision",
                "title": "Use generated Context Brief",
                "updated_at": "2026-04-30T00:00:00+00:00",
                "source_session_id": "sess_1",
            },
            {
                "record_id": "rec_456",
                "kind": "constraint",
                "record_role": "gotcha",
                "title": "Keep SQLite canonical",
                "updated_at": "2026-04-30T00:00:00+00:00",
                "source_session_id": "sess_2",
            },
        ],
    )

    assert "Records considered: 4" in markdown
    assert "Previous generation: `2026-04-29T00:00:00+00:00`" in markdown
    assert "Generation trigger: `manual`" in markdown
    assert "Records cited: 2" in markdown
    assert "DB records changed before this generation: 3" in markdown
    assert "Use generated Context Brief [rec_123]" in markdown
    assert "Keep SQLite canonical [rec_456]" in markdown
    assert "`rec_123` (decision" in markdown
    assert "`rec_456` (constraint, role gotcha" in markdown
    assert "source_session: `sess_1`" in markdown


def test_rendered_markdown_preserves_sources_when_body_is_long(tmp_path):
    """Renderer keeps source references even when the body must be truncated."""
    repo = tmp_path / "repo"
    repo.mkdir()
    project = resolve_context_brief_project(
        config=replace(make_config(tmp_path / ".lerim"), projects={"repo": str(repo)}),
        cwd=repo,
    )
    record_ids = tuple(f"rec_{idx:03d}" for idx in range(20))
    draft = ContextBriefDraft(
        summary=(MemoryLine("Summary item", (record_ids[0],)),),
        sections=tuple(
            MemorySection(
                f"Long Section {section_idx}",
                tuple(
                    MemoryLine(
                        f"Long detail {section_idx}-{idx}",
                        (record_ids[idx % len(record_ids)],),
                    )
                    for idx in range(14)
                ),
            )
            for section_idx in range(8)
        ),
    )
    candidates = [
        {
            "record_id": record_id,
            "kind": "decision",
            "title": f"Decision {idx}",
            "updated_at": "2026-04-30T00:00:00+00:00",
        }
        for idx, record_id in enumerate(record_ids)
    ]

    markdown = render_context_brief_markdown(
        project=project,
        generated_at="2026-04-30T00:00:00+00:00",
        previous_generated_at=None,
        generation_trigger="manual",
        records_considered=len(candidates),
        records_included=len(record_ids),
        db_records_changed_since_previous=0,
        draft=draft,
        candidate_records=candidates,
    )

    assert "## Sources" in markdown
    assert "`rec_000`" in markdown
    assert "Body truncated for startup size" in markdown
    assert "## Project Facts\n\n\n> Body truncated" not in markdown


def test_rendered_markdown_dedupes_and_skips_empty_cleaned_lines(tmp_path):
    """Renderer removes accidental inline IDs and skips duplicate/empty bullets."""
    repo = tmp_path / "repo"
    repo.mkdir()
    project = resolve_context_brief_project(
        config=replace(make_config(tmp_path / ".lerim"), projects={"repo": str(repo)}),
        cwd=repo,
    )
    draft = ContextBriefDraft(
        summary=(
            MemoryLine("Use SQLite rec_abc", ("rec_abc",)),
            MemoryLine("Use SQLite", ("rec_abc",)),
            MemoryLine("rec_abc", ("rec_abc",)),
        ),
        decisions=(
            MemoryLine("Use SQLite", ("rec_abc",)),
            MemoryLine("", ("rec_abc",)),
        ),
    )

    markdown = render_context_brief_markdown(
        project=project,
        generated_at="2026-04-30T00:00:00+00:00",
        previous_generated_at=None,
        generation_trigger="manual",
        records_considered=1,
        records_included=1,
        db_records_changed_since_previous=0,
        draft=draft,
        candidate_records=[
            {
                "record_id": "rec_abc",
                "kind": "decision",
                "title": "Use SQLite",
                "updated_at": "2026-04-30T00:00:00+00:00",
            }
        ],
    )

    assert markdown.count("Use SQLite [rec_abc]") == 1
    assert "- [rec_abc]" not in markdown


def test_changed_record_count_uses_versions_since_baseline(tmp_path, mock_embeddings):
    """Freshness count tracks created records after the generation baseline."""
    repo = tmp_path / "repo"
    repo.mkdir()
    store = ContextStore(tmp_path / "context.sqlite3")
    project_id = _register_seeded_project(store, repo)
    baseline = "2026-04-30T00:00:00+00:00"
    created = _create_record(
        store,
        project_id=project_id,
        kind="decision",
        title="Newer decision",
    )

    count = count_changed_records_since(store, project_id=project_id, since=baseline)

    assert count == 1
    assert created["record_id"]


def test_runtime_refresh_writes_dated_and_current_artifacts(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    mock_embeddings,
):
    """Runtime refresh writes run-local and stable current artifacts."""
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = replace(make_config(tmp_path / ".lerim"), projects={"repo": str(repo)})
    store = ContextStore(cfg.context_db_path)
    project_id = _register_seeded_project(store, repo)
    record = _create_record(
        store,
        project_id=project_id,
        kind="decision",
        title="Generate cited startup context",
    )
    monkeypatch.setattr(
        "lerim.config.providers.validate_provider_for_role",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "lerim.server.runtime.compile_context_brief",
        lambda **_kwargs: (
            ContextBriefDraft(
                summary=(
                    MemoryLine(
                        "Generate cited startup context",
                        (record["record_id"],),
                    ),
                ),
                sections=(),
            ),
            [],
        ),
    )

    runtime = LerimRuntime(default_cwd=str(repo), config=cfg)
    result = runtime.context_brief(repo_root=repo, project_name="repo", force=True)
    paths = context_brief_paths(cfg, project_id)

    assert result["status"] == "generated"
    assert paths.current_file.is_file()
    assert Path(result["run_folder"], "CONTEXT_BRIEF.md").is_file()
    assert record["record_id"] in paths.current_file.read_text(encoding="utf-8")


def test_cli_show_reads_existing_current_artifact(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    mock_embeddings,
) -> None:
    """CLI show prints live freshness before the current file without refresh."""
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = write_test_config(tmp_path, projects={"repo": str(repo)})
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    from lerim.config.settings import reload_config

    cfg = reload_config()
    store = ContextStore(cfg.context_db_path)
    project_id = _register_seeded_project(store, repo)
    paths = context_brief_paths(cfg, project_id)
    paths.current_dir.mkdir(parents=True)
    paths.current_file.write_text("# Context Brief\n\nhello\n", encoding="utf-8")
    paths.current_manifest.write_text(
        json.dumps(
            {
                "generated_at": "2026-04-30T00:00:00+00:00",
                "records_included": 0,
            }
        ),
        encoding="utf-8",
    )
    store.create_record(
        project_id=project_id,
        session_id="sess_context_brief",
        kind="decision",
        title="Fresh live context",
        body="Fresh live context body.",
        decision="Fresh live context",
        why="Because show should report live freshness.",
        created_at="2026-04-30T01:00:00+00:00",
        updated_at="2026-04-30T01:00:00+00:00",
    )
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        "lerim.server.cli.run_context_brief_for_project",
        lambda **_kwargs: pytest.fail("show must not refresh"),
    )

    code, output = run_cli(["context-brief", "show"])

    assert code == 0
    assert "Context Brief Live Status:" in output
    assert "- availability: stale" in output
    assert "- db_records_changed_since_generation: 1" in output
    assert "Refresh if newest persisted DB context matters." in output
    assert "hello" in output


def test_cli_status_json_reports_changed_records(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    mock_embeddings,
) -> None:
    """CLI status JSON exposes freshness metadata for scripts."""
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = write_test_config(tmp_path, projects={"repo": str(repo)})
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    from lerim.config.settings import reload_config

    cfg = reload_config()
    store = ContextStore(cfg.context_db_path)
    project_id = _register_seeded_project(store, repo)
    paths = context_brief_paths(cfg, project_id)
    paths.current_dir.mkdir(parents=True)
    paths.current_file.write_text("# Context Brief\n", encoding="utf-8")
    paths.current_manifest.write_text(
        json.dumps(
            {
                "generated_at": "2026-04-30T00:00:00+00:00",
                "records_included": 0,
                "run_folder": str(tmp_path / "run"),
            }
        ),
        encoding="utf-8",
    )
    _create_record(
        store,
        project_id=project_id,
        kind="decision",
        title="New startup context choice",
    )
    monkeypatch.chdir(repo)

    code, payload = run_cli_json(["context-brief", "status", "--json"])

    assert code == 0
    assert payload["availability"] == "stale"
    assert payload["records_changed_since_generation"] == 1


def test_cli_status_json_reports_missing_included_records(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    mock_embeddings,
) -> None:
    """A Context Brief is stale when its manifest cites records deleted from the DB."""
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = write_test_config(tmp_path, projects={"repo": str(repo)})
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    from lerim.config.settings import reload_config

    cfg = reload_config()
    store = ContextStore(cfg.context_db_path)
    project_id = _register_seeded_project(store, repo)
    missing_record_id = "rec_missing_context"
    paths = context_brief_paths(cfg, project_id)
    paths.current_dir.mkdir(parents=True)
    paths.current_file.write_text("# Context Brief\n\nstale citation\n", encoding="utf-8")
    paths.current_manifest.write_text(
        json.dumps(
                {
                    "generated_at": "2100-01-01T00:00:00+00:00",
                    "records_included": 1,
                    "included_record_ids": [missing_record_id],
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.chdir(repo)

    code, payload = run_cli_json(["context-brief", "status", "--json"])

    assert code == 0
    assert payload["availability"] == "stale"
    assert payload["records_changed_since_generation"] == 0
    assert payload["records_missing_since_generation"] == 1
    assert "cites records no longer present" in payload["suggested_action"]


def test_cli_show_reports_missing_included_records(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    mock_embeddings,
) -> None:
    """CLI show warns before printing a derived brief with missing cited records."""
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = write_test_config(tmp_path, projects={"repo": str(repo)})
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    from lerim.config.settings import reload_config

    cfg = reload_config()
    store = ContextStore(cfg.context_db_path)
    project_id = _register_seeded_project(store, repo)
    missing_record_id = "rec_missing_context"
    paths = context_brief_paths(cfg, project_id)
    paths.current_dir.mkdir(parents=True)
    paths.current_file.write_text("# Context Brief\n\nstale citation\n", encoding="utf-8")
    paths.current_manifest.write_text(
        json.dumps(
                {
                    "generated_at": "2100-01-01T00:00:00+00:00",
                    "records_included": 1,
                    "included_record_ids": [missing_record_id],
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.chdir(repo)

    code, output = run_cli(["context-brief", "show"])

    assert code == 0
    assert "- availability: stale" in output
    assert "- db_records_missing_since_generation: 1" in output
    assert "cites records no longer present" in output
    assert "stale citation" in output


def test_status_reports_error_when_manifest_missing(
    tmp_path,
    mock_embeddings,
) -> None:
    """A current markdown without manifest is an error, not stale."""
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = replace(make_config(tmp_path / ".lerim"), projects={"repo": str(repo)})
    store = ContextStore(cfg.context_db_path)
    project_id = _register_seeded_project(store, repo)
    paths = context_brief_paths(cfg, project_id)
    paths.current_dir.mkdir(parents=True)
    paths.current_file.write_text("# Context Brief\n", encoding="utf-8")
    _create_record(
        store,
        project_id=project_id,
        kind="decision",
        title="New record without manifest",
    )
    project = resolve_context_brief_project(config=cfg, cwd=repo)

    status = context_brief_status(config=cfg, store=store, project=project)

    assert status.availability == "error"
    assert status.suggested_action == "Run `lerim context-brief refresh --force`."
