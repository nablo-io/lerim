# Trace Sources & Workflow Adapters

Lerim turns agent traces into reusable context across business workflows.

The context model is meant to sit above support, research, operations, revenue,
custom business agents, and engineering automation. Current adapters cover
supported sources available today while the broader trace-import layer expands.

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

For a custom agent, the current bridge is explicit import:

```bash
lerim trace import ./agent-run.jsonl \
  --source-name support-bot \
  --source-profile support \
  --scope-type domain \
  --scope support
```

The custom agent or surrounding system produces a JSON, JSONL, or text trace.
Lerim normalizes that file, writes a canonical compact copy under the Lerim
workspace, registers the selected scope, and runs ingestion into the shared
context store.

Built-in adapters monitor known tools automatically. Custom agents currently use
explicit import unless a customer deployment adds a workflow-specific adapter.

For sensitive or very noisy traces, run a customer-owned cleaner before import.
Lerim can filter reusable signal during ingestion, but it should not be treated
as the only redaction, privacy, or retention-control layer for arbitrary custom
source data.

## Custom business trace import

A customer-specific trace import should preserve enough structure for Lerim to
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
