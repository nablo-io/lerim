class ContextBriefLineDraft {
  text string @description("Compact memory statement without inline citations.")
  record_ids string[] @description("Exact source record IDs copied from the candidate records.")
}

class ContextBriefDraftOutput {
  summary ContextBriefLineDraft[]
  start_here ContextBriefLineDraft[]
  current_handoff ContextBriefLineDraft[]
  decisions ContextBriefLineDraft[] @description("Only cite candidate records whose kind is decision.")
  constraints_preferences ContextBriefLineDraft[] @description("Only cite candidate records whose kind is preference or constraint.")
  project_facts ContextBriefLineDraft[] @description("Only cite candidate records whose kind is fact or reference.")
  open_risks ContextBriefLineDraft[]
  follow_up_queries ContextBriefLineDraft[]
}

function CompileContextBrief(
  candidate_profile_json: string,
  candidate_records_json: string
) -> ContextBriefDraftOutput {
  client MiniMaxM27
  prompt #"
    {{ _.role("system") }}
    You are Lerim's context-brief compiler. Compile a compact startup brief from persisted context records.
    Return only structured output. Do not include <think> tags, hidden reasoning, markdown, or prose.

    Rules:
    - Use only the supplied candidate records.
    - Produce the fixed fields: summary, start_here, current_handoff, decisions, constraints_preferences, project_facts, open_risks, follow_up_queries.
    - Every field must be a list of objects with text and record_ids.
    - Every non-empty line must include at least one exact record_id copied from the candidate records.
    - Put record IDs only in record_ids, never in text.
    - Keep current_handoff empty unless candidate records explicitly describe recent flow, current work, open loops, or next actions.
    - Keep open_risks empty unless candidate records explicitly describe an unresolved risk, blocker, requested follow-up, or review queue item.
    - Treat validation/build/check results as historical persisted evidence; line text must say "Persisted record says ..." and "rerun relevant checks after edits."
    - Prefer decisions, preferences, constraints, stable facts, and high-value references over episode detail.
    - Respect source kinds exactly: decisions may cite only decision records; constraints_preferences may cite only preference or constraint records; project_facts may cite only fact or reference records.
    - Leave a section empty when there are no candidate records for that section's allowed kinds.
    - Do not place a fact, constraint, preference, or reference in decisions just because it sounds important or rule-like.
    - Do not duplicate the same point in multiple detail sections. Choose the one section that matches the cited record kind.
    - Do not invent current workspace state beyond stored records.
    - Do not repeat the same memory across fields.
    - Keep the whole output compact enough for a roughly 50-line markdown file.

    {{ _.role("user") }}
    CANDIDATE PROFILE JSON:
    {{ candidate_profile_json }}

    CANDIDATE RECORDS JSON:
    {{ candidate_records_json }}

    {{ ctx.output_format }}
  "#
}
