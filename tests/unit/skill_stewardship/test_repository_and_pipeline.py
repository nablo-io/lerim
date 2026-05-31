"""Tests for skill stewardship persistence and deterministic guards."""

from __future__ import annotations

from pathlib import Path

import pytest

from lerim.context import ContextStore, ProjectIdentity
from lerim.skill_stewardship import patching
from lerim.skill_stewardship.artifacts import scan_instruction_artifact
from lerim.skill_stewardship.patching import apply_proposal
from lerim.skill_stewardship.pipeline import _candidate_records, guard_proposal, hydrate_patch_text
from lerim.skill_stewardship.repository import SkillStewardshipRepository
from lerim.skill_stewardship.schemas import AutoApplyPolicy, SkillPatch, SkillProposalDraft
from lerim.skill_stewardship.validation import validate_proposal


def test_repository_registers_target_and_files(tmp_path: Path) -> None:
    """Target registration persists a manifest and tracked files."""
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: Demo.\n---\n\nUse evidence.\n", encoding="utf-8")
    base, manifest, files = scan_instruction_artifact(skill)
    repository = SkillStewardshipRepository(ContextStore(tmp_path / "context.sqlite3"))

    target = repository.upsert_target(
        name="demo",
        path=base,
        description="Improve demo",
        manifest=manifest,
        files=files,
    )

    assert repository.get_target(target.target_id).name == "demo"
    assert repository.target_files(target.target_id)[0].relative_path == "SKILL.md"


def test_repository_preserves_mode_and_policy_when_reregistering(tmp_path: Path) -> None:
    """Target refresh keeps existing mode and policy unless the caller replaces them."""
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: Demo.\n---\n\nUse evidence.\n", encoding="utf-8")
    base, manifest, files = scan_instruction_artifact(skill)
    repository = SkillStewardshipRepository(ContextStore(tmp_path / "context.sqlite3"))
    target = repository.upsert_target(name="demo", path=base, description=None, manifest=manifest, files=files)
    repository.update_target_mode(
        target.target_id,
        "auto_apply",
        AutoApplyPolicy(enabled=True, max_added_lines=7),
    )

    refreshed = repository.upsert_target(name="demo", path=base, description=None, manifest=manifest, files=files)

    assert refreshed.update_mode == "auto_apply"
    assert refreshed.auto_apply_policy.enabled is True
    assert refreshed.auto_apply_policy.max_added_lines == 7


def test_repository_rejects_invalid_mode_before_write(tmp_path: Path) -> None:
    """Invalid update modes fail before poisoning persisted target rows."""
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: Demo.\n---\n\nUse evidence.\n", encoding="utf-8")
    base, manifest, files = scan_instruction_artifact(skill)
    repository = SkillStewardshipRepository(ContextStore(tmp_path / "context.sqlite3"))
    target = repository.upsert_target(name="demo", path=base, description=None, manifest=manifest, files=files)
    repository.update_target_mode(target.target_id, "auto_apply", AutoApplyPolicy(enabled=True))

    with pytest.raises(ValueError, match="invalid_update_mode"):
        repository.update_target_mode(target.target_id, "instant")

    assert repository.get_target(target.target_id).update_mode == "auto_apply"


def test_repository_refresh_removes_stale_tracked_files(tmp_path: Path) -> None:
    """A scan refresh deletes file rows that no longer belong to the target."""
    skill = tmp_path / "skill"
    references = skill / "references"
    references.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: Demo.\n---\n\nUse evidence.\n", encoding="utf-8")
    stale = references / "old.md"
    stale.write_text("Old guidance.\n", encoding="utf-8")
    base, manifest, files = scan_instruction_artifact(skill)
    repository = SkillStewardshipRepository(ContextStore(tmp_path / "context.sqlite3"))
    target = repository.upsert_target(name="demo", path=base, description=None, manifest=manifest, files=files)
    stale.unlink()
    base, manifest, files = scan_instruction_artifact(skill)

    repository.upsert_target(name="demo", path=base, description=None, manifest=manifest, files=files)

    assert {item.relative_path for item in repository.target_files(target.target_id)} == {"SKILL.md"}


