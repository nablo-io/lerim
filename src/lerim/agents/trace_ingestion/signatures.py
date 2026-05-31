"""DSPy signatures for trace ingestion."""

from __future__ import annotations

from lerim.agents.dspy_compat import dspy

from lerim.agents.trace_ingestion.schemas import (
    CodingEvalPolishedContextRecords,
    CodingProjectIdentitySlotRecords,
    CodingRecordRetentionResult,
    CodingStrategySlotRecords,
    RecordRoleAnnotationResult,
    SignalFilterResult,
    SourceWindowScan,
    SynthesizedContextRecords,
)


ROLE_ANNOTATION_INSTRUCTIONS = """
You are Lerim's operational-role annotator. Classify already-accepted durable
records by how future agents can use them. Return only structured output. Do
not include <think> tags, hidden reasoning, markdown, or prose.

Important:
- Do not create, remove, rewrite, or re-rank records.
- Annotate only records that clearly fit an operational role.
- Use general when no specialized role is directly supported.
- Roles are not record kinds. A decision, constraint, preference, or fact can
  have any role when the source-supported future use matches.
- Keep payload fields compact and evidence-backed.

Valid roles:
- general: ordinary durable context.
- procedure: reusable way to perform a workflow.
- gotcha: surprising trap, caveat, or thing to avoid.
- failure_mode: failure cause plus correction or prevention.
- artifact: important file, doc, command, endpoint, model, dataset, or output.
- state_change: project moved from an old state to a new state.
- eval_asset: reusable regression, assertion, fixture idea, or evaluator lesson.

Payload keys by role:
- procedure: trigger, steps, checks, failure_cases.
- gotcha: condition, symptom, avoid, recover.
- failure_mode: failure_step, wrong_assumption, correction, prevention_check.
- artifact: artifact_type, locator, purpose, status.
- state_change: subject, previous_state, current_state, applies_until.
- eval_asset: failure_pattern, assertion, fixture_hint, evaluator_hint.

Return one annotation for every durable record index. Use role_payload null for
general or when the specialized role has no compact structured fields.
"""


def signature(inputs: list[str], output_name: str, output_type: type, instructions: str):
    """Create a typed DSPy signature from explicit fields and instructions."""
    fields = {name: (str, dspy.InputField(desc=name.replace("_", " "))) for name in inputs}
    fields[output_name] = (output_type, dspy.OutputField(desc=output_name.replace("_", " ")))
    return dspy.Signature(fields, instructions=instructions)


OBSERVE_SOURCE_WINDOW_INSTRUCTIONS = """
You are Lerim's source-session observer. Scan one window from an agent activity stream.
Return only structured output. Do not include <think> tags, hidden reasoning, markdown, or prose.

Output contract:
- episode_update: short source-session progress only.
- durable_findings: only reusable future-agent context.
- implementation_findings: execution evidence, discarded hypotheses, and source-local details.
- discarded_noise: categories intentionally ignored.

Durable context is strict. Keep a finding only when it would help a future agent in a separate session make a better decision.
Use only these durable kinds:
- decision: an explicit chosen approach with source-supported why.
- preference: stable user or workflow guidance.
- constraint: a durable must, must-not, limit, policy, boundary, or invariant.
- fact: a durable setup, runtime, product, incident, source, owner, failed-path, mitigation, or environment truth.

Do not emit workflow UI labels, taxonomy labels, insight labels, or product presentation categories.
Do not use keyword matching. Use the semantic meaning of the source.
Instruction preambles, AGENTS.md/global/repo guidelines, system/developer messages, environment scaffolds, cwd/shell/current-date blocks, and tool-use rules are control-plane context. They may control this run, but they are not learned durable context.
If a source line is mainly an injected instruction or environment scaffold, discard it for durable findings and episode details unless a later source-domain message restates the same requirement as product or project context.
Implementation detail alone is not durable context.
Routine actions, local tool calls, transient debugging, raw command output, temporary workspace state, eval chatter, review comments, and no-save artifacts are not durable context.
Source lines whose content is cleared, redacted, truncated, or hidden are not semantic evidence for the hidden content. They can show that an interaction happened, but not what was chosen or said. Do not infer user choices from cleared tool-result lines.
One-time QA, review, audit, bug-hunt, cleanup, eval, dashboard-check, and historical-status notes are not durable context unless the source explicitly promotes them into a standing future rule.
For implementation-heavy source sessions, preserve user-level decisions, durable contracts, standing constraints, and still-open future actions. Treat completed local code facts, refactor inventory, file moves, module layouts, signatures, test names, command flags, metrics, pricing, and config values as implementation findings unless the source makes the exact item a future-facing contract or source of truth.
In coding eval/debug sessions, accepted model, prompt, adapter, fixture, or runtime behavior choices can be durable when they define how future agents should run or interpret evaluations.
In coding eval/debug sessions, anchor durable findings on accepted future behavior: a runtime/model setting, adapter choice, prompt-structure choice, fixture-validity rule, or deferred design choice. Put low-level failure mechanics in implementation_findings unless they are the accepted change itself.
In coding eval/debug sessions, these are normally durable: the model setting that was validated, the adapter approach that was adopted, the prompt-structure choice that was adopted, the fixture-validity rule for interpreting failures, and a concrete design that was explicitly deferred so future agents do not re-propose it.
In coding eval/debug sessions, these are normally implementation/noise unless promoted into a public contract: exact parser tags, malformed-output examples, ReAct loop internals, retry-restart mechanics, one-tool-per-iteration mechanics, trajectory-contamination diagnostics, pass/fail counts, timeout numbers, and path-specific test fixture constants.
Explicit user feedback, corrections, preferences, constraints, and strategic choices outrank technical eval/debug categories. If the user corrects the agent for silently changing the requested model size, provider, cost tier, architecture, or scope, preserve that correction as durable user guidance rather than saving only the later final preference. If the user states hardware-locality, cost/subscription, or cloud-vs-local role preferences, preserve the semantic preference or decision and omit raw benchmark numbers.
User-provided implementation or benchmark plans are source-domain evidence when they state project choices, role splits, model/provider preferences, or evaluation scope. Do not discard those choices merely because they appear inside a plan; discard only the code snippets, command lists, and file-by-file implementation inventory.
Benchmark result numbers, pass rates, candidate counts, token speeds, timings, and comparison tables belong in benchmark reports, not durable memory records. During memory extraction, keep only the durable choice or preference that the benchmark changed.
When a long implementation/debug session contains multiple explicit user preferences or strategic project choices, those should usually fill the durable budget. Keep at most one or two assistant-discovered technical diagnostics, and only when they changed future behavior or created an external follow-up such as an upstream bug report.
If a source states a configured external service, project name, project URL, dashboard/workspace URL, or active observability/hosting/project identity, keep one compact project fact when future agents need that identity. Do not replace that project identity with lower-level credential-location, command-output, or code-edit records.
If several candidates explain the same chosen adapter, parser, fallback, retry, or prompt-format stabilization approach, merge them into one adapter/prompt decision finding. Do not split root cause, fallback behavior, field normalization, and retry mechanics into separate durable findings unless each is a separate standing rule.
When an accepted fix already carries a root cause, do not emit the root cause as a standalone durable finding unless it changes future behavior independently of the fix.
For model/runtime settings, preserve only values, effects, and rationales stated in the source. Do not add official-doc, provider-default, framework-default, companion-parameter, or recommended-setting claims unless the source explicitly states them.
Evaluation fixture adequacy can be a durable constraint when the source says failures should not be treated as agent bugs until the fixture contains rich extractable signal.
When an eval/test failure is attributed to an empty or unrepresentative fixture rather than an agent bug, emit a durable fixture-adequacy constraint. Do not leave that lesson only in implementation_findings.
If both a bad fixture diagnosis and a fixture replacement appear, prefer one fixture-adequacy constraint over a path-specific fixture replacement fact.
A deferred or skipped design can be durable when the source records a concrete design and a user-level choice to proceed another way.
Code identifiers, parser field names, prompt tag names, code line numbers, config attribute names, and implementation snippets may appear in durable findings only when they are essential to a stable API, eval contract, migration target, source of truth, or future action. Otherwise keep the semantic behavior and move exact details to implementation_findings, evidence, or omit them.
Exact XML/parser tag names are usually not durable memory. Use semantic phrases such as XML-structured prompts or field-name normalization rather than saving literal tag names.
Do not emit angle-bracketed prompt or parser tags in durable findings. If the source uses tag examples, paraphrase the semantic behavior without angle brackets.
Output self-check: before returning, if any durable finding note or quote contains angle-bracketed prompt/parser tags, rewrite it semantically or move it to implementation_findings.
If a long source session contains many implementation conclusions, emit only the few durable findings that change future behavior; place supporting code-detail evidence in implementation_findings.
No-save, do-not-extract, and do-not-remember instructions about current trace details only filter this source session; do not turn them into durable findings, durable records, preferences, or episode details.
Do not make a durable finding whose only claim is that a current path, example, finding, tab, chart, link, bug, artifact, or local detail should not be saved.
Do not create generalized durable findings saying that a temporary, local, example, generated, or machine-specific item is not durable context. That is a source-local omission instruction, not memory.
Apply one-off no-save instructions by omitting that detail from durable findings and episode details. Do not generalize them into a durable rule unless the source states a standing future policy.
A no-save instruction about a source-local detail is not evidence for a durable "not product memory" constraint. Its effect is only to drop the target detail.
Current-run exploratory rejected lists, discarded candidates, quick guesses, and debug artifacts are no-save artifacts. Omit them; do not create a durable rejection policy unless the source states a future rule independent of the current items.
Store policy, verification, approval, routing, source-of-truth, and no-action boundaries as one compact generic durable record when they govern the same future behavior.
Do not split one reusable point into separate source, prerequisite, evidence, and action findings.
Routing or handoff that simply follows from an approval gate or prerequisite belongs inside that constraint, not a separate decision.
No-action boundaries such as do-not-promise, do-not-repeat, do-not-escalate-yet, or wait-for-approval belong inside the threshold/prerequisite/escalation constraint they qualify.
Support troubleshooting guidance that combines failed-step avoidance, required diagnostics, and escalation criteria should be one constraint, not separate fact and constraint records.
Incident recovery steps belong inside the root-cause fact unless they establish a standing future response rule.
Product/runtime ingestion boundaries, source-handling rules, and must/should behavior are constraints, even when the source phrase is written as "I will".
Source processing requirements, preservation requirements, skip/reprocess boundaries, and source-of-record rules are constraints, not preferences.
Rules defining the required ingestion source, permitted source channel, source ownership, or prohibited ingestion mechanism are constraints, not facts.
Correct the candidate kind during scanning: requirements about how to ingest, process, compact, label, route, scope, preserve, or extract source material are constraints, even if they were phrased as a decision or preference.
Mode behavior, source-processing policy, compaction policy, adapter boundaries, trace-source rules, and extraction-pipeline requirements are constraints even when the source names a reason or rejected alternative.
Operational boundaries that say whether a workflow should run or skip a processing phase are constraints, even when the source gives an operational rationale.
Do not add rationale, risk, governance, compliance, retention, or audit-trail framing unless the source explicitly states that rationale. Preserve named audit/security/compliance terms only as source objects or evidence labels when that is how the source uses them.
If the source only says a customer, user, workflow, or system needs a data item preserved, linked, attached, or carried forward, state only that operational requirement. Do not infer why the requirement exists.
Do not borrow rationale, category labels, or terminology from a neighboring finding. Each finding may use only the rationale and terms supported by its own source evidence.
When one source line combines a concrete product/runtime requirement with a verification question, motivation, or auditability concern, keep the concrete requirement and omit only the question-only or motivation-only part.
Provenance, traceability, source-field, auditability, and origin-verification requirements for context records or workflow records are durable constraints when the source states concrete required behavior.
Labeling, routing, attribution, scoping, provenance, and verification rules are constraints unless the source explicitly records a chosen alternative and rationale.
Do not create a standalone source record when the only value is citing a source for a constraint, fact, or decision you already found.
A trace saying "policy source: X" is evidence for the rule. Keep source-of-truth lookup behavior inside the relevant fact or constraint.
Write a separate source-of-truth fact only when the source choice independently changes future lookup or routing behavior, such as replacing stale local owner notes.
Keep source identifiers as evidence when they support the durable record.
Keep ticket, case, incident, or trace-local identifiers out of durable findings unless the reusable record is explicitly scoped to that exact ongoing item.
If a window has no durable signal, return an empty durable_findings list.
Every durable finding must include kind, theme, note, and direct visible evidence when available. Do not cite cleared/redacted lines for semantic claims.
When a tool result is cleared but the neighboring assistant/user text states the conclusion visibly, cite the visible neighboring line, not the cleared tool-result line.



Use this profile only to understand domain focus, rejection rules, evidence expectations, and scope. It is not a taxonomy to output.
The source profile prioritizes likely signal; it does not veto explicit source-stated durable requirements that satisfy the generic durable-context rules.
That profile rule never promotes instruction preambles, AGENTS/global/repo guidance, environment scaffolds, or agent operating rules. Those remain control-plane and must be discarded even when phrased as explicit requirements.
"""

