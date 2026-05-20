# Lerim Results

This page is only for Lerim's own benchmark results. It should not contain a
competitor table. Use [Market Comparison](market-comparison.md) for market-wide
comparisons.

Every public Lerim number below points to a raw artifact in
`benchmarks/results/raw/`. Retrieval, context-budget, latency, ingestion, and
MCP artifacts keep clean release-worktree provenance in their environment
metadata. Aggregate extraction diagnostics keep their own provenance and are
reported only as first-party diagnostic numbers.

## Current Lerim Summary

| Surface | Current result | Evidence status | Source |
| --- | --- | --- | --- |
| LongMemEval-S retrieval, hybrid | R@5 96.2%, R@10 98.6%, R@20 99.6%, NDCG@10 88.4%, MRR 88.1% on 500 questions | Full retrieval-only artifact; clean release worktree | `benchmarks/results/raw/longmemeval-hybrid-full/report.json` |
| LongMemEval-S retrieval, lexical | R@5 77.0%, R@10 82.0%, R@20 89.8%, NDCG@10 62.7%, MRR 64.0% on 500 questions | Full retrieval-only artifact; clean release worktree | `benchmarks/results/raw/longmemeval-lexical-full/report.json` |
| Context budget, hybrid top-10 | 75.3% context reduction with 98.6% recall | Full retrieval-only artifact; clean release worktree | `benchmarks/results/raw/context-budget-hybrid-full/report.json` |
| Retrieval latency | 100 records p50 9.6 ms, p99 20.4 ms; 1,000 records p50 35.4 ms, p99 55.0 ms | Local retrieval artifact; clean release worktree | `benchmarks/results/raw/retrieval-latency-longmemeval/report.json` |
| Trace ingestion cost/performance | 3/3 traces passed; avg ingestion 96,994.9 ms; avg 5.0 LLM calls/trace; avg DB growth 581,632 bytes/trace; cost not available | Small LongMemEval-S public-trace sample; clean release worktree | `benchmarks/results/raw/trace-ingestion-cost-longmemeval-s-sample/report.json` |
| MCP integration | 15/15 config probes, doctor 14 passed/1 skipped, local context call passed, trace-submit idempotency passed, synthetic trace-submit extraction probe passed, 3 anonymized connection-visibility checks; separate Gemini CLI artifact records 1 installed-client connection and 1 live `lerim_context_brief` tool-call acceptance. Other clients are not live-tool-call validated yet. | Integration artifacts; clean release worktree; per-client local inventory omitted | `benchmarks/results/raw/mcp-integration-full/report.json`, `benchmarks/results/raw/mcp-gemini-live-tool-call/report.json` |
| Extraction quality | Diagnostic aggregate: quality 60.07%, quality gate 51.06%, hard gate 19.15% across 47 cases | Internal LLM-backed eval; aggregate-only public report | `benchmarks/results/raw/extraction-minimax-m27-full-47/report.json` |
| False-positive extraction | Negative precision 28.57%; 10 false-positive cases; 65 durable records created across 14 negative cases | Internal LLM-backed eval slice; aggregate-only public report | `benchmarks/results/raw/false-positive-extraction-minimax-m27-negative-cases/report.json` |

## LongMemEval-S Retrieval

