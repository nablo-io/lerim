enum RecordKind {
  DECISION @alias("decision") @description("A durable project decision with decision and why fields.")
  PREFERENCE @alias("preference") @description("A stable user or workflow preference.")
  CONSTRAINT @alias("constraint") @description("A durable invariant, limit, prohibition, or must/cannot rule.")
  FACT @alias("fact") @description("A durable project, runtime, source, setup, product, incident, or workflow truth.")
}

enum RecordStatus {
  ACTIVE @alias("active") @description("Current context that future sessions may reuse.")
  ARCHIVED @alias("archived") @description("Historical or routine context that should not be treated as active guidance.")
}

class DurableFinding {
  theme string @description("Short stable theme for this candidate.")
  kind RecordKind @description("Generic durable record kind. Use only decision, preference, constraint, or fact.")
  line int? @description("1-based supporting line when the window gives one.")
  quote string? @description("Short supporting quote from the current window.")
  note string @description("Compact semantic candidate. Avoid action logs and copied errors.")
}

class ImplementationFinding {
  theme string @description("Short theme for source-session-local evidence or noise.")
  line int? @description("1-based supporting line when the window gives one.")
  quote string? @description("Short supporting quote from the current window.")
  note string @description("Compact note explaining why this is only execution evidence, a discarded hypothesis, or local support.")
}

class SourceWindowScan {
  episode_update string? @description("Compact update for the final episode summary. May be omitted when this window adds nothing.")
  durable_findings DurableFinding[] @description("Reusable decisions, preferences, constraints, and facts found in this window.")
  implementation_findings ImplementationFinding[] @description("Implementation evidence, discarded hypotheses, and local details useful only as support/noise.")
  discarded_noise string[] @description("Short descriptions of noisy categories intentionally not saved.")
}

class SignalFilterResult {
  kept_durable_findings DurableFinding[] @description("Durable findings that passed the final reuse and dedupe filter.")
  rejected_findings DurableFinding[] @description("Candidate durable findings rejected as weak, duplicate, source-local, or implementation-only.")
  filtering_summary string? @description("Compact explanation of the filtering decision.")
}

class EpisodeDraft {
  title string @description("Short source-session title. Describe the original activity, not Lerim ingestion work.")
  body string @description("Compact episode body about the original source session. Do not describe created records or ingestion work.")
  status RecordStatus @description("Use archived for routine/no-durable sessions; active only when the episode itself remains useful.")
  user_intent string @description("What the user wanted in this source session, in source-domain terms.")
  what_happened string @description("What the source session actually did, in source-domain terms.")
  outcomes string? @description("Optional concise outcome.")
  source_event_refs string[] @description("Trace line refs that support this episode, for example line:12. Use [] only when no source line exists.")
  evidence_refs string[] @description("Short evidence refs or quotes that support this episode. Use [] when no evidence ref is available.")
}

class DurableRecordDraft {
  kind RecordKind @description("Generic durable record kind.")
  title string @description("Short standalone durable title.")
  body string @description("Compact standalone durable body.")
  status RecordStatus? @description("Use active for every reusable durable record.")
  decision string? @description("Decision text only when kind is decision and the source supports both the choice and why.")
  why string? @description("Decision rationale only when the source directly supports it.")
  alternatives string? @description("Decision alternatives only when explicitly supported by the source.")
  consequences string? @description("Decision consequences only when explicitly supported by the source.")
  source_event_refs string[] @description("Trace line refs that directly support this durable record, for example line:12. Use [] only when no source line is available.")
  evidence_refs string[] @description("Short evidence refs or quotes that directly support this durable record.")
}

class SynthesizedContextRecords {
  episode EpisodeDraft @description("Exactly one current-session episode record draft.")
  durable_records DurableRecordDraft[] @description("Zero or more durable records.")
  completion_summary string? @description("Brief summary of ingestion work for final_result/reporting.")
}

