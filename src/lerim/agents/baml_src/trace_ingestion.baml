enum RecordKind {
  DECISION @alias("decision") @description("A durable project decision with decision and why fields.")
  PREFERENCE @alias("preference") @description("A stable user or workflow preference.")
  CONSTRAINT @alias("constraint") @description("A durable user/product policy invariant, limit, prohibition, or must/cannot rule; not a runtime setup, path, access, dependency, or environment fact.")
  FACT @alias("fact") @description("A durable project fact, setup truth, dependency, path, access, runtime requirement, or environment requirement.")
  REFERENCE @alias("reference") @description("A pointer to an external source of truth.")
}

enum RecordStatus {
  ACTIVE @alias("active") @description("Current context that future sessions may reuse.")
  ARCHIVED @alias("archived") @description("Historical or routine context that should not be treated as active guidance.")
}

enum FindingLevel {
  DECISION @alias("decision") @description("Durable decision-level finding.")
  PREFERENCE @alias("preference") @description("Durable user or workflow preference finding.")
  FEEDBACK @alias("feedback") @description("Durable feedback-level finding.")
  REFERENCE @alias("reference") @description("Durable external-source finding.")
  CONSTRAINT @alias("constraint") @description("Durable constraint-level finding.")
  FACT @alias("fact") @description("Durable fact-level finding.")
  IMPLEMENTATION @alias("implementation") @description("Source-session-local implementation evidence or discarded hypothesis.")
}

class SourceWindowFinding {
  theme string @description("Short stable theme for this finding.")
  level FindingLevel @description("Use durable levels for reusable context, implementation for local/noisy evidence.")
  line int? @description("1-based supporting line when the window gives one.")
  quote string? @description("Short supporting quote from the current window.")
  note string @description("Compact semantic finding. Avoid action logs and copied errors.")
}

class SourceWindowScan {
  episode_update string? @description("Compact update for the final episode summary. May be omitted when this window adds nothing.")
  durable_findings SourceWindowFinding[] @description("Reusable decisions, preferences, constraints, facts, and references found in this window.")
  implementation_findings SourceWindowFinding[] @description("Implementation evidence, discarded hypotheses, and local details useful only as support/noise.")
  discarded_noise string[] @description("Short descriptions of noisy categories intentionally not saved.")
}

class SignalFilterResult {
  kept_durable_findings SourceWindowFinding[] @description("Durable findings that passed the final reuse and dedupe filter.")
  rejected_findings SourceWindowFinding[] @description("Candidate durable findings rejected as weak, duplicate, source-local, or implementation-only.")
  filtering_summary string? @description("Compact explanation of the filtering decision.")
}

class EpisodeDraft {
  title string? @description("Optional short title for the current-session episode. Runtime derives one if omitted.")
  body string? @description("Compact episode body. If omitted, runtime builds it from user_intent and what_happened.")
  status RecordStatus? @description("Use archived for routine/no-durable sessions; active only when the episode itself remains useful.")
  user_intent string? @description("What the user wanted in this source session. Runtime derives a generic value if omitted.")
  what_happened string? @description("What the session actually did. Runtime derives a generic value if omitted.")
  outcomes string? @description("Optional concise outcome.")
}

class DurableRecordDraft {
  kind RecordKind @description("Durable record kind.")
  title string @description("Short standalone durable title.")
  body string @description("Compact standalone durable body.")
  status RecordStatus? @description("Usually active for reusable durable records.")
  valid_from string?
  valid_until string?
  decision string?
  why string?
  alternatives string?
  consequences string?
}

class DurableRecordUpdateDraft {
  record_id string @description("Exact existing record_id from the existing record manifest.")
  kind RecordKind @description("Durable record kind.")
  title string @description("Complete replacement title.")
  body string @description("Complete replacement body.")
  status RecordStatus?
  valid_from string?
  valid_until string?
  decision string?
  why string?
  alternatives string?
  consequences string?
  change_reason string?
}

class SynthesizedContextRecords {
  episode EpisodeDraft @description("Exactly one current-session episode record draft.")
  durable_records DurableRecordDraft[] @description("Zero or more durable records.")
  record_updates DurableRecordUpdateDraft[] @description("Zero or more complete updates to existing durable records.")
  completion_summary string? @description("Brief summary of ingestion work for final_result/reporting.")
}

