<p align="center">
  <img src="assets/lerim.png" alt="Lerim Logo" width="160">
</p>

<h3 align="center">Durable context for coding agents.</h3>

<p align="center">
  Turn coding-agent sessions into reusable context records.
  Capture decisions, constraints, preferences, and evidence so the next agent starts with precedent, not guesswork.
</p>

<p align="center">
  <a href="https://pypi.org/project/lerim/"><img src="https://img.shields.io/pypi/v/lerim?style=flat-square&color=2563eb" alt="PyPI version"></a>
  <a href="https://pypi.org/project/lerim/"><img src="https://img.shields.io/pypi/pyversions/lerim?style=flat-square" alt="Python versions"></a>
  <a href="https://github.com/lerim-dev/lerim-cli/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-BSL--1.1-10b981?style=flat-square" alt="License"></a>
  <a href="https://github.com/lerim-dev/lerim-cli/actions"><img src="https://img.shields.io/github/actions/workflow/status/lerim-dev/lerim-cli/ci.yml?style=flat-square&label=tests" alt="Tests"></a>
  <a href="https://github.com/lerim-dev/lerim-cli"><img src="https://img.shields.io/github/stars/lerim-dev/lerim-cli?style=flat-square" alt="GitHub stars"></a>
</p>

<p align="center">
  <a href="https://lerim.dev/">Website</a>
  ·
  <a href="https://docs.lerim.dev">Docs</a>
  ·
  <a href="https://pypi.org/project/lerim/">PyPI</a>
  ·
  <a href="https://github.com/lerim-dev/lerim-cli/blob/main/LICENSE">License</a>
</p>

<p align="center">
  Works with Claude Code, Codex CLI, Cursor, and OpenCode.
</p>

# Lerim

Lerim is a local-first context runtime for coding agents.

It watches agent sessions, extracts the durable parts, and stores them in one shared context layer that every future agent can query.

Instead of losing the reasoning after each session, Lerim keeps:

- decisions
- constraints
- preferences
- reference facts
- evidence linked back to the source session

## Why Lerim

Coding agents are fast, but they forget.

Without a durable context layer:

- decisions get re-debated
- constraints get rediscovered
- preferences get ignored
- every new session starts too close to zero

Lerim fixes that by turning raw traces into reusable context records and making them queryable from the CLI, Lerim Cloud, and agent tools.

## Key Capabilities

- Local-first storage. Durable context lives in one global SQLite database at `~/.lerim/context.sqlite3`.
- Shared across agents. What Claude Code learns can be reused by Codex, Cursor, or another supported agent later.
- Background maintenance. `sync` ingests sessions, `maintain` consolidates overlap and archives stale records, `ask` retrieves relevant precedent.
- Generated Working Memory. `working-memory` keeps a compact Markdown startup view at `~/.lerim/workspace/current/<project_id>/WORKING_MEMORY.md`.
- Hybrid retrieval. Lerim combines local ONNX embeddings stored through `sqlite-vec` with SQLite FTS5 and RRF fusion.
- Clean agent tool surface. The runtime exposes semantic DB-era tools like `list_context`, `search_context`, `get_context`, `save_context`, `revise_context`, and `count_context` instead of file CRUD.

## Quick Start

Install Lerim:

```bash
pip install lerim
```

Initialize and register the current repo:

```bash
lerim init
lerim connect auto
lerim project add .
```

Start the service:

```bash
lerim up
```

Check status:

```bash
lerim status
lerim status --live
```

Ask a question:

```bash
lerim ask "What do we already know about the auth flow?"
```

## What the Commands Do

### `lerim up`

Starts Lerim in the background so it can watch your workflow and process context jobs.

### `lerim status`

Shows service health and current status.

### `lerim status --live`

Shows live status updates. This is useful for demos and for watching background extraction happen.

### `lerim sync`

Indexes sessions and extracts durable context from recent work. When Lerim is running in the background, sync work is scheduled from your configured intervals.

### `lerim maintain`

Improves context quality over time by merging duplicates, archiving weak records, and refreshing useful context. Background maintenance is also driven by configured intervals.

### `lerim ask`

Lets you ask questions against accumulated project context.

```bash
lerim ask "Why did we choose SQLite for local metadata?"
```

### `lerim working-memory`

