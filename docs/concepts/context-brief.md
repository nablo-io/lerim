# Context Brief

Context Brief is Lerim's long-term startup context for workflow-scoped agent work.

It is a generated Markdown view of durable context records. It is not a second
memory store, and agents should not edit it by hand. The source of truth remains
the durable context store.

Use Context Brief for stable project decisions, constraints, preferences, and
facts. Use [Working Memory](working-memory.md) for short-term continuation context
from the last few hours.

In the current CLI, workflow scope is resolved through project registration.
For customer pilots, the same idea can map to clients, engagements, research
topics, teams, incidents, accounts, or other business scopes.

`lerim context-brief show` prepends live DB freshness before printing the
current markdown snapshot. The preface is current at read time; the markdown is
the last generated artifact.

The current view lives at:

```text
~/.lerim/workspace/current/<project_id>/CONTEXT_BRIEF.md
```

Agents do not need to know the internal scope id ahead of time. They should run
`lerim context-brief show`, `status`, or `path` from inside the current
workspace, or pass `--project <name-or-path>`.

## Flow

```mermaid
flowchart TD
    A["Agent starts project work"] --> B["lerim context-brief show"]
    B --> C["lerim working-memory show"]
    C --> D{"Current Context Brief exists?"}
    D -- "yes" --> E["Read live freshness preface and CONTEXT_BRIEF.md"]
    D -- "no" --> F["Use lerim context-brief status"]
    F --> G["Suggested action: refresh"]
    E --> H{"Need deeper or newer long-term context?"}
    H -- "yes" --> I["lerim context-brief status"]
    I --> J{"DB records changed since generation?"}
    J -- "yes" --> K["lerim context-brief refresh"]
    J -- "no" --> L["Use lerim query or lerim answer for deeper lookup"]
    H -- "no" --> M["Proceed with project work"]
    K --> N["Runtime generates dated artifacts"]
    N --> O["Copy latest markdown and manifest to workspace/current"]
    O --> E
```

## Generation Architecture

```mermaid
flowchart TD
    A["Trigger: manual refresh, daily daemon, or curate changed records"] --> B["Resolve project from cwd, name, or path"]
    B --> C["Resolve scope id from workspace path"]
    C --> D["Read current manifest from workspace/current"]
    D --> E["Count record_versions changed after previous generated_at"]
    E --> F{"Current file exists and changed count is 0 and not --force?"}
    F -- "yes" --> G["Return skipped result and record service run"]
    F -- "no" --> H["Create dated run folder under workspace/YYYY/MM/DD/context-brief"]
    H --> I["Load active candidate records from context store"]
    I --> J{"Any candidates?"}
    J -- "no" --> K["Build empty Context Brief draft without model call"]
    J -- "yes" --> L["Context Brief synthesis agent"]
    L --> M["Compile cited startup context"]
    M --> N["Structured output: summary sections and cited lines"]
    N --> O["Validate every line cites known record IDs and fixed sections"]
    K --> P["Render Markdown"]
    O --> P
    P --> Q["Write run artifacts: CONTEXT_BRIEF.md, manifest, events, agent log, trace"]
    Q --> R["Copy CONTEXT_BRIEF.md and manifest into workspace/current/<project_id>"]
    R --> S["Return generated result and record service run"]
```

## Agent Boundary

The Context Brief feature has two layers:

- `lerim.context_brief` owns deterministic use-case logic: project resolution,
  changed-record detection, candidate loading, validation, rendering, manifests,
  status, artifact paths, deterministic `Start Here`, fixed section order, and
  live freshness prefaces.
- `lerim.agents.context_brief` owns model synthesis only. It receives bounded
  candidate records and returns structured cited lines for the model-filled
  sections.

`LerimRuntime.context_brief()` ties those layers together. The daemon calls the
runtime for all registered projects during the daily pass, and after `curate`
only when curate changed records. The same scheduling point refreshes Working
Memory separately.

## Refresh Rules

Context Brief refresh is intentionally not part of the ingest hot path.

- `lerim context-brief show`, `status`, and `path` are fast local reads.
- `show` prepends live DB freshness before printing the static markdown snapshot.
- `lerim context-brief refresh` generates only when records changed, unless
  `--force` is passed.
- The daemon runs a daily pass across registered projects and skips unchanged
  projects.
- `curate` triggers Context Brief and Working Memory when it creates, updates,
  archives, or otherwise changed records.
- Empty projects get an empty-state Markdown file without a model call.

## Fixed Markdown Shape

Generated Context Brief has a stable section order so agents can scan it
predictably:

1. `Summary`
2. `Start Here`
3. `Continuation Handoff`
4. `Decisions`
5. `Constraints & Preferences`
6. `Project Facts`
7. `Open Risks / Review Queue`
8. `Follow-up Queries`
9. `Sources`

`Summary` is the compact durable startup cache. `Start Here` is deterministic
and rendered by Lerim from project metadata and artifact status. `Continuation
Handoff` must come only from recent episode evidence; without that evidence, it
should explicitly say no persisted implementation handoff is available and point
agents to Working Memory, the current chat, workspace state, and relevant checks
for live work.

`Decisions`, `Constraints & Preferences`, and `Project Facts` hold durable
records. `Open Risks / Review Queue` and `Follow-up Queries` are populated only
from records that explicitly describe unresolved work, review concerns, or
questions worth asking if the next prompt continues that work. `Sources` lists the cited record IDs used in the
body. Any test/build result in these sections is historical persisted evidence;
agents must rerun relevant checks after making edits.

## Artifact Layout

Each generation writes a dated run folder:

```text
~/.lerim/workspace/YYYY/MM/DD/context-brief/context-brief-<timestamp>-<id>/
  CONTEXT_BRIEF.md
  manifest.json
  events.jsonl
  agent.log
  agent-trace.json
```

The latest successful run is copied to the stable current path:

```text
~/.lerim/workspace/current/<project_id>/
  CONTEXT_BRIEF.md
  manifest.json
```

The manifest records the project, `project_id`, run folder, generated time,
previous generation time, trigger, candidate count, included record IDs, and
changed-record count before generation.

## What Agents Should Do

At startup, an agent working in a workspace should:

1. Run `lerim context-brief show` from the workspace.
2. Run `lerim working-memory show` when recent decisions or handoff may matter.
3. If the file is missing or the task depends on newest long-term context, run
   `lerim context-brief status`.
4. If status reports changed DB records, suggest or run
   `lerim context-brief refresh`.
5. Use `lerim query` for exact inspection and `lerim answer` for synthesized
   answers across more context.
