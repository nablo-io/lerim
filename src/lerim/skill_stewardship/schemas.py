"""Typed contracts for skill stewardship targets, scans, and proposals."""

from __future__ import annotations

from typing import Any, Literal, cast, get_args

from pydantic import BaseModel, Field


TargetType = Literal[
    "codex_skill",
    "claude_skill",
    "claude_context",
    "agent_skill",
    "agents_md",
    "gemini_context",
    "cline_rules",
    "cursor_rules",
    "opencode_rules",
    "generic_markdown_bundle",
]
TargetStatus = Literal["active", "paused", "archived"]
UpdateMode = Literal["review", "auto_apply", "paused"]
UPDATE_MODES = tuple(str(value) for value in get_args(UpdateMode))
ProposalStatus = Literal[
    "draft",
    "pending_review",
    "approved",
    "applied",
    "rejected",
    "superseded",
    "failed_validation",
]
RiskLevel = Literal["low", "medium", "high"]
SignalType = Literal[
    "procedure_addition",
    "procedure_refinement",
    "guardrail_addition",
    "anti_pattern",
    "validator_addition",
    "example_addition",
    "reference_addition",
    "trigger_refinement",
    "stale_instruction",
    "conflict_detected",
]


class AutoApplyPolicy(BaseModel):
    """Target-level limits for automatic application."""

    enabled: bool = False
    max_risk: RiskLevel = "low"
    allow_entry_file_body: bool = True
    allow_frontmatter: bool = False
    allow_new_reference_files: bool = True
    allow_scripts: bool = False
    allow_assets: bool = False
    allow_config_files: bool = False
    max_changed_files: int = 2
    max_added_lines: int = 40
    max_removed_lines: int = 20
    require_validation: bool = True


class TargetFile(BaseModel):
    """One file that belongs to a registered instruction target."""

    relative_path: str
    file_role: str
    size_bytes: int
    sha256: str
    text_preview: str | None = None
    tracked: bool = True
    risk_surface: RiskLevel = "low"


class ArtifactManifest(BaseModel):
    """Detected instruction artifact shape and update boundaries."""

    target_type: TargetType
    entry_file: str
    instruction_files: list[str] = Field(default_factory=list)
    supporting_files: list[str] = Field(default_factory=list)
    required_frontmatter: list[str] = Field(default_factory=list)
    known_frontmatter: list[str] = Field(default_factory=list)
    allowed_update_surfaces: list[str] = Field(default_factory=list)
    high_risk_surfaces: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class InstructionTarget(BaseModel):
    """A user-registered skill or instruction artifact Lerim may improve."""

    target_id: str
    name: str
    description: str | None = None
    path: str
    target_type: TargetType
    entry_file: str
    scope_type: str = "global"
    scope_id: str | None = None
    update_mode: UpdateMode = "review"
    auto_apply_policy: AutoApplyPolicy = Field(default_factory=AutoApplyPolicy)
    status: TargetStatus = "active"
    manifest: ArtifactManifest | None = None
    created_at: str | None = None
    updated_at: str | None = None


class SkillSignal(BaseModel):
    """Evidence that a record may imply an update to a registered target."""

    record_id: str
    version_id: str | None = None
    signal_type: SignalType
    strength: float = Field(ge=0.0, le=1.0)
    rationale: str


class SkillPatch(BaseModel):
    """One proposed file edit."""

    relative_path: str
    change_type: Literal["modify", "create"]
    risk: RiskLevel
    rationale: str
    evidence_record_ids: list[str] = Field(default_factory=list)
    before_text: str | None = None
    after_text: str
    diff_text: str


class SkillProposalDraft(BaseModel):
    """Model-authored proposal before persistence."""

    title: str
    summary: str
    risk_level: RiskLevel
    signals: list[SkillSignal] = Field(default_factory=list)
    patches: list[SkillPatch] = Field(default_factory=list)
    auto_apply_eligible: bool = False


class ProposalGuardResult(BaseModel):
    """Safety and quality review for a proposal."""

    accepted: bool
    risk_level: RiskLevel
    auto_apply_eligible: bool
    reasons: list[str] = Field(default_factory=list)


class ValidationResult(BaseModel):
    """Deterministic validation result for one proposal."""

    ok: bool
    checks: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class SkillProposal(BaseModel):
    """Persisted skill update proposal."""

    proposal_id: str
    target_id: str
    title: str
    summary: str
    risk_level: RiskLevel
    status: ProposalStatus
    patch_json: dict[str, Any]
    validation_json: dict[str, Any]
    guard_json: dict[str, Any]
    auto_apply_eligible: bool
    created_at: str
    updated_at: str


def normalize_update_mode(value: str | None) -> UpdateMode:
    """Validate and normalize one target update mode before persistence."""
    mode = str(value or "review").strip()
    if mode not in UPDATE_MODES:
        raise ValueError(f"invalid_update_mode:{mode}")
    return cast(UpdateMode, mode)
