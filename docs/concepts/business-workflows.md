# Business Workflows

Lerim is useful when a team runs repeated AI workflows and keeps losing the
operating context, correction signal, and evaluation evidence between runs.

The pattern is:

1. an agent completes work inside a business process
2. the trace contains evidence, decisions, constraints, open questions, and handoffs
3. Lerim extracts the reusable signal
4. Lerim writes compact context records and eval-ready assets with source evidence
5. the next agent starts with compact, cited context instead of a raw transcript
6. approved corrections can later become training data for workflow-specific models

The commercial wedge is one repeated private workflow with trace access, a
workflow owner, privacy constraints, and measurable quality failure. Coding
agents are a strong proof pack because native adapters are mature, but the
company positioning should be broader: private agent improvement infrastructure
for enterprise workflows.

Support, incident/security operations, research, compliance, revenue, and other
workflows can use custom clean traces today when the source owner handles export,
redaction, and retention. Dedicated signal packs for those verticals are future
extensions, not separate pipelines.

## Support operations

Support teams preserve customer constraints, known fixes, failed fixes,
escalation reasons, policy-backed facts, source-of-truth evidence, and handoffs.

Example import:

```bash
lerim trace import docs/examples/traces/support-agent-run.jsonl \
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
conventions, release lessons, and operational constraints. This remains a strong
technical proof workflow, but it should not be the only market story.

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

## Funding-readiness checklist

Use this as the operating to-do list for aligning pitch, website, docs, and the
repo without changing the open-source code boundary:

- Pitch: lead with private agent improvement infrastructure, not AI Lab and not coding-only memory.
- Website: make the first offer a Context Audit for one repeated enterprise agent workflow.
- Docs: keep the open-core boundary clear and show support, incident/security, research, compliance, and engineering automation as workflow packs.
- Product proof: measure context reused, false memories rejected, eval pass rate, human acceptance, token budget saved, and repeated work reduced.
- Commercial proof: target 3-5 paid audits, 2-3 pilots, and one private deployment before a seed round.
- Pricing: use Context Audit at roughly $15K-$40K, private deployment at roughly $60K-$250K/year per workflow or team, and SFT/RL services at roughly $15K-$75K/month plus compute after approved traces and evals exist.
- Market framing: TAM is enterprise agentic AI, SAM is private/custom agent improvement workflows, and SOM is the first 24-36 months of deployments and training retainers.
- Repo boundary: keep coding adapters because they are real product proof; add enterprise features through docs, examples, workflow packs, evals, and private deployment work rather than hiding the current open-source strengths.
