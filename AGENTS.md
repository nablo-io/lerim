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
- normalized traces are only `~/.lerim/cache/traces/<agent>/<run_id>.jsonl`

Registered projects are just scoped host paths in config.
Project separation happens in the database by `project_id`.

## Trace format and parsing

Lerim does not write harness parsers. Transcript parsing belongs to Letta's
[trajectory](https://github.com/letta-ai/trajectory) standard, and
**trajectory-v1 is Lerim's single internal trace format**.

- Native harness sessions go through the pinned `@letta-ai/trajectory` npm
  package, driven from `src/lerim/adapters/trajectory_bridge.py` over its
  protocol-v1 stdin/stdout bridge. That module owns transport only.
- Custom folders, `lerim trace import`, and `lerim_trace_submit` produce the same
  trajectory-v1 records directly. One format, one downstream pipeline.
- Records are `meta` (always first), `user`, `reasoning`, `assistant`
  (optionally with `tool_calls`), and `tool`. The canonical schema is
  `trajectory-v1.schema.json`, shipped inside the installed npm package.
- **The trace cache is one JSON record per line, compact.** Context records cite
  evidence as `line:<N>` into these files, so pretty-printing or merging lines
  silently invalidates every citation already in the database.
- trajectory does not redact. Lerim's redaction still runs on normalized records
  before they are written to the cache.
- A session the normalizer rejects is skipped and logged. A per-session failure
  must never fail its batch.

Node.js >= 20 is therefore a hard runtime requirement. `lerim init`, `lerim up`,
and `lerim serve` preflight it and fail with an install hint. Do not add a
Python fallback parser.

Adding support for a new harness means contributing an adapter upstream to
trajectory, not adding a parser here. `SOURCE_MAP` in
`src/lerim/adapters/trajectory_source.py` lists what Lerim ingests today
(`claude`, `codex`, `letta-code`, `openclaw`) and `UNSUPPORTED_PLATFORMS` lists
what it refuses and why. Cursor, OpenCode, and pi have no upstream adapter.
Hermes, OpenHands, and DeepAgents do, but their session stores are not transcript
files — wiring them means writing per-harness assembly code, which is exactly
what this migration deleted, so they stay unsupported until upstream hands Lerim
a transcript.

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

The main agent flows are DSPy module pipelines under `src/lerim/agents/`.
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
- hand-rolled harness parsers alongside the trajectory normalizer
- a second trace format alongside trajectory-v1

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
