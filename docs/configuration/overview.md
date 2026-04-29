# Configuration Overview

Lerim uses one global user config.

## Layers

Priority order:

1. `LERIM_CONFIG`
2. `~/.lerim/config.toml`
3. package defaults in `src/lerim/config/default.toml`

## Main sections

- `[data]`
- `[server]`
- `[semantic_search]`
- `[roles.agent]`
- `[providers]`
- `[cloud]`
- `[agents]`
- `[projects]`

## Important paths

The defaults point at `~/.lerim/`.

Key paths derived from that root:

- `context_db_path = ~/.lerim/context.sqlite3`
- `sessions_db_path = ~/.lerim/index/sessions.sqlite3`
- `platforms_path = ~/.lerim/platforms.json`
- `trace_cache_dir = ~/.lerim/cache/traces/<agent>`
- `embedding_cache_dir = ~/.lerim/models/embeddings`
