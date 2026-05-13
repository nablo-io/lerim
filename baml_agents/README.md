# Lerim BAML Agents

Minimal experiment for testing whether a BAML plus LangGraph windowed
extraction harness can replace the PydanticAI extract agent.

## What This Uses

- LangGraph owns trace windowing, coverage, synthesis, and persistence.
- BAML makes the two LLM calls: scan one trace window, then synthesize records.
- The default benchmark/runtime model is MiniMax M2.7 through BAML's
  OpenAI-compatible client.
- Ollama-compatible local models can still be used explicitly with
  `--baml-provider ollama`.
- Model clients live in `baml_src/models.baml`; extraction prompts and
  functions live in `baml_src/extract_react.baml`; BAML-native tests live in
  `baml_src/extract_react_tests.baml`.
- Lerim's existing DB-backed `save_context` tool persists synthesized records.
- The default model can be overridden with `--model` for local and API model
  comparisons.

## Run

From the `lerim-cli` repo root:

```bash
uv run --with baml-py==0.222.0 baml-cli generate --from baml_agents/baml_src
PYTHONPATH="baml_agents:src" uv run --with baml-py==0.222.0 --with langgraph==1.2.0 \
  python -m baml_extract_agent.run \
  --trace tests/fixtures/traces/unit/codex_simple.jsonl \
  --context-db baml_agents/.tmp/context.sqlite3 \
  --project-root .
```

Local Ollama-compatible model:

```bash
PYTHONPATH="baml_agents:src" uv run --with baml-py==0.222.0 --with langgraph==1.2.0 \
  python -m baml_extract_agent.run \
  --trace tests/fixtures/traces/unit/codex_simple.jsonl \
  --context-db baml_agents/.tmp/context_ollama.sqlite3 \
  --project-root . \
  --baml-provider ollama \
  --model <local-model-name>
```

BAML-native tests:

```bash
MINIMAX_API_KEY=... uv run --with baml-py==0.222.0 baml-cli test --from baml_agents/baml_src --parallel 1
```

The graph writes into the context DB you pass with `--context-db`. Use a scratch
DB while comparing behavior.