def test_guard_allows_low_risk_auto_apply_with_evidence() -> None:
    """Low-risk bounded edits can become auto-apply eligible when policy allows it."""
    draft = SkillProposalDraft(
        title="Add simplification guard",
        summary="Adds one reusable simplification reminder.",
        risk_level="low",
        patches=[
            SkillPatch(
                relative_path="references/simplification.md",
                change_type="create",
                risk="low",
                rationale="Supported by repeated records.",
                evidence_record_ids=["rec_123"],
                after_text="Avoid pass-through wrappers that only call one function.\n",
                diff_text="",
            )
        ],
    )

    result = guard_proposal(draft=draft, policy=AutoApplyPolicy(enabled=True))

    assert result.accepted is True
    assert result.auto_apply_eligible is True


def test_guard_respects_frontmatter_auto_apply_policy(tmp_path: Path) -> None:
    """Frontmatter edits can be reviewed but do not auto-apply under the default policy."""
    skill = tmp_path / "skill"
    skill.mkdir()
    original = "---\nname: demo\ndescription: Demo.\n---\n\nUse evidence.\n"
    changed = "---\nname: demo\ndescription: Changed.\n---\n\nUse evidence.\n"
    (skill / "SKILL.md").write_text(original, encoding="utf-8")
    _base, manifest, _files = scan_instruction_artifact(skill)
    draft = SkillProposalDraft(
        title="Adjust metadata",
        summary="Changes metadata.",
        risk_level="low",
        patches=[
            SkillPatch(
                relative_path="SKILL.md",
                change_type="modify",
                risk="low",
                rationale="Supported by record.",
                evidence_record_ids=["rec_1"],
                before_text=original,
                after_text=changed,
                diff_text="",
            )
        ],
    )

    result = guard_proposal(draft=draft, policy=AutoApplyPolicy(enabled=True), manifest=manifest)

    assert result.accepted is True
    assert result.auto_apply_eligible is False
    assert any("frontmatter" in reason for reason in result.reasons)


def test_guard_blocks_large_removals_from_auto_apply() -> None:
    """Deletion-heavy patches stay manual even when net added lines are small."""
    before = "\n".join(f"line {index}" for index in range(30)) + "\n"
    draft = SkillProposalDraft(
        title="Shrink guidance",
        summary="Removes most existing guidance.",
        risk_level="low",
        patches=[
            SkillPatch(
                relative_path="AGENTS.md",
                change_type="modify",
                risk="low",
                rationale="Supported by record.",
                evidence_record_ids=["rec_1"],
                before_text=before,
                after_text="keep one line\n",
                diff_text="",
            )
        ],
    )

    result = guard_proposal(draft=draft, policy=AutoApplyPolicy(enabled=True, max_removed_lines=5))

    assert result.accepted is True
    assert result.auto_apply_eligible is False
    assert any("removed lines" in reason for reason in result.reasons)


def test_guard_blocks_missing_evidence() -> None:
    """A patch without record evidence stays out of the review path."""
    draft = SkillProposalDraft(
        title="Unsupported change",
        summary="No evidence.",
        risk_level="low",
        patches=[
            SkillPatch(
                relative_path="SKILL.md",
                change_type="modify",
                risk="low",
                rationale="Unsupported.",
                evidence_record_ids=[],
                after_text="text",
                diff_text="",
            )
        ],
    )

    result = guard_proposal(draft=draft, policy=AutoApplyPolicy(enabled=True))

    assert result.accepted is False
    assert result.auto_apply_eligible is False


def test_validation_rejects_frontmatter_removal(tmp_path: Path) -> None:
    """Entry file edits must preserve existing YAML frontmatter."""
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: Demo.\n---\n\n# Demo\n", encoding="utf-8")
    base, manifest, _files = scan_instruction_artifact(skill)
    draft = SkillProposalDraft(
        title="Bad edit",
        summary="Drops metadata.",
        risk_level="low",
        patches=[
            SkillPatch(
                relative_path="SKILL.md",
                change_type="modify",
                risk="low",
                rationale="Bad.",
                evidence_record_ids=["rec_1"],
                before_text="---\nname: demo\ndescription: Demo.\n---\n\n# Demo\n",
                after_text="# Demo\n\nUpdated.\n",
                diff_text="",
            )
        ],
    )

    result = validate_proposal(base_path=base, manifest=manifest, proposal=draft)

    assert result.ok is False
    assert any("frontmatter" in error for error in result.errors)


