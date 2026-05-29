# Agent Startup Context

Use this guide when you want a coding agent to use Lerim as project context and
memory when past work may matter.

The setup has three parts:

1. Install and initialize Lerim.
2. Install the bundled Lerim skill for your agent.
3. Add a short instruction to the repository's agent instruction file.

## 1. Install Lerim

Install the CLI and register the repository you want Lerim to remember:

```bash
pip install lerim
lerim init
lerim connect auto
cd ~/codes/my-project
lerim project add .
```

If you use `uv`, this is also fine:

```bash
uv tool install lerim
```

Start the local service when you want server-backed commands such as
`lerim answer`, `lerim ingest`, `lerim curate`, and `lerim status`:

```bash
lerim up
```

The startup context reads below are host-only local reads. They do not require
the server:

```bash
lerim context-brief show
lerim working-memory show
```

## 2. Install The Agent Skill

Install the bundled skill:

```bash
lerim skill install
```

This copies the Lerim skill into standard agent skill directories:

- `~/.agents/skills/lerim/` for Codex, Cursor, OpenCode, and other shared-skill clients.
- `~/.claude/skills/lerim/` for Claude Code.

The skill is the detailed playbook. It explains command resolution, when to use
Context Brief versus Working Memory, and when to use `query` or `answer`.

## 3. Add Agent Instructions

Add a short Lerim section to the repository instruction file your agent reads,
such as `AGENTS.md`, `CLAUDE.md`, or another tool-specific instruction file.

```markdown
## Lerim startup context

Use Lerim as the project context and memory layer when a new chat or task needs
past project knowledge: prior decisions, constraints, preferences, handoffs,
historical failures, or current-vs-historical truth.

Do not use Lerim as a ritual for every chat. Skip it for self-contained tasks
where past project context would not change the answer.

When past context may help, invoke the `lerim` skill. In clients that expose
slash skills, invoke `/lerim`. Then read both generated context views:

- `lerim context-brief show` for durable project decisions, constraints, preferences, and facts.
- `lerim working-memory show` for recent continuation context and short-term project movement.

Read their freshness prefaces before trusting the generated Markdown.
Treat both as persisted context, not live workspace state; still inspect source files,
run `git status`, and rerun relevant checks after edits.

Use `lerim query ...` for exact lookup and
`lerim answer "<question>"` for synthesized context.
Do not use removed aliases: `lerim ask`, `lerim sync`, or `lerim maintain`.
Do not inspect repo-local store artifacts or hardcode project IDs.
```

For local development inside the Lerim repository itself, use `uv run lerim ...`
instead of `lerim ...` so the agent runs the checkout version:

```bash
uv run lerim context-brief show
uv run lerim working-memory show
```

## 4. Verify The Flow

From the repository, run:

```bash
lerim context-brief show
lerim working-memory show
lerim query records count --kind decision
```

If the local service is running, also verify synthesized answers:

```bash
lerim answer "What should I know before working in this project?"
```

You know the setup is working when:

- Context Brief prints a live freshness preface and a generated startup brief.
- Working Memory prints a live freshness preface and recent project movement.
- `lerim query` returns deterministic local context data.
- `lerim answer` returns a grounded synthesized answer, or says there is no direct stored support.

If any command cannot run, ask the agent to say that plainly and continue from
the workspace instead of pretending Lerim answered.
