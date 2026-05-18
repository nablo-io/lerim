# Context Records

Context records are the first persisted output of ingestion.

Ingestion does not create product presentation artifacts or insights. It writes:

- one `episode` record for the source session
- zero or more durable records when the trace contains reusable context

Durable record kinds are intentionally small:

- `decision`
- `preference`
- `constraint`
- `fact`

The source profile only guides what to notice and what to reject. It is not an
output taxonomy.

Example:

```text
kind = constraint
source_profile = support
body = Do not promise refunds above EUR 500 before supervisor approval.
```

List records:

```bash
lerim context records --profile support
lerim context records --profile ops
lerim context records --type fact
lerim context records --profile support --status active
```

Later product layers can group records into insights, review surfaces, or other
workflow views. The ingestion layer stays strict and stores only reusable,
evidence-backed context.
