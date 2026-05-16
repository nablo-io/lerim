# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.1] - 2026-05-16

### Added
- Added custom trace-folder projects with `lerim project add <path> --type custom`.
- Added direct custom JSONL session discovery with `agent_type=custom`, without platform adapters or compaction.
- Added custom-agent integration docs with a pasteable cleaner prompt for generating customer-owned trace cleaning scripts.
- Added per-agent architecture docs with Mermaid flowcharts generated from the compiled LangGraph graphs.
- Added integration coverage that registers a custom project, writes synthetic traces, runs ingest, and verifies custom sessions/jobs use the original clean trace files.

### Changed
- Updated README, docs, bundled skill text, and configuration reference around the broader context-compiler positioning and custom-agent trace flow.
- Extended project config/API/CLI payloads with `supported` and `custom` source types while preserving existing project defaults.

## [0.2.0] - 2026-05-16

### Added
- Expanded Lerim from a coding-agent memory layer into a general trace-to-context architecture for AI agent workflows.
- Added BAML/LangGraph context curation, context answering, and context-brief compilation alongside trace ingestion.
- Added layered durable-signal filtering, source-session review, synthesized record updates, and a final context quality gate.
- Added integration coverage for each BAML-backed agent role with real LLM calls.

### Changed
- Renamed the live agent roles around the new context architecture: trace ingestion, durable-signal filtering, context writing, context curation, context answering, and context-brief compilation.
- Refreshed prompts to avoid fixture-specific or coding-only assumptions and to classify durable signal from broader agent activity traces.
- Updated README, docs, bundled skill text, and landing-page positioning around agent traces, durable signal, and future-agent context.

### Fixed
- Hardened SQLite migrations for foreign-key-safe table rebuilds.
- Tightened answerer retrieval so topical words are not misread as record-kind filters.
- Improved context-brief validation by dropping unsupported generated lines before rendering.

### Removed
- Removed the remaining PydanticAI agent runtime and retired fallback/dead configuration paths.

## [0.1.83] - 2026-05-14

### Added
- Packaged the BAML source and generated client under `src/lerim/agents/` so future agents can share the same BAML/LangGraph layout.
- Added the production BAML/LangGraph extract package with deterministic trace windowing, typed BAML scans, record synthesis, context-store persistence, and structured graph events.

### Changed
- Replaced sync extraction with the BAML/LangGraph harness while keeping maintain, ask, and working-memory on PydanticAI.
- Updated extraction evals, integration tests, docs, and run artifacts to use graph events instead of PydanticAI extract messages.
- Tuned extraction prompts to avoid storing incidental personal names unless identity itself is the durable context.

### Fixed
- Hardened session catalog/API status paths so catalog storage issues degrade status responses instead of crashing status or maintain.
- Made extraction persistence idempotent when a rebuilt session catalog replays a session whose episode already exists.
- Improved long-running extraction queue handling so transient SQLite heartbeat write failures and sequential processing do not create false stale-running jobs.

### Removed
- Removed the legacy PydanticAI extract agent, extract-only trace tools, history processors, and the experimental `baml_agents/` sidecar.

## [0.1.81] - 2026-04-29

### Fixed
- Unit docs-contract tests no longer require a repo-local `AGENTS.md`; the authoritative agent tool contract check now only reads committed public docs.

## [0.1.80] - 2026-04-29

### Added
- Local-first context runtime built around the canonical SQLite context store, project-scoped records, version history, FTS, and local embedding-backed retrieval.
- Semantic agent toolsets for sync, maintain, and ask flows, including context listing, search, fetching, writing, revising, archiving, superseding, counting, trace-note, and pruning tools.
- End-to-end MLflow observability for sync, maintain, and ask runs using Lerim-owned root/tool/event spans that continue through controlled PydanticAI retries.
- Expanded runtime artifacts, queue/status metadata, Docker runtime helpers, and CLI/API coverage for context operations.
- Larger unit, smoke, integration, and end-to-end test suites for context extraction, maintenance, retrieval, queueing, cloud sync, scope handling, and CLI behavior.

