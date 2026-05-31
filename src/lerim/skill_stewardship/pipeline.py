"""LLM-backed pipeline for proposing instruction artifact updates."""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

from lerim.agents.dspy_compat import dspy
from lerim.agents.model_helpers import call_model_step, prediction_payload
from lerim.agents.model_runtime import ModelRuntime, build_model_runtime
from lerim.config.settings import Config, get_config
from lerim.context import ContextStore, resolve_project_identity
from lerim.skill_stewardship.artifacts import manifest_json, read_target_text, scan_instruction_artifact
from lerim.skill_stewardship.patching import apply_proposal
from lerim.skill_stewardship.repository import SkillStewardshipRepository
from lerim.skill_stewardship.schemas import (
    AutoApplyPolicy,
    ProposalGuardResult,
    SkillPatch,
    SkillProposal,
    SkillProposalDraft,
    ArtifactManifest,
)
from lerim.skill_stewardship.signatures import CompileSkillUpdateProposal
from lerim.skill_stewardship.validation import frontmatter_block, path_belongs_to_manifest, validate_proposal

DEFAULT_RECORD_LIMIT = 80
PREFERRED_RECORD_ROLES = [
    "procedure",
    "gotcha",
    "failure_mode",
    "artifact",
    "state_change",
    "eval_asset",
]


