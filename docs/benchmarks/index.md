# Benchmarks

This is the public benchmark hub for Lerim.

The rule is simple: public numbers must point to raw artifacts or cited external
sources. Generated report copies are kept for auditability, but raw `report.json`
files are the source of truth for Lerim numbers.

Launch-grade benchmark artifacts should be rerun from a clean commit and pass
the clean/tracked public benchmark gate. The `v0.3.0` public artifacts passed
that release gate. Future artifacts with `git_dirty: true` should still be
treated as pre-release evidence until rerun after commit.

## Start Here

| Page | Use it for |
| --- | --- |
| [Benchmark Suite](benchmark-suite.md) | Plain-English explanation of each benchmark surface and boundary |
| [Lerim Results](lerim-results.md) | Detailed Lerim-only benchmark results, raw artifact references, commands, and boundaries |
| [Market Comparison](market-comparison.md) | Lerim vs other memory systems, with normalized rows, cited external numbers, and watchlist rows kept separate |

Generated reports live under `benchmarks/results/reports/` as audit copies.
Use the two pages above for the public reading path; use generated reports when
you need to trace a table back to raw artifacts.

Raw artifacts are tracked in this repo under `benchmarks/results/raw/`.
Generated audit copies are tracked under `benchmarks/results/reports/`.
Those paths must be included in the release commit before public benchmark links
are treated as launch-grade evidence.

## Artifact Map

| Path | Purpose | How to read it |
| --- | --- | --- |
| `docs/benchmarks/index.md` | Public benchmark hub | Start here |
| `docs/benchmarks/benchmark-suite.md` | Benchmark surface explanations | Use when learning what each benchmark means |
| `docs/benchmarks/lerim-results.md` | Public Lerim-only results | Use for first-party claims |
| `docs/benchmarks/market-comparison.md` | Public market comparison | Use for competitor/market claims |
| `benchmarks/lerim_evidence/` | Lerim benchmark runners | Code that produces Lerim numbers |
| `benchmarks/competitors/` | Source-backed competitor importers | Competitor evidence normalization, not product code |
| `benchmarks/results/raw/` | Raw benchmark artifacts | Numeric source of truth |
| `benchmarks/results/reports/` | Generated Markdown reports | Audit copies generated from raw artifacts |

Do not edit numbers by hand in docs. Change the runner, rerun it, then update
the generated artifacts.

## Current Evidence

| Surface | Current evidence | Where to read |
| --- | --- | --- |
| LongMemEval-S retrieval | Full 500-question retrieval-only runs for hybrid and lexical modes | [Lerim Results](lerim-results.md#longmemeval-s-retrieval) |
| Context budget | Full 500-question context-selection run using a Hugging Face tokenizer | [Lerim Results](lerim-results.md#context-budget) |
| Retrieval latency | Partial local scale run on LongMemEval-S sessions | [Lerim Results](lerim-results.md#retrieval-latency) |
| Trace ingestion cost/performance | Small public-trace sample with measured LLM calls and unavailable-cost disclosure | [Lerim Results](lerim-results.md#trace-ingestion-costperformance) |
| MCP integration | Config validation, local stdio MCP probes, trace-submit idempotency, 0 trace-submit extraction acceptances, and a Gemini CLI live tool-call acceptance artifact | [Lerim Results](lerim-results.md#mcp-integration) |
| Extraction quality | Aggregate-only 47-case diagnostic report from a `MiniMax-M2.7` agent artifact judged by `MiniMax-M2.5`; not launch-grade | [Lerim Results](lerim-results.md#extraction-eval-status) |
| Market comparison | Source-backed market table with comparable and not-yet-comparable rows separated | [Market Comparison](market-comparison.md) |

## Surface Map

| Surface | Public question answered | Current evidence | Not proven |
| --- | --- | --- | --- |
| LongMemEval-S retrieval | Can Lerim find answer-bearing sessions? | Full 500-question retrieval-only run | Answer generation or official LongMemEval QA accuracy |
| Context budget | How much context does Lerim select after retrieval? | Same 500 LongMemEval-S questions, Hugging Face tokenizer counts, recall shown beside reduction | Dollar cost savings, answer quality, or a replacement for the retrieval benchmark |
| Retrieval latency | How fast is local search on this machine? | Local timings over LongMemEval-S sessions | Hosted/server load performance |
| Trace ingestion cost/performance | How much time, LLM-call count, and local DB growth does the write path use? | Small LongMemEval-S public-trace sample through DSPy ingestion | Extraction quality, answer quality, or dollar cost when provider usage is unavailable |
| MCP integration | Does Lerim's config and MCP plumbing work? | Config validation, local stdio tools/context probes, trace-submit idempotency, 0 trace-submit extraction acceptances, and Gemini CLI live tool-call acceptance | Autonomous live tool use by every external client or successful trace-submit extraction in this artifact |
| Extraction | Can Lerim extract durable records from source sessions? | Aggregate-only report from one 47-case internal eval | Launch-grade public claim or market comparison |
| Market comparison | How does Lerim compare to alternatives? | Market table with source/provenance per row | Full same-boundary market ranking |

## Reporting Rules

- `report.json` is the numeric source of truth for Lerim rows.
- Use `predictions.jsonl` for per-question benchmark rows.
- Use `details.jsonl` for integration probe rows.
- Do not publish partial slices as final benchmark results.
- Do not call retrieval-only scores official LongMemEval QA scores.
- Do not use context-budget numbers without recall.
- Do not reuse retrieval numbers as extraction-quality numbers.
- Treat Lerim's trace-to-context extraction eval as first-party/private until
  a competitor runner feeds the same traces into another system and scores
  its saved memories with the same labels and judge.
- Do not publish competitor numbers without matching metric boundaries and
  source-backed provenance.
- Before publishing a benchmark claim, require the exact command, git commit,
  dataset snapshot, raw `report.json`, generated report, model/provider,
  hardware/runtime metadata, and failure count.
