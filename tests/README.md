# Lerim Test Suite

## Summary

The maintained test surface is DB-first.

What we test:

- unit tests for config, adapters, store, CLI, API, daemon, runtime, and generated Context Brief
- smoke tests for quick real-LLM trace-ingestion sanity
- integration tests for real trace ingestion, context curation, context answering, context briefs, cloud ingest state, and multi-project scope flows
- integration tests for runtime orchestration behavior like workspace artifact layout, answer debug trace ordering, and mutation count reporting
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

## Case-based integration suites

The new live integration suites are behavior-first.

Shape:

- one folder per cluster under `tests/integration/`, for example:
  - `tests/integration/trace_ingestion/`
  - `tests/integration/context_curator/`
  - `tests/integration/answer/`
  - `tests/integration/agents/`
  - `tests/integration/runtime/`
  - `tests/integration/scope/`
  - `tests/integration/cloud/`
  - `tests/integration/queue/`
- one or more behavior-grouped test files inside each cluster folder
- one `helpers.py` per cluster when that cluster needs a runner/harness
- one expectation YAML per case under `tests/fixtures/expectations/<cluster>/`
- trace fixtures under `tests/fixtures/traces/trace_ingestion/` only when the agent truly works from a trace

Design rule:

- `trace_ingestion` cases are trace-driven
- `answer` and `context_curator` cases are mostly seeded-state-driven
- scope/runtime/cloud/queue clusters use the smallest real setup that exercises that behavior
- runtime cases cover generated Context Brief artifact layout, current-copy behavior, skip behavior, and empty-state generation

Some extract pressure cases generate a long trace dynamically instead of checking in a giant fixture. That is intentional. Use a generated trace when the test is about context pressure or pruning, not about exact transcript wording.

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

Subpackages: `agents/`, `adapters/`, `config/`, `context/`, `server/`, `sessions/`, `cloud/`, `skills/`.

Rules:

- one test file per source module
- test file names match source file names (without `_adapter`/`_functions` suffix)
- each subpackage has its own `conftest.py` with domain-specific fixtures
- agent tool tests also cover source-session provenance defaults so historical traces do not look freshly created when indexed later
- agent build tests guard the runtime tool contract against documentation and helper drift
- adapter tests cover compact-trace visibility for canonical message fields and structured event messages without keyword heuristics
- ingest persistence tests cover idempotent replay when a session episode already exists
- curate unit tests cover semantic clustering, action validation, and direct `ContextStore` mutation application
- session catalog tests cover queue claim availability, content-hash refresh/change detection, and stable pagination ordering
- API/daemon tests cover degraded status reporting when the session catalog is unavailable
- daemon tests cover transient session-job heartbeat write failures
- session catalog tests cover process-local active-job leases that avoid false stale queue health during transient heartbeat write failures
- daemon ingest tests cover one-at-a-time job claiming to avoid false stale-running queue state
- config tests cover provider capability validation, provider-specific model normalization, strict config parsing, and SDK log-noise filters
- Context Brief tests cover cwd project resolution, freshness counts, markdown citations, CLI local reads, and artifact writes without live LLM calls

## Testing rules

1. **Every public function gets a test.** No exceptions.
2. **Database operations get direct store tests.** Never test `ContextStore` methods only through agent tools.
3. **Validation paths are first-class.** Every `ValueError`, graph-visible model retry, and guard condition needs a dedicated test.
4. **One test file per source module.** Never split one module's tests across multiple files.
5. **Three layers: unit (no LLM/network, temp DB), integration (real LLM, real DB, `@pytest.mark.llm`), smoke (quick sanity).**
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
- local semantic retrieval via ONNX embeddings + `sqlite-vec` + FTS5 + RRF
- ingest graph: deterministic window reads, BAML trace observation, BAML durable-signal filtering, BAML context writing, context-store persistence
- curate graph: active-record inventory, semantic-neighbor clusters, BAML context-cluster review, BAML record-health review for records without prior cluster actions, validated store mutations
- answer flow: BAML retrieval planning, read-only `ContextStore` count/list/search execution, BAML answer synthesis

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
