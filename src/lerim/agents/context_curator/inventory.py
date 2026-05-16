"""Record inventory and semantic grouping helpers for context curation."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lerim.context import ContextStore, ProjectIdentity

CONTEXT_CURATOR_RECORD_LIMIT = 200
CONTEXT_CURATOR_NEIGHBOR_LIMIT = 4
CONTEXT_CURATOR_MAX_CLUSTER_SIZE = 8
CONTEXT_CURATOR_HEALTH_BATCH_SIZE = 16
CONTEXT_CURATOR_TEXT_LIMIT = 1800
CONTEXT_CURATOR_PREVIEW_LIMIT = 520
CONTEXT_CURATOR_RECORD_FIELDS = (
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


def prepare_context_curator_store(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity,
    session_id: str,
    model_name: str,
) -> None:
    """Initialize store provenance for one context-curator run."""
    store = ContextStore(context_db_path)
    store.initialize()
    store.register_project(project_identity)
    store.upsert_session(
        project_id=project_identity.project_id,
        session_id=session_id,
        agent_type="context_curator",
        source_trace_ref=f"context_curator:{project_identity.project_id}",
        repo_path=str(project_identity.repo_path),
        cwd=str(project_identity.repo_path),
        started_at=datetime.now(timezone.utc).isoformat(),
        model_name=model_name,
        instructions_text=None,
        prompt_text=None,
        metadata={},
    )


def load_active_records(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity,
    limit: int = CONTEXT_CURATOR_RECORD_LIMIT,
) -> list[dict[str, Any]]:
    """Load a bounded active-record inventory for context curation."""
    store = ContextStore(context_db_path)
    store.initialize()
    store.register_project(project_identity)
    listing = store.query(
        entity="records",
        mode="list",
        project_ids=[project_identity.project_id],
        status="active",
        order_by="updated_at",
        limit=max(1, int(limit)),
        include_total=False,
    )
    return [_compact_record(row, preview=False) for row in listing.get("rows", [])]


def build_similarity_clusters(
    *,
    context_db_path: Path,
    project_identity: ProjectIdentity,
    records: list[dict[str, Any]],
    neighbor_limit: int = CONTEXT_CURATOR_NEIGHBOR_LIMIT,
    max_cluster_size: int = CONTEXT_CURATOR_MAX_CLUSTER_SIZE,
) -> list[dict[str, Any]]:
    """Build bounded semantic-neighbor clusters from active records."""
    if len(records) < 2:
        return []
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
            if not _cluster_compatible(records_by_id[record_id], records_by_id[neighbor_id]):
                continue
            directed_neighbors[record_id].add(neighbor_id)

    edges: dict[str, set[str]] = defaultdict(set)
    for record_id, neighbor_ids in directed_neighbors.items():
        for neighbor_id in neighbor_ids:
            if record_id not in directed_neighbors.get(neighbor_id, set()):
                continue
            edges[record_id].add(neighbor_id)
            edges[neighbor_id].add(record_id)

    components = _connected_components(edges)
    clusters: list[dict[str, Any]] = []
    for component_index, component_ids in enumerate(components, start=1):
        ordered_ids = sorted(
            component_ids,
            key=lambda item: str(records_by_id.get(item, {}).get("updated_at") or ""),
            reverse=True,
        )
        for chunk_index, chunk in enumerate(_chunks(ordered_ids, max_cluster_size), start=1):
            if len(chunk) < 2:
                continue
            clusters.append(
                {
                    "cluster_id": f"cluster_{component_index}_{chunk_index}",
                    "record_ids": chunk,
                    "records": [
                        _compact_record(records_by_id[record_id], preview=False)
                        for record_id in chunk
                        if record_id in records_by_id
                    ],
                }
            )
    return clusters


def build_health_batches(
    *,
    records: list[dict[str, Any]],
    excluded_record_ids: set[str],
    batch_size: int = CONTEXT_CURATOR_HEALTH_BATCH_SIZE,
) -> list[list[dict[str, Any]]]:
    """Build singleton health-review batches for records without prior actions."""
    candidates = [
        _compact_record(record, preview=False)
        for record in records
        if str(record.get("record_id") or "") not in excluded_record_ids
    ]
    return list(_chunks(candidates, max(1, int(batch_size))))


def format_records_json(records: list[dict[str, Any]]) -> str:
    """Render records as compact JSON for BAML prompts."""
    return json.dumps(
        [_compact_record(record, preview=False) for record in records],
        ensure_ascii=True,
        indent=2,
    )


def record_search_query(record: dict[str, Any]) -> str:
    """Build semantic-search text for one record from canonical fields."""
    parts = [
        str(record.get(field) or "").strip()
        for field in (
            "title",
            "body",
            "decision",
            "why",
            "user_intent",
            "what_happened",
            "outcomes",
        )
        if str(record.get(field) or "").strip()
    ]
    return "\n".join(parts)[:CONTEXT_CURATOR_TEXT_LIMIT]


def _compact_record(record: dict[str, Any], *, preview: bool) -> dict[str, Any]:
    """Return one record with stable fields and bounded text."""
    compact: dict[str, Any] = {}
    for field in CONTEXT_CURATOR_RECORD_FIELDS:
        value = record.get(field)
        if value is None:
            continue
        if isinstance(value, str):
            text_limit = CONTEXT_CURATOR_PREVIEW_LIMIT if preview and field == "body" else CONTEXT_CURATOR_TEXT_LIMIT
            compact[field] = value[:text_limit]
        else:
            compact[field] = value
    return compact


def _record_by_id(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return records keyed by record_id."""
    return {str(record.get("record_id") or ""): record for record in records}


def _cluster_compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Return whether two records should enter the same duplicate-review cluster."""
    return str(left.get("kind") or "") == str(right.get("kind") or "")


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
        if len(component) >= 2:
            components.append(component)
    return components


def _chunks(items: list[Any], size: int) -> list[list[Any]]:
    """Split items into fixed-size chunks."""
    return [items[index : index + size] for index in range(0, len(items), size)]
