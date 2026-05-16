<p align="center">
  <img src="assets/lerim.png" alt="Lerim Logo" width="160">
</p>

<h3 align="center">Context compiler infrastructure for AI agents.</h3>

<p align="center">
  Lerim extracts reusable decisions, constraints, evidence, and handoffs from completed agent work so future agents start with trusted context instead of raw logs.
</p>

<p align="center">
  <a href="https://pypi.org/project/lerim/"><img src="https://img.shields.io/pypi/v/lerim?style=flat-square&color=245f46" alt="PyPI version"></a>
  <a href="https://pypi.org/project/lerim/"><img src="https://img.shields.io/pypi/pyversions/lerim?style=flat-square" alt="Python versions"></a>
  <a href="https://github.com/lerim-dev/lerim-cli/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-BSL--1.1-a55f3f?style=flat-square" alt="License"></a>
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

# Lerim

Lerim is context compiler infrastructure for AI agents.

It watches supported agent sessions, filters noisy execution history into durable signal, and turns that signal into a shared context layer future agents can query.

Instead of replaying raw traces or losing what happened after each run, Lerim keeps:

- decisions
- constraints
- preferences
- reference facts
- evidence linked back to the source session

## Why Lerim

AI agents now triage tickets, investigate incidents, research markets, prepare handoffs, review policies, and change software.

Every run leaves a trace. Most traces are too long, too noisy, and too platform-specific for the next agent to reuse directly.

Without a durable context layer:

- decisions get re-debated
- constraints get rediscovered
- preferences get ignored
- every new session starts too close to zero

Lerim fixes that by turning raw traces into reusable context records and making them queryable from agent tools and product workflows.

The current package provides the trace-to-context foundation and supported source adapters. For customer deployments, Lerim can be adapted around the business traces that matter: support handoffs, operations incidents, research workflows, revenue processes, security reviews, and internal automation logs.

## Key Capabilities

- Trace-to-context extraction. `ingest` reads supported traces, extracts reusable signal, and can archive routine runs without creating noisy durable records.
- Shared context across agents. What one agent learns can become useful context for a different agent or workflow later.
- Context curation. Lerim consolidates overlap, archives weak records, and keeps the context layer compact.
- Query and startup context. Agents can ask questions against accumulated context or start from a compact context brief.
- Evidence-backed memory. Useful decisions, constraints, preferences, references, and handoffs stay linked to the work that produced them.
- Customer-adaptable workflows. The same context layer can be shaped around a software team, support desk, research process, operations workflow, or custom business agent.

## Business Workflows Lerim Supports

- Research and market intelligence: retain source trails, evidence strength, assumptions, rejected leads, and client-specific brief constraints across agent-assisted research cycles.
- Support operations: preserve triage decisions, escalation evidence, policy references, known fixes, and customer constraints.
- Security and IT: carry forward incident timelines, access-review rationale, policy exceptions, remediation evidence, and helpdesk handoffs.
- Operations: preserve incident decisions, inventory exceptions, supplier or carrier constraints, runbook lessons, and unresolved risks.
- Revenue and customer workflows: reuse account context, positioning decisions, campaign constraints, approvals, and follow-up commitments.
- Engineering automation: retain architecture decisions, failed tests, repo conventions, release lessons, and operational constraints.

## Custom Agent Traces

Built-in `connect` adapters monitor the supported sources available today:
Claude Code, Codex CLI, Cursor, and OpenCode.

For another agent or business workflow, use explicit trace import:

```bash
lerim trace import ./support-agent-run.jsonl \
  --source-name support-bot \
  --source-profile support \
  --scope-type domain \
  --scope support
```

The trace can be JSON, JSONL, or plain text. Lerim normalizes it into the compact
trace shape, stores a canonical copy under the Lerim workspace, registers the
selected scope, and runs ingestion into the shared context store.

Today, the custom-agent path expects the user or customer system to produce the
trace file and call `lerim trace import` after a run. For a customer deployment,
the adapter layer can automate that step around the customer's agent runtime,
ticket system, browser workflow, or internal automation logs.

Customers can also run their own exporter, cleaner, or redaction step before
import. That is the recommended path when traces contain source-specific noise,
large tool payloads, secrets, regulated data, or customer-specific retention
rules. Lerim filters for durable signal during ingestion, but it should not be
used as the only privacy or compliance sanitizer for arbitrary traces.

## Quick Start

Install Lerim:

```bash
pip install lerim
```

Initialize and register the current workspace:

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

Answer a question:

```bash
lerim answer "What sources supported our last competitor-pricing assumption?"
```

## What the Commands Do

### `lerim up`

