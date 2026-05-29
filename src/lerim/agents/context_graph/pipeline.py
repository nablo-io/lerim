"""DSPy context graph pipeline over curated context records."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from lerim.agents.dspy_compat import dspy

from lerim.agents.context_graph.inventory import (
    build_semantic_candidates,
    format_edges_json,
    format_pairs_json,
    format_records_json,
    load_existing_edges,
    load_graph_records,
)
from lerim.agents.context_graph.persistence import replace_context_graph
from lerim.agents.context_graph.signatures import LinkContextRecords, ReviewContextGraphLinks
from lerim.agents.model_helpers import call_model_step, prediction_payload
from lerim.agents.model_runtime import ModelRuntime, build_model_runtime
from lerim.config.settings import Config
from lerim.context import ProjectIdentity

SUPPORTED_RELATION_KINDS = {
    "supports",
    "refines",
    "depends_on",
    "contradicts",
    "same_topic",
    "evidence_for",
    "supersedes",
    "related",
}


class ContextGraphPipeline(dspy.Module):
    """Build semantic candidates, model-review links, and persist the graph."""

    def __init__(
        self,
        *,
        context_db_path: Any,
        project_identity: ProjectIdentity,
        session_id: str,
        config: Config,
        provider: str | None = None,
        model_name: str | None = None,
        api_base_url: str | None = None,
        api_key: str | None = None,
        temperature: float | None = None,
        max_model_steps: int = 20,
        progress: bool = False,
        link_step: Any | None = None,
        review_step: Any | None = None,
    ) -> None:
        """Create the context graph pipeline."""
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
        self.adapter = dspy.JSONAdapter()
        self.uses_real_model = link_step is None or review_step is None
        self.link_step = link_step or dspy.Predict(LinkContextRecords)
        self.review_step = review_step or dspy.Predict(ReviewContextGraphLinks)
        self.runtime: ModelRuntime | None = None

    def forward(self) -> dict[str, Any]:
        """Run inventory, link proposal, review, and persistence."""
        records = load_graph_records(
            context_db_path=self.context_db_path,
            project_identity=self.project_identity,
        )
        existing_edges = load_existing_edges(
            context_db_path=self.context_db_path,
            project_identity=self.project_identity,
        )
        semantic_clusters, candidate_pairs = build_semantic_candidates(
            context_db_path=self.context_db_path,
            project_identity=self.project_identity,
            records=records,
        )
        observations = [
            observation(
                "load_inventory",
                True,
                f"active_records={len(records)} existing_edges={len(existing_edges)}",
                {
                    "record_count": len(records),
                    "existing_edge_count": len(existing_edges),
                },
            ),
            observation(
                "build_semantic_candidates",
                True,
                f"semantic_clusters={len(semantic_clusters)} candidate_pairs={len(candidate_pairs)}",
                {
                    "semantic_cluster_count": len(semantic_clusters),
                    "candidate_pair_count": len(candidate_pairs),
                },
            ),
        ]
        link_result = self.link_records(
            records=records,
            semantic_clusters=semantic_clusters,
            candidate_pairs=candidate_pairs,
            existing_edges=existing_edges,
        )
        observations.extend(link_result["observations"])
        review_result = self.review_links(
            records=records,
            records_by_id={str(record["record_id"]): record for record in records},
            candidate_pairs=candidate_pairs,
            proposed_links=link_result["proposed_links"],
            model_steps=link_result["model_steps"],
        )
        observations.extend(review_result["observations"])
        summary = replace_context_graph(
            context_db_path=self.context_db_path,
            project_identity=self.project_identity,
            session_id=self.session_id,
            records=records,
            semantic_clusters=semantic_clusters,
            candidate_pairs=candidate_pairs,
            links=review_result["reviewed_links"],
        )
        completion_summary = (
            f"Context graph refreshed with {summary.nodes_written} node(s), "
            f"{summary.edges_written} edge(s), and {summary.semantic_clusters} semantic cluster(s)."
        )
        final = observation("final_result", True, completion_summary, summary.as_dict())
        final["done"] = True
        final["completion_summary"] = completion_summary
        return {
            "observations": [*observations, *summary.observations, final],
            "model_steps": review_result["model_steps"],
            "llm_calls": review_result["model_steps"],
            "records": records,
            "semantic_clusters": semantic_clusters,
            "candidate_pairs": candidate_pairs,
            "proposed_links": link_result["proposed_links"],
            "reviewed_links": review_result["reviewed_links"],
            "write_summary": summary.as_dict(),
            "done": True,
            "completion_summary": completion_summary,
        }

    def model_context(self):
        """Return a DSPy context manager with lazily constructed model runtime."""
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

    def link_records(
        self,
        *,
        records: list[dict[str, Any]],
        semantic_clusters: list[dict[str, Any]],
        candidate_pairs: list[dict[str, Any]],
        existing_edges: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Ask the model to propose links for each semantic cluster."""
        del candidate_pairs
        model_steps = 0
        observations: list[dict[str, Any]] = []
        links: list[dict[str, Any]] = []
        records_by_id = {str(record["record_id"]): record for record in records}
        instruction_text = run_instruction()
        for cluster in semantic_clusters:
            pairs = cluster.get("candidate_pairs") or []
            if not pairs:
                continue
            if model_steps >= self.max_model_steps:
                raise RuntimeError(
                    f"context graph exceeded max_model_steps={self.max_model_steps}"
                )
            allowed_pairs = allowed_pair_set(pairs)
            with self.model_context():
                result, retry_observations, attempts = call_model_step(
                    lambda instruction, cluster=cluster, pairs=pairs: self.link_step(
                        run_instruction=instruction,
                        cluster_id=str(cluster.get("cluster_id") or ""),
                        records_json=format_records_json(cluster.get("records") or []),
                        candidate_pairs_json=format_pairs_json(pairs),
                        existing_edges_json=format_edges_json(existing_edges),
                    ),
                    stage="link_records",
                    progress=self.progress,
                    progress_label="context-graph",
                    run_instruction=instruction_text,
                    validate_result=lambda result, records_by_id=records_by_id: validate_links_for_records(
                        result,
                        records_by_id=records_by_id,
                        allowed_pairs=allowed_pairs,
                    ),
                    make_observation=observation,
                    semantic_retry_content=semantic_retry_observation,
                    validation_retry_target="complete corrected link plan",
                )
            model_steps += attempts
            cluster_links = extract_links(result)
            links.extend(cluster_links)
            observations.extend(retry_observations)
            observations.append(
                observation(
                    "link_records",
                    True,
                    f"cluster={cluster.get('cluster_id')} proposed_links={len(cluster_links)}",
                    {
                        "cluster_id": cluster.get("cluster_id"),
                        "proposed_link_count": len(cluster_links),
                    },
                )
            )
        if not links:
            observations.append(
                observation(
                    "link_records",
                    True,
                    "No graph links proposed.",
                    {"proposed_link_count": 0},
                )
            )
        return {"model_steps": model_steps, "proposed_links": links, "observations": observations}

    def review_links(
        self,
        *,
        records: list[dict[str, Any]],
        records_by_id: dict[str, dict[str, Any]],
        candidate_pairs: list[dict[str, Any]],
        proposed_links: list[dict[str, Any]],
        model_steps: int,
    ) -> dict[str, Any]:
        """Review proposed graph links before persistence."""
        deduped_links = dedupe_links(proposed_links)
        if not deduped_links:
            return {
                "model_steps": model_steps,
                "reviewed_links": [],
                "observations": [
                    observation(
                        "review_links",
                        True,
                        "No graph links to review.",
                        {"reviewed_link_count": 0},
                    )
                ],
            }
        if model_steps >= self.max_model_steps:
            raise RuntimeError(f"context graph exceeded max_model_steps={self.max_model_steps}")
        allowed_pairs = allowed_pair_set(candidate_pairs)
        with self.model_context():
            result, retry_observations, attempts = call_model_step(
                lambda instruction: self.review_step(
                    run_instruction=instruction,
                    records_json=format_records_json(records),
                    proposed_links_json=format_edges_json(deduped_links),
                ),
                stage="review_links",
                progress=self.progress,
                progress_label="context-graph",
                run_instruction=run_instruction(),
                validate_result=lambda result: validate_links_for_records(
                    result,
                    records_by_id=records_by_id,
                    allowed_pairs=allowed_pairs,
                ),
                make_observation=observation,
                semantic_retry_content=semantic_retry_observation,
                validation_retry_target="complete corrected link plan",
            )
        reviewed_links = dedupe_links(extract_links(result))
        return {
            "model_steps": model_steps + attempts,
            "reviewed_links": reviewed_links,
            "observations": [
                *retry_observations,
                observation(
                    "review_links",
                    True,
                    f"reviewed_links={len(reviewed_links)}",
                    {"reviewed_link_count": len(reviewed_links)},
                ),
            ],
        }


