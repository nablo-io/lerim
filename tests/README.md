# Lerim Test Suite

## Summary

The maintained test surface is DB-first.

`integration` and `llm` are separate markers:

- `integration` means the test crosses a real runtime/API/database/filesystem boundary and is gated by `LERIM_INTEGRATION`.
- `llm` means the test makes a real provider/model call.
- `agent` is only a filter for live agent-flow tests; folder names still carry the domain ownership.

What we test:

- unit tests for config, adapters, store, CLI, API, daemon, runtime, benchmarks, generated Context Brief, Working Memory, and Run Clinic
- unit tests for release metadata preflight checks that gate package publishing
- smoke tests for quick real-LLM trace-ingestion sanity
- integration tests for real trace ingestion, context curation, context answering, context briefs, working memory, cloud ingest state, and multi-project scope flows
- integration tests for runtime/API/DB boundaries like workspace artifact layout, queue processing, custom project ingestion, memory reset, and cloud sync state
- e2e surface tests for CLI/API rendering and deterministic query behavior
- deterministic query tests preserve the difference between unscoped queries and empty project selections

## Quick reference

```bash
uv run tests/run_tests.sh unit
uv run pytest tests/unit -q
```

For live QA after runtime changes:

```bash
uv run tests/run_tests.sh smoke
uv run tests/run_tests.sh integration
```

## Debug Entry Points

Each LLM-backed agent folder can include `test_debug_entrypoint.py`: one focused
case that is easy to run from VS Code with F5. These files live beside the
behavior tests for the same agent. Use the matching `Agent: ... Real LLM` launch
configuration, or open one of these files and run `Pytest: Current File`.

These tests run real DSPy code, real SQLite writes, and real provider
calls, but they isolate state in temporary directories. They do not read or
mutate your normal `~/.lerim/context.sqlite3`. Provider/API-key settings must be
available through environment variables or an explicit `LERIM_CONFIG`; pytest
does not load the project `.env` for these runs.

```bash
LERIM_INTEGRATION=1 uv run pytest tests/integration/trace_ingestion/test_debug_entrypoint.py -q -s
LERIM_INTEGRATION=1 uv run pytest tests/integration/context_curator/test_debug_entrypoint.py -q -s
LERIM_INTEGRATION=1 uv run pytest tests/integration/context_graph/test_debug_entrypoint.py -q -s
LERIM_INTEGRATION=1 uv run pytest tests/integration/context_answerer/test_debug_entrypoint.py -q -s
LERIM_INTEGRATION=1 uv run pytest tests/integration/context_brief/test_debug_entrypoint.py -q -s
```

For Context Brief runs, inspect the generated `agent_trace.json` in the test's
temporary workspace path printed by pytest/debugger state if you need to follow
the runtime graph after the F5 session pauses.

## Case-based integration suites

Integration suites are behavior-first. Some use real providers and are also
marked `llm`; others exercise runtime, API, queue, cloud, or database boundaries
with deterministic test doubles for the provider call.

Shape:

- one folder per cluster under `tests/integration/`, for example:
  - `tests/integration/trace_ingestion/`
  - `tests/integration/context_curator/`
  - `tests/integration/context_graph/`
  - `tests/integration/context_answerer/`
  - `tests/integration/context_brief/`
  - `tests/integration/runtime/`
  - `tests/integration/project_ingestion/`
  - `tests/integration/scope/`
  - `tests/integration/cloud/`
  - `tests/integration/queue/`
  - `tests/integration/memory/`
- one or more behavior-grouped test files inside each cluster folder
- behavior files use names like `test_retrieval.py`, `test_project_scope.py`, or `test_orchestration.py`; do not add generic `test_cases.py` files
- one `helpers.py` per cluster when that cluster needs a runner/harness
- one expectation YAML per case under `tests/fixtures/expectations/<contract>/`; most contracts match cluster names, while context-answerer cases use the established `answer` contract folder
- trace fixtures under `tests/fixtures/traces/trace_ingestion/` only when the agent truly works from a trace

Design rule:

- `trace_ingestion` cases are trace-driven
- `context_answerer` and `context_curator` cases are mostly seeded-state-driven
- `test_debug_entrypoint.py` keeps one real-LLM debugger entry point in each LLM-backed agent folder
- scope/runtime/cloud/queue clusters use the smallest real setup that exercises that behavior
- runtime cases cover generated Context Brief, Working Memory, and Run Clinic artifact layout, current-copy behavior, skip behavior, and empty-state generation