Starts Lerim in the background so it can watch your workflow and process context jobs.

### `lerim status`

Shows service health and current status.

### `lerim status --live`

Shows live status updates. This is useful for demos and for watching background extraction happen.

### `lerim ingest`

Indexes supported trace sessions and extracts durable context from recent work. When Lerim is running in the background, ingest work is scheduled from your configured intervals.

### `lerim curate`

Improves context quality over time by merging duplicates, archiving weak records, and refreshing useful context. This is where Lerim keeps memory selective instead of turning every trace into permanent context.

### `lerim answer`

Lets you answer questions against accumulated project context.

```bash
lerim answer "What evidence supports the latest compliance decision?"
```

### `lerim context-brief`

Reads or refreshes a generated Markdown startup context for the current project.
This is the fast path an agent can read at the start of work without running
retrieval or synthesis in real time.

```bash
lerim context-brief show
lerim context-brief status
lerim context-brief refresh
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
temperature = 1.0
curate_max_llm_calls = 50
answer_max_retrieval_actions = 20
```

## How It Works

Lerim has six internal phases:

1. `trace_ingestor`
   Reads new supported traces/session metadata and prepares trace windows.

2. `durable_signal_filter`
   Separates reusable signal from implementation evidence and trace noise.

3. `context_writer`
   Writes exactly one episode plus zero or more durable records.

4. `context_curator`
   Refines existing records by merging overlap and retiring low-value stale records.

5. `context_answerer`
   Retrieves relevant records and answers a question using the current context layer.

6. `context_brief_compiler`
   Generates a compact, cited Markdown view from recent durable records so agents
   can start with fast context before querying deeper.

In practice, this means Lerim becomes the shared precedent store behind your agent workflows.

The trace-to-context pipeline is intentionally selective:

```text
raw trace -> evidence -> durable signal -> scoped context -> future agent
```

Most routine traces should produce no new durable record. Lerim's value is compact, cited context, not more logs.

Retrieval blends semantic and lexical signals so agents get compact, relevant
context instead of a raw trace dump.

## Implementation Details

### Technical Storage Model

Global Lerim state lives under `~/.lerim/`:

- `context.sqlite3` — canonical durable context store
- `index/sessions.sqlite3` — session catalog and queue
- `workspace/` — ingest and curate run artifacts
- `workspace/current/<project_id>/CONTEXT_BRIEF.md` — generated current Context Brief view
- `cache/traces/` — compacted agent trace cache
- `models/embeddings/` — local embedding model cache
- `models/huggingface/` — Hugging Face library cache
- `config.toml` — user config
- `platforms.json` — connected platform paths
- `logs/YYYY/MM/DD/` — dated runtime logs (`lerim.log`, `lerim.jsonl`, and `activity.log`)

Project registration only stores host paths in config.
Project separation happens inside the database by `project_id`.

There is no per-project durable store on disk.

### Agent Runtime

The runtime lives under `src/lerim/agents/`.
The trace ingestion flow reads deterministic trace windows, observes typed
findings, filters durable signal aggressively, writes one final context payload,
and persists it for later retrieval. Routine sessions can produce only an
archived episode and no durable records.

The context curator builds semantic-neighbor clusters from active records,
reviews clusters, reviews remaining records for single-record health issues,
and applies validated store operations.

The context answerer plans exact count/list/search retrieval actions, executes
read-only context queries, and synthesizes the answer from retrieved records
only. The context-brief compiler uses the same pattern to write cited
startup context from bounded candidate records.

## Common Commands

```bash
lerim status
lerim status --live
lerim logs --follow
lerim queue
lerim queue --failed
lerim ingest
lerim curate
lerim context-brief show
lerim context-brief status
lerim answer "What decisions exist about caching?"
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
- `tests/integration/` — real extract, curate, and semantic answer coverage
- `tests/e2e/` — full runtime-cycle checks over ingest, curate, and answer

Start here if you want to read the codebase:

- [src/lerim/README.md](src/lerim/README.md)
- [src/lerim/skills/cli-reference.md](src/lerim/skills/cli-reference.md)
- [docs/concepts/how-it-works.md](docs/concepts/how-it-works.md)
- [docs/concepts/context-model.md](docs/concepts/context-model.md)

## Contributing

Contributions are welcome.

Good starting points include:

- trace-source adapters and generic import
- extraction quality
- context curation quality
- docs and demo examples

Helpful links:

- [Contributing Guide](https://docs.lerim.dev/contributing/getting-started/)
- [Open issues](https://github.com/lerim-dev/lerim-cli/issues)
- Trace-source adapter examples: `src/lerim/adapters/`