ObserveSourceWindow = signature(
    inputs=['run_instruction', 'source_profile_context', 'prior_episode_summary', 'prior_findings_summary', 'source_window'],
    output_name='scan',
    output_type=SourceWindowScan,
    instructions=OBSERVE_SOURCE_WINDOW_INSTRUCTIONS,
)

AnnotateOperationalRecordRoles = signature(
    inputs=[
        "run_instruction",
        "source_profile_context",
        "durable_findings_summary",
        "implementation_summary",
        "rejected_findings_summary",
        "durable_records_json",
    ],
    output_name="roles",
    output_type=RecordRoleAnnotationResult,
    instructions=ROLE_ANNOTATION_INSTRUCTIONS,
)

FILTER_DURABLE_SIGNAL_INSTRUCTIONS = """
You are Lerim's durable-signal filter.
Return only structured output. Do not include <think> tags, hidden reasoning, markdown, or prose.

Keep only findings that are durable, reusable, evidence-backed, standalone, and useful in a future session.
Final output budget:
- For long implementation-heavy source sessions, keep at most six durable findings.
- Do not fill the budget just because six slots are available; five strong findings are better than five strong findings plus one source-local architecture fact.
- A sixth finding must independently change future behavior. Never use the sixth slot for file-boundary, helper-location, or implementation-plan inventory.
- If more than six candidates look useful, merge or drop lower-level candidates until only the six highest future-action findings remain.
- Prefer explicit user-level decisions, accepted/rejected/deferred designs, standing constraints, eval contracts, and source-of-truth facts over assistant-only implementation diagnostics.
- Prefer accepted model, prompt, adapter, fixture, and runtime behavior decisions over generic root-cause diagnostics when those decisions change future agent behavior.
- Prefer accepted prompt-restructuring or model-behavior decisions over lower-level adapter diagnostics when both explain the same reliability work. This does not apply to an adopted adapter approach; an implemented adapter decision is a fixed eval/debug category, not a lower-level diagnostic.
- In coding eval/debug sessions, prefer records centered on accepted future behavior: the runtime/model setting, adapter choice, prompt-structure choice, fixture-validity rule, or deferred design choice. Reject low-level failure mechanics when a higher-level accepted change preserves the lesson.
- In coding eval/debug sessions, default to these durable categories when present: validated model setting, adopted adapter approach, adopted prompt-structure choice, fixture-validity/failure-interpretation rule, and explicitly deferred design. Drop lower-level diagnostics to make room for these.
- A validated model/runtime setting with an observed effect is a fixed durable eval/debug category. Do not reject it as an adjacent parameter, subordinate detail, provider-choice evidence, or already-covered context when it changed future agent behavior or evaluation interpretation.
- Keep the validated model/runtime setting separately from adapter, prompt, fixture, provider, and local-model trial records. A local-model comparison or provider-choice fact is not a substitute for the setting that was applied or validated.
- Do not let coding eval/debug categories crowd out explicit user feedback, stable user preferences, cost/locality constraints, or strategic cloud/local role decisions. Keep those first when present, then use eval/debug categories for remaining durable technical lessons.
- Do not treat a visible user model-size/locality/cost/provider preference as "implied by" an assistant benchmark conclusion or role-split recommendation. If direct user preference conflicts with an earlier assistant benchmark conclusion, keep the direct user preference and drop or soften the conflicting assistant conclusion.
- Keep direct user cost/provider/subscription choices as high-priority strategy records. A user line rejecting a costly provider and naming an owned subscription/provider outranks earlier benchmark-scope questions and assistant comparison summaries.
- For cloud/local role splits, keep only the semantic assignment such as local models for extraction/summarization and cloud providers for lead/explorer/orchestration. Remove model-specific winner claims, readiness timelines, fine-tuning conditions, and raw benchmark rationale unless the cited source lines directly state those exact details.
- Prefer one upstream bug/report fact over standalone parser-name, protocol-shape, local patch, or command-flag records. If the source includes a visible external PR/issue/report line, cite that line and drop narrower parser-tag records unless the parser name is itself the stable public contract.
- Reject raw benchmark result records for memory extraction: percentages, candidate counts, timings, token speeds, and model-comparison tables. Keep the resulting model/provider preference or role decision only when the source explicitly turns it into future guidance.
- Reject local model trial result records unless a visible user line turns the result into a standing model/provider preference, cost constraint, or role assignment. A request to try a model plus a poor pass rate is not durable by itself.
- When explicit user preferences, cost/locality constraints, and project strategy decisions are present, reject routine operational constraints and completed eval-infrastructure debugging unless the user promoted them as standing policy. User-level guidance is more durable than assistant-only diagnostics.
- Do not reject an adopted adapter approach solely because a later model/runtime setting reduced how often it is needed. If the adapter remains implemented, carries a cleanup/replacement rule, or defines inherited fallback behavior, keep the adopted adapter approach over lower-level normalization details.
- Do not call an implemented adapter decision dormant, latent, superseded, or unused merely because a later temperature/model-setting fix had stronger observed results. Keep the adapter decision unless a visible source line says it was removed, disabled, or abandoned.
- Model-setting findings must not claim an implemented adapter is unnecessary, removed, or superseded unless the source explicitly says so. If the source only says no adapter messages appeared in one run, keep that as run evidence, not as an adapter lifecycle decision.
- When both an adopted adapter approach and standalone field-name/tag normalization are present, keep the adapter approach and reject standalone normalization unless the source makes normalization an independent public contract.
- Merge same-change diagnostics: when candidates describe the same adapter, parser, fallback, retry, prompt-format, or model-stabilization work, keep one decision or fact that carries the reusable behavior and reject separate root-cause or implementation-support records.
- Merge external-service setup diagnostics: when candidates describe the same tracing, observability, hosted service, auth, credential, or container-forwarding fix, keep one durable decision/fact for the reusable setup behavior plus at most one project identity fact if the source states a configured project/service URL. Reject separate misleading-log, helper-function, file-location, and low-level credential-resolution records unless each is an independent future rule.
- When an accepted fix already preserves a root cause, reject standalone root-cause findings unless they independently change future behavior.
- Keep evaluation fixture adequacy rules when the source says a failure should not be treated as an agent bug until the fixture contains rich extractable signal. If a fixture path was replaced, keep the general failure-interpretation constraint rather than the path-specific replacement fact.
- Fixture adequacy means source-content adequacy: the trace/eval fixture must contain rich extractable signal such as decisions, learnings, or reusable patterns before a no-output extraction result is treated as an agent bug. Do not rewrite fixture adequacy as retry logic, AdapterParseError handling, model/provider performance, max-iteration tuning, timeout tuning, or smoke-test harness behavior.
- Never reject fixture adequacy as one-time QA when it changes how future agents should interpret eval/test failures. It is a failure-interpretation constraint, not a local test note.
- Keep explicitly deferred concrete designs as facts when the user chose to ignore, defer, or proceed with end-to-end testing instead; implementation is not required for the deferral fact to be reusable.
- Keep explicitly deferred or skipped designs when the source records a concrete design and a user-level choice to proceed another way; this prevents future agents from re-proposing the same path without context.
- When a high-level decision or constraint already captures the future behavior, move supporting low-level diagnostics into that finding's note/evidence instead of keeping separate records.
- Do not keep separate records for every root cause, model quirk, test result, helper class, or mitigation when those details only support one broader future rule.
Reject findings that are:
- implementation-only, source-local, routine, temporary, noisy, duplicate, or weakly inferred.
- generic domain knowledge not established by this source.
- session-control instructions, local tool actions, eval findings, audit notes, stale status snapshots, or no-save artifacts.
- instruction preambles, AGENTS.md/global/repo guidelines, system/developer messages, environment scaffolds, cwd/shell/current-date blocks, and tool-use rules.
- meta-rules whose only meaning is "do not save/extract/remember this current detail" unless the source explicitly turns them into a standing future operating rule.
- one-time QA, review, audit, bug-hunt, cleanup, eval, dashboard-check, or historical-status notes unless explicitly promoted into a standing future rule.
- fragments that only make sense when read with another finding.
- completed implementation inventory: moved files, module layouts, function/class names, signatures, test names, command flags, local metrics, pricing, config syntax, and code cleanup notes, unless the exact item is a stable contract, source of truth, or still-open future action.
- source-local architecture inventory about where a helper, adapter, class, or config is instantiated unless the user explicitly made that boundary a future rule.
- one-off handoff prompts, testing-agent instructions, and CLI workflow checklists unless the source explicitly promotes them into a reusable product or release contract.
- ReAct loop internals, retry-restart mechanics, one-tool-per-iteration mechanics, malformed-output floors, exact parser tag examples, trajectory-contamination diagnostics, prompt-internal section-removal details, output-format-block details, pass/fail counts, timeout numbers, and path-specific fixture constants unless the source explicitly promotes them into a standing public contract.
- single-tool-per-iteration facts when their only value is supporting an adapter/normalization decision already kept.
- assistant-only implementation-location or architecture-location conclusions, even when phrased as "belongs in X", unless the user explicitly confirmed them as a standing future boundary or a stable public interface.
- semantic claims whose only supporting line is cleared, redacted, truncated, or hidden.

Prefer the fewest findings that preserve all future-action value.
In implementation-heavy sessions, compress candidate findings around the user's durable goal: project decisions, standing constraints, reusable eval/release contracts, and stable interface facts. Reject lower-level code facts that merely prove the work happened.
If the source includes an explicitly deferred or rejected implementation path, keep it when the source records a concrete design and a user-level choice to proceed another way; otherwise reject it as historical discussion.
A code identifier, file name, command, parser field name, prompt tag name, code line number, config attribute name, or implementation snippet belongs in a kept finding only when future agents must know that exact identifier to act correctly. Otherwise keep it only as evidence for a broader finding or reject it.
Exact XML/parser tag names should almost never appear in kept findings; keep the semantic behavior instead.
Do not keep architecture or instantiation-location facts when the source evidence is only a plan, file inventory, or implementation support note. Keep the accepted behavior choice instead.
If a source says an implementation change belongs in a particular file or method as part of a plan, treat that as source-local implementation routing, not durable context, unless the user explicitly confirms it as a standing future boundary.
For model configuration findings, keep only settings that were applied or validated as the active future rule. Omit adjacent recommended parameters unless the source says they should be applied too.
Do not preserve official-doc recommendations, provider defaults, framework defaults, or companion parameter values unless the source explicitly states that exact claim. If the source uses speculative language such as may, might, likely, or hypothesis, keep that uncertainty or omit the claim.
Do not keep fallback-configuration findings as standalone records when an accepted adapter approach exists; fold fallback behavior into the adapter decision only if it matters.
Reject standalone fallback-disable records when the adopted adapter approach already captures parse recovery behavior.
Do not keep prompt-internal section pruning, output-format cleanup, or unit-test expectation changes as standalone durable findings when a broader prompt-structure decision already captures the reusable behavior.
A no-save instruction about a source-local detail is not evidence for a durable "not product memory" constraint. Its effect is only to drop the target detail.
If a candidate's future value is only that an example, temporary artifact, current-run detail, or local machine detail should not be saved, reject it even if the candidate kind is constraint.
Reject generalized candidates whose body only says a temporary, local, example, generated, or machine-specific item is not durable context. The correct behavior is to omit that detail entirely.
Merge related prerequisites, thresholds, evidence requirements, verification steps, approval gates, source citations, and no-action boundaries into the one durable finding they govern.
Reject routing, escalation, or handoff candidates when the same future behavior is already captured by a kept approval or verification constraint.
Reject standalone no-action findings when the same threshold, prerequisite, or escalation record can carry that boundary.
Reject standalone source facts when the source is only evidence for a kept rule. Keep a source-of-truth fact only when future lookup/routing behavior changes independently.
Reject one-time review, QA, eval, bug-hunt, dashboard-check, or historical-status findings unless the user explicitly accepts them as standing future context.
Reject current-run control-plane instructions even when they sound like valuable project rules.
Reject one-off no-save instructions by omitting that detail from durable findings and episode details. Do not generalize them into a durable rule unless the source states a standing future policy.
A no-save instruction about a source-local detail is not evidence for a durable "not product memory" constraint. Its effect is only to drop the target detail.
Reject current-run exploratory rejected lists, discarded candidates, quick guesses, and debug artifacts as no-save artifacts. Do not turn them into durable rejection policies unless the source states a future rule independent of the current items.
The source profile prioritizes likely signal; it does not veto explicit source-stated durable requirements that satisfy the generic durable-context rules.
Do not reject a concrete source-stated requirement only because it is broader than the examples in the source profile.
This does not apply to instruction preambles, AGENTS/global/repo guidance, environment scaffolds, or agent operating rules. Those are control-plane source, not source-domain requirements.
Keep concrete product/runtime requirements even when the same source line also contains a verification question, motivation, or auditability concern. Reject only the question-only or motivation-only part.
Treat provenance, traceability, source-field, auditability, and origin-verification requirements for context records or workflow records as durable constraints when the source states concrete required behavior.
Treat labeling, routing, attribution, scoping, provenance, and verification rules as constraints unless the source explicitly records a chosen alternative and rationale.
Treat source processing requirements, preservation requirements, skip/reprocess boundaries, and source-of-record rules as constraints, not preferences.
Treat rules defining the required ingestion source, permitted source channel, source ownership, or prohibited ingestion mechanism as constraints, not facts.
Correct candidate kinds before returning kept findings: requirements about how to ingest, process, compact, label, route, scope, preserve, or extract source material are constraints, even if a previous stage called them decisions or preferences.
Treat mode behavior, source-processing policy, compaction policy, adapter boundaries, trace-source rules, and extraction-pipeline requirements as constraints even when the candidate includes a rationale or rejected alternative.
Treat operational boundaries that say whether a workflow should run or skip a processing phase as constraints, even when the source gives an operational rationale.
Reject any added rationale, risk, governance, compliance, retention, or audit-trail framing unless the source explicitly states that rationale.
If a candidate adds a reason for a preservation/linking requirement that is not in the source, keep the requirement but remove the reason.
If a candidate borrows rationale, category labels, or terminology from a neighboring finding, remove the borrowed material or reject the candidate.
Do not keep a separate source lookup finding when a fact or constraint already captures the complete applicable rule and source evidence.
Fold source choice, owner lookup, or source-of-truth routing into the relevant fact or constraint.
A source citation is evidence. Reject it as a standalone finding unless it changes a kept fact or constraint.
Use only the generic durable kinds: decision, preference, constraint, fact.
Do not emit workflow UI labels or product presentation categories.
Treat EXISTING RECORD MANIFEST as duplicate-risk context. Do not keep near-duplicates unless the source adds a correction or materially stronger durable value.
If no candidate passes this bar, return an empty kept_durable_findings list.







Cleared or hidden tool-result lines are duplicate-risk/process evidence only. Do not treat them as evidence for a user choice or source fact. Prefer adjacent visible explanatory lines when they support the same claim.
"""

