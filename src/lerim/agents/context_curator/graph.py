"""Clustered LangGraph context-curation pipeline backed by BAML."""

from __future__ import annotations

from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from lerim.agents.baml_runtime import build_baml_client_for_role
from lerim.agents.context_curator.inventory import (
    build_health_batches,
    build_similarity_clusters,
    format_records_json,
    load_active_records,
)
from lerim.agents.context_curator.operations import (
    apply_context_curation_plans,
    summarize_application,
)
from lerim.agents.context_curator.state import ContextCuratorGraphState
from lerim.config.settings import Config
from lerim.context import ProjectIdentity

MAX_BAML_MODEL_RETRIES = 3
BAML_RECOVERABLE_ERROR_NAMES = {
    "BamlClientFinishReasonError",
    "BamlClientHttpError",
    "BamlTimeoutError",
    "BamlValidationError",
}


def run_context_curator_graph(
    *,
    context_db_path,
    project_identity: ProjectIdentity,
    session_id: str,
    config: Config,
    provider: str | None = None,
    model_name: str | None = None,
    api_base_url: str | None = None,
    api_key: str | None = None,
    temperature: float | None = None,
    max_llm_calls: int | None = None,
    progress: bool = False,
) -> dict[str, Any]:
    """Run the BAML context-curator graph and return its final state."""
    graph = build_context_curator_graph(
        context_db_path=context_db_path,
        project_identity=project_identity,
        session_id=session_id,
        config=config,
        provider=provider,
        model_name=model_name,
        api_base_url=api_base_url,
        api_key=api_key,
        temperature=temperature,
        max_llm_calls=max_llm_calls or 40,
        progress=progress,
    )
    return graph.invoke(
        {
            "observations": [],
            "llm_calls": 0,
            "records": [],
            "records_by_id": {},
            "clusters": [],
            "clustered_record_ids": [],
            "health_batches": [],
            "action_plans": [],
            "done": False,
            "completion_summary": "",
        }
    )


