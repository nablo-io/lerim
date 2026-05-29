Read AGENTS.md if present.

## Lerim Context

Use Lerim as the project context and memory layer when a new chat or task needs
past project knowledge: prior decisions, constraints, preferences, handoffs,
historical failures, or current-vs-historical truth.

Do not use Lerim as a ritual for every chat. Skip it for self-contained tasks
where past project context would not change the answer.

When past context may help, invoke the `lerim` skill or `/lerim`, then read:

- `uv run lerim context-brief show`
- `uv run lerim working-memory show`

Treat both outputs as persisted context, not live workspace state. Still inspect
source files, run `git status`, and rerun relevant checks after edits.
