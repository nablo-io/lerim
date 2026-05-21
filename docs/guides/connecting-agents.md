# Connecting Trace Sources

Lerim reads trace data from supported sources and turns it into reusable context.

There are two connection paths:

- Native adapters read completed local sessions and feed Lerim's compiler.
- MCP client config adds Lerim's context and trace-submission tools to
  compatible clients. A specific client is only claimed as live-tool-call
  accepted when the integration matrix lists installed-client evidence for it.

## Native Auto-detect

```bash
lerim connect auto
```

## Native Manual Connect

```bash
lerim connect claude
lerim connect codex
lerim connect cursor
lerim connect opencode
lerim connect pi
```

## Custom path

```bash
lerim connect claude --path /custom/path
```

## MCP Clients

Install Lerim into MCP-compatible agent clients:

```bash
lerim connect auto --mode mcp --dry-run
lerim connect auto --mode mcp
```

Or connect one target:

```bash
lerim connect codex --mode mcp
lerim connect claude-code --mode mcp
lerim connect cursor --mode mcp
lerim connect opencode --mode mcp
lerim connect gemini-cli --mode mcp
lerim connect cline --mode mcp
lerim connect cline-cli --mode mcp
lerim connect claude-desktop --mode mcp
lerim connect openclaw --mode mcp
lerim connect hermes --mode mcp
lerim connect goose --mode mcp
lerim connect roo-code --mode mcp
lerim connect kilo-code --mode mcp
lerim connect windsurf --mode mcp
lerim connect openhuman --mode mcp
```

Every real write creates a timestamped backup when the target config already
exists.

MCP config is the retrieval and explicit-submit path. By itself, it does not
make Lerim scan that agent's local history. A connected MCP client can read
stored context with `lerim_context_brief`, `lerim_context_search`,
`lerim_context_answer`, or `lerim_records_list`. It can add a completed run to
Lerim only if it calls `lerim_trace_submit` with the completed transcript and
scope metadata.

## Check Connections

```bash
lerim connect list
lerim connect list --all
lerim connect doctor codex
```

## Custom agents

Custom agents can use MCP if they are MCP clients. If they export completed
workflow traces instead, clean those traces into Lerim canonical JSONL, then
register the clean folder as a custom project:

```bash
lerim project add ~/lerim-traces/support-clean \
  --type custom \
  --source-profile support
lerim ingest --agent custom
```

Add `--source-profile <id>` when that folder should extract through a bundled
or custom profile. See [Custom Trace Folders](custom-trace-folders.md) and
[Customize Lerim For Your Use Case](custom-source-profiles.md).

Do not register raw vendor logs directly unless they already match Lerim's
canonical JSONL schema. The cleaner/exporter is the adapter for unsupported
agents, and it owns redaction, structure mapping, and retention before Lerim
extracts durable context.