def test_validation_rejects_empty_instruction_body(tmp_path: Path) -> None:
    """Instruction edits cannot reduce the target to an empty body."""
    path = tmp_path / "AGENTS.md"
    path.write_text("# Project Instructions\n\nRun tests.\n", encoding="utf-8")
    base, manifest, _files = scan_instruction_artifact(path)
    draft = SkillProposalDraft(
        title="Empty instructions",
        summary="Accidentally deletes all guidance.",
        risk_level="low",
        patches=[
            SkillPatch(
                relative_path="AGENTS.md",
                change_type="modify",
                risk="low",
                rationale="Bad.",
                evidence_record_ids=["rec_1"],
                before_text="# Project Instructions\n\nRun tests.\n",
                after_text="",
                diff_text="",
            )
        ],
    )

    result = validate_proposal(base_path=base, manifest=manifest, proposal=draft)

    assert result.ok is False
    assert any("instruction body cannot be empty" in error for error in result.errors)


def test_validation_rejects_duplicate_patch_paths(tmp_path: Path) -> None:
    """One proposal cannot contain competing edits for the same file."""
    skill = tmp_path / "skill"
    skill.mkdir()
    original = "---\nname: demo\ndescription: Demo.\n---\n\nUse evidence.\n"
    (skill / "SKILL.md").write_text(original, encoding="utf-8")
    base, manifest, _files = scan_instruction_artifact(skill)
    patch = SkillPatch(
        relative_path="SKILL.md",
        change_type="modify",
        risk="low",
        rationale="Supported.",
        evidence_record_ids=["rec_1"],
        before_text=original,
        after_text=original + "Keep updates small.\n",
        diff_text="",
    )
    draft = SkillProposalDraft(
        title="Duplicate",
        summary="Contains duplicate patches.",
        risk_level="low",
        patches=[patch, patch],
    )

    result = validate_proposal(base_path=base, manifest=manifest, proposal=draft)

    assert result.ok is False
    assert any("duplicate patch path" in error for error in result.errors)


def test_validation_rejects_modify_for_missing_file(tmp_path: Path) -> None:
    """Missing files must be explicit create patches, not silent modify writes."""
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: Demo.\n---\n\n# Demo\n", encoding="utf-8")
    base, manifest, _files = scan_instruction_artifact(skill)
    draft = SkillProposalDraft(
        title="Bad missing edit",
        summary="Tries to modify a missing file.",
        risk_level="low",
        patches=[
            SkillPatch(
                relative_path="references/new.md",
                change_type="modify",
                risk="low",
                rationale="Bad.",
                evidence_record_ids=["rec_1"],
                before_text="",
                after_text="New guidance.\n",
                diff_text="",
            )
        ],
    )

    result = validate_proposal(base_path=base, manifest=manifest, proposal=draft)

    assert result.ok is False
    assert any("missing file must use create" in error for error in result.errors)


def test_validation_rejects_existing_file_outside_registered_artifact(tmp_path: Path) -> None:
    """File targets cannot write unrelated siblings in the containing directory."""
    path = tmp_path / "AGENTS.md"
    path.write_text("# Project Instructions\n\nRun tests.\n", encoding="utf-8")
    sibling = tmp_path / "README.md"
    sibling.write_text("# Project\n", encoding="utf-8")
    base, manifest, _files = scan_instruction_artifact(path)
    draft = SkillProposalDraft(
        title="Bad sibling edit",
        summary="Tries to edit an unregistered sibling file.",
        risk_level="low",
        patches=[
            SkillPatch(
                relative_path="README.md",
                change_type="modify",
                risk="low",
                rationale="Bad.",
                evidence_record_ids=["rec_1"],
                before_text="# Project\n",
                after_text="# Project\n\nNew instructions.\n",
                diff_text="",
            )
        ],
    )

    result = validate_proposal(base_path=base, manifest=manifest, proposal=draft)

    assert result.ok is False
    assert any("not part of the registered instruction artifact" in error for error in result.errors)


