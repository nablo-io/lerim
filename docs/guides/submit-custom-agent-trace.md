# Submit A Custom Agent Trace

Use this guide when an agent does not have a native Lerim adapter yet.

There are two supported paths:

- `lerim trace import` for one explicit file.
- `lerim_trace_submit` through MCP for agents that can call Lerim tools.

For ongoing folders of already-clean JSONL traces, use
[Custom Trace Folders](custom-trace-folders.md).

## Before You Submit

Clean and redact the trace before it enters Lerim:

- remove secrets, tokens, cookies, private keys, and passwords
- remove regulated personal data your retention policy forbids
- drop binary blobs, screenshots, huge raw payloads, and duplicate logs
- preserve decisions, constraints, evidence, source links, ticket ids, incident
  ids, workflow ids, and final outcomes

Lerim extraction is selective, but it is not a privacy firewall. Source owners
still own export, cleaning, redaction, and retention.

## Defense-In-Depth Redaction

For a quick first pass, use the bundled redaction helper to strip common secrets
and PII from a trace before import. It is pure regex, makes no network calls, and
handles JSONL, JSON, and plain text.

```bash
python scripts/redact_trace.py ./raw-support-agent-run.jsonl -o ./clean-support-run.jsonl
```

It catches obvious high-risk patterns — API keys (`sk-...`, `AKIA...`, Stripe,
GitHub, Slack tokens), bearer tokens, password assignments, private key blocks,
emails, credit card numbers, SSNs, and IBANs — and reports a count per category.

This is **defense-in-depth, not a compliance boundary**. A clean redaction report
does not mean the trace is safe for regulated data. Review the output before
import, and keep your customer-owned cleaning and retention process for anything
regulated or customer-specific. Lerim extraction is selective, but it is not a
privacy firewall.

## Accepted Trace Shapes

### JSONL

Each line can be a message-like object:

```json
{"role":"user","content":"Customer asked whether renewal needs legal approval.","timestamp":"2026-05-16T09:00:00Z"}
{"role":"assistant","content":"Agent checked policy and found approval is required above EUR 500.","timestamp":"2026-05-16T09:02:00Z"}
```

### JSON Array

```json
[
  {
    "role": "user",
    "content": "Investigate failed deploy for checkout service.",
    "timestamp": "2026-05-16T10:00:00Z"
  },
  {
    "role": "assistant",
    "content": "Found the rollback failed because the previous image tag was pruned.",
    "timestamp": "2026-05-16T10:08:00Z"
  }
]
```

### JSON Wrapper

The wrapper may use `messages`, `events`, `trace`, `steps`, or `items`.

```json
{
  "session_id": "incident-2026-05-16-checkout",
  "messages": [
    {
      "role": "user",
      "content": "Investigate failed deploy for checkout service."
    },
    {
      "role": "assistant",
      "content": "Rollback was blocked because the previous image tag was pruned."
    }
  ],
  "metadata": {
    "incident_id": "INC-2042",
    "source": "incident-agent"
  }
}
```

If a wrapper includes `session_id`, `sessionId`, `id`, `run_id`, or `runId`,
Lerim uses it as the default session id unless the caller overrides it.

### Plain Text

Plain text is accepted for pilots. Use structured JSON or JSONL for anything
that needs timestamps, source links, workflow ids, or reliable provenance.

## Import One File From The CLI

```bash
lerim trace import ./support-agent-run.jsonl \
  --source-name support-agent \
  --source-profile support \
  --scope-type domain \
  --scope support-ops
```

`--source-profile` can be a bundled profile or a custom YAML profile registered
with `lerim profile register`. See
[Customize Lerim For Your Use Case](custom-source-profiles.md).

Use `--force` only when you intentionally want to re-extract identical content.
Without `--force`, identical normalized session content is skipped after the
first successful import.

## Submit Through MCP

Agents with MCP access can call `lerim_trace_submit`.

Example tool payload:

```json
{
  "trace_text": "{\"session_id\":\"support-2026-05-16-renewal\",\"messages\":[{\"role\":\"user\",\"content\":\"Customer asked whether renewal needs legal approval.\"},{\"role\":\"assistant\",\"content\":\"Approval is required above EUR 500; opened legal review task.\"}]}",
  "source_name": "support-agent",
  "source_profile": "support",
  "scope_type": "domain",
  "scope": "support-ops",
  "scope_label": "Support Ops",
  "session_id": "support-2026-05-16-renewal"
}
```

The MCP server persists the submitted trace, normalizes it, registers the scope,
and routes it through Lerim's normal extraction path.

If extraction fails after the payload is saved, the tool response includes a
`retry_command`. Lerim also writes a `.lerim-submission.json` manifest next to
the submitted trace so the same source/profile/scope/session metadata can be
retried later:

```bash
lerim trace submissions --status failed
lerim trace retry <submitted_trace_path>
```

This retry path reruns Lerim's normal importer and DSPy extraction. It
does not create a separate memory-save path.

## Choose A Scope

| Scope type | Use it for |
| --- | --- |
| `project` | Repository and coding-agent work. |
| `domain` | Support, incidents, research, sales, or operations workflows. |
| `workspace` | A company workspace or business unit. |
| `session` | One isolated run. |
| `user` | Personal assistant context. |
| `custom` | Customer-defined boundary. |

## Verify After Submission

```bash
lerim ingest --agent custom --no-extract
lerim queue --status pending
lerim status --live
lerim answer "What was learned from the last support run?"
```

For MCP clients, also run:

```bash
lerim connect doctor <agent>
```

The doctor command is a read-only config check: it parses the target config and
checks whether a Lerim MCP entry exists. A doctor pass is not the same as
server reachability or proof that the installed client called
`lerim_context_brief`; that requires a live client tool-call probe.