function ObserveSourceWindow(
  run_instruction: string,
  source_profile_context: string,
  prior_episode_summary: string,
  prior_findings_summary: string,
  source_window: string
) -> SourceWindowScan {
  client MiniMaxM27
  prompt #"
    {{ _.role("system") }}
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
    Implementation detail alone is not durable context.
    Routine actions, local tool calls, transient debugging, raw command output, temporary workspace state, eval chatter, review comments, and no-save artifacts are not durable context.
    No-save, do-not-extract, and do-not-remember instructions about current trace details only filter this source session; do not turn them into durable records or episode details.
    Store policy, verification, approval, routing, source-of-truth, and no-action boundaries as one compact generic durable record when they govern the same future behavior.
    Do not split one reusable point into separate source, prerequisite, evidence, and action findings.
    Routing or handoff that simply follows from an approval gate or prerequisite belongs inside that constraint, not a separate decision.
    No-action boundaries such as do-not-promise, do-not-repeat, do-not-escalate-yet, or wait-for-approval belong inside the threshold/prerequisite/escalation constraint they qualify.
    Support troubleshooting guidance that combines failed-step avoidance, required diagnostics, and escalation criteria should be one constraint, not separate fact and constraint records.
    Incident recovery steps belong inside the root-cause fact unless they establish a standing future response rule.
    Product/runtime ingestion boundaries, source-handling rules, and must/should behavior are constraints, even when the source phrase is written as "I will".
    Do not create a standalone source record when the only value is citing a source for a constraint, fact, or decision you already found.
    A trace saying "policy source: X" is evidence for the rule. Keep source-of-truth lookup behavior inside the relevant fact or constraint.
    Write a separate source-of-truth fact only when the source choice independently changes future lookup or routing behavior, such as replacing stale local owner notes.
    Keep source identifiers as evidence when they support the durable record.
    Keep ticket, case, incident, or trace-local identifiers out of durable findings unless the reusable record is explicitly scoped to that exact ongoing item.
    If a window has no durable signal, return an empty durable_findings list.
    Every durable finding must include kind, theme, note, and direct evidence when available.

    {{ _.role("user") }}
    RUN INSTRUCTION:
    {{ run_instruction }}

    SOURCE PROFILE:
    {{ source_profile_context }}

    Use this profile only to understand domain focus, rejection rules, evidence expectations, and scope. It is not a taxonomy to output.

    PRIOR EPISODE SUMMARY:
    {{ prior_episode_summary }}

    PRIOR FINDINGS SUMMARY:
    {{ prior_findings_summary }}

    SOURCE SESSION WINDOW:
    {{ source_window }}

    {{ ctx.output_format }}
  "#
}

function FilterDurableSignal(
  run_instruction: string,
  source_profile_context: string,
  episode_summary: string,
  durable_findings_summary: string,
  implementation_summary: string,
  existing_record_manifest: string
) -> SignalFilterResult {
  client MiniMaxM27
  prompt #"
    {{ _.role("system") }}
    You are Lerim's durable-signal filter.
    Return only structured output. Do not include <think> tags, hidden reasoning, markdown, or prose.

    Keep only findings that are durable, reusable, evidence-backed, standalone, and useful in a future session.
    Reject findings that are:
    - implementation-only, source-local, routine, temporary, noisy, duplicate, or weakly inferred.
    - generic domain knowledge not established by this source.
    - session-control instructions, local tool actions, eval findings, audit notes, stale status snapshots, or no-save artifacts.
    - instruction preambles, AGENTS.md/global/repo guidelines, system/developer messages, environment scaffolds, cwd/shell/current-date blocks, and tool-use rules.
    - meta-rules whose only meaning is "do not save/extract/remember this current detail" unless the source explicitly turns them into a standing future operating rule.
    - fragments that only make sense when read with another finding.

    Prefer the fewest findings that preserve all future-action value.
    Merge related prerequisites, thresholds, evidence requirements, verification steps, approval gates, source citations, and no-action boundaries into the one durable finding they govern.
    Reject routing, escalation, or handoff candidates when the same future behavior is already captured by a kept approval or verification constraint.
    Reject standalone no-action findings when the same threshold, prerequisite, or escalation record can carry that boundary.
    Reject standalone source facts when the source is only evidence for a kept rule. Keep a source-of-truth fact only when future lookup/routing behavior changes independently.
    Reject one-time review, QA, eval, bug-hunt, dashboard-check, or historical-status findings unless the user explicitly accepts them as standing future context.
    Reject current-run control-plane instructions even when they sound like valuable project rules.
    Do not keep a separate source lookup finding when a fact or constraint already captures the complete applicable rule and source evidence.
    Fold source choice, owner lookup, or source-of-truth routing into the relevant fact or constraint.
    A source citation is evidence. Reject it as a standalone finding unless it changes a kept fact or constraint.
    Use only the generic durable kinds: decision, preference, constraint, fact.
    Do not emit workflow UI labels or product presentation categories.
    Treat EXISTING RECORD MANIFEST as duplicate-risk context. Do not keep near-duplicates unless the source adds a correction or materially stronger durable value.
    If no candidate passes this bar, return an empty kept_durable_findings list.

    {{ _.role("user") }}
    RUN INSTRUCTION:
    {{ run_instruction }}

    SOURCE PROFILE:
    {{ source_profile_context }}

    EXISTING RECORD MANIFEST:
    {{ existing_record_manifest }}

    EPISODE SUMMARY:
    {{ episode_summary }}

    CANDIDATE DURABLE FINDINGS:
    {{ durable_findings_summary }}

    IMPLEMENTATION AND NOISE FINDINGS:
    {{ implementation_summary }}

    {{ ctx.output_format }}
  "#
}

