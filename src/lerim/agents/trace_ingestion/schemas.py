"""Structured model schemas for trace ingestion."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


RecordKind = Literal["decision", "preference", "constraint", "fact"]
RecordStatus = Literal["active", "archived"]
RecordRole = Literal[
    "general",
    "procedure",
    "gotcha",
    "failure_mode",
    "artifact",
    "state_change",
    "eval_asset",
]


class DurableFinding(BaseModel):
    """Reusable future-agent context found in a source window."""

    theme: str
    kind: RecordKind
    line: int | None = None
    quote: str | None = None
    note: str
    record_role: RecordRole | None = None


class ImplementationFinding(BaseModel):
    """Source-local implementation evidence or discarded support detail."""

    theme: str
    line: int | None = None
    quote: str | None = None
    note: str


class SourceWindowScan(BaseModel):
    """Scan result for one trace window."""

    episode_update: str | None = None
    durable_findings: list[DurableFinding]
    implementation_findings: list[ImplementationFinding]
    discarded_noise: list[str]


class SignalFilterResult(BaseModel):
    """Final durable-signal filtering decision."""

    kept_durable_findings: list[DurableFinding]
    rejected_findings: list[DurableFinding]
    filtering_summary: str | None = None


class EpisodeDraft(BaseModel):
    """Exactly one source-session episode record draft."""

    title: str
    body: str
    status: RecordStatus
    user_intent: str
    what_happened: str
    outcomes: str | None = None
    source_event_refs: list[str]
    evidence_refs: list[str]


class DurableRecordDraft(BaseModel):
    """One durable context record draft."""

    kind: RecordKind
    title: str
    body: str
    status: RecordStatus | None = None
    decision: str | None = None
    why: str | None = None
    alternatives: str | None = None
    consequences: str | None = None
    record_role: RecordRole | None = None
    role_payload: dict[str, Any] | None = None
    source_event_refs: list[str]
    evidence_refs: list[str]


class FixedKindRecordDraft(BaseModel):
    """A record draft whose kind is assigned by the receiving slot."""

    title: str
    body: str
    status: RecordStatus | None = None
    decision: str | None = None
    why: str | None = None
    alternatives: str | None = None
    consequences: str | None = None
    record_role: RecordRole | None = None
    role_payload: dict[str, Any] | None = None
    source_event_refs: list[str]
    evidence_refs: list[str]


class RecordRoleAnnotation(BaseModel):
    """Operational role annotation for one accepted durable record."""

    record_index: int
    record_role: RecordRole
    role_payload: dict[str, Any] | None = None
    rationale: str | None = None


class RecordRoleAnnotationResult(BaseModel):
    """Operational role annotations for accepted durable records."""

    annotations: list[RecordRoleAnnotation] = Field(default_factory=list)


class SynthesizedContextRecords(BaseModel):
    """Synthesized episode and durable records."""

    episode: EpisodeDraft
    durable_records: list[DurableRecordDraft]
    completion_summary: str | None = None


class CodingEvalPolishedContextRecords(BaseModel):
    """Fixed coding-eval polish slots before conversion to durable records."""

    episode: EpisodeDraft
    silent_change_feedback_record: DurableRecordDraft | None = None
    model_size_priority_record: DurableRecordDraft | None = None
    provider_cost_record: DurableRecordDraft | None = None
    user_strategy_records: list[DurableRecordDraft] = Field(default_factory=list)
    role_split_record: DurableRecordDraft | None = None
    upstream_bug_report_record: DurableRecordDraft | None = None
    project_identity_fact: FixedKindRecordDraft | None = None
    model_setting_fact: FixedKindRecordDraft | None = None
    adapter_decision: FixedKindRecordDraft | None = None
    prompt_structure_decision: FixedKindRecordDraft | None = None
    fixture_constraint: FixedKindRecordDraft | None = None
    deferred_design_fact: FixedKindRecordDraft | None = None
    other_records: list[DurableRecordDraft] = Field(default_factory=list)
    completion_summary: str | None = None


class CodingStrategySlotRecords(BaseModel):
    """User-authored coding strategy slots extracted from visible user lines."""

    silent_change_feedback_record: DurableRecordDraft | None = None
    model_size_priority_record: DurableRecordDraft | None = None
    provider_cost_record: DurableRecordDraft | None = None
    user_strategy_records: list[DurableRecordDraft] = Field(default_factory=list)
    role_split_record: DurableRecordDraft | None = None


class CodingProjectIdentitySlotRecords(BaseModel):
    """Configured project/service identity slot extracted from visible source lines."""

    project_identity_fact: FixedKindRecordDraft | None = None


class CodingRecordRetentionDecision(BaseModel):
    """Keep/drop decision for one candidate coding record."""

    record_index: int
    keep: bool
    reason: str


class CodingRecordRetentionResult(BaseModel):
    """Post-polish retention decision for coding records."""

    save_any: bool
    session_reason: str
    decisions: list[CodingRecordRetentionDecision]
