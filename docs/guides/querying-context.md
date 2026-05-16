# Querying Context

Lerim provides several ways to search and retrieve context. All query paths are
read-only.

## Start with `lerim answer`

The main query interface is `lerim answer`. It queries the shared context DB and
returns a synthesized answer grounded in retrieved records.

### Recommended agent instruction snippet

Add a short instruction to the agent or workflow that should use Lerim context:

```markdown
## Lerim Context
This workflow uses Lerim for reusable context from prior agent work.
Before repeating investigation, ask Lerim for relevant decisions, constraints,
source evidence, handoffs, and open questions.
Use `lerim answer "your question"` for synthesized answers.
For current runtime state, use `lerim status`.
For detailed usage, invoke the `/lerim` skill.
```

For coding agents, the same snippet can live in `CLAUDE.md`, `AGENTS.md`, or the
tool-specific instruction file for the repository.

## `lerim answer` -- LLM-powered Q&A

The primary query interface. Sends your question through the context answerer
with retrieved context records.

!!! note "Requires running server"
    `lerim answer` is a service command that requires `lerim up` or `lerim serve`
    to be running.

### Basic query

```bash
lerim answer "What sources supported our last competitor-pricing assumption?"
```

The answer flow retrieves relevant records, uses them as context, and returns
a natural language answer with evidence of which records were consulted.

| Flag | Default | Description |
|------|---------|-------------|
| `question` | required | Your question (quote if it contains spaces) |
| `--scope` | `all` | Read scope: `all` projects or one `project` |
| `--project` | -- | Project name/path when `--scope=project` |

### JSON output

Get structured output for scripting or agent integration:

```bash
lerim answer "How is the database configured?" --json
```

Returns JSON with answer metadata (for example: `agent_session_id`, `error`,
and `cost_usd`).

## Inspect raw state when needed

If you need raw local state instead of a synthesized answer:

- Use `lerim status` for project stream health, queue state, and record counts.
- Use `lerim query` for deterministic record/version/session counts and lists.
- Use `lerim queue` or `lerim unscoped` for session-ingestion debugging.
- Inspect the underlying context store directly only as a last-resort local
  debugging step. Raw storage access is not the primary agent-facing query path.

## Tips for effective queries

### Be specific

```bash
# Good -- specific topic
lerim answer "What authentication pattern does the API use?"

# Less effective -- too broad
lerim answer "How does auth work?"
```

### Reference past decisions

```bash
lerim answer "Why did we switch from REST to gRPC for the internal API?"
lerim answer "What problems did we have with the original caching approach?"
```

### Check before implementing

At the start of an agent workflow, ask Lerim for the topic you care about. If
you need a synthesized answer across multiple records, use `lerim answer`:

```bash
lerim answer "What evidence supports the latest compliance decision?"
```
