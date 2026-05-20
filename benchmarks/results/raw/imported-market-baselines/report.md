# Imported Market Baselines

This generated audit artifact supports the market-wide comparison page. It is not a standalone competitor comparison or win/loss claim.

Current imported source rows cover the pinned upstream artifacts listed below. Add more market systems here only when their source artifacts and provenance are pinned with the same care.

- Generated: `2026-05-20T12:05:04.821199+00:00`
- Source system: `AgentMemory`
- Source repository: `https://github.com/rohitg00/agentmemory`
- Source commit: `68fddd418e1bbcc41d32a1c61b7a78d91eb7c4dc`
- Baseline type: `pinned_upstream_raw_artifacts`
- Rerun in this environment: `False`

## Source Artifacts

| System | Source | SHA-256 |
|---|---|---|
| AgentMemory | `benchmark/data/longmemeval_results_hybrid.json` | `4d65bea9e914c8eb98b7673bf1cd89215eabf30239f4fad1839b82c456e71ae8` |
| AgentMemory | `benchmark/data/longmemeval_results_bm25.json` | `c2e4a207eae571b9fa806243da069f0699ba81940952dce876fb41b7db90e3ea` |
| AgentMemory | `benchmark/results/load-100k-96c0ed0.json` | `e41dda9164443c6066a13459a7e5ce561a752b9a9fa2a2dfd65b699d8652691b` |

## LongMemEval-S Retrieval

| System | Mode | Questions | R@5 | R@10 | R@20 | NDCG@10 | MRR |
|---|---|---:|---:|---:|---:|---:|---:|
| AgentMemory | hybrid | 500 | 95.2% | 98.6% | 99.4% | 87.9% | 88.2% |
| AgentMemory | bm25 | 500 | 86.2% | 94.6% | 98.6% | 73.0% | 71.5% |

## Market Table Usage

| System | Mode | Status | Lerim artifact available | Warning |
|---|---|---|---:|---|
| AgentMemory | hybrid | `pinned_upstream_competitor_row_for_market_table` | True | The competitor was not rerun in this environment. Treat this as a pinned upstream market row, not a fresh competitor rerun. |
| AgentMemory | bm25 | `pinned_upstream_competitor_row_for_market_table` | True | The competitor was not rerun in this environment. Treat this as a pinned upstream market row, not a fresh competitor rerun. |

## Imported Load Artifact

| System | Endpoint | N | C | Ops | Errors | p50 | p90 | p99 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| AgentMemory | POST /agentmemory/remember | 1000 | 10 | 200 | 0 | 577.4 ms | 607.3 ms | 675.3 ms |
| AgentMemory | POST /agentmemory/smart-search | 1000 | 10 | 200 | 0 | 160.1 ms | 185.6 ms | 224.4 ms |
| AgentMemory | GET /agentmemory/memories?latest=true | 1000 | 10 | 200 | 0 | 395.5 ms | 475.7 ms | 542.6 ms |

AgentMemory load cells are HTTP endpoint measurements; Lerim's current latency artifact is local ContextStore.search, so they are not direct apples-to-apples latency comparisons.

## Publication Rules

- Say pinned upstream competitor artifact, not fresh local competitor rerun.
- Do not compare latency winner claims across HTTP and local-store boundaries.
- Do not claim Lerim beats a competitor until the benchmark boundary and provenance are visible.
