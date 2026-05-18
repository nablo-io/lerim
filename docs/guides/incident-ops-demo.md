# Incident Ops Demo

Incident examples live in the monorepo eval template, not in `lerim-cli`:

```text
lerim-cloud/evals/data/traces/
lerim-cloud/evals/data/labels/
lerim-cloud/evals/projects/vertical_samples/incident_ops/
```

Import an incident trace:

```bash
lerim trace import ../lerim-cloud/evals/data/traces/incident_webhook_outage_002.jsonl \
  --source-name incident-agent \
  --source-profile ops \
  --scope-type domain \
  --scope incident-ops
```

Inspect the resulting records:

```bash
lerim context records --profile ops
lerim context records --profile ops --type fact
```

Incident records should preserve confirmed root causes, rejected hypotheses,
mitigations, failed paths, owner decisions, and source-of-truth facts only
when the trace supports them.

Do not put incident datasets, expected files, or converter outputs under
`lerim-cli`. Stage conversion work under `lerim-cloud/evals/projects/vertical_samples/incident_ops`
and promote release-ready traces/labels into `lerim-cloud/evals/data`.