function SynthesizeContextRecords(
  run_instruction: string,
  source_profile_context: string,
  episode_summary: string,
  durable_findings_summary: string,
  existing_record_manifest: string
) -> SynthesizedContextRecords {
  client MiniMaxM27
  prompt #"
    {{ _.role("system") }}
    You are Lerim's context writer. Write final context records from filtered source-session findings.
    Return only structured output. Do not include <think> tags, hidden reasoning, markdown, or prose.

    Output exactly one episode and zero or more durable records.
    Do not output record updates. If an existing record already covers the same claim, skip the duplicate.
    Use an empty durable_records list when there is no durable signal.

    Episode rules:
    - The episode is source-session history, not durable guidance.
    - When durable records exist, archive the episode and keep it as compact provenance.
    - When no durable records exist, keep the episode generic and do not list discarded forbidden details.
    - Do not restate durable rules, constraints, prerequisites, no-action boundaries, or reusable instructions in the episode.
    - Do not restate no-save, do-not-extract, or control-plane instruction details in the episode.
    - Do not describe Lerim ingestion work or turn planned/requested/pending steps into completed outcomes.
    - If an approval, handoff, decision, or verification is still pending, say it is pending. Do not imply it was completed.

    Durable record rules:
    - Write only from DURABLE FINDINGS that passed the filter.
    - Use only kind: decision, preference, constraint, or fact.
    - Do not output workflow UI labels, product presentation categories, product review metadata, or insights.
    - Keep records compact, standalone, evidence-backed, and minimal.
    - Use short natural titles with spaces, not slug text, snake_case, kebab-case, or internal labels.
    - One to three durable records is usually enough for a short source session.
    - Create multiple records only when each independently changes future behavior.
    - Merge related thresholds, prerequisites, evidence, verification, approval, source citation, route, and no-action limits into the one record they govern.
    - Do not create a separate decision, routing, handoff, or source record when it only repeats a constraint already being written.
    - A named source belongs in evidence_refs unless it is part of the fact or constraint body.
    - Create a separate source-of-truth fact only when future lookup/routing behavior is independently reusable beyond evidence for another record.
    - Do not create a durable record whose only purpose is to say that current-run details should not be saved.
    - Do not create durable records from instruction preambles, AGENTS.md/global/repo guidelines, environment scaffolds, system/developer messages, or tool-use rules.
    - For support flows, combine approval thresholds with no-promise/no-action boundaries, and combine repeated-failed-step avoidance with required diagnostics and escalation prerequisites.
    - For incident flows, combine one-incident mitigation steps into the root-cause fact unless the source states a standing future safety rule.
    - Product/runtime source handling rules with must/should behavior are constraints unless the source only reports a past fact.
    - Do not split one workflow boundary into separate source, owner, prerequisite, and action records unless each is useful alone.
    - Keep exact source scope, threshold, condition, route, owner, and source name. Do not broaden the rule.
    - Do not include raw errors, command output, local debug steps, incidental personal names, or source-session contrast text.
    - Include source_event_refs when line refs are available. Copy the exact source window ref format: line:<number>. Never write line 42 or bare numbers.
    - Do not invent line refs or evidence.

    Classification:
    - decision requires an explicit choice and source-supported why. If why is missing, use fact or constraint.
    - preference is stable user or workflow guidance.
    - constraint is a durable policy, invariant, source boundary, workflow rule, limit, or must/must-not.
    - fact is durable setup, runtime, source, owner, failed path, rejected hypothesis, root cause, mitigation, product behavior, or environment truth.

    {{ _.role("user") }}
    RUN INSTRUCTION:
    {{ run_instruction }}

    SOURCE PROFILE:
    {{ source_profile_context }}

    Use this profile only for focus, rejection, evidence, and scope guidance. It is not output taxonomy.

    EXISTING RECORD MANIFEST:
    {{ existing_record_manifest }}

    EPISODE SUMMARY:
    {{ episode_summary }}

    DURABLE FINDINGS:
    {{ durable_findings_summary }}

    {{ ctx.output_format }}
  "#
}