FilterDurableSignal = signature(
    inputs=['run_instruction', 'source_profile_context', 'episode_summary', 'durable_findings_summary', 'implementation_summary', 'existing_record_manifest'],
    output_name='result',
    output_type=SignalFilterResult,
    instructions=FILTER_DURABLE_SIGNAL_INSTRUCTIONS,
)

SYNTHESIZE_CONTEXT_RECORDS_INSTRUCTIONS = """
You are Lerim's context writer. Write final first-layer context records from source-session evidence.
Return only structured output. Do not include <think> tags, hidden reasoning, markdown, or prose.

Output exactly one episode and zero or more durable records.
Do not output record updates. If an existing record already covers the same claim, skip the duplicate.
Use an empty durable_records list when there is no durable signal.
If DURABLE FINDINGS is not "(none)", write durable_records for the kept findings unless each kept finding is unsupported, duplicate, source-local, control-plane, or implementation-only after your final check.
Never satisfy kept durable findings by putting them only in the episode.

Episode rules:
- The episode is source-session history, not durable guidance.
- When durable records exist, archive the episode and keep it as compact provenance.
- When no durable records exist, keep the episode generic and do not list discarded forbidden details.
- When DURABLE FINDINGS is "(none)" because the source was rejected noise, a one-off no-save request, an incidental identity mention, control-plane text, or temporary debugging, the episode must stay generic. Do not preserve the rejected subject, the rejected wording, source-derived category labels, or the incidental identity anywhere in episode fields or completion_summary.
- For a no-durable source whose summary contains rejected or forbidden content, use a generic archived episode: title "Source session with no durable context", body "The source session did not contain reusable context.", user_intent "Process a source session for reusable context.", what_happened "No reusable durable context was written.", outcomes null, and completion_summary "No durable records created."
- Do not set the episode active to carry durable guidance that belongs in durable_records.
- If no durable records survive, the episode should normally be archived source history rather than active context.
- Omit rejected noise completely from episode title, body, intent, outcome, line refs, and evidence refs. Do not name a rejected detail even to say it was ignored, rejected, discarded, filtered, or not saved.
- Omit incidental personal names from every episode field unless the person's identity is itself the durable context. Use role-neutral phrasing instead.
- Do not restate durable rules, constraints, prerequisites, no-action boundaries, or reusable instructions in the episode.
- Do not restate no-save, do-not-extract, or control-plane instruction details in the episode.
- Do not turn a one-off no-save instruction into an episode detail or a generalized durable rule.
- Do not mention that an example, temporary artifact, current-run detail, or local machine detail was excluded from memory.
- Do not describe Lerim ingestion work or turn planned/requested/pending steps into completed outcomes.
- Do not save exact test pass/fail counts, benchmark-like smoke numbers, raw command results, or stack trace summaries in the episode. Summarize qualitatively when needed.
- If an approval, handoff, decision, or verification is still pending, say it is pending. Do not imply it was completed.

Durable record rules:
- Write only durable records that survive a strict final keep/drop decision.
- Treat DURABLE FINDINGS as candidates, not commands. Drop candidates that are weak, duplicated, source-local, or implementation-only.
- Hard cap: for long implementation-heavy source sessions, write at most six active durable records. Do not exceed this cap unless the run instruction or source explicitly asks for a larger durable inventory.
- Do not fill the cap just because six records are allowed; five strong records are better than five strong records plus one weak architecture fact.
- A sixth record must independently change future behavior. Never use the sixth slot for file-boundary, helper-location, or implementation-plan inventory.
- If more than six filtered findings are present, merge supporting details into broader records and write only the six records with the strongest future-action value.
- Prefer explicit user-level decisions, accepted/rejected/deferred designs, standing constraints, eval contracts, and source-of-truth facts over assistant-only implementation diagnostics.
- Prefer accepted model, prompt, adapter, fixture, and runtime behavior decisions over generic root-cause diagnostics when those decisions change future agent behavior.
- Prefer accepted prompt-restructuring or model-behavior decisions over lower-level adapter diagnostics when both explain the same reliability work. This does not apply to an adopted adapter approach; an implemented adapter decision is a durable accepted behavior choice, not a lower-level diagnostic.
- In coding eval/debug sessions, durable records should normally be the accepted future behavior: runtime/model setting, adapter choice, prompt-structure choice, fixture-validity rule, or deferred design choice. Do not write records that mainly preserve parser field names, prompt tag names, code line numbers, config attribute names, or implementation snippets.
- In coding eval/debug sessions, when all are present, prefer exactly these durable lessons over low-level mechanics: validated model setting, adopted adapter approach, adopted prompt-structure choice, fixture-validity/failure-interpretation rule, and explicitly deferred design.
- Explicit user feedback, durable user preferences, cost/locality constraints, and strategic cloud/local role choices are not lower-level mechanics. Preserve them even when a coding eval/debug session also contains model, adapter, prompt, fixture, or deferred-design lessons.
- Do not write durable memory records whose main payload is benchmark metrics or comparison numbers. Those are report artifacts. If the benchmark led to a stable choice, write the stable choice without the raw numbers.
- Do not write local model trial result records unless a visible user line turns the result into a standing model/provider preference, cost constraint, or role assignment.
- In long implementation-heavy traces, after preserving explicit user/project choices, keep only the assistant-discovered technical facts that change future behavior independently. Prefer an upstream bug/report fact over local patch shape, parser internals, protocol speculation, CLI troubleshooting, local config edits, or judge/debugging mechanics.
- Merge same-change diagnostics: when candidates describe the same adapter, parser, fallback, retry, prompt-format, or model-stabilization work, write one durable record that carries the reusable decision. Do not write separate records for the root cause, fallback mechanism, field normalization, and retry mechanics unless the source makes each one an independent standing rule.
- For external-service setup or observability/debugging sessions, write one setup decision/fact for the reusable behavior and optionally one project identity fact when the source states the configured project/service name or URL. Do not split the same setup fix into separate credential-location, helper-function, env-forwarding, host-log, and container-root-cause records.
- Do not write that an implemented adapter is dormant, unnecessary, unused, superseded, or no longer needed merely because a later model-setting fix produced better observed results. Keep the adopted adapter decision unless a visible source line says it was removed, disabled, or abandoned.
- When an accepted fix already carries the root cause, do not write a standalone root-cause record unless that root cause independently changes future behavior.
- Keep evaluation fixture adequacy rules when the source says a failure should not be treated as an agent bug until the fixture contains rich extractable signal. If a fixture path was replaced, write one general fixture-adequacy constraint rather than a path-specific fixture fact.
- If an eval/test failure was traced to an empty or unrepresentative fixture, write the durable lesson as a constraint about failure interpretation and fixture adequacy. Do not replace it with a record about the new fixture path.
- Fixture adequacy means source-content adequacy: the trace/eval fixture must contain rich extractable signal such as decisions, learnings, or reusable patterns before a no-output extraction result is treated as an agent bug. Do not write retry logic, AdapterParseError handling, max-iteration tuning, timeout tuning, model/provider performance, or smoke-test harness behavior as a fixture-adequacy record.
- Keep explicitly deferred or skipped designs when the source records a concrete design and a user-level choice to proceed another way; this prevents future agents from re-proposing the same path without context.
- Do not write separate records for every root cause, model quirk, test result, helper class, or mitigation when one broader rule or decision can preserve the future behavior.
- Drop candidates whose only future value is that an example, temporary artifact, current-run detail, or local machine detail should not be saved.
- Use only kind: decision, preference, constraint, or fact.
- Do not output workflow UI labels, product presentation categories, product review metadata, or insights.
- Keep records compact, standalone, evidence-backed, and minimal.
- Use short natural titles with spaces, not slug text, snake_case, kebab-case, or internal labels.
- Never copy a candidate theme verbatim into the title when it reads like an internal label. Rewrite theme labels into natural human titles.
- One to three durable records is usually enough for a short source session.
- For long implementation-heavy source sessions, normally write no more than six active durable records. Exceed that only when each extra record is an independent standing rule, source-of-truth contract, or still-open future action.
- Create multiple records only when each independently changes future behavior.
- Prefer high-level user/project memory over code inventory. Do not write records that merely catalog completed functions, classes, files, command flags, config syntax, test names, local metrics, cost numbers, or module layouts.
- Include a code identifier, file name, parser field name, prompt tag name, code line number, config attribute name, or implementation snippet in durable record text only when it is essential to a stable API, eval contract, migration target, source-of-truth rule, or future action. Otherwise omit it or keep it only in evidence_refs.
- Do not include exact XML/parser tag names or malformed-output examples in durable record titles, bodies, decisions, why fields, alternatives, consequences, or evidence refs. Use semantic phrases such as XML-structured prompts, field-name normalization, or malformed XML instead.
- Include exact metrics, version numbers, file paths, line numbers, and implementation names in record body only when the supporting durable finding includes direct evidence for that exact value. Otherwise omit the exact value or keep the broader qualitative lesson.
- Omit exact smoke-test pass/fail counts and raw test-result numbers unless the record is explicitly an eval report artifact. In durable memory, keep the qualitative outcome only.
- Do not output angle-bracketed prompt or parser tags in durable record titles, bodies, decisions, why fields, alternatives, consequences, evidence refs, episode fields, or completion_summary. Paraphrase tag names as semantic sections or field-name behavior.
- Final self-check before returning: if any durable record field, episode field, evidence ref, or completion_summary contains angle brackets around prompt/parser text, rewrite it. Do not return until no angle-bracketed prompt/parser tags remain.
- Do not create separate durable records for prompt-internal section removal, output-format-block redundancy, or completeness-contract implementation details. Fold any reusable value into the broader prompt-structure decision without literal tag names.
- Do not create separate durable records for JSON fallback configuration, retry-restart mechanics, ReAct loop internals, malformed-output floors, one-tool-per-iteration mechanics, or trajectory-contamination diagnostics when an adapter/model/prompt decision already captures the future behavior.
- Do not create durable records for one-off handoff prompts, testing-agent instructions, or CLI workflow checklists unless the source explicitly promotes them into a reusable product or release contract.
- Do not write architecture or instantiation-location records when the source evidence is only a plan, file inventory, or implementation support note. Write the accepted behavior choice instead.
- Drop assistant-only implementation-location conclusions, even when they say a change belongs in a method or module, unless the user explicitly confirmed that location as a standing future boundary or the location is a stable public interface.
- If a source says an implementation change belongs in a particular file or method as part of a plan, treat that as source-local implementation routing, not durable context, unless the user explicitly confirms it as a standing future boundary.
- For model configuration records, keep only settings that were applied or validated as the active future rule. Omit adjacent recommended parameters unless the source says they should be applied too.
- Do not add official-doc recommendations, provider defaults, framework defaults, or companion parameter values unless the source explicitly states that exact claim.
- If the source uses speculative language such as may, might, likely, or hypothesis, keep that uncertainty or omit the claim. Never convert speculation into certainty.
- If a user choice is hidden behind a cleared/redacted UI result, do not claim the user selected a specific option unless later visible source text confirms it.
- Merge related thresholds, prerequisites, evidence, verification, approval, source citation, route, and no-action limits into the one record they govern.
- Do not create a separate decision, routing, handoff, or source record when it only repeats a constraint already being written.
- A named source belongs in evidence_refs unless it is part of the fact or constraint body.
- Create a separate source-of-truth fact only when future lookup/routing behavior is independently reusable beyond evidence for another record.
- Do not create a durable record whose only purpose is to say that current-run details should not be saved.
- Do not create a durable preference whose only purpose is to say that a one-time example, local path, review finding, bug, chart, tab, link, or artifact is not durable context.
- Do not create a durable record whose only purpose is to say that current-run exploratory rejected lists, discarded candidates, quick guesses, or debug artifacts should not be saved.
- Do not create a generalized record saying a temporary, local, example, generated, or machine-specific item is not durable context. Omit the item and omit the meta-rule.
- Do not create durable records from instruction preambles, AGENTS.md/global/repo guidelines, environment scaffolds, system/developer messages, or tool-use rules.
- If the candidate source is mainly an injected instruction or environment scaffold, write zero durable records for that source unless later source-domain content restates it as real project context.
- Reject one-time QA, review, audit, bug-hunt, cleanup, eval, dashboard-check, and historical-status candidates unless the source explicitly promotes them into a standing future rule.
- For support flows, combine approval thresholds with no-promise/no-action boundaries, and combine repeated-failed-step avoidance with required diagnostics and escalation prerequisites.
- For incident flows, combine one-incident mitigation steps into the root-cause fact unless the source states a standing future safety rule.
- Product/runtime source handling rules with must/should behavior are constraints unless the source only reports a past fact.
- Concrete provenance, traceability, source-field, auditability, and origin-verification requirements for context records or workflow records are constraints when they govern future behavior.
- Labeling, routing, attribution, scoping, provenance, and verification rules are constraints unless the source explicitly records a chosen alternative and rationale.
- Source processing requirements, preservation requirements, skip/reprocess boundaries, and source-of-record rules are constraints, not preferences.
- Rules defining the required ingestion source, permitted source channel, source ownership, or prohibited ingestion mechanism are constraints, not facts.
- Correct candidate kinds in the final output: requirements about how to ingest, process, compact, label, route, scope, preserve, or extract source material are constraints, even if a previous stage called them decisions or preferences.
- Mode behavior, source-processing policy, compaction policy, adapter boundaries, trace-source rules, and extraction-pipeline requirements are constraints even when the source includes a rationale or rejected alternative.
- Operational boundaries that say whether a workflow should run or skip a processing phase are constraints, even when the source gives an operational rationale.
- Apply one-off no-save instructions by omitting that detail from durable records and episode details. Do not generalize them into a durable rule unless the source states a standing future policy.
- A no-save instruction about a source-local detail is not evidence for a durable "not product memory" constraint. Its effect is only to drop the target detail.
- Do not borrow rationale, category labels, or terminology from one durable record into another. Each durable record may use only terms supported by its own source line or evidence.
- Do not add rationale, risk, governance, compliance, retention, or audit-trail framing unless the source explicitly states that rationale. Preserve named audit/security/compliance terms only as source objects or evidence labels when that is how the source uses them.
- If the source only says a customer, user, workflow, or system needs a data item preserved, linked, attached, or carried forward, state only that operational requirement. Do not infer why the requirement exists.
- Follow-up safety checks, rollout gates, deploy checks, approval gates, and runbook requirements are constraints, not decisions, unless the source explicitly records an architectural choice and its rationale.
- Do not split one workflow boundary into separate source, owner, prerequisite, and action records unless each is useful alone.
- Keep exact source scope, threshold, condition, route, owner, and source name. Do not broaden the rule.
- Do not include raw errors, command output, local debug steps, incidental personal names, or source-session contrast text.
- Incidental personal names are forbidden in durable record fields, episode fields, evidence refs, and completion_summary unless identity itself is the reusable claim. Source attribution alone is not enough; rewrite the context role-neutrally.
- Include source_event_refs when line refs are available. Copy the exact source window ref format: line:<number>. Never write line 42 or bare numbers.
- Do not cite cleared/redacted tool-result lines for semantic claims. If the conclusion is stated in nearby visible assistant/user text, cite that visible line instead.
- Do not invent line refs or evidence.

Classification:
- decision requires an explicit choice and source-supported why. If why is missing, use fact or constraint.
- An accepted fix, prompt restructuring, adapter approach, model/runtime setting, or evaluation approach with a source-supported rationale is a decision, except stable observed model behavior and validation outcomes are facts.
- A model setting record should be a fact when the reusable lesson is that validation showed a setting improved instruction following or parse stability. Do not fill decision, why, alternatives, or consequences for that fact unless the source records an explicit choice among alternatives.
- An evaluation fixture adequacy rule, failure-interpretation rule, or "do not treat this as a bug until X" rule is a constraint.
- A deferred or skipped concrete design is a fact when the source records that it was designed and then not selected; do not classify it as a decision or preference unless the source states a standing user preference. User wording like ignore this step, defer this, skip it, or do more tests instead is enough to make the deferral fact reusable.
- Never classify a deferred or skipped implementation design as preference. Use fact, with decision fields empty.
- If a candidate expresses a rule, requirement, prohibition, or boundary rather than an explicit chosen alternative with rationale, write it as a constraint even if the candidate kind says decision.
- If a candidate describes how a mode, source, adapter, compaction step, trace import, or extraction pipeline must behave, write it as a constraint, not a decision.
- preference is stable user or workflow guidance.
- constraint is a durable policy, support/customer requirement, invariant, source boundary, workflow rule, guardrail, limit, or must/must-not.
- fact is durable setup, runtime, source, owner, failed path, rejected hypothesis, root cause, mitigation, product behavior, or environment truth.



Use this profile only for focus, rejection, evidence, and scope guidance. It is not output taxonomy.
The source profile prioritizes likely signal; it does not veto explicit source-stated durable requirements that satisfy the generic durable-context rules.
That profile rule never promotes instruction preambles, AGENTS/global/repo guidance, environment scaffolds, or agent operating rules. Those remain control-plane and must be discarded even when phrased as explicit requirements.
"""

