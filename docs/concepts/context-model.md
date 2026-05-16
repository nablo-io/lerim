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

Each record can carry:

- title
- summary
- structured payload
- status
- validity window
- links to related records
- evidence from traces
- version history

## Important idea

The database is the source of truth.

Raw traces are not the product surface. Durable records are.

Most traces should produce no new durable record unless they contain reusable
decisions, preferences, constraints, facts, references, or handoff context.

## What curate does

`curate` works on records, not files.

It can:

- merge duplicates
- archive low-value records
- add links
- supersede outdated records
- keep context selective as more traces are processed

## What answer does

`answer` retrieves records with hybrid search and then fetches the full records needed for the answer.