[LongMemEval](https://arxiv.org/abs/2410.10813) is a long-term memory benchmark
for chat assistants. It contains 500 manually created questions that test
information extraction, multi-session reasoning, temporal reasoning, knowledge
updates, and abstention.

The benchmark has two common history sizes:

| Setting | Meaning in practice | Approximate size |
| --- | --- | ---: |
| LongMemEval-S | Shorter/smaller standard setting | about 115k tokens per question |
| LongMemEval-M | Larger setting with 500 sessions per problem | about 1.5M tokens per question |

The paper does not expand the letter `S` in prose. In Lerim docs, treat `S` as
the smaller standard setting compared with `M`. Lerim's runner uses the public
cleaned file `longmemeval_s_cleaned.json` from
[`xiaowu0162/longmemeval-cleaned`](https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned),
snapshot `98d7416c24c778c2fee6e6f3006e7a073259d48f`.

Lerim's current LongMemEval-S runner is retrieval-only:

1. Load the cleaned LongMemEval-S entry.
2. Index one retrievable unit per haystack session.
3. Search with the question text.
4. Compare retrieved session IDs against the gold `answer_session_ids`.
5. Report R@K, NDCG@10, and MRR.

This answers: "Can Lerim retrieve the session that contains the answer evidence?"
It does not answer: "Can Lerim generate the final answer?"

The hybrid run indexes each compact episode plus hidden retrieval-only source
text, then fuses semantic and lexical candidates with weighted reciprocal rank
fusion (`rrf_k=2`, semantic weight `0.7`, lexical weight `0.3`).

| Mode | Questions | R@1 | R@3 | R@5 | R@10 | R@20 | NDCG@10 | MRR | Raw artifact |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Hybrid | 500 | 81.8% | 93.0% | 96.2% | 98.6% | 99.6% | 88.4% | 88.1% | `benchmarks/results/raw/longmemeval-hybrid-full/report.json` |
| Lexical | 500 | 54.0% | 71.0% | 77.0% | 82.0% | 89.8% | 62.7% | 64.0% | `benchmarks/results/raw/longmemeval-lexical-full/report.json` |

Run the full hybrid retrieval artifact:

```bash
uv run python benchmarks/scripts/run_longmemeval_full.py
```

Run a small retrieval slice:

```bash
uv run python benchmarks/lerim_evidence/longmemeval.py \
  --limit 5 \
  --output-dir /tmp/lerim-longmemeval-slice
```

## Context Budget

The context-budget runner asks:

"If an agent needed context for this question, how much raw session text would
Lerim include by selecting top-K sessions instead of replaying the whole
haystack?"

It counts Hugging Face tokenizer tokens for all haystack sessions, then compares that to
the token count of Lerim's top-1, top-3, top-5, top-10, and top-20 retrieved
sessions. A context-budget result must always include recall. A smaller context
window is not useful if it misses the answer-bearing session.

This benchmark is not a cost-saving shortcut or an answer-quality score.
It uses the same 500 LongMemEval-S questions and retrieved sessions as the
retrieval benchmark, then reports a tokenizer-count diagnostic beside recall.
Use it to understand context selection, not answer quality or actual API spend.

Source artifact: `benchmarks/results/raw/context-budget-hybrid-full/report.json`

| Selection | Average selected tokens | Average tokens reduced | Average reduction | Recall |
| --- | ---: | ---: | ---: | ---: |
| Full haystack | 110,326 | 0 | 0.0% | 100.0% by definition |
| Top 1 | 2,984 | 107,343 | 97.3% | 81.8% |
| Top 3 | 8,814 | 101,512 | 92.0% | 93.0% |
| Top 5 | 14,260 | 96,067 | 87.1% | 96.2% |
| Top 10 | 27,304 | 83,023 | 75.3% | 98.6% |
| Top 20 | 52,561 | 57,765 | 52.4% | 99.6% |

Run the full context-budget artifact:

```bash
uv run python benchmarks/scripts/run_context_budget_full.py
```

Run a small context-budget slice:

```bash
uv run python benchmarks/lerim_evidence/context_budget.py \
  --limit 5 \
  --output-dir /tmp/lerim-context-budget-slice
```

## Retrieval Latency

The latency runner measures local search speed, not answer quality. It uses real
LongMemEval-S haystack sessions as the corpus and repeatedly calls Lerim's local
hybrid search path.

Source artifact: `benchmarks/results/raw/retrieval-latency-longmemeval/report.json`

| Corpus size | Ops | Average hit count | p50 | p90 | p95 | p99 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 records | 75 | 20.0 | 9.6 ms | 14.4 ms | 15.6 ms | 20.4 ms |
| 1,000 records | 75 | 20.0 | 35.4 ms | 39.9 ms | 48.8 ms | 55.0 ms |

These numbers are useful engineering evidence, but they are not a hosted load
test and should not be marketed as server throughput.

Run the latency artifact:

```bash
uv run python benchmarks/lerim_evidence/retrieval_latency.py \
  --sizes 100,1000 \
  --query-count 25 \
  --iterations 3 \
  --output-dir /tmp/lerim-retrieval-latency
```

## Trace Ingestion Cost/Performance

This runner measures Lerim's source-session write path. It normalizes public
LongMemEval-S haystack sessions through Lerim's generic trace envelope, then
ingests them through the MiniMax M2.7 BAML/LangGraph trace-ingestion graph in an
isolated context database.

Source artifact:
`benchmarks/results/raw/trace-ingestion-cost-longmemeval-s-sample/report.json`

| Metric | Result |
| --- | ---: |
| Public traces evaluated | 3 |
| Passed traces | 3 |
| Average ingestion time | 96,994.9 ms |
| p95 ingestion time | 111,169.5 ms |
| Average LLM calls per trace | 5.0 |
| Total LLM calls | 15 |
| Average context DB growth per trace | 581,632 bytes |
| Average durable records per trace | 0.0 |
| Cost per trace | not available |

This is a small performance sample, not an extraction-quality score. The sample
uses the current `support` source profile on public chat sessions, and the
durable-record count should not be interpreted as market quality evidence. Cost
is not estimated because the runtime exposes LLM call counts but not provider
token usage or billed cost for the BAML calls.

Run the sample artifact:

```bash
BAML_LOG=ERROR uv run python benchmarks/lerim_evidence/trace_ingestion_cost_performance.py \
  --limit 3 \
  --output-dir benchmarks/results/raw/trace-ingestion-cost-longmemeval-s-sample
```

## MCP Integration

The MCP integration runner checks product plumbing, not memory quality.

Source artifacts:

- `benchmarks/results/raw/mcp-integration-full/report.json`
- `benchmarks/results/raw/mcp-gemini-live-tool-call/report.json`

| Probe group | Result |
| --- | --- |
| Known target config probes | 15/15 passed |
| Installed-config doctor probes | 15 probes: 14 passed, 1 skipped |
| Installed-client CLI/config visibility probes | 4 probes, 4 passed; per-client local inventory omitted |
| Connection-visibility acceptances | 3 anonymized acceptance rows |
| Local stdio tools-list probe | passed |
| Local `lerim_context_brief` MCP call | passed |
| Local `lerim_trace_submit` idempotency call | passed |
| Local `lerim_trace_submit` extraction call | passed with a synthetic submitted trace fixture; created 1 episode record and 1 durable record through the real BAML/LangGraph path |
| Live installed-client tool-call probes | skipped in this artifact |

The separate Gemini CLI live artifact records:

| Probe group | Result |
| --- | --- |
| Known target config probes | 15/15 passed |
| Local stdio tools-list probe | passed |
| Local `lerim_context_brief` MCP call | passed |
| Local `lerim_trace_submit` idempotency call | passed |
| Gemini CLI installed-client probe | connected |
| Gemini CLI live `lerim_context_brief` tool call | accepted |

It verifies:

- supported MCP config shapes can be written
- those configs can be validated
- Lerim's MCP server can list tools over stdio
- local stdio calls to `lerim_context_brief` and `lerim_trace_submit` work
- the opt-in `lerim_trace_submit` extraction probe can import a fresh completed trace and create records through MiniMax M2.7 BAML/LangGraph ingestion
- optional installed-client probes can confirm installed clients can see the MCP config

Temporary config fixtures do not count as installed-agent acceptance. Live
installed-client tool-call validation is opt-in because it can spend model or
subscription credits. The current public live client acceptance is Gemini CLI
only; other clients still need their own live tool-call artifacts.

Run the MCP integration artifact:

```bash
uv run python benchmarks/lerim_evidence/integration.py \
  --context-project lerim \
  --include-real-doctor \
  --include-installed-client-probes \
  --include-stdio-trace-submit-extraction \
  --stdio-extraction-timeout-seconds 300 \
  --output-dir benchmarks/results/raw/mcp-integration-full
```

Add `--include-tool-call-probes` only for an opt-in live client run where model
or subscription spend is acceptable.

Run the Gemini CLI live tool-call artifact:

```bash
uv run python benchmarks/lerim_evidence/integration.py \
  --include-installed-client-probes \
  --installed-client-targets gemini-cli \
  --include-tool-call-probes \
  --tool-call-targets gemini-cli \
  --allow-live-client-tool-calls \
  --tool-call-timeout-seconds 120 \
  --max-tool-call-budget-usd 0.25 \
  --output-dir benchmarks/results/raw/mcp-gemini-live-tool-call
```

## Extraction Eval Status

Lerim has an aggregate-only public report from one internal 47-case MiniMax M2.7
extraction eval, judged by MiniMax M2.7.

Source artifact:
`benchmarks/results/raw/extraction-minimax-m27-full-47/report.json`

This is diagnostic evidence, not a launch-grade benchmark claim. The source
report is private, and the public artifact includes aggregate metrics only. It
excludes raw traces, per-case metrics, extracted record text, tool payloads, and
judge details.
No competitor has been run on this private extraction eval yet, so these numbers
must not be used as a market comparison row.

The eval measures the core trace-to-context job:

- extracting durable records when source sessions contain durable signal
- dropping weak, duplicate, temporary, or source-derivable notes
- staying faithful to source evidence
- passing concept recall and precision gates
- producing zero durable records when the source has no durable signal

| Metric | Result |
| --- | ---: |
| Dataset cases | 47 |
| Harness case failures | 0 |
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

### False-Positive Extraction

Source artifact:
`benchmarks/results/raw/false-positive-extraction-minimax-m27-negative-cases/report.json`

This diagnostic filters the 47-case extraction eval to the 14 cases labeled
`negative`. These are cases where the target behavior is no durable records.
It measures false-positive memory creation, not retrieval quality.

| Metric | Result |
| --- | ---: |
| Negative cases | 14 |
| No-durable cases | 4 |
| False-positive cases | 10 |
| Negative precision | 28.57% |
| False-positive case rate | 71.43% |
| Durable records on negative cases | 65 |
| Forbidden-concept score average | 74.05% |
| Signal-filtering score average | 28.57% |

The first labeled extraction dataset uses coding-agent traces because that is
where Lerim has the strongest labels today, not because Lerim is limited to
coding agents. Future public domain benchmarks should use labeled traces
for support, incident operations, research, data analysis, or product workflows.

Do not compare these extraction metrics to LongMemEval retrieval-only scores,
LoCoMo answer scores, public feature tables, or competitor market rows.
