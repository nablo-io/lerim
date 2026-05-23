# lerim dashboard

Prints the local dashboard startup commands.

## Overview

The dashboard needs two running processes:

- backend: `lerim serve`
- UI: `cd dashboard && npm run dev`

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
  Lerim Dashboard runs locally with two processes:
    backend: lerim serve
    UI:      cd dashboard && npm run dev

  Open: http://localhost:3000
  API:  http://localhost:8765
  Writes stay in the CLI: ingest, curate, answer, queue.
```

## See also

- [lerim status](status.md) — runtime state overview
- [lerim serve](serve.md) — HTTP API + daemon loop
- [Dashboard Guide](../guides/dashboard.md)
