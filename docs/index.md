# Lerim

Lerim is persistent context for coding agents.

## Summary

Lerim watches session traces from supported coding agents and turns them into durable context records.

The current architecture is simple:

- canonical durable store: `~/.lerim/context.sqlite3`
- canonical session catalog: `~/.lerim/index/sessions.sqlite3`
- canonical run artifacts: `~/.lerim/workspace/`

## Main flows

- `sync` extracts records from new traces
- `maintain` merges, links, and archives low-value records
- `ask` retrieves records and answers a question

## Start here

- [Installation](installation.md)
- [Quickstart](quickstart.md)
- [How It Works](concepts/how-it-works.md)
- [Working Memory](concepts/working-memory.md)
- [CLI Overview](cli/overview.md)
