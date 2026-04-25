# Querying Context

Lerim provides several ways to search and retrieve context. All query paths are
read-only.

## Start with `lerim ask`

The main query interface is `lerim ask`. It queries the shared context DB and
returns a synthesized answer grounded in retrieved records.

### Recommended CLAUDE.md / AGENTS.md snippet

Add the following to your project's `CLAUDE.md` or `AGENTS.md` so your coding
agent knows about Lerim from the start of every session:

```markdown
## Lerim Context
This project uses Lerim for persistent context across coding sessions.
Durable Lerim records live in `~/.lerim/context.sqlite3`.
Run artifacts live in `~/.lerim/workspace/`.
For synthesized answers, use `lerim ask "your question"` (requires server).
For current runtime state, use `lerim status`.
For detailed usage, invoke the `/lerim` skill.
```

## `lerim ask` -- LLM-powered Q&A

The primary query interface. Sends your question to the PydanticAI ask flow with
retrieved context records.

!!! note "Requires running server"
    `lerim ask` is a service command that requires `lerim up` or `lerim serve`
    to be running.

### Basic query

```bash
lerim ask "Why did we choose Postgres over SQLite?"
```

The ask flow retrieves relevant records, uses them as context, and returns
a natural language answer with evidence of which records were consulted.

| Flag | Default | Description |
|------|---------|-------------|
| `question` | required | Your question (quote if it contains spaces) |
| `--scope` | `all` | Read scope: `all` projects or one `project` |
| `--project` | -- | Project name/path when `--scope=project` |

### JSON output

Get structured output for scripting or agent integration:

```bash
lerim ask "How is the database configured?" --json
```

Returns JSON with answer metadata (for example: `agent_session_id`, `error`,
and `cost_usd`).

## Inspect raw state when needed

If you need raw local state instead of a synthesized answer:

- Use `lerim status` for project stream health, queue state, and record counts.
- Use `lerim query` for deterministic record/version/session counts and lists.
- Use `lerim queue` or `lerim unscoped` for session-ingestion debugging.
- Inspect `~/.lerim/context.sqlite3` directly only as a last-resort local
  debugging step. Raw SQL is not the primary agent-facing query path.

## Tips for effective queries

### Be specific

```bash
# Good -- specific topic
lerim ask "What authentication pattern does the API use?"

# Less effective -- too broad
lerim ask "How does auth work?"
```

### Reference past decisions

```bash
lerim ask "Why did we switch from REST to gRPC for the internal API?"
lerim ask "What problems did we have with the original caching approach?"
```

### Check before implementing

At the start of a coding session, ask Lerim for the topic you care about. If
you need a synthesized answer across multiple records, use `lerim ask`:

```bash
lerim ask "What was the rationale for the database migration strategy?"
```
