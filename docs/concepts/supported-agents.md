# Trace Sources & Workflow Adapters

Lerim turns agent traces into reusable context across business workflows.

The context model is meant to sit above support, research, operations, revenue,
custom business agents, and engineering automation. Current adapters cover
supported sources available today. Custom agents use clean trace folders.

## Current adapters

- Claude Code
- Codex CLI
- Cursor
- OpenCode

## Adapter job

Each adapter finds session traces and normalizes them into Lerim's internal trace shape.

The adapter does not write durable context itself.
It only feeds the extraction flow.

## Custom and business-agent traces

For a custom agent, create a folder of already-clean Lerim canonical JSONL files
and register it as a custom project:

```bash
lerim project add ~/lerim-traces/support-clean --type custom
lerim ingest --agent custom
```

Each `.jsonl` file is one completed agent or workflow session. Custom mode does
not run a Lerim adapter and does not compact or normalize files. It reads the
clean files directly and indexes them as `agent_type=custom`.

For sensitive or very noisy traces, run a customer-owned cleaner before import.
Lerim can filter reusable signal during ingestion, but it should not be treated
as the only redaction, privacy, or retention-control layer for arbitrary custom
source data.

## Custom business trace cleaning

A customer-specific cleaner should preserve enough structure for Lerim to
separate routine activity from reusable context.

Useful fields include:

- timestamp
- actor or agent name
- task, ticket, incident, account, or workflow id
- source artifacts and tool outputs
- evidence links or citations
- decisions, assumptions, approvals, and open questions
- customer, workspace, client, engagement, team, or project scope
- review status, retention policy, and redaction requirements

## Scope model

Sessions are matched to registered projects by path.
When a session belongs to a registered project, Lerim writes records for that `project_id` into the global context database.
