# config.toml Reference

This page documents the current DB-only config shape.

## Minimal user override

Most users only need to override the keys they want to change:

```toml
[roles.agent]
provider = "minimax"
model = "MiniMax-M2.7"
```

## Complete reference shape

```toml
[data]
dir = "~/.lerim"
# Optional; defaults to dir/context.sqlite3.
# context_db_path = "~/.lerim/context.sqlite3"

[server]
host = "127.0.0.1"
port = 8765
sync_interval_minutes = 30
maintain_interval_minutes = 60
sync_window_days = 7
sync_max_sessions = 50

[semantic_search]
embedding_model_id = "mixedbread-ai/mxbai-embed-xsmall-v1"
# Optional; defaults to dir/models/embeddings.
# embedding_cache_dir = "~/.lerim/models/embeddings"
semantic_shortlist_size = 40
lexical_shortlist_size = 40

[observability]
mlflow_enabled = false

[roles.agent]
provider = "minimax"
model = "MiniMax-M2.7"
api_base = ""
fallback_models = []
temperature = 1.0
top_p = 0.95
top_k = 40
max_tokens = 32000
parallel_tool_calls = true
max_iters_maintain = 50
max_iters_ask = 20

[providers]
minimax = "https://api.minimax.io/v1"
minimax_anthropic = "https://api.minimax.io/anthropic"
zai = "https://api.z.ai/api/coding/paas/v4"
openai = "https://api.openai.com/v1"
openrouter = "https://openrouter.ai/api/v1"
opencode_go = "https://opencode.ai/zen/go/v1"
ollama = "http://127.0.0.1:11434"
mlx = "http://127.0.0.1:8000/v1"
auto_unload = true

[cloud]
endpoint = "https://api.lerim.dev"
# token is usually set by `lerim auth` or LERIM_CLOUD_TOKEN.

[agents]
# claude = "~/.claude/projects"
# codex = "~/.codex/sessions"
# cursor = "~/Library/Application Support/Cursor/User/globalStorage"
# opencode = "~/.local/share/opencode"

[projects]
# my-project = "~/codes/my-project"
```

## Notes

- `dir` is the global Lerim root
- `context_db_path` is optional; default is `dir/context.sqlite3`
- `[semantic_search]` configures local ONNX embeddings, the embedding cache directory, and the semantic/lexical candidate counts used before RRF fusion
- `[observability].mlflow_enabled` enables local MLflow tracing for the long-running server process
- there is one active model role today: `[roles.agent]`
- API keys come from environment variables, not TOML
- `fallback_models` entries can be `"provider:model"` or, when you want the primary provider, just `"model"`
- OpenRouter fallback model IDs may include colon suffixes, for example `"deepseek/deepseek-r1-0528:free"`
- `top_k` is only sent to providers whose request API supports it; it is not sent to OpenAI
