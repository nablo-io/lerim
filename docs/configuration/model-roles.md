# Model Roles

Lerim uses one active model role today.

## `[roles.agent]`

This role powers:

- `sync` extraction orchestration
- `maintain`
- `ask`

Important fields:

- `provider`
- `model`
- `api_base`
- `fallback_models` (`"provider:model"` or an unqualified model that uses the primary provider)
- `temperature`
- `top_p`
- `top_k` (sent only for providers whose request API supports it; not sent to OpenAI)
- `max_tokens`
- `parallel_tool_calls`
- `max_iters_maintain`
- `max_iters_ask`