### Changed
- Reworked memory terminology and docs around durable context records instead of legacy per-project markdown memory files.
- Updated provider, tracing, configuration, logging, and run-artifact behavior for the DB-backed context architecture.
- Refreshed README and documentation for setup, commands, storage layout, context model, tracing, and operational workflows.

### Fixed
- Release-readiness cleanup for provider fallback parsing, strict TOML string/path validation, SPDX license metadata, and docs accuracy around sync ordering, tracing, semantic search config, and query sessions.
- MLflow traces now preserve a successful Lerim root run even when a tool attempt raises a controlled retry before later recovery.

## [0.1.72] - 2026-04-13

### Fixed
- CI `unit-tests` pipeline now passes again after restoring missing test compatibility helper `build_test_ctx` in `lerim.agents.tools`.
- Addressed Ruff failures in unit tests (unused imports and ambiguous loop variable names), so release branches no longer fail lint before tests run.
- Kept queue project filter fallback behavior while removing unused exception binding in API path resolution.

## [0.1.71] - 2026-04-13

### Added
- `lerim status --live` now uses the same data and renderer as snapshot mode, with periodic refresh only as the difference.
- New status payload fields for richer operations visibility:
  - `projects[]` per-project memory/queue/blocker summary
  - `recent_activity[]` timeline including `sync` and `maintain`
  - `unscoped_sessions` totals by agent
- New `lerim unscoped` command to inspect indexed sessions that do not map to a registered project.
- Queue filters now support exact project matching (`--project`) and explicit substring matching (`--project-like`).

### Changed
- Status UI redesigned for clarity:
  - project stream table (`blocked` / `running` / `queued` / `healthy`)
  - explicit “What These Terms Mean” section
  - actionable “What To Do Next” section with full `lerim ...` commands
  - activity panel (sync + maintain)
- Read/query defaults now use all registered projects unless explicitly narrowed:
  - `lerim status --scope all|project --project ...`
  - `lerim ask --scope all|project --project ...`
  - `lerim query records list --scope all|project --project ...`
- Canonical run telemetry is now written in `service_runs.details_json` with normalized keys (`metrics_version=1`, sync/maintain totals, per-project metrics, events) while preserving legacy compatibility fields.

### Fixed
- Live status activity no longer appears stale during long in-flight sync runs; running queue jobs are now surfaced in `recent_activity`.
- Fixed maintain runtime error (`name 'index_path' is not defined`) that caused maintain runs to fail.

## [0.1.70] - 2026-03-28

### Quality Improvements
- **+41% composite quality score** via Layer 1 AutoResearch optimization
- ChainOfThought for the prior extraction pipeline (biggest single improvement)
- Explicit dedup classification thresholds (0.7/0.4) in sync prompt
- Improved MemoryCandidate schema field descriptions for better output consistency
- Tighter post-extraction body filter (30→50 chars minimum)

### Evaluation Infrastructure
- 4 new eval runners: dedup accuracy, maintain quality, search relevance (NDCG@5), tool selection
- LerimBench 7-dimension composite scoring with configurable weights
- Fuzzy title matching for dedup accuracy (substring + Jaccard similarity)
- Golden dataset support via `--golden-dir` flag
- Deterministic extraction and summarization assertion checkers

### Dashboard
- Local bundled dashboard removed — web UI moving to https://lerim.dev
- `lerim dashboard` shows transition message with CLI alternatives
- API server remains for Docker container health checks

### Cleanup
- Removed stale Codex tool references from ask prompt
- Cleaned up stale OAI SDK / ResponsesProxy references in internal docs

## [0.1.69] - 2026-03-25

### Breaking

- Removed PydanticAI dependency -- all agent operations now use a ReAct runtime.
- Removed explorer subagent — replaced by Codex filesystem sub-agent.
- Removed custom filesystem tools (read, write, edit, glob, grep) — Codex handles all filesystem ops.
- Removed `[roles.explorer]` config section (kept in default.toml for compatibility but unused).

### Added

