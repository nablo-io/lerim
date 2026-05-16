# Dashboard

Lerim's hosted dashboard lives on Lerim Cloud.

This repo does not ship a full local web UI. The local runtime exposes:

- the CLI
- the local JSON API from `lerim serve`
- a small transition page at `/` when no bundled static assets are present

For local work, use the CLI and local API directly:

```bash
lerim status
lerim answer "What changed?"
lerim ingest
lerim curate
```

The local API is available on port `8765` (default) when you run `lerim up` or `lerim serve`.

```bash
curl http://localhost:8765/api/health
```

## Related

- [CLI: lerim serve](../cli/serve.md) — local API + daemon loop
- [CLI: lerim dashboard](../cli/dashboard.md) — prints the current transition message
- [CLI: lerim status](../cli/status.md) — runtime overview