class SkillStewardshipPipeline(dspy.Module):
    """Scan one registered target and create evidence-backed update proposals."""

    def __init__(
        self,
        *,
        repository: SkillStewardshipRepository,
        config: Config,
        runtime: ModelRuntime | None = None,
        compile_step: Any | None = None,
        progress: bool = False,
    ) -> None:
        """Create the stewardship pipeline."""
        super().__init__()
        self.repository = repository
        self.config = config
        self.runtime = runtime
        self.compile_step = compile_step or dspy.Predict(CompileSkillUpdateProposal)
        self.progress = progress
        self.adapter = dspy.JSONAdapter()

    def forward(self, target_id_or_name: str, *, record_limit: int = DEFAULT_RECORD_LIMIT) -> dict[str, Any]:
        """Run one target refresh and return run/proposal metadata."""
        target = self.repository.get_target(target_id_or_name)
        run_id = self.repository.create_run(target.target_id)
        proposals: list[SkillProposal] = []
        applied = 0
        try:
            base, manifest, files = scan_instruction_artifact(target.path)
            refreshed = self.repository.upsert_target(
                name=target.name,
                path=Path(target.path),
                description=target.description,
                manifest=manifest,
                files=files,
                update_mode=target.update_mode,
                auto_apply_policy=target.auto_apply_policy,
                scope_type=target.scope_type,
                scope_id=target.scope_id,
            )
            records = _candidate_records(
                self.repository.context_store,
                limit=record_limit,
                project_ids=_target_project_ids(self.config, refreshed),
            )
            draft = self._compile(target=refreshed, base=base, manifest=manifest, files=files, records=records)
            draft = hydrate_patch_text(base=base, draft=draft)
            guard = guard_proposal(draft=draft, policy=refreshed.auto_apply_policy, manifest=manifest)
            validation = validate_proposal(base_path=base, manifest=manifest, proposal=draft)
            signals = self.repository.save_signals(run_id=run_id, target_id=refreshed.target_id, draft=draft)
            if draft.patches and guard.accepted:
                status = "pending_review" if validation.ok else "failed_validation"
                proposal = self.repository.save_proposal(
                    run_id=run_id,
                    target_id=refreshed.target_id,
                    draft=draft,
                    guard=guard,
                    validation=validation,
                    status=status,
                )
                proposals.append(proposal)
                if validation.ok and _should_auto_apply(refreshed.update_mode, refreshed.auto_apply_policy, proposal):
                    proposals[-1] = apply_proposal(
                        repository=self.repository,
                        proposal=proposal,
                        applied_by="auto_apply",
                        workspace_root=self.config.global_data_dir / "workspace",
                    )
                    applied += 1
            self.repository.finish_run(
                run_id=run_id,
                status="completed",
                records_scanned=len(records),
                signals_created=signals,
                proposals_created=len(proposals),
                proposals_applied=applied,
            )
            return {
                "run_id": run_id,
                "target_id": refreshed.target_id,
                "records_scanned": len(records),
                "signals_created": signals,
                "proposals": [proposal.model_dump() for proposal in proposals],
                "applied": applied,
            }
        except Exception as exc:
            self.repository.finish_run(
                run_id=run_id,
                status="failed",
                records_scanned=0,
                signals_created=0,
                proposals_created=0,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

    def _compile(
        self,
        *,
        target: Any,
        base: Path,
        manifest: Any,
        files: Any,
        records: list[dict[str, Any]],
    ) -> SkillProposalDraft:
        """Call the model to compile one proposal draft."""
        if self.runtime is None:
            self.runtime = build_model_runtime(config=self.config)
        file_payload = [
            {
                "relative_path": item.relative_path,
                "file_role": item.file_role,
                "risk_surface": item.risk_surface,
                "text": item.text_preview,
            }
            for item in files
            if item.text_preview is not None
        ]
        with dspy.context(lm=self.runtime.lm, adapter=self.adapter):
            result, _, _attempts = call_model_step(
                lambda: self.compile_step(
                    target_json=target.model_dump_json(),
                    manifest_json=manifest_json(manifest, files),
                    files_json=json.dumps(file_payload, ensure_ascii=True),
                    records_json=json.dumps(records, ensure_ascii=True),
                ),
                stage="skill_update_compile",
                progress=self.progress,
                progress_label="skill-stewardship",
                validate_result=_validate_compile_result,
                validation_retry_target="complete SkillProposalDraft JSON object",
            )
        payload = prediction_payload(result, output_field="proposal")
        return SkillProposalDraft.model_validate(payload)


def run_skill_stewardship_refresh(
    target_id_or_name: str,
    *,
    config: Config | None = None,
    record_limit: int = DEFAULT_RECORD_LIMIT,
    progress: bool = False,
) -> dict[str, Any]:
    """Refresh one registered target using the configured agent model."""
    effective_config = config or get_config()
    repository = SkillStewardshipRepository(ContextStore(effective_config.context_db_path))
    pipeline = SkillStewardshipPipeline(repository=repository, config=effective_config, progress=progress)
    return pipeline(target_id_or_name, record_limit=record_limit)


def guard_proposal(
    *,
    draft: SkillProposalDraft,
    policy: AutoApplyPolicy,
    manifest: ArtifactManifest | None = None,
) -> ProposalGuardResult:
    """Apply deterministic safety gates to a model-authored proposal."""
    reasons: list[str] = []
    if not draft.patches:
        return ProposalGuardResult(accepted=False, risk_level="low", auto_apply_eligible=False, reasons=["no_patch"])
    risk = _highest_risk([draft.risk_level, *(patch.risk for patch in draft.patches)])
    added_lines = sum(
        max(0, len((patch.after_text or "").splitlines()) - len((patch.before_text or "").splitlines()))
        for patch in draft.patches
    )
    removed_lines = sum(
        max(0, len((patch.before_text or "").splitlines()) - len((patch.after_text or "").splitlines()))
        for patch in draft.patches
    )
    if not _risk_allowed(risk, policy.max_risk):
        reasons.append(f"risk {risk} exceeds auto-apply max_risk={policy.max_risk}")
    if len(draft.patches) > policy.max_changed_files:
        reasons.append(f"changed files exceed auto-apply max_changed_files={policy.max_changed_files}")
    if added_lines > policy.max_added_lines:
        reasons.append(f"added lines exceed auto-apply max_added_lines={policy.max_added_lines}")
    if removed_lines > policy.max_removed_lines:
        reasons.append(f"removed lines exceed auto-apply max_removed_lines={policy.max_removed_lines}")
    for patch in draft.patches:
        if not patch.evidence_record_ids:
            reasons.append(f"{patch.relative_path}: missing evidence")
        reasons.extend(_patch_policy_reasons(patch=patch, policy=policy, manifest=manifest))
    auto_apply = (
        policy.enabled
        and _risk_allowed(risk, policy.max_risk)
        and len(draft.patches) <= policy.max_changed_files
        and added_lines <= policy.max_added_lines
        and removed_lines <= policy.max_removed_lines
        and not reasons
    )
    return ProposalGuardResult(
        accepted=all("missing evidence" not in reason for reason in reasons),
        risk_level=risk,
        auto_apply_eligible=auto_apply,
        reasons=reasons,
    )


def _patch_policy_reasons(
    *,
    patch: SkillPatch,
    policy: AutoApplyPolicy,
    manifest: ArtifactManifest | None,
) -> list[str]:
    """Return policy reasons that keep one patch out of automatic application."""
    reasons: list[str] = []
    path = Path(patch.relative_path)
    first = path.parts[0] if path.parts else ""
    if patch.risk == "high":
        reasons.append(f"{patch.relative_path}: high-risk changes require manual review")
    if manifest and not path_belongs_to_manifest(manifest, patch.relative_path, change_type=patch.change_type):
        reasons.append(f"{patch.relative_path}: outside registered instruction artifact")
    if manifest and patch.relative_path == manifest.entry_file and not policy.allow_entry_file_body:
        reasons.append(f"{patch.relative_path}: entry-file body changes are blocked from auto-apply")
    if frontmatter_block(str(patch.before_text or "")) != frontmatter_block(patch.after_text) and not policy.allow_frontmatter:
        reasons.append(f"{patch.relative_path}: frontmatter changes are blocked from auto-apply")
    if patch.change_type == "create" and first in {"references", "reference", "examples"} and not policy.allow_new_reference_files:
        reasons.append(f"{patch.relative_path}: new reference/example files are blocked from auto-apply")
    if first == "scripts" and not policy.allow_scripts:
        reasons.append(f"{patch.relative_path}: scripts are blocked from auto-apply")
    if first == "assets" and not policy.allow_assets:
        reasons.append(f"{patch.relative_path}: assets are blocked from auto-apply")
    if _is_config_path(path) and not policy.allow_config_files:
        reasons.append(f"{patch.relative_path}: config files are blocked from auto-apply")
    return reasons


def _candidate_records(store: ContextStore, *, limit: int, project_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Load high-value operational records for proposal generation."""
    store.initialize()
    role_rows: list[dict[str, Any]] = []
    for role in PREFERRED_RECORD_ROLES:
        result = store.query(
            entity="records",
            mode="list",
            project_ids=project_ids,
            record_role=role,
            status="active",
            order_by="updated_at",
            limit=max(1, limit // len(PREFERRED_RECORD_ROLES)),
            include_total=False,
        )
        role_rows.extend(result.get("rows") or [])
    if len(role_rows) >= limit:
        return role_rows[:limit]
    result = store.query(
        entity="records",
        mode="list",
        project_ids=project_ids,
        status="active",
        order_by="updated_at",
        limit=limit - len(role_rows),
        include_total=False,
    )
    seen = {row.get("record_id") for row in role_rows}
    return role_rows + [row for row in result.get("rows") or [] if row.get("record_id") not in seen]


def _target_project_ids(config: Config, target: Any) -> list[str] | None:
    """Return the project ids a target may learn from."""
    if target.scope_type == "project" and target.scope_id:
        return [str(target.scope_id)]
    registered = []
    for path in config.projects.values():
        registered.append(resolve_project_identity(Path(path).expanduser().resolve()).project_id)
    return registered or None


def hydrate_patch_text(*, base: Path, draft: SkillProposalDraft) -> SkillProposalDraft:
    """Attach before_text and unified diffs to model patches."""
    patches: list[SkillPatch] = []
    for patch in draft.patches:
        before = ""
        try:
            before = read_target_text(base, patch.relative_path)
        except FileNotFoundError:
            before = ""
        patches.append(
            patch.model_copy(
                update={
                    "before_text": before,
                    "diff_text": _diff_text(
                        relative_path=patch.relative_path,
                        before=before,
                        after=patch.after_text,
                    ),
                }
            )
        )
    return draft.model_copy(update={"patches": patches})


def _diff_text(*, relative_path: str, before: str, after: str) -> str:
    """Render a unified diff for UI and CLI review."""
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{relative_path}",
            tofile=f"b/{relative_path}",
        )
    )


def _validate_compile_result(result: Any) -> str | None:
    """Return a validation error string when model output is incomplete."""
    payload = prediction_payload(result, output_field="proposal")
    try:
        SkillProposalDraft.model_validate(payload)
    except Exception as exc:
        return str(exc)
    return None


def _highest_risk(values: list[str]) -> str:
    """Return the highest risk level from a list."""
    order = {"low": 0, "medium": 1, "high": 2}
    return max(values, key=lambda value: order.get(str(value), 0))


def _risk_allowed(risk: str, max_risk: str) -> bool:
    """Return whether a patch risk is within an auto-apply policy ceiling."""
    order = {"low": 0, "medium": 1, "high": 2}
    return order.get(str(risk), 2) <= order.get(str(max_risk), 0)


def _is_config_path(path: Path) -> bool:
    """Return whether a relative target path is a high-risk config surface."""
    first = path.parts[0] if path.parts else ""
    return first in {"agents", ".cursor", ".roo", ".clinerules"} or path.suffix in {
        ".json",
        ".toml",
        ".yaml",
        ".yml",
    }


def _should_auto_apply(update_mode: str, policy: AutoApplyPolicy, proposal: SkillProposal) -> bool:
    """Return whether a persisted proposal should be auto-applied."""
    validation_ok = bool(proposal.validation_json.get("ok"))
    return (
        update_mode == "auto_apply"
        and policy.enabled
        and proposal.auto_apply_eligible
        and (validation_ok or not policy.require_validation)
    )
