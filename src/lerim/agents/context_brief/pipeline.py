"""DSPy Context Brief compilation pipeline."""

from __future__ import annotations

import json
from contextlib import nullcontext
from typing import Any

from lerim.agents.dspy_compat import dspy

from lerim.agents.context_brief.signatures import CompileContextBrief
from lerim.agents.model_helpers import prediction_payload
from lerim.agents.model_runtime import ModelRuntime, build_model_runtime
from lerim.config.settings import Config
from lerim.context_brief import ContextBriefDraft, MemoryLine


class ContextBriefPipeline(dspy.Module):
    """Shape candidate records and compile a fixed-section Context Brief."""

    def __init__(
        self,
        *,
        config: Config,
        runtime: ModelRuntime | None = None,
        compile_step: Any | None = None,
    ) -> None:
        """Create the Context Brief compiler with an optional test double."""
        super().__init__()
        self.config = config
        self.runtime = runtime
        self.adapter = dspy.JSONAdapter()
        self.uses_real_model = compile_step is None
        self.compile_step = compile_step or dspy.Predict(CompileContextBrief)

    def forward(self, *, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        """Run candidate shaping, model synthesis, and draft construction."""
        compact_candidates = [candidate_for_prompt(record) for record in candidates]
        profile = candidate_profile(candidates)
        with self.model_context():
            output = self.compile_step(
                candidate_profile_json=json.dumps(profile, ensure_ascii=True),
                candidate_records_json=json.dumps(compact_candidates, ensure_ascii=True),
            )
        return {
            "draft": draft_from_output(output),
            "events": [
                {
                    "kind": "model_step",
                    "stage": "compile_context_brief",
                    "candidate_count": len(compact_candidates),
                }
            ],
            "done": True,
        }

    def model_context(self):
        """Return a DSPy context only when real predictors need a configured LM."""
        if not self.uses_real_model:
            return nullcontext()
        if self.runtime is None:
            self.runtime = build_model_runtime(config=self.config)
        return dspy.context(lm=self.runtime.lm, adapter=self.adapter)


def candidate_for_prompt(record: dict[str, Any]) -> dict[str, Any]:
    """Return the compact candidate fields shown to the model."""
    return {
        "record_id": record.get("record_id"),
        "kind": record.get("kind"),
        "record_role": record.get("record_role"),
        "role_payload": record.get("role_payload"),
        "title": record.get("title"),
        "body": record.get("body"),
        "decision": record.get("decision"),
        "why": record.get("why"),
        "user_intent": record.get("user_intent"),
        "what_happened": record.get("what_happened"),
        "outcomes": record.get("outcomes"),
        "updated_at": record.get("updated_at"),
    }


def candidate_profile(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return compact metadata that frames what the records can support."""
    kind_counts: dict[str, int] = {}
    role_counts: dict[str, int] = {}
    record_ids_by_kind: dict[str, list[str]] = {}
    record_ids_by_role: dict[str, list[str]] = {}
    newest_updated_at = ""
    newest_episode_updated_at = ""
    for record in records:
        kind = str(record.get("kind") or "unknown")
        role = str(record.get("record_role") or "general")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        role_counts[role] = role_counts.get(role, 0) + 1
        record_id = str(record.get("record_id") or "")
        if record_id:
            record_ids_by_kind.setdefault(kind, []).append(record_id)
            record_ids_by_role.setdefault(role, []).append(record_id)
        updated_at = str(record.get("updated_at") or "")
        newest_updated_at = max(newest_updated_at, updated_at)
        if kind == "episode":
            newest_episode_updated_at = max(newest_episode_updated_at, updated_at)
    episode_record_ids = record_ids_by_kind.get("episode", [])
    return {
        "candidate_count": len(records),
        "kind_counts": kind_counts,
        "role_counts": role_counts,
        "record_ids_by_kind": record_ids_by_kind,
        "record_ids_by_role": record_ids_by_role,
        "newest_updated_at": newest_updated_at or None,
        "newest_episode_updated_at": newest_episode_updated_at or None,
        "has_recent_flow_evidence": bool(episode_record_ids),
        "current_handoff_evidence_record_ids": episode_record_ids,
        "guidance": (
            "Treat current_handoff as continuation-only context. Only populate it "
            "when cited episode records describe recent flow, current work, or open "
            "loops. Do not invent next actions because the next user prompt decides "
            "what happens next. Leave open_risks empty unless "
            "cited record text explicitly supports an unresolved risk, blocker, or "
            "requested follow-up. Treat all test/build results as historical persisted "
            "evidence and tell agents to rerun relevant checks after edits."
        ),
    }


def memory_lines(lines: list[Any]) -> tuple[MemoryLine, ...]:
    """Convert model line objects into draft memory lines."""
    converted: list[MemoryLine] = []
    for line in lines:
        payload = prediction_payload(line)
        text = str(payload.get("text") or "")
        record_ids = tuple(str(item) for item in payload.get("record_ids") or [])
        if text.strip() and not record_ids:
            continue
        converted.append(MemoryLine(text=text, record_ids=record_ids))
    return tuple(converted)


def draft_from_output(output: Any) -> ContextBriefDraft:
    """Build a fixed-section ContextBriefDraft from model output."""
    payload = prediction_payload(output, output_field="brief")
    return ContextBriefDraft(
        summary=memory_lines(payload.get("summary") or []),
        start_here=memory_lines(payload.get("start_here") or []),
        current_handoff=memory_lines(payload.get("current_handoff") or []),
        decisions=memory_lines(payload.get("decisions") or []),
        constraints_preferences=memory_lines(
            payload.get("constraints_preferences") or []
        ),
        operational_context=memory_lines(payload.get("operational_context") or []),
        project_facts=memory_lines(payload.get("project_facts") or []),
        open_risks=memory_lines(payload.get("open_risks") or []),
        follow_up_queries=memory_lines(payload.get("follow_up_queries") or []),
    )
