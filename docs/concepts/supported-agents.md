# Trace Sources, MCP Clients, And Workflow Adapters

Lerim supports agents in two different ways:

1. **Native trace adapters** read completed local sessions and feed Lerim's trace-to-context compiler.
2. **MCP client support** lets agents query Lerim context and submit completed sessions through `lerim_trace_submit`.

Do not treat these as identical. Native adapters are the strongest ingestion path when an agent has a stable local session store. MCP is the universal access layer for recall and broad compatibility.

## Support Matrix

| Agent | Native trace ingestion | MCP config support | Config command | Notes |
| --- | --- | --- | --- | --- |
| Claude Code | Native adapter implemented | MCP config writer | `lerim connect claude-code --mode mcp` | Native adapter reads completed Claude project sessions. |
| Codex CLI | Native adapter implemented | MCP config writer | `lerim connect codex --mode mcp` | Native adapter reads Codex JSONL sessions and prefers visible event messages. |
| Cursor | Native DB adapter implemented | MCP config writer | `lerim connect cursor --mode mcp` | Native adapter reads Cursor's local storage and exports compact traces; local DB format can change. |
| OpenCode | Native adapter implemented | MCP config writer | `lerim connect opencode --mode mcp` | Native adapter reads `opencode.db`; MCP uses OpenCode's top-level `mcp` config. |
| Gemini CLI | Not implemented | MCP config writer, live tool-call verified | `lerim connect gemini-cli --mode mcp` | MCP recall is live-tool-call verified; completed-session capture still needs stable export. |
| Cline VS Code | Not implemented | MCP config writer | `lerim connect cline --mode mcp` | MCP-first for VS Code agent workflows. |
| Cline CLI | Not implemented | MCP config writer | `lerim connect cline-cli --mode mcp` | MCP-first for terminal Cline workflows. |
| Claude Desktop | Not implemented | MCP config writer | `lerim connect claude-desktop --mode mcp` | Desktop recall and context answering. |
| OpenClaw | Plugin planned | MCP config writer | `lerim connect openclaw --mode mcp` | MCP first; native plugin should add lifecycle capture later. |
| Hermes | Plugin planned | MCP config writer | `lerim connect hermes --mode mcp` | MCP first; provider plugin should submit completed sessions later. |
| pi | Native adapter implemented | No current MCP claim | `lerim connect pi` | Native adapter reads completed pi JSONL sessions from `~/.pi/agent/sessions/`; extension hooks remain planned. |
| Goose | Not implemented | MCP config writer | `lerim connect goose --mode mcp` | MCP-first. |
| Roo Code | Not implemented | MCP config writer | `lerim connect roo-code --mode mcp` | MCP-first. |
| Kilo Code | Not implemented | MCP config writer | `lerim connect kilo-code --mode mcp` | MCP-first. |
| Windsurf | Not implemented | MCP config writer | `lerim connect windsurf --mode mcp` | MCP-first. |
| OpenHuman | Investigating | Experimental generic MCP config writer | `lerim connect openhuman --mode mcp` | Do not overclaim native support until OpenHuman's memory trait path is implemented and client-loading evidence exists. |
| Custom trace folder | User-owned clean trace import | No | `lerim project add <path> --type custom` | Watches user-owned folders of already-clean canonical JSONL traces. |
| Generic trace import / MCP submit | N/A; explicit trace import/MCP submission | Yes, through trace submit | `lerim trace import ...` or `lerim_trace_submit` | Best path for business agents and internal workflows that export JSONL, JSON arrays, JSON wrappers, or plain text transcripts. |

## Native Adapter Job

Each native adapter finds session traces and normalizes them into Lerim's internal trace shape.

The adapter does not write durable context itself. It only feeds the DSPy extraction pipeline.

Current native adapters:

- Claude Code
- Codex CLI
- Cursor
- OpenCode
- pi

## MCP Tool Surface

`lerim mcp` exposes these tools:

- `lerim_context_brief`
- `lerim_context_answer`
- `lerim_context_search`
- `lerim_records_list`
- `lerim_trace_submit`
- `lerim_ingest_status`

The MCP server intentionally avoids a broad `memory_save` primitive. Completed sessions should be submitted through `lerim_trace_submit`, then Lerim decides what should become durable context.

## Custom And Business-Agent Traces

For a custom agent, create a folder of already-clean Lerim canonical JSONL files and register it as a custom project:

```bash
lerim project add ~/lerim-traces/support-clean --type custom
lerim ingest --agent custom
```

Each `.jsonl` file is one completed agent or workflow session. Custom mode does not run a Lerim adapter and does not compact or normalize files. It reads the clean files directly and indexes them as `agent_type=custom`.

For explicit one-file imports, use:

```bash
lerim trace import ./support-agent-run.jsonl \
  --source-name support-agent \
  --source-profile support \
  --scope-type domain \
  --scope support-ops
```

For MCP clients without a stable local trace store, use `lerim_trace_submit` with source metadata. Lerim will persist the submitted trace, normalize it, and run normal extraction.

These are separate flows:

- Ingestion flow: native adapter, custom clean folder, `lerim trace import`, or
  MCP `lerim_trace_submit` feeds completed sessions into Lerim's compiler.
- Retrieval flow: MCP read tools let the same or another agent retrieve the
  context that is already in the global store.

For OpenClaw and other MCP-first agents without native adapters, `lerim connect
<agent> --mode mcp` only sets up retrieval and explicit submission. Automatic
completed-session capture requires a shipped native plugin, lifecycle hook, or
stable exporter; it is not implied by MCP config support.

## Scope Model

Sessions are matched to registered projects by path. When a session belongs to a registered project, Lerim writes records for that `project_id` into the global context database.

Business workflows can also use explicit scopes such as `domain:support-ops`, `workspace:customer-success`, or `custom:<id>` when importing traces.
