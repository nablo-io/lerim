enum RecordKind {
  DECISION @alias("decision") @description("A durable project decision with decision and why fields.")
  PREFERENCE @alias("preference") @description("A stable user or workflow preference.")
  CONSTRAINT @alias("constraint") @description("A durable invariant, limit, or must/cannot rule.")
  FACT @alias("fact") @description("A durable project fact or setup truth.")
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
  IMPLEMENTATION @alias("implementation") @description("Trace-local implementation evidence or discarded hypothesis.")
}

class TraceWindowFinding {
  theme string @description("Short stable theme for this finding.")
  level FindingLevel @description("Use durable levels for reusable context, implementation for local/noisy evidence.")
  line int? @description("1-based supporting line when the window gives one.")
  quote string? @description("Short supporting quote from the current window.")
  note string @description("Compact semantic finding. Avoid command logs and copied errors.")
}

class TraceWindowScan {
  episode_update string? @description("Compact update for the final episode summary. May be omitted when this window adds nothing.")
  durable_findings TraceWindowFinding[] @description("Reusable decisions, preferences, constraints, facts, and references found in this window.")
  implementation_findings TraceWindowFinding[] @description("Implementation evidence, discarded hypotheses, and local details useful only as support/noise.")
  discarded_noise string[] @description("Short descriptions of noisy categories intentionally not saved.")
}

