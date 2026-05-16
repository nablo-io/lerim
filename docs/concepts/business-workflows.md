# Business Workflows

Lerim is useful when a team runs repeated AI workflows and keeps losing the
context between runs.

The pattern is:

1. an agent completes work inside a business process
2. the trace contains evidence, decisions, constraints, open questions, and handoffs
3. Lerim extracts the reusable signal
4. the next agent starts with compact, cited context instead of a raw transcript

## Research and market intelligence

Research teams can preserve source trails, evidence strength, assumptions,
rejected leads, client-specific brief constraints, and analyst handoffs across
agent-assisted research cycles.

Example question:

```bash
lerim answer "What sources supported our last competitor-pricing assumption?"
```

## Support operations

Support teams can preserve triage decisions, escalation evidence, policy
references, known fixes, product behavior, customer constraints, and next steps.

Example question:

```bash
lerim answer "What do we already know about this customer escalation pattern?"
```

## Operations and incidents

Operations teams can preserve incident timelines, owner decisions, inventory
exceptions, supplier or carrier constraints, unresolved risks, and runbook
lessons.

Example question:

```bash
lerim answer "What risks were still open after the last carrier-delay incident?"
```

## Security and IT

Security and IT teams can carry forward investigation timelines, access-review
rationale, policy exceptions, remediation evidence, and internal helpdesk
handoffs.

Example question:

```bash
lerim answer "What evidence supports the latest access-review exception?"
```

## Revenue and customer workflows

Revenue and customer teams can reuse account context, positioning decisions,
campaign constraints, legal approvals, and follow-up commitments.

Example question:

```bash
lerim answer "What account constraints should the renewal agent know before outreach?"
```

## Engineering automation

Engineering teams can retain architecture decisions, failed tests, repo
conventions, release lessons, and operational constraints.

Example question:

```bash
lerim answer "What release constraints did previous agents discover?"
```

## Current source boundary

The open-source package includes the trace-to-context foundation and supported
source adapters. Customer deployments can adapt the input layer around the
business traces that matter for a pilot workflow.

For custom agents today, the practical path is `lerim trace import`: export a
JSON, JSONL, or text trace from the agent run, choose a scope such as `domain`,
`workspace`, `project`, or `custom`, and let Lerim normalize and ingest it.
Customer adapters can automate that import step once the pilot workflow is
clear.

If the source trace contains customer-specific noise or sensitive fields, run a
customer-owned cleaner before import. Lerim filters for durable business signal,
but pre-import cleaning is still the right boundary for secrets, regulated data,
large raw tool outputs, and retention policy.
