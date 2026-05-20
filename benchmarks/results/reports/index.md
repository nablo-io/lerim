# Benchmark Reports

Generated from public, non-ignored raw `report.json` artifacts. Do not edit benchmark numbers here by hand.

Unpublished live-client artifacts should stay ignored; this index only includes public, non-ignored raw reports.

| Benchmark | Run | Scope | Questions | Snapshot | Worktree | Evidence status | Headline | Raw report | Markdown |
|---|---|---:|---:|---|---|---|---|---|---|
| lerim_extraction_quality_minimax_m27_full_47 | `extraction-minimax-m27-full-47` | diagnostic | 47 | `` | clean | diagnostic; aggregate-only; not launch-grade | quality 60.1%, quality gate 51.1%, hard gate 19.1% | [report.json](../raw/extraction-minimax-m27-full-47/report.json) | [report.md](extraction-minimax-m27-full-47.md) |
| lerim_false_positive_extraction_minimax_m27_negative_cases | `false-positive-extraction-minimax-m27-negative-cases` | diagnostic | 14 | `` | dirty | diagnostic; aggregate-only; not launch-grade; dirty provenance | negative precision 28.6%, false-positive cases 10, durable records on negatives 65 | [report.json](../raw/false-positive-extraction-minimax-m27-negative-cases/report.json) | [report.md](false-positive-extraction-minimax-m27-negative-cases.md) |
| lerim_mcp_integration | `mcp-gemini-live-tool-call` | partial |  | `` | clean | public artifact | 15/15 config probes, stdio tools 1, local context calls 0, trace submit idempotency 1, trace submit extraction 0, installed config validation 0, installed client connections 1, installed-client tool calls 1 | [report.json](../raw/mcp-gemini-live-tool-call/report.json) | [report.md](mcp-gemini-live-tool-call.md) |
| lerim_mcp_integration | `mcp-integration-full` | partial |  | `` | clean | public artifact | 15/15 config probes, stdio tools 1, local context calls 0, trace submit idempotency 1, trace submit extraction 0, installed config validation 15, installed client connections 3, installed-client tool calls 0 | [report.json](../raw/mcp-integration-full/report.json) | [report.md](mcp-integration-full.md) |
| longmemeval_s_context_budget | `hybrid` | retrieval-only | 500 | `98d7416c24c7` | clean | retrieval-only; not QA score | top10 reduction 75.3%, recall 98.6% | [report.json](../raw/context-budget-hybrid-full/report.json) | [report.md](context-budget-hybrid-full.md) |
| longmemeval_s_retrieval_latency | `hybrid` | retrieval-only | 25 | `98d7416c24c7` | clean | retrieval-only; not QA score | 1000 records p50 31.1 ms, p99 47.5 ms | [report.json](../raw/retrieval-latency-longmemeval/report.json) | [report.md](retrieval-latency-longmemeval.md) |
| longmemeval_s_retrieval_only | `hybrid` | retrieval-only | 500 | `98d7416c24c7` | clean | retrieval-only; not QA score | R@5 96.2%, R@10 98.6%, MRR 88.1% | [report.json](../raw/longmemeval-hybrid-full/report.json) | [report.md](longmemeval-hybrid-full.md) |
| longmemeval_s_retrieval_only | `lexical` | retrieval-only | 500 | `98d7416c24c7` | clean | retrieval-only; not QA score | R@5 77.0%, R@10 82.0%, MRR 64.0% | [report.json](../raw/longmemeval-lexical-full/report.json) | [report.md](longmemeval-lexical-full.md) |
| longmemeval_s_trace_ingestion_cost_performance | `trace-ingestion-cost-longmemeval-s-sample` | sample | 3 | `98d7416c24c7` | clean | sample; live LLM calls; cost unavailable | 3 traces, avg ingestion 106303.6 ms, avg LLM calls 5.0, avg DB growth 581632 bytes, cost unavailable | [report.json](../raw/trace-ingestion-cost-longmemeval-s-sample/report.json) | [report.md](trace-ingestion-cost-longmemeval-s-sample.md) |
| imported_market_baselines | `pinned_upstream_raw_artifacts` | pinned upstream retrieval-only | 500 | `68fddd418e1b` | imported | imported; pinned upstream; not local rerun | hybrid R@5 95.2%, R@10 98.6%, MRR 88.2%; bm25 R@5 86.2%, R@10 94.6%, MRR 71.5% | [report.json](../raw/agentmemory-pinned-baseline/report.json) | [report.md](imported-market-baselines.md) |

Raw directory: `benchmarks/results/raw`
