# Compliance Demo

This demo shows a realistic compliance-review agent trace moving through Lerim's
compiler so the next compliance review of a data-export feature starts with the
policy boundary, the rejected interpretation, and the approval gate instead of
re-litigating whether a proprietary format meets GDPR Article 20. The trace and
the extracted records below are real: the output was captured by importing the
checked-in example through Lerim's `compliance` profile.

## 1. The completed run

A compliance agent reviewed whether a new customer data-export feature met GDPR
Article 20 data portability requirements before launch. The feature exported to a
proprietary encrypted blob. The valuable signal is not the review itself; it is
the rejected interpretation (with reasoning), the standing approval gate, and the
durable policy boundary with its regulatory citation.

The trace lives at [`docs/examples/traces/compliance-review-run.jsonl`](../examples/traces/compliance-review-run.jsonl).

## 2. Import it

```bash
lerim trace import docs/examples/traces/compliance-review-run.jsonl \
  --source-name compliance-agent \
  --source-profile compliance \
  --scope-type domain \
  --scope compliance
```

## 3. What Lerim kept

Four records were extracted: two durable constraints (a policy boundary with a
regulatory citation, and a standing approval gate), one citation fact, and one
archived episode for the review.

**Constraints (active — these govern future export-feature work):**

- **GDPR Article 20 portability requires open formats** — A proprietary encrypted
  blob fails the "commonly used and machine-readable" requirement per EDPB
  Guidelines on data portability. This interpretation applies to all future
  export-feature reviews.
- **Standing approval gate for data-export format changes** — This is not a
  product-team-only decision. Proprietary-blob exports must be blocked from launch
  until reworked to an open format and re-reviewed.

**Fact (active):**

- **GDPR Article 20 regulation citation** — GDPR Article 20(1); EDPB Guidelines
  on the right to data portability. Effective jurisdiction: EU/EEA; version
  current as of May 2026.

**Episode (archived — history, not standing context):**

- The GDPR Article 20 data portability compliance review of the specific feature.

Inspect them yourself:

```bash
lerim context records --source-profile compliance
```

## 4. What the next agent now knows

Before this trace was compiled, the next compliance review of a data-export
feature would have to re-research whether a proprietary format satisfies Article
20 and re-decide who needs to sign off. After compilation, the rejected
interpretation (with its reasoning), the approval gate, and the regulatory
citation are available before work begins — so a product team cannot ship a
proprietary-only export format by treating it as a product-only decision.

## 5. The improvement loop

The policy boundaries and rejected interpretations above are exactly the kind of
approved, cited, compliance-specific data for a
smaller, private compliance-review model. The open core captures and cites; model
specialization (distillation, RL, prompt and harness tuning) is the private Lerim
layer, built on top of this open foundation.

## Notes

- Replace the checked-in example with your own cleaned compliance-agent source
  session for real evaluation.
- Do not put private regulated data under public docs. Use customer-owned storage
  for raw traces and commit only small sanitized examples when a public example is
  useful.
- If the trace contains regulated personal data, run a cleaner or the
  [defense-in-depth redaction helper](submit-custom-agent-trace.md#defense-in-depth-redaction)
  before import.
