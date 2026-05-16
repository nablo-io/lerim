# lerim serve

Start the HTTP API server and daemon loop in a single process. This repo does not bundle the full dashboard UI; the hosted product surface lives on Lerim Cloud.

## Overview

`lerim serve` is the all-in-one runtime process. It starts:

1. **HTTP API** — JSON endpoints used by CLI commands (`answer`, `ingest`, `curate`, `status`)
2. **Daemon loop** — Background ingest and curate cycles on configured intervals

GET `/` may return a minimal HTML stub when no bundled static assets are present.

This is the Docker container entrypoint (`lerim up` runs `lerim serve` inside the container), but it can also be run directly for development without Docker.

!!! info
    For Docker-based usage, prefer `lerim up` which handles container lifecycle, volume mounts, and config generation automatically. Use `lerim serve` directly when developing or running without Docker.

## Syntax

```bash
lerim serve [--host HOST] [--port PORT]
```

## Parameters

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--host</span>
    <span class="param-type">string</span>
    <span class="param-badge default">default: from [server].host (shipped: 127.0.0.1)</span>
  </div>
  <p class="param-desc">Network interface to bind to. Docker path (<code>lerim up</code>) injects <code>--host 0.0.0.0</code>. Direct <code>lerim serve</code> uses config default unless overridden.</p>
</div>

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--port</span>
    <span class="param-type">integer</span>
    <span class="param-badge default">default: 8765</span>
  </div>
  <p class="param-desc">TCP port for the JSON API (and stub HTML at <code>/</code> when no bundled UI).</p>
</div>

## Examples

### Start with defaults

```bash
lerim serve
```

Logs show the API bind address and daemon intervals (exact wording may vary by version).

### Custom bind address

```bash
# Local-only access on a custom port
lerim serve --host 127.0.0.1 --port 9000
```

### Development workflow

```bash
# Install in editable mode and run directly
uv pip install -e .
lerim serve
```

!!! tip
    When running `lerim serve` directly (not via Docker), make sure your config exists. Run `lerim init` first if needed.

## What it starts

| Component | Description | Endpoint |
|-----------|-------------|----------|
| HTTP API | JSON API for CLI commands | `http://<host>:<port>/api/` |
| Root | Stub HTML or optional assets | `http://<host>:<port>/` |
| Daemon loop | Background ingest/curate on intervals | — (internal) |

The daemon loop uses `ingest_interval_minutes` and `curate_interval_minutes` from your active Lerim config (defaults are in the shipped `default.toml`).

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Clean shutdown (SIGINT/SIGTERM) |
| `1` | Startup failure (port in use, config missing) |

## Related commands

<div class="grid cards" markdown>

-   :material-arrow-up-bold: **lerim up**

    ---

    Start Lerim via Docker (runs `serve` inside)

    [:octicons-arrow-right-24: lerim up](up-down-logs.md)

-   :material-refresh: **Background loop**

    ---

    Ingest + curate intervals (runs inside `serve`)

    [:octicons-arrow-right-24: Background loop](daemon.md)

-   :material-monitor-dashboard: **lerim dashboard**

    ---

    Print the dashboard transition message + CLI alternatives

    [:octicons-arrow-right-24: lerim dashboard](dashboard.md)

-   :material-chart-box: **lerim status**

    ---

    Check server health and runtime state

    [:octicons-arrow-right-24: lerim status](status.md)

</div>
