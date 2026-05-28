# lerim working-memory

`lerim working-memory` reads or refreshes short-term continuation context for
the resolved project.

It is host-only for `show`, `status`, and `path`. `refresh` runs local
generation and records a service run. The generated Markdown is a derived view
of recent `record_versions` in `~/.lerim/context.sqlite3`.

## Commands

```bash
lerim working-memory show
lerim working-memory status
lerim working-memory path
lerim working-memory refresh
lerim working-memory refresh --force
```

| Subcommand | Description |
|------------|-------------|
| `show` | Print live freshness plus the current `WORKING_MEMORY.md` |
| `status` | Print availability, generated time, age, window, changed-record count, paths, latest run folder, and suggested action |
| `path` | Print the stable expected current artifact path |
| `refresh` | Generate dated artifacts and update the stable current copy when recent context changed or the short-term window moved |

| Flag | Description |
|------|-------------|
| `--project` | Registered project name or path. Defaults to the project resolved from cwd |
| `--force` | On `refresh`, regenerate even when the current artifact is fresh |
| `--json` | Emit structured JSON for `status`, `path`, and `refresh` |

## Time Scale

Working Memory uses a six-hour recency window. It is meant to answer:

- what was recently completed or captured
- what context changed recently
- which records were superseded or archived
- where to resume only if the next user prompt continues the same work

It is not a task list. The next user prompt decides what should happen next.

Use `lerim context-brief show` for long-term durable project memory.

## Output Location

Current artifact:

```text
~/.lerim/workspace/current/<project_id>/WORKING_MEMORY.md
~/.lerim/workspace/current/<project_id>/WORKING_MEMORY.manifest.json
```

Dated run artifacts:

```text
~/.lerim/workspace/YYYY/MM/DD/working-memory/working-memory-<timestamp>-<id>/
```

## Refresh Behavior

`show` never refreshes. It reads the current artifact and computes a live
freshness preface from SQLite.

`refresh` skips when the current artifact exists, no project records changed
after its `generated_at`, and the artifact age is still inside the six-hour
window. It regenerates when records changed, when `--force` is passed, or when
the short-term window has moved.
