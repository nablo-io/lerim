"""Skill stewardship API functions shared by CLI and HTTP routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lerim.config.settings import Config, get_config
from lerim.context import ContextStore, resolve_project_identity
from lerim.skill_stewardship.artifacts import artifact_name, scan_instruction_artifact
from lerim.skill_stewardship.patching import apply_proposal
from lerim.skill_stewardship.pipeline import guard_proposal, hydrate_patch_text, run_skill_stewardship_refresh
from lerim.skill_stewardship.repository import SkillStewardshipRepository
from lerim.skill_stewardship.schemas import AutoApplyPolicy, SkillProposalDraft
from lerim.skill_stewardship.validation import validate_proposal


def _context_store(config: Config) -> ContextStore:
    """Return the canonical global context store."""
    store = ContextStore(config.context_db_path)
    store.initialize()
    return store


def _registered_projects(config: Config) -> list[tuple[str, Path]]:
    """Return registered projects as resolved (name, path) pairs."""
    return [(name, Path(path).expanduser().resolve()) for name, path in config.projects.items()]


def _project_scope_for_path(config: Config, path: Path) -> tuple[str, str | None]:
    """Return project scope for paths inside registered projects, otherwise global."""
    resolved = path.expanduser().resolve()
    matches = [
        project_path
        for _name, project_path in _registered_projects(config)
        if resolved == project_path or project_path in resolved.parents
    ]
    if not matches:
        return "global", None
    project_path = max(matches, key=lambda item: len(item.parts))
    return "project", resolve_project_identity(project_path).project_id


def api_skill_target_add(
    *,
    path: str,
    name: str | None = None,
    description: str | None = None,
    update_mode: str | None = None,
) -> dict[str, Any]:
    """Register or refresh one instruction target from a filesystem path."""
    config = get_config()
    repository = SkillStewardshipRepository(_context_store(config))
    base, manifest, files = scan_instruction_artifact(path)
    resolved_path = Path(path).expanduser().resolve()
    scope_type, scope_id = _project_scope_for_path(config, resolved_path)
    target = repository.upsert_target(
        name=name or artifact_name(path, manifest),
        path=resolved_path,
        description=description,
        manifest=manifest,
        files=files,
        update_mode=update_mode,
        scope_type=scope_type,
        scope_id=scope_id,
    )
    return {
        "target": target.model_dump(),
        "base_path": str(base),
        "files": [item.model_dump(exclude={"text_preview"}) for item in files],
        "error": False,
    }


def api_skill_targets() -> dict[str, Any]:
    """List registered instruction targets with latest scanned files."""
    config = get_config()
    repository = SkillStewardshipRepository(_context_store(config))
    targets = []
    for target in repository.list_targets():
        files = repository.target_files(target.target_id)
        targets.append(
            {
                **target.model_dump(),
                "file_count": len(files),
                "files": [item.model_dump(exclude={"text_preview"}) for item in files],
            }
        )
    return {"targets": targets, "error": False}


def api_skill_target_show(target_id_or_name: str) -> dict[str, Any]:
    """Return one instruction target, files, and related proposals."""
    config = get_config()
    repository = SkillStewardshipRepository(_context_store(config))
    target = repository.get_target(target_id_or_name)
    return {
        "target": target.model_dump(),
        "files": [item.model_dump() for item in repository.target_files(target.target_id)],
        "proposals": [item.model_dump() for item in repository.list_proposals(target_id=target.target_id)],
        "error": False,
    }


def api_skill_target_mode(
    *,
    target_id_or_name: str,
    update_mode: str,
    auto_apply_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update target review mode and auto-apply policy."""
    config = get_config()
    repository = SkillStewardshipRepository(_context_store(config))
    existing = repository.get_target(target_id_or_name)
    policy = existing.auto_apply_policy
    if auto_apply_policy is not None:
        policy = AutoApplyPolicy.model_validate({**policy.model_dump(), **auto_apply_policy})
    target = repository.update_target_mode(target_id_or_name, update_mode, policy)
    return {"target": target.model_dump(), "error": False}


def api_skill_refresh(target_id_or_name: str, *, record_limit: int = 80) -> dict[str, Any]:
    """Run the LLM-backed skill stewardship proposal pipeline."""
    result = run_skill_stewardship_refresh(
        target_id_or_name,
        record_limit=record_limit,
        progress=False,
    )
    return {**result, "error": False}


def api_skill_proposals(
    *,
    target_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """List skill update proposals."""
    config = get_config()
    repository = SkillStewardshipRepository(_context_store(config))
    proposals = repository.list_proposals(target_id=target_id, status=status)
    return {"proposals": [proposal.model_dump() for proposal in proposals], "error": False}


def api_skill_proposal_show(proposal_id: str) -> dict[str, Any]:
    """Return one proposal with its target and files."""
    config = get_config()
    repository = SkillStewardshipRepository(_context_store(config))
    proposal = repository.get_proposal(proposal_id)
    target = repository.get_target(proposal.target_id)
    return {
        "proposal": proposal.model_dump(),
        "target": target.model_dump(),
        "files": [item.model_dump() for item in repository.target_files(target.target_id)],
        "error": False,
    }


def api_skill_proposal_apply(proposal_id: str) -> dict[str, Any]:
    """Apply a proposal after user confirmation."""
    config = get_config()
    repository = SkillStewardshipRepository(_context_store(config))
    proposal = repository.get_proposal(proposal_id)
    applied = apply_proposal(
        repository=repository,
        proposal=proposal,
        applied_by="user",
        workspace_root=config.global_data_dir / "workspace",
    )
    return {"proposal": applied.model_dump(), "error": False}


def api_skill_proposal_reject(proposal_id: str) -> dict[str, Any]:
    """Reject a proposal from CLI or dashboard review."""
    config = get_config()
    repository = SkillStewardshipRepository(_context_store(config))
    proposal = repository.set_proposal_status(proposal_id, "rejected")
    return {"proposal": proposal.model_dump(), "error": False}


def api_skill_proposal_update(proposal_id: str, patch_json: dict[str, Any]) -> dict[str, Any]:
    """Replace proposal patch JSON after dashboard editing."""
    config = get_config()
    repository = SkillStewardshipRepository(_context_store(config))
    proposal = repository.get_proposal(proposal_id)
    target = repository.get_target(proposal.target_id)
    if target.manifest is None:
        raise ValueError("target manifest is missing")
    base = Path(target.path).expanduser().resolve()
    if base.is_file():
        base = base.parent
    draft = hydrate_patch_text(base=base, draft=SkillProposalDraft.model_validate(patch_json))
    guard = guard_proposal(draft=draft, policy=target.auto_apply_policy, manifest=target.manifest)
    validation = validate_proposal(base_path=base, manifest=target.manifest, proposal=draft)
    updated = repository.update_proposal_patch(
        proposal_id=proposal_id,
        draft=draft,
        validation=validation,
        guard=guard,
    )
    return {"proposal": updated.model_dump(), "error": False}


def api_skill_runs(limit: int = 20) -> dict[str, Any]:
    """List recent skill stewardship runs."""
    config = get_config()
    repository = SkillStewardshipRepository(_context_store(config))
    return {"runs": repository.recent_runs(limit=limit), "error": False}
