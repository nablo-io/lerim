# lerim curate

Run one context-curation pass.

## Examples

```bash
lerim curate
lerim curate --dry-run
```

## What it does

`curate` reads existing records and keeps active context compact:

- supersede weaker duplicates with stronger records
- archive low-value records
- revise useful but verbose records
- leave healthy records unchanged

It works on the database.

## Flow

```mermaid
flowchart TD
    A["Trigger: lerim curate or daemon"] --> B["Python runtime resolves project scope"]

    B --> C["Python: load active record inventory"]
    C --> D["Python: semantic search builds candidate clusters"]
    D --> E["LLM/BAML: review each cluster"]
    E --> F["LLM/BAML: review records without cluster actions"]

    E --> G["Python: validate proposed actions"]
    F --> G
    G --> H{"Action type"}
    H -- "duplicate or replaced truth" --> I["ContextStore.supersede_record"]
    H -- "verbose, weak, or report-like" --> J["ContextStore.update_record"]
    H -- "junk, obsolete, or low-value episode" --> K["ContextStore.archive_record"]
    H -- "healthy or false-positive neighbor" --> L["Leave unchanged"]

    I --> M["SQLite context DB + record_versions"]
    J --> M
    K --> M
    L --> N["Curate summary and artifacts"]
    M --> N

    N --> O{"Any records changed?"}
    O -- "yes" --> P["Refresh Context Brief for project"]
    O -- "no" --> Q["Finish"]
```
