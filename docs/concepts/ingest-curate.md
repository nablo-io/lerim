# Ingest & Curate

Lerim has two runtime paths that keep the shared context store accurate and
clean:

- **Trace ingestion** (hot path) -- processes supported traces or custom clean traces and extracts context records
- **Context curation** (cold path) -- refines existing records offline

Both run automatically in the daemon loop and can also be triggered manually.
Trace ingestion and context curation both use the configured agent model for
structured review and synthesis.

---

## Trace Ingestion Path

The ingestion path turns raw agent traces into structured context records:

1. **Discover** -- adapters scan supported session directories; custom projects scan clean `.jsonl` folders directly
2. **Index** -- new sessions are cataloged in `sessions.sqlite3`
3. **Match to project** -- sessions matching a registered project are enqueued; unmatched sessions are indexed but not extracted
4. **Prepare trace** -- supported traces are compacted and cached; custom traces are read directly because they are already canonical
5. **Trace-to-context flow** -- the ingest flow reads deterministic trace windows, observes typed findings, filters for durable signal, writes the final context payload, and stores one episode record plus zero or more durable records

### Record quality contract

- Durable records should store one reusable rule, decision, fact, preference, constraint, or reference.
- Durable records should not read like meeting notes or session recap prose.
- Episode records are short session recaps only. They should stay compact and should not become the main place where durable context lives.
- Good durable writing is closer to "what is true, why it matters, how to apply it" than to "the user asked, then the agent did X".
- A clean extraction can produce no durable records. Lerim should remember less but better.

### Time window

| Config key | Default | Description |
|------------|---------|-------------|
| `ingest_window_days` | `7` | How far back to look for sessions |
| `ingest_max_sessions` | `50` | Maximum sessions per ingest cycle |

Override with CLI flags:

```bash
lerim ingest --window 14d              # last 14 days
lerim ingest --window all              # all sessions ever
lerim ingest --max-sessions 10         # limit batch size
```

!!! info "Processing order"
    Normal backlog ingest claims the **newest available session per project first** so a fresh install surfaces recent corrections quickly. Historical replay paths can still request oldest-first ordering from the catalog API when chronological reconstruction is required.

---

## Context Curation Path

The curation path runs offline refinement over stored context records,
iterating over all registered projects:

1. **Load inventory** -- read a bounded set of active records in one project scope
2. **Build candidates** -- use semantic search to connect likely-neighbor records into small clusters
3. **Review clusters** -- the context curator decides whether clustered records are duplicates, replacements, complementary, or false-positive neighbors
4. **Review health batches** -- the context curator inspects records not already targeted by cluster actions for routine episodes, obsolete rows, or verbose session-report style
5. **Apply validated actions** -- apply only safe archive, revise, or supersede operations through `ContextStore`
6. **Keep the store lean** -- prefer stronger durable records over a noisy pile of routine episodes
7. **Compress weak records** -- rewrite useful but verbose records into compact reusable context instead of preserving recap style

### Agent Budgets

Context curation uses its config budget as a model-call cap. Context answering uses its config budget as a retrieval-action cap after the answer planner returns a plan:

| Flow | Config key | Purpose |
|------|------------|---------|
| Context curation | `curate_max_llm_calls` | Caps curation model calls per run |
| Context answering | `answer_max_retrieval_actions` | Caps planned retrieval actions per query |

---

## Automatic scheduling

The daemon runs ingest, curate, and Context Brief on independent schedules:

| Path | Config key | Default (see `default.toml`) |
|------|------------|---------|
| Ingest | `ingest_interval_minutes` | `30` |
| Curate | `curate_interval_minutes` | `60` |
| Context Brief | built-in daily pass | `24h` |

Ingest and curate trigger immediately on daemon startup, then repeat at their
configured intervals. Context Brief also runs from the daemon loop, but it
skips projects with no records changed since the current artifact was generated.

Curate also triggers Context Brief for a project when it changed records for
that project. Ingest does not directly trigger Context Brief.

### Local model memory management

When using Ollama, Lerim automatically loads the model into RAM before each cycle and unloads it after (`auto_unload = true` in `[providers]`). The model only occupies memory during active processing.

### Manual triggers

```bash
lerim ingest                       # ingest with default settings
lerim ingest --run-id <id>         # ingest a specific session
lerim ingest --dry-run             # preview without writing
lerim curate                       # run curate cycle
lerim curate --dry-run             # preview without writing
lerim context-brief status         # check generated startup context
lerim context-brief refresh        # refresh only if records changed
```

---

## Related

<div class="grid cards" markdown>

-   :material-cog:{ .lg .middle } **How It Works**

    ---

    Architecture overview and deployment model.

    [:octicons-arrow-right-24: How it works](how-it-works.md)

-   :material-brain:{ .lg .middle } **Context Model**

    ---

    Types, layout, and lifecycle.

    [:octicons-arrow-right-24: Context model](context-model.md)

-   :material-tune:{ .lg .middle } **Configuration**

    ---

    Full TOML config reference including daemon intervals.

    [:octicons-arrow-right-24: Configuration](../configuration/overview.md)

</div>
