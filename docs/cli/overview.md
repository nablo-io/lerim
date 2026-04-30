# CLI Overview

The CLI has two groups of commands.

## Host-only commands

These work on local files, Docker, or config:

- `lerim init`
- `lerim project`
- `lerim connect`
- `lerim up`
- `lerim down`
- `lerim logs`
- `lerim query`
- `lerim queue`
- `lerim retry`
- `lerim skip`
- `lerim skill`
- `lerim auth`
- `lerim working-memory show`
- `lerim working-memory status`
- `lerim working-memory path`
- `lerim working-memory refresh`

## Server-backed commands

These talk to the running Lerim service:

- `lerim sync`
- `lerim maintain`
- `lerim ask`
- `lerim status`
- `lerim unscoped`

## Durable context

The CLI works with the global context database.
Project commands register scope only.

Working Memory is a generated Markdown view of the global context database. It
is useful at agent startup, but `~/.lerim/context.sqlite3` remains the source of
truth.
