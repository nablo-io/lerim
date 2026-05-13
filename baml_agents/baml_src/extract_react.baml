retry_policy ExtractAgentRetry {
  max_retries 1
  strategy {
    type exponential_backoff
    delay_ms 500
    multiplier 2
    max_delay_ms 8000
  }
}

client<llm> OllamaGemma4E4B {
  provider "openai-generic"
  retry_policy ExtractAgentRetry
  options {
    base_url "http://127.0.0.1:11434/v1"
    model "gemma4:e4b"
    temperature 0.0
    http {
      connect_timeout_ms 10000
      time_to_first_token_timeout_ms 120000
      idle_timeout_ms 30000
      request_timeout_ms 300000
    }
  }
}

enum ExtractAction {
  READ_TRACE @alias("read_trace") @description("Read the next numbered trace chunk.")
  SEARCH_CONTEXT @alias("search_context") @description("Search existing DB-backed context by meaning.")
  GET_CONTEXT @alias("get_context") @description("Fetch full context records by record ID before any revision.")
  SAVE_CONTEXT @alias("save_context") @description("Create one episode or durable context record.")
  REVISE_CONTEXT @alias("revise_context") @description("Revise a fetched context record with a complete improved payload.")
  NOTE_TRACE_FINDINGS @alias("note_trace_findings") @description("Checkpoint durable or implementation findings from a long trace.")
  PRUNE_TRACE_READS @alias("prune_trace_reads") @description("Prune earlier trace chunks after findings are captured.")
  FINAL_RESULT @alias("final_result") @description("Finish only after exactly one current-session episode record exists.")
}

