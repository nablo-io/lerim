# lerim dashboard

Shows the current dashboard transition message and lists CLI alternatives.

## Overview

This command prints the current transition message:

- the local dashboard is not bundled in this repo
- the hosted product surface lives on Lerim Cloud
- the CLI remains the local control surface for ingest, curate, answer, and queue work

## Syntax

```bash
lerim dashboard
```

## Examples

```bash
lerim dashboard
```

Sample output:

```
  Lerim Dashboard is moving to the cloud.
  The new dashboard will be available at https://lerim.dev

  In the meantime, use these CLI commands:
    lerim status     - system overview
    lerim answer        - query your stored context
    lerim queue      - view session processing queue
    lerim ingest       - process new sessions
    lerim curate   - refine stored records
```

## See also

- [lerim status](status.md) — runtime state overview
- [lerim serve](serve.md) — HTTP API + daemon loop
- [Dashboard Guide](../guides/dashboard.md)
