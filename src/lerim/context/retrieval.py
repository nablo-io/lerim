"""Hybrid context retrieval helpers for the SQLite context store."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

import sqlite_vec

from lerim.config.settings import get_config

RRF_K = 60


@dataclass(frozen=True)
class SearchHit:
    """Compact retrieval hit returned by hybrid search."""

    record_id: str
    project_id: str
    kind: str
    title: str
    body: str
    status: str
    created_at: str
    updated_at: str
    valid_from: str
    valid_until: str | None
    score: float
    sources: list[str]
    decision: str | None = None
    why: str | None = None
    alternatives: str | None = None
    consequences: str | None = None
    user_intent: str | None = None
    what_happened: str | None = None
    outcomes: str | None = None


def search_records(
    store: Any,
    *,
    project_ids: list[str] | None,
    query: str,
    kind_filters: list[str] | None = None,
    statuses: list[str] | None = None,
    valid_at: str | None = None,
    include_archived: bool = False,
    limit: int = 8,
) -> list[SearchHit]:
    """Run hybrid retrieval over records for one context store."""
    config = get_config()
    with store.connect() as conn:
        fts_available = True
        try:
            store._prepare_search_fts(conn)
        except sqlite3.OperationalError:
            conn.rollback()
            fts_available = False
        store._prepare_search_embeddings(conn)
        conn.commit()
        conn.execute("BEGIN")
        semantic_rows = semantic_candidates(
            store,
            project_ids=project_ids,
            query=query,
            kind_filters=kind_filters,
            statuses=statuses,
            valid_at=valid_at,
            include_archived=include_archived,
            limit=max(limit * 3, config.semantic_shortlist_size),
            conn=conn,
        )
        lexical_rows: list[tuple[str, float]] = []
        if fts_available:
            try:
                lexical_rows = lexical_candidates(
                    store,
                    project_ids=project_ids,
                    query=query,
                    kind_filters=kind_filters,
                    statuses=statuses,
                    valid_at=valid_at,
                    include_archived=include_archived,
                    limit=max(limit * 3, config.lexical_shortlist_size),
                    conn=conn,
                )
            except sqlite3.OperationalError:
                lexical_rows = []
        combined = rrf_fuse(semantic_rows=semantic_rows, lexical_rows=lexical_rows)
        if not combined:
            return []
        top_ids = [record_id for record_id, _score, _sources in combined[:limit]]
        placeholders = ", ".join("?" for _ in top_ids)
        rows = conn.execute(
            f"SELECT * FROM records WHERE record_id IN ({placeholders})",
            tuple(top_ids),
        ).fetchall()
    row_map = {str(row["record_id"]): row for row in rows}
    hits: list[SearchHit] = []
    for record_id, score, sources in combined:
        row = row_map.get(record_id)
        if row is None:
            continue
        hits.append(
            SearchHit(
                record_id=record_id,
                project_id=str(row["project_id"]),
                kind=str(row["kind"]),
                title=str(row["title"]),
                body=str(row["body"]),
                decision=row["decision"],
                why=row["why"],
                alternatives=row["alternatives"],
                consequences=row["consequences"],
                user_intent=row["user_intent"],
                what_happened=row["what_happened"],
                outcomes=row["outcomes"],
                status=str(row["status"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
                valid_from=str(row["valid_from"]),
                valid_until=row["valid_until"],
                score=score,
                sources=sources,
            )
        )
        if len(hits) >= limit:
            break
    return hits


def semantic_candidates(
    store: Any,
    *,
    project_ids: list[str] | None,
    query: str,
    kind_filters: list[str] | None,
    statuses: list[str] | None,
    valid_at: str | None,
    include_archived: bool,
    limit: int,
    conn: sqlite3.Connection | None = None,
) -> list[tuple[str, float]]:
    """Return ranked semantic candidates from sqlite-vec nearest neighbors."""
    provider = store.embedding_provider()
    query_vec = sqlite_vec.serialize_float32(provider.embed_query(query))
    filter_sql, params = store._build_record_filter_sql(
        project_ids=project_ids,
        kind_filters=kind_filters,
        statuses=statuses,
        source_session_id=None,
        created_since=None,
        created_until=None,
        updated_since=None,
        updated_until=None,
        valid_at=valid_at,
        include_archived=include_archived,
        table_alias="records",
    )
    vector_filter_sql = ""
    vector_filter_params: list[Any] = []
    if project_ids and len(project_ids) == 1:
        vector_filter_sql = " AND record_embeddings.project_id = ?"
        vector_filter_params.append(project_ids[0])

    def read_candidates(active_conn: sqlite3.Connection) -> list[sqlite3.Row]:
        """Read filtered semantic candidates from one active connection."""
        max_candidates = int(
            active_conn.execute(
                f"SELECT COUNT(*) FROM record_embeddings WHERE 1=1{vector_filter_sql}",
                tuple(vector_filter_params),
            ).fetchone()[0]
        )
        candidate_limit = min(max_candidates, max(int(limit), 25))
        if project_ids or kind_filters or statuses or valid_at or include_archived:
            candidate_limit = min(max_candidates, max(candidate_limit, int(limit) * 4))
        rows: list[sqlite3.Row] = []
        while candidate_limit > 0:
            rows = active_conn.execute(
                f"""
                SELECT records.record_id, record_embeddings.distance
                FROM record_embeddings
                JOIN records ON records.record_id = record_embeddings.record_id
                WHERE record_embeddings.embedding MATCH ?
                  AND record_embeddings.k = ?
                  {vector_filter_sql}
                  AND {filter_sql}
                ORDER BY record_embeddings.distance ASC
                LIMIT ?
                """,
                tuple([query_vec, candidate_limit] + vector_filter_params + params + [candidate_limit]),
            ).fetchall()
            if len(rows) >= limit or candidate_limit >= max_candidates:
                break
            candidate_limit = min(max_candidates, max(candidate_limit * 2, candidate_limit + 1))
        return rows

    if conn is None:
        with store.connect() as active_conn:
            store._prepare_search_embeddings(active_conn)
            rows = read_candidates(active_conn)
    else:
        rows = read_candidates(conn)
    return [(str(row["record_id"]), float(row["distance"])) for row in rows]


def lexical_candidates(
    store: Any,
    *,
    project_ids: list[str] | None,
    query: str,
    kind_filters: list[str] | None,
    statuses: list[str] | None,
    valid_at: str | None,
    include_archived: bool,
    limit: int,
    conn: sqlite3.Connection | None = None,
) -> list[tuple[str, float]]:
    """Return ranked lexical candidates from SQLite FTS."""
    compiled_query = compile_safe_fts_query(query)
    if not compiled_query:
        return []
    filter_sql, params = store._build_record_filter_sql(
        project_ids=project_ids,
        kind_filters=kind_filters,
        statuses=statuses,
        source_session_id=None,
        created_since=None,
        created_until=None,
        updated_since=None,
        updated_until=None,
        valid_at=valid_at,
        include_archived=include_archived,
        table_alias="records",
    )

    def read_candidates(active_conn: sqlite3.Connection) -> list[sqlite3.Row]:
        """Read filtered lexical candidates from one active connection."""
        return active_conn.execute(
            f"""
            SELECT records.record_id, bm25(records_fts) AS rank_score
            FROM records_fts
            JOIN records ON records.record_id = records_fts.record_id
            WHERE records_fts MATCH ? AND {filter_sql}
            ORDER BY rank_score ASC
            LIMIT ?
            """,
            tuple([compiled_query] + params + [limit]),
        ).fetchall()

    if conn is None:
        with store.connect() as active_conn:
            store._prepare_search_fts(active_conn)
            rows = read_candidates(active_conn)
    else:
        rows = read_candidates(conn)
    return [(str(row["record_id"]), float(row["rank_score"])) for row in rows]


def rrf_fuse(
    *,
    semantic_rows: list[tuple[str, float]],
    lexical_rows: list[tuple[str, float]],
) -> list[tuple[str, float, list[str]]]:
    """Fuse ranked lists with Reciprocal Rank Fusion."""
    scores: dict[str, float] = {}
    sources: dict[str, set[str]] = {}
    for rank, (record_id, _score) in enumerate(semantic_rows, start=1):
        scores[record_id] = scores.get(record_id, 0.0) + 1.0 / (RRF_K + rank)
        sources.setdefault(record_id, set()).add("semantic")
    for rank, (record_id, _score) in enumerate(lexical_rows, start=1):
        scores[record_id] = scores.get(record_id, 0.0) + 1.0 / (RRF_K + rank)
        sources.setdefault(record_id, set()).add("fts")
    combined = [
        (record_id, score, sorted(sources.get(record_id, set())))
        for record_id, score in scores.items()
    ]
    combined.sort(key=lambda item: item[1], reverse=True)
    return combined


def compile_safe_fts_query(raw: str) -> str | None:
    """Compile free-form text into a conservative SQLite FTS query."""
    if not raw or not raw.strip():
        return None

    normalized_chars: list[str] = []
    for char in raw:
        normalized_chars.append(char if char.isalnum() else " ")
    normalized = "".join(normalized_chars)

    terms: list[str] = []
    seen: set[str] = set()
    for token in normalized.split():
        term = token.strip()
        if not term:
            continue
        lowered = term.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        terms.append(term)
        if len(terms) >= 8:
            break

    if not terms:
        return None
    return " OR ".join(f'"{term}"' for term in terms)
