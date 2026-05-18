# Context Model

## Summary

Lerim stores durable context records backed by source evidence.

The model starts from agent traces, but traces are evidence rather than memory.
Lerim extracts the small amount of durable signal worth reusing later and keeps
evidence linked back to the source run.

High-level record kinds include:

- `episode`
- `fact`
- `decision`
- `preference`
- `constraint`

Record kind is the only ingestion taxonomy. Product insights, review surfaces,
and workflow views can be built later from these records, but ingestion does not
persist those labels.

Each record can carry:

- title
- summary
- structured payload
- status
- `source_profile`
- validity window
- graph links to related records
- evidence from traces
- version history

## Important idea

The database is the source of truth.

Raw traces are not the product surface. Durable records are.

Most traces should produce no new durable record unless they contain reusable
decisions, preferences, constraints, facts, or handoff context.

## What curate does

`curate` works on records, not files.

It can:

- merge duplicates
- archive low-value records
- supersede outdated records
- keep context selective as more traces are processed

## What the context graph does

The context graph is derived from active curated records.

It can link:

- evidence to the decision it supports
- constraints to decisions that depend on them
- newer context to older records it supersedes
- contradictory records for review
- records that share a reusable topic

The graph stores the persisted semantic cluster for each node. The dashboard can
derive Louvain and combined visual lenses from the accepted graph links without
adding those transient labels to the runtime store.

## What answer does

`answer` retrieves records with hybrid search and then fetches the full records needed for the answer.