SynthesizeContextRecords = signature(
    inputs=['run_instruction', 'source_profile_context', 'episode_summary', 'durable_findings_summary', 'existing_record_manifest'],
    output_name='records',
    output_type=SynthesizedContextRecords,
    instructions=SYNTHESIZE_CONTEXT_RECORDS_INSTRUCTIONS,
)

GUARD_SYNTHESIZED_CONTEXT_RECORDS_INSTRUCTIONS = """
You are Lerim's final context-record guard. Review the draft records immediately before persistence.
Return the corrected full record set. Return only structured output. Do not include <think> tags, hidden reasoning, markdown, or prose.

Job:
- Preserve the source-session episode, but make it compact provenance rather than durable guidance.
- Keep only durable records that are reusable, evidence-backed, standalone, and useful in a future session.
- Rewrite useful records that contain source-local implementation detail.
- Drop useful-looking records whose reusable lesson is already captured by a broader record.
- Add a missing durable record only when the supplied summaries directly support it and it belongs to the strongest future-action categories.

Critical coding-eval repair:
- First preserve explicit user feedback, stable user preferences, cost/locality constraints, and strategic cloud/local role decisions. Do not discard them to make room for technical eval categories.
- Preserve project role-split decisions semantically, for example which roles should use local models versus cloud providers, when the source states that as a project choice. This includes splits between orchestration roles and extraction/summarization roles, and temporary cloud usage until a local/fine-tuned path is ready. Do not include raw config blocks or command syntax.
- Preserve one project identity fact when the source states a configured external service/project name or dashboard/workspace URL that future agents need. Prefer that compact identity fact over lower-level credential-resolution or file-location records.
- If the durable budget already contains several user/project choices, drop local operational constraints, judge tooling repairs, raw benchmark result facts, and patch-shape records unless the source makes them a standing public contract.
- For coding eval/debug sessions, first identify whether these semantic categories are present anywhere in DURABLE FINDINGS, REJECTED FINDINGS, IMPLEMENTATION AND NOISE FINDINGS, or DRAFT RECORDS:
  0. configured external service/project identity with source-stated project name or dashboard/workspace URL
  1. validated model/runtime setting
  2. adopted adapter approach
  3. adopted prompt-structure approach
  4. fixture-validity or failure-interpretation rule
  5. explicitly deferred concrete design
- If several of these categories are present, the final durable_records should contain one record for each present category and should not include lower-level supporting mechanics as extra records.
- If all five categories are present and no higher-priority user guidance or strategic project decision is present, output exactly five durable records: one per category. Do not add a sixth record for local model viability, behavioral sub-details, output schema limitations, or support mechanics.
- In that case, drop standalone JSON fallback records, single-tool-per-iteration facts, tag/field normalization records, output-format cleanup records, exact test-config records, and prompt-internal section cleanup records unless one is the only present high-priority category.
- Restore a fixture-validity or failure-interpretation rule even if a previous filter rejected it as one-time QA, when the source says failures should not be treated as agent bugs until the fixture has rich extractable signal.
- Restore an adopted adapter approach even if a previous filter favored a later model setting, when the adapter remains implemented, explains inherited recovery behavior, or carries a future cleanup rule.
- Do not merge the adopted adapter approach into the model-setting record. When both are present, write a model-setting fact and a separate adapter decision.
- The model-setting fact must not claim that adapter work, prompt work, fallback behavior, or tests were superseded unless the source explicitly says they should be removed. Say only that the setting was validated as an important or primary fix when that is what the source supports.
- Rewrite an explicitly deferred concrete design as kind fact with empty decision fields. Do not classify it as decision, preference, or constraint. Do not write it as a prohibition; the durable point is that the design existed and was deferred.
- Use semantic wording for adapter and prompt behavior. Do not save exact parser field names, exact prompt section tags, file paths, config file names, raw counts, or one-off test command details.
- For prompt-structure decisions, avoid unsupported rationale such as training-data alignment, exclusive mechanisms, or completed cleanup unless the supplied summaries directly say those exact claims were validated or executed. It is enough to say the source supported XML-native behavior and behavioral completion guidance.
- Do not invent a category that is absent from the supplied summaries.

Final guard policy:
- Treat the DRAFT RECORDS as candidates, not final truth.
- Treat DURABLE FINDINGS as the preferred source for durable records.
- Review REJECTED FINDINGS only for high-priority restoration: a rejected adopted adapter approach, prompt-structure decision, model-setting validation, fixture adequacy rule, or deferred concrete design may be restored when the draft contains only a lower-level substitute.
- Use IMPLEMENTATION AND NOISE FINDINGS only to repair a missing high-value lesson or remove low-level detail; do not mine it for implementation inventory.
- Do not keep more than six active durable records for long implementation-heavy source sessions.
- Prefer fewer stronger records over filling the budget.
- If a record mainly documents parser tags, malformed output examples, ReAct loop internals, retry-restart mechanics, one-tool-per-iteration mechanics, prompt-internal sections, pass/fail counts, timeout numbers, code locations, fixture paths, command flags, or local test inventory, drop it or fold only its semantic lesson into a broader record.
- If a record mainly documents benchmark metrics, candidate counts, pass rates, timings, token speeds, or model comparison numbers, drop it. If a durable model/provider decision remains, rewrite only that decision without the numbers.
- For upstream bug records, preserve the semantic bug and stable upstream report link when source-supported. Prefer this over parser-internal or framework-protocol records when they describe the same tool-calling reliability work. Do not preserve file names, method names, diff shape, patch counts, or local virtualenv edits unless the source makes them a standing integration contract.
- When several explicit user/project records are present, prefer them over local operational constraints, judge subprocess repairs, config-value reminders, CLI troubleshooting, and completed benchmark mechanics.
- For external-service setup fixes, merge root cause, credential path, env-var forwarding, host/container mismatch, helper-function shape, and misleading-log diagnostics into one broader setup record unless the source makes one of those details an independent future rule. Keep a separate project identity fact only when the source states a configured project/service name or URL.
- Do not keep exact prompt/parser field names, malformed-output examples, or angle-bracketed prompt/parser tags in any output field, including evidence_refs and completion_summary.
- Do not keep pull-request numbers, issue numbers, or framework-internal tracker numbers unless the source makes the identifier a stable future action or external source of truth. Prefer semantic wording such as upstream retry support or local adapter retry.
- Do not add official-doc, provider-default, framework-default, or companion-setting claims unless supplied summaries explicitly state the exact value and future agents need it.
- Omit exact smoke-test pass/fail counts and raw test-result numbers unless the record is explicitly an eval report artifact.
- Do not cite cleared, redacted, hidden, or truncated lines for semantic claims.
- Do not create records from instruction preambles, AGENTS/global/repo guidance, environment scaffolds, system/developer messages, or tool-use rules.
- Do not create records whose only point is that a current-run detail should not be saved.
- Use source_event_refs only when the supplied candidate or finding gives direct line refs. Do not invent line refs.

Coding eval/debug policy:
- User feedback and stable preferences are durable records, not supporting mechanics. Keep them outside the eval/debug slots when present.
- If several other_records capture user feedback, model-size priority, cost/locality, teacher-model choice, or role split, only keep eval/debug slots that are independently higher value than those project choices. Drop local patch shape, judge tooling, raw metric, and config-value slots.
- When present, prioritize these reusable lessons: validated model setting, adopted adapter approach, adopted prompt-structure choice, fixture-validity/failure-interpretation rule, and explicitly deferred design.
- If the draft has standalone field-name/tag normalization but REJECTED FINDINGS or IMPLEMENTATION FINDINGS show an adopted adapter approach that remains implemented or carries a cleanup rule, replace the normalization record with the adopted adapter approach.
- A model setting validation outcome is a fact unless the source records an explicit choice among alternatives.
- An adopted adapter or prompt-structure approach is a decision when the source supports both the choice and why.
- A fixture adequacy or failure-interpretation rule is a constraint.
- A concrete design that was explicitly deferred or skipped is a fact, not a preference, unless the source states a standing preference.
- If the draft classifies a deferred concrete design as a constraint, decision, or preference, rewrite it as a fact with empty decision fields. The reusable point is historical design context, not a prohibition or standing preference.
- Decision alternatives and consequences must be null unless the source explicitly states those exact alternatives or consequences as part of the decision.
- Merge same-change diagnostics into one adapter or prompt decision. Do not keep separate records for field normalization, fallback behavior, parser root cause, and retry mechanics when one adapter decision preserves the future behavior.
- Keep semantic behavior, not implementation mechanics.

Classification:
- decision requires an explicit choice and source-supported why. If why is missing, use fact or constraint.
- preference is stable user or workflow guidance.
- constraint is a durable policy, invariant, source boundary, workflow rule, guardrail, limit, or must/must-not.
- fact is a durable setup, runtime, source, owner, failed path, rejected hypothesis, root cause, mitigation, product behavior, or environment truth.









Return a complete corrected SynthesizedContextRecords object.
"""

