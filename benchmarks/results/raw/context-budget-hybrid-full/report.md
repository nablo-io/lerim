# Lerim Context Budget Benchmark

- Generated: `2026-05-20T10:03:31.155750+00:00`
- Command: `benchmarks/scripts/run_context_budget_full.py --output-dir /tmp/lerim-clean-artifacts/context-budget-hybrid-full`
- Dataset snapshot: `98d7416c24c778c2fee6e6f3006e7a073259d48f`
- Questions evaluated: `500`
- Tokenizer: `mixedbread-ai/mxbai-embed-xsmall-v1`
- Retrieval mode: `hybrid`
- Full filtered run: `True`

## Headline

| Window | Avg selected tokens | Avg tokens reduced | Avg reduction | Recall any |
|---|---:|---:|---:|---:|
| Top 1 | 2984 | 107343 | 97.3% | 81.8% |
| Top 3 | 8814 | 101512 | 92.0% | 93.0% |
| Top 5 | 14260 | 96067 | 87.1% | 96.2% |
| Top 10 | 27304 | 83023 | 75.3% | 98.6% |
| Top 20 | 52561 | 57765 | 52.4% | 99.6% |

## Methodology Notes

- Full replay tokens count every LongMemEval-S haystack session transcript.
- Selected tokens count the raw transcripts for Lerim's retrieved top-K sessions.
- Counts use a Hugging Face tokenizer, not character division.
- This is a retrieval-window benchmark, not a context-brief quality score.
