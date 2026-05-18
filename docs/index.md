# Lerim

Lerim turns completed agent traces into reusable operating context.

It filters noisy execution history into evidence-backed context records:
decisions, preferences, constraints, facts, references, and compact episode
history.

## Summary

Lerim sits after trace systems and before future agents. Observability shows
what happened; Lerim decides what was worth learning from it.

The focused wedge is coding agents plus two non-coding profiles: support
operations and operations/incidents. Other workflows can be added later as
signal packs, not separate pipelines.

If you are evaluating Lerim, start with the workflow: traces in, durable context
out, cited answers and startup context for future agents.

The operating model is simple:

- capture traces from supported agent work
- filter noisy execution history into durable signal
- curate overlap so context stays compact
- link related context into a navigable graph
- answer questions and compile startup context for future agents

## Main phases

- `ingest` extracts durable records from supported traces
- `curate` merges and archives low-value records so memory stays selective
- `context_graph` links curated records into a sparse context graph during curate cycles
- `answer` retrieves records and answers a question

## Focused workflows

- Coding agents preserve repo conventions, architecture decisions, setup facts, failed commands, test lessons, and release handoffs.
- Support operations preserve customer constraints, known fixes, failed fixes, escalation reasons, policy-backed facts, and handoffs.
- Operations and incidents preserve root causes, mitigations, rejected hypotheses, runbook gaps, incident handoffs, and follow-up risks.

## Start here

- [Installation](installation.md)
- [Quickstart](quickstart.md)
- [Business Workflows](concepts/business-workflows.md)
- [Signal Packs](concepts/signal-packs.md)
- [Context Records](concepts/context-records.md)
- [Custom Trace Folders](guides/custom-trace-folders.md)
- [How It Works](concepts/how-it-works.md)
- [Context Brief](concepts/context-brief.md)
- [CLI Overview](cli/overview.md)
