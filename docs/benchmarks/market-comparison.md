# Market Comparison

This is the public page for Lerim vs other agent-memory and context systems.
It is intentionally market-wide. No single competitor is the organizing frame
for Lerim's benchmark story.

Rows get numbers only when the number is tied to a raw artifact, an official
benchmark page, a paper, or a clearly cited public report. Rows are not win/loss
claims unless the benchmark boundary matches.

## Where To Look

| Need | Location |
| --- | --- |
| Human-readable market table | This page |
| Lerim-only results and commands | [Lerim Results](lerim-results.md) |
| Benchmark hub | [Benchmark Overview](index.md) |
| Raw Lerim benchmark artifacts | `benchmarks/results/raw/` in this repo |
| Audit/provenance reports | `benchmarks/results/reports/` in this repo |
| Third-party source manifest | `benchmarks/results/market-sources.json` |
| Imported competitor artifacts | `benchmarks/results/raw/*-baseline/` when available |

The current repo includes one normalized imported competitor artifact. Other
competitor rows are linked to public docs/posts until they are normalized into
raw artifacts too.

Imported market baselines are pinned upstream source rows. Their local wrapper
records when Lerim normalized the source material; that wrapper is not a fresh
Lerim benchmark run and is exempt from first-party clean-run publication gates.

## Current Market Snapshot

### Benchmark Numbers

| System | Product type | Benchmark number tracked today | Benchmark boundary | Source / provenance | Comparable to Lerim LongMemEval-S retrieval? |
| --- | --- | --- | --- | --- | --- |
| Lerim | Source-session context compiler | Hybrid R@5 96.2%, R@10 98.6%, NDCG@10 88.4%, MRR 88.1%; lexical R@5 77.0%, R@10 82.0%, NDCG@10 62.7%, MRR 64.0% | LongMemEval-S retrieval-only over 500 questions | First-party raw artifacts: `benchmarks/results/raw/longmemeval-hybrid-full/report.json` and `benchmarks/results/raw/longmemeval-lexical-full/report.json`; dirty development tree, not launch-grade until rerun from a clean release candidate | Yes, first-party baseline |
| AgentMemory | Local memory engine plus MCP server | Hybrid R@5 95.2%, R@10 98.6%, NDCG@10 87.9%, MRR 88.2%; BM25 R@5 86.2%, R@10 94.6%, NDCG@10 73.0%, MRR 71.5% | LongMemEval-S retrieval-only over 500 questions | Pinned upstream raw artifact normalized in this repo at commit `68fddd418e1bbcc41d32a1c61b7a78d91eb7c4dc`; pinned public docs at <https://github.com/rohitg00/agentmemory/blob/68fddd418e1bbcc41d32a1c61b7a78d91eb7c4dc/benchmark/LONGMEMEVAL.md>, accessed 2026-05-19 | Pinned upstream artifact, not local rerun |
| MemPalace | Memory system | Pinned public docs report raw ChromaDB full-500 R@5 96.6%; later held-out-450 hybrid_v4 no-rerank R@5 98.4%, R@10 99.8%; neither row is normalized locally | LongMemEval retrieval recall, but raw artifacts and method are not normalized here | MemPalace benchmark docs: <https://github.com/MemPalace/mempalace/blob/1b94f4efb4949765d6965936476c236df13fd108/benchmarks/BENCHMARKS.md>, develop commit checked 2026-05-20; not normalized in this repo yet | Not yet |
| Mem0 | Memory API / cloud platform | Official Mem0 docs report LongMemEval overall 93.4 and LoCoMo overall 91.6 | Official answer/judge metrics, not Lerim's retrieval-only boundary | Mem0 official evaluation docs: <https://docs.mem0.ai/core-concepts/memory-evaluation>, accessed 2026-05-19; not pinned or normalized locally yet | No |
| Letta | Agent runtime | Official Letta post reports a filesystem LoCoMo result of 74.0 | LoCoMo filesystem-agent benchmark, not LongMemEval-S retrieval-only | Letta benchmark post dated 2025-08-12: <https://www.letta.com/blog/benchmarking-ai-agent-memory>, accessed 2026-05-19 | No |
| Zep / Graphiti | Temporal knowledge graph memory | No number tracked in this repo yet | Pending | Not available in this repo yet | No |
| Supermemory | Memory infrastructure | No number tracked in this repo yet | Pending | Not available in this repo yet | No |
| Khoj / claude-mem / Hippo / other systems | Mixed memory systems | No number tracked in this repo yet | Pending | Not available in this repo yet | No |

### Third-Party Feature Snapshot

These are feature and retrieval claims from one public market table that covers
several systems, not normalized benchmark artifacts in this repo. They are
useful for market awareness, but they are not independent measurements, fresh
local reruns, or Lerim raw `report.json` artifacts. The market-row source is
<https://www.agent-memory.dev/>, accessed 2026-05-19.

| System | Retrieval R@5 | External deps | REST endpoints | MCP tools | Auto-hooks | Native plugins | Open source | Source / provenance |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| AgentMemory | 95.2% | 0 | 121 | 51 | 12 | 6 | Yes, Apache-2.0 | Competitor-maintained market-row source: <https://www.agent-memory.dev/>, accessed 2026-05-19 |
| Mem0 | 81.4% | 2 (Qdrant, Neo4j) | Not reported | 12 | 0 | Not reported | Yes | Competitor-maintained market-row source: <https://www.agent-memory.dev/>, accessed 2026-05-19 |
| Letta | 73.8% | 1 (Postgres) | Not reported | 18 | 0 | Not reported | Yes | Competitor-maintained market-row source: <https://www.agent-memory.dev/>, accessed 2026-05-19 |
| Cognee | 78.1% | 1 (Neo4j) | Not reported | 9 | 0 | Not reported | Yes | Competitor-maintained market-row source: <https://www.agent-memory.dev/>, accessed 2026-05-19 |