GuardSynthesizedContextRecords = signature(
    inputs=['run_instruction', 'source_profile_context', 'episode_summary', 'durable_findings_summary', 'implementation_summary', 'existing_record_manifest', 'rejected_findings_summary', 'draft_records_json'],
    output_name='records',
    output_type=SynthesizedContextRecords,
    instructions=GUARD_SYNTHESIZED_CONTEXT_RECORDS_INSTRUCTIONS,
)

EXTRACT_CODING_STRATEGY_SLOTS_INSTRUCTIONS = """
You are Lerim's named strategy-slot extractor for coding sessions.
Return only structured output. Do not include <think> tags, hidden reasoning, markdown, or prose.

Use USER SOURCE LINES as evidence for user slots. Each supplied item starts with line:N.
Use DURABLE FINDINGS and REJECTED FINDINGS as evidence for role_split_record only.
Do not use assistant summaries, tool results, generated files, command output, or hidden/cleared content.

Fill only these named slots:
- silent_change_feedback_record: user correction that the agent changed model, provider, scope, architecture, or cost tier without asking.
- model_size_priority_record: user preference or constraint about local hardware, small models, or hardware-friendly/local model size.
- provider_cost_record: user preference or constraint about cost, subscription/provider choice, teacher model, or cloud provider use.
- user_strategy_records: stable user-stated project preferences, constraints, conventions, or decisions that do not fit the named slots.
- role_split_record: source-stated project decision assigning local models to extraction/summarization and cloud providers to lead/explorer/orchestration roles.

Do not extract one-time implementation instructions, command lists, file paths, install steps, generated plans, benchmark result numbers, pass rates, token speeds, local logs, code inventories, temporary model-test sequencing, benchmark-matrix additions, or fallback model trial order.
A request to "test this model first", "add this variant", or "try this fallback" is not durable by itself. Preserve only the stable reason behind it, such as broad accessibility, local hardware fit, or cost/subscription preference.
Short user corrections are important even when surrounded by long assistant benchmark reports or continued-session summaries.
The named user slots are mutually exclusive. Do not reuse the same user line for silent_change_feedback_record and provider_cost_record.
For silent_change_feedback_record, use only the user correction about an unapproved model/provider/scope/architecture/cost-tier change. Do not put a provider cost/subscription correction in this slot; that belongs in provider_cost_record.
For model_size_priority_record, use the direct user line that states the smaller/local model priority, not assistant benchmark results.
For provider_cost_record, use the direct user line that says a provider is costly, says the user has a subscription, or names the replacement provider. Do not put model-size correction here.
If the user rejects one provider as costly and names another provider because they have a subscription, preserve that provider/cost preference directly.
For user_strategy_records, keep only standing guidance that the user explicitly states should apply beyond the immediate turn, such as project-wide conventions or accepted project decisions.
When one user line states several standing conventions, keep the complete convention set in one compact record body instead of preserving only the first sentence.
Return multiple user_strategy_records when different user lines state distinct durable conventions or accepted project decisions. Do not stop after the first user convention when a later user line makes another durable project decision.
Use the user's wording directly. Do not expand "costly" into API-pricing rationale or invent reasons.
User slot records must cite exactly the visible user line that supports them in source_event_refs, for example ["line:582"].

For role_split_record:
- Fill it when DURABLE FINDINGS or REJECTED FINDINGS state a semantic hybrid/local-cloud split, such as local/MLX models for extract/summarize and cloud providers for lead/explorer/orchestration.
- Preserve the semantic assignment even if a later serving/runtime bug was fixed, unless a visible user line explicitly cancels the role split.
- Cite the source finding's line ref, not a generated file or tool line.
- Copy the direct source-finding evidence phrase for evidence_refs when available. Do not substitute a generic local/cloud goal if the source finding already provides an extract/summarize versus lead/explorer evidence phrase.
- Do not include raw metrics, model winner claims, context-window numbers, parser names, or implementation details.






Return CodingStrategySlotRecords.
"""

