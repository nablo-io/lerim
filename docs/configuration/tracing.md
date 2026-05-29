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
- **Agent events** -- ingest, curate, and Context Brief still write detailed
  local `agent_trace.json` files under the run workspace. These are the most
  detailed per-phase record today; MLflow currently shows the operation and
  named-agent boundaries rather than every pipeline step as its own nested span.
- **Retrieval actions** -- answerer retrieval planning and read-only context
  queries are recorded in the local debug trace when verbose answer output is enabled.
- **agent_trace.json** -- each ingest/curate run also writes local agent events
  under the run workspace. Answer debug output writes model and retrieval events.
- **Lerim run id correlation** -- each ingest/curate trace is tagged with
  `lerim.run_id`, and MLflow `client_request_id` is set to the same value used
  in the local run `manifest.json` and workspace folder name.

## Setup

MLflow ships as a Lerim dependency, so `pip install lerim` already includes the
client library. A common local setup is a small Docker Compose MLflow service
outside the Lerim repo, for example under `~/codes/personal/local-mlflow`.

!!! info "No account needed"
	The shared MLflow server is local. No authentication, external account, or
	API key is required. Each project uses the same tracking URL with a different
	experiment name.

## Enable tracing

Enable tracing for the long-running Lerim server process. Setting it only on a
client command like `lerim ingest` will not enable tracing for a server that is
already running.

MLflow has two separate roles in Lerim:

- **Lerim server writes traces.** This happens during `lerim serve` or the
  Docker service started by `lerim up`, when tracing is enabled in config.
- **Shared MLflow server stores and shows traces.** It must be running when
  `LERIM_MLFLOW_REQUIRED=1`; otherwise Lerim fails early instead of silently
  losing observability.

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
	MLFLOW_TRACKING_URI=http://127.0.0.1:5050
	LERIM_MLFLOW_EXPERIMENT=lerim
	LERIM_MLFLOW_REQUIRED=1
	```

	Restart the service after changing the file:

	```bash
	lerim up
	```

## Viewing traces

Start the shared MLflow server:

```bash
cd ~/codes/personal/local-mlflow
docker compose up -d --build
```

Then navigate to [http://127.0.0.1:5050](http://127.0.0.1:5050).

The server is both the trace writer target and the UI. If it is stopped and
`LERIM_MLFLOW_REQUIRED=1`, Lerim refuses to start traced work.

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
tracing is broken; check the Traces view or use the API check below.

!!! tip "Filtering"
	Use the MLflow search bar to filter traces by experiment name, tags, status,
	or text. This is useful when you have many ingest/curate cycles logged.

## Verify Logging

You do not need the UI to confirm that the shared server is reachable:

```bash
curl -s http://127.0.0.1:5050/api/2.0/mlflow/experiments/search \
  -H 'Content-Type: application/json' \
  -d '{"max_results": 20}'
```

You should see the `lerim` experiment after a traced run creates it.

## Local Run Artifacts

Each ingest or curate execution writes a local artifact bundle under:

```text
~/.lerim/workspace/YYYY/MM/DD/<ingest-or-curate>/<run_id>/
```

Important files:

- `manifest.json` -- run id, operation, project, session id, artifact paths, and
  status. `mlflow_client_request_id` matches the MLflow trace request id.
- `events.jsonl` -- compact started/succeeded/failed events for that run.
- `agent_trace.json` -- serialized pipeline, model, or retrieval events when available.
- `agent.log` -- short human-readable agent summary on success.
- `error.json` -- structured error details on failure.

## Notes

- Lerim reads `MLFLOW_TRACKING_URI`, `LERIM_MLFLOW_EXPERIMENT`, and
  `LERIM_MLFLOW_REQUIRED` from `.env` / shell.
- `[observability].mlflow_enabled = true` is the persistent switch for the server process.
- `LERIM_MLFLOW=true` is still supported as an environment override.
- If `MLFLOW_TRACKING_URI` is missing and strict mode is off, Lerim still has a
  legacy SQLite fallback under `~/.lerim/observability/mlflow.db`.
- Hidden provider chain-of-thought is not available to Lerim or MLflow unless a
  provider exposes it. Visible prompts, model responses, tool calls, tool
  results, timing, token metadata, and spans are the expected trace payload.

## Troubleshooting

If Lerim says MLflow is required but unavailable, start the shared server:

```bash
cd ~/codes/personal/local-mlflow
docker compose up -d --build
```

For the legacy SQLite fallback only: if `mlflow ui` reports an out-of-date or
unknown database revision, start Lerim once with tracing enabled. Lerim checks
the MLflow schema at startup and will upgrade compatible databases. If MLflow
cannot migrate the recorded revision, Lerim backs up the incompatible DB under
`~/.lerim/observability/backups/` and creates a fresh trace DB.
