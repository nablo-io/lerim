# Capacity and SLOs

These targets describe the intended operating envelope for the current
open-source runtime on one machine, not a hosted multi-tenant service.

## Baseline assumptions

- Storage is on a local SSD, not a network filesystem.
- One background runtime owns routine ingest and curation.
- SQLite runs with WAL enabled so readers can continue during short writes.
- Embedding model download and first-time warmup are outside steady-state SLOs.
- LLM answer generation time is tracked separately from local retrieval time.

## SLO targets

| Area | Target |
| --- | --- |
| `answer` retrieval latency | p50 under 500 ms, p95 under 2 s for a warm local index up to 100k records/project. |
| `answer` end-to-end latency | p95 under 8 s for normal questions when the answer model is available locally or through a responsive API. |
| Ingest throughput | Drain normal agent-session traces at 5-20 MB/minute, bounded mostly by extraction/model time rather than SQLite writes. |
| Queue recovery | After a crash or restart, queued ingest work resumes without manual cleanup; duplicate processing must be idempotent. |
| Index freshness | FTS and embedding indexes are derived from canonical records and should normally have zero missing rows after writes settle. |
| Curation | Routine compaction, supersession, and index refresh should finish in minutes for an active project, not block reads. |

## Search index freshness contract

The `records` table is the source of truth. `records_fts` and
`record_embeddings` are derived indexes used by `answer` retrieval. Search quality
depends on those derived indexes staying complete, so freshness is an explicit
operational contract:

- Every committed record write should schedule or perform a best-effort refresh
  for both FTS and embedding rows.
- A healthy warm index reports `record_count == fts_count`,
  `record_count == embedding_count`, and `missing_embedding_count == 0` for the
  inspected project scope.
- During startup, crash recovery, model changes, or first-time embedding setup,
  temporary gaps are allowed, but the runtime must make forward progress toward
  those healthy counts without requiring manual SQL edits.
- Retrieval may continue while indexes catch up, but user-facing diagnostics
  should describe the index as warming, stale, or degraded instead of implying
  that search is fully fresh.
- Index repair must rebuild from canonical records rather than treating derived
  index rows as authoritative.

## Practical capacity envelope

- Trace size: normal traces should stay under 5 MB; large traces up to 50 MB are
  supported but should be processed asynchronously.
- Records per project: design for 10k-100k active records/project before users
  need to archive, split projects, or tune curation.
- Global database: design for hundreds of projects and up to about 1M total
  records on a developer laptop.
- Queue depth: short bursts of hundreds of pending sessions are acceptable if
  the runtime can make steady forward progress and expose retry state.

## SQLite write-lock assumptions

SQLite is the right default while Lerim is local-first, but it is still a
single-writer database.

- Keep write transactions short: normalize outside the transaction, then commit
  records, versions, embeddings, and queue state together.
- Allow many readers, but assume only one active writer for `ingest` or
  `curate` at a time.
- Use bounded busy timeouts and retry with backoff instead of blocking the CLI
  indefinitely.
- Do not place `~/.lerim/context.sqlite3` on Dropbox, iCloud Drive, NFS, or
  other syncing/network filesystems.
- If a manual command and background runtime contend, the user-facing command
  should fail clearly or wait briefly with a visible message.

## Cloud scale trigger points

Move beyond local SQLite only when the product needs behavior that local-first
SQLite should not pretend to provide:

- multiple machines writing to the same project concurrently
- shared team memory with permissions and audit trails
- p95 `answer` retrieval above 2 s after index tuning at 100k+ active
  records/project
- sustained ingest backlog that cannot drain overnight on a normal developer
  laptop
- trace volumes regularly above 50 MB/session or thousands of sessions/day
- need for centralized backup, observability, billing, or SRE ownership

Until those triggers appear, prefer improving local indexing, pruning,
curation, and queue visibility over introducing a server.
