"""Record inventory and semantic candidate helpers for context graph linking."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lerim.context import ContextStore, ProjectIdentity

CONTEXT_GRAPH_RECORD_LIMIT = 260
CONTEXT_GRAPH_NEIGHBOR_LIMIT = 5
CONTEXT_GRAPH_MAX_CLUSTER_SIZE = 12
CONTEXT_GRAPH_TEXT_LIMIT = 1800
CONTEXT_GRAPH_RECORD_FIELDS = (
    "record_id",
    "kind",
    "title",
    "body",
    "status",
    "created_at",
    "updated_at",
    "valid_from",
    "valid_until",
    "superseded_by_record_id",
    "decision",
    "why",
    "alternatives",
    "consequences",
    "user_intent",
    "what_happened",
    "outcomes",
)


def prepare_context_graph_store(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity,
    session_id: str,
    model_name: str,
) -> None:
    """Initialize store provenance for one context-graph run."""
    store = ContextStore(context_db_path)
    store.initialize()
    store.register_project(project_identity)
    store.upsert_session(
        project_id=project_identity.project_id,
        session_id=session_id,
        agent_type="context_graph",
        source_trace_ref=f"context_graph:{project_identity.project_id}",
        repo_path=str(project_identity.repo_path),
        cwd=str(project_identity.repo_path),
        started_at=datetime.now(timezone.utc).isoformat(),
        model_name=model_name,
        instructions_text=None,
        prompt_text=None,
        metadata={},
    )


def load_graph_records(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity,
    limit: int = CONTEXT_GRAPH_RECORD_LIMIT,
) -> list[dict[str, Any]]:
    """Load active durable records that can become graph nodes."""
    store = ContextStore(context_db_path)
    store.initialize()
    store.register_project(project_identity)
    durable_records: list[dict[str, Any]] = []
    page_size = max(1, int(limit))
    offset = 0
    while len(durable_records) < page_size:
        listing = store.query(
            entity="records",
            mode="list",
            project_ids=[project_identity.project_id],
            status="active",
            order_by="updated_at",
            limit=page_size,
            offset=offset,
            include_total=False,
        )
        rows = list(listing.get("rows", []))
        if not rows:
            break
        for row in rows:
            if str(row.get("kind") or "").strip().lower() == "episode":
                continue
            durable_records.append(_compact_record(row))
            if len(durable_records) >= page_size:
                break
        offset += len(rows)
    return durable_records


def build_semantic_candidates(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity,
    records: list[dict[str, Any]],
    neighbor_limit: int = CONTEXT_GRAPH_NEIGHBOR_LIMIT,
    max_cluster_size: int = CONTEXT_GRAPH_MAX_CLUSTER_SIZE,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build semantic-neighbor clusters and candidate link pairs."""
    if len(records) < 2:
        return [], []
    store = ContextStore(context_db_path)
    store.initialize()
    store.register_project(project_identity)
    known_ids = {str(record.get("record_id") or "") for record in records}
    records_by_id = _record_by_id(records)
    directed_neighbors: dict[str, set[str]] = defaultdict(set)
    for record in records:
        record_id = str(record.get("record_id") or "")
        query = record_search_query(record)
        if not record_id or not query:
            continue
        hits = store.search(
            project_ids=[project_identity.project_id],
            query=query,
            statuses=["active"],
            include_archived=False,
            limit=max(2, int(neighbor_limit) + 1),
        )
        for hit in hits:
            neighbor_id = str(hit.record_id)
            if neighbor_id == record_id or neighbor_id not in known_ids:
                continue
            directed_neighbors[record_id].add(neighbor_id)

    edges: dict[str, set[str]] = defaultdict(set)
    for record_id, neighbor_ids in directed_neighbors.items():
        for neighbor_id in neighbor_ids:
            if record_id not in directed_neighbors.get(neighbor_id, set()):
                continue
            edges[record_id].add(neighbor_id)
            edges[neighbor_id].add(record_id)

    clusters: list[dict[str, Any]] = []
    all_pairs: list[dict[str, Any]] = []
    components = _connected_components(edges)
    for component_index, component_ids in enumerate(components, start=1):
        ordered_ids = sorted(
            component_ids,
            key=lambda item: str(records_by_id.get(item, {}).get("updated_at") or ""),
            reverse=True,
        )
        for chunk_index, chunk in enumerate(_chunks(ordered_ids, max_cluster_size), start=1):
            if len(chunk) < 2:
                continue
            cluster_pairs = _candidate_pairs_for_cluster(chunk, edges, records_by_id)
            cluster = {
                "cluster_id": f"semantic_{component_index}_{chunk_index}",
                "record_ids": chunk,
                "records": [
                    _compact_record(records_by_id[record_id])
                    for record_id in chunk
                    if record_id in records_by_id
                ],
                "candidate_pairs": cluster_pairs,
            }
            clusters.append(cluster)
            all_pairs.extend(cluster_pairs)
    return clusters, all_pairs


