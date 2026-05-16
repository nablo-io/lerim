"""LangGraph context-answerer pipeline backed by BAML."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from lerim.agents.baml_runtime import build_baml_client_for_role
from lerim.agents.context_answerer.state import ContextAnswererGraphState
from lerim.agents.context_answerer.types import ContextAnswerResult
from lerim.config.settings import Config
from lerim.context import ContextStore, ProjectIdentity
from lerim.context.spec import (
    ALLOWED_KINDS,
    ALLOWED_STATUSES,
    normalize_record_kind,
    normalize_record_status,
)


def run_context_answerer_graph(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity,
    project_ids: list[str] | None,
    question: str,
    config: Config,
    hints: str = "",
) -> dict[str, Any]:
    """Run the BAML context-answerer graph and return its final state."""
    store = ContextStore(context_db_path)
    store.initialize()
    store.register_project(project_identity)
    resolved_project_ids = project_ids or [project_identity.project_id]
    graph = build_context_answerer_graph(
        store=store,
        project_ids=resolved_project_ids,
        config=config,
    )
    return graph.invoke(
        {
            "question": question.strip(),
            "current_utc": datetime.now(timezone.utc).isoformat(),
            "hints": hints.strip(),
            "actions": [],
            "retrieval_payload": {},
            "events": [],
            "done": False,
        }
    )


def build_context_answerer_graph(
    *,
    store: ContextStore,
    project_ids: list[str],
    config: Config,
):
    """Compile retrieval planning, store reads, and answer synthesis."""
    baml_runtime = build_baml_client_for_role(config=config)
    max_actions = max(1, int(config.agent_role.answer_max_retrieval_actions))

    def plan_retrieval(state: ContextAnswererGraphState) -> dict[str, Any]:
        """Plan context-store reads using BAML."""
        plan = baml_runtime.PlanContextRetrieval(
            question=str(state.get("question") or ""),
            current_utc=str(state.get("current_utc") or ""),
            hints=str(state.get("hints") or ""),
        )
        actions = _plan_actions(plan)
        if not actions:
            raise ValueError("context_retrieval_plan_empty")
        if len(actions) > max_actions:
            raise ValueError(f"context_retrieval_plan_too_large:{len(actions)}>{max_actions}")
        return {
            "actions": actions,
            "events": [
                {
                    "kind": "baml_call",
                    "function": "PlanContextRetrieval",
                    "action_count": len(actions),
                    "rationale": _model_payload(plan).get("rationale"),
                }
            ],
        }

    def execute_retrieval(state: ContextAnswererGraphState) -> dict[str, Any]:
        """Execute planned read-only context-store actions."""
        retrieval = _execute_actions(
            store=store,
            project_ids=project_ids,
            actions=state.get("actions") or [],
        )
        return {
            "retrieval_payload": retrieval["payload"],
            "events": retrieval["events"],
        }

    def synthesize_answer(state: ContextAnswererGraphState) -> dict[str, Any]:
        """Synthesize the final answer from retrieved context only."""
        retrieval_payload = state.get("retrieval_payload") or {}
        answer = baml_runtime.AnswerFromContext(
            question=str(state.get("question") or ""),
            current_utc=str(state.get("current_utc") or ""),
            hints=str(state.get("hints") or ""),
            retrieval_json=json.dumps(retrieval_payload, ensure_ascii=True),
        )
        answer_payload = _model_payload(answer)
        supporting_record_ids = _valid_supporting_record_ids(
            answer_payload.get("supporting_record_ids"),
            retrieval_payload,
        )
        return {
            "result": ContextAnswerResult(
                answer=str(answer_payload.get("answer") or "").strip()
            ),
            "events": [
                {
                    "kind": "baml_call",
                    "function": "AnswerFromContext",
                    "supporting_record_ids": supporting_record_ids,
                }
            ],
            "done": True,
        }

    graph = StateGraph(ContextAnswererGraphState)
    graph.add_node("plan_retrieval", plan_retrieval)
    graph.add_node("execute_retrieval", execute_retrieval)
    graph.add_node("synthesize_answer", synthesize_answer)
    graph.add_edge(START, "plan_retrieval")
    graph.add_edge("plan_retrieval", "execute_retrieval")
    graph.add_edge("execute_retrieval", "synthesize_answer")
    graph.add_edge("synthesize_answer", END)
    return graph.compile()


def _plan_actions(plan: Any) -> list[dict[str, Any]]:
    """Return normalized retrieval actions from a BAML plan object."""
    payload = _model_payload(plan)
    return [_model_payload(action) for action in payload.get("actions") or []]


def _execute_actions(
    *,
    store: ContextStore,
    project_ids: list[str],
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Execute planned retrieval actions through ContextStore APIs."""
    events: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for index, action in enumerate(actions, start=1):
        action_type = str(action.get("action_type") or "").strip().lower()
        if action_type == "count":
            result = _execute_count(
                store=store,
                project_ids=project_ids,
                action=action,
            )
        elif action_type == "list":
            result = _execute_list(
                store=store,
                project_ids=project_ids,
                action=action,
            )
        elif action_type == "search":
            result = _execute_search(
                store=store,
                project_ids=project_ids,
                action=action,
            )
        else:
            raise ValueError(f"invalid_context_retrieval_action:{action_type}")
        events.append(
            {
                "kind": "retrieval",
                "index": index,
                "action_type": action_type,
                "result_count": int(result.get("count") or 0),
                "record_ids": [
                    str(record.get("record_id"))
                    for record in result.get("records", [])
                    if isinstance(record, dict) and record.get("record_id")
                ],
                "rationale": action.get("rationale"),
            }
        )
        results.append(result)
    return {"payload": {"actions": actions, "results": results}, "events": events}