def run_instruction() -> str:
    """Return context-graph task framing for model steps."""
    return (
        "Build a sparse, evidence-backed context graph from curated records. "
        "Prefer a few durable relationships that help future agents navigate context. "
        "Do not link merely because two records are broadly adjacent."
    )


def validate_links_for_records(
    result: Any,
    *,
    records_by_id: dict[str, dict[str, Any]],
    allowed_pairs: set[tuple[str, str]] | None = None,
) -> str | None:
    """Return semantic validation feedback for generated graph links."""
    seen: set[tuple[str, str, str]] = set()
    record_ids = set(records_by_id)
    for link in extract_links(result):
        source_id = str(link.get("source_record_id") or "").strip()
        target_id = str(link.get("target_record_id") or "").strip()
        relation_kind = clean_relation_kind(link.get("relation_kind"))
        if not source_id or not target_id:
            return "graph links must include source_record_id and target_record_id"
        if source_id == target_id:
            return f"graph link {source_id} cannot target itself"
        if source_id not in records_by_id:
            return f"source_record_id {source_id} was not in reviewed records"
        if target_id not in records_by_id:
            return f"target_record_id {target_id} was not in reviewed records"
        if allowed_pairs is not None and tuple(sorted((source_id, target_id))) not in allowed_pairs:
            return f"graph link {source_id}->{target_id} was not in candidate_pairs_json"
        if relation_kind not in SUPPORTED_RELATION_KINDS:
            return f"unsupported relation_kind {relation_kind or '<empty>'}"
        confidence = float(link.get("confidence") or 0.0)
        if confidence < 0.0 or confidence > 1.0:
            return f"confidence for {source_id}->{target_id} must be between 0 and 1"
        if confidence < 0.55:
            return f"confidence for {source_id}->{target_id} is too low to persist"
        evidence_ids = {
            str(record_id or "").strip()
            for record_id in (link.get("evidence_record_ids") or [])
            if str(record_id or "").strip()
        }
        if not evidence_ids:
            return f"graph link {source_id}->{target_id} must include evidence_record_ids"
        if missing := sorted(evidence_ids - record_ids):
            return f"evidence_record_ids were not reviewed records: {', '.join(missing)}"
        key = (source_id, target_id, relation_kind)
        if key in seen:
            return f"duplicate graph link {source_id}->{target_id}:{relation_kind}"
        seen.add(key)
    return None