Some trace-ingestion pressure cases generate a long trace dynamically instead of checking in a giant fixture. That is intentional. Use a generated trace when the test is about context pressure or pruning, not about exact transcript wording.

## Stability rule

Real-agent behavior cases are not done when they pass once.

Rule:

- add one behavior case
- run it against the real agent
- if it fails, fix the system or the test shape at the right abstraction level
- once green, rerun the new case at least `3x`
- if it is still flaky, keep iterating before adding the next case

For grouped live suites, rerun the affected cluster after the focused case is stable.

## Unit test structure

Unit tests live in `tests/unit/` and mirror the source tree. Each `src/lerim/<package>/` has a corresponding `tests/unit/<package>/` with one test file per source module.

Subpackages: `agents/`, `adapters/`, `benchmarks/`, `config/`, `context/`, `integrations/`, `server/`, `sessions/`, `cloud/`, `skills/`.

Rules:

- one test file per source module
- test file names match source file names (without `_adapter`/`_functions` suffix)
- each subpackage has its own `conftest.py` with domain-specific fixtures
- agent tool tests also cover source-session provenance defaults so historical traces do not look freshly created when indexed later
- agent build tests guard the runtime tool contract against documentation and helper drift
- adapter tests cover compact-trace visibility for canonical message fields and structured event messages without keyword heuristics
- ingest persistence tests cover idempotent replay when a session episode already exists, and the durable-record seed collection (`load_session_durable_record_ids`) that write-time reconciliation reviews
- curate unit tests cover semantic clustering, action validation, and direct `ContextStore` mutation application
- reconcile-on-write tests cover the scoped seed-plus-neighbor inventory, the write-time supersede that populates `valid_until`/`superseded_by_record_id` and drops the retired record from current retrieval, the protected-seed guard that stops a new record retiring itself, the scoped curator pass skipping single-record health review, and the ingestion trigger firing over the new durable ids (and skipping scope-only, empty, incomplete, and offline runs)
- session catalog tests cover queue claim availability, legacy schema migration, derived FTS rebuilds, content-hash refresh/change detection, and stable pagination ordering
- session catalog tests cover retrying both failed and dead-letter queue jobs without display pagination limits, including project child paths
- dashboard HTTP tests cover project-scoped sessions under root and child paths, full-window source agent options for runs and search, scoped run detail/messages, scoped record detail, stats, search, legacy search schema migration, dashboard processing-status filters, requested session sort order, status-aware record filters, structured log filtering, graph project switching, all-project graph membership, registered-project graph bounds, read-only graph queries, full and partial graph record fallback, missing graph projection-table fallback, equivalent project aliases, invalid-project errors, degraded refine reports, and generated artifact history filtering
- API/daemon tests cover status/query project shorthand, degraded status reporting when the session catalog is unavailable, project-scoped status latest/schedule fields, current-active versus archived/total record counts, all-status query normalization, text-filtered record queries, project-scoped empty answer selections, and project-scoped session/queue/reset counts under child paths
- server CLI tests cover the dashboard launcher contract, including backend startup checks and the Next.js dev command
- server Docker compose tests cover GHCR startup, local build source-root resolution, no-build local image reuse, and generated compose hardening
- daemon tests cover transient session-job heartbeat write failures
- session catalog tests cover process-local active-job leases that avoid false stale queue health during transient heartbeat write failures
- daemon ingest tests cover one-at-a-time job claiming to avoid false stale-running queue state
- config tests cover provider capability validation, provider-specific model normalization, strict config parsing, MLflow env/tracing configuration, and SDK log-noise filters
- cloud tests cover local dashboard auth verification through host-to-local endpoint fallbacks
- profile tests cover bundled signal packs, registered custom YAML profiles, and project-level default source profiles
- Context Brief tests cover cwd project resolution, fixed-section kind cleanup, freshness counts, markdown citations, CLI local reads, and artifact writes without live LLM calls
- Working Memory tests cover separate artifact paths, continuation-handoff rendering, superseded-record replacement rendering, freshness counts, CLI local reads, and artifact writes without live LLM calls
- Run Clinic tests cover diagnostic artifact paths, active versus archived evidence totals, current report writes, freshness metadata, CLI local reads, and artifact writes without live LLM calls
- MCP integration tests cover client config writers, dry-run/backup behavior, exposed MCP tool registration, in-process context search/brief calls, and trace-submit importer routing
- Context store search tests cover derived-index generation metadata, fast-path retrieval, and stale-index repair without live LLM calls
- Benchmark doc tests cover README launch links and visual references, duplicate demo media, public benchmark table values, artifact-path wording, and positioning guardrails for non-coding workflows
- Skill stewardship tests cover artifact scanning, target registration refresh, project-scoped candidate loading, auto-apply guard policy, proposal lifecycle guards, proposal validation, and stale/partial apply safety