def test_guard_keeps_unregistered_paths_out_of_auto_apply(tmp_path: Path) -> None:
    """The auto-apply guard refuses paths outside the scanned instruction artifact."""
    path = tmp_path / "AGENTS.md"
    path.write_text("# Project Instructions\n\nRun tests.\n", encoding="utf-8")
    _base, manifest, _files = scan_instruction_artifact(path)
    draft = SkillProposalDraft(
        title="Bad sibling edit",
        summary="Tries to edit an unregistered sibling file.",
        risk_level="low",
        patches=[
            SkillPatch(
                relative_path="README.md",
                change_type="modify",
                risk="low",
                rationale="Bad.",
                evidence_record_ids=["rec_1"],
                after_text="# Project\n\nNew instructions.\n",
                diff_text="",
            )
        ],
    )

    result = guard_proposal(draft=draft, policy=AutoApplyPolicy(enabled=True), manifest=manifest)

    assert result.accepted is True
    assert result.auto_apply_eligible is False
    assert any("outside registered instruction artifact" in reason for reason in result.reasons)


def test_hydrate_patch_text_recomputes_before_text_and_diff(tmp_path: Path) -> None:
    """Edited proposals get fresh file content and unified diffs before validation."""
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("Use simple code.\n", encoding="utf-8")
    draft = SkillProposalDraft(
        title="Add wrapper guidance",
        summary="Adds wrapper review guidance.",
        risk_level="low",
        patches=[
            SkillPatch(
                relative_path="SKILL.md",
                change_type="modify",
                risk="low",
                rationale="Supported by record.",
                evidence_record_ids=["rec_1"],
                after_text="Use simple code.\nRemove wrappers that only forward.\n",
                diff_text="stale",
            )
        ],
    )

    hydrated = hydrate_patch_text(base=skill, draft=draft)

    assert hydrated.patches[0].before_text == "Use simple code.\n"
    assert "--- a/SKILL.md" in hydrated.patches[0].diff_text
    assert "+Remove wrappers that only forward." in hydrated.patches[0].diff_text


def test_apply_rejects_non_review_proposal(tmp_path: Path) -> None:
    """Rejected proposals cannot be applied later by CLI or dashboard calls."""
    repository, proposal = _saved_valid_proposal(tmp_path, status="rejected")

    with pytest.raises(ValueError, match="status cannot be applied"):
        apply_proposal(repository=repository, proposal=proposal, applied_by="test")


def test_apply_rejects_failed_validation_payload(tmp_path: Path) -> None:
    """A pending proposal with failed validation metadata cannot write files."""
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: Demo.\n---\n\nUse evidence.\n", encoding="utf-8")
    base, manifest, files = scan_instruction_artifact(skill)
    repository = SkillStewardshipRepository(ContextStore(tmp_path / "context.sqlite3"))
    target = repository.upsert_target(name="demo", path=base, description=None, manifest=manifest, files=files)
    draft = SkillProposalDraft(
        title="Invalid edit",
        summary="Tries to modify a missing file.",
        risk_level="low",
        patches=[
            SkillPatch(
                relative_path="references/missing.md",
                change_type="modify",
                risk="low",
                rationale="Bad.",
                evidence_record_ids=["rec_1"],
                after_text="New guidance.\n",
                diff_text="",
            )
        ],
    )
    hydrated = hydrate_patch_text(base=base, draft=draft)
    proposal = repository.save_proposal(
        run_id=repository.create_run(target.target_id),
        target_id=target.target_id,
        draft=hydrated,
        guard=guard_proposal(draft=hydrated, policy=AutoApplyPolicy(enabled=True), manifest=manifest),
        validation=validate_proposal(base_path=base, manifest=manifest, proposal=hydrated),
        status="pending_review",
    )

    with pytest.raises(ValueError, match="validation has not passed"):
        apply_proposal(repository=repository, proposal=proposal, applied_by="test")


