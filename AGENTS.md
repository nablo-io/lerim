Read global instructions: ~/.codex/AGENTS.md

# Lerim

This repo is the DB-only Lerim CLI/runtime.

## Summary

Use these files first:

- [README.md](README.md) — product and runtime overview
- [src/lerim/README.md](src/lerim/README.md) — source code map
- [src/lerim/skills/cli-reference.md](src/lerim/skills/cli-reference.md) — CLI source of truth
- [docs/concepts/how-it-works.md](docs/concepts/how-it-works.md) — architecture
- [docs/simple-coding-rules.md](docs/simple-coding-rules.md) — simplification rules

## Current architecture

The important storage rules are strict:

- durable context is only `~/.lerim/context.sqlite3`
- session catalog is `~/.lerim/index/sessions.sqlite3`
- run artifacts are only `~/.lerim/workspace/`

Registered projects are just scoped host paths in config.
Project separation happens in the database by `project_id`.

## How to query existing context

Use Lerim as the project context and memory layer when a new chat or task needs
past project knowledge: prior decisions, constraints, preferences, handoffs,
historical failures, or current-vs-historical truth.

Do not use Lerim as a ritual for every chat. Skip it for self-contained tasks
where past project context would not change the answer.

When past context may help, invoke the `lerim` skill. In clients that expose
slash skills, invoke `/lerim`. Then read both generated context views from this
checkout:

- `uv run lerim context-brief show` for durable project decisions, constraints, preferences, and facts.
- `uv run lerim working-memory show` for recent continuation context and short-term project movement.

Read their freshness prefaces before trusting the generated Markdown.
Treat both as persisted context, not live workspace state; still inspect source files,
run `git status`, and rerun relevant checks after edits.

Use `uv run lerim query ...` for exact lookup and
`uv run lerim answer "<question>"` for synthesized context.
Do not use deprecated aliases such as `lerim ask`, `sync`, or `maintain`.
Do not inspect repo-local store artifacts or hardcode project IDs.

## Runtime tool contract

Ingest extraction and curate are BAML/LangGraph graphs under `src/lerim/agents/`.
They do not use PydanticAI tool loops.

The answer agent-facing tools are:

- `count_context`
- `list_context`
- `search_context`
- `get_context`

Do not add file-era tools back.

Not allowed:

- file CRUD as durable context tools
- raw SQL as an agent tool
- alternate code paths that preserve removed architecture

## Rules

- Never make silent decisions. Ask Isaac when a product choice is ambiguous.
- Never commit unless the user asks clearly.
- Do not revert unrelated user changes.
- Keep docs updated when boundaries change.
- All functions need docstrings.
- All modules need a short top-level docstring.
- Every module should have tests in `tests/`.
- Never add fallback behavior for missing packages.
- Never keep old code paths alive.
- Prefer the smallest simple solution.
- Lerim extraction and curate are always LLM-driven. No non-LLM bypass path.
- When changing tests, update [tests/README.md](tests/README.md).

## Testing

Run relevant tests after each change.
If you skip tests, say why.
