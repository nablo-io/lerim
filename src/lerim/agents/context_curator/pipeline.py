"""DSPy context curation pipeline over active context records."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Any

from lerim.agents.dspy_compat import dspy

from lerim.agents.context_curator.inventory import (
    build_health_batches,
    build_similarity_clusters,
    format_records_json,
    load_active_records,
    load_seed_and_neighbors,
)
from lerim.agents.context_curator.operations import (
    apply_context_curation_plans,
    summarize_application,
)
from lerim.agents.context_curator.signatures import (
    CurateContextCluster,
    CurateRecordHealthBatch,
)
from lerim.agents.model_helpers import call_model_step, prediction_payload
from lerim.agents.model_runtime import ModelRuntime, build_model_runtime
from lerim.config.settings import Config
from lerim.context import ProjectIdentity

RUN_INSTRUCTION = (
    "Keep the context store useful, current, compact, and non-duplicative. "
    "Cluster review handles duplicate, overlapping, or contradictory records. "
    "Health review handles single-record problems such as routine episodes or verbose records. "
    "Prefer no action for healthy records. Prefer supersession over direct archive for duplicate or replaced durable records."
)


class ContextCuratorPipeline(dspy.Module):
    """Load records, review curation actions, and apply safe mutations."""

    def __init__(
        self,
        *,
        context_db_path: Path,
        project_identity: ProjectIdentity,
        session_id: str,
        config: Config,
        provider: str | None = None,
        model_name: str | None = None,
        api_base_url: str | None = None,
        api_key: str | None = None,
        temperature: float | None = None,
        max_model_steps: int = 40,
        progress: bool = False,
        runtime: ModelRuntime | None = None,
        seed_record_ids: list[str] | None = None,
        cluster_step: Any | None = None,
        health_step: Any | None = None,
    ) -> None:
        """Create the context curator pipeline.

        When seed_record_ids is set, the pipeline runs a scoped write-time
        reconciliation pass over those records plus their active semantic neighbors
        instead of the whole active project, and skips single-record health review.
        """
        super().__init__()
        self.context_db_path = context_db_path
        self.project_identity = project_identity
        self.session_id = session_id
        self.config = config
        self.provider = provider
        self.model_name = model_name
        self.api_base_url = api_base_url
        self.api_key = api_key
        self.temperature = temperature
        self.max_model_steps = max(1, int(max_model_steps))
        self.progress = progress
        self.runtime = runtime
        self.adapter = dspy.JSONAdapter()
        self.seed_record_ids = [
            str(record_id).strip()
            for record_id in (seed_record_ids or [])
            if str(record_id).strip()
        ]
        self.uses_real_model = cluster_step is None or health_step is None
        self.cluster_step = cluster_step or dspy.Predict(CurateContextCluster)
        self.health_step = health_step or dspy.Predict(CurateRecordHealthBatch)

    def forward(self) -> dict[str, Any]:
        """Run inventory, model review, and context-store mutation."""
        scoped = bool(self.seed_record_ids)
        if scoped:
            records = load_seed_and_neighbors(
                context_db_path=self.context_db_path,
                project_identity=self.project_identity,
                seed_record_ids=self.seed_record_ids,
            )
        else:
            records = load_active_records(
                context_db_path=self.context_db_path,
                project_identity=self.project_identity,
            )
        inventory_mode = "reconcile" if scoped else "active"
        if self.progress:
            print(
                f"  context-curator inventory {inventory_mode}_records={len(records)}",
                flush=True,
            )
        observations = [
            observation(
                "load_inventory",
                True,
                f"{inventory_mode}_records={len(records)}",
                {"record_count": len(records), "scoped": scoped},
            )
        ]
        clusters = build_similarity_clusters(
            context_db_path=self.context_db_path,
            project_identity=self.project_identity,
            records=records,
        )
        clustered_ids = {
            str(record_id)
            for cluster in clusters
            for record_id in (cluster.get("record_ids") or [])
        }
        if self.progress:
            print(f"  context-curator clusters={len(clusters)}", flush=True)
        observations.append(
            observation(
                "build_similarity_clusters",
                True,
                f"clusters={len(clusters)} clustered_records={len(clustered_ids)}",
                {
                    "cluster_count": len(clusters),
                    "clustered_record_count": len(clustered_ids),
                },
            )
        )
        cluster_review = self.review_clusters(clusters)
        observations.extend(cluster_review["observations"])
        if scoped:
            # Write-time reconciliation only resolves duplicate/superseded records
            # across the seed's neighborhood; single-record health review stays with
            # the periodic curator so a brand-new record is not archived on write.
            health_batches: list[list[dict[str, Any]]] = []
            health_action_plans: list[Any] = []
            model_steps = cluster_review["model_steps"]
        else:
            health_review = self.review_health(
                records=records,
                prior_action_plans=cluster_review["action_plans"],
                model_steps=cluster_review["model_steps"],
            )
            observations.extend(health_review["observations"])
            health_batches = health_review["health_batches"]
            health_action_plans = health_review["action_plans"]
            model_steps = health_review["model_steps"]
        action_plans = [
            *cluster_review["action_plans"],
            *health_action_plans,
        ]
        evidence_record_ids = {
            str(record.get("record_id") or "")
            for record in records
            if str(record.get("record_id") or "").strip()
        }
        summary = apply_context_curation_plans(
            context_db_path=self.context_db_path,
            project_identity=self.project_identity,
            session_id=self.session_id,
            action_plans=action_plans,
            evidence_record_ids=evidence_record_ids,
            protected_record_ids=set(self.seed_record_ids) if scoped else None,
        )
        completion_summary = summarize_application(summary)
        final = observation(
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
        if self.progress:
            print(f"  context-curator apply actions={summary.applied_actions}", flush=True)
        return {
            "observations": [*observations, *summary.observations, final],
            "model_steps": model_steps,
            "llm_calls": model_steps,
            "records": records,
            "records_by_id": {str(record["record_id"]): record for record in records},
            "clusters": clusters,
            "clustered_record_ids": sorted(clustered_ids),
            "health_batches": health_batches,
            "action_plans": action_plans,
            "done": True,
            "completion_summary": completion_summary,
        }

    def model_context(self):
        """Return a DSPy context only when real predictors need a configured LM."""
        if not self.uses_real_model:
            return nullcontext()
        if self.runtime is None:
            self.runtime = build_model_runtime(
                config=self.config,
                provider=self.provider,
                model_name=self.model_name,
                api_base_url=self.api_base_url,
                api_key=self.api_key,
                temperature=self.temperature,
            )
        return dspy.context(lm=self.runtime.lm, adapter=self.adapter)

    def review_clusters(self, clusters: list[dict[str, Any]]) -> dict[str, Any]:
        """Run model curation over each semantic cluster."""
        model_steps = 0
        observations: list[dict[str, Any]] = []
        plans: list[Any] = []
        for cluster in clusters:
            if model_steps >= self.max_model_steps:
                raise RuntimeError(
                    f"context curator exceeded max_model_steps={self.max_model_steps}"
                )
            if self.progress:
                print(f"  context-curator review cluster {cluster.get('cluster_id')}", flush=True)
            with self.model_context():
                result, retry_observations, attempts = call_model_step(
                    lambda instruction, cluster=cluster: self.cluster_step(
                        run_instruction=instruction,
                        cluster_id=str(cluster.get("cluster_id") or ""),
                        records_json=format_records_json(cluster.get("records") or []),
                    ),
                    stage="review_cluster",
                    progress=self.progress,
                    progress_label="context-curator",
                    run_instruction=RUN_INSTRUCTION,
                    validate_result=lambda result, cluster=cluster: validate_action_plan_for_records(
                        result,
                        records=cluster.get("records") or [],
                    ),
                    make_observation=observation,
                    semantic_retry_content=semantic_retry_observation,
                    validation_retry_target="complete corrected action plan",
                    raise_on_validation_failure=False,
                )
            model_steps += attempts
            plans.append(result)
            action_count = len(plan_payload(result).get("actions") or [])
            observations.extend(retry_observations)
            observations.append(
                observation(
                    "review_cluster",
                    True,
                    f"cluster={cluster.get('cluster_id')} actions={action_count}",
                    {
                        "cluster_id": cluster.get("cluster_id"),
                        "action_count": action_count,
                    },
                )
            )
        return {"model_steps": model_steps, "action_plans": plans, "observations": observations}

    def review_health(
        self,
        *,
        records: list[dict[str, Any]],
        prior_action_plans: list[Any],
        model_steps: int,
    ) -> dict[str, Any]:
        """Run model curation over singleton health batches."""
        excluded_record_ids = planned_action_record_ids(prior_action_plans)
        health_batches = build_health_batches(
            records=records,
            excluded_record_ids=excluded_record_ids,
        )
        observations: list[dict[str, Any]] = []
        plans: list[Any] = []
        for index, batch in enumerate(health_batches, start=1):
            if not batch:
                continue
            if model_steps >= self.max_model_steps:
                raise RuntimeError(
                    f"context curator exceeded max_model_steps={self.max_model_steps}"
                )
            if self.progress:
                print(f"  context-curator review health batch {index}", flush=True)
            with self.model_context():
                result, retry_observations, attempts = call_model_step(
                    lambda instruction, batch=batch, index=index: self.health_step(
                        run_instruction=instruction,
                        batch_id=f"health_{index}",
                        records_json=format_records_json(batch),
                    ),
                    stage="review_health",
                    progress=self.progress,
                    progress_label="context-curator",
                    run_instruction=RUN_INSTRUCTION,
                    validate_result=lambda result, batch=batch: validate_action_plan_for_records(
                        result,
                        records=batch,
                    ),
                    make_observation=observation,
                    semantic_retry_content=semantic_retry_observation,
                    validation_retry_target="complete corrected action plan",
                    raise_on_validation_failure=False,
                )
            model_steps += attempts
            plans.append(result)
            action_count = len(plan_payload(result).get("actions") or [])
            observations.extend(retry_observations)
            observations.append(
                observation(
                    "review_health_batch",
                    True,
                    f"batch={index} actions={action_count}",
                    {"batch_id": f"health_{index}", "action_count": action_count},
                )
            )
        return {
            "model_steps": model_steps,
            "health_batches": health_batches,
            "action_plans": plans,
            "observations": observations,
        }


def planned_action_record_ids(action_plans: list[Any]) -> set[str]:
    """Return record IDs already targeted by non-noop actions."""
    record_ids: set[str] = set()
    for plan in action_plans:
        for raw_action in plan_payload(plan).get("actions") or []:
            action = prediction_payload(raw_action)
            action_type = clean_action_type(action.get("action_type"))
            record_id = str(action.get("record_id") or "").strip()
            if record_id and action_type != "noop":
                record_ids.add(record_id)
    return record_ids


def validate_action_plan_for_records(
    result: Any,
    *,
    records: list[dict[str, Any]],
) -> str | None:
    """Return semantic validation feedback for a curation action plan."""
    records_by_id = {str(record.get("record_id") or ""): record for record in records}
    touched_record_ids: set[str] = set()
    has_non_noop_action = False
    for raw_action in plan_payload(result).get("actions") or []:
        action = prediction_payload(raw_action)
        action_type = clean_action_type(action.get("action_type"))
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
                return (
                    f"replacement_record_id {replacement_record_id} "
                    "was not in the reviewed records"
                )
        if action_type == "revise":
            cosmetic_error = validate_revision_is_substantive(
                action.get("patch"),
                record=records_by_id[record_id],
                record_id=record_id,
            )
            if cosmetic_error:
                return cosmetic_error
            patch_error = validate_revision_patch(
                action.get("patch"),
                record=records_by_id[record_id],
                record_id=record_id,
            )
            if patch_error:
                return patch_error
    if not has_non_noop_action:
        for record in records:
            verbose_error = validate_noop_keeps_compact_records_only(record)
            if verbose_error:
                return verbose_error
    return None


def validate_noop_keeps_compact_records_only(record: dict[str, Any]) -> str | None:
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


def validate_revision_is_substantive(
    patch: Any,
    *,
    record: dict[str, Any],
    record_id: str,
) -> str | None:
    """Reject cosmetic rewrites of already-compact decision records."""
    payload = prediction_payload(patch)
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


def validate_revision_patch(
    patch: Any,
    *,
    record: dict[str, Any],
    record_id: str,
) -> str | None:
    """Return feedback for an incomplete or unsafe revise patch."""
    payload = prediction_payload(patch)
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


def plan_payload(value: Any) -> dict[str, Any]:
    """Return the plain action-plan payload from a prediction or dict."""
    return prediction_payload(value, output_field="plan")


def clean_action_type(value: Any) -> str:
    """Normalize generated enum/string action types."""
    enum_value = getattr(value, "value", None)
    text = str(enum_value if enum_value is not None else value or "").strip().lower()
    return text or "noop"


def semantic_retry_observation(validation_error: str) -> str:
    """Render compact feedback for a schema-valid but unsafe action plan."""
    return (
        "The previous model step produced structured output, but the proposed "
        "context-curation action plan was unsafe or incomplete. Retry with a complete safe "
        f"plan. Validation error: {validation_error}"
    )


def observation(
    action: str,
    ok: bool,
    content: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Build one context-curator event payload."""
    return {
        "action": action,
        "ok": ok,
        "content": content,
        "args": args,
        "done": False,
        "completion_summary": "",
    }
