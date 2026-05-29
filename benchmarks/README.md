# Lerim Benchmarks

Benchmark artifacts in this directory must be reproducible: runnable scripts,
raw JSON/JSONL outputs, generated Markdown reports, and enough metadata to
repeat the run.

## Start Here

| Path | Role |
| --- | --- |
| `../docs/benchmarks/index.md` | Public benchmark hub |
| `../docs/benchmarks/lerim-results.md` | Public Lerim-only results page |
| `../docs/benchmarks/market-comparison.md` | Public market comparison table |
| `lerim_evidence/` | Lerim benchmark runners |
| `competitors/` | Source-backed competitor importers |
| `results/raw/` | Numeric source-of-truth artifacts |
| `results/reports/` | Generated Markdown audit copies |
| `results/reports/index.md` | Generated index over public, non-ignored raw reports |

The intended reading path is:

1. Read `../docs/benchmarks/index.md` for the benchmark map and reporting
   rules.
2. Read `../docs/benchmarks/lerim-results.md` for current Lerim numbers,
   sources, and commands.
3. Read `../docs/benchmarks/market-comparison.md` for Lerim vs other memory
   systems.
4. Open `results/raw/<run>/report.json` when you need the numeric source of
   truth.
5. Use `results/reports/index.md` only as a generated audit index.

Do not add separate public comparison pages for individual competitors unless
there is a product-specific reason. Market comparisons should live in the
market comparison page and point back to raw artifacts.

## Lerim Benchmark Surfaces

The benchmark suite measures Lerim on first-party product claims: retrieval
usefulness, compactness, latency, trace submission, and integration plumbing.
Competitor research can inform which surfaces matter. Competitor rows are
allowed here only when backed by raw artifacts or repeatable commands, and the
report must say whether the baseline was rerun locally or pinned upstream.

### LongMemEval-S Retrieval

This is a retrieval-only benchmark. It is not the official LongMemEval QA score
and does not call an LLM judge.

Quick local development slice, not publishable benchmark evidence:

```bash
uv run python benchmarks/lerim_evidence/longmemeval.py \
  --limit 5 \
  --output-dir /tmp/lerim-longmemeval-slice
```

Full filtered run:

```bash
uv run python benchmarks/lerim_evidence/longmemeval.py \
  --retrieval-mode hybrid \
  --output-dir benchmarks/results/raw/longmemeval-hybrid-full
```

Convenience wrapper:

```bash
uv run python benchmarks/scripts/run_longmemeval_full.py
```

The output directory contains:

- `report.json`: full run metadata, summary metrics, and per-question results.
- `predictions.jsonl`: one scored prediction row per question.
- `report.md`: generated human-readable summary.

Default mode uses Lerim's hybrid local search path. To run the
SQLite FTS-only path:

```bash
uv run python benchmarks/lerim_evidence/longmemeval.py \
  --retrieval-mode lexical \
  --limit 5 \
  --output-dir /tmp/lerim-longmemeval-lexical-slice
```

### Context Budget

This uses LongMemEval-S haystacks and a Hugging Face tokenizer.
It compares loading every haystack session transcript against loading only the
top-K sessions retrieved by Lerim.

```bash
uv run python benchmarks/lerim_evidence/context_budget.py \
  --limit 5 \
  --output-dir /tmp/lerim-context-budget-slice
```

Full-run wrapper:

```bash
uv run python benchmarks/scripts/run_context_budget_full.py
```

### Retrieval Latency

This uses LongMemEval-S haystack sessions as the corpus and measures local
search p50/p90/p99 latency. It is not an HTTP daemon load test
yet.

```bash
uv run python benchmarks/lerim_evidence/retrieval_latency.py \
  --sizes 100,1000 \
  --query-count 25 \
  --iterations 3 \
  --output-dir /tmp/lerim-retrieval-latency
```

### Trace Ingestion Cost/Performance

This uses public LongMemEval-S haystack sessions as source-session inputs and
runs Lerim's DSPy ingestion path. It measures ingestion time, live LLM
call counts, and context DB file-size growth after schema initialization.
Provider cost stays `not available` unless the runtime exposes measured token
usage or billing data.

```bash
uv run python benchmarks/lerim_evidence/trace_ingestion_cost_performance.py \
  --limit 3 \
  --output-dir benchmarks/results/raw/trace-ingestion-cost-longmemeval-s-sample
```

The output directory contains:

- `report.json`: source-of-truth metrics and unavailable-cost disclosure.
- `details.jsonl`: one measured row per ingested public trace.
- `report.md`: generated human-readable summary.

### Market Baselines

Market baselines are a general comparison surface. Numeric competitor rows must
come from fresh local runs, pinned upstream raw artifacts, or cited public
reports with matching metric boundaries.

