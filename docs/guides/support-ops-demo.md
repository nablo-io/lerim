# Support Ops Demo

This demo shows a realistic support-agent trace moving through Lerim's compiler so
the next support agent starts with the durable constraints instead of re-deriving
them. The trace and the extracted records below are real: the output was captured
by importing the checked-in example through Lerim's `support` profile.

## 1. The completed run

A support agent handled a customer refund request for a failed annual renewal.
The interesting signal is not the refund itself; it is the standing policy the run
surfaced: a renewal where the gateway captured the charge but activation failed
must never be retried, because retrying creates a triple charge.

The trace lives at [`docs/examples/traces/support-agent-run.jsonl`](../examples/traces/support-agent-run.jsonl).

## 2. Import it

```bash
lerim trace import docs/examples/traces/support-agent-run.jsonl \
  --source-name support-agent \
  --source-profile support \
  --scope-type domain \
  --scope support-ops
```

## 3. What Lerim kept

Five records were extracted. Three are durable constraints that govern future
support work, one is a reusable fact about a known bug, and the specific ticket
itself is archived as an episode (kept as history, not promoted to standing
context).

**Constraints (active — these govern future runs):**

- **No retry for captured-but-inactive renewals** — Renewals where the payment
  gateway captures the charge but activation fails must not be retried. Only
  manual activation or customer refund are authorized follow-up actions.
  *Evidence: lines 4, 7.*
- **EUR 500 manager approval threshold for refunds** — Refunds exceeding EUR 500
  require manager approval and cannot be auto-approved. *Evidence: line 2.*
- **EUR 500+ refund escalation diagnostics requirements** — Escalations for
  EUR 500+ refunds must include gateway timeout log, duplicate capture evidence,
  and plan-inactive status confirmation. *Evidence: line 8.*

**Fact (active):**

- **Known gateway-capture-without-activation bug** — Payment gateway times out on
  activation but captures the charge; retry causes triple charge. Resolution is
  manual activation or refund only. *Evidence: line 4.*

**Episode (archived — history, not standing context):**

- Support escalation for the specific EUR 640 refund (kept as an episode record,
  not promoted to a durable constraint).

Inspect them yourself:

```bash
lerim context records --source-profile support
```

## 4. What the next agent now knows

Before this trace was compiled, a fresh support agent handling the next
captured-but-inactive renewal would most likely retry the charge (causing a triple
charge) or escalate without the required diagnostics. After compilation, the
standing constraints above are available before work begins.

## 5. The improvement loop

The constraints above are exactly the kind of approved, cited, workflow-specific
data for a smaller, private support
model. The open core captures and cites; model specialization (distillation, RL,
prompt and harness tuning) is the private Lerim layer, built on top of this open
foundation.

## Notes

- Replace the checked-in example with your own cleaned support-agent source
  session for real evaluation.
- Do not put private customer datasets or converter outputs under public docs.
  Use customer-owned storage for raw traces and commit only small sanitized
  examples when a public example is useful.
- If the trace contains customer PII or secrets, run a cleaner or the
  [defense-in-depth redaction helper](submit-custom-agent-trace.md#defense-in-depth-redaction)
  before import. Lerim extraction is selective, but it is not a privacy firewall.