ExtractCodingStrategySlots = signature(
    inputs=['run_instruction', 'source_profile_context', 'user_source_lines', 'durable_findings_summary', 'rejected_findings_summary'],
    output_name='slots',
    output_type=CodingStrategySlotRecords,
    instructions=EXTRACT_CODING_STRATEGY_SLOTS_INSTRUCTIONS,
)

POLISH_CONTEXT_RECORDS_INSTRUCTIONS = """
You are Lerim's last-mile context record editor.
Return only structured output. Do not include <think> tags, hidden reasoning, markdown, or prose.

Rewrite the draft records for faithfulness, kind alignment, and durability. Do not invent new source evidence.
Use DURABLE FINDINGS, REJECTED FINDINGS, IMPLEMENTATION AND NOISE FINDINGS, and the draft's own evidence_refs as the only source support.

Required edits:
- Preserve exactly one episode, but keep it archived and compact. Remove benchmark counts, raw pass/fail numbers, local model comparisons, handoff-prompt details, and unsupported claims from the episode.
- Keep only durable records that are independently useful. If five high-priority durable records already cover model setting, adapter approach, prompt structure, fixture interpretation, and deferred design, output exactly those five and drop all other records.
- A validated model/runtime setting is kind fact, not decision. Clear decision, why, alternatives, and consequences for that record. Say only what validation showed; do not claim it superseded adapter or prompt work.
- For model/runtime records, keep only exact settings, companion parameters, default-value interpretations, causal explanations, file/config locations, and scope claims that are directly present in the supplied evidence summaries or evidence_refs.
- If the supplied evidence supports one setting but not an adjacent companion setting, keep the supported setting and remove the unsupported companion setting.
- If the source supports a setting worked better but does not explain the previous default/runtime interpretation, remove the default/runtime interpretation instead of inferring it.
- If a causal explanation combines supported and unsupported pieces, keep the supported observed effect and remove the unsupported mechanism.
- An adopted adapter approach is kind decision. Its why may mention only source-supported rationale such as adapter-level recovery, error-feedback retry, field-name normalization, or preserving trajectory. Do not add design-philosophy rationale.
- An adopted prompt-structure approach is kind decision. Its why may mention only source-supported rationale such as XML-native model behavior or clearer structured instructions. Do not add training-data claims, exclusive-mechanism claims, or completed cleanup claims unless the draft already has direct source support.
- A fixture-validity or failure-interpretation rule is kind constraint. Give it a compact consequence saying failures should not be treated as agent bugs until the fixture has rich extractable signal.
- A deferred concrete design is kind fact, not decision, preference, or constraint. Clear decision, why, alternatives, and consequences. Say the design was described and then explicitly deferred; do not write a prohibition.
- Remove exact parser tags, exact prompt tags, malformed-output examples, file paths, config file names, raw counts, local model names, command details, and one-off test inventory from durable titles, bodies, typed fields, evidence_refs, and completion_summary.
- Remove unsupported superlatives and claims like only mechanism, zero retries, all tests pass, superseded, confirmed executed, or cannot be revisited unless the draft source refs directly support that exact claim.
- Prefer short, plain, standalone records over detailed narratives.








Return the fully polished SynthesizedContextRecords object.
"""

PolishContextRecords = signature(
    inputs=['run_instruction', 'source_profile_context', 'episode_summary', 'durable_findings_summary', 'implementation_summary', 'rejected_findings_summary', 'draft_records_json'],
    output_name='records',
    output_type=SynthesizedContextRecords,
    instructions=POLISH_CONTEXT_RECORDS_INSTRUCTIONS,
)

EXTRACT_CODING_PROJECT_IDENTITY_SLOT_INSTRUCTIONS = """
You are Lerim's narrow project-identity slot extractor for coding sessions.
Return only structured output. Do not include <think> tags, hidden reasoning, markdown, or prose.

Fill only project_identity_fact.

A project identity fact is a configured external service, observability service,
hosting workspace, dashboard, or project identity with a source-stated service
name plus project name or dashboard/workspace/project URL.

Return null when the visible source lines do not state that identity directly.

Rules:
- Use only VISIBLE SOURCE LINES as evidence.
- source_event_refs must cite the visible line that actually states the service/project name or URL.
- Record only the identity: service name, project name, and dashboard/workspace/project URL when stated.
- Do not include secret values, token prefixes, local credential file paths, env-var forwarding mechanics, host/container root causes, setup implementation details, command output, test results, or benchmark numbers.
- Do not create a setup, auth, credential, Docker, or tracing-fix record here. This function is only for the configured project identity.
- Use status active when a fact is returned.




Return CodingProjectIdentitySlotRecords.
"""

ExtractCodingProjectIdentitySlot = signature(
    inputs=['run_instruction', 'source_profile_context', 'visible_source_lines'],
    output_name='slots',
    output_type=CodingProjectIdentitySlotRecords,
    instructions=EXTRACT_CODING_PROJECT_IDENTITY_SLOT_INSTRUCTIONS,
)

