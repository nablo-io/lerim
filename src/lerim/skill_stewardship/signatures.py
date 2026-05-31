"""DSPy signatures for evidence-backed instruction artifact updates."""

from __future__ import annotations

from lerim.agents.dspy_compat import dspy
from lerim.skill_stewardship.schemas import SkillProposalDraft


class CompileSkillUpdateProposal(dspy.Signature):
    """Compile one small, evidence-backed update proposal for a registered instruction artifact.

    Return only structured output. Do not include hidden reasoning, markdown outside fields, or prose.

    Rules:
    - Use only the supplied durable records as evidence.
    - Propose no update when evidence is weak, one-off, private, or unrelated to the target.
    - Respect the artifact manifest exactly. Do not invent unsupported files or directories.
    - Every patch must cite at least one exact record_id in evidence_record_ids.
    - Prefer small edits that improve future agent behavior.
    - Preserve existing skill semantics unless the evidence clearly supports changing them.
    - Do not add scripts, assets, permissions, MCP config, hooks, or tool allowlists unless the manifest and user policy explicitly allow them.
    - Treat entry-file frontmatter and trigger descriptions as high risk.
    - Keep SKILL.md and instruction entry files concise; place longer reusable details into supported reference files.
    - If no proposal is warranted, return an empty patches list with a short summary explaining abstention.
    """

    target_json: str = dspy.InputField(desc="REGISTERED TARGET JSON")
    manifest_json: str = dspy.InputField(desc="ARTIFACT MANIFEST JSON")
    files_json: str = dspy.InputField(desc="CURRENT TARGET FILES JSON")
    records_json: str = dspy.InputField(desc="CANDIDATE EVIDENCE RECORDS JSON")
    proposal: SkillProposalDraft = dspy.OutputField(desc="Evidence-backed update proposal")