def allowed_pair_set(pairs: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """Return unordered candidate-pair ids accepted for one model step."""
    allowed: set[tuple[str, str]] = set()
    for pair in pairs:
        source_id = str(pair.get("source_record_id") or "").strip()
        target_id = str(pair.get("target_record_id") or "").strip()
        if source_id and target_id and source_id != target_id:
            allowed.add(tuple(sorted((source_id, target_id))))
    return allowed


def extract_links(result: Any) -> list[dict[str, Any]]:
    """Extract normalized link dictionaries from model output."""
    links: list[dict[str, Any]] = []
    for raw_link in prediction_payload(result, output_field="plan").get("links") or []:
        link = prediction_payload(raw_link)
        relation_kind = clean_relation_kind(link.get("relation_kind"))
        links.append(
            {
                "source_record_id": str(link.get("source_record_id") or "").strip(),
                "target_record_id": str(link.get("target_record_id") or "").strip(),
                "relation_kind": relation_kind,
                "label": str(link.get("label") or relation_kind).strip(),
                "rationale": str(link.get("rationale") or "").strip(),
                "evidence_record_ids": [
                    str(record_id).strip()
                    for record_id in (link.get("evidence_record_ids") or [])
                    if str(record_id).strip()
                ],
                "confidence": float(link.get("confidence") or 0.0),
            }
        )
    return links


def dedupe_links(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one highest-confidence link for each directed relation key."""
    best: dict[tuple[str, str, str], dict[str, Any]] = {}
    for link in links:
        key = (
            str(link.get("source_record_id") or ""),
            str(link.get("target_record_id") or ""),
            str(link.get("relation_kind") or ""),
        )
        if not all(key):
            continue
        current = best.get(key)
        if current is None or float(link.get("confidence") or 0.0) > float(current.get("confidence") or 0.0):
            best[key] = link
    return list(best.values())


def clean_relation_kind(value: Any) -> str:
    """Normalize generated enum/string relation kinds."""
    enum_value = getattr(value, "value", None)
    raw = enum_value if enum_value is not None else value
    return str(raw or "").strip().lower() or "related"


def semantic_retry_observation(validation_error: str) -> str:
    """Return compact retry text for semantic validation errors."""
    return f"context_graph_validation_failed: {validation_error}"


def observation(
    action: str,
    ok: bool,
    content: str,
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one graph event payload."""
    return {"action": action, "ok": ok, "content": content, "args": args or {}}
