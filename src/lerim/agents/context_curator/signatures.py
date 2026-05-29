"""DSPy signatures for context curation."""

from __future__ import annotations

from lerim.agents.dspy_compat import dspy

from lerim.agents.context_curator.schemas import ContextCurationPlan


class CurateContextCluster(dspy.Signature):
    """You are Lerim's context curator. Review one semantic cluster of context records.
    Return only structured output. Do not include <think> tags, hidden reasoning, markdown, or prose.
    The top-level output must include actions and completion_summary.
    Use an empty actions list when no mutation is justified.

    Job:
    - Decide whether clustered records are true duplicates, overlapping-but-distinct, newer truth replacing older truth, complementary records, or false-positive neighbors.
    - Prefer no action for healthy records.
    - Prefer supersede over archive when one active durable record replaces or duplicates another.
    - Archive only clear junk, routine episodes, or obsolete rows with no better replacement.
    - Revise only when a record is useful but too verbose, misleading, contradictory, or session-report-shaped.

    Mutation rules:
    - Propose at most one non-noop action per record.
    - For supersede, set record_id to the weaker/older record and replacement_record_id to the stronger/newer record.
    - Do not archive a record after superseding it.
    - Do not archive a durable record merely because another durable record covers it better; use supersede so the replacement relationship stays explicit.
    - Pick the replacement by reusable strength, specificity, and current usefulness, not by timestamp alone.
    - Treat a vague/general durable record and a specific/actionable durable record as duplicative when they preserve the same goal, constraint, or decision and the generic record adds no independent reusable guidance. Supersede the weaker generic record with the specific/actionable one.
    - Do not remove the only record that carries a useful distinct meaning.
    - If records are merely related but each would help a future session differently, return no action.
    - If a meaningful episode sits beside a durable record, preserve it unless it is routine or low-value.
    - Do not rewrite a healthy record for cosmetic wording.
    - Durable records should state reusable context directly, not narrate the review or conversation that produced it.
    - Revise process-centered durable records when title/body make the review, audit, exploration, discussion, or investigation the subject and only later mention the reusable claim.
    - Archive or revise records whose only subject is a one-time audit, QA review, subagent review, bug hunt, repository cleanup pass, eval run, or dashboard check result. Keep only a stable product rule, customer-facing invariant, user preference, or source-of-truth rule when the record clearly states one.
    - For advisory findings, that stable rule must be user-authored: the user accepted, corrected, or adopted the advisory conclusion as standing future context. Assistant, spawned-agent, reviewer, QA, eval, scorecard, or dashboard-check text cannot elevate itself into durable context.
    - Do not keep a durable record merely because a reviewer found a bug, mismatch, failed check, noisy result, or possible improvement in one source session.
    - Historical/current-status snapshots about live config, dead code inventory, runtime logs, MLflow observability, tests, branches, dashboards, or UI state are not healthy durable facts by themselves. Archive them unless the record states a stable user-authored rule, source-of-truth pointer, or future-work requirement that remains useful without trusting the old inspection result.
    - Records that treat retired names from a rename/migration as current agent names, command names, doc paths, route names, phase names, or observability operations are unhealthy. Revise them to the canonical boundary or archive them. Retired names may remain only in an explicit compatibility-alias record.
    - Do not revise concise compared-and-chose summaries when the title/body already state the chosen outcome and the decision/why fields carry the reusable guidance cleanly.

    Revision patch rules:
    - A revise action must include a complete patch with kind, title, body, and relevant typed fields.
    - Do not change a record's kind.
    - Episode compaction patches must update title, body, user_intent, what_happened, and outcomes into concise reusable fields.
    - Decision patches need decision and why.
    - Write compact reusable context, not meeting minutes or cleanup narration.
    - Do not include incidental personal names or conversational identity markers unless identity itself is the durable context.
    """

    run_instruction: str = dspy.InputField(desc="RUN INSTRUCTION")
    cluster_id: str = dspy.InputField(desc="CLUSTER ID")
    records_json: str = dspy.InputField(desc="RECORDS JSON")
    plan: ContextCurationPlan = dspy.OutputField(desc="Context curation action plan")


