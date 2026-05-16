# lerim trace

Import explicit generic agent traces.

## Overview

`lerim trace import` is the current bridge for custom agents and business
workflows that are not one of the built-in connected sources.

Built-in `connect` adapters discover sessions from supported local tools such as
Claude Code, Codex CLI, Cursor, and OpenCode. Custom agents work differently
today: the agent or surrounding system should write a trace file, then call
`lerim trace import` for that run.

## Syntax

```bash
lerim trace import <path> \
  --source-name <name> \
  --source-profile <profile> \
  --scope-type <project|domain|user|session|workspace|custom> \
  --scope <scope>
```

## Example

```bash
lerim trace import ./support-agent-run.jsonl \
  --source-name support-bot \
  --source-profile support \
  --scope-type domain \
  --scope support
```

## What Happens

1. Lerim reads a JSON, JSONL, or text trace file.
2. It normalizes the trace into Lerim's compact user/assistant event shape.
3. It writes the normalized copy under the Lerim workspace imports directory.
4. It registers the selected scope in the context store.
5. It runs trace ingestion and writes any durable records into the shared context store.

## Trace Shape

For JSONL, each line can be a message-like object:

```json
{"role":"customer","content":"The renewal customer asked for legal approval.","timestamp":"2026-05-16T09:00:00Z"}
{"role":"agent","content":"Checked policy notes and opened a follow-up task.","timestamp":"2026-05-16T09:02:00Z"}
```

For JSON, Lerim accepts either an array of message-like objects or an object with
a `messages`, `events`, `trace`, `steps`, or `items` list.

Plain text is accepted as one trace message. It is useful for quick pilots, but
structured JSON or JSONL is better because timestamps, actor roles, source
artifacts, decisions, evidence links, and workflow identifiers survive import.

## Custom Cleaning

Teams may run their own exporter, cleaner, or redaction script before import.
That is the right place to remove secrets, regulated fields, oversized tool
payloads, screenshots, binary blobs, and source-specific noise.

Lerim's ingestion flow is selective about durable signal: routine traces can
produce no permanent durable record, and useful records are compacted around
decisions, constraints, evidence, assumptions, and handoffs. That filtering is
not a replacement for customer-owned privacy, retention, or compliance cleaning
before a trace enters Lerim.

## Scope

Scope decides where imported context belongs.

| Scope type | Good fit |
|------------|----------|
| `project` | Repository or implementation workflow |
| `domain` | Support, research, security, revenue, or operations workflow |
| `workspace` | A company workspace or business unit |
| `session` | One isolated run |
| `user` | Personal assistant context |
| `custom` | Customer-defined boundary |

For customer deployments, the import call is the integration point: an adapter
can export each run's trace and call `lerim trace import` automatically.