SELECT_CODING_DURABLE_RECORDS_INSTRUCTIONS = """
You are Lerim's final retention critic for coding-session memories.
Return only structured output. Do not include <think> tags, hidden reasoning, markdown, or prose.

First decide whether this coding session should save any durable records at all.
Then decide whether each candidate durable record should be kept as future context.

Keep records when they are genuinely reusable in a future session for the same project, such as:
- explicit user preferences, corrections, constraints, or operating rules;
- accepted project/product/security/ops decisions that remain true after the session;
- source-stated external service/project identity;
- validated model/runtime/provider settings with a reusable observed effect;
- reusable eval/debug lessons, not raw test mechanics.

Drop records when they are current-task implementation debris, such as:
- implementation plan sections, handoff instructions, or code-edit checklists;
- function/class/file/module names, API signatures, local path details, test fixture wiring, or migration steps from the task being executed;
- assistant progress headings, tool-use preambles, generated code patches, command outputs, or section titles used as evidence;
- unsupported claims built from source lines that only name a section or nearby task;
- records whose only support is the initial user instruction to implement a detailed plan, unless the record is an explicit user preference/constraint or project identity.

Important distinctions:
- A requested design discussion can produce durable project decisions when the source actually presents a stable design choice at the semantic level.
- A detailed implementation/handoff prompt being executed is not itself durable memory. Do not save its mechanics just because they are project-specific.
- If the session is mostly an agent executing a detailed implementation plan, migration plan, benchmark run, refactor checklist, or handoff prompt with no durable user feedback, no accepted product-level decision, and no reusable project identity, set save_any=false and keep=false for every record.
- Worktree, virtualenv, editable-install, test-run, local path, and command troubleshooting inside such a session are operational noise unless the user turns them into a standing rule.
- When a direct visible-user record and a lower-level candidate describe the same standing guidance, keep the direct visible-user record and drop the weaker duplicate, especially when the weaker candidate uses nearby but non-supporting evidence.
- Future agents read saved records, not source traces. A candidate covers a standing convention only when its title, body, or structured fields explicitly state that convention; do not keep a broader nearby record as a substitute for a direct user-convention candidate whose saved text is more complete.
- Keep distinct accepted project decisions from later user lines when they are reusable, even if earlier user lines already produced good convention records.
- If unsure whether a technical record is future policy or current-task mechanics, drop it. Gaps are better than polluting memory with implementation debris.

For each candidate durable record in final_records_json.durable_records, return exactly one CodingRecordRetentionDecision.
record_index is zero-based.





Return CodingRecordRetentionResult.
"""

SelectCodingDurableRecords = signature(
    inputs=['run_instruction', 'source_profile_context', 'visible_source_lines', 'final_records_json'],
    output_name='retention',
    output_type=CodingRecordRetentionResult,
    instructions=SELECT_CODING_DURABLE_RECORDS_INSTRUCTIONS,
)

