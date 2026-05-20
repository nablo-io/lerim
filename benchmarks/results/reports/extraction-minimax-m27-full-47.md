# Lerim Extraction Quality Benchmark

- Generated: `2026-05-20T09:49:10.020803+00:00`
- Command: `benchmarks/lerim_evidence/extraction_quality.py --source-report '<private-source-report>' --output-dir /tmp/lerim-clean-artifacts/extraction-minimax-m27-full-47`
- Source artifact: `private first-party extraction eval artifact`
- Source visibility: `private`
- Source digest: `db0bb32710b439a4de86fd185f15d4fabea80a97fdfc02092ebc76411e782e16`
- Agent model: `minimax / MiniMax-M2.7`
- Judge model: `MiniMax-M2.5`
- Dataset cases: `47`
- Aggregate-only public artifact: `True`
- Publication status: `development_baseline_not_launch_grade`

## Headline

| Metric | Result |
|---|---:|
| Task completion | 96.97% |
| Quality average | 60.07% |
| Quality gate pass | 51.06% |
| Hard gate pass | 19.15% |
| Concept recall average | 68.99% |
| Required concept coverage | 68.09% |
| Kind alignment | 91.49% |
| Record precision average | 76.16% |
| Faithfulness average | 78.21% |
| Claim faithfulness | 51.06% |
| Negative precision | 28.57% |
| Signal filtering | 25.53% |
| Evidence coverage | 100.00% |
| Evidence validity | 100.00% |

## Dataset Coverage

- Cases: `47` / `47`
- Dataset coverage: `100.00%`
- Case failures: `0`


## Public Artifact Boundary

- This is an aggregate-only public report derived from a full LLM-backed extraction artifact.
- Raw traces, extracted record text, tool payloads, case identifiers, per-case metrics, and judge details are intentionally excluded.
- Treat this as development baseline evidence until rerun from a clean release state.
- These metrics measure trace-to-context extraction quality, not LongMemEval retrieval or answer-generation accuracy.
- Competitors have not been run on this private labeled eval, so their scores are not available.

Do not compare these extraction metrics to LongMemEval retrieval-only metrics.
Competitor scores are not available for this private labeled eval.