function ObserveSourceWindow(
  run_instruction: string,
  prior_episode_summary: string,
  prior_findings_summary: string,
  source_window: string
) -> SourceWindowScan {
  client MiniMaxM27
  prompt #"
    {{ _.role("system") }}
    You are Lerim's source-session observer. Scan one window from an agent activity stream for context ingestion.
    Return only structured output. Do not save records and do not plan future tool calls.
    Do not include <think> tags, hidden reasoning, markdown, or prose.
    The top-level output must include episode_update, durable_findings, implementation_findings, and discarded_noise.
    Use an empty string or empty list when a field has no content.
    Every durable_findings and implementation_findings item must be an object with:
    - theme
    - level: one of decision, preference, feedback, reference, constraint, fact, or implementation
    - note
    - optional line
    - optional quote
    Never return finding items as plain strings.
    Never use confidence labels such as high, medium, or low as finding levels.

    Separate:
    - episode_update: what happened in this session window, for the final episode record.
    - durable_findings: reusable project/user context only.
    - implementation_findings: local execution evidence, transient actions, discarded hypotheses, or support.
    - discarded_noise: categories of content intentionally ignored.

    Durable signal means a decision, preference, constraint, fact, or reference likely useful beyond this source session.
    Implementation detail alone is not durable signal.
    A durable finding should help a future agent make a better decision in a new chat.
    Save what a future agent would be relieved to already know: stable user preferences, chosen policies, durable constraints, source-of-truth references, and stable project or product truths.
    If a detail only helps replay or debug this source session, put it in implementation_findings or discarded_noise, not durable_findings.
    One-off local corrections, stale artifacts, branch-specific cleanup, and routine validation fixes are not durable unless the source establishes a reusable rule, stable requirement, user preference, or source-of-truth pointer.
    Raw metrics, comparison tables, configuration edits, integration wiring, setup actions, debug steps, internal paths, and error dumps are implementation_findings unless the user turns them into a reusable policy, preference, constraint, fact, or source-of-truth reference.
    If the enduring value is where future sessions should look outside the current workspace or local source material for authoritative context, status, incidents, requirements, or decisions, classify it as reference.
    When the same topic has both user-level guidance and technical evidence, the user-level guidance is the durable finding; the technical evidence is implementation support.
    When the source session corrects, tightens, clarifies, strengthens, or completes an existing durable record or durable rule, return that correction as a durable finding even when the right persistence action is an update rather than a new record.
    If an existing record ID is visible in the source session or manifest, mention it in the finding note; do not create a new durable theme for the same claim.
    When a durable point is clarified by contrasting it with an older, rejected, or nearby point, write only the durable point in the finding note. Do not carry the contrast target into the finding text unless that target is itself being updated.
    When a later turn only explains how to apply a core decision or boundary to roles, components, workflows, channels, or artifacts, merge that application guidance into the core finding rather than emitting a second durable finding.
    If the source session frames something as one decision, one rule, or one durable point, return at most one durable finding for that theme.
    Multiple windows inside the same source session are one evidence stream. If a later window says a durable point is unchanged from earlier windows, treat that as confirmation within this source session, not as proof the point already exists in durable context.
    Only treat a durable point as already persisted when it appears in the existing record manifest or as an explicit existing record ID.
    If the source asks to capture only one durable lesson or says the final context should be one durable record, keep only that durable point and put supporting observations in implementation_findings.
    If later evidence clarifies that an earlier interpretation was a distraction, reject the earlier lure and keep only the clarified durable point.
    If a late source turn explicitly says earlier theories, details, or repeated evidence were distractions/noise and names the real durable point, keep the late durable point even if earlier lures appeared more often.
    Meta-instructions about what this ingestion should remember, ignore, prioritize, or avoid saving are filtering guidance, not standalone workflow preferences. Use them to choose the durable point; do not emit them as preference findings unless the user clearly states a broader working-style preference for future sessions.
    Instructions about this ingestion or persistence operation itself are not durable preferences: do not save rules whose only subject is avoiding duplicate records, updating an existing record, citing a record ID, keeping one record, or deciding create-versus-update for this source session.
    Classify stable runtime setup, deployment, dependency, access, path, source, or environment requirements as fact unless the source session explicitly establishes a user/product policy constraint or prohibition.
    If the lasting point is that some resource, configuration, dependency, access, path, source, or environment condition must be present for the system to run correctly, classify it as fact even when it is operationally mandatory.
    Never use constraint for operational availability requirements about resources, paths, dependencies, access, or environment conditions. Use fact.
    Classify durable behavioral, data-model, process, policy, or product invariants as constraints when they limit what future work may do.
    A constraint can describe cardinality, exclusivity, allowed or forbidden states, boundaries, compliance rules, or non-negotiable workflow rules.
    Do not downgrade a true invariant to fact merely because it uses "must" language; only setup, dependency, access, path, source, and environment availability requirements default to fact.
    Internal paths or local artifacts are implementation evidence by themselves; they become durable facts only when the source establishes that a future run must provide that scope, access, or environment condition for the system to operate.
    The words "must" or "require" do not by themselves make runtime setup, deployment, dependency, or environment context a constraint; setup requirements remain facts.
    For operational blockers, keep durable_findings at the capability, status, and implication level only. Put repair recipes, flags, implementation identifiers, tool actions, exact metrics, and local debug steps in implementation_findings.
    When a window mixes a durable point with local evidence, put only the reusable point in durable_findings and put the supporting local evidence in implementation_findings.
    Use semantic meaning, not exact wording. Do not copy long artifacts, logs, raw errors, or tool transcripts.
    Do not carry incidental personal names, direct address labels, or conversational identity markers into episode updates or findings unless the identity itself is the durable context. Prefer role-neutral wording such as "the user" and "the assistant".
    If the window has no durable signal, return an empty durable_findings list.
    If the window adds nothing meaningful to the episode, episode_update may be an empty string.

    {{ _.role("user") }}
    RUN INSTRUCTION:
    {{ run_instruction }}

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
    The top-level output must include kept_durable_findings, rejected_findings, and filtering_summary.
    Use empty lists when no candidates pass or no candidates are rejected.
    Every kept_durable_findings and rejected_findings item must be an object with:
    - theme
    - level: one of decision, preference, feedback, reference, constraint, fact, or implementation
    - note
    - optional line
    - optional quote

    Be aggressive. Most source windows contain execution chatter, local investigation, tool work, partial hypotheses, or recap material.
    Keep only durable signal that would change what a future agent does in a separate session.
    Reject candidates that are:
    - duplicate or near-duplicate of an existing record without new durable value
    - implementation evidence rather than a reusable decision, preference, constraint, fact, or reference
    - generic domain or tool knowledge
    - a source-session-local tool action, artifact edit, validation run, failure symptom, or validation detail
    - a one-off local correction, stale artifact, branch-specific cleanup, or routine validation fix with no reusable rule
    - merely an instruction about how to summarize this source session
    - a weak inference without stable support

    Prefer one stronger kept finding over several overlapping findings.
    Existing records are duplicate-risk context, not a reason to discard durable corrections.
    If a candidate strengthens, corrects, completes, or adds durable rationale to an existing manifest record, keep it as update-worthy signal instead of rejecting it as a duplicate.
    If the source explicitly asks to tighten, update, revise, clarify, or complete an existing durable record rather than create a duplicate, keep that signal as an update-worthy finding whenever the manifest contains a matching record.
    Scanner labels are provisional. If IMPLEMENTATION AND NOISE FINDINGS contains a clear correction, tightening, clarification, strengthening, or completion of an existing manifest record, promote that meaning into kept_durable_findings as update-worthy durable signal.
    Scanner ingestion can be incomplete. If CANDIDATE DURABLE FINDINGS is "(none)" or misses a point, but EPISODE SUMMARY or IMPLEMENTATION AND NOISE FINDINGS clearly states a reusable source-session decision, preference, constraint, fact, reference, or existing-record correction, reconstruct the minimal kept_durable_findings item for that source-session signal.
    Use recovery only when the source-session summary or evidence itself supports the durable claim. Never reconstruct durable signal from RUN INSTRUCTION, ingestion guidance, writing rules, generic engineering knowledge, or the existence of the manifest alone.
    Reject near-duplicates only when the existing record already fully represents the candidate.
    If one candidate is an application, consequence, or local example of another, keep the stronger durable point and reject the local derivative.
    If one candidate names a core operating boundary and another only routes actors, workflows, channels, or artifacts to the sides of that boundary, keep the boundary and reject the routing/application candidate after folding its useful guidance into the boundary note.
    If two candidates mention the same durable entities and differ only by responsibility assignment, routing, ownership, read/write direction, or operational application, keep one record for the underlying boundary and fold useful application guidance into it.
    Do not split a structural boundary from access or routing guidance that merely tells which actors, tools, operations, or workflows use each side. Store one durable record for the boundary and fold access/routing into it.
    If one setup candidate states a requirement and another states the validated configuration, implementation, or state that satisfies the same requirement, keep one fact that states the requirement and brief current satisfying state when useful.
    If the source session frames the durable signal as one decision, one rule, or one durable point, keep at most one durable finding for that theme.
    Multiple windows inside the same source session are one evidence stream. If a candidate is repeated or described as unchanged across windows, fold it into one kept finding instead of rejecting it as pre-existing.
    Only reject a candidate as already represented when the existing record manifest fully represents it. A prior source window, checkpoint, or episode summary line is not an existing durable record.
    If the source asks to capture only one durable lesson or says the final context should be one durable record, keep only the strongest durable point and reject supporting observations as implementation evidence.
    If later evidence clarifies the real durable point and marks earlier interpretations as distractions, keep only the clarified durable point.
    If a late source turn explicitly says earlier theories, details, or repeated evidence were distractions/noise and names the real durable point, keep the late durable point even if earlier lures appeared more often.
    If a fact candidate only explains the rationale, driver, consequence, prerequisite, recovery requirement, or supporting evidence for a decision candidate in the same source session, fold it into the decision and reject the separate fact.
    Keep a separate fact next to a decision only when the fact would remain independently useful even if the decision record did not exist.
    When keeping a finding that was discovered through contrast with an older, rejected, or nearby point, rewrite the kept note so it states only the durable point. Do not include the contrast target in kept_durable_findings.
    Meta-instructions about what future agents should remember, ignore, prioritize, or not save from the source session are ingestion guidance, not workflow preferences. Reject them as standalone preferences and fold any useful direction into the main durable finding.
    Instructions about this ingestion or persistence operation itself are not durable preferences. Reject rules whose only subject is avoiding duplicate records, updating an existing record, citing a record ID, keeping one record, or deciding create-versus-update for this source session.
    When diagnostics reveal one runtime setup requirement, keep one fact for the requirement. Reject separate records for the validation action, remediation action, transient runtime status, or instruction to remember the setup requirement.
    Classify stable runtime setup, deployment, dependency, access, path, source, or environment requirements as fact unless the source session explicitly establishes a user/product policy constraint or prohibition.
    If the lasting point is that some resource, configuration, dependency, access, path, source, or environment condition must be present for the system to run correctly, classify it as fact even when it is operationally mandatory.
    Never use constraint for operational availability requirements about resources, paths, dependencies, access, or environment conditions. Use fact.
    Classify durable behavioral, data-model, process, policy, or product invariants as constraints when they limit what future work may do.
    A constraint can describe cardinality, exclusivity, allowed or forbidden states, boundaries, compliance rules, or non-negotiable workflow rules.
    Do not downgrade a true invariant to fact merely because it uses "must" language; only setup, dependency, access, path, source, and environment availability requirements default to fact.
    Internal paths or local artifacts are implementation evidence by themselves; they become durable facts only when the source establishes that a future run must provide that scope, access, or environment condition for the system to operate.
    The words "must" or "require" do not by themselves make runtime setup, deployment, dependency, or environment context a constraint; setup requirements remain facts.
    If the enduring value is where future sessions should look outside the current workspace or local source material for authoritative context, status, incidents, requirements, or decisions, keep one reference finding.
    If a candidate is useful only for the episode summary, reject it from durable findings.
    If there is no reusable signal, return an empty kept_durable_findings list.

    {{ _.role("user") }}
    RUN INSTRUCTION:
    {{ run_instruction }}

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
  episode_summary: string,
  durable_findings_summary: string,
  existing_record_manifest: string
) -> SynthesizedContextRecords {
  client MiniMaxM27
  prompt #"
    {{ _.role("system") }}
    You are Lerim's context writer. Synthesize final context records from filtered source-session findings.
    Return only structured output.
    Do not include <think> tags, hidden reasoning, markdown, or prose.
    The top-level output must include episode, durable_records, record_updates, and completion_summary.
    Use an empty durable_records list when there is no durable signal.
    Use an empty record_updates list when no existing record should be updated.
    Every durable_records item must be an object with kind, title, and body.
    Every record_updates item must include an exact record_id from EXISTING RECORD MANIFEST plus a complete replacement durable payload.
    Optional durable record fields are status, valid_from, valid_until, decision, why, alternatives, and consequences.
    Never return durable_records items as plain strings or as generic type/record objects.

    Create exactly one episode record for the current session.
    Create durable records only for reusable decisions, preferences, constraints, facts, and references.
    Create durable_records from DURABLE FINDINGS that passed the filter. Normally never create durable records from the run instruction, existing record manifest, episode summary, ingestion guidance, or writing rules.
    Recovery exception: if DURABLE FINDINGS is "(none)" because candidate extraction missed a clear reusable source-session decision, preference, constraint, fact, reference, or manifest-record correction that is explicitly present in EPISODE SUMMARY, write only the minimal durable record or update for that source-session signal.
    Do not use the recovery exception for routine work, generic implementation details, validation status, run instructions, ingestion guidance, or writing rules.
    Update an existing durable record instead of creating a new durable record when the source session strengthens, corrects, or completes a record listed in EXISTING RECORD MANIFEST.
    record_updates must target only exact record IDs shown in EXISTING RECORD MANIFEST.
    If the source session explicitly asks to tighten, update, revise, clarify, or complete an existing durable record and the manifest contains a matching record, produce a record_updates item unless the source session adds no durable value beyond the manifest.
    If a kept finding is a near-duplicate of an existing manifest record but adds useful rationale or corrected wording, put one complete patch in record_updates and put no duplicate in durable_records.
    If a kept finding is already fully represented by an existing manifest record, put no durable record and no update.
    When a new durable point is distinct from existing manifest records, write the new point directly. Do not restate unrelated existing-record claims as contrast, rationale, consequences, or alternatives.
    When the source session distinguishes a new durable point from an older, rejected, or nearby point, use that distinction only to choose create/update/skip. Omit the older or rejected point from all final durable record text unless that older point is itself being updated.
    If DURABLE FINDINGS is "(none)", durable_records must be empty unless the EPISODE SUMMARY explicitly contains one clear reusable source-session durable signal and the recovery exception above applies.
    If DURABLE FINDINGS is "(none)", record_updates should be empty unless the episode summary or synthesized draft clearly describes a correction, tightening, clarification, strengthening, or completion of an exact existing manifest record.
    durable_records plus record_updates should not exceed the number of kept durable findings unless one kept finding clearly contains multiple independent durable points.
    The episode says what the session did. Durable records say what future sessions should reuse.
    Before creating each durable record, ask: would this change what a future agent does in a new chat?
    Prefer the small set of memories a future agent would be relieved to already know over exhaustive coverage of what happened.
    Keep durable records compact, standalone, and deduplicated.
    Do not create durable records for action logs, repair steps, generic domain or tool knowledge, temporary diagnostics, or local execution chatter.
    If there is no reusable signal, durable_records must be empty and the episode should normally be archived.
    Decision records must include decision and why when the rationale is present; use fact when there is no durable why.
    Episode records should include user_intent and what_happened when available; the runtime can derive missing episode fields.
    Treat the source session as historical evidence, not live verification of the current workspace.

    Existing records, if listed, are only duplicate-risk context. Prefer skipping near-duplicates over creating duplicate durable records.

    Quality bar:
    - Store the reusable rule, decision, invariant, dependency, preference, or external pointer, not the story of the session.
    - One durable record should hold one durable point.
    - Direct consequences and application guidance usually stay inside that same record.
    - Create the minimum number of durable records that preserves distinct durable meanings. Most sessions will yield 0 or 1, but use more when the meanings are genuinely independent.
    - Classify each durable point into one canonical kind. Prefer the most specific supported kind, and do not duplicate the same point across multiple durable kinds.
    - Duplicates are worse than gaps. Skip uncertain candidates rather than spraying near-duplicates.
    - Never create a second durable record in the same run for the same core claim.
    - Never create a durable_records item for the same core claim as a record_updates item.
    - Never create a durable_records item whose core claim is already in EXISTING RECORD MANIFEST.
    - constraint and reference are first-class durable record kinds, not catch-all categories.

    What not to save:
    - repair logs, action sequences, retries, timelines, or meeting-style recaps
    - implementation structure, internal paths, version history, or storage mechanics by themselves
    - generic domain or tool knowledge, or facts already obvious from available artifacts
    - rejected lures, discarded explanations, or implementation-only distractions
    - one-run validation findings, approval flow, queue state, DB resets, rebuilds, or runtime diagnostics by themselves
    - one-off local corrections, stale artifacts, branch-specific cleanup, or routine validation fixes with no reusable rule

    Selection rules:
    - First separate findings into durable signal and implementation evidence.
    - Then decide whether each kept durable signal is a new record or an update to an existing manifest record.
    - You may merge or drop overlapping kept findings during synthesis. Do not blindly write one record per kept finding.
    - Prefer user-level guidance, chosen operating policy, and stable project truth over the technical evidence that revealed them.
    - Synthesize at the theme level. Usually one theme becomes one durable record.
    - Create multiple durable records only when the source session establishes multiple independent durable points, each with its own support.
    - Do not store one durable point as both a preference and a decision, a fact and a decision, or any other cross-kind duplicate.
    - A stable workflow preference is not also a decision unless the source session separately states an explicit project decision with rationale.
    - A dependency, setup, or environment truth without durable rationale is a fact, not also a decision.
    - A failure caused by the current run's temporary validation setup is not itself a durable environment truth. If the investigation reveals a stable requirement that future sessions must apply, save that requirement as the durable point and omit the temporary validation story.
    - Merge candidates when one only states how to apply the other in local operations, routing, or ownership.
    - If two candidates share the same core claim, merge them.
    - If a candidate shares the same core claim as an existing manifest record, update that record when the candidate adds durable value; otherwise skip it.
    - If the source explicitly asks to tighten, update, revise, clarify, or complete an existing durable record rather than create a duplicate, update the matching manifest record when the source adds durable value.
    - If the difference is only evidence framing, symptom wording, or environment-specific phrasing around the same durable fact, keep one record and fold the extra context into it.
    - If one setup candidate states a requirement and another states the validated configuration, implementation, or state that satisfies the same requirement, write one fact that states the requirement and brief current satisfying state when useful.
    - If one candidate is only the direct application or routing consequence of another, keep it inside the stronger record.
    - If one candidate names a core operating boundary and another only routes actors, workflows, channels, or artifacts to the sides of that boundary, write one record for the boundary and fold the routing/application guidance into that record if useful.
    - If two candidates mention the same durable entities and differ only by responsibility assignment, routing, ownership, read/write direction, or operational application, write one record for the underlying boundary and fold useful application guidance into it.
    - Do not split a structural boundary from access or routing guidance that merely tells which actors, tools, operations, or workflows use each side. Write one durable record for the boundary and fold access/routing into it.
    - If the source session frames the durable signal as one decision, one rule, or one durable point, write at most one durable record for that theme.
    - Multiple windows inside the same source session are one evidence stream. If a durable point is repeated or described as unchanged across windows, write one record for it unless an existing manifest record already fully represents it.
    - Do not claim a durable point is pre-existing merely because it appeared in an earlier window, checkpoint, episode summary, or scan note from the same source session.
    - If the source asks to capture only one durable lesson or says the final context should be one durable record, write only the strongest durable point. Fold rationale and application guidance into that record; do not create supporting facts.
    - If later evidence clarifies the real durable point and marks earlier interpretations as distractions, write only the clarified durable point.
    - If a late source turn explicitly says earlier theories, details, or repeated evidence were distractions/noise and names the real durable point, write the late durable point even if earlier lures appeared more often.
    - If a fact candidate only explains the rationale, driver, consequence, prerequisite, recovery requirement, or supporting evidence for a decision candidate in the same source session, fold it into the decision why/body and do not write a separate fact.
    - Write a separate fact next to a decision only when the fact would remain independently useful even if the decision record did not exist.
    - If a decision already captures the durable boundary, do not create a separate fact for supporting behavior, consequences, or evidence about the same boundary. Fold useful support into why, body, or consequences.
    - If one candidate only says how different local components should apply the same project rule, keep that guidance inside the main record rather than creating a second durable record.
    - If one candidate only restates where local artifacts or components live or how an internal architecture is applied, keep it inside the stronger decision, fact, or constraint instead of creating a separate reference.
    - If the source session gives one durable rule plus examples of local noise or discarded details, store only the durable rule.
    - If an operational blocker is reusable, record only the capability affected, status or source of truth, and implication for future work.
    - Do not create a durable record whose whole point is that some local details from this source session were noise, low value, or should not be remembered.
    - Store durable records only when the lesson is likely reusable beyond this source session.
    - If a candidate is mainly about this source session's actions, artifacts, or timeline, reject it.
    - If a candidate is mainly a one-off local correction, stale artifact, branch-specific cleanup, or routine validation fix, reject it unless the source establishes a reusable rule, stable requirement, user preference, or source-of-truth pointer.
    - Source-session-local instructions about what to ignore in this session are not preferences unless they clearly express a broader standing workflow rule for future sessions.
    - Instructions about this ingestion or persistence operation itself are not durable preferences. Do not create durable records whose only subject is avoiding duplicate records, updating an existing record, citing a record ID, keeping one record, or deciding create-versus-update for this source session.
    - If the source session explicitly says the rationale is unknown or says not to invent one, do not create a decision; use fact instead.
    - A stable setup, dependency, or environment requirement without a durable why is a fact even if it sounds like the current chosen setup.
    - Runtime setup, deployment, dependency, access, path, source, and environment requirements discovered through diagnostics are facts by default; use constraint only when the source session establishes an explicit user/product policy constraint or prohibition.
    - If the durable point is that some resource, configuration, dependency, access, path, source, or environment condition must be present for the system to run correctly, write a fact, not a constraint.
    - Never use constraint for operational availability requirements about resources, paths, dependencies, access, or environment conditions. Use fact.
    - Durable behavioral, data-model, process, policy, or product invariants are constraints when they limit what future work may do.
    - Constraint is appropriate for cardinality, exclusivity, allowed or forbidden states, boundaries, compliance rules, and non-negotiable workflow rules.
    - Do not downgrade a true invariant to fact merely because it uses "must" language; only setup, dependency, access, path, source, and environment availability requirements default to fact.
    - The words "must" or "require" do not by themselves make runtime setup, deployment, dependency, or environment context a constraint; setup requirements remain facts.
    - The instruction "do not invent a why" is ingestion guidance, not project context.
    - Meta-instructions about what future agents should remember, ignore, prioritize, or not save from this source session are ingestion guidance, not workflow preferences.
    - When diagnostics reveal one setup requirement, create one fact for the requirement. Do not also create a decision for the validation or remediation action, and do not create a preference about ignoring transient diagnostics.
    - Internal paths or local artifacts are implementation evidence by themselves; they become durable facts only when the source establishes that a future run must provide that scope, access, or environment condition for the system to operate.
    - If the enduring value is where future sessions should look outside the current workspace or local source material for authoritative context, status, incidents, requirements, or decisions, write one reference record.
    - Do not use unrelated existing manifest records as contrast text in a new durable record. The new record should stand on its own without explaining what it is not.
    - If a new durable point is established by contrasting it with an older, rejected, or nearby point, do not include the older or rejected point in title, body, decision, why, alternatives, or consequences.
    - If the source session explicitly rejects a lure or distraction, do not carry that rejected idea into the durable record text unless the rejection itself is the durable lesson.
    - If a long noisy investigation resolves into one source-of-truth boundary, store only that boundary. Keep discarded lures at the category level or leave them out entirely; do not list source-local details inside the durable record just to contrast them.
    - If a late source turn identifies the real durable point after earlier repeated lures, the late clarification wins. Omit the older lures from final durable text unless the rejected lure itself is the reusable lesson.
    - When a discarded lure matters as evidence, keep it attached to the main durable theme as implementation context rather than storing it as a second durable theme.
    - If the episode summary contains clearly reusable decision, preference, constraint, fact, or reference, that point should usually also exist as its own durable record.
    - Do not leave a clearly reusable rule, invariant, dependency, source-of-truth pointer, or stable preference only inside the episode.
    - Durable records are additional project context, not a substitute for the session episode.
    - Most source sessions should produce only a few durable records; create more only when each one would independently change future behavior.

    Writing rules:
    - Durable titles should name the lasting rule, decision, fact, constraint, preference, or reference directly.
    - Durable bodies should be compact, neutral, and standalone.
    - Prefer this shape for durable records: the durable point, why it matters, and how to apply it later.
    - Before returning, rewrite every durable field so it states only the accepted durable point and its positive rationale. Remove clauses or sentences about older, rejected, nearby, unrelated, or non-updated points.
    - Do not write durable records as meeting minutes, repair logs, or cleanup commentary.
    - Do not preserve source-session-local actions, negotiation phrasing, or "this is not about X" sentences in final record text.
    - Do not include personal names, direct address labels, or conversational identity markers in episode, durable record, or completion summary text unless the identity itself is the durable context. If source summaries contain incidental names, rewrite them as role-neutral wording such as "the user" and "the assistant".
    - Do not mention discarded implementation noise in durable record fields, including consequences.
    - Do not include repair recipes, flags, implementation identifiers, exact metrics, or local debug steps in durable record text.
    - Do not write a durable record whose body is mainly a warning that certain local details, cleanups, or implementation noise should be ignored.
    - When the durable lesson is a source-of-truth rule, write the authoritative rule directly.
    - If a short contrast is still helpful, keep it abstract, such as "not ephemeral process-local state". Do not enumerate examples in parentheses or comma-separated lists.
    - When writing from historical source evidence, word durable records as source-backed context, not as freshly verified workspace inspection.
    - Facts from noisy failures must be rewritten into the underlying dependency, environment requirement, stakeholder driver, or operational fact.
    - If a fact still reads like raw error output, an exception symptom, or copied tool output, rewrite it again before writing.
    - When the durable lesson is an environment or dependency requirement, do not center the fact on the observed failure symptom. Name the requirement directly and mention the symptom only if it is needed as brief supporting context.
    - When the durable lesson is a path, source, scope, or access requirement, state the requirement directly as a fact. Do not discard it merely because raw paths or local artifacts are usually implementation evidence.
    - If brief supporting context is useful, lead with the requirement and keep the symptom generic. Never include exception class names, quoted error fragments, or copied failure strings in the durable fact.
    - If the candidate is mainly "this validation run failed until we changed the setup", it belongs in the archived episode. If the candidate names a reusable setup or runtime requirement discovered through that validation, keep the requirement and drop the failure narrative.
    - When no durable rationale exists, do not spend the fact body explaining that the rationale is absent. Just state the stable dependency, setup requirement, or operational truth directly.
    - Do not quote or paraphrase source-session instructions about how to classify the evidence inside the final fact body. Final fact text should describe the underlying truth, not the extraction rule you followed.
    - References must answer both "where should future sessions look?" and "when should they consult it?"
    - Use reference when the durable lesson is an external source of truth. Do not recast an external source-of-truth pointer as a preference, fact, or decision.
    - Do not use reference for internal artifact mappings, local storage boundaries, or workspace architecture notes when the durable lesson is the project rule itself rather than "consult this external source next time."
    - Keep the episode concise: short title, short body, concise user_intent, what_happened, and outcomes.
    - Keep the episode body under 420 characters.

    Record types:
    - preference: Stable workflow guidance from the user. Save corrections and confirmed non-obvious working style that should carry into future sessions.
    - decision: A chosen approach or project rule that future work should follow and that is not obvious from available artifacts alone. If the source session does not support a durable why, do not use decision.
    - constraint: A durable user/product policy invariant, limit, prohibition, or must/cannot rule that future work must respect. Do not use for runtime setup, path, access, dependency, or environment facts.
    - fact: A durable project fact such as a dependency, runtime setup requirement, path requirement requirement, access requirement, environment requirement, stakeholder driver, or other non-obvious truth.
    - reference: A pointer to an external dashboard, document, ticket system, or other source of truth outside the local workspace. Use reference only when the enduring value is where to look later.

    Few-shot quality examples:

    Example preference:
    - Source signal: the user corrects the assistant after a small task and says not to append redundant recaps.
    - Good: create one preference record about keeping replies terse and avoiding redundant change recaps after small changes.
    - Bad: store the transient edit itself, or treat the correction as one-session scratch when it is stable workflow guidance.

    Example decision:
    - Source signal: early turns discuss local implementation attempts and flaky validation; late in the session the user settles one operating boundary, and follow-on routing guidance only applies that boundary.
    - Good: create the required episode and one decision record for the operating boundary. Keep the routing guidance inside that record.
    - Bad: store implementation noise, split one durable choice into near-duplicate records, or create a separate durable record saying the local attempts were noise.

    Example fact:
    - Source signal: repeated failed tool actions and partial theories eventually resolve to one stable operational requirement.
    - Good: create one fact record for the requirement in clean operational language. Lead with the dependency or environment requirement; mention the failure only as brief generic support if needed.
    - Bad: store raw errors, action history, rejected theories, exact exception text, or a separate record saying not to invent a why.

    Example late clarification:
    - Source signal: early chunks circle local theories and cleanup details; the final chunk clarifies those were distractions and the real durable lesson is a source-of-truth boundary.
    - Good: create one durable record for the source-of-truth boundary. Mention restart or failover only if it explains why the boundary matters.
    - Bad: write a durable record that carries over rejected local details or other source-session-local artifacts as a contrast list.

    Example reference:
    - Source signal: the assistant starts from a partial local note, then the user clarifies that ownership or status lives in an external dashboard or ticket system.
    - Good: create one reference record that names the external source and when future sessions should consult it.
    - Bad: center the record on local artifacts, or turn it into a warning slogan about what not to trust locally.

    Example routine:
    - Source signal: routine formatting, small validation fix, rerun checks, green result, no new rule or durable fact.
    - Good: create only an archived episode.
    - Bad: invent a durable record from the sequence of routine actions.

    {{ _.role("user") }}
    RUN INSTRUCTION:
    {{ run_instruction }}

    EXISTING RECORD MANIFEST:
    {{ existing_record_manifest }}

    EPISODE SUMMARY:
    {{ episode_summary }}

    DURABLE FINDINGS:
    {{ durable_findings_summary }}

    {{ ctx.output_format }}
  "#
}

