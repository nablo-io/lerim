<p align="center">
  <img src="docs/assets/lerim-context-compiler.svg" alt="Lerim compiles agent traces into a reusable context graph for future agents and humans" width="860">
</p>


<h1 align="center">Lerim compiles completed agent traces into cited, reusable context.</h1>

<p align="center">
  Lerim sits above agent traces, compiles useful signal into cited context and eval assets, and gives future agents the operating memory they need before work begins.
</p>

<p align="center">
  <em>Lerim is an independent open-source project by <a href="https://nablo.io">Nablo</a> - one example of our work. It compiles context and does not train models; model specialization (distillation, RL) is Nablo's separate post-training business and does not depend on Lerim.</em>
</p>

<p align="center">
  <a href="https://pypi.org/project/lerim/"><img src="https://img.shields.io/pypi/v/lerim?style=flat-square&color=d4a44a" alt="PyPI version"></a>
  <a href="https://pypi.org/project/lerim/"><img src="https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat-square" alt="Python 3.11+"></a>
  <a href="https://github.com/nablo-io/lerim/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue?style=flat-square" alt="License"></a>
  <a href="https://github.com/nablo-io/lerim/actions"><img src="https://img.shields.io/github/actions/workflow/status/nablo-io/lerim/ci.yml?style=flat-square&label=tests" alt="Tests"></a>
  <a href="https://github.com/nablo-io/lerim"><img src="https://img.shields.io/github/stars/nablo-io/lerim?style=flat-square" alt="GitHub stars"></a>
</p>

<p align="center">
  <a href="https://nablo.io/lerim">Website</a>
  ·
  <a href="https://docs.nablo.io">Docs</a>
  ·
  <a href="docs/benchmarks/index.md">Benchmarks</a>
  ·
  <a href="docs/examples/index.md">Examples</a>
  ·
  <a href="https://pypi.org/project/lerim/">PyPI</a>
  ·
  <a href="https://github.com/nablo-io/lerim/blob/main/LICENSE">License</a>
</p>

# Lerim

Lerim is a context compiler for repeated AI agent workflows.

Agents leave traces everywhere: terminals, tools, tickets, code reviews,
support cases, research runs. Most of that history is too noisy to reuse
directly. Lerim filters those traces into evidence-backed context records and
eval-ready signal: the decisions, constraints, facts, preferences, corrections,
and handoffs future agents should not have to rediscover, each linked back to
the source session.

## What The Demo Shows

| Moment | Lerim does | Future agents get |
| --- | --- | --- |
| A completed agent run lands | Imports a source session from an adapter, MCP submit, or clean custom JSONL | A stable source boundary instead of a transcript paste |
| The trace is noisy | Compacts the run and filters for reusable decisions, constraints, facts, preferences, corrections, and handoffs | Durable context and eval-ready signal, not another log index |
| Someone asks later | Retrieves relevant records and answers with citations back to stored evidence | A shorter start with less re-explaining |

Most routine traces produce no durable record. Lerim's value is compact, cited
context, not more logs.

## Quick Install

```bash
pip install lerim
lerim init
lerim connect auto --mode auto
lerim project add .
lerim up
```

