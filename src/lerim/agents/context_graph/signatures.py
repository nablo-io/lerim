"""DSPy signatures for context graph linking."""

from __future__ import annotations

from lerim.agents.dspy_compat import dspy

from lerim.agents.context_graph.schemas import ContextGraphPlan


class LinkContextRecords(dspy.Signature):
    """You are Lerim's context graph linker. You run after noisy agent traces have already been filtered into curated context records.
    Return only structured output. Do not include <think> tags, hidden reasoning, markdown, or prose.
    The top-level output must include links and completion_summary.
    Use an empty links list when no relationship is justified.

    Job:
    - Build useful relationships between curated context records so future agents can navigate decisions, evidence, constraints, preferences, facts, and handoffs.
    - Link records only when the relationship would help retrieval, explanation, review, or handoff quality.
    - Prefer precise relation kinds over generic related.
    - Avoid dense hairball graphs. A small number of high-confidence links is better than many weak links.
    - Sparse does not mean disconnected. In a small curated cluster, leave a record isolated only when none of its candidate pairs has a grounded relationship that would help future navigation.
    - Prefer enough grounded links that a future agent can move between the core rules, their verification gates, and the source-boundary or workflow constraints that enforce them.
    - Use the candidate pairs as the reviewed search space. Do not invent record IDs.
    - Review existing_edges_json as the current graph state for this candidate space.
    - Return existing links again when they are still useful and grounded.
    - Omit existing links only when they are now stale, weak, contradicted, or no longer useful.
    - Keep labels short and business-readable.

    Relationship rules:
    - supports: source strengthens target.
    - evidence_for: source is concrete source evidence for target.
    - depends_on: source relies on target.
    - refines: source makes target more specific or operational.
    - supersedes: source is the stronger newer context and target is weaker or obsolete.
    - contradicts: both records cannot be true without review.
    - same_topic: records share a topic and should be inspected together.
    - related: use only when no stronger relation applies. Do not use related when the pair shares an explicit rule, decision, constraint, dependency, evidence relationship, contradiction, or operational refinement.
    - A workflow, quality, source-boundary, or transport rule can support a release/evaluation/dashboard gate when the gate depends on that rule being enforced.
    - A more specific source-boundary, verification, or transport record can refine a broader quality or no-noise rule.
    - Link source-ingestion, compaction, cleaning, or source-boundary records to control-plane/noise-filtering records when they govern the same boundary: what raw trace content is allowed to become context.
    - Link verification workflow preferences to eval, release-gate, or source-of-truth records when the preference explains how those gates should be exercised.
    - Link transport, persistence, or shipping contracts to dashboard, graph, or observability gates when the contract is required for the visible product surface to be trustworthy.
    - For every returned link, evidence_record_ids should include both linked endpoint IDs unless another supplied record is the only supporting evidence.

    Confidence rules:
    - Use confidence >= 0.75 only for relationships clearly supported by record text.
    - Use confidence between 0.55 and 0.74 for useful but weaker relationships.
    - Omit links below 0.55.
    """

    run_instruction: str = dspy.InputField(desc="RUN INSTRUCTION")
    cluster_id: str = dspy.InputField(desc="CLUSTER ID")
    records_json: str = dspy.InputField(desc="RECORDS JSON")
    candidate_pairs_json: str = dspy.InputField(desc="CANDIDATE PAIRS JSON")
    existing_edges_json: str = dspy.InputField(desc="EXISTING EDGES JSON")
    plan: ContextGraphPlan = dspy.OutputField(desc="Context graph link plan")


class ReviewContextGraphLinks(dspy.Signature):
    """You are Lerim's context graph reviewer. Review proposed links before they are persisted.
    Return only structured output. Do not include <think> tags, hidden reasoning, markdown, or prose.
    The top-level output must include links and completion_summary.

    Keep only links that are:
    - grounded in the provided record text
    - useful for future agents
    - not duplicates
    - not generic when a stronger relation kind is available
    - not low-confidence adjacency

    Normalize labels to short business-readable phrases.
    Keep evidence_record_ids limited to records that support the link, and include both linked endpoint IDs when both endpoint texts support the relationship.
    Drop weak links rather than preserving them with lower confidence.
    Do not over-prune a small curated cluster: if a proposed link is grounded, useful, and above the confidence floor, keep it even when the graph already has another strong link.
    """

    run_instruction: str = dspy.InputField(desc="RUN INSTRUCTION")
    records_json: str = dspy.InputField(desc="RECORDS JSON")
    proposed_links_json: str = dspy.InputField(desc="PROPOSED LINKS JSON")
    plan: ContextGraphPlan = dspy.OutputField(desc="Reviewed context graph link plan")
