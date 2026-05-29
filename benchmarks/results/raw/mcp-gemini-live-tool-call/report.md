# Lerim MCP Integration Benchmark

- Generated: `2026-05-20T09:14:55.412091+00:00`
- Command: `benchmarks/lerim_evidence/integration.py --include-installed-client-probes --installed-client-targets gemini-cli --include-tool-call-probes --tool-call-targets gemini-cli --allow-live-client-tool-calls --tool-call-timeout-seconds 120 --max-tool-call-budget-usd 0.25 --output-dir benchmarks/results/raw/mcp-gemini-live-tool-call`
- Mode: `local-integration`
- Overall status: `pass`
- Known MCP targets: `15`
- Config targets checked: `15`
- Config probe pass/fail: `15` / `0`
- Stdio tools-list pass/fail: `1` / `0`
- Stdio context tool-call pass/fail: `1` / `0`
- Local context tool-call acceptances: `1`
- Stdio trace-submit pass/fail: `1` / `0`
- Trace-submit idempotency acceptances: `1`
- Trace-submit extraction acceptances: `0`
- Real config validation probes: `0`
- Real config validation statuses: `{}`
- Installed client probes: `1`
- Installed client statuses: `{'pass': 1}`
- Installed client connection acceptances: `1`
- Tool-call probes: `1`
- Tool-call statuses: `{'pass': 1}`
- Installed-client context tool-call validation: `1`
- Installed-client context tool-call acceptances: `1`
- Blockers: `0`

## Acceptance Boundary

- Temporary config fixtures exercise Lerim writer and validation code paths but do not prove an agent is installed or can launch Lerim.
- The stdio tools-list probe starts Lerim's MCP server directly and lists tools; it does not prove every external MCP client can launch the command.
- The stdio context tool-call probe calls lerim_context_brief through the MCP protocol and proves Lerim's local tool path; it does not prove an external client selected the tool.
- The default stdio trace-submit probe calls lerim_trace_submit through the MCP protocol on an idempotent duplicate trace; it proves submission and normalization plumbing but not LLM extraction quality.
- The opt-in stdio trace-submit extraction probe calls the same MCP tool on a synthetic submitted trace and requires DSPy extraction to create one episode record plus at least one durable record.
- The opt-in stdio trace-submit extraction probe uses a synthetic submitted trace fixture; the MCP submission and DSPy extraction path are real, but this is not organic client-session evidence.
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

- Status: `pass`
- Command: `<python-executable> -m lerim.mcp_server`
- Tool: `lerim_context_brief`
- Project: `<configured benchmark project>`
- Availability: `missing`
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

## Installed Client MCP CLI Probe Summary

- Probe count: `1`
- Status counts: `{'pass': 1}`
- Connection acceptances: `1`
- Per-client local inventory is omitted from the public Markdown report.

## Installed Client Tool-Call Probe Summary

- Probe count: `1`
- Status counts: `{'pass': 1}`
- Context tool-call acceptances: `1`
- Observed Lerim tools: `lerim_context_brief`
- Per-client local inventory is omitted from the public Markdown report.
