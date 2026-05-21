# MCP Quickstart

Lerim can act as a shared context layer for any agent client that supports MCP.

MCP is the access layer. Lerim's extraction still happens inside Lerim's normal trace-to-context compiler.

## 1. Prepare Lerim

```bash
pip install lerim
lerim init
lerim connect auto
lerim project add .
lerim up
```

## 2. Install Lerim Into An MCP Client

Use `--dry-run` first:

```bash
lerim connect gemini-cli --mode mcp --dry-run
```

Then write the real config:

```bash
lerim connect gemini-cli --mode mcp
```

Every write verifies the resulting config. If the file already existed, Lerim creates a timestamped backup next to it.

## 3. Generic MCP Config

For any MCP client that accepts a standard `mcpServers` block:

```json
{
  "mcpServers": {
    "lerim": {
      "command": "/absolute/path/to/python",
      "args": ["-m", "lerim.mcp_server"]
    }
  }
}
```

Prefer `lerim connect <agent> --mode mcp` over hand editing. Lerim writes the
absolute Python command that can import `lerim.mcp_server`, which avoids MCP
startup failures in clients that do not inherit your shell `PATH`.

For OpenCode, Lerim writes the client-specific top-level `mcp` shape. For Codex, Lerim writes the TOML `mcp_servers` shape. For Hermes and Goose, Lerim writes YAML `mcp_servers`.

## 4. Available Tools

| Tool | Purpose |
| --- | --- |
| `lerim_context_brief` | Return generated startup context for a project. |
| `lerim_context_answer` | Ask a grounded question over stored context records. |
| `lerim_context_search` | Retrieve compact context records for a query. |
| `lerim_records_list` | Deterministically list context records with filters. |
| `lerim_trace_submit` | Submit a completed session transcript for normal Lerim extraction. |
| `lerim_ingest_status` | Inspect runtime, queue, and ingest health. |

Lerim does not expose broad `memory_save` as the primary interface. Completed sessions should go through `lerim_trace_submit`, then Lerim decides which evidence-backed context records are worth keeping.

MCP does not automatically import the client history. It gives the client tools:
read tools retrieve already-extracted context, and `lerim_trace_submit` imports
one completed session when the client or harness explicitly calls it.

Pass an explicit scope when submitting traces. For repo work:

```json
{
  "source_name": "openclaw",
  "source_profile": "coding",
  "scope_type": "project",
  "scope": "/absolute/path/to/repo"
}
```

For support, research, or operations workflows, prefer a domain or workspace
scope:

```json
{
  "source_name": "support-agent",
  "source_profile": "support",
  "scope_type": "domain",
  "scope": "support-ops"
}
```

If a submitted trace is saved but extraction fails, inspect and retry it from
the CLI:

```bash
lerim trace submissions --status failed
lerim trace retry <submitted_trace_path>
```

## 5. Verify

```bash
lerim connect list --all
lerim connect doctor gemini-cli
```

You can also launch the server directly:

```bash
lerim mcp
```

In normal use, the MCP client starts the configured Python module command for
you.