Reads or refreshes a generated Markdown startup context for the current project.
This is the fast path a coding agent can read at the start of work without
running retrieval or synthesis in real time.

```bash
lerim working-memory show
lerim working-memory status
lerim working-memory refresh
```

## Configuration

`lerim init` creates the default local configuration. You can override settings in:

```text
~/.lerim/config.toml
```

API keys are read from environment variables, stored by default in:

```text
~/.lerim/.env
```

Example `.env`:

```bash
MINIMAX_API_KEY=your-key
OPENROUTER_API_KEY=your-key
OPENAI_API_KEY=your-key
ZAI_API_KEY=your-key
```

Example provider config:

```toml
[roles.agent]
provider = "minimax"
model = "MiniMax-M2.7"
fallback_models = ["zai:glm-4.7"]
```

## How It Works

Lerim has four main flows:

1. `sync`
   Reads new traces/session metadata and extracts durable context records.

2. `maintain`
   Refines existing records by merging overlap and retiring low-value stale records.

3. `ask`
   Retrieves relevant records and answers a question using the current context layer.

4. `working-memory`
   Generates a compact, cited Markdown view from recent durable records so agents
   can start with fast context before querying deeper.

In practice, this means Lerim becomes the shared precedent store behind your agent workflows.

Semantic retrieval is local:

- ONNX model: `mixedbread-ai/mxbai-embed-xsmall-v1`
- vector storage: `sqlite-vec`
- lexical retrieval: SQLite FTS5
- fusion: `RRF`

## Storage Model

Global Lerim state lives under `~/.lerim/`:

- `context.sqlite3` — canonical durable context store
- `index/sessions.sqlite3` — session catalog and queue
- `workspace/` — sync and maintain run artifacts
- `workspace/current/<project_id>/WORKING_MEMORY.md` — generated current Working Memory view
- `cache/traces/` — compacted agent trace cache
- `models/embeddings/` — local embedding model cache
- `models/huggingface/` — Hugging Face library cache
- `config.toml` — user config
- `platforms.json` — connected platform paths
- `logs/YYYY/MM/DD/` — dated runtime logs (`lerim.log`, `lerim.jsonl`, and `activity.log`)

Project registration only stores host paths in config.
Project separation happens inside the database by `project_id`.

There is no per-project durable store on disk.

## Agent Tools

The agent-facing tool contract is intentionally small:

- `read_trace`
- `list_context`
- `search_context`
- `get_context`
- `save_context`
- `revise_context`
- `archive_context`
- `supersede_context`
- `count_context`
- `note_trace_findings`
- `prune_trace_reads`

These are the authoritative runtime tool names. Keeping the surface DB-era and semantic makes the runtime easier to reason about and gives smaller future models a cleaner action space for training.

## Common Commands

```bash
lerim status
lerim status --live
lerim logs --follow
lerim queue
lerim queue --failed
lerim sync
lerim maintain
lerim working-memory show
lerim working-memory status
lerim ask "What decisions exist about caching?"
```

Setup and management:

```bash
lerim connect auto
lerim project list
lerim project remove <name>
lerim skill install
```

Alternative to the background service:

```bash
lerim serve
```

## Development

```bash
uv venv && source .venv/bin/activate
uv pip install -e '.[test]'
tests/run_tests.sh unit
tests/run_tests.sh smoke
tests/run_tests.sh integration
tests/run_tests.sh e2e
```

Before release, verify the affected path with the relevant suites:

- `tests/smoke/` — quick real-LLM extract sanity
- `tests/integration/` — real extract, maintain, and semantic ask coverage
- `tests/e2e/` — full runtime-cycle checks over sync, maintain, and ask

Start here if you want to read the codebase:

- [src/lerim/README.md](src/lerim/README.md)
- [src/lerim/skills/cli-reference.md](src/lerim/skills/cli-reference.md)
- [docs/concepts/how-it-works.md](docs/concepts/how-it-works.md)
- [docs/concepts/context-model.md](docs/concepts/context-model.md)

## Contributing

Contributions are welcome.

Good starting points include:

- session adapters and adding more agents
- extraction quality
- context maintenance quality
- docs and demo examples

Helpful links:

- [Contributing Guide](https://docs.lerim.dev/contributing/getting-started/)
- [Open issues](https://github.com/lerim-dev/lerim-cli/issues)
- Agent adapter examples: `src/lerim/adapters/`