def _execute_count(
    *,
    store: ContextStore,
    project_ids: list[str],
    action: dict[str, Any],
) -> dict[str, Any]:
    """Execute one exact count action."""
    payload = store.query(
        entity="records",
        mode="count",
        project_ids=project_ids,
        kind=_kind(action),
        status=_status(action),
        source_session_id=_text(action.get("source_session_id")),
        created_since=_text(action.get("created_since")),
        created_until=_text(action.get("created_until")),
        updated_since=_text(action.get("updated_since")),
        updated_until=_text(action.get("updated_until")),
        valid_at=_text(action.get("valid_at")),
        include_archived=bool(action.get("include_archived") or action.get("valid_at")),
    )
    return {"action_type": "count", "count": int(payload.get("count") or 0)}


def _execute_list(
    *,
    store: ContextStore,
    project_ids: list[str],
    action: dict[str, Any],
) -> dict[str, Any]:
    """Execute one exact list action."""
    payload = store.query(
        entity="records",
        mode="list",
        project_ids=project_ids,
        kind=_kind(action),
        status=_status(action),
        source_session_id=_text(action.get("source_session_id")),
        created_since=_text(action.get("created_since")),
        created_until=_text(action.get("created_until")),
        updated_since=_text(action.get("updated_since")),
        updated_until=_text(action.get("updated_until")),
        valid_at=_text(action.get("valid_at")),
        order_by=_order_by(action),
        limit=_limit(action),
        include_total=False,
        include_archived=bool(action.get("include_archived") or action.get("valid_at")),
    )
    return {
        "action_type": "list",
        "count": int(payload.get("count") or 0),
        "records": [_record_for_answer(row) for row in payload.get("rows") or []],
    }


def _execute_search(
    *,
    store: ContextStore,
    project_ids: list[str],
    action: dict[str, Any],
) -> dict[str, Any]:
    """Execute one semantic search action."""
    query = _text(action.get("query"))
    if not query:
        raise ValueError("context_search_query_required")
    hits = store.search(
        project_ids=project_ids,
        query=query,
        kind_filters=[kind] if (kind := _kind(action)) else None,
        statuses=[status] if (status := _status(action)) else None,
        valid_at=_text(action.get("valid_at")),
        include_archived=bool(action.get("include_archived") or action.get("valid_at")),
        limit=_limit(action),
    )
    return {
        "action_type": "search",
        "query": query,
        "count": len(hits),
        "records": [
            {
                "record_id": hit.record_id,
                "project_id": hit.project_id,
                "kind": hit.kind,
                "title": hit.title,
                "body": hit.body,
                "decision": hit.decision,
                "why": hit.why,
                "alternatives": hit.alternatives,
                "consequences": hit.consequences,
                "user_intent": hit.user_intent,
                "what_happened": hit.what_happened,
                "outcomes": hit.outcomes,
                "status": hit.status,
                "created_at": hit.created_at,
                "updated_at": hit.updated_at,
                "valid_from": hit.valid_from,
                "valid_until": hit.valid_until,
                "score": round(hit.score, 6),
                "sources": hit.sources,
            }
            for hit in hits
        ],
    }


def _record_for_answer(row: dict[str, Any]) -> dict[str, Any]:
    """Return the record fields available to answer synthesis."""
    return {
        "record_id": row.get("record_id"),
        "project_id": row.get("project_id"),
        "scope_type": row.get("scope_type"),
        "scope_id": row.get("scope_id"),
        "kind": row.get("kind"),
        "title": row.get("title"),
        "body": row.get("body"),
        "decision": row.get("decision"),
        "why": row.get("why"),
        "user_intent": row.get("user_intent"),
        "what_happened": row.get("what_happened"),
        "outcomes": row.get("outcomes"),
        "status": row.get("status"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "valid_from": row.get("valid_from"),
        "valid_until": row.get("valid_until"),
    }


def _kind(action: dict[str, Any]) -> str | None:
    """Return a canonical record kind filter."""
    raw = normalize_record_kind(action.get("kind"))
    if not raw:
        return None
    if raw not in ALLOWED_KINDS:
        raise ValueError(f"invalid_answer_kind:{raw}")
    return raw


def _status(action: dict[str, Any]) -> str | None:
    """Return a canonical status filter."""
    raw = normalize_record_status(action.get("status"), default="")
    if not raw:
        return None
    if raw not in ALLOWED_STATUSES:
        raise ValueError(f"invalid_answer_status:{raw}")
    return raw


def _order_by(action: dict[str, Any]) -> str:
    """Return a supported exact ordering field."""
    raw = _text(action.get("order_by")) or "updated_at"
    return raw if raw in {"created_at", "updated_at", "valid_from"} else "updated_at"


def _valid_supporting_record_ids(raw_ids: Any, retrieval_payload: dict[str, Any]) -> list[str]:
    """Keep answer citations constrained to actually retrieved record IDs."""
    allowed: set[str] = set()
    for result in retrieval_payload.get("results") or []:
        if not isinstance(result, dict):
            continue
        for record in result.get("records") or []:
            if isinstance(record, dict) and record.get("record_id"):
                allowed.add(str(record["record_id"]))
    valid: list[str] = []
    seen: set[str] = set()
    for item in raw_ids or []:
        record_id = str(item or "").strip()
        if record_id and record_id in allowed and record_id not in seen:
            seen.add(record_id)
            valid.append(record_id)
    return valid


def _limit(action: dict[str, Any]) -> int:
    """Return a bounded retrieval limit."""
    try:
        raw = int(action.get("limit") or 8)
    except (TypeError, ValueError):
        raw = 8
    return max(1, min(raw, 20))


def _text(value: Any) -> str | None:
    """Return stripped text or None."""
    text = str(value or "").strip()
    return text or None


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
