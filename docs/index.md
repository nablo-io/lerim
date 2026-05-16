# Lerim

Lerim turns completed AI-agent work into reusable company context for future agents.

## Summary

Lerim sits above agent workflows, extracts durable signal from their traces, and gives future agents compact context they can query before they start work.

The product direction is a context compiler for support, research, operations,
revenue, custom business agents, and engineering automation. Current adapters
are one compatibility path; custom clean-trace folders are the path for other
agents and business workflows.

If you are evaluating Lerim, start with the workflow: traces in, durable context
out, cited answers and startup context for future agents.

The operating model is simple:

- capture traces from supported agent work
- filter noisy execution history into durable signal
- curate overlap so context stays compact
- answer questions and compile startup context for future agents

## Main flows

- `ingest` extracts durable records from supported traces
- `curate` merges, links, and archives low-value records so memory stays selective
- `answer` retrieves records and answers a question

## Common workflows

- Research teams preserving source trails, assumptions, evidence strength, and analyst handoffs.
- Support teams preserving escalation reasons, customer constraints, known fixes, and policy references.
- Operations teams preserving incident timelines, inventory exceptions, unresolved risks, and runbook lessons.
- Security and IT teams preserving investigation evidence, access-review rationale, policy exceptions, and remediation notes.
- Revenue and customer teams preserving account context, positioning decisions, approvals, and follow-up commitments.
- Engineering teams preserving architecture decisions, release constraints, and operational lessons.

## Start here

- [Installation](installation.md)
- [Quickstart](quickstart.md)
- [Business Workflows](concepts/business-workflows.md)
- [Custom Trace Folders](guides/custom-trace-folders.md)
- [How It Works](concepts/how-it-works.md)
- [Context Brief](concepts/context-brief.md)
- [CLI Overview](cli/overview.md)
