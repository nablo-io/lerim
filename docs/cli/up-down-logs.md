# lerim up / down / logs

Docker container lifecycle commands for starting, stopping, and monitoring Lerim.

## Overview

These host-only commands manage the Docker container that runs `lerim serve` (daemon + JSON API).

!!! info "Host-only commands"
    These commands run on the host machine. They do not require a running Lerim server.

## Syntax

```bash
lerim up [--build]
lerim down
lerim logs [--follow]
```

## Commands

### `lerim up`

Start Lerim as a Docker service:

```bash
lerim up                    # start Lerim (pull GHCR image)
lerim up --build            # build and recreate from the local Dockerfile
```

This reads `~/.lerim/config.toml`, generates a `docker-compose.yml` in `~/.lerim/`, and runs `docker compose up -d`.

By default the compose file references the pre-built GHCR image (`ghcr.io/lerim-dev/lerim-cli`) tagged with the current package version. Use `--build` to build from the local Dockerfile, tag it as `lerim-lerim:local`, and force-recreate the container.

After start, the CLI waits for `GET /api/health` to return `200 OK` before reporting success.

### `lerim down`

Stop the Docker container:

```bash
lerim down
```

### `lerim logs`

View local log entries from dated JSONL files under `~/.lerim/logs/YYYY/MM/DD/` (last 50 by default).

```bash
lerim logs                      # show recent logs
lerim logs --follow             # tail logs continuously
lerim logs --level error        # filter by level
lerim logs --since 2h           # entries from the last 2 hours
lerim logs --json               # raw JSONL output
```

## Parameters

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--build</span>
    <span class="param-type">boolean</span>
    <span class="param-badge default">default: off</span>
  </div>
  <p class="param-desc">Build from local Dockerfile instead of pulling the GHCR image.</p>
</div>

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--follow, -f</span>
    <span class="param-type">boolean</span>
    <span class="param-badge default">default: off</span>
  </div>
  <p class="param-desc">Live tail: watch for new log lines and print as they appear.</p>
</div>

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--level</span>
    <span class="param-type">string</span>
  </div>
  <p class="param-desc">Filter by log level (case-insensitive). E.g. <code>error</code>, <code>warning</code>, <code>info</code>.</p>
</div>

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--since</span>
    <span class="param-type">string</span>
  </div>
  <p class="param-desc">Show entries from the last N hours/minutes/days. Format: <code>1h</code>, <code>30m</code>, <code>2d</code>.</p>
</div>

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--json</span>
    <span class="param-type">boolean</span>
    <span class="param-badge default">default: off</span>
  </div>
  <p class="param-desc">Output raw JSONL lines instead of formatted text.</p>
</div>

## Examples

```bash
# Start the service
lerim up

# Check it's running
lerim status

# View logs
lerim logs --follow

# Stop when done
lerim down
```

## Notes

- The container runs `lerim serve` which provides the daemon loop and JSON API
- `http://localhost:8765/api/health` is the local health endpoint
- `http://localhost:8765/` serves a small local stub/diagnostic page
- Docker restart policy is `"no"` — the container does not auto-restart after reboots

## Related commands

<div class="grid cards" markdown>

-   :material-server: **lerim serve**

    ---

    Run directly without Docker

    [:octicons-arrow-right-24: lerim serve](serve.md)

-   :material-chart-box: **lerim status**

    ---

    Check runtime state

    [:octicons-arrow-right-24: lerim status](status.md)

</div>