class EpisodeDraft {
  title string? @description("Optional short title for the current-session episode. Runtime derives one if omitted.")
  body string? @description("Compact episode body. If omitted, runtime builds it from user_intent and what_happened.")
  status RecordStatus? @description("Use archived for routine/no-durable sessions; active only when the episode itself remains useful.")
  user_intent string? @description("What the user wanted in this source session. Runtime fills a generic fallback if omitted.")
  what_happened string? @description("What the session actually did. Runtime fills a generic fallback if omitted.")
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

class SynthesizedExtraction {
  episode EpisodeDraft @description("Exactly one current-session episode record draft.")
  durable_records DurableRecordDraft[] @description("Zero or more durable records.")
  completion_summary string? @description("Brief summary of extraction work for final_result/reporting.")
}

function ScanTraceWindow(
  run_instruction: string,
  prior_episode_summary: string,
  prior_findings_summary: string,
  trace_window: string
) -> TraceWindowScan {
  client MiniMaxM27
  prompt #"
    {{ _.role("system") }}
    You scan one window from a coding-agent trace for Lerim extraction.
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
    - implementation_findings: local evidence, command work, discarded hypotheses, or support.
    - discarded_noise: categories of content intentionally ignored.

    Durable signal means a decision, preference, constraint, fact, or reference likely useful beyond this trace.
    Implementation detail alone is not durable signal.
    A durable finding should help a future agent make a better decision in a new chat.
    Save what a future agent would be relieved to already know: stable user preferences, chosen policies, durable constraints, source-of-truth references, and stable project or product truths.
    If a detail only helps replay or debug this trace's terminal session, put it in implementation_findings or discarded_noise, not durable_findings.
    Raw metrics, comparison tables, config edits, API wiring, package installation, debug commands, file paths, and stack traces are implementation_findings unless the user turns them into a reusable policy, preference, constraint, fact, or source-of-truth reference.
    When the same topic has both user-level guidance and technical evidence, the user-level guidance is the durable finding; the technical evidence is implementation support.
    For technical blockers, keep durable_findings at the capability, status, and implication level only. Put patch recipes, flags, functions, commands, exact metrics, and local debug steps in implementation_findings.
    When a window mixes a durable point with local evidence, put only the reusable point in durable_findings and put the supporting local evidence in implementation_findings.
    Use semantic meaning, not exact wording. Do not copy long code, logs, stack traces, or command transcripts.
    If the window has no durable signal, return an empty durable_findings list.
    If the window adds nothing meaningful to the episode, episode_update may be an empty string.

    {{ _.role("user") }}
    RUN INSTRUCTION:
    {{ run_instruction }}

    PRIOR EPISODE SUMMARY:
    {{ prior_episode_summary }}

    PRIOR FINDINGS SUMMARY:
    {{ prior_findings_summary }}

    TRACE WINDOW:
    {{ trace_window }}

    {{ ctx.output_format }}
  "#
}

function SynthesizeExtractRecords(
  run_instruction: string,
  episode_summary: string,
  durable_findings_summary: string,
  existing_record_manifest: string
) -> SynthesizedExtraction {
  client MiniMaxM27
  prompt #"
    {{ _.role("system") }}
    You synthesize final Lerim context records from scanned trace findings.
    Return only structured output.
    Do not include <think> tags, hidden reasoning, markdown, or prose.
    The top-level output must include episode, durable_records, and completion_summary.
    Use an empty durable_records list when there is no durable signal.
    Every durable_records item must be an object with kind, title, and body.
    Optional durable record fields are status, valid_from, valid_until, decision, why, alternatives, and consequences.
    Never return durable_records items as plain strings or as generic type/record objects.

    Create exactly one episode record for the current session.
    Create durable records only for reusable decisions, preferences, constraints, facts, and references.
    The episode says what the session did. Durable records say what future sessions should reuse.
    Before creating each durable record, ask: would this change what a future agent does in a new chat?
    Prefer the small set of memories a future agent would be relieved to already know over exhaustive coverage of what happened.
    Keep durable records compact, standalone, and deduplicated.
    Do not create durable records for command logs, patch steps, generic programming knowledge, temporary diagnostics, or local implementation chatter.
    If there is no reusable signal, durable_records must be empty and the episode should normally be archived.
    Decision records must include decision and why when the rationale is present; use fact when there is no durable why.
    Episode records should include user_intent and what_happened when available; the runtime can derive missing episode fields.
    Treat the trace as historical evidence, not live verification of the current repo.

    Existing records, if listed, are only duplicate-risk context. Prefer skipping near-duplicates over creating duplicate durable records.

    Quality bar:
    - Store the reusable rule, decision, invariant, dependency, preference, or external pointer, not the story of the session.
    - One durable record should hold one durable point.
    - Direct consequences and application guidance usually stay inside that same record.
    - Create the minimum number of durable records that preserves distinct durable meanings. Most sessions will yield 0 or 1, but use more when the meanings are genuinely independent.
    - Classify each durable point into one canonical kind. Prefer the most specific supported kind, and do not duplicate the same point across multiple durable kinds.
    - Duplicates are worse than gaps. Skip uncertain candidates rather than spraying near-duplicates.
    - Never create a second durable record in the same run for the same core claim.
    - constraint and reference are first-class durable record kinds, not fallback categories.

    What not to save:
    - patch logs, command sequences, retries, timelines, or meeting-style recaps
    - code structure, file paths, git history, or storage mechanics by themselves
    - generic programming knowledge or facts already obvious from the repo
    - rejected lures, discarded explanations, or implementation-only distractions
    - one-run validation findings, approval flow, queue state, DB resets, rebuilds, or runtime diagnostics by themselves

    Selection rules:
    - First separate findings into durable signal and implementation evidence.
    - Prefer user-level guidance, chosen operating policy, and stable project truth over the technical evidence that revealed them.
    - Synthesize at the theme level. Usually one theme becomes one durable record.
    - Create multiple durable records only when the trace establishes multiple independent durable points, each with its own support.
    - Do not store one durable point as both a preference and a decision, a fact and a decision, or any other cross-kind duplicate.
    - A stable workflow preference is not also a decision unless the trace separately states an explicit project decision with rationale.
    - A dependency, setup, or environment truth without durable rationale is a fact, not also a decision.
    - A failure caused by the current run's temporary validation setup is not itself a durable environment truth. If the investigation reveals a stable requirement that future sessions must apply, save that requirement as the durable point and omit the temporary validation story.
    - Merge candidates when one only states how to apply the other in local operations, routing, or ownership.
    - If two candidates share the same core claim, merge them.
    - If the difference is only evidence framing, symptom wording, or local-vs-CI phrasing around the same durable fact, keep one record and fold the extra context into it.
    - If one candidate is only the direct application or routing consequence of another, keep it inside the stronger record.
    - If one candidate only says how different local components should apply the same project rule, keep that guidance inside the main record rather than creating a second durable record.
    - If one candidate only restates where local project components live or how an internal architecture is applied, keep it inside the stronger decision, fact, or constraint instead of creating a separate reference.
    - If the trace gives one durable rule plus examples of local noise or discarded details, store only the durable rule.
    - If a technical blocker is reusable, record only the capability affected, status or source of truth, and implication for future work.
    - Do not create a durable record whose whole point is that some local details from this trace were noise, low value, or should not be remembered.
    - Store durable records only when the lesson is likely reusable beyond this trace.
    - If a candidate is mainly about this trace's commands, files, or timeline, reject it.
    - Trace-local instructions about what to ignore in this session are not preferences unless they clearly express a broader standing workflow rule for future sessions.
    - If the trace explicitly says the rationale is unknown or says not to invent one, do not create a decision; use fact instead.
    - A stable setup, dependency, or environment requirement without a durable why is a fact even if it sounds like the current chosen setup.
    - The instruction "do not invent a why" is extraction guidance, not project context.
    - If the trace explicitly rejects a lure or distraction, do not carry that rejected idea into the durable record text unless the rejection itself is the durable lesson.
    - If a long noisy investigation resolves into one source-of-truth boundary, store only that boundary. Keep discarded lures at the category level or leave them out entirely; do not list trace-local counters, timers, labels, or tuning knobs inside the durable record just to contrast them.
    - When a discarded lure matters as evidence, keep it attached to the main durable theme as implementation context rather than storing it as a second durable theme.
    - If the episode summary contains clearly reusable decision, preference, constraint, fact, or reference, that point should usually also exist as its own durable record.
    - Do not leave a clearly reusable rule, invariant, dependency, source-of-truth pointer, or stable preference only inside the episode.
    - Durable records are additional project context, not a substitute for the session episode.
    - Most traces should produce only a few durable records; create more only when each one would independently change future behavior.

    Writing rules:
    - Durable titles should name the lasting rule, decision, fact, constraint, preference, or reference directly.
    - Durable bodies should be compact, neutral, and standalone.
    - Prefer this shape for durable records: the durable point, why it matters, and how to apply it later.
    - Do not write durable records as meeting minutes, patch logs, or cleanup commentary.
    - Do not preserve trace-local commands, negotiation phrasing, or "this is not about X" sentences in final record text.
    - Do not mention discarded implementation noise in durable record fields, including consequences.
    - Do not include patch recipes, flags, function names, exact metrics, or local debug steps in durable record text.
    - Do not write a durable record whose body is mainly a warning that certain local details, cleanups, or implementation noise should be ignored.
    - When the durable lesson is a source-of-truth rule, write the authoritative rule directly.
    - If a short contrast is still helpful, keep it abstract, such as "not worker-local state" or "not ephemeral local state". Do not enumerate examples in parentheses or comma-separated lists.
    - When writing from a historical trace, word durable records as source-backed context, not as freshly verified code inspection.
    - Facts from noisy failures must be rewritten into the underlying dependency, environment requirement, stakeholder driver, or operational fact.
    - If a fact still reads like stderr, an exception symptom, or copied command output, rewrite it again before writing.
    - When the durable lesson is an environment or dependency requirement, do not center the fact on the observed failure symptom. Name the requirement directly and mention the symptom only if it is needed as brief supporting context.
    - If brief supporting context is useful, lead with the requirement and keep the symptom generic. Never include exception class names, quoted error fragments, or copied failure strings in the durable fact.
    - If the candidate is mainly "this validation run failed until we changed the setup", it belongs in the archived episode. If the candidate names a reusable setup or runtime requirement discovered through that validation, keep the requirement and drop the failure narrative.
    - When no durable rationale exists, do not spend the fact body explaining that the rationale is absent. Just state the stable dependency, setup requirement, or operational truth directly.
    - Do not quote or paraphrase trace instructions about how to classify the evidence inside the final fact body. Final fact text should describe the underlying truth, not the extraction rule you followed.
    - References must answer both "where should future sessions look?" and "when should they consult it?"
    - Do not use reference for internal file mappings, local storage boundaries, or repo architecture notes when the durable lesson is the project rule itself rather than "consult this external source next time."
    - Keep the episode concise: short title, short body, concise user_intent, what_happened, and outcomes.

    Record types:
    - preference: Stable workflow guidance from the user. Save corrections and confirmed non-obvious working style that should carry into future sessions.
    - decision: A chosen approach or project rule that future work should follow and that is not obvious from code alone. If the trace does not support a durable why, do not use decision.
    - constraint: A durable invariant, limit, or must/cannot rule that future work must respect.
    - fact: A durable project fact such as a dependency, environment requirement, stakeholder driver, or other non-obvious truth.
    - reference: A pointer to an external dashboard, document, ticket system, or other source of truth outside the repo. Use reference only when the enduring value is where to look later.

    Few-shot quality examples:

    Example preference:
    - Trace signal: the user corrects the assistant after a small code fix and says not to append redundant recaps.
    - Good: create one preference record about keeping replies terse and avoiding redundant change recaps after small diffs.
    - Bad: store the file edit itself, or treat the correction as one-session scratch when it is stable workflow guidance.

    Example decision:
    - Trace signal: early turns discuss local refactors and flaky tests; late in the trace the user settles one architecture boundary, and follow-on routing guidance only applies that boundary.
    - Good: create the required episode and one decision record for the architecture boundary. Keep the routing guidance inside that record.
    - Bad: store refactor noise, split one architectural choice into near-duplicate records, or create a separate durable record saying the refactors were noise.

    Example fact:
    - Trace signal: repeated failed commands and partial theories eventually resolve to one stable operational requirement.
    - Good: create one fact record for the requirement in clean operational language. Lead with the dependency or environment requirement; mention the failure only as brief generic support if needed.
    - Bad: store raw errors, command history, rejected theories, exact exception text, or a separate record saying not to invent a why.

    Example late clarification:
    - Trace signal: early chunks circle local counters, timers, labels, and tuning; the final chunk clarifies those were distractions and the real durable lesson is a source-of-truth boundary.
    - Good: create one durable record for the source-of-truth boundary. Mention restart or failover only if it explains why the boundary matters.
    - Bad: write a durable record that carries over rejected local counters, attempt counts, backoff knobs, or other trace-local artifacts as a contrast list.

    Example reference:
    - Trace signal: the assistant starts from a partial repo note, then the user clarifies that ownership or status lives in an external dashboard or ticket system.
    - Good: create one reference record that names the external source and when future sessions should consult it.
    - Bad: center the record on local files, or turn it into a warning slogan about what not to trust locally.

    Example routine:
    - Trace signal: formatter, small lint fix, rerun tests, green result, no new rule or durable fact.
    - Good: create only an archived episode.
    - Bad: invent a durable record from the sequence of routine commands.

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
