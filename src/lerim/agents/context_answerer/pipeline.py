"""DSPy context-answer pipeline with deterministic store retrieval."""

from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lerim.agents.dspy_compat import dspy

from lerim.agents.context_answerer.signatures import AnswerFromContext, PlanContextRetrieval
from lerim.agents.context_answerer.types import ContextAnswerResult
from lerim.agents.model_helpers import call_model_step, prediction_payload
from lerim.agents.model_runtime import ModelRuntime, build_model_runtime
from lerim.config.settings import Config
from lerim.context import ContextStore, ProjectIdentity
from lerim.context.roles import ALLOWED_RECORD_ROLES, normalize_record_role
from lerim.context.spec import (
    ALLOWED_KINDS,
    ALLOWED_STATUSES,
    normalize_record_kind,
    normalize_record_status,
)

ANSWER_SYNTHESIS_RUN_INSTRUCTION = (
    "Write a complete user-facing answer from retrieval_json only. "
    "If there is no direct stored support, say that directly. "
    "Never return placeholders or schema examples."
)
PLAN_RETRIEVAL_RUN_INSTRUCTION = (
    "Plan the smallest valid context-store retrieval. "
    "Every search action must include a non-empty query. "
    "Use count or list when no semantic query is needed."
)


class ContextAnswerPipeline(dspy.Module):
    """Plan retrieval, read the context store, and synthesize a grounded answer."""

    def __init__(
        self,
        *,
        store: ContextStore,
        project_ids: list[str],
        config: Config,
        runtime: ModelRuntime | None = None,
        plan_step: Any | None = None,
        answer_step: Any | None = None,
    ) -> None:
        """Create the answer pipeline with optional test doubles for model steps."""
        super().__init__()
        self.store = store
        self.project_ids = project_ids
        self.max_actions = max(1, int(config.agent_role.answer_max_retrieval_actions))
        self.config = config
        self.runtime = runtime
        self.adapter = dspy.JSONAdapter()
        self.uses_real_model = plan_step is None or answer_step is None
        self.plan_step = plan_step or dspy.Predict(PlanContextRetrieval)
        self.answer_step = answer_step or dspy.Predict(AnswerFromContext)

    def forward(self, *, question: str, current_utc: str, hints: str = "") -> dict[str, Any]:
        """Run the full answer workflow and return the stable state payload."""
        events: list[dict[str, Any]] = []
        with self.model_context():
            plan, retry_events, plan_attempts = call_model_step(
                lambda instruction: self.plan_step(
                    run_instruction=instruction,
                    question=question,
                    current_utc=current_utc,
                    hints=hints,
                ),
                stage="plan_context_retrieval",
                progress=False,
                progress_label="context-answerer",
                run_instruction=PLAN_RETRIEVAL_RUN_INSTRUCTION,
                validate_result=lambda result: validate_retrieval_plan_result(
                    result,
                    max_actions=self.max_actions,
                ),
                make_observation=model_retry_event,
                semantic_retry_content=plan_retry_content,
                validation_retry_target="complete corrected retrieval plan",
            )
            events.extend(retry_events)
            actions = plan_actions(plan)
            events.append(
                {
                    "kind": "model_step",
                    "stage": "plan_retrieval",
                    "attempts": plan_attempts,
                    "action_count": len(actions),
                    "rationale": prediction_payload(plan, output_field="plan").get(
                        "rationale"
                    ),
                }
            )
            retrieval = execute_actions(
                store=self.store,
                project_ids=self.project_ids,
                actions=actions,
            )
            events.extend(retrieval["events"])
            answer, retry_events, answer_attempts = call_model_step(
                lambda instruction: self.answer_step(
                    run_instruction=instruction,
                    question=question,
                    current_utc=current_utc,
                    hints=hints,
                    retrieval_json=json.dumps(retrieval["payload"], ensure_ascii=True),
                ),
                stage="answer_from_context",
                progress=False,
                progress_label="context-answerer",
                run_instruction=ANSWER_SYNTHESIS_RUN_INSTRUCTION,
                validate_result=lambda result: validate_answer_result(
                    result,
                    retrieval_payload=retrieval["payload"],
                ),
                make_observation=model_retry_event,
                semantic_retry_content=answer_retry_content,
                validation_retry_target="complete corrected context answer",
            )
        events.extend(retry_events)
        answer_payload = prediction_payload(answer, output_field="answer")
        supporting_record_ids = valid_supporting_record_ids(
            answer_payload.get("supporting_record_ids"),
            retrieval["payload"],
        )
        events.append(
            {
                "kind": "model_step",
                "stage": "write_answer",
                "attempts": answer_attempts,
                "supporting_record_ids": supporting_record_ids,
            }
        )
        return {
            "result": ContextAnswerResult(
                answer=str(answer_payload.get("answer") or "").strip(),
                supporting_record_ids=supporting_record_ids,
            ),
            "events": events,
            "done": True,
        }

    def model_context(self):
        """Return a DSPy context only when real predictors need a configured LM."""
        if not self.uses_real_model:
            return nullcontext()
        if self.runtime is None:
            self.runtime = build_model_runtime(config=self.config)
        return dspy.context(lm=self.runtime.lm, adapter=self.adapter)