def build_context_curator_graph(
    *,
    context_db_path,
    project_identity: ProjectIdentity,
    session_id: str,
    config: Config,
    provider: str | None,
    model_name: str | None,
    api_base_url: str | None,
    api_key: str | None,
    temperature: float | None,
    max_llm_calls: int,
    progress: bool = False,
):
    """Compile inventory, review, and mutation phases for context curation."""
    baml_runtime = build_baml_client_for_role(
        config=config,
        provider=provider,
        model_name=model_name,
        api_base_url=api_base_url,
        api_key=api_key,
        temperature=temperature,
    )
    run_instruction = _run_instruction()

    def load_inventory(state: ContextCuratorGraphState) -> dict[str, Any]:
        """Load active records into graph state."""
        del state
        records = load_active_records(
            context_db_path=context_db_path,
            project_identity=project_identity,
        )
        if progress:
            print(f"  context-curator inventory active_records={len(records)}", flush=True)
        return {
            "records": records,
            "records_by_id": {str(record["record_id"]): record for record in records},
            "observations": [
                _observation(
                    "load_inventory",
                    True,
                    f"active_records={len(records)}",
                    {"record_count": len(records)},
                )
            ],
        }

    def build_clusters(state: ContextCuratorGraphState) -> dict[str, Any]:
        """Build semantic-neighbor clusters and health batches."""
        records = state.get("records") or []
        clusters = build_similarity_clusters(
            context_db_path=context_db_path,
            project_identity=project_identity,
            records=records,
        )
        clustered_ids = {
            str(record_id)
            for cluster in clusters
            for record_id in (cluster.get("record_ids") or [])
        }
        if progress:
            print(
                f"  context-curator clusters={len(clusters)}",
                flush=True,
            )
        return {
            "clusters": clusters,
            "clustered_record_ids": sorted(clustered_ids),
            "observations": [
                _observation(
                    "build_similarity_clusters",
                    True,
                    f"clusters={len(clusters)} clustered_records={len(clustered_ids)}",
                    {
                        "cluster_count": len(clusters),
                        "clustered_record_count": len(clustered_ids),
                    },
                )
            ],
        }

    def review_clusters(state: ContextCuratorGraphState) -> dict[str, Any]:
        """Run BAML context curation over each semantic cluster."""
        llm_calls = int(state.get("llm_calls") or 0)
        observations: list[dict[str, Any]] = []
        plans: list[Any] = []
        for cluster in state.get("clusters") or []:
            if llm_calls >= max_llm_calls:
                raise RuntimeError(f"BAML context curator exceeded max_llm_calls={max_llm_calls}.")
            if progress:
                print(f"  context-curator review cluster {cluster.get('cluster_id')}", flush=True)
            result, retry_observations, attempts = _call_baml_with_retries(
                lambda instruction, cluster=cluster: baml_runtime.CurateContextCluster(
                    run_instruction=instruction,
                    cluster_id=str(cluster.get("cluster_id") or ""),
                    records_json=format_records_json(cluster.get("records") or []),
                ),
                stage="review_cluster",
                progress=progress,
                run_instruction=run_instruction,
                validate_result=lambda result, cluster=cluster: _validate_action_plan_for_records(
                    result,
                    records=cluster.get("records") or [],
                ),
            )
            llm_calls += attempts
            plans.append(result)
            action_count = len(_model_payload(result).get("actions") or [])
            observations.extend(retry_observations)
            observations.append(
                _observation(
                    "review_cluster",
                    True,
                    f"cluster={cluster.get('cluster_id')} actions={action_count}",
                    {
                        "cluster_id": cluster.get("cluster_id"),
                        "action_count": action_count,
                    },
                )
            )
        return {"llm_calls": llm_calls, "action_plans": plans, "observations": observations}

    def review_health(state: ContextCuratorGraphState) -> dict[str, Any]:
        """Run BAML context curation over singleton health batches."""
        llm_calls = int(state.get("llm_calls") or 0)
        excluded_record_ids = _planned_action_record_ids(state.get("action_plans") or [])
        health_batches = build_health_batches(
            records=state.get("records") or [],
            excluded_record_ids=excluded_record_ids,
        )
        observations: list[dict[str, Any]] = []
        plans: list[Any] = []
        for index, batch in enumerate(health_batches, start=1):
            if not batch:
                continue
            if llm_calls >= max_llm_calls:
                raise RuntimeError(f"BAML context curator exceeded max_llm_calls={max_llm_calls}.")
            if progress:
                print(f"  context-curator review health batch {index}", flush=True)
            result, retry_observations, attempts = _call_baml_with_retries(
                lambda instruction, batch=batch, index=index: baml_runtime.CurateRecordHealthBatch(
                    run_instruction=instruction,
                    batch_id=f"health_{index}",
                    records_json=format_records_json(batch),
                ),
                stage="review_health",
                progress=progress,
                run_instruction=run_instruction,
                validate_result=lambda result, batch=batch: _validate_action_plan_for_records(
                    result,
                    records=batch,
                ),
            )
            llm_calls += attempts
            plans.append(result)
            action_count = len(_model_payload(result).get("actions") or [])
            observations.extend(retry_observations)
            observations.append(
                _observation(
                    "review_health_batch",
                    True,
                    f"batch={index} actions={action_count}",
                    {"batch_id": f"health_{index}", "action_count": action_count},
                )
            )
        return {
            "llm_calls": llm_calls,
            "health_batches": health_batches,
            "action_plans": plans,
            "observations": observations,
        }

    def apply_actions(state: ContextCuratorGraphState) -> dict[str, Any]:
        """Validate and apply proposed context-curation actions."""
        evidence_record_ids = {
            str(record_id)
            for record_id in (state.get("records_by_id") or {}).keys()
            if str(record_id).strip()
        }
        summary = apply_context_curation_plans(
            context_db_path=context_db_path,
            project_identity=project_identity,
            session_id=session_id,
            action_plans=state.get("action_plans") or [],
            evidence_record_ids=evidence_record_ids,
        )
        completion_summary = summarize_application(summary)
        final = _observation(
            "final_result",
            True,
            completion_summary,
            {
                "records_created": summary.records_created,
                "records_updated": summary.records_updated,
                "records_archived": summary.records_archived,
                "applied_actions": summary.applied_actions,
            },
        )
        final["done"] = True
        final["completion_summary"] = completion_summary
        if progress:
            print(f"  context-curator apply actions={summary.applied_actions}", flush=True)
        return {
            "observations": [*summary.observations, final],
            "done": True,
            "completion_summary": completion_summary,
        }

    graph = StateGraph(ContextCuratorGraphState)
    graph.add_node("load_inventory", load_inventory)
    graph.add_node("build_clusters", build_clusters)
    graph.add_node("review_clusters", review_clusters)
    graph.add_node("review_health", review_health)
    graph.add_node("apply_actions", apply_actions)
    graph.add_edge(START, "load_inventory")
    graph.add_edge("load_inventory", "build_clusters")
    graph.add_edge("build_clusters", "review_clusters")
    graph.add_edge("review_clusters", "review_health")
    graph.add_edge("review_health", "apply_actions")
    graph.add_edge("apply_actions", END)
    return graph.compile()