def test_apply_rejects_stale_patch_baseline(tmp_path: Path) -> None:
    """Apply fails when the target file changed after proposal generation."""
    repository, proposal = _saved_valid_proposal(tmp_path, status="pending_review")
    target = repository.get_target(proposal.target_id)
    Path(target.path, "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo.\n---\n\nChanged elsewhere.\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="target changed"):
        apply_proposal(repository=repository, proposal=proposal, applied_by="test")


def test_repository_edit_updates_visible_proposal_metadata(tmp_path: Path) -> None:
    """Proposal list metadata follows the edited patch payload."""
    repository, proposal = _saved_valid_proposal(tmp_path, status="pending_review")
    target = repository.get_target(proposal.target_id)
    draft = SkillProposalDraft.model_validate(
        {
            **proposal.patch_json,
            "title": "Edited title",
            "summary": "Edited summary.",
            "risk_level": "medium",
        }
    )

    updated = repository.update_proposal_patch(
        proposal_id=proposal.proposal_id,
        draft=draft,
        validation=validate_proposal(base_path=Path(target.path), manifest=target.manifest, proposal=draft),
        guard=guard_proposal(draft=draft, policy=target.auto_apply_policy, manifest=target.manifest),
    )

    assert updated.title == "Edited title"
    assert updated.summary == "Edited summary."
    assert updated.risk_level == "medium"


def test_repository_rejects_edits_to_terminal_proposals(tmp_path: Path) -> None:
    """Applied/rejected proposals cannot be resurrected by patch edits."""
    repository, proposal = _saved_valid_proposal(tmp_path, status="applied")
    draft = SkillProposalDraft.model_validate(proposal.patch_json)
    target = repository.get_target(proposal.target_id)

    with pytest.raises(ValueError, match="cannot be edited"):
        repository.update_proposal_patch(
            proposal_id=proposal.proposal_id,
            draft=draft,
            validation=validate_proposal(base_path=Path(target.path), manifest=target.manifest, proposal=draft),
            guard=guard_proposal(draft=draft, policy=target.auto_apply_policy, manifest=target.manifest),
        )

    assert repository.get_proposal(proposal.proposal_id).status == "applied"


def test_repository_rejects_terminal_status_transition(tmp_path: Path) -> None:
    """Rejected proposals stay terminal."""
    repository, proposal = _saved_valid_proposal(tmp_path, status="rejected")

    with pytest.raises(ValueError, match="terminal"):
        repository.set_proposal_status(proposal.proposal_id, "pending_review")

    assert repository.get_proposal(proposal.proposal_id).status == "rejected"


def test_apply_rolls_back_files_when_metadata_write_fails(monkeypatch, tmp_path: Path) -> None:
    """A failed multi-file apply restores every file it already touched."""
    skill = tmp_path / "skill"
    references = skill / "references"
    references.mkdir(parents=True)
    skill_text = "---\nname: demo\ndescription: Demo.\n---\n\nUse evidence.\n"
    reference_text = "Old reference.\n"
    (skill / "SKILL.md").write_text(skill_text, encoding="utf-8")
    (references / "notes.md").write_text(reference_text, encoding="utf-8")
    base, manifest, files = scan_instruction_artifact(skill)
    repository = SkillStewardshipRepository(ContextStore(tmp_path / "context.sqlite3"))
    target = repository.upsert_target(name="demo", path=base, description=None, manifest=manifest, files=files)
    draft = SkillProposalDraft(
        title="Two edits",
        summary="Edits two files.",
        risk_level="low",
        patches=[
            SkillPatch(
                relative_path="SKILL.md",
                change_type="modify",
                risk="low",
                rationale="Supported.",
                evidence_record_ids=["rec_1"],
                after_text=skill_text + "Keep updates small.\n",
                diff_text="",
            ),
            SkillPatch(
                relative_path="references/notes.md",
                change_type="modify",
                risk="low",
                rationale="Supported.",
                evidence_record_ids=["rec_1"],
                after_text="New reference.\n",
                diff_text="",
            ),
        ],
    )
    hydrated = hydrate_patch_text(base=base, draft=draft)
    proposal = repository.save_proposal(
        run_id=repository.create_run(target.target_id),
        target_id=target.target_id,
        draft=hydrated,
        guard=guard_proposal(draft=hydrated, policy=AutoApplyPolicy(enabled=True), manifest=manifest),
        validation=validate_proposal(base_path=base, manifest=manifest, proposal=hydrated),
        status="pending_review",
    )
    monkeypatch.setattr(
        patching,
        "_record_applied_versions",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("db write failed")),
    )

    with pytest.raises(RuntimeError, match="db write failed"):
        apply_proposal(repository=repository, proposal=proposal, applied_by="test")

    assert (skill / "SKILL.md").read_text(encoding="utf-8") == skill_text
    assert (references / "notes.md").read_text(encoding="utf-8") == reference_text
    assert repository.get_proposal(proposal.proposal_id).status == "pending_review"


