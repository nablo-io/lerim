---
name: lerim
description: Query Lerim's persistent project context before coding. Use it to check prior decisions, constraints, preferences, and historical context through exact queries or synthesized answers.
---

# Lerim

Use this skill when you need project context before or during coding work.

Lerim stores durable context from past agent sessions and exposes it through a small CLI/API surface. The important distinction is:

- `lerim working-memory show` for instant precomputed startup context
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

1. Use `lerim working-memory show` at startup for fast project context.
2. Use `lerim query` for counts, latest rows, date windows, and exact inspection.
3. Use `lerim ask` when you need a synthesized explanation or semantic retrieval.
4. Use `lerim status` or `lerim queue` when the question is operational rather than semantic.

Examples:

```bash
lerim working-memory show
lerim working-memory status
lerim query records count --kind decision
lerim query records list --kind constraint --limit 10
lerim ask "What do we already know about the auth flow?"
lerim ask "What changed recently about storage and why?"
lerim status --json
```

## Working Memory flow

Working Memory is the startup path. It is generated ahead of time by the daemon
or by `lerim working-memory refresh`, so `show`, `status`, and `path` should be
fast local reads.

```mermaid
flowchart TD
    A["Agent starts in a repo"] --> B["lerim working-memory show"]
    B --> C{"Current markdown exists?"}
    C -- "yes" --> D["Read generated context"]
    C -- "no" --> E["lerim working-memory status"]
    E --> F["Run or suggest refresh"]
    D --> G{"Need exact or deeper context?"}
    G -- "exact" --> H["lerim query"]
    G -- "synthesis" --> I["lerim ask"]
    G -- "no" --> J["Continue work"]
```

## Working rules

- Working Memory is a generated markdown view of SQLite records, not durable state.
- Read the freshness block. If it reports changed records and newest context matters, suggest `lerim working-memory refresh`.
- Use `lerim working-memory status` to see dynamic age, record-change count, current path, latest run, and suggested action.
- Use `lerim working-memory path` when another tool needs the stable Markdown path.
- Do not hardcode project IDs. Run commands from inside the repo or pass `--project <name-or-path>`.
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