def load_existing_edges(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity,
) -> list[dict[str, Any]]:
    """Load existing active context graph edges for duplicate avoidance."""
    store = ContextStore(context_db_path)
    store.initialize()
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT edge_id, source_node_id, target_node_id, relation_kind, label,
                   evidence_record_ids, confidence, status, updated_at
            FROM context_edges
            WHERE project_id = ? AND status = 'active'
            ORDER BY updated_at DESC, edge_id ASC
            LIMIT 500
            """,
            (project_identity.project_id,),
        ).fetchall()
    return [
        {
            "edge_id": str(row["edge_id"]),
            "source_record_id": str(row["source_node_id"]),
            "target_record_id": str(row["target_node_id"]),
            "relation_kind": str(row["relation_kind"]),
            "label": str(row["label"] or ""),
            "evidence_record_ids": _parse_json_list(row["evidence_record_ids"]),
            "confidence": float(row["confidence"] or 0.0),
            "status": str(row["status"]),
        }
        for row in rows
    ]


def format_records_json(records: list[dict[str, Any]]) -> str:
    """Render records as compact JSON for BAML prompts."""
    return json.dumps([_compact_record(record) for record in records], ensure_ascii=True, indent=2)


def format_pairs_json(pairs: list[dict[str, Any]]) -> str:
    """Render candidate pairs as compact JSON for BAML prompts."""
    return json.dumps(pairs, ensure_ascii=True, indent=2)


def format_edges_json(edges: list[dict[str, Any]]) -> str:
    """Render existing or proposed graph edges as compact JSON."""
    return json.dumps(edges, ensure_ascii=True, indent=2)


def record_search_query(record: dict[str, Any]) -> str:
    """Build semantic-search text for one context record."""
    parts = [
        str(record.get(field) or "").strip()
        for field in (
            "title",
            "body",
            "decision",
            "why",
            "alternatives",
            "consequences",
            "user_intent",
            "what_happened",
            "outcomes",
        )
        if str(record.get(field) or "").strip()
    ]
    return "\n".join(parts)[:CONTEXT_GRAPH_TEXT_LIMIT]


def _candidate_pairs_for_cluster(
    record_ids: list[str],
    edges: dict[str, set[str]],
    records_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return deterministic candidate pair payloads for one semantic cluster."""
    pairs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for source_id in record_ids:
        for target_id in sorted(edges.get(source_id, set())):
            if target_id not in record_ids:
                continue
            pair_key = tuple(sorted((source_id, target_id)))
            if pair_key in seen:
                continue
            seen.add(pair_key)
            source = records_by_id.get(source_id, {})
            target = records_by_id.get(target_id, {})
            pairs.append(
                {
                    "source_record_id": source_id,
                    "source_kind": source.get("kind"),
                    "source_title": source.get("title"),
                    "target_record_id": target_id,
                    "target_kind": target.get("kind"),
                    "target_title": target.get("title"),
                }
            )
    return pairs


def _compact_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return one record with stable fields and bounded text."""
    compact: dict[str, Any] = {}
    for field in CONTEXT_GRAPH_RECORD_FIELDS:
        value = record.get(field)
        if value is None:
            continue
        compact[field] = value[:CONTEXT_GRAPH_TEXT_LIMIT] if isinstance(value, str) else value
    return compact


def _record_by_id(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return records keyed by record_id."""
    return {str(record.get("record_id") or ""): record for record in records}


def _connected_components(edges: dict[str, set[str]]) -> list[list[str]]:
    """Return connected components from an undirected graph."""
    seen: set[str] = set()
    components: list[list[str]] = []
    for start in sorted(edges):
        if start in seen:
            continue
        queue: deque[str] = deque([start])
        component: list[str] = []
        seen.add(start)
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbor in sorted(edges.get(current, set())):
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                queue.append(neighbor)
        if len(component) > 1:
            components.append(component)
    return components


def _chunks(values: list[str], size: int) -> list[list[str]]:
    """Split values into bounded non-empty chunks."""
    safe_size = max(1, int(size))
    return [values[index : index + safe_size] for index in range(0, len(values), safe_size)]


def _parse_json_list(raw: Any) -> list[str]:
    """Parse a JSON list into strings."""
    try:
        parsed = json.loads(str(raw or "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]
