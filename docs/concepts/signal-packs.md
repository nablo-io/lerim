# Signal Packs

A signal pack defines what reusable context means for one workflow.

The core compiler remains generic:

```text
source window -> durable findings -> filter -> context records
```

The signal pack changes extraction focus, rejection rules, evidence
expectations, scope rules, and evaluation expectations.

Bundled profiles:

- `coding`: repo conventions, architecture decisions, setup facts, failed paths, test lessons, release handoffs
- `support`: customer constraints, known fixes, failed fixes, escalation reasons, policy-backed facts, handoffs
- `ops`: root causes, mitigations, rejected hypotheses, runbook gaps, owner decisions, follow-up risks

Signal packs live in `src/lerim/profiles/` as YAML files. Each pack defines:

```yaml
id: support
display_name: Support Operations
description: Customer support and customer operations traces.
focus_rules: []
reject_as_noise: []
evidence_rules: []
scope_rules: []
evaluation_gold_schema: []
```

Use one compiler architecture. Add a new profile only when the workflow has
different reusable signals, noise rules, evidence expectations, or scope
boundaries.
