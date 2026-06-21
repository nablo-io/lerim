# Incident Ops Demo

This demo shows a realistic incident-agent trace moving through Lerim's compiler so
the next on-call responder or postmortem writer starts with the confirmed root
cause, the rejected hypothesis, and the open runbook gap instead of re-investigating
from scratch. The trace and the extracted records below are real: the output was
captured by importing the checked-in example through Lerim's `ops` profile.

## 1. The completed run

An incident agent investigated a 70% webhook delivery failure rate on the EU
billing queue at 03:10 UTC. It rejected the first hypothesis (gateway down),
confirmed the real root cause (a stale route not decommissioned after migration),
applied a mitigation, and flagged a runbook gap. The valuable signal is not the
mitigation; it is the rejected hypothesis, the authority boundary, and the open
follow-up risk.

The trace lives at [`docs/examples/traces/incident-agent-run.jsonl`](../examples/traces/incident-agent-run.jsonl).

## 2. Import it

```bash
lerim trace import docs/examples/traces/incident-agent-run.jsonl \
  --source-name incident-agent \
  --source-profile ops \
  --scope-type domain \
  --scope incident-ops
```

## 3. What Lerim kept

Five records were extracted: two durable constraints (a runbook gap and an
authority boundary), two facts (the confirmed root cause and the rejected
hypothesis), and one archived episode for the incident itself.

**Constraints (active — these govern future response):**

- **Runbook requires replay protection** — No replay protection or
  idempotency/dedup step exists. A future stale route or carrier-side duplicate
  can cause the same retry exhaustion. The runbook must be updated before this
  incident class is considered resolved.
- **Routing changes require Platform authority** — Routing decommission authority
  sits with the Platform team. On-call responders must not decommission routing
  paths independently; they must escalate to Platform.

**Facts (active):**

- **Stale route caused webhook delivery failures** — The stale
  `eu-billing-legacy` route was not decommissioned after migration; the carrier
  retried duplicate deliveries up to 8 times, exhausting the retry budget.
  Disabling the route resolved the issue.
- **Gateway was healthy during webhook failures** — EU gateway was confirmed
  healthy; gateway failure was not the cause. This fact prevents the next
  responder from re-chasing the gateway-down hypothesis.

**Episode (archived — history, not standing context):**

- The EU billing webhook delivery failure investigation itself.

Inspect them yourself:

```bash
lerim context records --source-profile ops
```

## 4. What the next agent now knows

Before this trace was compiled, the next responder to a similar webhook failure
would likely start by checking the gateway (the rejected hypothesis) and might
decommission a route without Platform sign-off (the authority boundary). After
compilation, the confirmed root cause, the explicitly rejected gateway hypothesis,
and the open runbook gap are available before work begins — so the 9am postmortem
writer does not re-derive what the 3am responder already concluded.

## 5. The improvement loop

The rejected-hypothesis and root-cause records above are exactly the kind of
approved, cited, incident-specific data that can be exported as training-ready
data for a smaller, private incident-response model. Lerim captures and cites the
data for a smaller, private incident-response model. The open core captures and cites; model specialization (distillation, RL,
prompt and harness tuning) is the private Lerim layer, built on top of this open
foundation.

## Notes

- Replace the checked-in example with your own cleaned incident-agent source
  session for real evaluation.
- Do not put private incident datasets or converter outputs under public docs.
  Use team-owned storage for raw traces and commit only small sanitized examples
  when a public example is useful.
