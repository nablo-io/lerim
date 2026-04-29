# Contributing to Lerim

Lerim is licensed under BSL 1.1. By contributing you agree your changes
fall under the same license (1 user free, 2+ users need a commercial license).

## Dev environment setup

Requires Python 3.12+.

```bash
uv venv && source .venv/bin/activate
uv pip install -e '.[test]'
```

## Running tests

```bash
# Unit tests (no LLM keys needed)
tests/run_tests.sh unit

# Smoke tests (needs live agent provider + API key)
tests/run_tests.sh smoke

# Real integration tests (extract, maintain, ask)
tests/run_tests.sh integration

# End-to-end runtime flow
tests/run_tests.sh e2e

# Everything
tests/run_tests.sh all
```

Release note:

- `smoke`, `integration`, and `e2e` are real suites now.
- They must collect and run real tests.
- “No tests collected” is a failure, not a pass.

Lint before submitting:

```bash
ruff check src/ tests/
```

## Coding style

Full rules live in `docs/simple-coding-rules.md`. The short version:

- **Minimal code.** Prefer fewer functions, fewer layers, fewer lines.
- **Strict schemas.** Use Pydantic models / enums for inputs and outputs.
- **Fail fast.** No `try/except` fallbacks for missing packages. If something
  is required, let it raise.
- **Docstrings everywhere.** Every file gets a top-level docstring explaining
  what it contains. Every function gets a docstring.
- **Self-tests.** Add an `if __name__ == "__main__":` block that exercises the
  real code path (no mocking or stubbing). Keep it inline, not in a separate
  function.
- **No dead code.** When you replace logic, remove the old path.
- **Config from TOML, keys from env.** Runtime config comes from the TOML
  layer stack (`default.toml -> ~/.lerim/config.toml -> LERIM_CONFIG`).
  API keys and documented cloud overrides use environment variables.

## Adding a new platform adapter

This is the most common contribution. Follow these steps:

1. **Create `src/lerim/adapters/<platform>.py`.**
   Start with a top-level docstring. Implement the four functions required by
   the `Adapter` protocol in `src/lerim/adapters/base.py`:

   - `default_path() -> Path | None` -- where traces live on disk.
   - `count_sessions(path) -> int`
   - `iter_sessions(traces_dir, start, end, known_run_ids) -> list[SessionRecord]`
   - `find_session_path(session_id, traces_dir) -> Path | None`
   - `read_session(session_path, session_id) -> ViewerSession | None`

   See an existing adapter (e.g. `codex.py` or `claude.py`) as a reference.

2. **Register the adapter** in `src/lerim/adapters/registry.py`:
   add an entry to `_ADAPTER_MODULES` and optionally to `_AUTO_SEED_PLATFORMS`.

3. **Add a self-test** (`if __name__ == "__main__":` block) at the bottom of
   the new adapter file that exercises the real code path.

4. **Add unit tests** in `tests/test_<platform>_adapter.py`. Look at the
   existing adapter test files for the expected patterns.

5. **Update `tests/README.md`** if you added new fixtures or test infrastructure.

## Reporting bugs

Open a GitHub issue with:

- Steps to reproduce.
- Expected vs actual behavior.
- Lerim version (`python -m lerim --version`), Python version, and OS.
- Relevant config (redact API keys).

## Pull request checklist

- [ ] `ruff check src/ tests/` passes with no errors.
- [ ] `tests/run_tests.sh unit` passes.
- [ ] Run the relevant live suite when runtime, agent tools, prompts, or DB behavior change.
- [ ] New/changed files have top-level docstrings and function docstrings.
- [ ] New source files include an `if __name__ == "__main__":` self-test when practical.
- [ ] No mocking or stubbing in self-test flows.
- [ ] Related docs updated if behavior changed.
