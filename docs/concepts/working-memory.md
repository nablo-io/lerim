# Working Memory

Working Memory is Lerim's fast startup context for coding agents.

It is a generated Markdown view of durable SQLite context records. It is not a
second memory store, and agents should not edit it by hand. The source of truth
remains `~/.lerim/context.sqlite3`.

The current view lives at:

```text
~/.lerim/workspace/current/<project_id>/WORKING_MEMORY.md
```

Agents do not need to know `<project_id>` ahead of time. They should run
`lerim working-memory show`, `status`, or `path` from inside the repository, or
pass `--project <name-or-path>`.

## Flow

```mermaid
flowchart TD
    A["Coding agent starts work"] --> B["lerim working-memory show"]
    B --> C{"Current artifact exists?"}
    C -- "yes" --> D["Read WORKING_MEMORY.md"]
    C -- "no" --> E["Use lerim working-memory status"]
    E --> F["Suggested action: refresh"]
    D --> G{"Need deeper or newer context?"}
    G -- "yes" --> H["lerim working-memory status"]
    H --> I{"Records changed since generation?"}
    I -- "yes" --> J["lerim working-memory refresh"]
    I -- "no" --> K["Use lerim query or lerim ask for deeper lookup"]
    G -- "no" --> L["Proceed with coding"]
    J --> M["Runtime generates dated artifacts"]
    M --> N["Copy latest markdown and manifest to workspace/current"]
    N --> D
```

## Generation Architecture

```mermaid
flowchart TD
    A["Trigger: manual refresh, daily daemon, or maintain changed records"] --> B["Resolve project from cwd, name, or path"]
    B --> C["Resolve project_id from repo path"]
    C --> D["Read current manifest from workspace/current"]
    D --> E["Count record_versions changed after previous generated_at"]
    E --> F{"Current file exists and changed count is 0 and not --force?"}
    F -- "yes" --> G["Return skipped result and record service run"]
    F -- "no" --> H["Create dated run folder under workspace/YYYY/MM/DD/working-memory"]
    H --> I["Load active candidate records from SQLite"]
    I --> J{"Any candidates?"}
    J -- "no" --> K["Build empty Working Memory draft without model call"]
    J -- "yes" --> L["Working Memory synthesis agent"]
    L --> M["PydanticAI agent with low-variance settings"]
    M --> N["Structured output: summary sections and cited lines"]
    N --> O["Validate every line cites known record IDs"]
    K --> P["Render Markdown"]
    O --> P
    P --> Q["Write run artifacts: WORKING_MEMORY.md, manifest, events, agent log, trace"]
    Q --> R["Copy WORKING_MEMORY.md and manifest into workspace/current/<project_id>"]
    R --> S["Return generated result and record service run"]
```

## Agent Boundary

The Working Memory feature has two layers:

- `lerim.working_memory` owns deterministic use-case logic: project resolution,
  changed-record detection, candidate loading, validation, rendering, manifests,
  status, and artifact paths.
- `lerim.agents.working_memory` owns model synthesis only. It receives bounded
  candidate records and returns structured cited lines.

`LerimRuntime.working_memory()` ties those layers together. The daemon calls the
runtime for all registered projects during the daily pass, and after `maintain`
only when maintain changed records.

## Refresh Rules

Working Memory refresh is intentionally not part of the sync hot path.

- `lerim working-memory show`, `status`, and `path` are fast local reads.
- `lerim working-memory refresh` generates only when records changed, unless
  `--force` is passed.
- The daemon runs a daily pass across registered projects and skips unchanged
  projects.
- `maintain` triggers Working Memory only when it merged, archived,
  consolidated, or otherwise changed records.
- Empty projects get an empty-state Markdown file without a model call.

## Artifact Layout

Each generation writes a dated run folder:

```text
~/.lerim/workspace/YYYY/MM/DD/working-memory/working-memory-<timestamp>-<id>/
  WORKING_MEMORY.md
  manifest.json
  events.jsonl
  agent.log
  agent-trace.json
```

The latest successful run is copied to the stable current path:

```text
~/.lerim/workspace/current/<project_id>/
  WORKING_MEMORY.md
  manifest.json
```

The manifest records the project, `project_id`, run folder, generated time,
candidate count, included record IDs, and changed-record count.

## What Agents Should Do

At startup, a coding agent should:

1. Run `lerim working-memory show` from the repo.
2. If the file is missing or the task depends on newest context, run
   `lerim working-memory status`.
3. If status reports changed records, suggest or run
   `lerim working-memory refresh`.
4. Use `lerim query` for exact inspection and `lerim ask` for synthesized
   answers across more context.
