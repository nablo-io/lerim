# Model Roles

Lerim uses one active model role today.

## `[roles.agent]`

This role powers:

- `ingest` extraction orchestration
- `curate`
- `answer`

Important fields:

- `provider`
- `model`
- `api_base`
- `temperature`
- `curate_max_llm_calls`
- `answer_max_retrieval_actions`
