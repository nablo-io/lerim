# lerim mcp

Run Lerim's stdio MCP server for compatible agent clients.

Most users do not start this command by hand. `lerim connect <agent> --mode mcp`
writes client config that launches it automatically.

## Usage

```bash
lerim mcp
```

Generic MCP client config:

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

`lerim connect` writes this using the Python executable that is running Lerim,
so external MCP clients do not need a `lerim` executable on their `PATH`.

## Tools

| Tool | Purpose |
| --- | --- |
| `lerim_context_brief` | Read or refresh startup context for a project. |
| `lerim_context_answer` | Ask a grounded question over stored context records. |
| `lerim_context_search` | Retrieve compact records for a query. |
| `lerim_records_list` | List records deterministically with filters. |
| `lerim_trace_submit` | Submit a completed session transcript for normal extraction. |
| `lerim_ingest_status` | Inspect runtime, queue, ingest, and project status. |

`lerim_trace_submit` accepts JSONL, JSON arrays, JSON objects with `messages`,
`events`, `trace`, `steps`, or `items`, and plain text transcripts. Wrapper
metadata can provide `session_id`, and identical normalized content is skipped
for the same session unless `force=true` is passed.

Failed submissions are retryable. The tool response includes
`submitted_trace_path`, `submission_manifest_path`, and `retry_command`; the
same saved metadata can be inspected with `lerim trace submissions` and retried
with `lerim trace retry <submitted_trace_path>`.

## Notes

MCP is the access layer. Lerim's durable context still lives in the global
SQLite context store, and extraction still runs through Lerim's normal
BAML/LangGraph compiler.

Think of MCP as two tool groups:

- Read tools: `lerim_context_brief`, `lerim_context_answer`,
  `lerim_context_search`, and `lerim_records_list` retrieve context that Lerim
  has already extracted.
- Submit tool: `lerim_trace_submit` is an explicit import path for one completed
  session. It is not automatic background capture.

When calling `lerim_trace_submit`, pass the scope explicitly. For repository
work, use `scope_type="project"` and `scope="/absolute/path/to/repo"`. For
business workflows, use a domain or workspace scope such as
`scope_type="domain"` and `scope="support-ops"`. Relying on the MCP server's
current working directory can put submitted context under the wrong scope in
clients that launch MCP servers from a generic directory.
