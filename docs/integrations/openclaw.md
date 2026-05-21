# OpenClaw

OpenClaw is currently MCP-first in Lerim, with native plugin support planned.

| Field | Status |
| --- | --- |
| Native trace ingestion | Planned plugin, not shipped |
| MCP config support | Yes |
| Config command | `lerim connect openclaw --mode mcp` |
| MCP config path | `~/.openclaw/openclaw.json` |

## What Works Today

The MCP config writer adds Lerim to OpenClaw's MCP config:

```bash
lerim connect openclaw --mode mcp --dry-run
lerim connect openclaw --mode mcp
```

Lerim writes OpenClaw's documented nested registry shape:

```json
{
  "mcp": {
    "servers": {
      "lerim": {
        "command": "/absolute/path/to/python",
        "args": ["-m", "lerim.mcp_server"]
      }
    }
  }
}
```

This config registers Lerim's MCP tool surface for OpenClaw. Treat live tool
use as unverified until an installed-client acceptance artifact exists.

## How OpenClaw Context Enters Lerim

`lerim connect openclaw --mode mcp` does not make Lerim read OpenClaw's local
traces. It only gives OpenClaw access to Lerim tools.

Today there are two ingestion paths for OpenClaw work:

1. If OpenClaw can call MCP tools at the end of a run, call
   `lerim_trace_submit` with the completed transcript and explicit
   `source_name`, `source_profile`, `scope_type`, and `scope` fields. Lerim
   stores the submitted payload, normalizes it, runs the normal trace-ingestion
   compiler, and writes durable records to the context store.
2. If OpenClaw exports local traces, run a user-owned cleaner that converts
   those raw traces into Lerim canonical JSONL, register that clean folder with
   `lerim project add <folder> --type custom --source-profile <profile>`, then
   run `lerim ingest --agent custom`.

Do not point a custom project at raw OpenClaw logs unless they are already in
Lerim canonical JSONL. Custom folders are strict clean-trace inputs, not
OpenClaw-specific adapters.

## Current Boundary

The native OpenClaw plugin is planned, not shipped. Do not treat MCP config support as native lifecycle capture.

`lerim connect openclaw --mode plugin` reports the pending plugin status and does not install MCP.

## Related

- [Integration matrix](matrix.md)
- [Integration Verification](verification.md)
- [Trace Sources, MCP Clients, And Workflow Adapters](../concepts/supported-agents.md)
- [lerim connect](../cli/connect.md)
- [OpenClaw MCP docs](https://docs.openclaw.ai/cli/mcp)
