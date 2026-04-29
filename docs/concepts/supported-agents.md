# Supported Agents

Lerim reads session traces from multiple coding agents.

## Current adapters

- Claude Code
- Codex CLI
- Cursor
- OpenCode

## Adapter job

Each adapter finds session traces and normalizes them into Lerim's internal trace shape.

The adapter does not write durable context itself.
It only feeds the extraction flow.

## Scope model

Sessions are matched to registered projects by path.
When a session belongs to a registered project, Lerim writes records for that `project_id` into the global context database.
