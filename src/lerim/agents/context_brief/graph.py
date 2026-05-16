"""LangGraph context-brief compiler backed by BAML."""

from __future__ import annotations

import json
from typing import Any

from langgraph.graph import END, START, StateGraph

from lerim.agents.baml_runtime import build_baml_client_for_role
from lerim.agents.context_brief.state import ContextBriefGraphState
from lerim.config.settings import Config
from lerim.context_brief import ContextBriefDraft, MemoryLine


def run_context_brief_graph(
    *,
    config: Config,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run the BAML context-brief compiler graph and return its final state."""
    graph = build_context_brief_graph(config=config)
    return graph.invoke(
        {
            "candidates": candidates,
            "compact_candidates": [],
            "candidate_profile": {},
            "candidate_profile_json": "",
            "candidate_records_json": "",
            "events": [],
            "done": False,
        }
    )


def build_context_brief_graph(*, config: Config):
    """Compile candidate shaping, BAML synthesis, and draft construction."""
    baml_runtime = build_baml_client_for_role(config=config)

    def prepare_candidates(state: ContextBriefGraphState) -> dict[str, Any]:
        """Shape records into compact prompt inputs."""
        candidates = state.get("candidates") or []
        compact_candidates = [_candidate_for_prompt(record) for record in candidates]
        candidate_profile = _candidate_profile(candidates)
        return {
            "compact_candidates": compact_candidates,
            "candidate_profile": candidate_profile,
            "candidate_profile_json": json.dumps(candidate_profile, ensure_ascii=True),
            "candidate_records_json": json.dumps(compact_candidates, ensure_ascii=True),
        }

    def compile_brief(state: ContextBriefGraphState) -> dict[str, Any]:
        """Run BAML synthesis over bounded candidate records."""
        compact_candidates = state.get("compact_candidates") or []
        output = baml_runtime.CompileContextBrief(
            candidate_profile_json=str(state.get("candidate_profile_json") or "{}"),
            candidate_records_json=str(state.get("candidate_records_json") or "[]"),
        )
        return {
            "output": output,
            "events": [
                {
                    "kind": "baml_call",
                    "function": "CompileContextBrief",
                    "candidate_count": len(compact_candidates),
                }
            ],
        }

    def build_draft(state: ContextBriefGraphState) -> dict[str, Any]:
        """Convert BAML output into the fixed-section draft contract."""
        return {"draft": _draft_from_output(state.get("output")), "done": True}

    graph = StateGraph(ContextBriefGraphState)
    graph.add_node("prepare_candidates", prepare_candidates)
    graph.add_node("compile_brief", compile_brief)
    graph.add_node("build_draft", build_draft)
    graph.add_edge(START, "prepare_candidates")
    graph.add_edge("prepare_candidates", "compile_brief")
    graph.add_edge("compile_brief", "build_draft")
    graph.add_edge("build_draft", END)
    return graph.compile()


def _candidate_for_prompt(record: dict[str, Any]) -> dict[str, Any]:
    """Return the compact candidate fields shown to the model."""
    return {
        "record_id": record.get("record_id"),
        "kind": record.get("kind"),
        "title": record.get("title"),
        "body": record.get("body"),
        "decision": record.get("decision"),
        "why": record.get("why"),
        "user_intent": record.get("user_intent"),
        "what_happened": record.get("what_happened"),
        "outcomes": record.get("outcomes"),
        "updated_at": record.get("updated_at"),
    }


def _candidate_profile(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return compact metadata that frames what the records can support."""
    kind_counts: dict[str, int] = {}
    record_ids_by_kind: dict[str, list[str]] = {}
    newest_updated_at = ""
    newest_episode_updated_at = ""
    for record in records:
        kind = str(record.get("kind") or "unknown")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        record_id = str(record.get("record_id") or "")
        if record_id:
            record_ids_by_kind.setdefault(kind, []).append(record_id)
        updated_at = str(record.get("updated_at") or "")
        newest_updated_at = max(newest_updated_at, updated_at)
        if kind == "episode":
            newest_episode_updated_at = max(newest_episode_updated_at, updated_at)
    episode_count = int(kind_counts.get("episode") or 0)
    episode_record_ids = record_ids_by_kind.get("episode", [])
    return {
        "candidate_count": len(records),
        "kind_counts": kind_counts,
        "record_ids_by_kind": record_ids_by_kind,
        "newest_updated_at": newest_updated_at or None,
        "newest_episode_updated_at": newest_episode_updated_at or None,
        "has_recent_flow_evidence": episode_count > 0,
        "current_handoff_evidence_record_ids": episode_record_ids,
        "guidance": (
            "Only populate current_handoff when cited records describe recent flow, "
            "current work, open loops, or next commands. Leave open_risks empty unless "
            "cited record text explicitly supports an unresolved risk, blocker, or "
            "requested follow-up. Treat all test/build results as historical persisted "
            "evidence and tell agents to rerun relevant checks after edits."
        ),
    }


def _memory_lines(lines: list[Any]) -> tuple[MemoryLine, ...]:
    """Convert model line objects into draft memory lines."""
    converted: list[MemoryLine] = []
    for line in lines:
        payload = _model_payload(line)
        text = str(payload.get("text") or "")
        record_ids = tuple(str(item) for item in payload.get("record_ids") or [])
        if text.strip() and not record_ids:
            continue
        converted.append(
            MemoryLine(
                text=text,
                record_ids=record_ids,
            )
        )
    return tuple(converted)


def _draft_from_output(output: Any) -> ContextBriefDraft:
    """Build a fixed-section ContextBriefDraft from model output."""
    payload = _model_payload(output)
    return ContextBriefDraft(
        summary=_memory_lines(payload.get("summary") or []),
        start_here=_memory_lines(payload.get("start_here") or []),
        current_handoff=_memory_lines(payload.get("current_handoff") or []),
        decisions=_memory_lines(payload.get("decisions") or []),
        constraints_preferences=_memory_lines(payload.get("constraints_preferences") or []),
        project_facts=_memory_lines(payload.get("project_facts") or []),
        open_risks=_memory_lines(payload.get("open_risks") or []),
        follow_up_queries=_memory_lines(payload.get("follow_up_queries") or []),
    )


def _model_payload(value: Any) -> dict[str, Any]:
    """Convert generated BAML objects into plain dictionaries."""
    if hasattr(value, "model_dump"):
        return _plain_value(value.model_dump(exclude_none=True))
    if isinstance(value, dict):
        return _plain_value({key: item for key, item in value.items() if item is not None})
    if value is None:
        return {}
    return _plain_value(getattr(value, "__dict__", {}))


def _plain_value(value: Any) -> Any:
    """Convert enum-ish values recursively into JSON-like values."""
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return enum_value
    if isinstance(value, dict):
        return {key: _plain_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain_value(item) for item in value]
    return value
