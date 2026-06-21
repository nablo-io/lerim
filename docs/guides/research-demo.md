# Research Demo

This demo shows a realistic research-analyst agent trace moving through Lerim's
compiler so the next research run starts with the source-quality rules, the
rejected leads, and the cited conclusions instead of re-deriving methodology from
scratch. The trace and the extracted records below are real: the output was
captured by importing the checked-in example through Lerim's `research` profile.

## 1. The completed run

A research agent assessed the competitive position of the EU neobank sector for
Q2 2026. The valuable signal is not the report itself; it is the standing
source-quality rule, the rejected uncorroborated lead, the coverage boundary, and
the confirmed filing-backed conclusion.

The trace lives at [`docs/examples/traces/research-analyst-run.jsonl`](../examples/traces/research-analyst-run.jsonl).

## 2. Import it

```bash
lerim trace import docs/examples/traces/research-analyst-run.jsonl \
  --source-name research-agent \
  --source-profile research \
  --scope-type domain \
  --scope research
```

## 3. What Lerim kept

Six records were extracted: three durable constraints (a source-quality hierarchy,
a rejected-lead rule, and a coverage boundary), one confirmed fact, one analyst
preference, and one archived episode for the research session.

**Constraints (active — these govern future research):**

- **Source-quality hierarchy for quantitative claims** — ESMA filings and SEC
  equivalents are preferred over press releases and blog posts for confirming
  quantitative claims such as funding figures.
- **TechCrunch funding figures require regulatory corroboration** — TechCrunch
  funding figures should not be used for EU neobank sizing unless corroborated by
  a regulatory filing. A single unnamed source with no filing corroboration was
  rejected.
- **Crypto-native institutions excluded from EU neobank coverage** —
  Crypto-native and stablecoin-first institutions are excluded from the EU
  neobank coverage set unless explicitly requested; they distort funding-trend
  comparisons.

**Fact (active, cited):**

- **Q2 2026 EU neobank funding declined approximately 35% year-over-year** —
  Driven by tightened MiFID II marketing restrictions. Confirmed by three
  ESMA-filed prospectuses. *Evidence: line 4.*

**Preference (active):**

- **Benchmark EU neobank valuations against traditional bank multiples** — Not
  against US fintech multiples. US-fintech comparables overstate value because
  they ignore MiFID II capital constraints.

**Episode (archived — history, not standing context):**

- The EU neobank funding research session itself.

Inspect them yourself:

```bash
lerim context records --source-profile research
```

## 4. What the next agent now knows

Before this trace was compiled, the next research run on EU neobanks would likely
re-cite the uncorroborated TechCrunch funding figure, benchmark against US fintech
multiples, and include crypto-native institutions in the coverage set — all of
which this run rejected. After compilation, the source-quality hierarchy, the
explicit coverage boundary, and the filing-backed conclusion are available before
work begins.

## 5. The improvement loop

The source-quality rules and cited conclusions above are exactly the kind of
approved, evidence-backed data for a
smaller, private research-analyst model. The open core captures and cites; model
specialization (distillation, RL, prompt and harness tuning) is the private Lerim
layer, built on top of this open foundation.

## Notes

- Lerim is selective by design. The first extraction of an earlier version of this
  trace produced zero durable records because the methodology rules were stated
  only as in-the-moment narration. Adding one explicit "going forward, we should
  not use TechCrunch figures unless a filing corroborates them" line caused the
  rules to be captured as standing constraints. Routine sessions can and should
  produce zero records; that is the compiler rejecting noise.
- Replace the checked-in example with your own cleaned research-agent source
  session for real evaluation.
