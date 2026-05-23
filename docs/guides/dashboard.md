# Dashboard

Lerim ships an open-source local dashboard in `dashboard/`.

The dashboard talks directly to the local JSON API exposed by `lerim serve`.
It is a read-only product surface for source sessions, runtime activity,
records, and graph exploration.

## Run Locally

```bash
# Terminal 1: backend API + daemon
lerim serve

# Terminal 2: dashboard UI
cd dashboard
npm install
npm run dev
```

Open `http://localhost:3000`.

The UI proxies `/api` to `http://localhost:8765` in development. If the backend
uses a different URL, start the UI with `LERIM_API_URL=<backend-url> npm run dev`.

Use the CLI for write actions:

```bash
lerim ingest
lerim curate
lerim answer "What changed?"
lerim queue
```

## Related

- [CLI: lerim serve](../cli/serve.md) — local API + daemon loop
- [CLI: lerim dashboard](../cli/dashboard.md) — local dashboard instructions
- [CLI: lerim status](../cli/status.md) — runtime overview
