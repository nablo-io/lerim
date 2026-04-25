# Tracing

Lerim uses [MLflow](https://mlflow.org) for PydanticAI agent observability.
Tracing is opt-in and controlled by the `LERIM_MLFLOW` environment variable.

## What gets traced

When tracing is enabled, MLflow records:

- **PydanticAI model calls** -- via `mlflow.pydantic_ai.autolog()`, every language model invocation
  across sync/maintain/ask flows is captured automatically, including
  input prompts, outputs, token counts, and latency.
- **Agent/tool executions** -- tool calls and agent steps are traced as nested spans within each run.
- **agent_trace.json** -- each sync/maintain run also writes a local
  `agent_trace.json` under the run workspace for a full tool/message history
  (not MLflow-specific).

## Setup

MLflow ships as a Lerim dependency, so `pip install lerim` already includes it.

!!! info "No account needed"
	Lerim writes traces to a local SQLite DB. No authentication,
	no external account, and no API keys required. Everything stays on your machine.

## Enable tracing

Set `LERIM_MLFLOW=true` for the long-running Lerim server process. Setting it
only on a client command like `lerim sync` will not enable tracing for a server
that is already running.

=== "Environment variable"

	Start the server with tracing enabled:

	```bash
	LERIM_MLFLOW=true lerim serve
	```

=== ".env file"

	Persistent toggle for `lerim serve` and `lerim up` in `~/.lerim/.env`:

	```bash
	LERIM_MLFLOW=true
	```

	Restart the service after changing the file:

	```bash
	lerim up
	```

## Viewing traces

Start the MLflow UI and open your browser:

```bash
mlflow ui
```

Then navigate to [http://localhost:5000](http://localhost:5000). You'll see:

- **Runs** -- each sync or maintain cycle appears as a separate run with
  parameters, metrics, and artifacts.
- **Traces** -- expand a run to see the full trace tree of model calls.
- **Model calls** -- every PydanticAI model request is logged with input prompts,
  outputs, token counts, and latency.
- **Spans** -- nested spans show the call hierarchy from the top-level
  orchestration down to individual LM calls and tool invocations.

Lerim stores trace data in `~/.lerim/observability/mlflow.db` (SQLite).
If you run `mlflow ui` from any directory, you can point it explicitly:

```bash
mlflow ui --backend-store-uri sqlite:///$HOME/.lerim/observability/mlflow.db
```

!!! tip "Filtering"
	Use the MLflow search bar to filter runs by experiment name, tags, or
	parameters. This is useful when you have many sync/maintain cycles logged.

## Notes

- Lerim configures MLflow tracking to a local SQLite store (`~/.lerim/observability/mlflow.db`).
- `LERIM_MLFLOW=true` is the main switch to enable or disable tracing for the server process.