- ReAct runtime with a provider-agnostic LM wrapper for multi-provider support (MiniMax, ZAI, OpenRouter, OpenAI, Ollama, MLX).
- Codex tool as intelligent filesystem sub-agent with kernel-level sandboxing.
- Unified `providers.py` -- all providers use the same LM wrapper path (no proxy layer needed).
- Cross-session intelligence in maintain: signal amplification, contradiction detection, gap detection.
- Cross-agent knowledge synthesis: detects patterns across Claude, Cursor, Codex, OpenCode sessions.
- Context curation with Active Decisions, Key Learnings, Recent Context, and Watch Out sections.
- Memory outcome field (worked/failed/unknown) for feedback tracking.
- Docker container hardening: read_only root, cap_drop ALL, seccomp profile, mount only .lerim/ dirs.
- Dashboard Intelligence tab: memory health score, contradictions, signals, gaps, cross-agent insights.

## [0.1.68] - 2026-03-21

### Added

- **Server readiness check on `lerim up`**: `cli.py` now polls `/api/health` for up to 30 seconds after starting the container, printing a clear warning if the server never responds.
- **`pytest-timeout`** added to the `[test]` optional dependency group for controlled test execution time.
- **`MINIMAX_API_KEY`** added to the environment-variable look-up list in the HTTP API.

### Fixed

- **Docker dashboard path**: `dashboard.py` resolves the dashboard directory correctly inside containers by falling back to `/opt/lerim/dashboard` when the repo-relative path does not exist. A corresponding `COPY dashboard/` step is added to the `Dockerfile`.
- **Test `pythonpath`**: `[tool.pytest.ini_options]` now includes `pythonpath = ["."]` so `from tests.helpers import ...` resolves when running `uv run pytest`.
- **HTTP API key list**: `_API_KEY_ENV_NAMES` is sorted alphabetically and now includes `ANTHROPIC_API_KEY` and `MINIMAX_API_KEY`.

## [0.1.66] - 2026-03-15

### Added

- **Parallelism support**: three config knobs control concurrent execution:
  - `max_workers` in `[roles.extract]`: parallel extraction window processing via ThreadPoolExecutor (each thread gets its own LM instance for thread safety).
  - `max_explorers` in `[roles.explorer]`: concurrent explorer subagent calls per lead turn.
  - `parallel_pipelines` in `[server]`: run extract + summarize pipelines in the same tool turn.
- **Async explore tool**: explorer subagent changed from sync (`run_sync`) to async (`await agent.run`), enabling true concurrent dispatch via PydanticAI's `asyncio.create_task`.
- **Adaptive prompts**: sync and maintain prompts now emit parallel or sequential instructions based on config values. Set knobs to `1`/`false` for local models.

### Removed

- `max_workers` from `[roles.summarize]` — summarization uses sequential refine/fold, so window parallelism does not apply.

## [0.1.60] - 2026-03-05

### Added

- **Ollama lifecycle management**: automatic model load/unload around sync and maintain cycles. Models are warm-loaded into GPU/RAM before each cycle and unloaded after (`keep_alive: 0`) to free 5-10 GB of memory between runs. Controlled by `auto_unload = true` in `[providers]`.
- **Proxy bridge support**: new proxy provider base URL in `[providers]` for routing PydanticAI OpenAI-format calls to Ollama's native API (enables thinking mode control).
- **Eval framework**: four eval pipelines (`extraction`, `summarization`, `sync`, `maintain`) with LLM-as-judge scoring, config-driven model comparison, and `bench_models.sh` multi-model benchmarking script.
- Eval configs for Ollama models (Qwen3.5 4B/9B, thinking/non-thinking) and MiniMax-M2.5 cloud baseline.
- Synthetic eval traces and judge prompt templates for all four pipelines.
- `evals/compare.py` for cross-config result comparison.
- `lerim skill install` command to copy skill files into agent directories.

### Fixed

- **Docker networking**: generated `docker-compose.yml` now includes `extra_hosts: host.docker.internal:host-gateway` so containers can reach Ollama running on the host.

### Changed

