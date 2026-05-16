# lerim context-brief

`lerim context-brief` reads or refreshes generated startup context for the
resolved project.

It is host-only for `show`, `status`, and `path`. `refresh` runs local
generation and records a service run. The generated Markdown is a derived view
of `~/.lerim/context.sqlite3`.

## Commands

```bash
lerim context-brief show
lerim context-brief status
lerim context-brief path
lerim context-brief refresh
lerim context-brief refresh --force
```

| Subcommand | Description |
|------------|-------------|
| `show` | Print live DB freshness plus the current `CONTEXT_BRIEF.md` without model calls |
| `status` | Print availability, generated time, age, included records, DB changed-record count, paths, latest run folder, and suggested action |
| `path` | Print the stable expected current artifact path |
| `refresh` | Generate dated artifacts and update the stable current copy when records changed |

| Flag | Description |
|------|-------------|
| `--project` | Registered project name or path. Defaults to the project resolved from cwd |
| `--force` | On `refresh`, regenerate even when no context records changed |
| `--json` | Emit structured JSON for `status`, `path`, and `refresh` |

## Project Resolution

Agents should not hardcode project IDs. Lerim resolves the project from the
current directory by matching registered project paths. If the command is run
outside the repository, pass a registered project name or path:

```bash
lerim context-brief show --project lerim-cli
lerim context-brief status --project ~/codes/my-app
```

## Printed Output

`show` prints a live DB freshness preface before the static markdown artifact.
The preface is computed when `show` runs, so agents can see whether records
changed after the snapshot was generated without triggering synthesis.

The markdown artifact itself uses a fixed section order:

1. `Summary`
2. `Start Here`
3. `Current Handoff`
4. `Decisions`
5. `Constraints & Preferences`
6. `Project Facts`
7. `Open Risks / Review Queue`
8. `Follow-up Queries`
9. `Sources`

`Start Here` is deterministic repo/startup guidance rendered by Lerim, not
model-written prose. `Current Handoff` is populated only from recent episode
evidence; when recent episode evidence is absent, it says no implementation
handoff is available from persisted records. Test/build claims inside the
markdown are historical persisted evidence, not current verification.

## Output Location

Current artifact:

```text
~/.lerim/workspace/current/<project_id>/CONTEXT_BRIEF.md
```

Dated run artifacts:

```text
~/.lerim/workspace/YYYY/MM/DD/context-brief/context-brief-<timestamp>-<id>/
```

## Generation Flow

```mermaid
flowchart TD
    A["Trigger: daily daemon, after curate, or manual refresh"] --> B["Resolve project and check current manifest"]
    B --> C{"New DB records or --force?"}

    C -- "no" --> D["Skip generation"]
    C -- "yes" --> E["Select candidate records from SQLite"]
    E --> F["Context Brief agent receives compact candidate set"]

    F --> G["Prompt goal: create fast startup memory from candidates only"]
    G --> H["Agent ranks what matters for starting work: current handoff, decisions, preferences, facts, risks"]
    H --> I["Agent writes fixed sections with cited record IDs"]

    I --> J{"Every line cites an allowed source record?"}
    J -- "no" --> K["Validation fails; retry or error"]
    J -- "yes" --> L["Render CONTEXT_BRIEF.md"]

    L --> M["Write dated artifact"]
    M --> N["Update workspace/current/<project_id>/CONTEXT_BRIEF.md"]

    O["Agent startup"] --> P["lerim context-brief show"]
    P --> Q["Fast read of generated memory plus live freshness status"]
```

## Automation

Context Brief is refreshed outside the ingest hot path:

- the daemon runs a daily pass for all registered projects
- `curate` runs it only for projects whose records changed
- unchanged projects are skipped
- manual `refresh --force` bypasses the unchanged check

See [Context Brief](../concepts/context-brief.md) for the architecture.

`CONTEXT_BRIEF.md` is a static snapshot. Use the live preface from `show` or
the JSON from `status` for current DB freshness. Test/build results inside the
markdown are historical persisted evidence; rerun relevant checks after edits.
