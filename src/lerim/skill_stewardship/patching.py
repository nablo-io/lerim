"""Apply approved instruction proposal patches with snapshots."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from lerim.skill_stewardship.repository import SkillStewardshipRepository, new_id, utc_now
from lerim.skill_stewardship.schemas import SkillPatch, SkillProposal, SkillProposalDraft
from lerim.skill_stewardship.validation import validate_proposal

APPLICABLE_STATUSES = {"pending_review", "approved"}


def apply_proposal(
    *,
    repository: SkillStewardshipRepository,
    proposal: SkillProposal,
    applied_by: str,
    workspace_root: Path | None = None,
) -> SkillProposal:
    """Apply a persisted proposal after review or auto-apply guard approval."""
    target = repository.get_target(proposal.target_id)
    base = Path(target.path).expanduser().resolve()
    if base.is_file():
        base = base.parent
    if target.manifest is None:
        raise ValueError("target manifest is missing")
    _ensure_proposal_is_applicable(proposal)
    draft = SkillProposalDraft.model_validate(proposal.patch_json)
    validation = validate_proposal(base_path=base, manifest=target.manifest, proposal=draft)
    if not validation.ok:
        raise ValueError(f"proposal validation failed: {'; '.join(validation.errors)}")
    snapshot_root = _snapshot_root(workspace_root or repository.context_store.db_path.parent / "workspace", proposal.proposal_id)
    prepared: list[tuple[SkillPatch, Path, str | None]] = []
    versions: list[dict[str, str | None]] = []
    wrote_files = False
    try:
        repository.initialize()
        with repository.context_store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _ensure_current_status_applicable(conn, proposal.proposal_id)
            prepared = _prepare_patches(base=base, draft=draft)
            for patch, target_file, current_text in prepared:
                before_hash = _hash_text(current_text) if current_text is not None else None
                snapshot_path = None
                if current_text is not None:
                    snapshot_path = str(_write_snapshot(snapshot_root, patch.relative_path, target_file))
                target_file.parent.mkdir(parents=True, exist_ok=True)
                wrote_files = True
                target_file.write_text(patch.after_text, encoding="utf-8")
                versions.append(
                    {
                        "relative_path": patch.relative_path,
                        "before_hash": before_hash,
                        "after_hash": _hash_text(patch.after_text),
                        "snapshot_path": snapshot_path,
                    }
                )
            _record_applied_versions(
                conn=conn,
                target_id=target.target_id,
                proposal_id=proposal.proposal_id,
                versions=versions,
                applied_by=applied_by,
            )
    except Exception:
        if wrote_files:
            _restore_prepared_files(prepared)
        raise
    return repository.get_proposal(proposal.proposal_id)


def _ensure_proposal_is_applicable(proposal: SkillProposal) -> None:
    """Reject proposals that are stale, unsafe, or outside the apply lifecycle."""
    if proposal.status not in APPLICABLE_STATUSES:
        raise ValueError(f"proposal status cannot be applied: {proposal.status}")
    if proposal.validation_json.get("ok") is not True:
        raise ValueError("proposal validation has not passed")
    if proposal.guard_json.get("accepted") is not True:
        raise ValueError("proposal guard has not accepted this update")


def _ensure_current_status_applicable(conn: sqlite3.Connection, proposal_id: str) -> None:
    """Reject stale apply attempts while the database write lock is held."""
    row = conn.execute(
        "SELECT status FROM instruction_update_proposals WHERE proposal_id = ?",
        (proposal_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"instruction proposal not found: {proposal_id}")
    status = str(row["status"])
    if status not in APPLICABLE_STATUSES:
        raise ValueError(f"proposal status cannot be applied: {status}")


def _record_applied_versions(
    *,
    conn: sqlite3.Connection,
    target_id: str,
    proposal_id: str,
    versions: list[dict[str, str | None]],
    applied_by: str,
) -> None:
    """Persist version rows and terminal proposal status in the active transaction."""
    now = utc_now()
    for version in versions:
        conn.execute(
            """
            INSERT INTO instruction_versions(
                version_id, target_id, proposal_id, relative_path, before_hash,
                after_hash, snapshot_path, applied_at, applied_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("iv"),
                target_id,
                proposal_id,
                version["relative_path"],
                version["before_hash"],
                version["after_hash"],
                version["snapshot_path"],
                now,
                applied_by,
            ),
        )
    conn.execute(
        """
        UPDATE instruction_update_proposals
        SET status = 'applied', updated_at = ?
        WHERE proposal_id = ?
        """,
        (now, proposal_id),
    )


def _prepare_patches(base: Path, draft: SkillProposalDraft) -> list[tuple[SkillPatch, Path, str | None]]:
    """Resolve patches and verify the current files still match proposal baselines."""
    prepared = []
    seen_paths: set[str] = set()
    for patch in draft.patches:
        if patch.relative_path in seen_paths:
            raise ValueError(f"{patch.relative_path}: duplicate patch path")
        seen_paths.add(patch.relative_path)
        target_file = _safe_child(base, patch.relative_path)
        current_text = target_file.read_text(encoding="utf-8", errors="replace") if target_file.exists() else None
        if current_text is None and patch.change_type != "create":
            raise ValueError(f"{patch.relative_path}: missing target file cannot be modified")
        if current_text is None and (patch.before_text or ""):
            raise ValueError(f"{patch.relative_path}: target changed since proposal was created")
        if current_text is not None and patch.before_text is not None and current_text != patch.before_text:
            raise ValueError(f"{patch.relative_path}: target changed since proposal was created")
        prepared.append((patch, target_file, current_text))
    return prepared


def _restore_prepared_files(prepared: list[tuple[SkillPatch, Path, str | None]]) -> None:
    """Restore target files after a failed multi-file apply."""
    for _patch, target_file, current_text in prepared:
        if current_text is None:
            if target_file.exists():
                target_file.unlink()
            continue
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(current_text, encoding="utf-8")


def _snapshot_root(base: Path, proposal_id: str) -> Path:
    """Return the snapshot directory for one proposal application."""
    return base / "skill-snapshots" / proposal_id / utc_now().replace(":", "-")


def _write_snapshot(snapshot_root: Path, relative_path: str, target_file: Path) -> Path:
    """Copy one before-file into the snapshot folder."""
    snapshot_path = _safe_child(snapshot_root, relative_path)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_bytes(target_file.read_bytes())
    return snapshot_path


def _safe_child(base: Path, relative_path: str) -> Path:
    """Resolve a child path and reject traversal outside base."""
    resolved = (base / relative_path).resolve()
    if base != resolved and base not in resolved.parents:
        raise ValueError(f"path escapes instruction target: {relative_path}")
    return resolved


def _hash_text(text: str) -> str:
    """Hash file text for version metadata."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
