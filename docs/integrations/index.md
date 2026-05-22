# Integrations

Lerim integrates with agents through two separate paths:

- **Native trace ingestion** reads completed local sessions and feeds Lerim's trace-to-context compiler.
- **MCP client support** writes a Lerim MCP server entry so an agent can load Lerim tools. Live recall or trace-submit acceptance is claimed only where the matrix lists installed-client/tool-call evidence.

These are not the same support level. MCP config support is useful, but config-only rows are not live recall acceptance and are not a claim that Lerim can capture that agent's completed sessions natively.

See the [integration matrix](matrix.md) for the public support boundary.
Use [integration verification](verification.md) before turning config support
into a public support claim.

Lerim is not limited to coding agents. Native adapters are strongest for local
agent traces, while custom trace import and MCP trace submission are the paths
for support, incident, research, operations, and other workflow agents.

## Native Adapters Today

- [Claude Code](claude-code.md)
- [Codex CLI](codex-cli.md)
- [Cursor](cursor.md)
- [OpenCode](opencode.md)
- [pi](pi.md)

## MCP Config Targets

- [Codex CLI](codex-cli.md)
- [Claude Code](claude-code.md)
- [Cursor](cursor.md)
- [OpenCode](opencode.md)
- [Gemini CLI](gemini-cli.md)
- [Cline](cline.md)
- Cline CLI, through `lerim connect cline-cli --mode mcp`
- [Claude Desktop](claude-desktop.md)
- [OpenClaw](openclaw.md)
- [Hermes](hermes.md)
- [Goose](goose.md)
- [Roo Code](roo-code.md)
- [Kilo Code](kilo-code.md)
- [Windsurf](windsurf.md)
- [OpenHuman](openhuman.md) experimental generic MCP config

These are MCP config surfaces unless their page lists installed-client or
tool-call evidence. They are not native completed-session capture adapters.

## Custom And Business Workflow Sources

- [Generic trace submission](../guides/submit-custom-agent-trace.md)
- [Custom trace folders](../guides/custom-trace-folders.md)
- [Support operations demo](../guides/support-ops-demo.md)
- [Incident operations demo](../guides/incident-ops-demo.md)

## Planned Plugin Or Extension Paths

- [OpenClaw](openclaw.md) native plugin
- [Hermes](hermes.md) provider plugin
- [pi extension](pi.md)
