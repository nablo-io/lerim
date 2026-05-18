# Evaluate Extraction Quality

Do not evaluate Lerim by the number of memories created.

Evaluate whether the compiler produced a small set of useful, supported,
non-duplicate context records:

- precision
- usefulness
- evidence coverage
- duplicate rate
- scope compatibility
- expected record kind alignment
- future reuse

Use the existing `lerim-cloud/evals` template. Do not create a second eval
harness under `lerim-cli`.

From the monorepo root:

```bash
cd lerim-cloud
env -u VIRTUAL_ENV uv run python -m evals.run_context_system
env -u VIRTUAL_ENV uv run python -m evals.run_extraction --case support_refund_escalation_001
env -u VIRTUAL_ENV uv run python -m evals.run_extraction --case incident_webhook_outage_002
```

Dataset artifacts stay under the shared eval tree, never under `lerim-cli`:

```text
lerim-cloud/evals/data/traces/
lerim-cloud/evals/data/labels/
lerim-cloud/evals/projects/vertical_samples/support_ops/
lerim-cloud/evals/projects/vertical_samples/incident_ops/
```