The first implemented competitor importer fetches one upstream raw benchmark
artifact set at a fixed commit and emits a normalized market-comparison report.
It is source-backed, but it is not a fresh local competitor rerun.

```bash
uv run python benchmarks/scripts/run_imported_market_baselines.py
```

### Extraction Quality

This imports an aggregate-only report from the internal 47-case extraction eval.
It keeps aggregate metrics only, and excludes raw traces, case identifiers,
per-case metrics, extracted record text, tool payloads, and judge details.

The current artifact is diagnostic evidence, not launch-grade. The source run
uses MiniMax M2.7 for extraction and MiniMax M2.5 for judging.
This is a first-party private eval. Do not add competitor extraction scores here
unless a competitor runner feeds the same traces into the other system and
scores that system's saved memories or context records with the same labels and
judge.

```bash
uv run python benchmarks/scripts/import_extraction_full.py
```

The output directory contains:

- `report.json`: aggregate-only metrics and metadata.
- `report.md`: generated human-readable summary.

### False-Positive Extraction

This derives a negative-case diagnostic from the same 47-case extraction eval.
It selects only cases labeled `negative` and measures whether Lerim avoids
creating durable records when the labeled source session has no durable signal.

The current diagnostic is deliberately not a market-comparison score.
Competitors have not been run on this private labeled eval, and the public
artifact excludes raw traces, extracted records, tool payloads, judge details,
case identifiers, per-case metrics, and forbidden concept text.

```bash
uv run python benchmarks/scripts/import_false_positive_extraction.py
```

The output directory contains:

- `report.json`: aggregate-only negative-case metrics and metadata.
- `report.md`: generated human-readable summary.

### MCP Integration

This audits every known Lerim MCP target using the real config writer and
validation code against temporary config paths. It also runs local stdio MCP tools-list and
`lerim_context_brief` call probes through the MCP client library when possible.
The default run also calls `lerim_trace_submit` on an idempotent duplicate trace
in an isolated temp config to validate submission and normalization plumbing
without spending extraction budget.

```bash
uv run python benchmarks/lerim_evidence/integration.py \
  --output-dir /tmp/lerim-mcp-integration
```

The output directory contains:

- `report.json`: source-of-truth summary, environment metadata, limitations,
  and detail rows.
- `details.jsonl`: one raw probe row per target/config check plus the stdio
  tools-list, context tool-call, and trace-submit probe rows.
- `report.md`: generated human-readable summary.

Temporary config fixtures are not real installed-agent acceptance. Use
`--include-real-doctor` only for read-only observation of local real config
paths; it still does not prove an external client can launch Lerim.
The stdio context tool-call probe validates Lerim's local MCP tool path, not that
an external installed client selected the tool.
The stdio trace-submit probe validates duplicate-path submission plumbing, not
trace-to-context extraction quality.

To also exercise the LLM extraction path through MCP, opt in to the live
trace-submit extraction probe. It submits a fresh completed trace through
`lerim_trace_submit`, runs MiniMax-backed DSPy ingestion, and passes only if
Lerim creates one episode record plus at least one durable record.

```bash
uv run python benchmarks/lerim_evidence/integration.py \
  --include-stdio-trace-submit-extraction \
  --stdio-extraction-timeout-seconds 300 \
  --output-dir /tmp/lerim-mcp-trace-submit-extraction
```

When rerunning live client probes, use `--installed-client-targets` or
`--tool-call-targets` with comma-separated target names to avoid spending model
budget on clients that already have fresh evidence.

Current public live-client acceptance artifact:

```bash
uv run python benchmarks/lerim_evidence/integration.py \
  --include-installed-client-probes \
  --installed-client-targets gemini-cli \
  --include-tool-call-probes \
  --tool-call-targets gemini-cli \
  --allow-live-client-tool-calls \
  --tool-call-timeout-seconds 120 \
  --max-tool-call-budget-usd 0.25 \
  --output-dir benchmarks/results/raw/mcp-gemini-live-tool-call
```

## Publishing Rules

- Do not publish a benchmark number without the raw `report.json` and generated
  `report.md`.
- Rerun public launch artifacts from a clean commit. Current development
  artifacts can still be useful engineering evidence while recording
  `git_dirty: true`.
- Mark partial runs as partial; only full runs should be compared publicly.
- Keep retrieval-only scores separate from extraction, QA, and judge-backed
  scores.
- Mark competitor extraction rows as `not run` unless they were produced by the
  same trace set, labels, judge, and scoring harness.
- Record git commit, dataset snapshot, model/provider, runtime, and failures.
- Run the public artifact validator before publishing or updating benchmark
  docs:

```bash
uv run python benchmarks/scripts/validate_public_artifacts.py
```

Before a launch-grade benchmark publish from a clean commit, run the stricter
variant:

```bash
uv run python benchmarks/scripts/validate_public_artifacts.py --require-clean
```
