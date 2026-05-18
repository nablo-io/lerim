# Support Ops Demo

Support examples live in the monorepo eval template, not in `lerim-cli`:

```text
lerim-cloud/evals/data/traces/
lerim-cloud/evals/data/labels/
lerim-cloud/evals/projects/vertical_samples/support_ops/
```

Import a support trace:

```bash
lerim trace import ../lerim-cloud/evals/data/traces/support_refund_escalation_001.jsonl \
  --source-name support-agent \
  --source-profile support \
  --scope-type domain \
  --scope support-ops
```

Inspect the resulting records:

```bash
lerim context records --profile support
lerim context records --profile support --type constraint
lerim context records --profile support --type fact
```

Support records should preserve strict reusable context: customer constraints,
policy-backed facts, source-of-truth evidence, known fixes, failed paths, and
handoff boundaries when they are supported by the trace.

Do not put support datasets, expected files, or converter outputs under
`lerim-cli`. Stage conversion work under `lerim-cloud/evals/projects/vertical_samples/support_ops`
and promote release-ready traces/labels into `lerim-cloud/evals/data`.