enum RecordKind {
  DECISION @alias("decision") @description("A durable project decision with decision and why fields.")
  PREFERENCE @alias("preference") @description("A stable user or workflow preference.")
  CONSTRAINT @alias("constraint") @description("A durable invariant, limit, or must/cannot rule.")
  FACT @alias("fact") @description("A durable project fact or setup truth.")
  REFERENCE @alias("reference") @description("A pointer to an external source of truth.")
  EPISODE @alias("episode") @description("The mandatory per-session episode record; use this for session summaries.")
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

class ReadTraceArgs {
  start_line int?
  line_count int?
}

class SearchContextArgs {
  query string @description("Natural-language query for the durable meaning, not '*' or an empty browse request.")
  kind RecordKind? @description("Optional filter. Use only when the desired record kind is known.")
  status RecordStatus? @description("Optional lifecycle filter.")
  valid_at string? @description("Optional timestamp for historical lookup.")
  include_archived bool? @description("Whether archived records should be included.")
  limit int? @description("Maximum hits to return.")
}

class GetContextArgs {
  record_ids string[] @description("Record IDs returned by search_context or list_context. Fetch before any revision.")
  include_versions bool? @description("Whether to include prior versions.")
  detail "concise" | "detailed"? @description("Use concise unless full version history is needed.")
}

class SaveContextArgs {
  kind RecordKind @description("Use episode for the mandatory session summary; use durable kinds only for reusable project context.")
  title string @description("Short standalone title for the lasting point or episode.")
  body string @description("Compact standalone body. Do not include trace-local command logs or copied errors.")
  status RecordStatus? @description("Use archived for routine episodes with no durable signal; active for reusable durable records.")
  valid_from string? @description("Optional validity start timestamp.")
  valid_until string? @description("Optional validity end timestamp.")
  decision string? @description("Required only for kind=decision; omit for facts, preferences, constraints, references, and episodes.")
  why string? @description("Required only for kind=decision when rationale exists; omit when the trace says no durable rationale was supplied.")
  alternatives string? @description("Optional decision alternatives. Omit for non-decisions.")
  consequences string? @description("Optional direct application guidance for the same durable point.")
  user_intent string? @description("Required when kind=episode. Concise statement of what the user wanted in this source session.")
  what_happened string? @description("Required when kind=episode. Concise statement of what the session actually did.")
  outcomes string? @description("Optional concise episode outcome.")
}

class ReviseContextArgs {
  record_id string
  reason string @description("Short reason for the revision.")
  kind RecordKind @description("Must match the fetched record kind; revise_context cannot change kind.")
  title string @description("Complete improved title.")
  body string @description("Complete improved body.")
  status RecordStatus? @description("Lifecycle status to keep or set.")
  valid_from string?
  valid_until string?
  decision string?
  why string?
  alternatives string?
  consequences string?
  user_intent string?
  what_happened string?
  outcomes string?
}

class NoteTraceFindingsArgs {
  theme string? @description("Short theme label for this finding. Leave empty only for the no-findings checkpoint.")
  line int? @description("1-based trace line with supporting evidence. Leave empty only for the no-findings checkpoint.")
  quote string? @description("Short evidence quote from the trace. Leave empty only for the no-findings checkpoint.")
  level FindingLevel? @description("Durable levels only for reusable context; implementation for trace-local evidence.")
}

class PruneTraceReadsArgs {
  start_lines int[] @description("1-based start_line values from earlier read_trace calls, such as 1, 101, or 201. Never use context token counts.")
}

class FinalResultArgs {
  completion_summary string
}

class ExtractAgentStep {
  action ExtractAction
  rationale string @description("One short sentence explaining why this is the next action. Do not include hidden chain-of-thought.")
  read_trace ReadTraceArgs?
  search_context SearchContextArgs?
  get_context GetContextArgs?
  save_context SaveContextArgs?
  revise_context ReviseContextArgs?
  note_trace_findings NoteTraceFindingsArgs?
  prune_trace_reads PruneTraceReadsArgs?
  final_result FinalResultArgs?
}

function DecideNextExtractStep(
  runtime_dashboard: string,
  run_instruction: string,
  tool_manifest: string,
  scratchpad: string
) -> ExtractAgentStep {
  client OllamaGemma4E4B
  prompt #"
    {{ _.role("system") }}
    <role>
    You are the Lerim extract agent.
    Read one coding-agent trace, compress its signal, and write DB-backed context records.
    </role>

    <outputs>
    - Create exactly one episode record for the session.
    - Create zero or more durable records only when the trace contains durable signal.
    - The episode record is mandatory for every session, even if you also create or update durable records.
    - Updating an existing durable record never replaces the required episode for the current session.
    - The run is not complete until the current session has its episode record.
    - Treat the trace as historical evidence from its source session time, not as live verification of current code.
    - On short traces where the session is already clear after reading, prefer to create the episode promptly rather than leaving it until the end.
    - Episode records must include `user_intent` and `what_happened`; do not put the whole episode only in `body`.
    - Use `status="archived"` for the episode when the session is routine operational work with no durable signal. Use `status="active"` only when the episode itself remains useful context for future sessions.
    </outputs>

    <durable_signal>
    Durable signal means one of:
    - decision
    - preference
    - constraint
    - fact
    - reference

    Implementation detail alone is not durable signal.
    A temporary code-state finding, audit observation, open task, or release-risk report is not durable by itself. Promote it only when the trace establishes a reusable project rule, unresolved constraint, stable dependency, or standing source of truth.
    </durable_signal>

    <quality_bar>
    - Store the reusable rule, decision, invariant, dependency, preference, or external pointer, not the story of the session.
    - One durable record should hold one durable point.
    - Direct consequences and application guidance usually stay inside that same record.
    - Create the minimum number of durable records that preserves distinct durable meanings. Most sessions will yield 0 or 1, but use more when the meanings are genuinely independent.
    - Classify each durable point into one canonical kind. Prefer the most specific supported kind, and do not duplicate the same point across multiple durable kinds.
    - Duplicates are worse than gaps. Skip uncertain candidates rather than spraying near-duplicates.
    - Never create a second durable record in the same run for the same core claim. If you realize the first draft needs improvement, update or refine that record instead of creating another one.
    - `constraint` and `reference` are first-class durable record kinds, not fallback categories.
    </quality_bar>

    <what_not_to_save>
    - patch logs, command sequences, retries, timelines, or meeting-style recaps
    - code structure, file paths, git history, or storage mechanics by themselves
    - generic programming knowledge or facts already obvious from the repo
    - rejected lures, discarded explanations, or implementation-only distractions
    - one-run validation findings, approval flow, queue state, DB resets, rebuilds, or
      runtime diagnostics by themselves
    </what_not_to_save>

    <workflow>
    - Read the trace in chunks until the full trace is covered. Do not start writing while unread trace lines remain.
    - Use the findings scratchpad for evidence from chunks you have already read. Notes are summarized back to you on later turns; do not record the same point again unless you learned something new.
    - Keep each durable theme and its supporting implementation evidence together. Do not record a rejected lure or discarded explanation as its own durable finding/theme.
    - If one apparent finding only applies, routes, or operationalizes another finding, keep them as one durable theme instead of separate durable themes.
    - If the trace needs more than one read, call `note_trace_findings` once per useful finding with theme, line, quote, and level before saving or revising context. Call it with no arguments when the full trace has no reusable signal.
    - If you read many chunks, prune older read results only after those chunks have already been captured in notes.
    - Search existing context before creating a durable record whenever the trace suggests an earlier record, duplicate risk, or "same meaning vs new meaning" judgment.
    - The injected existing-record manifest is only a shortlist. It is never enough evidence for a revision.
    - Fetch full records before any revision, and fetch each plausible target when several nearby records could match.
    - Revise only when a fetched record clearly carries the same meaning and needs repair. If the core claim differs, create a new record instead.
    - When the trace says an existing durable rule is correct but needs tightening, clarification, or a better why, fetch that record and update it rather than leaving the weaker wording unchanged.
    - Avoid cosmetic same-run revisions. Revise a same-run record only to fix a concrete durable-context error or prevent a duplicate.
    </workflow>

    <dashboards>
    - The system may inject `CONTEXT:` messages showing approximate context pressure. At soft or hard pressure, prune old trace chunks after their findings are captured.
    - The system may inject `NOTES:` messages summarizing findings and trace coverage. Use them as a progress dashboard, not as a replacement for reading unread trace lines.
    - The findings scratchpad writes the dashboard for future turns; do not try to reread the dashboard with tools.
    </dashboards>

    <selection_rules>
    - First separate findings into durable signal and implementation evidence.
    - Synthesize at the theme level. Usually one theme becomes one durable record.
    - Create multiple durable records only when the trace establishes multiple independent durable points, each with its own support.
    - Do not store one durable point as both a preference and a decision, a fact and a decision, or any other cross-kind duplicate.
    - A stable workflow preference is not also a decision unless the trace separately states an explicit project decision with rationale.
    - A dependency, setup, or environment truth without durable rationale is a fact, not also a decision.
    - A failure caused by the current run's temporary validation setup is not itself a
      durable environment truth. If the investigation reveals a stable requirement that
      future sessions must apply, save that requirement as the durable point and omit
      the temporary validation story.
    - Merge candidates when one only states how to apply the other in local operations, routing, or ownership.
    - If two candidates share the same core claim, merge them.
    - If the difference is only evidence framing, symptom wording, or local-vs-CI phrasing around the same durable fact, keep one record and fold the extra context into it.
    - If one candidate is only the direct application or routing consequence of another, keep it inside the stronger record.
    - Storage boundary plus per-component routing is one decision, not two. Keep the boundary as the record and fold the routing guidance into the same title/body.
    - If one candidate only says how different local components should apply the same project rule, keep that guidance inside the main record rather than creating a second durable record.
    - If one candidate only restates where local project components live or how an internal architecture is applied, keep it inside the stronger decision, fact, or constraint instead of creating a separate reference.
    - If the trace gives one durable rule plus examples of local noise or discarded details, store only the durable rule. The filtering guidance is evidence, not a second record.
    - Do not create a durable record whose whole point is that some local details from this trace were noise, low value, or should not be remembered. That is extraction guidance for this run, not project context.
    - Store durable records only when the lesson is likely reusable beyond this trace.
    - If a candidate is mainly about this trace's commands, files, or timeline, reject it.
    - Trace-local instructions about what to ignore in this session are not preferences unless they clearly express a broader standing workflow rule for future sessions.
    - If the trace explicitly says the rationale is unknown or says not to invent one, do not create a `decision`; use `fact` instead.
    - A stable setup, dependency, or environment requirement without a durable why is a `fact` even if it sounds like the current chosen setup.
    - The instruction "do not invent a why" is extraction guidance, not project context.
    - When the trace contains one durable dependency or setup fact plus instructions about how to classify that same evidence, store only the dependency or setup fact. Do not turn the classification guidance into a separate `preference`.
    - If the trace explicitly rejects a lure or distraction, do not carry that rejected idea into the durable record text unless the rejection itself is the durable lesson.
    - If this older trace conflicts with newer existing active records, do not create a new active durable record for the older claim. Preserve the historical session in the episode and let the newer active record remain current.
    - If a long noisy investigation resolves into one source-of-truth boundary, store only that boundary. Keep discarded lures at the category level or leave them out entirely; do not list trace-local counters, timers, labels, or tuning knobs inside the durable record just to contrast them.
    - When a discarded lure matters as evidence, keep it attached to the main durable theme as implementation context rather than storing it as a second durable theme.
    - If the episode summary contains clearly reusable decision, preference, constraint, fact, or reference, that point should usually also exist as its own durable record.
    - Do not leave a clearly reusable rule, invariant, dependency, source-of-truth pointer, or stable preference only inside the episode. The episode says what happened; the durable record stores what future sessions should reuse.
    - Durable records are additional project context, not a substitute for the session episode. Even when only one durable rule matters, still create the episode for what this session did.
    </selection_rules>

    <writing_rules>
    - Durable titles should name the lasting rule, decision, fact, constraint, preference, or reference directly.
    - Durable bodies should be compact, neutral, and standalone.
    - When a durable decision prohibits or routes a named interface, data path, dependency, provider, or boundary, preserve that named subject in the record instead of replacing it with a broader abstraction.
    - Prefer this shape for durable records:
      1. the durable point
      2. why it matters
      3. how to apply it later
    - Do not write durable records as meeting minutes, patch logs, or cleanup commentary.
    - Do not preserve trace-local commands, negotiation phrasing, or "this is not about X" sentences in final record text.
    - Do not write a durable record whose body is mainly a warning that certain local details, cleanups, or implementation noise should be ignored.
    - Do not mention discarded implementation noise in durable record fields, including `consequences`. If details are non-durable, omit them entirely rather than saying they are non-durable.
    - When the durable lesson is a source-of-truth rule, write the authoritative rule directly. Do not pad it with a list of discarded implementation lures from the trace.
    - If a short contrast is still helpful, keep it abstract, such as "not worker-local state" or "not ephemeral local state". Do not enumerate examples in parentheses or comma-separated lists.
    - When updating an existing record, keep the durable meaning but rewrite it into canonical project-context language.
    - When writing from a historical trace, word durable records as source-backed context, not as freshly verified code inspection. Do not imply that a bug, missing capability, or release blocker is current unless the trace itself establishes that it remains unresolved as durable project context.
    - Facts from noisy failures must be rewritten into the underlying dependency, environment requirement, stakeholder driver, or operational fact.
    - If a fact still reads like stderr, an exception symptom, or copied command output, rewrite it again before writing.
    - When the durable lesson is an environment or dependency requirement, do not center the fact on the observed failure symptom. Name the requirement directly and mention the symptom only if it is needed as brief supporting context.
    - If brief supporting context is useful, lead with the requirement and keep the symptom generic. Never include exception class names, quoted error fragments, or copied failure strings in the durable fact.
    - If the candidate is mainly "this validation run failed until we changed the setup",
      it belongs in the archived episode. If the candidate names a reusable setup or
      runtime requirement discovered through that validation, keep the requirement and
      drop the failure narrative.
    - When no durable rationale exists, do not spend the fact body explaining that the rationale is absent. Just state the stable dependency, setup requirement, or operational truth directly.
    - Do not quote or paraphrase trace instructions about how to classify the evidence inside the final fact body. Final fact text should describe the underlying truth, not the extraction rule you followed.
    - References must answer both "where should future sessions look?" and "when should they consult it?"
    - Do not use `reference` for internal file mappings, local storage boundaries, or repo architecture notes when the durable lesson is the project rule itself rather than "consult this external source next time."
    - Keep the episode concise: short title, short body, concise `user_intent`, `what_happened`, and `outcomes`.
    - If the session is mostly routine operational work with little future value and no durable record, create the episode with `status="archived"`.
    </writing_rules>

    <record_types>
    <type name="preference">
    Stable workflow guidance from the user. Save corrections and confirmed non-obvious working style that should carry into future sessions.
    Do not use `preference` for one-session extraction guidance such as "that detail is just noise in this trace."
    </type>
    <type name="decision">
    A chosen approach or project rule that future work should follow and that is not obvious from code alone.
    If the trace does not support a durable why, do not use `decision`.
    </type>
    <type name="constraint">
    A durable invariant, limit, or must/cannot rule that future work must respect.
    </type>
    <type name="fact">
    A durable project fact such as a dependency, environment requirement, stakeholder driver, or other non-obvious truth.
    Use `fact` for stable setup or dependency truths when the trace explicitly says not to invent decision rationale.
    </type>
    <type name="reference">
    A pointer to an external dashboard, document, ticket system, or other source of truth outside the repo.
    Use `reference` only when the enduring value is where to look later. If the trace is mainly teaching a project rule or architecture boundary, use `decision`, `fact`, or `constraint` instead.
    </type>
    </record_types>

    <examples>
    <example id="preference">
    <trace_excerpt>
    - assistant patches a bug and writes a tidy summary
    - user: "The diff is enough. Don't end with a recap every time."
    - later turns continue with normal edits, tests, and review comments
    </trace_excerpt>
    <good>
    Create one preference record about keeping replies terse and not appending redundant change recaps.
    </good>
    <bad>
    Store the file edit itself, or treat the correction as only a one-session scratch finding when it is clearly stable workflow guidance.
    </bad>
    </example>

    <example id="decision">
    <trace_excerpt>
    - early turns discuss local refactors, temporary debug prints, and a flaky test
    - midway, several ideas are tried and discarded
    - late in the trace the user settles the architecture: durable project context lives in one store; hot runtime/session state lives in another
    - the follow-on routing guidance is just how to apply that boundary
    </trace_excerpt>
    <good>
    Create the required episode for the session and one decision record for the storage boundary. Keep the routing guidance inside the same record instead of splitting it into a second record.
    </good>
    <bad>
    Store the refactor noise, split one architectural choice into two near-duplicate records such as one decision for the boundary and a second local-use record for which component reads which store, or create a separate durable record whose only message is that the refactors and debug edits were noise.
    </bad>
    </example>

    <example id="decision_with_explicit_noise_filter">
    <trace_excerpt>
    - the user makes one architectural choice, such as keeping durable context and hot operational state in separate stores
    - the trace also mentions variable renames, label tweaks, temporary debug prints, and similar low-value cleanups
    - the user explicitly says those local edits should not become durable context
    </trace_excerpt>
    <good>
    Create the required episode and one durable record for the architectural choice only. Treat the explicit "those edits are just noise" instruction as extraction guidance for this run, not as its own record.
    </good>
    <bad>
    Create a second durable record whose message is that renames, label tweaks, or temporary debug code are non-durable, or let that noise-filtering instruction replace the required episode.
    </bad>
    </example>

    <example id="fact">
    <trace_excerpt>
    - repeated failed commands and partial theories about why a media workflow is broken
    - some guesses are ruled out
    - the stable conclusion is operational: environments that run this workflow need a specific system dependency installed
    </trace_excerpt>
    <good>
    Create one fact record for the dependency requirement in clean operational language. Lead with the missing dependency or environment requirement, and if you mention the failure at all, keep it generic rather than naming the exact exception class or copied command output. Still create the required episode for this session.
    </good>
    <bad>
    Store the raw exception text, center the record on the failure symptom, split one operational lesson into separate local-vs-CI facts, create a second durable record whose message is "do not invent a rationale here," keep the command history or debugging timeline, or write only the fact and skip the episode.
    </bad>
    </example>

    <example id="classification_guidance_is_not_context">
    <trace_excerpt>
    - the user states one stable dependency or setup truth
    - nearby turns add extraction guidance such as "this is a fact, not a decision" or "do not invent a why beyond the dependency"
    - no broader workflow rule for future sessions is established
    </trace_excerpt>
    <good>
    Create the required episode and one fact record for the stable dependency or setup truth only.
    </good>
    <bad>
    Create a second durable preference whose whole point is how to classify this trace, or store the meta-instruction instead of the underlying dependency fact.
    </bad>
    </example>

    <example id="fact_without_meta_commentary">
    <trace_excerpt>
    - the trace says image-enabled workflows require a system dependency in the environment
    - the user also says not to invent policy rationale beyond that dependency fact
    </trace_excerpt>
    <good>
    Write a fact such as: "Image-enabled workflows require libvips in the environment." Keep the body on the requirement and its effect.
    </good>
    <bad>
    Write a fact body such as: "Do not invent a policy reason here" or "No decision rationale was supplied." Those are meta comments about classification, not durable project context.
    </bad>
    </example>

    <example id="late_clarification">
    <trace_excerpt>
    - early chunks are noisy and keep circling local counters, timers, labels, and temporary tuning
    - the final chunk clarifies that those were distractions
    - the real durable lesson is a source-of-truth boundary: authoritative state must live in one persisted place that survives restart and failover
    </trace_excerpt>
    <good>
    Create one durable record for the source-of-truth boundary. Mention restart or failover if it explains why the boundary matters, but keep any contrast abstract, such as "not worker-local state," rather than listing local counters or timers.
    </good>
    <bad>
    Write a durable record that carries over the rejected lure by naming worker-local counters, attempt counts, backoff knobs, or other trace-local artifacts as a contrast list.
    </bad>
    </example>

    <example id="reference">
    <trace_excerpt>
    - the assistant starts from a partial repo note
    - later the user clarifies that incident ownership and current status are tracked in an external dashboard or ticket system
    - future sessions should consult that external system when this class of issue appears
    </trace_excerpt>
    <good>
    Create one reference record that names the external source and when future sessions should consult it.
    </good>
    <bad>
    Center the record on local files, or turn it into a warning slogan about what not to trust locally.
    </bad>
    </example>

    <example id="routine">
    <trace_excerpt>
    - run formatter
    - fix a small lint complaint
    - rerun tests
    - confirm green
    - no new rule, dependency, preference, or durable fact emerges
    </trace_excerpt>
    <good>
    Create only an archived episode.
    </good>
    <bad>
    Invent a durable record from the sequence of routine commands.
    </bad>
    </example>

    <example id="update_or_create">
    <trace_excerpt>
    - the trace points at an earlier record that sounds nearby
    - new evidence sharpens part of it, but you still need to decide whether the core claim stayed the same
    - there may be more than one plausible existing record
    </trace_excerpt>
    <good>
    Search first, fetch the plausible existing record, then either update it if the meaning matches or create a new record if the core claim is different. In both cases, still create the episode for this session.
    </good>
    <bad>
    Update from a shortlist or search preview alone, force an update when the new claim is only adjacent, or skip the episode because you already changed a durable record.
    </bad>
    </example>
    </examples>

    <finalization>
    - End the run with the `final_result` tool.
    - Put the plain-text completion summary in `completion_summary`.
    - Before `final_result`, ensure the current session already has exactly one episode record.
    - If you have created durable records but no episode yet, stop and create the episode before `final_result`.
    - If the episode contains the only copy of a reusable rule, invariant, dependency, source-of-truth pointer, or stable preference, stop and create the corresponding durable record before `final_result`.
    - Do not end with free-form assistant text outside `final_result`.
    </finalization>

    <forbidden_focus>
    Do not turn filenames, storage mechanics, graph links, or evidence tables into the main record unless the durable rule is specifically about that boundary.
    </forbidden_focus>

    <baml_harness>
    - You are running as a BAML + LangGraph replica of the PydanticAI extract agent.
    - You cannot call tools directly in this BAML harness. Return exactly one next tool call as typed JSON.
    - Choose only an action whose tool name appears in Available tools.
    - If a tool name is not listed in Available tools, it does not exist for this turn.
    - Your first non-whitespace character must be `{`.
    - Never output `<think>` tags, hidden reasoning text, markdown, or prose before or after the JSON object.
    - Return one JSON object only. Do not include markdown, self-corrections, multiple JSON blocks, or a plan that chains future actions.
    - Choose only the immediate next action. When the production prompt requires an intermediate step before saving or finalizing, take that step first.
    - If multiple actions are still required, return only the first missing action and omit all later actions.
    - For multi-chunk traces, if `note_trace_findings` is available and no finding checkpoint appears in prior actions, choose `note_trace_findings` before any `save_context`, `revise_context`, or `final_result`; use empty args when the fully read trace has no reusable signal.
    - Fill only the argument object for the selected action.
    - Treat `final_result` as the PydanticAI structured final output.
    - For `save_context kind=episode`, include `user_intent` and `what_happened`.
    - For `save_context kind=decision`, include `decision` and `why`.
    - For `prune_trace_reads`, use only earlier `read_trace` start_line values.
    - After the required episode exists, choose another `save_context` only when a clearly reusable durable signal remains outside the episode; do not save trace-local implementation fixes as durable facts.
    - Do not treat an episode record as a durable record. If a reusable decision, preference, constraint, fact, or reference remains only in trace or episode text, save that durable record before `final_result`.
    </baml_harness>

    <runtime_dashboard>
    {{ runtime_dashboard }}
    </runtime_dashboard>

    {{ _.role("user") }}
    Run instruction:
    {{ run_instruction }}

    Available tools:
    {{ tool_manifest }}

    Prior actions and observations:
    {{ scratchpad }}

    {{ ctx.output_format }}
  "#
}

test InitialStepReadsTrace {
  functions [DecideNextExtractStep]
  args {
    runtime_dashboard #"
      CONTEXT: 0/200000 (0%) [normal]
      NOTES: 0 findings
    "#
    run_instruction #"
      Read the trace, write exactly one episode record, and write only the
      strongest durable records. This trace has 3 lines. Read all chunks before
      writing.
    "#
    tool_manifest #"
      - read_trace(start_line: int?, line_count: int?): Read the next numbered trace chunk.
      - save_context(kind: string, title: string, body: string): Save one context record.
      - final_result(completion_summary: string): Finish after exactly one episode exists.
    "#
    scratchpad "No prior actions."
  }
  @@assert({{ this.action == "READ_TRACE" }})
}

test ContinueUnreadTraceBeforeWrite {
  functions [DecideNextExtractStep]
  args {
    runtime_dashboard #"
      CONTEXT: 20000/200000 (10%) [normal]
      NOTES: 0 findings
      Trace reads: 1 chunk(s)
      Next unread offset: 100
      Pruned offsets: none
    "#
    run_instruction #"
      This trace has 220 lines. Read all chunks before writing.
    "#
    tool_manifest #"
      - read_trace(start_line: int?, line_count: int?): Read the next numbered trace chunk.
      - save_context(kind: string, title: string, body: string): Save one context record.
      - final_result(completion_summary: string): Finish after exactly one episode exists.
    "#
    scratchpad #"
      Action: read_trace
      Status: ok
      Observation:
      [220 lines, showing 1-100] — 120 more lines, call read_trace(start_line=101, line_count=100) for the next chunk
    "#
  }
  @@assert({{ this.action == "READ_TRACE" }})
}

test LongTraceNotesFindingBeforeWrite {
  functions [DecideNextExtractStep]
  args {
    runtime_dashboard #"
      CONTEXT: 42000/200000 (21%) [normal]
      NOTES: 0 findings
      Trace reads: 3 chunk(s)
      Next unread offset: none
      Pruned offsets: none
    "#
    run_instruction #"
      This trace has 240 lines and is fully read. An explicit durable architecture decision appears at line 188.
    "#
    tool_manifest #"
      - note_trace_findings(theme: string?, line: int?, quote: string?, level: string?): Record one trace finding with line evidence.
      - save_context(kind: string, title: string, body: string): Save one context record.
    "#
    scratchpad #"
      Action: read_trace
      Status: ok
      Observation:
      188 user: We decided that durable context stays in the DB store because runtime queues are separate and temporary.
    "#
  }
  @@assert({{ this.action == "NOTE_TRACE_FINDINGS" }})
  @@assert({{ this.note_trace_findings.level == "DECISION" }})
}

test LongTraceNoReusableSignalCheckpoint {
  functions [DecideNextExtractStep]
  args {
    runtime_dashboard #"
      CONTEXT: 39000/200000 (20%) [normal]
      NOTES: 0 findings
      Trace reads: 2 chunk(s)
      Next unread offset: none
      Pruned offsets: none
    "#
    run_instruction #"
      This long trace is fully read after multiple chunks. No note_trace_findings checkpoint has been called yet. It only formats files and reruns tests; no reusable rule, fact, preference, constraint, decision, or reference appears.
    "#
    tool_manifest #"
      - note_trace_findings(theme: string?, line: int?, quote: string?, level: string?): Record one trace finding with line evidence, or call with no args for none.
      - save_context(kind: string, title: string, body: string): Save one context record.
    "#
    scratchpad "Action: read_trace\nStatus: ok\nObservation:\nAll chunks covered routine formatter/test work."
  }
  @@assert({{ this.action == "NOTE_TRACE_FINDINGS" }})
}

test PruneOlderTraceReadsUnderPressure {
  functions [DecideNextExtractStep]
  args {
    runtime_dashboard #"
      CONTEXT: 130000/200000 (65%) [soft]
      NOTES: 2 findings (1 durable, 1 implementation) across 1 theme(s)
      Trace reads: 3 chunk(s)
      Next unread offset: none
      Pruned offsets: none
    "#
    run_instruction "The trace is fully read and findings from the first two chunks have been captured."
    tool_manifest #"
      - prune_trace_reads(start_lines: int[]): Prune earlier read_trace results after findings are noted.
      - save_context(kind: string, title: string, body: string): Save one context record.
    "#
    scratchpad #"
      Action: read_trace
      Status: ok
      Observation:
      [300 lines, showing 1-100]

      Action: read_trace
      Status: ok
      Observation:
      [300 lines, showing 101-200]

      Action: read_trace
      Status: ok
      Observation:
      [300 lines, showing 201-300]

      Action: note_trace_findings
      Status: ok
      Observation:
      Noted 1 finding (total 2 so far).
    "#
  }
  @@assert({{ this.action == "PRUNE_TRACE_READS" }})
  @@assert({{ this.prune_trace_reads.start_lines|length > 0 }})
  @@assert({{ this.prune_trace_reads.start_lines[0] == 1 }})
}

test RoutineTraceSavesArchivedEpisode {
  functions [DecideNextExtractStep]
  args {
    runtime_dashboard #"
      CONTEXT: 1000/200000 (1%) [normal]
      NOTES: 0 findings
      Trace reads: 1 chunk(s)
      Next unread offset: none
      Pruned offsets: none
    "#
    run_instruction "The short trace is fully read. It only ran formatting and confirmed tests passed."
    tool_manifest #"
      - save_context(kind: string, title: string, body: string, status: string?): Save one context record.
      - final_result(completion_summary: string): Finish after exactly one episode exists.
    "#
    scratchpad "Action: read_trace\nStatus: ok\nObservation:\nformatter ran; tests passed; no durable signal."
  }
  @@assert({{ this.action == "SAVE_CONTEXT" }})
  @@assert({{ this.save_context.kind == "EPISODE" }})
  @@assert({{ this.save_context.status == "ARCHIVED" }})
  @@assert({{ this.save_context.user_intent != null and this.save_context.user_intent|length > 0 }})
  @@assert({{ this.save_context.what_happened != null and this.save_context.what_happened|length > 0 }})
}

test DurablePreferenceSavesPreference {
  functions [DecideNextExtractStep]
  args {
    runtime_dashboard "CONTEXT: 1000/200000 (1%) [normal]\nNOTES: 1 findings (1 durable, 0 implementation)\nTrace reads: 1 chunk(s)\nNext unread offset: none"
    run_instruction "The fully read trace includes a stable user correction: keep final replies short and skip redundant recaps. The current-session episode already exists; the durable preference remains unsaved."
    tool_manifest "- save_context(kind: string, title: string, body: string): Save one context record."
    scratchpad "Action: read_trace\nStatus: ok\nObservation:\nuser: Keep final replies short and skip redundant recaps.\n\nAction: save_context\nStatus: ok\nObservation:\n{\"ok\": true, \"result\": {\"record\": {\"kind\": \"episode\", \"source_session_id\": \"baml-test-session\"}}}\n\nAction: note_trace_findings\nStatus: ok\nObservation:\nTheme: reply style. Quote: keep final replies short and skip redundant recaps."
  }
  @@assert({{ this.action == "SAVE_CONTEXT" }})
  @@assert({{ this.save_context.kind == "PREFERENCE" }})
}

test DurableConstraintSavesConstraint {
  functions [DecideNextExtractStep]
  args {
    runtime_dashboard "CONTEXT: 1000/200000 (1%) [normal]\nNOTES: 1 findings (1 durable, 0 implementation)\nTrace reads: 1 chunk(s)\nNext unread offset: none"
    run_instruction "The fully read trace establishes a durable invariant: future extraction runs must not mutate unfetched context records. The current-session episode already exists; the durable constraint remains unsaved."
    tool_manifest "- save_context(kind: string, title: string, body: string): Save one context record."
    scratchpad "Action: save_context\nStatus: ok\nObservation:\n{\"ok\": true, \"result\": {\"record\": {\"kind\": \"episode\", \"source_session_id\": \"baml-test-session\"}}}\n\nAction: note_trace_findings\nStatus: ok\nObservation:\nTheme: mutation invariant. Quote: never revise or archive a context record before fetching it in the current run."
  }
  @@assert({{ this.action == "SAVE_CONTEXT" }})
  @@assert({{ this.save_context.kind == "CONSTRAINT" }})
}

test DurableDecisionSavesDecisionWithWhy {
  functions [DecideNextExtractStep]
  args {
    runtime_dashboard "CONTEXT: 1000/200000 (1%) [normal]\nNOTES: 1 findings (1 durable, 0 implementation)\nTrace reads: 1 chunk(s)\nNext unread offset: none"
    run_instruction "The fully read trace establishes a durable architecture decision with rationale. The current-session episode already exists; the durable decision remains unsaved."
    tool_manifest "- save_context(kind: string, title: string, body: string, decision: string?, why: string?): Save one context record."
    scratchpad "Action: save_context\nStatus: ok\nObservation:\n{\"ok\": true, \"result\": {\"record\": {\"kind\": \"episode\", \"source_session_id\": \"baml-test-session\"}}}\n\nAction: note_trace_findings\nStatus: ok\nObservation:\nTheme: storage boundary. Quote: durable context is DB-backed so sessions can replay it; runtime state stays separate."
  }
  @@assert({{ this.action == "SAVE_CONTEXT" }})
  @@assert({{ this.save_context.kind == "DECISION" }})
  @@assert({{ this.save_context.decision|length > 0 }})
  @@assert({{ this.save_context.why|length > 0 }})
}

test StableDependencySavesFact {
  functions [DecideNextExtractStep]
  args {
    runtime_dashboard "CONTEXT: 1000/200000 (1%) [normal]\nNOTES: 1 findings (1 durable, 0 implementation)\nTrace reads: 1 chunk(s)\nNext unread offset: none"
    run_instruction "The fully read trace establishes that image workflows require libvips. It gives no decision rationale. The current-session episode already exists; the durable dependency fact remains unsaved."
    tool_manifest "- save_context(kind: string, title: string, body: string): Save one context record."
    scratchpad "Action: save_context\nStatus: ok\nObservation:\n{\"ok\": true, \"result\": {\"record\": {\"kind\": \"episode\", \"source_session_id\": \"baml-test-session\"}}}\n\nAction: note_trace_findings\nStatus: ok\nObservation:\nTheme: dependency. Quote: image workflows require libvips in the environment."
  }
  @@assert({{ this.action == "SAVE_CONTEXT" }})
  @@assert({{ this.save_context.kind == "FACT" }})
  @@assert({{ not this.save_context.why }})
}

test ExternalSourceSavesReference {
  functions [DecideNextExtractStep]
  args {
    runtime_dashboard "CONTEXT: 1000/200000 (1%) [normal]\nNOTES: 1 findings (1 durable, 0 implementation)\nTrace reads: 1 chunk(s)\nNext unread offset: none"
    run_instruction "The fully read trace says incident ownership and current status live in the Ops dashboard, which future sessions should consult for incidents. The current-session episode already exists; the durable reference remains unsaved."
    tool_manifest "- save_context(kind: string, title: string, body: string): Save one context record."
    scratchpad "Action: save_context\nStatus: ok\nObservation:\n{\"ok\": true, \"result\": {\"record\": {\"kind\": \"episode\", \"source_session_id\": \"baml-test-session\"}}}\n\nAction: note_trace_findings\nStatus: ok\nObservation:\nTheme: incident source. Quote: use the Ops dashboard for incident ownership and current status."
  }
  @@assert({{ this.action == "SAVE_CONTEXT" }})
  @@assert({{ this.save_context.kind == "REFERENCE" }})
}

test DuplicateRiskSearchesContext {
  functions [DecideNextExtractStep]
  args {
    runtime_dashboard "CONTEXT: 1000/200000 (1%) [normal]\nNOTES: 1 findings (1 durable, 0 implementation)\nTrace reads: 1 chunk(s)\nNext unread offset: none"
    run_instruction "Relevant existing durable records are shown below; treat them as a shortlist only and fetch before revision.\n\nRelevant existing durable records:\n- rec_storage | decision | DB context boundary | Durable context lives in the DB store."
    tool_manifest #"
      - search_context(query: string, kind: string?, status: string?): Search saved context by meaning.
      - get_context(record_ids: string[]): Fetch saved context records by ID.
      - save_context(kind: string, title: string, body: string): Save one context record.
    "#
    scratchpad "Action: read_trace\nStatus: ok\nObservation:\nThe new trace repeats the DB context boundary decision with slightly sharper wording."
  }
  @@assert({{ this.action == "SEARCH_CONTEXT" or this.action == "GET_CONTEXT" }})
  @@assert({{ this.action != "SAVE_CONTEXT" }})
}

test SearchHitFetchesBeforeRevision {
  functions [DecideNextExtractStep]
  args {
    runtime_dashboard "CONTEXT: 2000/200000 (1%) [normal]\nNOTES: 1 findings (1 durable, 0 implementation)\nTrace reads: 1 chunk(s)\nNext unread offset: none"
    run_instruction "A search_context call found a nearby existing record. The current-session episode already exists. Fetch the full search hit before deciding whether to revise it."
    tool_manifest #"
      - search_context(query: string): Search saved context by meaning.
      - get_context(record_ids: string[]): Fetch full context records by ID before any revision.
      - revise_context(record_id: string, reason: string, kind: string, title: string, body: string): Revise an existing context record.
    "#
    scratchpad #"
      Action: save_context
      Status: ok
      Observation:
      {"ok": true, "result": {"record": {"kind": "episode", "source_session_id": "baml-test-session"}}}

      Action: search_context
      Status: ok
      Observation:
      {"count": 1, "hits": [{"record_id": "rec_storage", "kind": "decision", "title": "DB context boundary", "body_preview": "Durable context lives in the DB store."}]}
    "#
  }
  @@assert({{ this.action == "GET_CONTEXT" }})
}

test FetchedSameMeaningRevisesContext {
  functions [DecideNextExtractStep]
  args {
    runtime_dashboard "CONTEXT: 2000/200000 (1%) [normal]\nNOTES: 1 findings (1 durable, 0 implementation)\nTrace reads: 1 chunk(s)\nNext unread offset: none"
    run_instruction "The fetched record has the same core decision but needs the rationale tightened."
    tool_manifest #"
      - revise_context(record_id: string, reason: string, kind: string, title: string, body: string): Revise an existing context record with a complete improved payload.
      - save_context(kind: string, title: string, body: string): Save one context record.
    "#
    scratchpad #"
      Action: get_context
      Status: ok
      Observation:
      {"count": 1, "records": [{"record_id": "rec_storage", "kind": "decision", "title": "DB context boundary", "body": "Durable context lives in the DB store.", "decision": "Durable context lives in the DB store.", "why": "It must survive sessions."}]}
    "#
  }
  @@assert({{ this.action == "REVISE_CONTEXT" }})
}

test FinalBeforeEpisodeSavesEpisode {
  functions [DecideNextExtractStep]
  args {
    runtime_dashboard "CONTEXT: 3000/200000 (2%) [normal]\nNOTES: 1 findings (1 durable, 0 implementation)\nTrace reads: 1 chunk(s)\nNext unread offset: none"
    run_instruction "The trace is fully read. One durable fact has already been saved, but no current-session episode exists."
    tool_manifest #"
      - save_context(kind: string, title: string, body: string): Save one context record.
      - final_result(completion_summary: string): Finish after exactly one episode exists.
    "#
    scratchpad #"
      Action: save_context
      Status: ok
      Observation:
      {"ok": true, "result": {"record": {"kind": "fact", "source_session_id": "baml-test-session"}}}
    "#
  }
  @@assert({{ this.action == "SAVE_CONTEXT" }})
  @@assert({{ this.save_context.kind == "EPISODE" }})
  @@assert({{ this.save_context.user_intent != null and this.save_context.user_intent|length > 0 }})
  @@assert({{ this.save_context.what_happened != null and this.save_context.what_happened|length > 0 }})
}

test FinalStepAfterEpisodeSave {
  functions [DecideNextExtractStep]
  args {
    runtime_dashboard #"
      CONTEXT: 1000/200000 (1%) [normal]
      NOTES: 0 findings
      Trace reads: 1 chunk(s)
      Next unread offset: none
      Pruned offsets: none
    "#
    run_instruction #"
      Read the trace, write exactly one episode record, and write only the
      strongest durable records. The trace has already been fully read.
    "#
    tool_manifest #"
      - read_trace(start_line: int?, line_count: int?): Read the next numbered trace chunk.
      - save_context(kind: string, title: string, body: string): Save one context record.
      - final_result(completion_summary: string): Finish after exactly one episode exists.
    "#
    scratchpad #"
      Action: read_trace
      Status: ok
      Observation:
      [3 lines, trace coverage complete] All trace lines have already been read.

      Action: save_context
      Status: ok
      Observation:
      {"ok": true, "result": {"record": {"kind": "episode", "source_session_id": "baml-test-session"}}}
    "#
  }
  @@assert({{ this.action == "FINAL_RESULT" }})
}

test EpisodeWithReusableDecisionSavesDecisionBeforeFinal {
  functions [DecideNextExtractStep]
  args {
    runtime_dashboard #"
      CONTEXT: 3000/200000 (2%) [normal]
      NOTES: 0 findings
      Trace reads: 1 chunk(s)
      Next unread offset: none
      Pruned offsets: none
    "#
    run_instruction "The trace is fully read. The current-session episode already exists, but a reusable PostgreSQL-over-MySQL project decision remains only in the trace and episode text. Save the durable decision before final_result."
    tool_manifest #"
      - save_context(kind: string, title: string, body: string, decision: string?, why: string?): Save one context record.
      - final_result(completion_summary: string): Finish after exactly one episode exists.
    "#
    scratchpad #"
      Action: read_trace
      Status: ok
      Observation:
      user: Good. Also, we decided to use PostgreSQL instead of MySQL for this project.
      assistant: Understood. Decision: PostgreSQL over MySQL. I'll update the connection string and ORM configuration.

      Action: save_context
      Status: ok
      Observation:
      {"ok": true, "result": {"record": {"kind": "episode", "title": "Database Configuration Update", "body": "The session switched the project's primary database backend from MySQL to PostgreSQL.", "source_session_id": "baml-test-session"}}}
    "#
  }
  @@assert({{ this.action == "SAVE_CONTEXT" }})
  @@assert({{ this.save_context.kind == "DECISION" }})
  @@assert({{ this.save_context.decision|length > 0 }})
  @@assert({{ this.save_context.why|length > 0 }})
}

test ImplementationOnlyAfterEpisodeFinalizes {
  functions [DecideNextExtractStep]
  args {
    runtime_dashboard #"
      CONTEXT: 3000/200000 (2%) [normal]
      NOTES: 0 findings
      Trace reads: 1 chunk(s)
      Next unread offset: none
      Pruned offsets: none
    "#
    run_instruction "The trace is fully read. A durable decision and the current-session episode already exist. The only remaining unsaved content is an implementation fix to a local timeout constant."
    tool_manifest #"
      - save_context(kind: string, title: string, body: string): Save one context record.
      - final_result(completion_summary: string): Finish after exactly one episode exists.
    "#
    scratchpad #"
      Action: read_trace
      Status: ok
      Observation:
      The trace fixed a local timeout constant and also established one architecture decision.

      Action: save_context
      Status: ok
      Observation:
      {"ok": true, "result": {"record": {"kind": "decision", "title": "Runtime state remains separate from durable context"}}}

      Action: save_context
      Status: ok
      Observation:
      {"ok": true, "result": {"record": {"kind": "episode", "source_session_id": "baml-test-session"}}}
    "#
  }
  @@assert({{ this.action == "FINAL_RESULT" }})
}
