# lerim sync

Discover new sessions and extract context records.

## Examples

```bash
lerim sync
lerim sync --window 30d
lerim sync --run-id <run_id> --force
lerim sync --agent claude,codex
```

## What it does

- scans connected agent traces
- matches sessions to registered projects
- queues work
- runs extraction
- writes records into `~/.lerim/context.sqlite3`

## Flow

```mermaid
flowchart TD
    A["Trigger: lerim sync or daemon"] --> B["Discover and queue changed sessions"]
    B --> C["Extractor receives one session trace"]

    C --> D["Deterministic graph reads the next trace window"]
    D --> E["BAML ScanTraceWindow returns typed findings"]
    E --> F{"More trace windows?"}
    F -- "yes" --> D
    F -- "no" --> G["BAML SynthesizeExtractRecords creates one episode and durable candidates"]

    G --> H["Persistence normalizes and validates record drafts"]
    H --> I{"Durable records present?"}
    I -- "yes" --> J["Write active durable records"]
    I -- "no" --> K["Write archived episode only"]

    J --> L["SQLite context DB + record_versions"]
    K --> L
    L --> M["Completion summary"]
    M --> N["Sync artifacts: manifest, graph events, trace"]
```

## Notes

- `--no-extract` only indexes and queues work
- `--dry-run` previews the operation