def _run_instruction() -> str:
    """Return context-curation task framing for BAML calls."""
    return (
        "Keep the context store useful, current, compact, and non-duplicative. "
        "Cluster review handles duplicate, overlapping, or contradictory records. "
        "Health review handles single-record problems such as routine episodes or verbose records. "
        "Prefer no action for healthy records. Prefer supersession over direct archive for duplicate or replaced durable records."
    )


def _call_baml_with_retries(
    call: Callable[[str], Any],
    *,
    stage: str,
    progress: bool,
    run_instruction: str,
    validate_result: Callable[[Any], str | None] | None = None,
) -> tuple[Any, list[dict[str, Any]], int]:
    """Run one BAML call with graph-visible recoverable retries."""
    observations: list[dict[str, Any]] = []
    attempts = 0
    validation_feedback = ""
    while True:
        attempts += 1
        try:
            result = call(_instruction_with_validation_feedback(run_instruction, validation_feedback))
        except Exception as exc:
            if not _is_recoverable_baml_error(exc) or attempts > MAX_BAML_MODEL_RETRIES:
                raise
            if progress:
                print(f"  context-curator retry {stage} attempt={attempts}", flush=True)
            observations.append(
                _observation(
                    "model_retry",
                    False,
                    _model_retry_observation(exc),
                    {"stage": stage, "attempt": attempts},
                )
            )
            continue
        if validate_result is None:
            return result, observations, attempts
        validation_error = validate_result(result)
        if not validation_error:
            return result, observations, attempts
        observations.append(
            _observation(
                "model_retry",
                False,
                _semantic_retry_observation(validation_error),
                {"stage": stage, "attempt": attempts},
            )
        )
        if attempts > MAX_BAML_MODEL_RETRIES:
            return result, observations, attempts
        validation_feedback = validation_error
        if progress:
            print(f"  context-curator retry {stage} attempt={attempts}", flush=True)


def _model_payload(value: Any) -> dict[str, Any]:
    """Convert generated BAML objects into plain dictionaries."""
    if hasattr(value, "model_dump"):
        return _plain_value(value.model_dump(exclude_none=True))
    if isinstance(value, dict):
        return _plain_value({key: item for key, item in value.items() if item is not None})
    if value is None:
        return {}
    return _plain_value(getattr(value, "__dict__", {}))


def _planned_action_record_ids(action_plans: list[Any]) -> set[str]:
    """Return record IDs already targeted by non-noop cluster actions."""
    record_ids: set[str] = set()
    for plan in action_plans:
        for raw_action in _model_payload(plan).get("actions") or []:
            action = _model_payload(raw_action)
            action_type = str(action.get("action_type") or "").strip().lower()
            record_id = str(action.get("record_id") or "").strip()
            if record_id and action_type and action_type != "noop":
                record_ids.add(record_id)
    return record_ids


def _validate_action_plan_for_records(result: Any, *, records: list[dict[str, Any]]) -> str | None:
    """Return semantic validation feedback for a BAML action plan."""
    records_by_id = {str(record.get("record_id") or ""): record for record in records}
    touched_record_ids: set[str] = set()
    has_non_noop_action = False
    for raw_action in _model_payload(result).get("actions") or []:
        action = _model_payload(raw_action)
        action_type = _clean_action_type(action.get("action_type"))
        record_id = str(action.get("record_id") or "").strip()
        if action_type == "noop":
            continue
        has_non_noop_action = True
        if action_type not in {"archive", "revise", "supersede"}:
            return f"unsupported action_type {action_type or '<empty>'}"
        if not record_id:
            return "non-noop actions must include record_id"
        if record_id not in records_by_id:
            return f"record_id {record_id} was not in the reviewed records"
        if record_id in touched_record_ids:
            return f"record_id {record_id} has more than one non-noop action"
        touched_record_ids.add(record_id)
        if action_type == "supersede":
            replacement_record_id = str(action.get("replacement_record_id") or "").strip()
            if not replacement_record_id:
                return f"supersede action for {record_id} must include replacement_record_id"
            if replacement_record_id == record_id:
                return f"supersede action for {record_id} cannot replace itself"
            if replacement_record_id not in records_by_id:
                return f"replacement_record_id {replacement_record_id} was not in the reviewed records"
        if action_type == "revise":
            cosmetic_error = _validate_revision_is_substantive(
                action.get("patch"),
                record=records_by_id[record_id],
                record_id=record_id,
            )
            if cosmetic_error:
                return cosmetic_error
            patch_error = _validate_revision_patch(
                action.get("patch"),
                record=records_by_id[record_id],
                record_id=record_id,
            )
            if patch_error:
                return patch_error
    if not has_non_noop_action:
        for record in records:
            verbose_error = _validate_noop_keeps_compact_records_only(record)
            if verbose_error:
                return verbose_error
    return None