def test_stale_apply_does_not_restore_already_applied_files(monkeypatch, tmp_path: Path) -> None:
    """A stale concurrent apply exits before writes, leaving applied disk content alone."""
    repository, proposal = _saved_valid_proposal(tmp_path, status="pending_review")
    target = repository.get_target(proposal.target_id)
    draft = SkillProposalDraft.model_validate(proposal.patch_json)
    applied_text = draft.patches[0].after_text
    target_file = Path(target.path, "SKILL.md")
    target_file.write_text(applied_text, encoding="utf-8")
    repository.set_proposal_status(proposal.proposal_id, "applied")
    monkeypatch.setattr(
        patching,
        "_prepare_patches",
        lambda **_kwargs: pytest.fail("stale apply should not prepare or restore files"),
    )

    with pytest.raises(ValueError, match="status cannot be applied"):
        apply_proposal(repository=repository, proposal=proposal, applied_by="second")

    assert target_file.read_text(encoding="utf-8") == applied_text


def test_apply_snapshots_under_workspace_root(tmp_path: Path) -> None:
    """Snapshots are run artifacts under the canonical workspace tree."""
    repository, proposal = _saved_valid_proposal(tmp_path, status="pending_review")
    workspace = tmp_path / "workspace"

    apply_proposal(repository=repository, proposal=proposal, applied_by="test", workspace_root=workspace)

    with repository.context_store.connect() as conn:
        row = conn.execute("SELECT snapshot_path FROM instruction_versions").fetchone()
    assert row is not None
    assert str(row["snapshot_path"]).startswith(str(workspace / "skill-snapshots"))


def test_candidate_records_can_be_project_scoped(tmp_path: Path) -> None:
    """Skill refresh candidates stay inside the target project when scoped."""
    store = ContextStore(tmp_path / "context.sqlite3")
    store.initialize()
    store.register_project(ProjectIdentity(project_id="proj_a", project_slug="a", repo_path=tmp_path / "a"))
    store.register_project(ProjectIdentity(project_id="proj_b", project_slug="b", repo_path=tmp_path / "b"))
    store.create_record(
        project_id="proj_a",
        session_id=None,
        kind="fact",
        title="A procedure",
        body="A body.",
        record_role="procedure",
        scope_type="project",
        scope_id="proj_a",
        scope_label="a",
    )
    store.create_record(
        project_id="proj_b",
        session_id=None,
        kind="fact",
        title="B procedure",
        body="B body.",
        record_role="procedure",
        scope_type="project",
        scope_id="proj_b",
        scope_label="b",
    )

    rows = _candidate_records(store, limit=10, project_ids=["proj_a"])

    assert {row["project_id"] for row in rows} == {"proj_a"}


def _saved_valid_proposal(tmp_path: Path, *, status: str):
    """Create a repository and one valid persisted proposal for apply tests."""
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: Demo.\n---\n\nUse evidence.\n", encoding="utf-8")
    base, manifest, files = scan_instruction_artifact(skill)
    repository = SkillStewardshipRepository(ContextStore(tmp_path / "context.sqlite3"))
    target = repository.upsert_target(name="demo", path=base, description=None, manifest=manifest, files=files)
    draft = SkillProposalDraft(
        title="Improve evidence",
        summary="Adds one evidence reminder.",
        risk_level="low",
        patches=[
            SkillPatch(
                relative_path="SKILL.md",
                change_type="modify",
                risk="low",
                rationale="Supported by record.",
                evidence_record_ids=["rec_1"],
                after_text="---\nname: demo\ndescription: Demo.\n---\n\nUse evidence.\nKeep updates small.\n",
                diff_text="",
            )
        ],
    )
    hydrated = hydrate_patch_text(base=base, draft=draft)
    proposal = repository.save_proposal(
        run_id=repository.create_run(target.target_id),
        target_id=target.target_id,
        draft=hydrated,
        guard=guard_proposal(draft=hydrated, policy=AutoApplyPolicy(enabled=True), manifest=manifest),
        validation=validate_proposal(base_path=base, manifest=manifest, proposal=hydrated),
        status=status,
    )
    return repository, proposal