## Testing rules

1. **Every public function gets a test.** No exceptions.
2. **Database operations get direct store tests.** Never test `ContextStore` methods only through agent tools.
3. **Validation paths are first-class.** Every `ValueError`, graph-visible model retry, and guard condition needs a dedicated test.
4. **One test file per source module.** Never split one module's tests across multiple files.
5. **Three layers: unit (no LLM/network, temp DB), integration (real runtime/API/DB/provider boundary, gated by `LERIM_INTEGRATION`, with `@pytest.mark.llm` when a test calls a provider), smoke (quick real-LLM sanity).**
6. **Mock external deps, not internal modules.** Use temp SQLite DBs instead of mocking ContextStore.
7. **When adding a new tool:** test happy path, validation failure, guard failure, edge case.
8. **When adding a store method:** test happy path, each validation error, idempotency, canonical side effects, and best-effort derived index refresh (FTS/embedding).
9. **Keep tests independent.** Each test creates its own temp state. No shared mutable state.
10. **Reference source constants.** Use `MAX_RECORD_TITLE_CHARS + 1` not `121` in boundary tests.

## Architecture under test

The current system is:

- canonical durable context in `~/.lerim/context.sqlite3`
- canonical session catalog in `~/.lerim/index/sessions.sqlite3`
- canonical run artifacts in `~/.lerim/workspace/`
- generated Context Brief artifacts in `~/.lerim/workspace/current/<project_id>/CONTEXT_BRIEF.md`
- generated Working Memory artifacts in `~/.lerim/workspace/current/<project_id>/WORKING_MEMORY.md`
- generated Run Clinic artifacts in `~/.lerim/workspace/current/<project_id>/RUN_CLINIC.md` and `RUN_CLINIC.report.json`
- local semantic retrieval via ONNX embeddings + `sqlite-vec` + FTS5 + RRF
- ingest graph: deterministic window reads, model trace observation, model durable-signal filtering, model context writing, context-store persistence
- curate graph: active-record inventory, semantic-neighbor clusters, model context-cluster review, model record-health review for records without prior cluster actions, validated store mutations
- reconcile-on-write: after ingest persists durable records, a scoped curate pass over just those seeds plus their active semantic neighbors supersedes replaced records at write time, protecting the just-written seeds and skipping single-record health review
- context graph: active-record inventory, semantic candidate pairs, model link/review steps, semantic cluster persistence, context graph shipping
- answer flow: model retrieval planning, read-only `ContextStore` count/list/search execution, model answer synthesis

## Fixtures

Shared fixtures live in `tests/conftest.py`.

Main ones:

- `tmp_lerim_root` — temporary global Lerim root
- `tmp_config` — config pointing at that root
- `live_lerim_root` — temporary isolated global root for real LLM suites
- `live_config` — current provider/model config copied into that isolated root
- `live_repo_root` — temporary project root for live runtime flows
- `live_runtime` — runtime bound to the isolated root and temp project
- `TRACES_DIR` — normalized trace fixtures for supported adapters

Live QA helpers live in `tests/live_helpers.py`.
They audit:

- schema exactness
- dead forbidden tables
- agent tool use from `agent_trace.json`
- DB quality after ingest and curate

## Expectation files

Expectation YAML files are part of the contract for case-based suites.

Use them for:

- required and forbidden tools
- expected record counts or kinds
- answer or record content checks
- lifecycle expectations like archive vs supersede

Keep them behavior-shaped.

Do not encode accidental wording or prompt internals unless the behavior truly depends on that distinction.

For answer cases, prefer support-boundary assertions over total tool bans: exact time/current queries should prove narrowing happens before synthesis, zero-result windows should prove no later retrieval widens scope, and semantic cases should prove the returned fetched support is sufficient. Only ban tools when the ban is the behavior under test, such as deterministic count questions avoiding semantic retrieval.

For curate cases, no-churn expectations should assert that useful records keep their content and receive no mutation rows. Do not require rewrite churn when a concise durable record already has clear typed fields.