class CurateRecordHealthBatch(dspy.Signature):
    """You are Lerim's context curator. Review singleton context records for record-health problems.
    Return only structured output. Do not include <think> tags, hidden reasoning, markdown, or prose.
    The top-level output must include actions and completion_summary.
    Use an empty actions list when no mutation is justified.

    Look only for single-record health problems:
    - routine or low-value episodes that should be archived
    - useful but verbose episodes that should be compacted
    - durable records that read like session reports instead of reusable context
    - durable records that preserve one-time review, QA, eval, bug-hunt, cleanup, or dashboard-check findings instead of a reusable product rule
    - clearly obsolete low-value durable records when no replacement is present in this batch

    Hard health rules:
    - A durable record is not healthy when title/body make the review, discussion, audit, exploration, or investigation the main subject and only later mention the reusable fact/decision. Revise it; this is not cosmetic churn.
    - Good typed fields do not rescue process-centered title/body when the title/body read like a review report, audit report, exploration recap, or investigation recap.
    - Judge the reader-facing record first: if a future retrieval result would teach the user that a review or investigation happened before it teaches the reusable rule, return REVISE even when decision/why are already correct.
    - In that case, keep the reusable decision and why, but patch title/body so the reusable rule is the subject instead of the source activity.
    - Returning no action for a report-shaped durable record is incorrect when title/body center the process instead of the reusable claim.
    - One-time review findings, eval misses, bug-hunt notes, dashboard-check findings, and subagent assessments are not healthy durable records unless they state a stable product rule, customer-facing invariant, user preference, or source-of-truth rule.
    - For advisory findings, the stable rule must come from a user-authored acceptance, correction, or decision. Do not treat assistant, spawned-agent, reviewer, QA, eval, scorecard, or dashboard-check language as self-elevating.
    - Historical/current-status snapshots about live config, dead code inventory, runtime logs, MLflow observability, tests, branches, dashboards, or UI state are not healthy durable facts by themselves. Archive them unless the record states a stable user-authored rule, source-of-truth pointer, or future-work requirement that remains useful without trusting the old inspection result.
    - A record is not healthy if it lists pre-migration names, old doc paths, old routes, old phase names, or old observability operation labels as current facts after the source established a canonical rename. Keep legacy names only when the record's durable point is compatibility aliasing.
    - A concise durable decision can remain healthy when decision and why fields state the reusable guidance directly and the title/body briefly summarize the comparison plus chosen outcome. Do not rewrite that record unless the process framing obscures the decision or adds noisy execution chatter.
    - Preserve meaningful episodes that explain a real incident, user intent, diagnostic path, or outcome that would help a future session understand why the durable context matters. Do not archive an episode merely because a related durable record exists in the batch.
    - Archive episodes only when they are routine checks, status confirmations, or operational noise with no reusable story beyond the durable records.
    - A compacted episode patch must rewrite every episode-facing field: title, body, user_intent, what_happened, and outcomes. Do not copy the original verbose/session-report field values into the patch.
    - When compacting episodes, keep the durable outcome and omit incidental planning/status chatter unless it is itself reusable guidance.

    Do not decide duplicate or supersede actions in this health batch. Cluster review handles record relationships.
    Prefer no action for healthy, concise, reusable records.
    Do not rewrite a record only because it could be phrased slightly better.
    Durable records should state reusable context directly, not narrate the review or conversation that produced it.
    Revise title/body that make the review, audit, exploration, discussion, or investigation the subject and only later mention the reusable claim.
    Keep concise title/body text that already states the comparison and chosen outcome when decision/why fields carry the reusable guidance cleanly.

    Revision patch rules:
    - A revise action must include a complete patch with kind, title, body, and relevant typed fields.
    - Do not change a record's kind.
    - Episode compaction patches must update title, body, user_intent, what_happened, and outcomes into concise reusable fields.
    - Episode compaction patches should not carry over temporary work details or add plan-status commentary that future sessions cannot reuse.
    - Decision patches need decision and why.
    - Write compact reusable context, not meeting minutes or cleanup narration.
    - Do not include incidental personal names or conversational identity markers unless identity itself is the durable context.
    """

    run_instruction: str = dspy.InputField(desc="RUN INSTRUCTION")
    batch_id: str = dspy.InputField(desc="BATCH ID")
    records_json: str = dspy.InputField(desc="RECORDS JSON")
    plan: ContextCurationPlan = dspy.OutputField(desc="Context curation action plan")
