# Integration Verification

Use this page when deciding whether an agent integration is truly ready to
claim publicly.

## Evidence Levels

| Evidence | Counts as | Does not count as |
| --- | --- | --- |
| `lerim connect <agent> --mode mcp --dry-run` | Safe preview of the config write. | Installed-client support. |
| `lerim connect <agent> --mode mcp` | Config writer can mutate the target file and create a backup. | A live agent tool call. |
| `lerim connect doctor <agent>` | Lerim can parse the config and see the MCP entry. | Proof that the client loaded or used the tool. |
| Local stdio MCP tool-call probe | Lerim's MCP server can execute `lerim_context_brief` through the MCP protocol. | Proof that an external client selected the tool. |
| Local stdio trace-submit duplicate probe | Lerim's MCP server can accept, normalize, and idempotently skip a duplicate submitted trace. | Proof that LLM extraction quality passed. |
| Opt-in local stdio trace-submit extraction probe | When accepted, a synthetic submitted trace ran through the DSPy extraction path and created records. | Proof that an external installed client submitted an organic trace; the current public MCP artifact has 0 extraction acceptances. |
| Installed client CLI list/get command | The installed client can see the Lerim MCP entry. | Proof that `lerim_context_brief` was called. |
| Live tool-call probe | The client called a Lerim MCP context tool. | Native completed-session capture. |
| Native adapter ingest over a completed session | Native capture for that source shape. | MCP recall support. |

## Standard Lerim Checks

Run these for every target:

```bash
lerim connect <agent> --mode mcp --dry-run
lerim connect <agent> --mode mcp
lerim connect doctor <agent>
```

`doctor` should report:

- config exists
- configured is true
- no parse error

If the target config already existed, `lerim connect` should create a timestamped
backup next to it.

## Installed Client Checks

The benchmark runner also calls `lerim_context_brief` directly through Lerim's
local stdio MCP server by default. That is a useful server-path check, but keep
it separate from installed-client acceptance.

These commands are currently useful where the client exposes an MCP CLI:

| Client | Command | Strongest current signal |
| --- | --- | --- |
| Codex CLI | `codex mcp get lerim` | Config visible; no connected marker exposed by this probe. |
| Claude Code | `claude mcp get lerim` | `Status: Connected`. |
| Gemini CLI | `gemini mcp list` | `lerim` listed as connected. |
| OpenCode | `opencode mcp list` | `lerim connected`. |

For clients without a CLI list/get command, use config doctor evidence and mark
the row as config-writer tested until an installed-client probe exists.
Public benchmark artifacts keep aggregate installed-client counts but omit
per-machine client inventory from `details.jsonl`.

## Live Tool-Call Probes

Live probes can spend model or subscription budget, so keep them opt-in. The
current benchmark runner has an explicit gate:

```bash
uv run python benchmarks/lerim_evidence/integration.py \
  --include-real-doctor \
  --include-installed-client-probes \
  --include-tool-call-probes \
  --allow-live-client-tool-calls \
  --tool-call-targets gemini-cli \
  --output-dir <private-output-dir>
```

The live tool-call acceptance target is simple: the installed client should call
`lerim_context_brief` and return the probe marker.
Use `--tool-call-targets` to rerun only the client whose auth, quota, or config
changed.

Current public live tool-call evidence:

| Client | Artifact | Result |
| --- | --- | --- |
| Gemini CLI | `benchmarks/results/raw/mcp-gemini-live-tool-call/report.json` | Connected and called `lerim_context_brief` through the installed client. |

See [MCP Integration](../benchmarks/lerim-results.md#mcp-integration) for the
current public evidence boundary.

Do not turn a skipped live probe into a support claim.

## Native Capture Checks

For Claude Code, Codex CLI, Cursor, OpenCode, and pi, native adapter support should
be verified with completed local sessions:

```bash
lerim ingest --agent <agent> --no-extract
lerim queue --status pending
lerim ingest --agent <agent>
lerim context records --profile coding
```

Fixtures and unit tests are useful, but they do not prove that the current local
client version still stores sessions in the expected shape.

## Troubleshooting

- If `doctor` reports a parse error, inspect the target config file before
  running another write.
- If the client cannot start the MCP server, rerun
  `lerim connect <agent> --mode mcp` so Lerim writes the absolute Python
  executable plus `-m lerim.mcp_server`. For manual configs, set `command` to an
  absolute Python path and `args` to `["-m", "lerim.mcp_server"]`; do not point
  the client `command` at the `lerim` executable.
- If a VS Code agent cannot find its config, confirm the active VS Code profile
  and global storage path.
- If an installed-client probe passes but live tool calls fail, treat support as
  config-visible only.
- If an MCP-first agent has no stable session export, mark it recall-only unless
  the agent explicitly calls `lerim_trace_submit`.
