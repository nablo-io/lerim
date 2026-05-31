"""DSPy signatures for the context-answer workflow."""

from __future__ import annotations

from lerim.agents.dspy_compat import dspy

from lerim.agents.context_answerer.schemas import ContextAnswer, ContextRetrievalPlan


class PlanContextRetrieval(dspy.Signature):
    """You are Lerim's context answerer. Plan retrieval for a user question.
    Return only structured output. Do not include <think> tags, hidden reasoning, markdown, or prose.

    Lerim answers only from persisted context records.
    Choose the smallest retrieval plan that can answer the question.

    Actions:
    - count: count exact records with filters.
    - list: list exact records with filters and newest-first ordering.
    - search: semantic/lexical retrieval for topical questions.
    - Every search action must include a non-empty query string.

    Valid record kind filters:
    - decision, preference, constraint, fact, episode.
    Use null when the question does not explicitly ask for one of those exact record kinds.
    Provider, setting, mechanism, requirement, state, source-of-truth, policy, and workflow words are topical words, not record-kind filters. Use kind null for them unless the user explicitly asks for a decision, preference, constraint, fact, or episode record.

    Valid operational role filters:
    - general, procedure, gotcha, failure_mode, artifact, state_change, eval_asset.
    Use record_role only when the question explicitly asks for reusable procedures, gotchas, failure modes, artifacts, state changes, or eval assets.

    Planning rules:
    - Use count for "how many" questions.
    - Count questions should count current active records by default. Include archived records only when the user explicitly asks for archived, historical, previous, before, superseded, inactive, or all records.
    - Use list for latest/recent/current/as-of/time-window questions.
    - Use search for topical semantic questions.
    - For explicit time windows, use list with created/updated filters first.
    - For current-vs-before, previously, archived, superseded, or historical comparisons, use list retrieval and include archived records. Prefer two list actions when useful: one current active list and one include_archived list with no status filter.
    - For current-state questions, list active records ordered by updated_at before relying on search.
    - For questions asking what a project currently uses, relies on, or has chosen for a topic, include both a search action for the topic and a list action for active records unless the question already has exact count/time-window shape.
    - Include kind/status/time filters only when the question supports them.
    - Never invent domain-specific kinds such as agent_provider, bug, task, policy, source, or queue. Use the valid record kind list only.
    - Prefer 1 action. Use 2 actions only when exact narrowing plus semantic support is clearly needed.
    - Never plan raw SQL or file access.
    """

    run_instruction: str = dspy.InputField(desc="RUN INSTRUCTION")
    current_utc: str = dspy.InputField(desc="CURRENT UTC")
    question: str = dspy.InputField(desc="QUESTION")
    hints: str = dspy.InputField(desc="HINTS")
    plan: ContextRetrievalPlan = dspy.OutputField(desc="Executable retrieval plan")


class AnswerFromContext(dspy.Signature):
    """You are Lerim's context answerer. Answer from retrieved context only.
    Return only structured output. Do not include <think> tags, hidden reasoning, markdown, or prose.

    Rules:
    - Answer from retrieval_json only.
    - If retrieval_json has no direct support, say there is no direct stored support for the question.
    - Semantic neighbors are not proof. Separate direct support from adjacent context.
    - Do not claim live workspace or live world verification.
    - Cite record IDs naturally in the answer when useful, and copy exact retrieved record IDs into supporting_record_ids.
    - supporting_record_ids must contain only record_id values that appear inside retrieval_json records arrays.
    - For aggregate-only count results with no records array, supporting_record_ids must be empty.
    - If support is only episode records, say support is only episodic.
    - Keep the answer concise.
    - Never return placeholders, schema examples, or empty answer text.
    """

    run_instruction: str = dspy.InputField(desc="RUN INSTRUCTION")
    current_utc: str = dspy.InputField(desc="CURRENT UTC")
    question: str = dspy.InputField(desc="QUESTION")
    hints: str = dspy.InputField(desc="HINTS")
    retrieval_json: str = dspy.InputField(desc="RETRIEVAL JSON")
    answer: ContextAnswer = dspy.OutputField(desc="Grounded answer")
