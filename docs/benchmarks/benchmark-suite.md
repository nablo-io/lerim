# Benchmark Suite

This page explains the benchmark surfaces. Use [Lerim Results](lerim-results.md)
for first-party numbers and [Market Comparison](market-comparison.md) for
source-backed market rows.

## Surfaces

| Surface | What it measures | What it does not measure | Current public artifact |
| --- | --- | --- | --- |
| LongMemEval-S retrieval | Whether Lerim retrieves the session containing gold answer evidence | Generated answer quality or official LongMemEval QA accuracy | `benchmarks/results/raw/longmemeval-hybrid-full/report.json` and `benchmarks/results/raw/longmemeval-lexical-full/report.json` |
| Context budget | Token count of selected top-K sessions compared with replaying the whole haystack, always shown with recall | Dollar savings or answer quality | `benchmarks/results/raw/context-budget-hybrid-full/report.json` |
| Retrieval latency | Local search latency over LongMemEval-S session records | Hosted throughput, concurrent load, or ingestion speed | `benchmarks/results/raw/retrieval-latency-longmemeval/report.json` |
| Trace ingestion cost/performance | Wall-clock ingestion time, measured LLM calls, and context DB file growth for public source-session traces | Extraction quality, answer quality, or dollar cost when provider usage data is unavailable | `benchmarks/results/raw/trace-ingestion-cost-longmemeval-s-sample/report.json` |
| MCP integration | Config writer coverage, stdio MCP tools/context probes, trace-submit idempotency, extraction-probe accounting, and selected installed-client acceptances | Completed-session capture for every client, organic production traces, or extraction quality when the extraction probe has 0 acceptances | `benchmarks/results/raw/mcp-integration-full/report.json` and `benchmarks/results/raw/mcp-gemini-live-tool-call/report.json` |
| Extraction quality | Lerim's trace-to-context extraction behavior on labeled source-session cases | Market comparison, because competitors have not been run on this private eval | `benchmarks/results/raw/extraction-minimax-m27-full-47/report.json` |
| False-positive extraction | Whether Lerim avoids durable records on cases labeled as having no durable signal | Market comparison or general retrieval quality | `benchmarks/results/raw/false-positive-extraction-minimax-m27-negative-cases/report.json` |
| Imported market baselines | Source-backed external numbers normalized or cited for comparison | Fresh local competitor reruns unless explicitly stated | `benchmarks/results/raw/*-baseline/` when available |

## LongMemEval-S

LongMemEval is a long-term memory benchmark for chat assistants. Lerim currently
uses LongMemEval-S, the smaller standard setting compared with LongMemEval-M.
The public runner uses `longmemeval_s_cleaned.json` from
`xiaowu0162/longmemeval-cleaned`, snapshot
`98d7416c24c778c2fee6e6f3006e7a073259d48f`.

The LongMemEval paper distinguishes two history sizes:

| Setting | Meaning in these docs | Approximate size |
| --- | --- | ---: |
| LongMemEval-S | The smaller standard setting Lerim currently uses | about 115k tokens per question |
| LongMemEval-M | The larger setting with many more sessions per problem | about 1.5M tokens per question |

The paper does not spell out the letter `S` in prose. In these docs, read it as
the smaller LongMemEval setting, not as a short or synthetic benchmark invented
by Lerim.

Lerim's LongMemEval-S artifact is retrieval-only:

1. Index one retrievable unit per haystack session.
2. Search with the question text.
3. Compare retrieved session IDs with `answer_session_ids`.
4. Report R@K, NDCG@10, and MRR.

Do not call these official QA scores. They do not call an LLM judge and do not
score generated answers.

## Context Budget

The context-budget runner asks how much source-session text Lerim selects after
retrieval. It compares full haystack tokens with the tokens in Lerim's top-1,
top-3, top-5, top-10, and top-20 retrieved sessions. A context-budget number is
only meaningful when shown with recall.

This is a context-selection diagnostic on the same LongMemEval-S 500-question
retrieval run. It does not replace LongMemEval-S retrieval, does not call an
LLM judge, and does not claim actual dollar savings. It answers a narrower
engineering question: if the downstream agent used Lerim's retrieved sessions
as context, how much of the original haystack would be sent forward, and did
that smaller context still include the answer-bearing session?

## Trace Ingestion Cost/Performance

The trace-ingestion cost/performance runner measures the write path, not the
retrieval path. It takes public LongMemEval-S haystack sessions, normalizes them
through Lerim's generic trace envelope, then sends them through the same
DSPy trace-ingestion path used by Lerim.

The current public artifact is a small sample, not a full-suite result. It
reports:

- ingestion wall-clock time per trace
- measured LLM calls per trace
- context SQLite file-size growth after schema initialization
- whether provider cost is available

Cost is not inferred from fixed stages or pricing guesses. In the current
artifact, cost is `not available` because Lerim records LLM call counts but does
not yet expose provider token usage or billed cost for model calls.

## Extraction

The extraction eval measures trace-to-context behavior: durable-record
precision, required concept coverage, faithfulness, evidence validity, and
negative precision. The current public artifact is an aggregate-only diagnostic
report from an internal 47-case eval. Competitor extraction scores are not
available yet because no competitor has been run on the same private traces with
the same labels and judge.

The false-positive extraction diagnostic is a narrower slice of that same
47-case eval. It filters to cases labeled `negative`, where the target behavior
is zero durable records. It reports negative precision, false-positive case
count, and durable records created on negative cases. This is useful as an
engineering guardrail because a memory system can look strong on retrieval while
still saving too much temporary or source-derivable context.

## Reporting Rule

Do not edit benchmark numbers by hand. Rerun the benchmark, update
`report.json`, regenerate `report.md`, and then update public docs from those
artifacts.
