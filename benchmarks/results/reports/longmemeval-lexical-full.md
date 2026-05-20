# Lerim LongMemEval-S Retrieval-Only Benchmark

- Generated: `2026-05-20T09:54:50.252030+00:00`
- Command: `benchmarks/lerim_evidence/longmemeval.py --retrieval-mode lexical --local-files-only --progress-every 25 --output-dir /tmp/lerim-clean-artifacts/longmemeval-lexical-full`
- Retrieval mode: `lexical`
- Dataset: `xiaowu0162/longmemeval-cleaned/longmemeval_s_cleaned.json`
- Dataset snapshot: `98d7416c24c778c2fee6e6f3006e7a073259d48f`
- Questions evaluated: `500`
- Full filtered run: `True`
- LLM in loop: `False`

## Headline

| Metric | Value |
|---|---:|
| Recall any @ 1 | 54.0% |
| Recall any @ 3 | 71.0% |
| Recall any @ 5 | 77.0% |
| Recall any @ 10 | 82.0% |
| Recall any @ 20 | 89.8% |
| NDCG @ 10 | 62.7% |
| MRR | 64.0% |
| Retrieval p50 | 2.40 ms |
| Retrieval p95 | 3.32 ms |
| Indexing p50 | 683.75 ms |

## By Question Type

| Type | Count | R@5 | R@10 | R@20 | MRR |
|---|---:|---:|---:|---:|---:|
| knowledge-update | 78 | 94.9% | 96.2% | 97.4% | 86.8% |
| multi-session | 133 | 87.2% | 91.7% | 97.7% | 74.1% |
| single-session-assistant | 56 | 30.4% | 39.3% | 51.8% | 25.6% |
| single-session-preference | 30 | 50.0% | 56.7% | 86.7% | 37.6% |
| single-session-user | 70 | 92.9% | 97.1% | 97.1% | 81.6% |
| temporal-reasoning | 133 | 73.7% | 79.7% | 90.2% | 53.6% |

## Methodology Notes

- This is retrieval-only, not the official LongMemEval QA score.
- Each question builds a fresh Lerim SQLite context store.
- Each haystack session becomes one Lerim `episode` record.
- Raw predictions are saved in `predictions.jsonl`.