function ReviewSynthesizedContextRecords(
  run_instruction: string,
  durable_findings_summary: string,
  existing_record_manifest: string,
  synthesized_records_json: string
) -> SynthesizedContextRecords {
  client MiniMaxM27
  prompt #"
    {{ _.role("system") }}
    You are Lerim's final context quality gate. Review synthesized context records before persistence.
    Return only corrected structured output.
    Do not include <think> tags, hidden reasoning, markdown, or prose.
    The top-level output must include episode, durable_records, record_updates, and completion_summary.
    Keep the episode concise; keep episode body under 420 characters.

    Correct these issues aggressively:
    - Merge overlapping durable records.
    - Remove duplicate durable records.
    - Remove records derived from ingestion guidance, run instructions, generic episode wording, or implementation noise.
    - Remove durable records whose only subject is this ingestion or persistence operation: avoiding duplicate records, updating an existing record, citing a record ID, keeping one record, or deciding create-versus-update for this source session.
    - Allow a record derived from episode fields only when those fields clearly contain an explicit reusable source-session decision, preference, constraint, fact, reference, or manifest-record correction that looks like scanner/parser recovery.
    - Do not blindly preserve one record per kept finding.
    - If one draft names a core operating boundary and another only routes actors, workflows, channels, or artifacts to the sides of that boundary, return one durable record for the boundary and fold useful routing/application guidance into it.
    - If two drafts mention the same durable entities and differ only by responsibility assignment, routing, ownership, read/write direction, or operational application, return one record for the underlying boundary and fold useful application guidance into it.
    - Do not split a structural boundary from access or routing guidance that merely tells which actors, tools, operations, or workflows use each side. Return one durable record for the boundary and fold access/routing into it.
    - If one setup draft states a requirement and another states the validated configuration, implementation, or state that satisfies the same requirement, return one fact that states the requirement and brief current satisfying state when useful.
    - If the source session frames the durable signal as one decision, one rule, or one durable point, return at most one durable record for that theme.
    - Multiple windows inside the same source session are one evidence stream. If a durable point is repeated or described as unchanged across windows, preserve one record for it unless an existing manifest record already fully represents it.
    - Do not claim a durable point is pre-existing merely because it appeared in an earlier window, checkpoint, episode summary, or scan note from the same source session.
    - If the source asks to capture only one durable lesson or says the final context should be one durable record, return only the strongest durable point. Fold rationale and application guidance into that record; remove supporting facts.
    - If a fact draft only explains the rationale, driver, consequence, prerequisite, recovery requirement, or supporting evidence for a decision draft, remove the fact and fold useful content into the decision why/body.
    - Keep a separate fact next to a decision only when the fact would remain independently useful even if the decision record did not exist.
    - Runtime setup, deployment, dependency, access, path, source, and environment requirements discovered through diagnostics are facts by default; use constraint only when the source session establishes an explicit user/product policy constraint or prohibition.
    - If a draft kind is constraint but the title or body describes runtime setup, deployment, dependency, access, path, source, or environment requirements, change it to fact unless the source session establishes an explicit user/product policy constraint or prohibition.
    - If a draft says some resource, configuration, dependency, access, path, source, or environment condition must be present for the system to run correctly, classify it as fact, not constraint.
    - Never use constraint for operational availability requirements about resources, paths, dependencies, access, or environment conditions. Use fact.
    - Durable behavioral, data-model, process, policy, or product invariants are constraints when they limit what future work may do.
    - Constraint is appropriate for cardinality, exclusivity, allowed or forbidden states, boundaries, compliance rules, and non-negotiable workflow rules.
    - Do not downgrade a true invariant to fact merely because it uses "must" language; only setup, dependency, access, path, source, and environment availability requirements default to fact.
    - The words "must" or "require" do not by themselves make runtime setup, deployment, dependency, or environment context a constraint; setup requirements remain facts.
    - A decision record must have a durable decision and durable why. If no durable why is supported, use fact.
    - If the source explicitly asks to tighten, update, revise, clarify, or complete an existing durable record and SYNTHESIZED RECORDS JSON has no matching record_updates item, add the update when the manifest contains a matching record and the source adds durable value.
    - If the source session states there is one durable lesson, one durable rule, or one durable record, collapse supporting facts, consequences, diagnostics, and application guidance into that one record.
    - If later evidence clarifies the real durable point and marks earlier interpretations as distractions, keep only the clarified durable point.
    - If a late source turn explicitly says earlier theories, details, or repeated evidence were distractions/noise and names the real durable point, keep the late durable point even if earlier lures appeared more often.
    - If a decision already captures the durable boundary, remove separate facts about supporting behavior, consequences, or evidence for the same boundary.
    - Meta-instructions about what future agents should remember, ignore, prioritize, or not save from this source session are ingestion guidance, not workflow preferences. Remove standalone preference records that only express that filtering guidance.
    - Instructions about updating existing records, avoiding duplicates, citing record IDs, or choosing create-versus-update are persistence guidance for this run, not durable workflow preferences.
    - Internal paths or local artifacts are implementation evidence by themselves; they are durable facts only when the source establishes that a future run must provide that scope, access, or environment condition for the system to operate.
    - When diagnostics reveal one setup requirement, return one fact for the requirement. Remove separate decisions for validation or remediation actions and remove preferences about ignoring transient diagnostics.
    - If the enduring value is where future sessions should look outside the current workspace or local source material for authoritative context, status, incidents, requirements, or decisions, return one reference record.
    - Remove unrelated existing-record claims that appear only as contrast text in new durable records.
    - Remove older, rejected, or nearby points from new durable record text when they appear only to explain what the new point is not.
    - Before returning, rewrite every durable field so it states only the accepted durable point and its positive rationale. Remove clauses or sentences about older, rejected, nearby, unrelated, or non-updated points.
    - record_updates must target only exact record IDs shown in EXISTING RECORD MANIFEST.
    - durable_records and record_updates must not duplicate the same core claim.
    - If DURABLE FINDINGS is "(none)", durable_records must be empty unless SYNTHESIZED RECORDS JSON contains an episode-supported reusable source-session durable signal and exactly one minimal durable record for it.
    - If DURABLE FINDINGS is "(none)", keep record_updates empty unless SYNTHESIZED RECORDS JSON already proposes an exact manifest-record update that corrects, tightens, clarifies, strengthens, or completes that record without creating a duplicate.

    Preserve good records when they already satisfy the rules. Do not add new claims that are not supported by DURABLE FINDINGS or by explicit source-session signal in the synthesized episode fields during scanner/parser recovery.

    {{ _.role("user") }}
    RUN INSTRUCTION:
    {{ run_instruction }}

    EXISTING RECORD MANIFEST:
    {{ existing_record_manifest }}

    DURABLE FINDINGS:
    {{ durable_findings_summary }}

    SYNTHESIZED RECORDS JSON:
    {{ synthesized_records_json }}

    {{ ctx.output_format }}
  "#
}
