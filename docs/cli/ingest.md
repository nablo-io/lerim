# lerim ingest

Discover supported trace sessions and extract context records.

## Examples

```bash
lerim ingest
lerim ingest --window 30d
lerim ingest --run-id <run_id> --force
lerim ingest --agent claude,codex
```

## What it does

- scans connected trace sources
- matches sessions to registered projects
- queues work
- runs selective trace-to-context extraction
- writes records into `~/.lerim/context.sqlite3`

## Flow

```mermaid
flowchart TD
    A["Trigger: lerim ingest or daemon"] --> B["Discover and queue changed sessions"]
    B --> C["Extractor receives one session trace"]

    C --> D["Deterministic graph reads the next trace window"]
    D --> E["BAML ObserveSourceWindow returns typed findings"]
    E --> F{"More trace windows?"}
    F -- "yes" --> D
    F -- "no" --> G["BAML FilterDurableSignal keeps reusable signal"]
    G --> H["BAML SynthesizeContextRecords creates one episode and zero or more durable candidates"]

    H --> I{"Durable records present?"}
    I -- "yes" --> J["Write active durable records"]
    I -- "no" --> K["Write archived episode only"]

    J --> L["SQLite context DB + record_versions"]
    K --> L
    L --> M["Completion summary"]
    M --> N["Ingest artifacts: manifest, graph events, trace"]
```

## Notes

- `--no-extract` only indexes and queues work
- `--dry-run` previews the operation
