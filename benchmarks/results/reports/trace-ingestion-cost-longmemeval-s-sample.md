# Lerim Trace Ingestion Cost/Performance Benchmark

- Generated: `2026-05-20T09:08:12.236102+00:00`
- Command: `benchmarks/lerim_evidence/trace_ingestion_cost_performance.py --limit 3 --output-dir benchmarks/results/raw/trace-ingestion-cost-longmemeval-s-sample`
- Dataset snapshot: `98d7416c24c778c2fee6e6f3006e7a073259d48f`
- Source profile: `support`
- Traces evaluated: `3`
- Model: `MiniMax-M2.7`

## Headline

| Metric | Value |
| --- | ---: |
| Passed traces | 3 / 3 |
| Avg ingestion time | 106303.6 ms |
| p95 ingestion time | 118911.5 ms |
| Avg LLM calls per trace | 5.0 |
| Total LLM calls | 15 |
| Avg DB growth per trace | 581,632 bytes |
| Cost per trace | not available |

## Methodology Notes

- Input traces are public LongMemEval-S haystack sessions normalized through Lerim's generic trace envelope.
- Ingestion uses Lerim's BAML/LangGraph trace-ingestion path with live LLM calls.
- LLM call counts come from `TraceIngestionRunDetails.llm_calls`.
- Database growth excludes empty schema initialization, then measures cumulative SQLite file-size deltas around each trace.
- Cost is not inferred. It stays unavailable unless provider usage or billing data is measured.