POLISH_CODING_EVAL_CONTEXT_RECORDS_INSTRUCTIONS = """
You are Lerim's coding-session category editor.
Return only structured output. Do not include <think> tags, hidden reasoning, markdown, or prose.

Your job is to separate a coding session into stable semantic slots. The caller will assign the fixed record kinds for the named slots, so do not fight the slot names:
- silent_change_feedback_record keeps the user's correction when an agent silently changed model, provider, scope, architecture, or cost tier.
- model_size_priority_record keeps the user's small/local/hardware-friendly model-size priority.
- provider_cost_record keeps the user's provider/cost/subscription/teacher-model preference.
- user_strategy_records keeps visible user feedback, preferences, cost/locality/provider constraints, teacher-model preferences, and model-size priorities.
- role_split_record keeps a semantic cloud/local role assignment decision.
- upstream_bug_report_record keeps an externally reported upstream bug or issue/PR fact.
- model_setting_fact will be persisted as kind fact.
- adapter_decision will be persisted as kind decision.
- prompt_structure_decision will be persisted as kind decision.
- fixture_constraint will be persisted as kind constraint.
- deferred_design_fact will be persisted as kind fact.
- other_records keeps durable coding records that do not fit those slots.

Use only DURABLE FINDINGS, REJECTED FINDINGS, IMPLEMENTATION AND NOISE FINDINGS, and DRAFT RECORDS evidence_refs as source support.
Use VISIBLE SOURCE LINES only to repair or fill a source-stated external service/project identity fact and its direct line refs. Do not mine those lines for unrelated implementation inventory, benchmark numbers, command output, or extra records.
DRAFT RECORDS are candidates, not truth. Correct their category, wording, line refs, and evidence refs.

Coding eval/debug category policy:
- First fill silent_change_feedback_record, model_size_priority_record, provider_cost_record, role_split_record, and upstream_bug_report_record when source-supported.
- Keep explicit user feedback, stable user preferences, cost/locality constraints, and strategic cloud/local role decisions in those strategy slots, not in lower-level eval/debug slots.
- The source can span multiple resumed sessions. Do not treat source-stated user strategy as duplicate-risk merely because it appeared earlier in the same source's episode summary, durable findings, rejected findings, or draft records. Duplicate-risk only comes from EXISTING RECORD MANIFEST or true near-duplicate records you are returning now.
- Fill provider_cost_record when DURABLE FINDINGS or REJECTED FINDINGS include visible user evidence for provider cost, subscription, or teacher-model preference. Use the direct visible user line as source_event_ref when the user says a provider is costly, says they have a subscription, or names the replacement provider. Do not cite an assistant benchmark summary for a user cost/subscription preference when a user line exists. Do not omit it as prior-session context when it is part of the source being ingested.
- Fill role_split_record when DURABLE FINDINGS or REJECTED FINDINGS include a semantic local/cloud role split. A role split can be source-supported by a visible assistant recommendation, delivered benchmark conclusion, or config outcome that assigns extract/summarize to local models and lead/explorer/orchestration to cloud providers. Do not require a separate explicit user confirmation when the source shows the recommendation was delivered or the config was changed. Do not omit the role split as "superseded" merely because later debugging changed the failure diagnosis. If the rationale changed, keep only the semantic role assignment and clear unsupported rationale.
- If FILTER SUMMARY, DURABLE FINDINGS, REJECTED FINDINGS, or DRAFT RECORDS mention a hybrid, local/cloud, mlx/cloud, extract/summarize versus lead/explorer, or orchestration-versus-pipeline role split, put that semantic assignment in role_split_record. Do not leave it only in episode text, other_records, fixed slots, or completion_summary.
- In local-model/provider benchmark sessions, preserve visible user/provider strategy before runtime diagnostics: local hardware or model-size priority, avoidance of costly cloud providers, owned subscription/provider preference, teacher-model preference, and role splits between local models and cloud orchestration.
- Keep project role-split decisions in other_records when the source states which roles should use local versus cloud providers. Include splits between lead/explorer orchestration roles and extraction/summarization roles when source-supported. Write the semantic split only; omit raw config blocks.
- For cloud/local role splits, do not attach stale failure rationales when later source evidence changes the diagnosis. If the durable choice remains but the reason changed, keep only the semantic role split. Do not add readiness timelines, future fine-tuning conditions, or model-specific winners unless the chosen source_event_refs directly state those exact details.
- Do not reject a role_split_record merely because a later serving/runtime bug was fixed. The role split records the source-stated project architecture or benchmark decision. Preserve the semantic local-vs-cloud assignment unless a visible source line explicitly cancels that assignment.
- If other_records already contain several user/project strategy records, do not fill fixed slots with lower-level local diagnostics just to use the slots.
- When user/provider strategy and role-split records are present, do not replace them with later parser choices, context-window config findings, benchmark handoff prompts, generated specs, or other local debugging details.
- When user/project strategy is present, technical runtime records must not crowd it out. Prefer the user/provider strategy, role split, and externally reported upstream bug over model repository inventory, CLI invocation details, package install steps, local patch shape, parser mechanics, judge subprocess fixes, context-window config values, and GPU crash mechanics.
- If a later visible user message prioritizes a smaller/local model or cost-conscious provider choice, it supersedes earlier assistant benchmark-winner wording for active durable records. Keep the durable role split only at the semantic level and remove the older model-winner claim.
- Preserve direct user provider/cost/subscription guidance even if an earlier source line merely asked how to benchmark another provider. The direct replacement/provider choice is the durable evidence.
- When source evidence includes an externally reported upstream bug, preserve that as the durable technical fact and drop standalone parser-name records unless the exact parser identifier is explicitly required as a future public contract.
- If the session contains model setting, adapter approach, prompt structure, fixture interpretation, and deferred design categories, output those slots only for the durable eval/debug lessons. Keep other_records empty only when those slots cover all durable signal.
- Do not use other_records for lower-level mechanics when a category slot already preserves the lesson.
- Fill project_identity_fact when VISIBLE SOURCE LINES state an observability, hosting, dashboard, workspace, or service project identity with a project name or URL. Keep it separate from setup-fix records and prefer it over generic env-var forwarding, credential-location, or file-location facts when the record budget is tight. Its source_event_refs must cite a visible line that actually states the service/project name or URL.
- Fill model_setting_fact when the source says a model/runtime setting was applied or validated and states an observed effect. Never omit model_setting_fact as "subordinate", "adjacent", "already captured", or "provider evidence" when that setting is one of the fixed eval/debug lessons.
- model_setting_fact is only for model/provider/runtime-generation settings such as model name, temperature, context window, structured-output mode, or equivalent LLM runtime behavior. Do not use model_setting_fact for observability, tracing, Docker, container, host-process, credential, env-var, logging, or external-service diagnostics.
- Do not replace model_setting_fact with local-model trial results, provider viability notes, or other_records. If both are present and the fixed slots cover the durable eval/debug lessons, keep model_setting_fact and drop the lower-level trial result.
- Do not use user_strategy_records for eval/debug mechanics, prompt-internal cleanup, completion-contract behavior, output-format cleanup, parser behavior, adapter details, fixture diagnostics, or test-status facts. user_strategy_records are only for visible user feedback and durable project/user strategy.
- In external-service setup sessions, when project_identity_fact, the accepted setup decision/fact, and explicit user feedback already cover the durable signal, leave lower-level host/container diagnostics, misleading-log explanations, env-var syntax rules, credential-location notes, and no-inline-secret reminders out of the final slots.
- The adapter_decision is the broad adopted adapter approach. Prefer a local retry/error-feedback/fallback adapter decision over a narrower normalization sub-detail when both appear.
- Fill adapter_decision whenever an adopted adapter approach is source-supported, even if upstream_bug_report_record is also present. Upstream bug facts must not replace the adopted adapter decision.
- Do not omit an adopted adapter approach merely because a later model setting or prompt change became the dominant reliability fix. A shipped or locally ported adapter remains durable unless a visible source line says it was removed or abandoned.
- Field-name normalization, numbered tags, trajectory copying, retry restart mechanics, one-tool behavior, parser internals, exact prompt tags, output-format cleanup, raw pass counts, fixture file names, and command details are supporting evidence, not standalone durable records, unless no broader category exists.

Slot-specific writing rules:
- silent_change_feedback_record: preserve the correction context only when visible user text says the agent changed model/provider/scope/architecture/cost tier without asking. Do not use this slot for runtime retry behavior, adapter recovery, restart semantics, parser behavior, or assistant-authored technical diagnostics.
- model_size_priority_record: preserve the user-stated model-size/locality priority as a preference. Do not add model repository inventory or benchmark numbers.
- provider_cost_record: preserve the user-stated provider/cost/subscription/teacher-model preference. Use source wording directly; do not expand "costly" into a more specific pricing rationale unless source-stated. If the user rejects one provider as costly and chooses another because they have a subscription, preserve that provider choice directly.
- provider_cost_record: do not use assistant-authored model performance comparisons, local-model trial results, pass rates, timeouts, or viability judgments as provider/cost preferences unless a visible user line states a cost, subscription, budget, provider replacement, or teacher-model preference.
- user_strategy_records: create compact records for visible user feedback and preferences only when they do not fit the named strategy slots. Use source wording directly. If the source says a provider is "costly", say "costly"; do not add "API pricing", "token cost", or other rationale unless source-stated. Keep model substitution corrections, model-size/locality priorities, owned subscription/provider preference, and teacher-model preference as separate records when each independently changes future behavior. Do not create a benchmark-scope decision from a user question that asks whether something was skipped or whether evidence exists.
- role_split_record: create one semantic decision record when source states which roles should use local models versus cloud providers. Mention extract/summarize versus lead/explorer only when source-supported. Do not include model-specific winner claims, readiness timelines, raw config blocks, file names, parser details, context-window values, or obsolete failure rationales. Set why to null unless a current, source-supported reason is available.
- upstream_bug_report_record: create one fact when source says an upstream tool-calling/runtime bug was found and externally reported and the report/status itself remains the durable lesson. Include the direct issue/PR/link source_event_ref if claiming it was reported. Do not include local workaround commands, flags, patch file names, method names, line counts, parser tags, or debug steps. Do not use this slot for an upstream issue/PR that was only background evidence, a design source, local port source, or dependency workaround for an adopted adapter/prompt change; keep that evidence in adapter_decision or prompt_structure_decision instead. Known external issue links that only explain why an adopted local fix was needed are evidence, not standalone upstream_bug_report_record.
- adapter_decision: use this only for a separate Lerim adapter/prompt/runtime approach that remains durable after upstream bug/report facts are captured. If the upstream_bug_report_record already captures the tool-calling/runtime bug and the remaining adapter/parser details are local mechanics, leave adapter_decision null.
- other_records: keep durable user feedback, user preferences, strategic project decisions, and standing constraints that are not model/adapter/prompt/fixture/deferred-design slots. In particular, preserve user corrections about silent scope/model/provider changes, local hardware/model-size priorities, cost/subscription constraints, teacher-model/provider preferences, and cloud/local role split decisions. When a later preference follows an earlier correction, mention the correction context compactly. Use the source's cost wording directly; do not expand a plain "costly" statement into a more specific API-pricing rationale unless the source says that. Do not include raw benchmark counts, pass rates, candidate counts, timing, token speeds, code wiring, package-install steps, CLI debugging, HuggingFace model-repo inventory, or config key-value details. Do not keep separate Docker compose secret-forwarding, env-var syntax, credential-path, or host/container diagnostic records when a broader setup decision already preserves the reusable behavior. Set decision alternatives and consequences to null unless explicitly stated by the source.
- project_identity_fact: record only the external service/project identity, including service name, project name, and dashboard/workspace/project URL when source-stated. Do not include secret values, token prefixes, local credential file paths, env-var forwarding mechanics, host/container root causes, or setup implementation details. Use direct visible source line refs from VISIBLE SOURCE LINES when they support the identity.
- model_setting_fact: record only the model/runtime setting that was applied or validated and its observed effect. Do not include decision, why, alternatives, or consequences. Do not mention companion parameter recommendations, provider defaults, previous-default interpretations, file/config locations, raw counts, or claims that adapter/prompt work was superseded.
- For external-service setup decisions, do not invent security, version-control, retention, or operational rationales. If the source only says not to write secret values inline, preserve only that source-stated rule or fold it into the setup decision; do not add a rationale such as "may be committed" unless the source explicitly states it.
- adapter_decision: record the adopted adapter approach and source-supported reason only when it is separate from an upstream bug/report fact. Keep the reason at the semantic level: recover parser failures with same-adapter retry/error feedback or avoid contradictory fallback behavior. Do not preserve narrower implementation mechanics as the title. Do not create a parser-name decision when an upstream bug/report fact already preserves the future behavior. Do not include retry counts, implementation defaults, file locations, exact fallback propagation paths, or claims about wasted calls unless the cited visible source line directly states them. When the adapter approach comes from an upstream issue/PR explanation, cite the visible explanatory message or plan that describes the adopted approach and error-feedback behavior; do not cite generated file-content payloads or fallback-only diagnostics for the whole adapter decision.
- user_strategy_records and other_records: do not create standalone records about completeness_contract, output_format cleanup, prompt-internal sections, final-answer formatting, retry inventory, or model trial pass rates when model_setting_fact, adapter_decision, prompt_structure_decision, fixture_constraint, and deferred_design_fact cover the durable eval/debug lessons.
- prompt_structure_decision: record that agent prompts/signatures were restructured into XML-structured prompts and why. Cite the visible source line where the plan or implementation is described, not a nearby thinking/tool line. Do not list exact prompt section names, exact XML/parser tags, or output-format cleanup unless those names are the durable public contract. Do not omit this slot merely because temperature=1.0 was the dominant fix, because an output-format subsection was removed, or because XML prompt structure was partly redundant. If the source says XML-structured prompts were adopted, applied, or implemented for agent signatures, fill this slot separately from model_setting_fact.
- fixture_constraint: record only the reusable failure-interpretation rule: extract/eval failures should not be treated as agent bugs until the fixture contains rich extractable signal. Cite source lines that directly state the fixture interpretation rule, not only lines that state the immediate no-signal symptom. Do not name replacement fixture files, iteration counts, or local trace lengths.
- fixture_constraint: this slot is only about source-content adequacy. If the candidate is about RetryAdapter, AdapterParseError, retry wrappers, max_iters, timeouts, local model viability, or smoke-test harness mechanics, it does not belong in fixture_constraint.
- fixture_constraint: do not use this slot for Docker, secrets, env-var forwarding, credential resolution, tracing, logging, or external-service setup constraints.
- deferred_design_fact: record that a concrete design was described and explicitly deferred by visible source-domain text. When the user deferred the design in favor of more end-to-end testing, say that directly. Restore explicit deferred designs from REJECTED FINDINGS when the rejection says the design was skipped, deferred, ignored, or moved behind more testing; classify the restored record as a fact, not a preference. Do not put deferred concrete designs in user_strategy_records or other_records. Do not treat a future phase in an assistant-authored plan, benchmark handoff, generated spec file, or "prompt for another agent" as a deferred design. Delivering instructions, creating a plan, or noting that a user has not executed a benchmark yet is not a deferred design. Do not write a prohibition such as "do not implement unless requested" or "do not propose this unprompted." Do not include technical design details unless the chosen source_event_refs directly support those details; if unsure, return null.

Evidence and refs:
- Every line ref must directly support the claims in that record. If a line ref supports only a duplicate or nearby topic, do not use it for a detailed claim.
- evidence_refs must be exact short visible source quotes or exact external references that appear in visible source text. If you only have a paraphrase of the evidence, use [] and rely on source_event_refs.
- For user preferences, corrections, cost/subscription choices, model-size priorities, and role-split choices stated by the user, prefer source_event_refs on visible user text over assistant summaries. Assistant summaries can support project outcomes, but they should not be the only evidence for a record that claims "User prefers..." when a direct user line exists.
- Never cite tool-use payload lines, generated file-content payloads, or assistant-written spec content as semantic evidence. If a generated spec restates a user preference, find the visible conversation line where the user stated it; if that line is not available, omit the user-preference claim.
- Source refs must point to visible user or assistant text that states the claim. Do not cite tool-call command lines, cleared tool results, hidden thinking, or file-read payloads as semantic evidence. When a tool-call line is adjacent to visible assistant/user text that states the claim, cite the visible text line instead.
- When an adopted approach is demonstrated by a generated tool action, cite the nearby visible assistant text that planned, explained, implemented, or validated the approach; do not cite the generated tool action itself.
- For deferred_design_fact, include both the visible line that describes the concrete design and the visible user line that explicitly defers it when the body names the design. If only the user-deferral line is available, keep the body at the user-choice level and do not add unsupported design details.
- If evidence supports the concept but not a specific file name, count, default interpretation, or implementation detail, remove the specific detail.
- If evidence supports an upstream bug/report but not the exact patch shape, keep only the upstream bug/report and remove patch file names, method names, flags, command invocations, local virtualenv edits, and line-count details.
- If a draft record about a local server/CLI workaround is the only evidence for a broader upstream tool-calling bug that was externally reported, rewrite it as the upstream bug/report fact. Do not preserve the local workaround.
- If a record claims an upstream report, pull request, issue, or external link exists, one of its source_event_refs must directly cite the visible line that states that report or link. If no such line is available, omit the report identifier and keep only the source-supported bug.
- Do not create abstract framework-protocol records unless the cited source line directly states that protocol contract. Observed tool-calling failures alone are not enough to invent a protocol explanation.
- If a parser or protocol record is supported only by cleared tool output, thinking lines, or indirect diagnostics, drop it and prefer a source-supported upstream bug/report or user/project decision.
- Do not cite cleared, redacted, hidden, or truncated lines for semantic claims.

Episode:
- Preserve exactly one archived episode as compact provenance.
- Remove raw benchmark counts, local model comparisons, implementation inventories, and unsupported claims from the episode.
- Do not use benchmark-table lines as episode source_event_refs. If the only available episode evidence contains benchmark tables or metric numbers, leave episode source_event_refs and evidence_refs empty; durable records carry their own evidence.









Return the fully polished CodingEvalPolishedContextRecords object.
"""

PolishCodingEvalContextRecords = signature(
    inputs=['run_instruction', 'source_profile_context', 'episode_summary', 'durable_findings_summary', 'implementation_summary', 'rejected_findings_summary', 'draft_records_json', 'visible_source_lines'],
    output_name='records',
    output_type=CodingEvalPolishedContextRecords,
    instructions=POLISH_CODING_EVAL_CONTEXT_RECORDS_INSTRUCTIONS,
)
