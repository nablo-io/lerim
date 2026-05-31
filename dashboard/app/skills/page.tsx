"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type UIEvent } from "react";
import { api } from "@/lib/api";
import type { SkillProposal, SkillTarget } from "@/lib/types";
import { useToast } from "@/components/Toast";

type ConfirmAction =
  | { kind: "apply"; proposal: SkillProposal }
  | { kind: "reject"; proposal: SkillProposal }
  | { kind: "auto_apply"; target: SkillTarget; enabled: boolean };

export default function SkillsPage() {
  const { addToast } = useToast();
  const [targets, setTargets] = useState<SkillTarget[]>([]);
  const [proposals, setProposals] = useState<SkillProposal[]>([]);
  const [selectedTargetId, setSelectedTargetId] = useState("");
  const [selectedProposalId, setSelectedProposalId] = useState("");
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [selectedPatchIndex, setSelectedPatchIndex] = useState(0);
  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [targetData, proposalData] = await Promise.all([api.getSkillTargets(), api.getSkillProposals()]);
      setTargets(targetData.targets);
      setProposals(proposalData.proposals);
      setSelectedTargetId((current) =>
        current && targetData.targets.some((target) => target.target_id === current)
          ? current
          : targetData.targets[0]?.target_id || "",
      );
      setSelectedProposalId((current) =>
        current && proposalData.proposals.some((proposal) => proposal.proposal_id === current)
          ? current
          : proposalData.proposals[0]?.proposal_id || "",
      );
    } catch (err) {
      addToast({ type: "error", message: err instanceof Error ? err.message : "Failed to load skills" });
    } finally {
      setLoading(false);
    }
  }, [addToast]);

  useEffect(() => {
    load();
  }, [load]);

  const selectedTarget = targets.find((target) => target.target_id === selectedTargetId) || targets[0] || null;
  const filteredProposals = useMemo(
    () => proposals.filter((proposal) => !selectedTarget || proposal.target_id === selectedTarget.target_id),
    [proposals, selectedTarget],
  );
  const selectedProposal =
    filteredProposals.find((proposal) => proposal.proposal_id === selectedProposalId) || filteredProposals[0] || null;
  const selectedPatch = selectedProposal?.patch_json.patches[selectedPatchIndex] || null;
  const hasUnsavedEdit = Boolean(selectedPatch && editText !== (selectedPatch.after_text || ""));

  useEffect(() => {
    setSelectedPatchIndex(0);
  }, [selectedProposal?.proposal_id]);

  useEffect(() => {
    setEditText(selectedPatch?.after_text || "");
  }, [selectedPatch?.after_text, selectedPatchIndex, selectedProposal?.proposal_id]);

  const canDiscardEdits = useCallback(() => {
    return !hasUnsavedEdit || window.confirm("Discard unsaved edits?");
  }, [hasUnsavedEdit]);

  const selectTarget = (targetId: string) => {
    if (targetId === selectedTargetId || !canDiscardEdits()) return;
    setSelectedTargetId(targetId);
    setSelectedProposalId(proposals.find((proposal) => proposal.target_id === targetId)?.proposal_id || "");
    setSelectedPatchIndex(0);
  };

  const selectProposal = (proposalId: string) => {
    if (proposalId === selectedProposalId || !canDiscardEdits()) return;
    setSelectedProposalId(proposalId);
    setSelectedPatchIndex(0);
  };

  const selectPatch = (index: number) => {
    if (index === selectedPatchIndex || !canDiscardEdits()) return;
    setSelectedPatchIndex(index);
  };

  const register = async () => {
    if (!path.trim()) return;
    setBusy("register");
    try {
      const result = await api.addSkillTarget({
        path: path.trim(),
        name: name.trim() || undefined,
        description: description.trim() || undefined,
      });
      setPath("");
      setName("");
      setDescription("");
      addToast({ type: "success", message: "Skill registered" });
      await load();
      setSelectedTargetId(result.target.target_id);
    } catch (err) {
      addToast({ type: "error", message: err instanceof Error ? err.message : "Could not register skill" });
    } finally {
      setBusy(null);
    }
  };

  const refreshTarget = async (target: SkillTarget) => {
    if (!canDiscardEdits()) return;
    setBusy(`refresh:${target.target_id}`);
    try {
      await api.refreshSkillTarget(target.target_id);
      addToast({ type: "success", message: "Scan completed" });
      await load();
    } catch (err) {
      addToast({ type: "error", message: err instanceof Error ? err.message : "Scan failed" });
    } finally {
      setBusy(null);
    }
  };

  const updateAutoApply = async (target: SkillTarget, enabled: boolean) => {
    setBusy(`mode:${target.target_id}`);
    try {
      await api.updateSkillTargetMode(target.target_id, {
        update_mode: enabled ? "auto_apply" : "review",
        auto_apply_policy: { ...target.auto_apply_policy, enabled },
      });
      addToast({ type: "success", message: enabled ? "Auto-apply enabled" : "Auto-apply disabled" });
      await load();
    } catch (err) {
      addToast({ type: "error", message: err instanceof Error ? err.message : "Mode update failed" });
    } finally {
      setBusy(null);
    }
  };

  const toggleAutoApply = (target: SkillTarget) => {
    const enabled = target.update_mode !== "auto_apply";
    if (enabled) {
      setConfirmAction({ kind: "auto_apply", target, enabled });
      return;
    }
    updateAutoApply(target, false);
  };

  const updatePolicyRisk = async (target: SkillTarget, maxRisk: string) => {
    setBusy(`mode:${target.target_id}`);
    try {
      await api.updateSkillTargetMode(target.target_id, {
        update_mode: target.update_mode,
        auto_apply_policy: { ...target.auto_apply_policy, max_risk: maxRisk },
      });
      addToast({ type: "success", message: "Policy updated" });
      await load();
    } catch (err) {
      addToast({ type: "error", message: err instanceof Error ? err.message : "Policy update failed" });
    } finally {
      setBusy(null);
    }
  };

  const applyProposal = async (proposal: SkillProposal) => {
    setBusy(`apply:${proposal.proposal_id}`);
    try {
      await api.applySkillProposal(proposal.proposal_id);
      addToast({ type: "success", message: "Proposal applied" });
      await load();
    } catch (err) {
      addToast({ type: "error", message: err instanceof Error ? err.message : "Apply failed" });
    } finally {
      setBusy(null);
    }
  };

  const rejectProposal = async (proposal: SkillProposal) => {
    setBusy(`reject:${proposal.proposal_id}`);
    try {
      await api.rejectSkillProposal(proposal.proposal_id);
      addToast({ type: "success", message: "Proposal rejected" });
      await load();
    } catch (err) {
      addToast({ type: "error", message: err instanceof Error ? err.message : "Reject failed" });
    } finally {
      setBusy(null);
    }
  };

  const saveEditedProposal = async (proposal: SkillProposal) => {
    if (!selectedPatch) return;
    setBusy(`edit:${proposal.proposal_id}`);
    try {
      await api.editSkillProposal(proposal.proposal_id, {
        ...proposal.patch_json,
        patches: proposal.patch_json.patches.map((patch, index) =>
          index === selectedPatchIndex ? { ...patch, after_text: editText } : patch,
        ),
      });
      addToast({ type: "success", message: "Proposal updated" });
      await load();
    } catch (err) {
      addToast({ type: "error", message: err instanceof Error ? err.message : "Edit failed" });
    } finally {
      setBusy(null);
    }
  };

  const confirmPendingAction = async () => {
    const action = confirmAction;
    setConfirmAction(null);
    if (!action) return;
    if (action.kind === "apply") await applyProposal(action.proposal);
    if (action.kind === "reject") await rejectProposal(action.proposal);
    if (action.kind === "auto_apply") await updateAutoApply(action.target, action.enabled);
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-[var(--text)]">Skills</h1>
          <p className="mt-1 max-w-2xl text-sm text-[var(--text-secondary)]">
            Registered instruction artifacts, evidence-backed proposals, and review gates.
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            if (canDiscardEdits()) load();
          }}
          className="min-h-11 rounded-md border border-[var(--border)] px-4 text-sm font-medium text-[var(--text)] outline-none hover:bg-white/[0.05] focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
        >
          Refresh
        </button>
      </div>

      <section className="grid min-w-0 gap-3 rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4 md:grid-cols-[minmax(0,1fr)_10rem]">
        <div className="grid gap-3 md:grid-cols-3">
          <label className="grid gap-1 md:col-span-3">
            <span className="text-xs font-medium text-[var(--text-secondary)]">Path</span>
            <input
              name="skill-path"
              autoComplete="off"
              value={path}
              onChange={(event) => setPath(event.target.value)}
              placeholder="Path to SKILL.md, AGENTS.md, or skill folder"
              className="min-h-11 rounded-md border border-[var(--border)] bg-black/20 px-3 text-sm text-[var(--text)] outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
            />
          </label>
          <label className="grid gap-1">
            <span className="text-xs font-medium text-[var(--text-secondary)]">Name</span>
            <input
              name="skill-name"
              autoComplete="off"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Name"
              className="min-h-11 rounded-md border border-[var(--border)] bg-black/20 px-3 text-sm text-[var(--text)] outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
            />
          </label>
          <label className="grid gap-1 md:col-span-2">
            <span className="text-xs font-medium text-[var(--text-secondary)]">Intent</span>
            <input
              name="skill-description"
              autoComplete="off"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="Improvement intent"
              className="min-h-11 rounded-md border border-[var(--border)] bg-black/20 px-3 text-sm text-[var(--text)] outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
            />
          </label>
        </div>
        <button
          type="button"
          onClick={register}
          disabled={busy === "register" || !path.trim()}
          className="min-h-11 self-end rounded-md bg-[var(--accent-blue)] px-4 text-sm font-semibold text-white outline-none hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
        >
          Register
        </button>
      </section>

      <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(16rem,20rem)_minmax(0,1fr)]">
        <section className="min-w-0 overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--bg-card)]">
          <div className="border-b border-[var(--border)] px-4 py-3">
            <h2 className="text-sm font-semibold text-[var(--text)]">Registered Skills</h2>
          </div>
          <div className="max-h-[34rem] overflow-y-auto p-2" role="list">
            {loading && <div className="p-3 text-sm text-[var(--text-muted)]">Loading...</div>}
            {!loading && targets.length === 0 && (
              <div className="p-3 text-sm text-[var(--text-muted)]">Register a path above to start.</div>
            )}
            {targets.map((target) => (
              <button
                key={target.target_id}
                type="button"
                aria-pressed={selectedTarget?.target_id === target.target_id}
                onClick={() => selectTarget(target.target_id)}
                className={`mb-2 min-w-0 w-full rounded-md border p-3 text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)] ${
                  selectedTarget?.target_id === target.target_id
                    ? "border-[var(--accent-blue)] bg-blue-500/10"
                    : "border-[var(--border)] bg-white/[0.02] hover:bg-white/[0.05]"
                }`}
                role="listitem"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-semibold text-[var(--text)]">{target.name}</span>
                  <span className="rounded-full bg-white/[0.06] px-2 py-0.5 text-[10px] text-[var(--text-secondary)]">
                    {formatMode(target.update_mode)}
                  </span>
                </div>
                <div className="mt-1 truncate text-xs text-[var(--text-muted)]">{target.path}</div>
                <div className="mt-2 flex flex-wrap gap-1.5 text-[10px] text-[var(--text-secondary)]">
                  <span className="rounded bg-white/[0.05] px-1.5 py-0.5">{formatLabel(target.target_type)}</span>
                  <span className="rounded bg-white/[0.05] px-1.5 py-0.5">{target.file_count || 0} files</span>
                </div>
              </button>
            ))}
          </div>
        </section>

        <section className="min-w-0 space-y-4">
          {selectedTarget ? (
            <div className="min-w-0 overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="truncate text-base font-semibold text-[var(--text)]">{selectedTarget.name}</h2>
                  <p className="mt-1 break-all text-xs text-[var(--text-muted)]">{selectedTarget.path}</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => refreshTarget(selectedTarget)}
                    disabled={busy === `refresh:${selectedTarget.target_id}`}
                    className="min-h-10 rounded-md border border-[var(--border)] px-3 text-xs font-medium text-[var(--text)] outline-none hover:bg-white/[0.05] disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
                  >
                    Scan
                  </button>
                  <button
                    type="button"
                    onClick={() => toggleAutoApply(selectedTarget)}
                    disabled={busy === `mode:${selectedTarget.target_id}`}
                    className={`min-h-10 rounded-md px-3 text-xs font-semibold outline-none disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)] ${
                      selectedTarget.update_mode === "auto_apply"
                        ? "bg-emerald-400 text-slate-950 hover:brightness-110"
                        : "border border-[var(--border)] text-[var(--text)] hover:bg-white/[0.05]"
                    }`}
                  >
                    {selectedTarget.update_mode === "auto_apply" ? "Auto-apply on" : "Auto-apply off"}
                  </button>
                </div>
              </div>
              <div className="mt-4 grid gap-3 md:grid-cols-4">
                <Metric label="Type" value={formatLabel(selectedTarget.target_type)} />
                <Metric label="Entry" value={selectedTarget.entry_file} />
                <Metric label="Scope" value={formatLabel(selectedTarget.scope_type)} />
                <Metric
                  label="Pending"
                  value={String(filteredProposals.filter((proposal) => proposal.status === "pending_review").length)}
                />
              </div>
              <div className="mt-4 grid gap-3 xl:grid-cols-3">
                <FileList title="Tracked Files" files={selectedTarget.files || []} />
                <ManifestPanel target={selectedTarget} />
                <PolicyPanel
                  target={selectedTarget}
                  busy={busy === `mode:${selectedTarget.target_id}`}
                  onRiskChange={(risk) => updatePolicyRisk(selectedTarget, risk)}
                />
              </div>
            </div>
          ) : (
            <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4 text-sm text-[var(--text-muted)]">
              Register a skill or instruction file to review updates.
            </div>
          )}

          <div className="grid min-w-0 gap-4">
            <ProposalList
              proposals={filteredProposals}
              selectedId={selectedProposal?.proposal_id || ""}
              hasTarget={Boolean(selectedTarget)}
              onSelect={selectProposal}
            />
            <ProposalReview
              proposal={selectedProposal}
              editText={editText}
              onEditText={setEditText}
              selectedPatchIndex={selectedPatchIndex}
              onSelectPatch={selectPatch}
              onSave={saveEditedProposal}
              onApply={(proposal) => setConfirmAction({ kind: "apply", proposal })}
              onReject={(proposal) => setConfirmAction({ kind: "reject", proposal })}
              busy={busy}
            />
          </div>
        </section>
      </div>

      {confirmAction && (
        <ConfirmDialog
          action={confirmAction}
          onCancel={() => setConfirmAction(null)}
          onConfirm={confirmPendingAction}
          busy={Boolean(busy)}
        />
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-[var(--border)] bg-black/10 p-3">
      <div className="text-[10px] uppercase text-[var(--text-muted)]">{label}</div>
      <div className="mt-1 truncate text-sm font-medium text-[var(--text)]">{value}</div>
    </div>
  );
}