def run_context_answer_pipeline(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity,
    project_ids: list[str] | None,
    question: str,
    config: Config,
    hints: str = "",
    steps: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the context-answer pipeline and return its final state."""
    store = ContextStore(context_db_path)
    store.initialize()
    store.register_project(project_identity)
    pipeline = ContextAnswerPipeline(
        store=store,
        project_ids=project_ids or [project_identity.project_id],
        config=config,
        plan_step=(steps or {}).get("plan"),
        answer_step=(steps or {}).get("answer"),
    )
    return pipeline(
        question=question.strip(),
        current_utc=datetime.now(timezone.utc).isoformat(),
        hints=hints.strip(),
    )


def plan_actions(plan: Any) -> list[dict[str, Any]]:
    """Return normalized retrieval actions from a model plan object."""
    payload = prediction_payload(plan, output_field="plan")
    return [prediction_payload(action) for action in payload.get("actions") or []]


def validate_retrieval_plan_result(result: Any, *, max_actions: int) -> str | None:
    """Reject retrieval plans that would fail before executing store reads."""
    actions = plan_actions(result)
    if not actions:
        return "retrieval plan must include at least one action"
    if len(actions) > max_actions:
        return f"retrieval plan has too many actions: {len(actions)}>{max_actions}"
    for index, action in enumerate(actions, start=1):
        action_type = str(action.get("action_type") or "").strip().lower()
        if action_type not in {"count", "list", "search"}:
            return f"action {index} has invalid action_type: {action_type}"
        if action_type == "search" and not text(action.get("query")):
            return f"search action {index} must include a non-empty query"
    return None


def execute_actions(
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
            result = execute_count(store=store, project_ids=project_ids, action=action)
        elif action_type == "list":
            result = execute_list(store=store, project_ids=project_ids, action=action)
        elif action_type == "search":
            result = execute_search(store=store, project_ids=project_ids, action=action)
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


def execute_count(
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
        kind=record_kind(action),
        record_role=record_role(action),
        status=record_status(action),
        source_session_id=text(action.get("source_session_id")),
        created_since=text(action.get("created_since")),
        created_until=text(action.get("created_until")),
        updated_since=text(action.get("updated_since")),
        updated_until=text(action.get("updated_until")),
        valid_at=text(action.get("valid_at")),
        include_archived=bool(action.get("include_archived") or action.get("valid_at")),
    )
    return {"action_type": "count", "count": int(payload.get("count") or 0)}


def execute_list(
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
        kind=record_kind(action),
        record_role=record_role(action),
        status=record_status(action),
        source_session_id=text(action.get("source_session_id")),
        created_since=text(action.get("created_since")),
        created_until=text(action.get("created_until")),
        updated_since=text(action.get("updated_since")),
        updated_until=text(action.get("updated_until")),
        valid_at=text(action.get("valid_at")),
        order_by=order_by(action),
        limit=limit(action),
        include_total=False,
        include_archived=bool(action.get("include_archived") or action.get("valid_at")),
    )
    return {
        "action_type": "list",
        "count": int(payload.get("count") or 0),
        "records": [record_for_answer(row) for row in payload.get("rows") or []],
    }


def execute_search(
    *,
    store: ContextStore,
    project_ids: list[str],
    action: dict[str, Any],
) -> dict[str, Any]:
    """Execute one semantic search action."""
    query = text(action.get("query"))
    if not query:
        raise ValueError("context_search_query_required")
    hits = store.search(
        project_ids=project_ids,
        query=query,
        kind_filters=[kind] if (kind := record_kind(action)) else None,
        role_filters=[role] if (role := record_role(action)) else None,
        statuses=[status] if (status := record_status(action)) else None,
        valid_at=text(action.get("valid_at")),
        include_archived=bool(action.get("include_archived") or action.get("valid_at")),
        limit=limit(action),
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
                "record_role": hit.record_role,
                "role_payload": hit.role_payload,
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


def record_for_answer(row: dict[str, Any]) -> dict[str, Any]:
    """Return the record fields available to answer synthesis."""
    return {
        "record_id": row.get("record_id"),
        "project_id": row.get("project_id"),
        "scope_type": row.get("scope_type"),
        "scope_id": row.get("scope_id"),
        "kind": row.get("kind"),
        "record_role": row.get("record_role"),
        "role_payload": row.get("role_payload"),
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


def record_kind(action: dict[str, Any]) -> str | None:
    """Return a canonical record kind filter."""
    raw = normalize_record_kind(action.get("kind"))
    if not raw:
        return None
    if raw not in ALLOWED_KINDS:
        raise ValueError(f"invalid_answer_kind:{raw}")
    return raw


def record_role(action: dict[str, Any]) -> str | None:
    """Return a canonical operational role filter."""
    raw = str(action.get("record_role") or "").strip()
    if not raw:
        return None
    role = normalize_record_role(raw)
    if role not in ALLOWED_RECORD_ROLES:
        raise ValueError(f"invalid_answer_record_role:{role}")
    return role


def record_status(action: dict[str, Any]) -> str | None:
    """Return a canonical status filter."""
    raw = normalize_record_status(action.get("status"), default="")
    if not raw:
        return None
    if raw not in ALLOWED_STATUSES:
        raise ValueError(f"invalid_answer_status:{raw}")
    return raw


def order_by(action: dict[str, Any]) -> str:
    """Return a supported exact ordering field."""
    raw = text(action.get("order_by")) or "updated_at"
    return raw if raw in {"created_at", "updated_at", "valid_from"} else "updated_at"


def valid_supporting_record_ids(
    raw_ids: Any,
    retrieval_payload: dict[str, Any],
) -> list[str]:
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


def validate_answer_result(
    result: Any,
    *,
    retrieval_payload: dict[str, Any],
) -> str | None:
    """Reject structured answer payloads that cannot be shown to users."""
    answer_payload = prediction_payload(result, output_field="answer")
    answer = str(answer_payload.get("answer") or "").strip()
    if is_placeholder_only_answer(answer):
        return (
            "answer is empty or placeholder-only; write a complete answer from the "
            "retrieved records, or explicitly state that there is no direct stored support"
        )
    raw_ids = answer_payload.get("supporting_record_ids")
    if raw_ids is not None and not isinstance(raw_ids, list):
        return "supporting_record_ids must be a list"
    return None


def is_placeholder_only_answer(answer: str) -> bool:
    """Return whether an answer has no substantive alphanumeric content."""
    return not answer or not any(character.isalnum() for character in answer)


def model_retry_event(
    action: str,
    ok: bool,
    content: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Render retry observations in the context-answer event shape."""
    return {"kind": action, "ok": ok, "content": content, **args}


def answer_retry_content(validation_error: str) -> str:
    """Return compact retry guidance for semantically invalid answer output."""
    return (
        "The previous answer output could not be shown to the user. "
        "Return exactly one structured answer object with a substantive answer and "
        f"retrieved record IDs only. Validation error: {validation_error}"
    )


def plan_retry_content(validation_error: str) -> str:
    """Return compact retry guidance for semantically invalid retrieval plans."""
    return (
        "The previous retrieval plan could not be executed. "
        "Return exactly one structured retrieval plan using only valid actions. "
        f"Validation error: {validation_error}"
    )


def limit(action: dict[str, Any]) -> int:
    """Return a bounded retrieval limit."""
    try:
        raw = int(action.get("limit") or 8)
    except (TypeError, ValueError):
        raw = 8
    return max(1, min(raw, 20))


def text(value: Any) -> str | None:
    """Return stripped text or None."""
    raw = str(value or "").strip()
    return raw or None
