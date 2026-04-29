---
name: lerim
description: Query Lerim's persistent project context before coding. Use it to check prior decisions, constraints, preferences, and historical context through exact queries or synthesized answers.
---

# Lerim

Use this skill when you need project context before or during coding work.

Lerim stores durable context from past agent sessions and exposes it through a small CLI/API surface. The important distinction is:

- `lerim query` for exact deterministic retrieval
- `lerim ask` for retrieval plus synthesis
- `lerim status` for runtime health, project counts, and queue state

## When to use

- Before starting a task in a repo with existing Lerim history
- When a decision, constraint, or preference may already have precedent
- When debugging and you want prior facts or earlier decisions
- When you need current vs historical truth from stored records

## Fast path

Start with the smallest tool that answers the question:

1. Use `lerim query` for counts, latest rows, date windows, and exact inspection.
2. Use `lerim ask` when you need a synthesized explanation or semantic retrieval.
3. Use `lerim status` or `lerim queue` when the question is operational rather than semantic.

Examples:

```bash
lerim query records count --kind decision
lerim query records list --kind constraint --limit 10
lerim ask "What do we already know about the auth flow?"
lerim ask "What changed recently about storage and why?"
lerim status --json
```

## Working rules

- Prefer `query` over `ask` when the question is exact.
- Prefer `ask` over manual browsing when the question needs synthesis across records.
- Treat Lerim as the context layer, not as a place to manually edit durable state during normal coding work.
- Query Lerim through its CLI/API instead of inspecting storage directly.
- If the runtime is down, say so plainly and use the repo/codebase directly rather than pretending Lerim answered.

## Operational notes

- `lerim up` runs the local service in Docker.
- `lerim serve` runs the local API directly without Docker.
- `lerim dashboard` is only a transition message; the hosted UI lives on Lerim Cloud.
- Local durable context is stored in the global SQLite store.

## Read more when needed

Open [cli-reference.md](cli-reference.md) only when you need:

- full command syntax
- runtime vs host-only command behavior
- less common commands like `queue`, `retry`, `skip`, `skill`, or `auth`
