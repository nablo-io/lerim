# Lerim Test Suite

## Summary

The maintained test surface is DB-first.

What we test:

- unit tests for config, adapters, store, tools, CLI, API, daemon, and runtime
- smoke tests for quick real-LLM extract sanity
- integration tests for real extract, maintain, semantic ask, cloud sync state, and multi-project scope flows
- integration tests for runtime orchestration behavior like workspace artifact layout, ask debug trace ordering, and mutation count reporting
- e2e surface tests for CLI/API rendering and deterministic query behavior

What we do not keep anymore:

- file-based smoke tests
- file-based index checks
- file-based extraction / maintain / ask flows

## Quick reference

```bash
tests/run_tests.sh unit
uv run pytest tests/unit -q
```

For live QA after runtime changes:

```bash
tests/run_tests.sh smoke
tests/run_tests.sh integration
```

## Case-based integration suites

The new live integration suites are behavior-first.

Shape:

- one folder per cluster under `tests/integration/`, for example:
  - `tests/integration/extract/`
  - `tests/integration/maintain/`
  - `tests/integration/ask/`
  - `tests/integration/runtime/`
  - `tests/integration/scope/`
  - `tests/integration/cloud/`
  - `tests/integration/queue/`
- one or more behavior-grouped test files inside each cluster folder
- one `helpers.py` per cluster when that cluster needs a runner/harness
- one expectation YAML per case under `tests/fixtures/expectations/<cluster>/`
- trace fixtures under `tests/fixtures/traces/extract/` only when the agent truly works from a trace

Design rule:

- `extract` cases are trace-driven
- `ask` and `maintain` cases are mostly seeded-state-driven
- scope/runtime/cloud/queue clusters use the smallest real setup that exercises that behavior

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

Subpackages: `agents/`, `adapters/`, `config/`, `context/`, `server/`, `sessions/`, `cloud/`, `transcripts/`, `skills/`.

Rules:

- one test file per source module
- test file names match source file names (without `_adapter`/`_functions` suffix)
- each subpackage has its own `conftest.py` with domain-specific fixtures

## Testing rules

1. **Every public function gets a test.** No exceptions.
2. **Database operations get direct store tests.** Never test `ContextStore` methods only through agent tools.
3. **Validation paths are first-class.** Every `ValueError`, `ModelRetry`, and guard condition needs a dedicated test.
4. **One test file per source module.** Never split one module's tests across multiple files.
5. **Three layers: unit (no LLM/network, temp DB), integration (real LLM, real DB, `@pytest.mark.llm`), smoke (quick sanity).**
6. **Mock external deps, not internal modules.** Use temp SQLite DBs instead of mocking ContextStore.
7. **When adding a new tool:** test happy path, validation failure, guard failure, edge case.
8. **When adding a store method:** test happy path, each validation error, idempotency, side effects (version/FTS/embedding).
9. **Keep tests independent.** Each test creates its own temp state. No shared mutable state.
10. **Reference source constants.** Use `MAX_RECORD_TITLE_CHARS + 1` not `121` in boundary tests.

## Architecture under test

The current system is:

- canonical durable context in `~/.lerim/context.sqlite3`
- canonical session catalog in `~/.lerim/index/sessions.sqlite3`
- canonical run artifacts in `~/.lerim/workspace/`
- local semantic retrieval via ONNX embeddings + `sqlite-vec` + FTS5 + RRF
- extract tools: `trace_read`, `search_records`, `fetch_records`, `create_record`, `update_record`, `note`, `prune`
- maintain tools: `list_records`, `search_records`, `fetch_records`, `update_record`, `archive_record`, `supersede_record`
- ask tools: `list_records`, `search_records`, `fetch_records`, `context_query`

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
- DB quality after sync and maintain

## Expectation files

Expectation YAML files are part of the contract for case-based suites.

Use them for:

- required and forbidden tools
- expected record counts or kinds
- answer or record content checks
- lifecycle expectations like archive vs supersede

Keep them behavior-shaped.

Do not encode accidental wording or prompt internals unless the behavior truly depends on that distinction.
