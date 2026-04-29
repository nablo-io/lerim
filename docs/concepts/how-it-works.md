# How It Works

Lerim is a DB-only context system.

## Summary

The flow is:

1. adapters read raw agent sessions
2. traces are normalized
3. `sync` extracts durable context records
4. `maintain` cleans and supersedes records
5. `ask` retrieves records and answers questions

For operational targets and scale boundaries, see
[Capacity and SLOs](capacity-and-slos.md).

## Storage

Canonical storage is global:

- `~/.lerim/context.sqlite3` — projects, sessions, records, versions, embeddings, and FTS
- `~/.lerim/index/sessions.sqlite3` — session catalog and queue
- `~/.lerim/workspace/` — run artifacts and logs
- `~/.lerim/cache/traces/` — compacted agent trace cache
- `~/.lerim/models/embeddings/` — local ONNX embedding model cache
- `~/.lerim/models/huggingface/` — Hugging Face library cache

Projects are scoped by `project_id` inside the database.

## Agent tool surface

Lerim does not expose raw SQL or file CRUD to the agent.

The durable context tools are:

- `read_trace`
- `list_context`
- `search_context`
- `get_context`
- `save_context`
- `revise_context`
- `archive_context`
- `supersede_context`
- `count_context`

The extract flow also uses:

- `note_trace_findings`
- `prune_trace_reads`

Retrieval is hybrid:

- local ONNX embeddings from `mixedbread-ai/mxbai-embed-xsmall-v1`
- vector storage and KNN lookup via `sqlite-vec`
- lexical retrieval via SQLite FTS5
- RRF fusion in the context store

Search indexes are derived, not canonical:

- `records` is the authoritative source for durable context.
- `records_fts` mirrors canonical record text for lexical retrieval.
- `record_embeddings` mirrors canonical record search text for semantic
  retrieval.
- Index health is measured with `record_count`, `fts_count`,
  `embedding_count`, and `missing_embedding_count`.
- A fresh index has matching record, FTS, and embedding counts with no missing
  embeddings for the project scope being queried.
- If counts diverge, `ask` can still run, but retrieval is operationally
  degraded until maintenance or write-time refresh rebuilds derived rows from
  canonical records.

## Why this design

The agent says what it wants to do.
Python owns the storage mechanics.

That keeps:

- tool use smaller
- prompts cleaner
- invariants enforced in code
- training trajectories easier for smaller models later
