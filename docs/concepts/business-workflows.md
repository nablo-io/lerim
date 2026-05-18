# Business Workflows

Lerim is useful when a team runs repeated AI workflows and keeps losing the
operating context between runs.

The pattern is:

1. an agent completes work inside a business process
2. the trace contains evidence, decisions, constraints, open questions, and handoffs
3. Lerim extracts the reusable signal
4. Lerim writes compact context records with source evidence
5. the next agent starts with compact, cited context instead of a raw transcript

The first product wedge is coding agents plus support and incident operations.
Research, revenue, security, and other workflows are future signal-pack
extensions, not separate pipelines today.

## Support operations

Support teams preserve customer constraints, known fixes, failed fixes,
escalation reasons, policy-backed facts, source-of-truth evidence, and handoffs.

Example import:

```bash
lerim trace import ../lerim-cloud/evals/data/traces/support_refund_escalation_001.jsonl \
  --source-name support-agent \
  --source-profile support \
  --scope-type domain \
  --scope support-ops
```

Example question:

```bash
lerim answer "What do we already know about this customer escalation pattern?"
```

## Operations and incidents

Operations teams preserve confirmed root causes, rejected hypotheses,
mitigations, failed mitigations, runbook gaps, owner decisions, source-of-truth
facts, and follow-up risks.

Example question:

```bash
lerim answer "What risks were still open after the last carrier-delay incident?"
```

## Engineering automation

Engineering teams can retain architecture decisions, failed tests, repo
conventions, release lessons, and operational constraints.

Example question:

```bash
lerim answer "What release constraints did previous agents discover?"
```

## Current source boundary

The open-source package includes the trace-to-context foundation, supported
source adapters, and custom clean-trace folders. Customer pilots can start by
choosing one workflow, cleaning its traces into Lerim canonical JSONL, and
registering that folder as a custom project or importing explicit traces with
`lerim trace import`.

For custom agents today, the practical path is:

```bash
lerim project add ~/lerim-traces/support-clean --type custom
lerim ingest --agent custom
lerim context records --profile support
```

If the source trace contains customer-specific noise or sensitive fields, run a
customer-owned cleaner before the files enter that folder. Lerim filters for
durable business signal, but pre-ingest cleaning is still the right boundary for
secrets, regulated data, large raw tool outputs, and retention policy.
