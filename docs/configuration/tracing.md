# Tracing

Lerim uses [MLflow](https://mlflow.org) for agent observability.
Tracing is opt-in and controlled by `[observability].mlflow_enabled` in
`~/.lerim/config.toml`. The `LERIM_MLFLOW` environment variable can override it
for one-off runs.

## What gets traced

When tracing is enabled, MLflow records:

- **Runtime operations** -- ingest, curate, answer, and Context Brief emit root
  `lerim.<operation>` traces tagged with run id, session id, project id, and
  workspace artifact paths.
- **Named agent spans** -- trace ingestion, context curation, context answering,
  and context-brief compilation emit `lerim.agent.<name>` spans when they run
  inside a traced runtime operation.
- **Graph events** -- ingest, curate, and Context Brief still write detailed
  local `agent_trace.json` files under the run workspace. These are the most
  detailed per-phase record today; MLflow currently shows the operation and
  named-agent boundaries rather than every graph node as its own nested span.
- **Retrieval actions** -- answerer retrieval planning and read-only context
  queries are recorded in the local debug trace when verbose answer output is enabled.
- **agent_trace.json** -- each ingest/curate run also writes local graph events
  under the run workspace. Answer debug output writes BAML and retrieval events.
- **Lerim run id correlation** -- each ingest/curate trace is tagged with
  `lerim.run_id`, and MLflow `client_request_id` is set to the same value used
  in the local run `manifest.json` and workspace folder name.

## Setup

MLflow ships as a Lerim dependency, so `pip install lerim` already includes it.

!!! info "No account needed"
	Lerim writes traces to a local SQLite DB. No authentication,
	no external account, and no API keys required. Everything stays on your machine.

## Enable tracing

Enable tracing for the long-running Lerim server process. Setting it only on a
client command like `lerim ingest` will not enable tracing for a server that is
already running.

MLflow has two separate roles in Lerim:

- **Lerim server writes traces.** This happens during `lerim serve` or the
  Docker service started by `lerim up`, when tracing is enabled in config.
- **MLflow UI reads traces.** `mlflow ui` only starts a local web viewer for the
  SQLite trace database. It does not need to be running while ingest/curate
  jobs execute, and it does not cause Lerim to log anything.

=== "config.toml"

	Add this to `~/.lerim/config.toml`:

	```toml
	[observability]
	mlflow_enabled = true
	```

	Restart the service after changing the file:

	```bash
	lerim up
	```

=== "Environment variable"

	Start the server with tracing enabled:

	```bash
	LERIM_MLFLOW=true lerim serve
	```

=== ".env file override"

	Persistent toggle for `lerim serve` and `lerim up` in `~/.lerim/.env`:

	```bash
	LERIM_MLFLOW=true
	```

	Restart the service after changing the file:

	```bash
	lerim up
	```

## Viewing traces

Lerim stores trace data in `~/.lerim/observability/mlflow.db` (SQLite).
Start the MLflow UI pointed at that database:

```bash
mlflow ui --backend-store-uri sqlite:///$HOME/.lerim/observability/mlflow.db
```

Then navigate to [http://localhost:5000](http://localhost:5000).

When working from the source checkout, prefer the locked project environment so
the UI uses the same MLflow schema version as Lerim:

```bash
uv run mlflow ui --backend-store-uri sqlite:///$HOME/.lerim/observability/mlflow.db
```

The UI command is only a viewer. You can stop it without stopping tracing;
Lerim continues writing traces as long as the server is running with
`mlflow_enabled = true`.

In the UI, look for:

- **Experiments** -- select the `lerim` experiment.
- **Traces** -- the primary view for Lerim operation and agent spans. Expand a
  trace to see named spans such as `lerim.agent.trace_ingestion`,
  `lerim.agent.context_curator`, `lerim.agent.context_answerer`, or
  `lerim.agent.context_brief_compiler`.
- **Run id** -- match a local run folder to MLflow by searching for the
  `manifest.json` `run_id` value. It is also stored as `client_request_id` and
  the `lerim.run_id` tag.
- **Model labels and inputs** -- agent spans include model/scope inputs where
  the runtime has them.
- **Local graph detail** -- for per-node graph events, open the matching
  run folder's `agent_trace.json`.

Classic MLflow **Runs** may be empty for agent traces. That does not mean
tracing is broken; check the Traces view or verify the SQLite counts below.

!!! tip "Filtering"
	Use the MLflow search bar to filter traces by experiment name, tags, status,
	or text. This is useful when you have many ingest/curate cycles logged.

## Verify Logging

You do not need the UI to confirm that Lerim is logging. From the source
checkout, inspect the trace tables directly:

```bash
uv run python -c "import sqlite3, pathlib; p=pathlib.Path.home()/'.lerim/observability/mlflow.db'; con=sqlite3.connect(p); print('trace_info', con.execute('select count(*) from trace_info').fetchone()[0]); print('spans', con.execute('select count(*) from spans').fetchone()[0])"
```

You should see `trace_info` and `spans` counts increase while ingest, curate,
answer, or Context Brief work runs.

## Local Run Artifacts

Each ingest or curate execution writes a local artifact bundle under:

```text
~/.lerim/workspace/YYYY/MM/DD/<ingest-or-curate>/<run_id>/
```

Important files:

- `manifest.json` -- run id, operation, project, session id, artifact paths, and
  status. `mlflow_client_request_id` matches the MLflow trace request id.
- `events.jsonl` -- compact started/succeeded/failed events for that run.
- `agent_trace.json` -- serialized graph, BAML, or retrieval events when available.
- `agent.log` -- short human-readable agent summary on success.
- `error.json` -- structured error details on failure.

## Notes

- Lerim configures MLflow tracking to a local SQLite store (`~/.lerim/observability/mlflow.db`).
- `[observability].mlflow_enabled = true` is the persistent switch for the server process.
- `LERIM_MLFLOW=true` is still supported as an environment override.
- The UI command can be run later, after the traces were already recorded.
- Hidden provider chain-of-thought is not available to Lerim or MLflow unless a
  provider exposes it. Visible prompts, model responses, tool calls, tool
  results, timing, token metadata, and spans are the expected trace payload.

## Troubleshooting

If `mlflow ui` reports an out-of-date or unknown database revision, start Lerim
once with tracing enabled. Lerim checks the MLflow schema at startup and will
upgrade compatible databases. If MLflow cannot migrate the recorded revision,
Lerim backs up the incompatible DB under `~/.lerim/observability/backups/` and
creates a fresh trace DB.