Native adapters ingest completed local sessions where a stable trace store
exists; MCP setup writes tool entries for compatible agents. See
[Agent Support](#agent-support) for what is verified per agent.

Then ask Lerim what a future agent should know:

```bash
lerim answer "What context should I know before working in this project?"
```

## Why Lerim

AI agents now triage tickets, investigate incidents, research markets, prepare
handoffs, review policies, analyze customers, and change software. Every run
leaves a trace, and most traces are too long, too noisy, and too
platform-specific for the next agent to reuse.

Without a durable context layer, decisions get re-debated, constraints get
rediscovered, preferences get ignored, corrections never become reusable
context or eval signal, and every new session starts too close to zero.

Lerim fits best where one repeated workflow has trace access, a workflow owner,
privacy constraints, and a measurable quality failure to fix. Coding is the
proof-rich workflow today because the native adapters are mature; support,
incident, research, and compliance run the same compiler through custom traces.

## Key Capabilities

- Trace-to-context extraction. `ingest` reads supported sources and custom clean-trace folders, extracts reusable signal, and can archive routine runs without creating noisy durable records.
- Shared context across agents. What one agent learns can become useful context for a different agent or workflow later.
- Context curation. Lerim consolidates overlap, archives weak records, and keeps the context layer compact.
- Derived context graph. Lerim links related decisions, constraints, evidence, facts, and handoffs for curation and future/hosted visualization.
- Query and startup context. Agents can ask questions against accumulated context or start from a compact context brief.
- Evidence-backed memory. Useful decisions, constraints, preferences, facts, and handoffs stay linked to the work that produced them.
- Skill updates. Register a skill or instruction file, let Lerim propose evidence-backed edits from learned context, then review the diff in the dashboard before applying it.
- Custom source profiles. Coding, support, and incident workflows share one compiler, and teams can register YAML profiles for their own verticals with focus, noise, evidence, and scope rules.

## What Lerim Is Not

- Not a raw transcript replay tool.
- Not a broad `memory_save` bucket for agents to write arbitrary memories.
- Not a replacement for observability. Observability keeps the trace; Lerim compiles reusable context from completed source sessions.
- Not a claim that every listed agent has native completed-session ingestion. MCP recall is useful, but it is different from native trace ingestion.

## Agent Support

Lerim has two integration layers:

- **Native trace adapters** read completed local sessions and feed Lerim's compiler.
- **MCP support** lets compatible agents query Lerim context and explicitly submit completed sessions through `lerim_trace_submit`; it is not automatic local-history capture.

| Support level | Agents and sources |
| --- | --- |
| Native adapter plus MCP config writer | Claude Code, Codex CLI, Cursor, OpenCode |
| MCP config writer; live recall/submit only where verified | Gemini CLI, Cline, Claude Desktop, OpenClaw, Hermes, Goose, Roo Code, Kilo Code, Windsurf |
| Native adapter, no MCP claim | pi |
| Experimental or user-owned path | OpenHuman, custom JSONL, generic MCP trace submit |

See the [integration matrix](docs/integrations/matrix.md) for the exact support
boundary and evidence level per agent.

## MCP Quickstart

Install Lerim into an MCP client (dry-run first, then write):

```bash
lerim connect gemini-cli --mode mcp --dry-run
lerim connect gemini-cli --mode mcp
```

MCP tools: `lerim_context_brief`, `lerim_context_answer`, `lerim_context_search`,
`lerim_records_list`, `lerim_context_feedback`, `lerim_trace_submit`,
`lerim_ingest_status`.

See [MCP Quickstart](docs/guides/mcp-quickstart.md) for the generic client
config, the absolute-path rationale, and verification.

## Benchmarks

Benchmark numbers live in docs, not in a marketing scoreboard. Start with
[Benchmark Overview](docs/benchmarks/index.md) for the map and reporting rules:

- [Benchmark Suite](docs/benchmarks/benchmark-suite.md): what each benchmark
  surface measures and its boundary.
- [Lerim Results](docs/benchmarks/lerim-results.md): first-party raw artifacts,
  commands, and boundaries, including retrieval-only and aggregate-only scope.
- [Market Comparison](docs/benchmarks/market-comparison.md): source-backed rows
  with provenance for each external number.

| Surface | Evidence |
| --- | --- |
| LongMemEval-S retrieval | Full 500-question hybrid + lexical retrieval-only artifact |
| Context budget | Full 500-question context-selection artifact, recall vs. token reduction |
| Extraction quality | Aggregate-only 47-case diagnostic report |

## Skill Updates

Lerim can also update the instructions future agents use. Register a skill
directory, `SKILL.md`, `AGENTS.md`, `CLAUDE.md`, or another instruction file;
Lerim scans scoped context records from past traces and proposes small,
evidence-backed edits. Review each unified diff in the dashboard before applying.
Targets default to review mode; auto-apply is opt-in and bounded by policy.

```bash
lerim skill target add ~/.agents/skills/clean-code \
  --description "Keep simplification guidance current"
lerim skill refresh clean-code
lerim dashboard
```

See [Skill Updates](docs/guides/skill-updates.md) for the dashboard workflow and
[CLI: lerim skill](docs/cli/skill.md) for command details.

## Custom & Non-Coding Agents

Lerim is not only for coding agents. Support, incident and security operations,
research, compliance, revenue, and other custom business agents feed the same
compiler through clean JSONL traces and a signal profile that matches the
workflow.

<p align="center">
  <img src="docs/assets/lerim-context-loop.svg" alt="Lerim's context loop: your agent produces a completed run, Lerim compiles it into cited context, and that context is served back into the next run." width="860">
</p>

Bundled signal profiles cover the common verticals out of the box:

| Profile | Workflow |
| --- | --- |
| `coding` | Repository and coding-agent work (default). |
| `support` | Customer support and customer operations. |
| `ops` | Incident response, operations, and reliability. |
| `research` | Research, market intelligence, and analysis. |
| `compliance` | Compliance, legal, regulatory, and policy review. |
| `generic` | General-purpose fallback. |

List them with `lerim profile list` / `lerim profile show research`, or write
your own in [a few minutes](docs/guides/custom-source-profiles.md). Start at
[Custom & Non-Coding Agents](docs/guides/custom-agents.md) for the full path,
or jump to a worked demo:
[support](docs/guides/support-ops-demo.md) ·
[incident](docs/guides/incident-ops-demo.md) ·
[research](docs/guides/research-demo.md) ·
[compliance](docs/guides/compliance-demo.md).

### Custom Agent Traces

Built-in `connect` adapters cover Claude Code, Codex CLI, Cursor, OpenCode, and
pi. For any other agent or business workflow, register already-clean canonical
JSONL — one `.jsonl` file per completed session. Custom mode has no adapter and
no compaction step, so you own export, cleaning, redaction, and retention before
files enter the folder.

See [Submit a Custom Agent Trace](docs/guides/submit-custom-agent-trace.md) for
the JSONL schema, the `clean_to_lerim_jsonl.py` pattern, and the
`lerim trace import` profile/scope flags.

## Common Commands

```bash
lerim status              # pipeline and queue state
lerim ingest               # compile completed sessions into context
lerim curate               # consolidate and prune records
lerim answer "What decisions exist about caching?"
lerim connect auto         # (re)write agent integrations
lerim context-brief show   # compact startup context
```

Full command reference: [CLI Overview](docs/cli/overview.md).

## Development

```bash
uv venv && source .venv/bin/activate
uv pip install -e '.[test]'
tests/run_tests.sh unit
```

See the [Contributing Guide](https://docs.nablo.io/contributing/getting-started/)
for full dev setup, the live test suites, and the release checklist.

To read the codebase, start with
[src/lerim/README.md](src/lerim/README.md) and
[docs/concepts/how-it-works.md](docs/concepts/how-it-works.md).

## License

Lerim core is Apache-2.0. The local CLI, runtime, self-hosted sync server,
native adapters, context DB schema, benchmark scripts, and integration docs stay
usable without any paid account. Any hosted, commercial, or model-training
offering is Nablo's business, not a Lerim product. See COMMERCIAL.md for the
open-source scope.

Want Lerim's compiled context feeding a trained, specialized model - or want us
to host and run this for you? [Get in touch →](https://nablo.io/#audit)

## Contributing

Contributions are welcome.

Good starting points include:

- trace-source adapters and custom trace-folder examples
- extraction quality
- context curation quality
- context graph link quality
- docs and demo examples

Helpful links:

- [Contributing Guide](https://docs.nablo.io/contributing/getting-started/)
- [Open issues](https://github.com/nablo-io/lerim/issues)
- Trace-source adapter examples: `src/lerim/adapters/`
