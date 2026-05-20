# Lerim Retrieval Latency Benchmark

- Generated: `2026-05-20T09:50:20.484366+00:00`
- Command: `benchmarks/lerim_evidence/retrieval_latency.py --local-files-only --sizes 100,1000 --query-count 25 --iterations 3 --output-dir /tmp/lerim-clean-artifacts/retrieval-latency-longmemeval`
- Dataset snapshot: `98d7416c24c778c2fee6e6f3006e7a073259d48f`
- Queries: `25`
- Iterations: `3`

| Corpus records | Ops | p50 | p90 | p99 | Avg hits |
|---:|---:|---:|---:|---:|---:|
| 100 | 75 | 9.64 ms | 14.37 ms | 20.43 ms | 20.0 |
| 1000 | 75 | 35.40 ms | 39.85 ms | 54.96 ms | 20.0 |

## Methodology Notes

- Corpus rows are LongMemEval-S haystack sessions.
- Each corpus row is stored as one Lerim episode record.
- Latency measures local `ContextStore.search` with real hybrid retrieval.
- This is a local retrieval benchmark, not an HTTP daemon load test.
