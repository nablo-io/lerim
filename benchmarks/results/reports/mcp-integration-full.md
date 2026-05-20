# Lerim MCP Integration Benchmark

- Generated: `2026-05-20T09:00:41.436388+00:00`
- Command: `benchmarks/lerim_evidence/integration.py --include-real-doctor --include-installed-client-probes --include-stdio-trace-submit-extraction --stdio-extraction-timeout-seconds 300 --output-dir benchmarks/results/raw/mcp-integration-full`
- Mode: `local-integration`
- Overall status: `fail`
- Known MCP targets: `15`
- Config targets checked: `15`
- Config probe pass/fail: `15` / `0`
- Stdio tools-list pass/fail: `1` / `0`
- Stdio context tool-call pass/fail: `0` / `1`
- Local context tool-call acceptances: `0`
- Stdio trace-submit pass/fail: `1` / `1`
- Trace-submit idempotency acceptances: `1`
- Trace-submit extraction acceptances: `0`
- Real config validation probes: `15`
- Real config validation statuses: `{'skip': 15}`
- Installed client probes: `4`
- Installed client statuses: `{'pass': 4}`
- Installed client connection acceptances: `3`
- Tool-call probes: `0`
- Tool-call statuses: `{}`
- Installed-client context tool-call validation: `0`
- Installed-client context tool-call acceptances: `0`
- Blockers: `0`

## Acceptance Boundary

- Temporary config fixtures exercise Lerim writer and validation code paths but do not prove an agent is installed or can launch Lerim.
- The stdio tools-list probe starts Lerim's MCP server directly and lists tools; it does not prove every external MCP client can launch the command.
- The stdio context tool-call probe calls lerim_context_brief through the MCP protocol and proves Lerim's local tool path; it does not prove an external client selected the tool.
- The default stdio trace-submit probe calls lerim_trace_submit through the MCP protocol on an idempotent duplicate trace; it proves submission and normalization plumbing but not LLM extraction quality.
- The opt-in stdio trace-submit extraction probe calls the same MCP tool on a synthetic submitted trace and requires BAML/LangGraph extraction to create one episode record plus at least one durable record.
- The opt-in stdio trace-submit extraction probe uses a synthetic submitted trace fixture; the MCP submission and BAML/LangGraph extraction path are real, but this is not organic client-session evidence.
- Installed-client MCP CLI probes prove client config/connection visibility only; they do not prove context tool-call behavior unless a client actually calls lerim_context_brief.
- Public artifacts preserve aggregate installed-client counts and statuses but omit per-machine installed-client inventory from detail rows.
- Live client tool-call probes may spend model/subscription credits and are skipped unless explicitly enabled.
- Installed-agent context tool-call acceptance still needs an installed-client invocation of lerim_context_brief.

## Target Config Probes

| Target | Status | Format | Backup | Configured |
| --- | --- | --- | --- | --- |
| codex | `pass` | `toml_mcp_servers` | `yes` | `True` |
| claude-code | `pass` | `json_claude_code` | `yes` | `True` |
| cursor | `pass` | `json_mcp_servers` | `yes` | `True` |
| opencode | `pass` | `json_opencode` | `yes` | `True` |
| gemini-cli | `pass` | `json_mcp_servers` | `yes` | `True` |
| cline | `pass` | `json_mcp_servers` | `yes` | `True` |
| cline-cli | `pass` | `json_mcp_servers` | `yes` | `True` |
| claude-desktop | `pass` | `json_mcp_servers` | `yes` | `True` |
| openclaw | `pass` | `json_mcp_nested_servers` | `yes` | `True` |
| hermes | `pass` | `yaml_mcp_servers` | `yes` | `True` |
| goose | `pass` | `yaml_mcp_servers` | `yes` | `True` |
| roo-code | `pass` | `json_mcp_servers` | `yes` | `True` |
| kilo-code | `pass` | `json_mcp_servers` | `yes` | `True` |
| windsurf | `pass` | `json_mcp_servers` | `yes` | `True` |
| openhuman | `pass` | `json_mcp_servers` | `yes` | `True` |

## MCP Stdio Tools Probe

- Status: `pass`
- Command: `<python-executable> -m lerim.mcp_server`
- Tools: `lerim_context_answer, lerim_context_brief, lerim_context_search, lerim_ingest_status, lerim_records_list, lerim_trace_submit`
- Missing tools: `none`

## MCP Stdio Context Tool Call

- Status: `fail`
- Command: `<python-executable> -m lerim.mcp_server`
- Tool: `lerim_context_brief`
- Project: `<configured benchmark project>`
- Availability: `None`
- Content chars returned: `0`

## MCP Stdio Trace Submit

- Status: `pass`
- Probe: `stdio_mcp_trace_submit_duplicate`
- Command: `<python-executable> -m lerim.mcp_server`
- Tool: `lerim_trace_submit`
- Result status: `duplicate_skipped`
- Session id: `mcp-trace-submit-duplicate`
- Scope type: `domain`
- Records created: `None`
- Durable records: `None`
- Extraction acceptance: `False`
- Input trace: `not declared`

- Status: `fail`
- Probe: `stdio_mcp_trace_submit_extraction`
- Command: `<python-executable> -m lerim.mcp_server`
- Tool: `lerim_trace_submit`
- Result status: `ingested`
- Session id: `None`
- Scope type: `None`
- Records created: `0`
- Durable records: `0`
- Extraction acceptance: `False`
- Input trace: `synthetic_protocol_acceptance_trace`

## Installed Config Doctor Probe Summary

- Probe count: `15`
- Status counts: `{'skip': 15}`
- Per-client local inventory is omitted from the public Markdown report.

## Installed Client MCP CLI Probe Summary

- Probe count: `4`
- Status counts: `{'pass': 4}`
- Connection acceptances: `3`
- Per-client local inventory is omitted from the public Markdown report.

## Failures

- `stdio_mcp_context_brief_call` / `lerim-mcp-stdio`:
- `stdio_mcp_trace_submit_extraction` / `lerim-mcp-stdio`:
