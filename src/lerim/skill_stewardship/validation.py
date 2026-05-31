"""Deterministic validation for proposed instruction artifact patches."""

from __future__ import annotations

from pathlib import Path

import yaml

from lerim.skill_stewardship.schemas import ArtifactManifest, SkillProposalDraft, ValidationResult


def validate_proposal(
    *,
    base_path: Path,
    manifest: ArtifactManifest,
    proposal: SkillProposalDraft,
) -> ValidationResult:
    """Validate proposal shape, paths, frontmatter, and artifact constraints."""
    checks: list[str] = []
    errors: list[str] = []
    base = base_path.resolve()
    changed_files: set[str] = set()
    instruction_files = set(manifest.instruction_files or [manifest.entry_file])
    if not proposal.patches:
        return ValidationResult(ok=True, checks=["abstained_no_patch"], errors=[])
    checks.append("has_patch")
    for patch in proposal.patches:
        if patch.relative_path in changed_files:
            errors.append(f"{patch.relative_path}: duplicate patch path")
            continue
        changed_files.add(patch.relative_path)
        _validate_patch_path(base, patch.relative_path, errors)
        target_file = (base / patch.relative_path).resolve()
        if not path_belongs_to_manifest(manifest, patch.relative_path, change_type=patch.change_type):
            errors.append(f"{patch.relative_path}: path is not part of the registered instruction artifact")
        if target_file.exists() and patch.change_type == "create":
            errors.append(f"{patch.relative_path}: create patch targets an existing file")
        if not target_file.exists():
            if patch.change_type != "create":
                errors.append(f"{patch.relative_path}: missing file must use create change_type")
            if not _new_file_allowed(manifest, patch.relative_path):
                errors.append(f"{patch.relative_path}: new file is not allowed for {manifest.target_type}")
        if not patch.evidence_record_ids:
            errors.append(f"{patch.relative_path}: missing evidence_record_ids")
        if patch.relative_path == manifest.entry_file:
            if frontmatter_block(str(patch.before_text or "")) and not frontmatter_block(patch.after_text):
                errors.append(f"{manifest.entry_file}: must preserve existing YAML frontmatter")
            _validate_entry_text(manifest, patch.after_text, errors)
        if patch.relative_path in instruction_files:
            _validate_instruction_body(patch.relative_path, patch.after_text, errors)
    if len(changed_files) <= 3:
        checks.append("bounded_changed_files")
    else:
        errors.append("proposal changes too many files for one review")
    return ValidationResult(ok=not errors, checks=checks, errors=errors)


def frontmatter_block(text: str) -> str | None:
    """Return the complete YAML frontmatter block from text when present."""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    return text[: end + 4]


def _validate_patch_path(base: Path, relative_path: str, errors: list[str]) -> None:
    """Reject absolute paths and path traversal."""
    path = Path(relative_path)
    if path.is_absolute():
        errors.append(f"{relative_path}: absolute paths are not allowed")
        return
    if any(part in {"", ".", ".."} for part in path.parts):
        errors.append(f"{relative_path}: relative path components are not allowed")
        return
    resolved = (base / path).resolve()
    if base != resolved and base not in resolved.parents:
        errors.append(f"{relative_path}: path escapes target")


def _validate_entry_text(manifest: ArtifactManifest, text: str, errors: list[str]) -> None:
    """Check required frontmatter for entry-file edits."""
    if not manifest.required_frontmatter:
        return
    if not text.startswith("---\n"):
        errors.append(f"{manifest.entry_file}: missing YAML frontmatter")
        return
    end = text.find("\n---", 4)
    if end < 0:
        errors.append(f"{manifest.entry_file}: unterminated YAML frontmatter")
        return
    parsed = yaml.safe_load(text[4:end]) or {}
    if not isinstance(parsed, dict):
        errors.append(f"{manifest.entry_file}: frontmatter must be a mapping")
        return
    for key in manifest.required_frontmatter:
        if not str(parsed.get(key) or "").strip():
            errors.append(f"{manifest.entry_file}: missing required frontmatter field {key}")


def _validate_instruction_body(relative_path: str, text: str, errors: list[str]) -> None:
    """Require instruction files to retain human-readable guidance."""
    body = text
    frontmatter = frontmatter_block(text)
    if frontmatter:
        body = text[len(frontmatter):]
    if not body.strip():
        errors.append(f"{relative_path}: instruction body cannot be empty")


def _new_file_allowed(manifest: ArtifactManifest, relative_path: str) -> bool:
    """Return whether a target type supports creating this file."""
    first = Path(relative_path).parts[0] if Path(relative_path).parts else ""
    if manifest.target_type in {"codex_skill", "claude_skill", "agent_skill"}:
        return first in {"references", "reference", "examples"}
    return False


def path_belongs_to_manifest(manifest: ArtifactManifest, relative_path: str, *, change_type: str) -> bool:
    """Return whether a patch path is inside the scanned artifact surface."""
    tracked = {
        manifest.entry_file,
        *manifest.instruction_files,
        *manifest.supporting_files,
    }
    if relative_path in tracked:
        return True
    return change_type == "create" and _new_file_allowed(manifest, relative_path)
