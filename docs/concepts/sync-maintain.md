# Sync & Maintain

Lerim has two runtime paths that keep the shared context store accurate and
clean:

- **Sync** (hot path) -- processes new agent sessions and extracts context records
- **Maintain** (cold path) -- refines existing records offline

Both run automatically in the daemon loop and can also be triggered manually.
Sync extraction and maintain both use BAML plus LangGraph graphs with the
`[roles.agent]` role model.

---

## Sync path

The sync path turns raw agent session transcripts into structured context
records:

1. **Discover** -- adapters scan session directories for new sessions within the time window
2. **Index** -- new sessions are cataloged in `sessions.sqlite3`
3. **Match to project** -- sessions matching a registered project are enqueued; unmatched sessions are indexed but not extracted
4. **Compact** -- traces are compacted (tool outputs stripped) and cached
5. **Extract flow** -- the BAML plus LangGraph extractor (`[roles.agent]`) reads deterministic trace windows, scans typed findings, synthesizes the final payload, and writes one episode record plus a small number of durable records into `~/.lerim/context.sqlite3`

### Record quality contract

- Durable records should store one reusable rule, decision, fact, preference, constraint, or reference.
- Durable records should not read like meeting notes or session recap prose.
- Episode records are short session recaps only. They should stay compact and should not become the main place where durable context lives.
- Good durable writing is closer to "what is true, why it matters, how to apply it" than to "the user asked, then the agent did X".

### Time window

| Config key | Default | Description |
|------------|---------|-------------|
| `sync_window_days` | `7` | How far back to look for sessions |
| `sync_max_sessions` | `50` | Maximum sessions per sync cycle |

Override with CLI flags:

```bash
lerim sync --window 14d              # last 14 days
lerim sync --window all              # all sessions ever
lerim sync --max-sessions 10         # limit batch size
```

!!! info "Processing order"
    Normal backlog sync claims the **newest available session per project first** so a fresh install surfaces recent corrections quickly. Historical replay paths can still request oldest-first ordering from the catalog API when chronological reconstruction is required.

---

## Maintain path

The maintain path runs offline refinement over stored context records,
iterating over all registered projects:

1. **Load inventory** -- read a bounded set of active records in one project scope
2. **Build candidates** -- use semantic search to connect likely-neighbor records into small clusters
3. **Review clusters** -- ask BAML to decide whether clustered records are duplicates, replacements, complementary, or false-positive neighbors
4. **Review health batches** -- ask BAML to inspect records not already targeted by cluster actions for routine episodes, obsolete rows, or verbose session-report style
5. **Apply validated actions** -- apply only safe archive, revise, or supersede operations through `ContextStore`
6. **Keep the store lean** -- prefer stronger durable records over a noisy pile of routine episodes
7. **Compress weak records** -- rewrite useful but verbose records into compact reusable context instead of preserving recap style

### Request turn limits

Maintain uses its config budget as a BAML-call cap. Ask uses its config budget as a PydanticAI request-turn cap:

| Flow | Config key | Purpose |
|------|------------|---------|
| Maintain | `max_iters_maintain` | Caps maintain BAML calls per run |
| Ask | `max_iters_ask` | Caps ask agent request turns per query |

---

## Automatic scheduling

The daemon runs sync, maintain, and Working Memory on independent schedules:

| Path | Config key | Default (see `default.toml`) |
|------|------------|---------|
| Sync | `sync_interval_minutes` | `30` |
| Maintain | `maintain_interval_minutes` | `60` |
| Working Memory | built-in daily pass | `24h` |

Sync and maintain trigger immediately on daemon startup, then repeat at their
configured intervals. Working Memory also runs from the daemon loop, but it
skips projects with no records changed since the current artifact was generated.

Maintain also triggers Working Memory for a project when maintain changed records
for that project. Sync does not directly trigger Working Memory.

### Local model memory management

When using Ollama, Lerim automatically loads the model into RAM before each cycle and unloads it after (`auto_unload = true` in `[providers]`). The model only occupies memory during active processing.

### Manual triggers

```bash
lerim sync                           # sync with default settings
lerim sync --run-id <id>             # sync a specific session
lerim sync --dry-run                 # preview without writing
lerim maintain                       # run maintain cycle
lerim maintain --dry-run             # preview without writing
lerim working-memory status          # check generated startup context
lerim working-memory refresh         # refresh only if records changed
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
