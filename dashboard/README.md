# Lerim Dashboard

Local Next.js dashboard for the open-source Lerim runtime.

## Run Locally

Run these two processes from the `lerim` repo:

```bash
# Terminal 1: backend API + daemon
lerim serve

# Terminal 2: dashboard UI
cd dashboard
npm install
npm run dev
```

Open `http://localhost:3000`. The UI proxies `/api` to the backend at
`http://localhost:8765`; set `LERIM_API_URL` before `npm run dev` only when the
backend is running somewhere else.

The dashboard is read-only. Use the CLI for write actions such as `lerim ingest`,
`lerim curate`, `lerim answer`, and queue retry/skip.
