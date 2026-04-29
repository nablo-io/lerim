# Context Model

## Summary

Lerim stores durable context records in SQLite.

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

## What maintain does

`maintain` works on records, not files.

It can:

- merge duplicates
- archive low-value records
- add links
- supersede outdated records

## What ask does

`ask` retrieves records with hybrid search and then fetches the full records needed for the answer.
