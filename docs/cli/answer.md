# lerim answer

Query existing project context.

## Examples

```bash
lerim answer "What decisions do we have about auth?"
lerim answer "How is caching handled?" --scope project --project lerim-cli
```

## How it works

`answer` uses the context answerer: BAML plans retrieval, Python executes read-only context-store queries, and BAML answers from the retrieved records.

```mermaid
flowchart TD
    A["User runs lerim answer"] --> B["Context answerer receives question and project scope"]

    B --> C["Prompt goal: answer only from retrieved Lerim records"]
    C --> D{"What kind of question is this?"}

    D -- "count, latest, date, current state" --> E["Plan exact count/list retrieval"]
    D -- "topic, rationale, explanation" --> F["Plan semantic/lexical search retrieval"]
    D -- "mixed question" --> G["Use exact filtering first, then search or inspect within that set"]

    E --> H{"Is the returned evidence enough?"}
    F --> H
    G --> H

    H -- "needs more support" --> I["Run another bounded retrieval action"]
    H -- "enough" --> J["Synthesize answer"]

    I --> K{"Do records support the answer?"}
    K -- "yes" --> J
    K -- "no" --> L["Say the context does not contain enough evidence"]

    J --> M["Return answer, scope, projects used, optional debug trace"]
    L --> M
```

Use `--scope project` when you want one project only.
