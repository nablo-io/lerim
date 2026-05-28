# CLI Overview

The CLI has two groups of commands.

## Host-only commands

These work on local files, Docker, or config:

- `lerim init`
- `lerim project`
- `lerim connect`
- `lerim mcp`
- `lerim up`
- `lerim down`
- `lerim logs`
- `lerim trace import`
- `lerim profile list`
- `lerim profile show`
- `lerim profile validate`
- `lerim profile register`
- `lerim query`
- `lerim queue`
- `lerim retry`
- `lerim skip`
- `lerim skill`
- `lerim auth`
- `lerim context-brief show`
- `lerim context-brief status`
- `lerim context-brief path`
- `lerim context-brief refresh`
- `lerim working-memory show`
- `lerim working-memory status`
- `lerim working-memory path`
- `lerim working-memory refresh`
- `lerim context records`

## Server-backed commands

These talk to the running Lerim service:

- `lerim ingest`
- `lerim curate`
- `lerim answer`
- `lerim status`
- `lerim unscoped`

## Durable context

The CLI works with the global context database.
Project commands register scope only.

Context Brief is a generated long-term Markdown view of the global context
database. Working Memory is a separate short-term generated view of recent
record-version movement. Both are useful at agent startup, but
`~/.lerim/context.sqlite3` remains the source of truth.