- Evals folder reorganized: active configs moved to `evals/configs/`, stale MLX configs removed.
- Default provider switched to MiniMax-M2.5 with Z.AI fallback.

## [0.1.53] - 2026-03-01

### Fixed

- Daemon loop: maintain never triggered on startup in Docker containers where `time.monotonic()` reflected VM uptime smaller than the maintain interval (60 min).
- Daemon loop: sync/maintain cycles produced zero log output, making `lerim logs` appear idle. Added per-cycle status logging.
- Session queue: NULL `repo_path` jobs clogged the claim queue, preventing valid sessions from being extracted. Added filter in `claim_session_jobs` and guard in `enqueue_session_job`.
- DB migration: orphaned NULL `repo_path` pending/failed jobs are now purged on schema init.
- Explorer subagent: switched from structured `ExplorerEnvelope` output to plain `str` to avoid repeated output-validation failures with models that return empty responses after tool calls.
- Explorer failures no longer crash the lead agent; the `explore` tool returns empty evidence and logs a warning.
- Maintain action path validation: handle list-valued `source_path`/`target_path` from LLM output (model sometimes returns multiple paths per action).
- `run_maintain_once` now accepts a `trigger` parameter instead of hardcoding `"manual"` for all service-run records.

## [0.1.5] - 2026-03-01

### Added

- Per-run LLM cost tracking via OpenRouter's `usage.cost` response field. Cost (USD) logged in `activity.log` and returned in sync/maintain/ask result payloads.
- Chronological (oldest-first) session processing for correct memory ordering.

### Changed

- Structured context-writing tool replaces raw file writes.
- `_process_claimed_jobs` runs sequentially (was parallel) for chronological memory consistency.
- Activity log format now includes cost column.

## [0.1.0] - 2026-02-28

### Added

- Docker service architecture: always-on daemon + HTTP API + dashboard in a single container.
- `lerim init` interactive setup wizard for first-time configuration.
- `lerim project add/list/remove` for incremental project registration.
- `lerim up/down/logs` for Docker container lifecycle management.
- `lerim serve` command — combined HTTP API + dashboard + daemon loop (Docker entrypoint, also usable directly without Docker).
- Service commands (`ask`, `sync`, `maintain`, `status`) are thin HTTP clients that talk to the running server.
- HTTP API: `/api/health`, `/api/ask`, `/api/sync`, `/api/maintain`, `/api/status`, `/api/connect`, `/api/project/*`.
- `[agents]`, `[projects]`, and `[providers]` config sections in `config.toml`.
- Provider API base URLs configurable via `[providers]` section (no more hardcoded URLs).
- `Dockerfile` with Python 3.12, health check, `lerim serve` entrypoint.
- Same-path volume mounting for zero path translation between host and container.
- Continual learning layer for coding agents and projects.
- Platform adapters for Claude Code, Codex CLI, Cursor, and OpenCode.
- Memory extraction pipeline using ChainOfThought with transcript windowing to extract decisions and learnings from coding session traces.
- Trace summarization pipeline using ChainOfThought with transcript windowing to produce structured summaries.
- PydanticAI lead agent with a read-only explorer subagent for memory operations.
- Three CLI flows: `sync` (extract, summarize, write memories), `maintain` (merge, archive, decay), and `ask` (query memories).
- Daemon mode for continuous sync and maintain loop.
- Local read-only web dashboard with HTTP API.
- Session catalog with SQLite FTS5 for session search.
- Job queue with stale job reclamation.
- TOML-layered configuration: shipped defaults, global, project, and env var override.
- OpenTelemetry tracing via Logfire with PydanticAI and runtime instrumentation.
- Multi-provider LLM support: OpenRouter (with Nebius routing), Ollama, ZAI, OpenAI, Anthropic.
- SQLite-backed context model for durable records and derived indexes.
- Project-scoped context records in the global context DB.
- Context record kinds: decisions, facts, procedures, preferences, and episodes.
- Comprehensive test suite with 290 tests across unit, smoke, integration, and e2e layers.
- Skills distribution via `npx skills add lerim-dev/lerim-cli`.