## LongMemEval-S Retrieval

Retrieval-only rows. Do not treat these as answer-generation or extraction
scores.

| System | Mode | Questions | R@5 | R@10 | R@20 | NDCG@10 | MRR | Evidence |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Lerim | Hybrid | 500 | 96.2% | 98.6% | 99.6% | 88.4% | 88.1% | `benchmarks/results/raw/longmemeval-hybrid-full/report.json` |
| Lerim | Lexical | 500 | 77.0% | 82.0% | 89.8% | 62.7% | 64.0% | `benchmarks/results/raw/longmemeval-lexical-full/report.json` |
| AgentMemory | BM25+Vector | 500 | 95.2% | 98.6% | 99.4% | 87.9% | 88.2% | `benchmarks/results/raw/imported-market-baselines/report.json` |
| AgentMemory | BM25-only | 500 | 86.2% | 94.6% | 98.6% | 73.0% | 71.5% | `benchmarks/results/raw/imported-market-baselines/report.json` |
| MemPalace | Raw ChromaDB full set | 500 | 96.6% | Not available | Not available | Not available | Not available | Public docs only; not normalized locally |
| MemPalace | hybrid_v4 no-rerank held-out set | 450 | 98.4% | 99.8% | Not available | Not available | Not available | Public docs only; not normalized locally |

These rows are retrieval-only. They are not official LongMemEval QA scores, do
not call an LLM judge, and do not score generated answers.
Imported competitor rows are pinned upstream raw artifacts normalized locally;
they are not fresh local reruns. The imported LongMemEval-S rows currently
available in this repo come from commit
`68fddd418e1bbcc41d32a1c61b7a78d91eb7c4dc`.

## Extraction Comparison Status

Lerim's trace-to-context extraction eval is a private first-party benchmark.
No competitor row below has been run on that private extraction eval yet.

| System | Status on Lerim's private extraction eval |
| --- | --- |
| Lerim | Internal diagnostic aggregate exists; see [Lerim Results](lerim-results.md) for the current extraction-quality and false-positive numbers. |
| AgentMemory | Not available yet; not run on this private eval. |
| Cognee | Not available yet; not run on this private eval. |
| Letta | Not available yet; not run on this private eval. |
| Mem0 | Not available yet; not run on this private eval. |

Do not substitute LongMemEval-S retrieval numbers, LoCoMo answer scores, feature
tables, or public marketing rows for extraction-quality scores. A fair
extraction comparison requires a runner that feeds the same traces to each
system, collects the system's saved memories/context records, and scores those
records with the same labels and judge.

## Not-Yet-Comparable Rows

Mem0 and Letta have useful public benchmark numbers, but they do not share the
same boundary as Lerim's current retrieval-only artifacts:

- Mem0 reports managed-platform answer/judge results for LoCoMo, LongMemEval,
  and BEAM. Its docs also describe memory extraction from submitted
  conversation payloads, so do not describe Mem0 as retrieval-only
  infrastructure.
- Letta reports a LoCoMo filesystem-agent result. That is an agent/tool-use
  benchmark, not a LongMemEval-S retrieval-only benchmark.
- Cognee has a cited third-party market row at <https://www.agent-memory.dev/>,
  but its raw benchmark artifact is not normalized locally yet.
- MemPalace is close enough to track for LongMemEval-S retrieval, but the row
  is a pinned public source citation and is not normalized locally yet.
- Zep/Graphiti, Supermemory, Khoj, claude-mem, Hippo, and other systems are
  listed as watchlist rows until a cited public number or local run is added.

Rows marked as not normalized locally are source citations only.
Competitor-maintained market-row metrics are labeled as such; they are included
for market awareness, not as pinned reproducibility artifacts.

## Next Normalization Work

- Rerun Lerim public artifacts from a clean commit before launch.
- Normalize MemPalace if raw artifacts are available.
- Add reproducible importers or fresh local runs for Mem0, Letta, Zep/Graphiti,
  Supermemory, Khoj, claude-mem, Hippo, and any other serious memory system.
- Keep extraction-quality numbers separate from retrieval-only numbers.
- Publish no market-ranking claim until rows share the same benchmark boundary.

## Sources

- Lerim raw artifacts: `benchmarks/results/raw/`
- Normalized imported LongMemEval-S baseline currently available:
  `benchmarks/results/raw/imported-market-baselines/`, upstream commit
  `68fddd418e1bbcc41d32a1c61b7a78d91eb7c4dc`.
- Public benchmark docs for that imported baseline:
  <https://github.com/rohitg00/agentmemory/blob/68fddd418e1bbcc41d32a1c61b7a78d91eb7c4dc/benchmark/LONGMEMEVAL.md>,
  accessed 2026-05-19.
- Public market-row source for source-reported feature metrics:
  <https://www.agent-memory.dev/>, accessed 2026-05-19.
- MemPalace benchmark docs:
  <https://github.com/MemPalace/mempalace/blob/1b94f4efb4949765d6965936476c236df13fd108/benchmarks/BENCHMARKS.md>,
  develop commit checked 2026-05-20; not normalized locally yet.
- Mem0 official evaluation docs:
  <https://docs.mem0.ai/core-concepts/memory-evaluation>, accessed
  2026-05-19; not pinned or normalized locally yet.
- Letta benchmark post:
  <https://www.letta.com/blog/benchmarking-ai-agent-memory>, dated
  2025-08-12 and accessed 2026-05-19; not pinned or normalized locally yet.
