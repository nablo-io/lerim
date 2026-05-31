"""SQLite persistence for registered instruction targets and proposals."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lerim.context import ContextStore
from lerim.skill_stewardship.schemas import (
    ArtifactManifest,
    AutoApplyPolicy,
    InstructionTarget,
    ProposalGuardResult,
    SkillProposal,
    SkillProposalDraft,
    TargetFile,
    ValidationResult,
    normalize_update_mode,
)

TERMINAL_PROPOSAL_STATUSES = {"applied", "rejected", "superseded"}
EDITABLE_PROPOSAL_STATUSES = {"pending_review", "failed_validation"}
APPLICABLE_PROPOSAL_STATUSES = {"pending_review", "approved"}


def utc_now() -> str:
    """Return an ISO UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    """Create a short prefixed identifier."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class SkillStewardshipRepository:
    """Persist instruction targets, scans, runs, signals, and proposals."""

    def __init__(self, context_store: ContextStore) -> None:
        """Create a repository backed by Lerim's canonical context store."""
        self.context_store = context_store

    def initialize(self) -> None:
        """Create stewardship tables and indexes."""
        self.context_store.initialize()
        with self.context_store.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS instruction_targets (
                    target_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    path TEXT NOT NULL UNIQUE,
                    target_type TEXT NOT NULL,
                    entry_file TEXT NOT NULL,
                    scope_type TEXT NOT NULL DEFAULT 'global',
                    scope_id TEXT,
                    update_mode TEXT NOT NULL DEFAULT 'review',
                    auto_apply_policy_json TEXT NOT NULL DEFAULT '{}',
                    manifest_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS instruction_target_files (
                    file_id TEXT PRIMARY KEY,
                    target_id TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    file_role TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    risk_surface TEXT NOT NULL,
                    text_preview TEXT,
                    last_seen_at TEXT NOT NULL,
                    FOREIGN KEY(target_id) REFERENCES instruction_targets(target_id),
                    UNIQUE(target_id, relative_path)
                );

                CREATE TABLE IF NOT EXISTS instruction_update_runs (
                    run_id TEXT PRIMARY KEY,
                    target_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    records_scanned INTEGER NOT NULL DEFAULT 0,
                    signals_created INTEGER NOT NULL DEFAULT 0,
                    proposals_created INTEGER NOT NULL DEFAULT 0,
                    proposals_applied INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    FOREIGN KEY(target_id) REFERENCES instruction_targets(target_id)
                );

                CREATE TABLE IF NOT EXISTS instruction_signals (
                    signal_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    version_id TEXT,
                    signal_type TEXT NOT NULL,
                    strength REAL NOT NULL,
                    rationale TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(target_id) REFERENCES instruction_targets(target_id),
                    FOREIGN KEY(run_id) REFERENCES instruction_update_runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS instruction_update_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    target_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    status TEXT NOT NULL,
                    patch_json TEXT NOT NULL,
                    validation_json TEXT NOT NULL,
                    guard_json TEXT NOT NULL,
                    auto_apply_eligible INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(target_id) REFERENCES instruction_targets(target_id),
                    FOREIGN KEY(run_id) REFERENCES instruction_update_runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS instruction_proposal_records (
                    proposal_id TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    version_id TEXT,
                    PRIMARY KEY(proposal_id, record_id, version_id),
                    FOREIGN KEY(proposal_id) REFERENCES instruction_update_proposals(proposal_id)
                );

                CREATE TABLE IF NOT EXISTS instruction_versions (
                    version_id TEXT PRIMARY KEY,
                    target_id TEXT NOT NULL,
                    proposal_id TEXT,
                    relative_path TEXT NOT NULL,
                    before_hash TEXT,
                    after_hash TEXT,
                    snapshot_path TEXT,
                    applied_at TEXT NOT NULL,
                    applied_by TEXT NOT NULL,
                    FOREIGN KEY(target_id) REFERENCES instruction_targets(target_id),
                    FOREIGN KEY(proposal_id) REFERENCES instruction_update_proposals(proposal_id)
                );

                CREATE INDEX IF NOT EXISTS idx_instruction_targets_status ON instruction_targets(status);
                CREATE INDEX IF NOT EXISTS idx_instruction_proposals_status ON instruction_update_proposals(status);
                CREATE INDEX IF NOT EXISTS idx_instruction_proposals_target ON instruction_update_proposals(target_id);
                CREATE INDEX IF NOT EXISTS idx_instruction_runs_target ON instruction_update_runs(target_id);
                """
            )

    def upsert_target(
        self,
        *,
        name: str,
        path: Path,
        description: str | None,
        manifest: ArtifactManifest,
        files: list[TargetFile],
        update_mode: str | None = None,
        auto_apply_policy: AutoApplyPolicy | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
    ) -> InstructionTarget:
        """Create or update a registered instruction target from a fresh scan."""
        self.initialize()
        now = utc_now()
        with self.context_store.connect() as conn:
            existing = conn.execute(
                """
                SELECT target_id, created_at, update_mode, auto_apply_policy_json, scope_type, scope_id
                FROM instruction_targets
                WHERE path = ?
                """,
                (str(path),),
            ).fetchone()
            target_id = str(existing["target_id"]) if existing else new_id("it")
            created_at = str(existing["created_at"]) if existing else now
            if existing and auto_apply_policy is None:
                policy = AutoApplyPolicy.model_validate(json.loads(str(existing["auto_apply_policy_json"] or "{}")))
            else:
                policy = auto_apply_policy or AutoApplyPolicy()
            effective_update_mode = (
                normalize_update_mode(update_mode)
                if update_mode is not None
                else normalize_update_mode(str(existing["update_mode"]) if existing else None)
            )
            effective_scope_type = str(scope_type or (existing["scope_type"] if existing else "global") or "global")
            effective_scope_id = scope_id if scope_type else (existing["scope_id"] if existing else None)
            if effective_scope_type == "project" and not effective_scope_id:
                raise ValueError("project-scoped instruction targets require scope_id")
            if effective_scope_type != "project":
                effective_scope_id = None
            conn.execute(
                """
                INSERT INTO instruction_targets(
                    target_id, name, description, path, target_type, entry_file,
                    scope_type, scope_id, update_mode, auto_apply_policy_json,
                    manifest_json, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    target_type=excluded.target_type,
                    entry_file=excluded.entry_file,
                    scope_type=excluded.scope_type,
                    scope_id=excluded.scope_id,
                    update_mode=excluded.update_mode,
                    auto_apply_policy_json=excluded.auto_apply_policy_json,
                    manifest_json=excluded.manifest_json,
                    status='active',
                    updated_at=excluded.updated_at
                """,
                (
                    target_id,
                    name,
                    description,
                    str(path),
                    manifest.target_type,
                    manifest.entry_file,
                    effective_scope_type,
                    effective_scope_id,
                    effective_update_mode,
                    policy.model_dump_json(),
                    manifest.model_dump_json(),
                    created_at,
                    now,
                ),
            )
            self._replace_files(conn, target_id=target_id, files=files, seen_at=now)
        return self.get_target(target_id)

    def list_targets(self, *, include_archived: bool = False) -> list[InstructionTarget]:
        """Return registered instruction targets."""
        self.initialize()
        where = "1=1" if include_archived else "status != 'archived'"
        with self.context_store.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM instruction_targets WHERE {where} ORDER BY updated_at DESC"
            ).fetchall()
        return [self._target_from_row(row) for row in rows]

    def get_target(self, target_id_or_name: str) -> InstructionTarget:
        """Load one target by id or name."""
        self.initialize()
        with self.context_store.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM instruction_targets
                WHERE target_id = ? OR name = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (target_id_or_name, target_id_or_name),
            ).fetchone()
        if row is None:
            raise KeyError(f"instruction target not found: {target_id_or_name}")
        return self._target_from_row(row)

    def target_files(self, target_id: str) -> list[TargetFile]:
        """Return the latest scanned files for a target."""
        self.initialize()
        with self.context_store.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM instruction_target_files
                WHERE target_id = ?
                ORDER BY relative_path ASC
                """,
                (target_id,),
            ).fetchall()
        return [self._file_from_row(row) for row in rows]

    def update_target_mode(self, target_id_or_name: str, update_mode: str, policy: AutoApplyPolicy | None = None) -> InstructionTarget:
        """Update review or auto-apply behavior for a target."""
        effective_update_mode = normalize_update_mode(update_mode)
        target = self.get_target(target_id_or_name)
        effective_policy = policy or target.auto_apply_policy
        now = utc_now()
        with self.context_store.connect() as conn:
            conn.execute(
                """
                UPDATE instruction_targets
                SET update_mode = ?, auto_apply_policy_json = ?, updated_at = ?
                WHERE target_id = ?
                """,
                (effective_update_mode, effective_policy.model_dump_json(), now, target.target_id),
            )
        return self.get_target(target.target_id)

    def create_run(self, target_id: str) -> str:
        """Create a stewardship run row and return its id."""
        self.initialize()
        run_id = new_id("sir")
        with self.context_store.connect() as conn:
            conn.execute(
                """
                INSERT INTO instruction_update_runs(run_id, target_id, status, started_at)
                VALUES (?, ?, 'running', ?)
                """,
                (run_id, target_id, utc_now()),
            )
        return run_id

    def finish_run(
        self,
        *,
        run_id: str,
        status: str,
        records_scanned: int,
        signals_created: int,
        proposals_created: int,
        proposals_applied: int = 0,
        error: str | None = None,
    ) -> None:
        """Persist final run counters."""
        self.initialize()
        with self.context_store.connect() as conn:
            conn.execute(
                """
                UPDATE instruction_update_runs
                SET status = ?, finished_at = ?, records_scanned = ?, signals_created = ?,
                    proposals_created = ?, proposals_applied = ?, error = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    utc_now(),
                    records_scanned,
                    signals_created,
                    proposals_created,
                    proposals_applied,
                    error,
                    run_id,
                ),
            )

    def save_proposal(
        self,
        *,
        run_id: str,
        target_id: str,
        draft: SkillProposalDraft,
        guard: ProposalGuardResult,
        validation: ValidationResult,
        status: str,
    ) -> SkillProposal:
        """Persist one guarded proposal and its evidence links."""
        self.initialize()
        now = utc_now()
        proposal_id = new_id("ip")
        evidence_ids = sorted({record_id for patch in draft.patches for record_id in patch.evidence_record_ids})
        with self.context_store.connect() as conn:
            conn.execute(
                """
                INSERT INTO instruction_update_proposals(
                    proposal_id, run_id, target_id, title, summary, risk_level, status,
                    patch_json, validation_json, guard_json, auto_apply_eligible,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    run_id,
                    target_id,
                    draft.title,
                    draft.summary,
                    guard.risk_level,
                    status,
                    draft.model_dump_json(),
                    validation.model_dump_json(),
                    guard.model_dump_json(),
                    1 if guard.auto_apply_eligible else 0,
                    now,
                    now,
                ),
            )
            for record_id in evidence_ids:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO instruction_proposal_records(proposal_id, record_id, version_id)
                    VALUES (?, ?, '')
                    """,
                    (proposal_id, record_id),
                )
        return self.get_proposal(proposal_id)

    def save_signals(
        self,
        *,
        run_id: str,
        target_id: str,
        draft: SkillProposalDraft,
    ) -> int:
        """Persist proposal signals and return the number saved."""
        self.initialize()
        with self.context_store.connect() as conn:
            for signal in draft.signals:
                conn.execute(
                    """
                    INSERT INTO instruction_signals(
                        signal_id, run_id, target_id, record_id, version_id,
                        signal_type, strength, rationale, status, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)
                    """,
                    (
                        new_id("is"),
                        run_id,
                        target_id,
                        signal.record_id,
                        signal.version_id,
                        signal.signal_type,
                        signal.strength,
                        signal.rationale,
                        utc_now(),
                    ),
                )
        return len(draft.signals)

    def list_proposals(self, *, target_id: str | None = None, status: str | None = None) -> list[SkillProposal]:
        """Return proposals, optionally filtered by target and status."""
        self.initialize()
        clauses = ["1=1"]
        params: list[Any] = []
        if target_id:
            clauses.append("target_id = ?")
            params.append(target_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        with self.context_store.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM instruction_update_proposals
                WHERE {' AND '.join(clauses)}
                ORDER BY updated_at DESC
                """,
                tuple(params),
            ).fetchall()
        return [self._proposal_from_row(row) for row in rows]

    def get_proposal(self, proposal_id: str) -> SkillProposal:
        """Load one persisted proposal."""
        self.initialize()
        with self.context_store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM instruction_update_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"instruction proposal not found: {proposal_id}")
        return self._proposal_from_row(row)

    def set_proposal_status(self, proposal_id: str, status: str) -> SkillProposal:
        """Move a proposal through the review lifecycle."""
        self.initialize()
        with self.context_store.connect() as conn:
            current = self._proposal_row(conn, proposal_id)
            self._ensure_status_transition(current_status=str(current["status"]), next_status=status)
            conn.execute(
                """
                UPDATE instruction_update_proposals
                SET status = ?, updated_at = ?
                WHERE proposal_id = ?
                """,
                (status, utc_now(), proposal_id),
            )
        return self.get_proposal(proposal_id)

    def update_proposal_patch(
        self,
        *,
        proposal_id: str,
        draft: SkillProposalDraft,
        validation: ValidationResult,
        guard: ProposalGuardResult,
    ) -> SkillProposal:
        """Replace a proposal patch after user editing and validation."""
        self.initialize()
        with self.context_store.connect() as conn:
            current = self._proposal_row(conn, proposal_id)
            if str(current["status"]) not in EDITABLE_PROPOSAL_STATUSES:
                raise ValueError(f"proposal status cannot be edited: {current['status']}")
            conn.execute(
                """
                UPDATE instruction_update_proposals
                SET title = ?, summary = ?, patch_json = ?, validation_json = ?, guard_json = ?,
                    risk_level = ?, auto_apply_eligible = ?, status = 'pending_review',
                    updated_at = ?
                WHERE proposal_id = ?
                """,
                (
                    draft.title,
                    draft.summary,
                    draft.model_dump_json(),
                    validation.model_dump_json(),
                    guard.model_dump_json(),
                    guard.risk_level,
                    1 if guard.auto_apply_eligible else 0,
                    utc_now(),
                    proposal_id,
                ),
            )
        return self.get_proposal(proposal_id)

    def recent_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent stewardship runs as dictionaries."""
        self.initialize()
        with self.context_store.connect() as conn:
            rows = conn.execute(
                """
                SELECT r.*, t.name AS target_name
                FROM instruction_update_runs r
                JOIN instruction_targets t ON t.target_id = r.target_id
                ORDER BY r.started_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def _replace_files(
        self,
        conn: sqlite3.Connection,
        *,
        target_id: str,
        files: list[TargetFile],
        seen_at: str,
    ) -> None:
        """Refresh target file rows after a scan."""
        relative_paths = [file_info.relative_path for file_info in files]
        if relative_paths:
            placeholders = ", ".join("?" for _ in relative_paths)
            conn.execute(
                f"""
                DELETE FROM instruction_target_files
                WHERE target_id = ? AND relative_path NOT IN ({placeholders})
                """,
                (target_id, *relative_paths),
            )
        else:
            conn.execute("DELETE FROM instruction_target_files WHERE target_id = ?", (target_id,))
        for file_info in files:
            conn.execute(
                """
                INSERT INTO instruction_target_files(
                    file_id, target_id, relative_path, file_role, size_bytes,
                    sha256, risk_surface, text_preview, last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(target_id, relative_path) DO UPDATE SET
                    file_role=excluded.file_role,
                    size_bytes=excluded.size_bytes,
                    sha256=excluded.sha256,
                    risk_surface=excluded.risk_surface,
                    text_preview=excluded.text_preview,
                    last_seen_at=excluded.last_seen_at
                """,
                (
                    new_id("itf"),
                    target_id,
                    file_info.relative_path,
                    file_info.file_role,
                    file_info.size_bytes,
                    file_info.sha256,
                    file_info.risk_surface,
                    file_info.text_preview,
                    seen_at,
                ),
            )

    def _proposal_row(self, conn: sqlite3.Connection, proposal_id: str) -> sqlite3.Row:
        """Return one proposal row or raise a stable not-found error."""
        row = conn.execute(
            "SELECT * FROM instruction_update_proposals WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"instruction proposal not found: {proposal_id}")
        return row

    def _ensure_status_transition(self, *, current_status: str, next_status: str) -> None:
        """Reject lifecycle transitions that would resurrect terminal proposals."""
        if current_status == next_status:
            return
        if current_status in TERMINAL_PROPOSAL_STATUSES:
            raise ValueError(f"proposal status is terminal: {current_status}")
        if next_status == "applied" and current_status not in APPLICABLE_PROPOSAL_STATUSES:
            raise ValueError(f"proposal status cannot be applied: {current_status}")

    def _target_from_row(self, row: sqlite3.Row) -> InstructionTarget:
        """Convert one target row into a typed model."""
        policy_raw = json.loads(str(row["auto_apply_policy_json"] or "{}"))
        manifest_raw = json.loads(str(row["manifest_json"] or "{}"))
        return InstructionTarget(
            target_id=str(row["target_id"]),
            name=str(row["name"]),
            description=row["description"],
            path=str(row["path"]),
            target_type=str(row["target_type"]),
            entry_file=str(row["entry_file"]),
            scope_type=str(row["scope_type"] or "global"),
            scope_id=row["scope_id"],
            update_mode=str(row["update_mode"] or "review"),
            auto_apply_policy=AutoApplyPolicy.model_validate(policy_raw),
            status=str(row["status"] or "active"),
            manifest=ArtifactManifest.model_validate(manifest_raw) if manifest_raw else None,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _file_from_row(self, row: sqlite3.Row) -> TargetFile:
        """Convert one file row into a typed model."""
        return TargetFile(
            relative_path=str(row["relative_path"]),
            file_role=str(row["file_role"]),
            size_bytes=int(row["size_bytes"]),
            sha256=str(row["sha256"]),
            text_preview=row["text_preview"],
            risk_surface=str(row["risk_surface"] or "low"),
        )

    def _proposal_from_row(self, row: sqlite3.Row) -> SkillProposal:
        """Convert one proposal row into a typed model."""
        return SkillProposal(
            proposal_id=str(row["proposal_id"]),
            target_id=str(row["target_id"]),
            title=str(row["title"]),
            summary=str(row["summary"]),
            risk_level=str(row["risk_level"]),
            status=str(row["status"]),
            patch_json=json.loads(str(row["patch_json"] or "{}")),
            validation_json=json.loads(str(row["validation_json"] or "{}")),
            guard_json=json.loads(str(row["guard_json"] or "{}")),
            auto_apply_eligible=bool(row["auto_apply_eligible"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
