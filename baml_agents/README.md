# Lerim BAML Agents

Minimal experiment for testing whether BAML improves ReAct-style tool selection
with a small local Ollama model.

## What This Uses

- LangGraph builds the graph loop.
- BAML makes the LLM call and parses the next action into a typed schema.
- Ollama serves `gemma4:e4b` through `http://127.0.0.1:11434/v1`.
- MiniMax M2.7 can also be used through BAML's OpenAI-compatible client
  registry with `--baml-provider minimax`.
- The BAML function copies Lerim's extraction `SYSTEM_PROMPT` text and keeps the
  small BAML/LangGraph harness adaptation in `baml_src/extract_react.baml`.
- Lerim's existing DB-backed extraction tools are imported from `src/lerim`.
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
  --project-root . \
  --model gemma4:e4b
```

MiniMax M2.7:

```bash
PYTHONPATH="baml_agents:src" uv run --with baml-py==0.222.0 --with langgraph==1.2.0 \
  python -m baml_extract_agent.run \
  --trace tests/fixtures/traces/unit/codex_simple.jsonl \
  --context-db baml_agents/.tmp/context_minimax.sqlite3 \
  --project-root . \
  --baml-provider minimax \
  --model MiniMax-M2.7 \
  --temperature 1.0
```

BAML-native tests live in `baml_src/extract_react.baml`:

```bash
uv run --with baml-py==0.222.0 baml-cli test --from baml_agents/baml_src --parallel 1 -i "DecideNextExtractStep::"
```

The graph writes into the context DB you pass with `--context-db`. Use a scratch
DB while comparing behavior.