function FileList({ title, files }: { title: string; files: NonNullable<SkillTarget["files"]> }) {
  return (
    <div className="rounded-md border border-[var(--border)] bg-black/10 p-3">
      <h3 className="text-xs font-semibold text-[var(--text)]">{title}</h3>
      <div className="mt-2 max-h-44 space-y-1 overflow-y-auto">
        {files.length === 0 && <div className="text-xs text-[var(--text-muted)]">No tracked files.</div>}
        {files.map((file) => (
          <div key={file.relative_path} className="flex items-center justify-between gap-2 text-xs">
            <span className="min-w-0 truncate text-[var(--text-secondary)]">{file.relative_path}</span>
            <span className="shrink-0 rounded bg-white/[0.05] px-1.5 py-0.5 text-[10px] text-[var(--text-muted)]">
              {formatLabel(file.file_role)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ManifestPanel({ target }: { target: SkillTarget }) {
  const manifest = target.manifest;
  const surfaces = [...(manifest?.allowed_update_surfaces || []), ...(manifest?.high_risk_surfaces || [])];
  return (
    <div className="rounded-md border border-[var(--border)] bg-black/10 p-3">
      <h3 className="text-xs font-semibold text-[var(--text)]">Update Surfaces</h3>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {surfaces.length === 0 && <div className="text-xs text-[var(--text-muted)]">No surfaces detected.</div>}
        {(manifest?.allowed_update_surfaces || []).map((surface) => (
          <span key={surface} className="rounded bg-emerald-400/10 px-2 py-1 text-[10px] text-emerald-200">
            {formatLabel(surface)}
          </span>
        ))}
        {(manifest?.high_risk_surfaces || []).map((surface) => (
          <span key={surface} className="rounded bg-amber-400/10 px-2 py-1 text-[10px] text-amber-200">
            {formatLabel(surface)}
          </span>
        ))}
      </div>
    </div>
  );
}

function PolicyPanel({
  target,
  busy,
  onRiskChange,
}: {
  target: SkillTarget;
  busy: boolean;
  onRiskChange: (risk: string) => void;
}) {
  const policy = target.auto_apply_policy;
  return (
    <div className="rounded-md border border-[var(--border)] bg-black/10 p-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-xs font-semibold text-[var(--text)]">Auto-apply Policy</h3>
        <span className="text-[10px] text-[var(--text-muted)]">{policy.enabled ? "Enabled" : "Disabled"}</span>
      </div>
      <label className="mt-3 grid gap-1 text-xs">
        <span className="text-[var(--text-muted)]">Max risk</span>
        <select
          value={policy.max_risk}
          disabled={busy}
          onChange={(event) => onRiskChange(event.target.value)}
          className="min-h-10 rounded-md border border-[var(--border)] bg-black/20 px-2 text-[var(--text)] outline-none disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
        >
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
        </select>
      </label>
      <div className="mt-3 grid grid-cols-3 gap-2 text-[10px] text-[var(--text-secondary)]">
        <PolicyMetric label="Files" value={String(policy.max_changed_files)} />
        <PolicyMetric label="Adds" value={String(policy.max_added_lines)} />
        <PolicyMetric label="Removes" value={String(policy.max_removed_lines ?? 20)} />
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {policy.allow_entry_file_body && <PolicyChip label="Entry body" />}
        {policy.allow_new_reference_files && <PolicyChip label="New references" />}
        {policy.allow_scripts && <PolicyChip label="Scripts" />}
        {policy.allow_assets && <PolicyChip label="Assets" />}
        {policy.allow_frontmatter && <PolicyChip label="Frontmatter" />}
      </div>
    </div>
  );
}

function PolicyMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-[var(--border)] bg-white/[0.03] px-2 py-1">
      <div className="text-[var(--text-muted)]">{label}</div>
      <div className="text-[var(--text)]">{value}</div>
    </div>
  );
}

function PolicyChip({ label }: { label: string }) {
  return <span className="rounded bg-white/[0.05] px-2 py-1 text-[10px] text-[var(--text-secondary)]">{label}</span>;
}

function ProposalList({
  proposals,
  selectedId,
  hasTarget,
  onSelect,
}: {
  proposals: SkillProposal[];
  selectedId: string;
  hasTarget: boolean;
  onSelect: (id: string) => void;
}) {
  return (
    <section className="min-w-0 overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--bg-card)]">
      <div className="border-b border-[var(--border)] px-4 py-3">
        <h2 className="text-sm font-semibold text-[var(--text)]">Update Proposals</h2>
      </div>
      <div className="max-h-[30rem] overflow-y-auto p-2" role="list">
        {proposals.length === 0 && (
          <div className="p-3 text-sm text-[var(--text-muted)]">
            {hasTarget ? "No proposals yet. Scan this skill to draft updates." : "Register a skill first."}
          </div>
        )}
        {proposals.map((proposal) => (
          <button
            key={proposal.proposal_id}
            type="button"
            aria-pressed={selectedId === proposal.proposal_id}
            onClick={() => onSelect(proposal.proposal_id)}
            className={`mb-2 min-w-0 w-full rounded-md border p-3 text-left outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)] ${
              selectedId === proposal.proposal_id
                ? "border-[var(--accent-blue)] bg-blue-500/10"
                : "border-[var(--border)] bg-white/[0.02] hover:bg-white/[0.05]"
            }`}
            role="listitem"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="line-clamp-1 text-sm font-semibold text-[var(--text)]">{proposal.title}</span>
              <span className={`rounded-full px-2 py-0.5 text-[10px] ${riskClass(proposal.risk_level)}`}>
                {formatLabel(proposal.risk_level)}
              </span>
            </div>
            <div className="mt-1 line-clamp-2 text-xs text-[var(--text-secondary)]">{proposal.summary}</div>
            <div className="mt-2 text-[10px] text-[var(--text-muted)]">{formatStatus(proposal.status)}</div>
          </button>
        ))}
      </div>
    </section>
  );
}

function ProposalReview({
  proposal,
  editText,
  onEditText,
  selectedPatchIndex,
  onSelectPatch,
  onSave,
  onApply,
  onReject,
  busy,
}: {
  proposal: SkillProposal | null;
  editText: string;
  onEditText: (value: string) => void;
  selectedPatchIndex: number;
  onSelectPatch: (index: number) => void;
  onSave: (proposal: SkillProposal) => void;
  onApply: (proposal: SkillProposal) => void;
  onReject: (proposal: SkillProposal) => void;
  busy: string | null;
}) {
  if (!proposal) {
    return (
      <section className="min-w-0 rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4 text-sm text-[var(--text-muted)]">
        Select a proposal to review.
      </section>
    );
  }
  const patches = proposal.patch_json.patches;
  const patch = patches[selectedPatchIndex] || patches[0];
  const hasUnsavedEdit = Boolean(patch && editText !== (patch.after_text || ""));
  const canEdit = proposal.status === "pending_review" || proposal.status === "failed_validation";
  const canReject = !["applied", "rejected", "superseded"].includes(proposal.status);
  const applyBlocker = applyBlockerText(proposal, hasUnsavedEdit);
  const canApply = !applyBlocker;
  const diffText =
    patch && hasUnsavedEdit
      ? previewDiff(patch.relative_path, patch.before_text || "", editText)
      : patch?.diff_text || "No diff available until the proposal is regenerated.";

  return (
    <section className="min-w-0 overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--bg-card)]">
      <div className="border-b border-[var(--border)] p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-[var(--text)]">{proposal.title}</h2>
            <p className="mt-1 text-sm text-[var(--text-secondary)]">{proposal.summary}</p>
          </div>
          <span className={`rounded-full px-2.5 py-1 text-xs ${riskClass(proposal.risk_level)}`}>
            {formatLabel(proposal.risk_level)}
          </span>
        </div>
        <div className="mt-3 flex flex-wrap gap-1.5">
          <span className="rounded bg-white/[0.05] px-2 py-1 text-[10px] text-[var(--text-secondary)]">
            {formatStatus(proposal.status)}
          </span>
          {proposal.status === "pending_review" && (
            <span className="rounded bg-amber-400/10 px-2 py-1 text-[10px] text-amber-200">
              Not applied
            </span>
          )}
          {Array.from(new Set(patches.flatMap((item) => item.evidence_record_ids || []))).map((recordId) => (
            <span key={recordId} className="rounded bg-white/[0.05] px-2 py-1 text-[10px] text-[var(--text-secondary)]">
              {recordId}
            </span>
          ))}
        </div>
        {patches.length > 1 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {patches.map((item, index) => (
              <button
                key={`${item.relative_path}:${index}`}
                type="button"
                aria-pressed={index === selectedPatchIndex}
                onClick={() => onSelectPatch(index)}
                className={`min-h-9 max-w-full rounded-md border px-2 text-xs outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)] ${
                  index === selectedPatchIndex
                    ? "border-[var(--accent-blue)] bg-blue-500/10 text-[var(--text)]"
                    : "border-[var(--border)] text-[var(--text-secondary)] hover:bg-white/[0.05]"
                }`}
              >
                <span className="block max-w-56 truncate">{item.relative_path}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      {patch ? (
        <div className="grid min-w-0 gap-0 2xl:grid-cols-2">
          <div className="min-w-0 border-b border-[var(--border)] p-4 2xl:border-b-0 2xl:border-r">
            <div className="mb-2 flex items-center justify-between gap-2">
              <label htmlFor="skill-proposal-edit" className="text-xs font-semibold text-[var(--text)]">
                Full File Preview
              </label>
              <span className="min-w-0 truncate text-[10px] text-[var(--text-muted)]">{patch.relative_path}</span>
            </div>
            <FullFilePreview
              id="skill-proposal-edit"
              value={editText}
              onChange={onEditText}
              readOnly={!canEdit}
            />
          </div>
          <div className="min-w-0 p-4">
            <div className="mb-2 flex items-center justify-between gap-2">
              <h3 className="text-xs font-semibold text-[var(--text)]">Change Diff</h3>
              {hasUnsavedEdit && <span className="text-[10px] text-amber-200">Unsaved edit</span>}
            </div>
            <DiffViewer diffText={diffText} />
          </div>
        </div>
      ) : (
        <div className="p-4 text-sm text-[var(--text-muted)]">This proposal has no patch.</div>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[var(--border)] p-4">
        <ValidationState proposal={proposal} blocker={applyBlocker} />
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => onSave(proposal)}
            disabled={!patch || !canEdit || !hasUnsavedEdit || busy === `edit:${proposal.proposal_id}`}
            className="min-h-10 rounded-md border border-[var(--border)] px-3 text-xs font-medium text-[var(--text)] outline-none hover:bg-white/[0.05] disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
          >
            Save proposal edit
          </button>
          <button
            type="button"
            onClick={() => onReject(proposal)}
            disabled={!canReject || busy === `reject:${proposal.proposal_id}`}
            className="min-h-10 rounded-md border border-red-400/30 px-3 text-xs font-medium text-red-200 outline-none hover:bg-red-400/10 disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-red-300"
          >
            Reject
          </button>
          <button
            type="button"
            onClick={() => onApply(proposal)}
            disabled={!canApply || busy === `apply:${proposal.proposal_id}`}
            title={applyBlocker || "Apply this proposal"}
            className="min-h-10 rounded-md bg-emerald-400 px-3 text-xs font-semibold text-slate-950 outline-none hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-emerald-200"
          >
            {hasUnsavedEdit ? "Save edit first" : "Apply update"}
          </button>
        </div>
      </div>
    </section>
  );
}

function ConfirmDialog({
  action,
  onCancel,
  onConfirm,
  busy,
}: {
  action: ConfirmAction;
  onCancel: () => void;
  onConfirm: () => void;
  busy: boolean;
}) {
  const title =
    action.kind === "apply" ? "Apply proposal?" : action.kind === "reject" ? "Reject proposal?" : "Enable auto-apply?";
  const body = confirmBody(action);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/90 p-4 backdrop-blur-sm" role="presentation">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="skill-confirm-title"
        className="w-full max-w-lg rounded-lg border border-[var(--border)] bg-[var(--bg-subtle)] p-4 shadow-2xl"
      >
        <h2 id="skill-confirm-title" className="text-base font-semibold text-[var(--text)]">
          {title}
        </h2>
        <div className="mt-3 space-y-2 text-sm text-[var(--text-secondary)]">
          {body.map((line) => (
            <p key={line}>{line}</p>
          ))}
        </div>
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="min-h-10 rounded-md border border-[var(--border)] px-3 text-xs font-medium text-[var(--text)] outline-none hover:bg-white/[0.05] disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className="min-h-10 rounded-md bg-[var(--accent-blue)] px-3 text-xs font-semibold text-white outline-none hover:brightness-110 disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
          >
            Confirm
          </button>
        </div>
      </div>
    </div>
  );
}

function confirmBody(action: ConfirmAction) {
  if (action.kind === "auto_apply") {
    const policy = action.target.auto_apply_policy;
    return [
      `${action.target.name} will auto-apply proposals only when validation and guard checks pass.`,
      `Policy: max risk ${formatLabel(policy.max_risk)}, ${policy.max_changed_files} files, ${policy.max_added_lines} added lines, ${policy.max_removed_lines ?? 20} removed lines.`,
    ];
  }
  const proposal = action.proposal;
  const fileCount = proposal.patch_json.patches.length;
  if (action.kind === "reject") {
    return [`${proposal.title} will be marked rejected.`, `${fileCount} proposed file change${fileCount === 1 ? "" : "s"} will stay unapplied.`];
  }
  return [
    `${proposal.title} will write ${fileCount} file change${fileCount === 1 ? "" : "s"} to disk.`,
    `Validation: ${proposal.validation_json?.ok ? "passed" : "not passed"}. Guard: ${proposal.guard_json?.accepted ? "accepted" : "not accepted"}.`,
  ];
}

function previewDiff(relativePath: string, before: string, after: string) {
  const beforeLines = splitPreviewLines(before);
  const afterLines = splitPreviewLines(after);
  return [
    `--- a/${relativePath}`,
    `+++ b/${relativePath}`,
    "@@ unsaved edit preview @@",
    ...beforeLines.map((line) => `-${line}`),
    ...afterLines.map((line) => `+${line}`),
  ].join("\n");
}

function splitPreviewLines(text: string) {
  const trimmed = text.endsWith("\n") ? text.slice(0, -1) : text;
  return trimmed ? trimmed.split("\n") : [""];
}

function FullFilePreview({
  id,
  value,
  onChange,
  readOnly,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  readOnly: boolean;
}) {
  const lineNumberRef = useRef<HTMLDivElement>(null);
  const lineNumbers = useMemo(
    () => Array.from({ length: Math.max(1, value.split("\n").length) }, (_line, index) => index + 1).join("\n"),
    [value],
  );
  const syncLineScroll = (event: UIEvent<HTMLTextAreaElement>) => {
    if (lineNumberRef.current) lineNumberRef.current.scrollTop = event.currentTarget.scrollTop;
  };

  return (
    <div className="flex h-72 w-full max-w-full overflow-hidden rounded-md border border-[var(--border)] bg-black/30 font-mono text-xs leading-5 md:h-80 xl:h-96">
      <div
        ref={lineNumberRef}
        aria-hidden="true"
        className="w-14 shrink-0 overflow-hidden border-r border-white/5 bg-black/20"
      >
        <pre className="select-none px-2 py-3 text-right text-[var(--text-muted)]">{lineNumbers}</pre>
      </div>
      <textarea
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onScroll={syncLineScroll}
        readOnly={readOnly}
        className="h-full min-w-0 flex-1 resize-none border-0 bg-transparent p-3 text-[var(--text)] outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--accent-blue)]"
      />
    </div>
  );
}

type DiffRow = {
  text: string;
  oldLine: number | null;
  newLine: number | null;
  kind: "add" | "remove" | "context" | "hunk" | "meta";
};

function DiffViewer({ diffText }: { diffText: string }) {
  return (
    <div className="h-72 w-full max-w-full overflow-auto rounded-md border border-[var(--border)] bg-black/30 font-mono text-xs leading-5 md:h-80 xl:h-96">
      <div className="min-w-max py-2">
        {parseDiffRows(diffText).map((row, index) => (
          <div
            key={`${index}:${row.kind}:${row.oldLine ?? ""}:${row.newLine ?? ""}`}
            className={`grid grid-cols-[3.5rem_3.5rem_minmax(34rem,1fr)] ${diffRowClass(row.kind)}`}
          >
            <span className="select-none border-r border-white/5 px-2 text-right text-[var(--text-muted)]">
              {row.oldLine ?? ""}
            </span>
            <span className="select-none border-r border-white/5 px-2 text-right text-[var(--text-muted)]">
              {row.newLine ?? ""}
            </span>
            <code className="whitespace-pre px-3">{row.text || " "}</code>
          </div>
        ))}
      </div>
    </div>
  );
}

function parseDiffRows(diffText: string) {
  const rows: DiffRow[] = [];
  let oldLine: number | null = null;
  let newLine: number | null = null;
  for (const line of splitPreviewLines(diffText)) {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
    if (hunk) {
      oldLine = Number(hunk[1]);
      newLine = Number(hunk[2]);
      rows.push({ text: line, oldLine: null, newLine: null, kind: "hunk" });
      continue;
    }
    if (line.startsWith("--- ") || line.startsWith("+++ ")) {
      rows.push({ text: line, oldLine: null, newLine: null, kind: "meta" });
      continue;
    }
    if (line.startsWith("@@ ")) {
      oldLine = 1;
      newLine = 1;
      rows.push({ text: line, oldLine: null, newLine: null, kind: "hunk" });
      continue;
    }
    if (oldLine === null || newLine === null) {
      rows.push({ text: line, oldLine: null, newLine: null, kind: "meta" });
      continue;
    }
    if (line.startsWith("+")) {
      rows.push({ text: line, oldLine: null, newLine, kind: "add" });
      newLine += 1;
      continue;
    }
    if (line.startsWith("-")) {
      rows.push({ text: line, oldLine, newLine: null, kind: "remove" });
      oldLine += 1;
      continue;
    }
    rows.push({ text: line.startsWith(" ") ? line.slice(1) : line, oldLine, newLine, kind: "context" });
    oldLine += 1;
    newLine += 1;
  }
  return rows;
}

function diffRowClass(kind: DiffRow["kind"]) {
  if (kind === "add") return "bg-emerald-400/10 text-emerald-100";
  if (kind === "remove") return "bg-red-400/10 text-red-100";
  if (kind === "hunk") return "bg-blue-400/10 text-blue-200";
  if (kind === "meta") return "text-[var(--text-muted)]";
  return "text-[var(--text-secondary)]";
}

function ValidationState({ proposal, blocker }: { proposal: SkillProposal; blocker: string | null }) {
  const ok = proposal.validation_json?.ok;
  const errors = proposal.validation_json?.errors || [];
  const reasons = proposal.guard_json?.reasons || [];
  return (
    <div className="max-w-full text-xs">
      <div className={blocker ? "text-amber-200" : "text-emerald-300"}>{blocker || "Ready for approval"}</div>
      {ok === false && errors[0] && <div className="mt-1 text-[var(--text-muted)]">{errors[0]}</div>}
      {ok !== false && reasons[0] && <div className="mt-1 text-[var(--text-muted)]">{reasons[0]}</div>}
    </div>
  );
}

function applyBlockerText(proposal: SkillProposal, hasUnsavedEdit: boolean) {
  if (proposal.status === "applied") return "Already applied";
  if (proposal.status === "rejected") return "Rejected";
  if (proposal.status === "superseded") return "Superseded";
  if (proposal.status !== "pending_review") return `Status is ${formatStatus(proposal.status)}`;
  if (hasUnsavedEdit) return "Save edits before applying";
  if (proposal.validation_json?.ok !== true) return "Validation has not passed";
  if (proposal.guard_json?.accepted !== true) return "Guard has not accepted this proposal";
  return null;
}

function formatStatus(status: string) {
  const labels: Record<string, string> = {
    approved: "Approved",
    applied: "Applied",
    draft: "Draft",
    failed_validation: "Failed validation",
    pending_review: "Pending review",
    rejected: "Rejected",
    superseded: "Superseded",
  };
  return labels[status] || formatLabel(status);
}

function formatMode(mode: string) {
  if (mode === "auto_apply") return "Auto-apply";
  return formatLabel(mode);
}

function formatLabel(value: string) {
  const words = String(value || "")
    .replace(/[_-]+/g, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  return words.map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(" ") || "Unknown";
}

function riskClass(risk: string) {
  if (risk === "high") return "bg-red-400/10 text-red-200";
  if (risk === "medium") return "bg-amber-400/10 text-amber-200";
  return "bg-emerald-400/10 text-emerald-200";
}
