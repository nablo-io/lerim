# lerim status

Show runtime state.

## Examples

```bash
lerim status
lerim status --live
lerim status --live --interval 1
lerim status --scope project --project lerim-cli
lerim status --json
```

## What it shows

- connected agents
- context record counts
- indexed session counts
- ingest discovery window used for queueing
- queue state
- per-project stream state
- recent ingest and curate activity

## Stream states

- `running`: a project has an active extraction job now
- `queued`: a project has pending work waiting to run
- `quiet`: past in-scope sessions were already processed; no queued work now
- `idle`: no indexed sessions exist for that project in the current ingest window
- `blocked`: the oldest queued job is dead-lettered and needs retry or skip

## Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--scope` | `all` | Status scope: all projects or one project |
| `--project` | -- | Project name/path when `--scope=project` |
| `--live` | off | Refresh the status display until interrupted |
| `--interval` | `3.0` | Refresh interval in seconds for `--live` |
