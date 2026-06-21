# Examples

These examples show the supported workflow shapes without turning config support
into a capture claim.

| Workflow | Use when | Evidence boundary |
| --- | --- | --- |
| Native coding-agent ingestion | The agent has a stable local trace store Lerim already reads | Completed sessions can feed Lerim's compiler |
| MCP context recall | The agent can load an MCP server config | The agent can query Lerim context after the client loads the config |
| Generic trace submission | A custom agent can export completed sessions | The exporter owns capture, cleaning, redaction, and retention |
| Support-agent trace import | Support workflows produce clean completed transcripts | Use a support source profile and domain scope |
| Incident-agent trace import | Incident workflows produce clean completed transcripts | Use an ops source profile and incident/domain scope |

## Native Coding-Agent Ingestion

Native adapters are the best path when the agent has a stable local session
store.

```bash
lerim init
lerim connect claude
lerim connect codex
lerim connect cursor
lerim connect opencode
lerim connect pi
lerim project add .
lerim ingest
lerim context-brief show
```

Current native adapters:

- Claude Code
- Codex CLI
- Cursor
- OpenCode
- pi

See the [integration matrix](../integrations/matrix.md) for the current support
boundary.

## MCP Context Recall

MCP is the universal recall layer. It lets compatible clients ask Lerim for
context, but it is not the same as native completed-session capture.

```bash
lerim connect gemini-cli --mode mcp --dry-run
lerim connect gemini-cli --mode mcp
```

The same pattern works for the known MCP targets:

```bash
lerim connect codex --mode mcp
lerim connect claude-code --mode mcp
lerim connect cursor --mode mcp
lerim connect opencode --mode mcp
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

Then validate the local config:

```bash
lerim connect doctor gemini-cli
```

## Generic JSONL Trace Submission

Use this when another agent or application can export a completed session.

```json
{"type":"user","message":{"role":"user","content":"Customer asked whether refund approval is required."},"timestamp":"2026-05-19T09:00:00Z"}
{"type":"assistant","message":{"role":"assistant","content":"Agent found refunds above EUR 500 require manager approval."},"timestamp":"2026-05-19T09:02:00Z"}
```

Import the trace:

```bash
lerim trace import ./support-refund-session.jsonl \
  --source-name support-agent \
  --source-profile support \
  --scope-type domain \
  --scope support-ops
```

Or submit through MCP with `lerim_trace_submit` from a compatible client.

## Support-Agent Trace Import

Support traces should preserve policy evidence, customer constraints, failed
fixes, escalation reasons, and handoff state.

```bash
lerim trace import ./traces/support/*.jsonl \
  --source-name support-agent \
  --source-profile support \
  --scope-type domain \
  --scope support-ops

lerim context records --profile support
lerim answer "What refund approval constraints should the next support agent know?"
```

See the [Support Ops Demo](../guides/support-ops-demo.md) for a realistic
before/after with real extracted records.

## Incident-Agent Trace Import

Incident traces should preserve root causes, mitigations, rejected hypotheses,
runbook gaps, owners, and follow-up risks.

```bash
lerim trace import ./traces/incidents/*.jsonl \
  --source-name incident-agent \
  --source-profile ops \
  --scope-type domain \
  --scope incident-ops

lerim context records --profile ops
lerim answer "What did we learn from the last incident handoff?"
```

See the [Incident Ops Demo](../guides/incident-ops-demo.md) for a realistic
before/after with real extracted records.

## Research-Agent Trace Import

Research traces should preserve source-quality rules, cited conclusions, rejected
leads, and analyst assumptions.

```bash
lerim trace import ./traces/research/*.jsonl \
  --source-name research-agent \
  --source-profile research \
  --scope-type domain \
  --scope research

lerim context records --profile research
lerim answer "What source-quality rules should the next research run follow?"
```

See the [Research Demo](../guides/research-demo.md) for a realistic before/after
with real extracted records.

## Compliance-Agent Trace Import

Compliance traces should preserve policy boundaries, approval gates, rejected
interpretations with reasoning, and regulatory citations.

```bash
lerim trace import ./traces/compliance/*.jsonl \
  --source-name compliance-agent \
  --source-profile compliance \
  --scope-type domain \
  --scope compliance

lerim context records --profile compliance
lerim answer "What approval gates apply to data-export feature changes?"
```

See the [Compliance Demo](../guides/compliance-demo.md) for a realistic
before/after with real extracted records.

## What Not To Claim

- MCP config support does not prove native completed-session capture.
- A temporary config fixture does not prove an installed client called a tool.
- Retrieval-only benchmark results are not extraction-quality results.
- A custom JSONL import is only as trustworthy as the upstream exporter and
  cleaner.