def _validate_noop_keeps_compact_records_only(record: dict[str, Any]) -> str | None:
    """Reject no-op plans for verbose decision records that need compaction."""
    record_id = str(record.get("record_id") or "").strip() or "<unknown>"
    kind = str(record.get("kind") or "").strip().lower()
    if kind != "decision":
        return None
    if not str(record.get("decision") or "").strip() or not str(record.get("why") or "").strip():
        return None
    body = str(record.get("body") or "").strip()
    if len(body) <= 220:
        return None
    return (
        f"record {record_id} is a verbose decision body, so no action is unsafe. "
        "Return a revise action with compact title/body while preserving decision and why."
    )


def _validate_revision_is_substantive(
    patch: Any, *, record: dict[str, Any], record_id: str
) -> str | None:
    """Reject cosmetic rewrites of already-compact decision records."""
    payload = _model_payload(patch)
    current_kind = str(record.get("kind") or "").strip().lower()
    if current_kind != "decision":
        return None
    if not str(record.get("decision") or "").strip() or not str(record.get("why") or "").strip():
        return None
    current_title = str(record.get("title") or "").strip()
    current_body = str(record.get("body") or "").strip()
    if len(current_title) > 96 or len(current_body) > 180:
        return None
    patch_decision = str(payload.get("decision") or "").strip()
    patch_why = str(payload.get("why") or "").strip()
    if patch_decision != str(record.get("decision") or "").strip():
        return None
    if patch_why != str(record.get("why") or "").strip():
        return None
    return (
        f"revise action for {record_id} is cosmetic: the existing decision is compact "
        "and its decision/why fields already carry the reusable guidance. Return no action."
    )


def _validate_revision_patch(patch: Any, *, record: dict[str, Any], record_id: str) -> str | None:
    """Return feedback for an incomplete or unsafe revise patch."""
    payload = _model_payload(patch)
    if not payload:
        return f"revise action for {record_id} must include a complete patch"
    current_kind = str(record.get("kind") or "").strip().lower()
    patch_kind = str(payload.get("kind") or "").strip().lower()
    if not patch_kind:
        return f"revise patch for {record_id} must include kind"
    if current_kind and patch_kind and current_kind != patch_kind:
        return f"revise patch for {record_id} must keep kind={current_kind}"
    for field_name in ("title", "body"):
        if not str(payload.get(field_name) or "").strip():
            return f"revise patch for {record_id} must include non-empty {field_name}"
    if current_kind == "episode":
        for field_name in ("user_intent", "what_happened", "outcomes"):
            if not str(payload.get(field_name) or "").strip():
                return f"episode revise patch for {record_id} must include non-empty {field_name}"
    if current_kind == "decision":
        for field_name in ("decision", "why"):
            if not str(payload.get(field_name) or "").strip():
                return f"decision revise patch for {record_id} must include non-empty {field_name}"
    return None


def _clean_action_type(value: Any) -> str:
    """Normalize generated enum/string action type values."""
    enum_value = getattr(value, "value", None)
    text = str(enum_value if enum_value is not None else value or "").strip().lower()
    return text or "noop"


def _instruction_with_validation_feedback(run_instruction: str, validation_feedback: str) -> str:
    """Add compact retry feedback to the BAML run instruction."""
    if not validation_feedback:
        return run_instruction
    return (
        f"{run_instruction}\n\n"
        "Previous structured output was unsafe or incomplete. "
        f"Fix this validation error and return a complete corrected action plan: {validation_feedback}"
    )


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


def _is_recoverable_baml_error(exc: Exception) -> bool:
    """Return whether a BAML model/parsing failure should be retried."""
    return type(exc).__name__ in BAML_RECOVERABLE_ERROR_NAMES


def _model_retry_observation(exc: Exception) -> str:
    """Render a compact model failure note."""
    message = str(exc).replace("\n", " ")[:1200]
    return (
        "The previous BAML model call did not produce valid structured output. "
        "Retry and return exactly one JSON object matching the requested schema. "
        "Do not include <think> tags, hidden reasoning, markdown, or prose before "
        f"the JSON. Error: {type(exc).__name__}: {message}"
    )


def _semantic_retry_observation(validation_error: str) -> str:
    """Render compact feedback for a schema-valid but unsafe model plan."""
    return (
        "The previous BAML model call produced structured output, but the proposed "
        "context-curation action plan was unsafe or incomplete. Retry with a complete safe "
        f"plan. Validation error: {validation_error}"
    )


def _observation(action: str, ok: bool, content: str, args: dict[str, Any]) -> dict[str, Any]:
    """Build one context-curator graph observation."""
    return {
        "action": action,
        "ok": ok,
        "content": content,
        "args": args,
        "done": False,
        "completion_summary": "",
    }
