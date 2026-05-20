# Lerim False-Positive Extraction Diagnostic

- Generated: `2026-05-20T09:49:10.040839+00:00`
- Command: `benchmarks/lerim_evidence/false_positive_extraction.py --source-report '<private-source-report>' --output-dir /tmp/lerim-clean-artifacts/false-positive-extraction-minimax-m27-negative-cases`
- Source artifact: `private first-party extraction eval artifact`
- Source visibility: `private`
- Source digest: `db0bb32710b439a4de86fd185f15d4fabea80a97fdfc02092ebc76411e782e16`
- Agent model: `minimax / MiniMax-M2.7`
- Judge model: `MiniMax-M2.5`
- Source cases: `47`
- Negative cases: `14`
- Aggregate-only public artifact: `True`
- Publication status: `diagnostic_development_guardrail_not_market_comparison`

## Headline

| Metric | Result |
|---|---:|
| Negative cases | 14 |
| No-durable cases | 4 |
| False-positive cases | 10 |
| Negative precision | 28.57% |
| False-positive case rate | 71.43% |
| Durable records on negative cases | 65 |
| Forbidden-concept score average | 74.05% |
| Signal-filtering score average | 28.57% |

## Dataset Slice

- Selection rule: `case.category == 'negative'`

## Public Artifact Boundary

- This diagnostic is derived from the negative/noise cases in the 47-case LLM-backed extraction artifact.
- It measures whether Lerim avoids durable records when labeled source sessions have no durable signal.
- Raw traces, case identifiers, extracted record text, tool payloads, forbidden concept text, per-case metrics, and judge details are intentionally excluded.
- Treat this as internal development evidence until rerun from a clean release state.
- Competitors have not been run on this private labeled eval, so their scores are not available.

Do not compare this diagnostic to LongMemEval retrieval-only metrics or market rows.
