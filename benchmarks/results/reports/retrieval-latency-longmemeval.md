# Lerim Retrieval Latency Benchmark

- Generated: `2026-05-20T08:59:47.166551+00:00`
- Command: `benchmarks/lerim_evidence/retrieval_latency.py --local-files-only --sizes 100,1000 --query-count 25 --iterations 3 --output-dir benchmarks/results/raw/retrieval-latency-longmemeval`
- Dataset snapshot: `98d7416c24c778c2fee6e6f3006e7a073259d48f`
- Queries: `25`
- Iterations: `3`

| Corpus records | Ops | p50 | p90 | p99 | Avg hits |
|---:|---:|---:|---:|---:|---:|
| 100 | 75 | 8.40 ms | 8.89 ms | 9.61 ms | 20.0 |
| 1000 | 75 | 31.12 ms | 32.36 ms | 47.51 ms | 20.0 |

## Methodology Notes

- Corpus rows are LongMemEval-S haystack sessions.
- Each corpus row is stored as one Lerim episode record.
- Latency measures local `ContextStore.search` with real hybrid retrieval.
- This is a local retrieval benchmark, not an HTTP daemon load test.
