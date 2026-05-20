# Lerim LongMemEval-S Retrieval-Only Benchmark

- Generated: `2026-05-20T08:39:34.993086+00:00`
- Command: `benchmarks/lerim_evidence/longmemeval.py --retrieval-mode hybrid --local-files-only --output-dir benchmarks/results/raw/longmemeval-hybrid-full`
- Retrieval mode: `hybrid`
- Dataset: `xiaowu0162/longmemeval-cleaned/longmemeval_s_cleaned.json`
- Dataset snapshot: `98d7416c24c778c2fee6e6f3006e7a073259d48f`
- Questions evaluated: `500`
- Full filtered run: `True`
- LLM in loop: `False`

## Headline

| Metric | Value |
|---|---:|
| Recall any @ 1 | 81.8% |
| Recall any @ 3 | 93.0% |
| Recall any @ 5 | 96.2% |
| Recall any @ 10 | 98.6% |
| Recall any @ 20 | 99.6% |
| NDCG @ 10 | 88.4% |
| MRR | 88.1% |
| Retrieval p50 | 7.86 ms |
| Retrieval p95 | 8.86 ms |
| Indexing p50 | 1495.68 ms |

## By Question Type

| Type | Count | R@5 | R@10 | R@20 | MRR |
|---|---:|---:|---:|---:|---:|
| knowledge-update | 78 | 100.0% | 100.0% | 100.0% | 92.7% |
| multi-session | 133 | 97.7% | 100.0% | 100.0% | 93.1% |
| single-session-assistant | 56 | 100.0% | 100.0% | 100.0% | 97.3% |
| single-session-preference | 30 | 86.7% | 93.3% | 96.7% | 73.8% |
| single-session-user | 70 | 92.9% | 97.1% | 100.0% | 75.3% |
| temporal-reasoning | 133 | 94.7% | 97.7% | 99.2% | 86.4% |

## Methodology Notes

- This is retrieval-only, not the official LongMemEval QA score.
- Each question builds a fresh Lerim SQLite context store.
- Each haystack session becomes one Lerim `episode` record.
- Raw predictions are saved in `predictions.jsonl`.
