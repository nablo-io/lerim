"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { api } from "@/lib/api";
import { useProjectScope } from "@/lib/projectScope";
import type {
	MemoryArtifact,
	MemoryArtifactType,
	MemoryArtifactVersion,
	MemoryArtifactsResponse,
} from "@/lib/types";
import ProjectScope from "@/components/ProjectScope";

const PANEL_SECTIONS: Record<MemoryArtifactType, string[]> = {
	context_brief: ["Summary", "Continuation Handoff", "Decisions", "Constraints & Preferences", "Project Facts"],
	working_memory: ["Current State", "Completed Recently", "Changed Context", "If Continuing This Work"],
};

const PANEL_COPY: Record<MemoryArtifactType, { title: string; eyebrow: string; description: string }> = {
	context_brief: {
		title: "Project Brief",
		eyebrow: "Context brief · long-term",
		description: "Stable decisions, constraints, preferences, and project facts.",
	},
	working_memory: {
		title: "Working Memory",
		eyebrow: "Recent continuation",
		description: "What changed recently and where to resume only if the next prompt continues this work.",
	},
};

export default function MemoryPage() {
	return (
		<Suspense fallback={<div className="text-sm text-[var(--text-muted)]">Loading…</div>}>
			<MemoryContent />
		</Suspense>
	);
}

function MemoryContent() {
	const [data, setData] = useState<MemoryArtifactsResponse | null>(null);
	const { project, setProject } = useProjectScope();
	const [activeVersions, setActiveVersions] = useState<Partial<Record<MemoryArtifactType, string>>>({});
		const [compareLatest, setCompareLatest] = useState(false);
		const [loading, setLoading] = useState(true);
		const [error, setError] = useState<string | null>(null);
		const loadSeqRef = useRef(0);

		const load = useCallback(async (selectedProject?: string) => {
			const seq = loadSeqRef.current + 1;
			loadSeqRef.current = seq;
			setLoading(true);
			setError(null);
			try {
				const payload = await api.getMemoryArtifacts(selectedProject || project || undefined);
				if (seq !== loadSeqRef.current) return;
				setData(payload);
				if (!project && payload.selected_project) setProject(payload.selected_project);
				setActiveVersions({});
			} catch (err) {
				if (seq === loadSeqRef.current) {
					setData(null);
					setActiveVersions({});
					setError(err instanceof Error ? err.message : "Failed to load memory artifacts");
				}
			} finally {
				if (seq === loadSeqRef.current) setLoading(false);
			}
		}, [project, setProject]);

	useEffect(() => {
		load();
	}, [load]);

	const contextArtifact = data?.artifacts.context_brief;
	const workingArtifact = data?.artifacts.working_memory;
	const activeContext = selectedVersion(contextArtifact, activeVersions.context_brief);
	const activeWorking = selectedVersion(workingArtifact, activeVersions.working_memory);
	const latestGenerated = latestTimestamp([contextArtifact, workingArtifact]);
	const staleCount = [contextArtifact, workingArtifact].filter((artifact) => artifact?.status.availability === "stale").length;

	return (
		<>
			<div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
				<div>
					<h1 className="text-lg font-semibold text-[var(--text)]">Briefs</h1>
					<p className="mt-0.5 text-xs text-[var(--text-muted)]">
						Latest agent-ready project brief and working memory for the selected project
					</p>
				</div>
				<div className="flex flex-wrap items-center gap-2">
					<ProjectScope value={project} onChange={setProject} includeAll={false} label="Project" />
					<button
						type="button"
						onClick={() => load(project || undefined)}
						disabled={loading}
						className="min-h-10 rounded-md border border-[var(--border)] px-3 text-xs font-medium text-[var(--text-secondary)] transition-colors hover:text-[var(--text)] disabled:cursor-not-allowed disabled:opacity-40"
					>
						{loading ? "Refreshing..." : "Refresh"}
					</button>
				</div>
			</div>

			{error && (
				<div className="mt-4 rounded-lg border border-red-500/20 bg-red-500/5 px-4 py-3 text-sm text-red-400">
					{error}
				</div>
			)}

			<section className="mt-4 rounded-lg border border-[var(--border)] bg-[var(--bg-card)] px-4 py-3">
				<div className="grid gap-3 text-xs text-[var(--text-secondary)] md:grid-cols-[minmax(0,1fr)_auto_auto] md:items-center">
					<div className="min-w-0">
						<div className="flex flex-wrap items-center gap-2">
							<span className="font-medium text-[var(--text)]">{data?.selected_project || "Project"}</span>
							{data?.project_id && (
								<span className="rounded-md bg-white/[0.04] px-2 py-1 font-mono text-[10px] text-[var(--text-muted)]">
									{data.project_id}
								</span>
							)}
						</div>
						<p className="mt-1 truncate text-[var(--text-muted)]">{data?.repo_path || "Loading project memory..."}</p>
					</div>
					<StatusChip label="Latest generated" value={formatDateTime(latestGenerated)} />
					<StatusChip
						label="Freshness"
						value={staleCount ? `${staleCount} stale artifact${staleCount === 1 ? "" : "s"}` : "Both current"}
						tone={staleCount ? "warn" : "good"}
					/>
				</div>
			</section>

			{loading && !data ? (
				<div className="mt-8 grid gap-3 lg:grid-cols-2">
					<div className="h-96 animate-pulse rounded-lg border border-[var(--border)] bg-white/[0.03]" />
					<div className="h-96 animate-pulse rounded-lg border border-[var(--border)] bg-white/[0.03]" />
				</div>
			) : data ? (
				<div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_16rem] 2xl:grid-cols-[minmax(0,1fr)_18rem]">
					<div className="min-w-0 space-y-4">
						<div className="grid gap-4 lg:grid-cols-2">
							<MemoryPanel
								artifact={contextArtifact}
								version={activeContext}
								compareLatest={compareLatest}
								onRefresh={() => load(project)}
								onHistory={() => setCompareLatest((value) => !value)}
							/>
							<MemoryPanel
								artifact={workingArtifact}
								version={activeWorking}
								compareLatest={compareLatest}
								onRefresh={() => load(project)}
								onHistory={() => setCompareLatest((value) => !value)}
							/>
						</div>
						<TriggerFlow />
					</div>
					<VersionRail
						versions={data.versions}
						activeVersions={activeVersions}
						compareLatest={compareLatest}
						onToggleCompare={() => setCompareLatest((value) => !value)}
						onSelect={(version) => {
							setActiveVersions((current) => ({ ...current, [version.type]: version.id }));
						}}
					/>
				</div>
			) : null}
		</>
	);
}

function MemoryPanel({
	artifact,
	version,
	compareLatest,
	onRefresh,
	onHistory,
}: {
	artifact?: MemoryArtifact;
	version?: MemoryArtifactVersion;
	compareLatest: boolean;
	onRefresh: () => void;
	onHistory: () => void;
}) {
	const type = artifact?.type || "context_brief";
	const copy = PANEL_COPY[type];
	const sections = useMemo(() => parseMemorySections(version?.content || "", type), [version?.content, type]);
	const sourceCount = version?.included_record_ids.length || 0;

	return (
		<section className="min-w-0 rounded-lg border border-[var(--border)] bg-[var(--bg-card)]">
			<div className="border-b border-[var(--border)] p-4">
				<div className="flex flex-wrap items-start justify-between gap-3">
					<div className="min-w-0">
						<div className="flex flex-wrap items-center gap-2">
							<span className={`rounded-md px-2 py-1 text-[10px] font-semibold uppercase tracking-wide ${type === "context_brief" ? "bg-[var(--accent-blue)]/15 text-[var(--accent-blue)]" : "bg-[var(--accent-teal)]/15 text-[var(--accent-teal)]"}`}>
								{copy.eyebrow}
							</span>
							<span className="rounded-md border border-[var(--border)] px-2 py-1 text-[10px] font-medium uppercase tracking-wide text-[var(--text-secondary)]">
								{version?.current ? "Latest" : "Selected version"}
							</span>
						</div>
						<h2 className="mt-3 text-base font-semibold text-[var(--text)]">{copy.title}</h2>
						<p className="mt-1 text-xs leading-5 text-[var(--text-muted)]">{copy.description}</p>
					</div>
					<FreshnessBadge availability={artifact?.status.availability} />
				</div>
				<div className="mt-4 grid gap-2 text-xs text-[var(--text-secondary)] sm:grid-cols-2">
					<MetaLine label="Generated" value={formatDateTime(version?.generated_at)} />
					<MetaLine label="Trigger" value={version?.trigger || "unknown"} />
					<MetaLine label={type === "working_memory" ? "Recent versions" : "Records cited"} value={String(type === "working_memory" ? version?.recent_versions_considered || 0 : version?.records_included || 0)} />
					<MetaLine label="Sources" value={`${sourceCount} record${sourceCount === 1 ? "" : "s"}`} />
				</div>
				<div className="mt-4 flex flex-wrap gap-2">
					<ActionButton onClick={onRefresh}>Refresh</ActionButton>
					<ActionButton onClick={() => scrollToSources(type)}>View Sources</ActionButton>
					<ActionButton onClick={onHistory}>{compareLatest ? "Comparing" : "Version History"}</ActionButton>
				</div>
			</div>
			<div className="grid gap-3 p-4">
				{PANEL_SECTIONS[type].map((sectionTitle) => (
					<SectionBox
						key={sectionTitle}
						title={sectionTitle}
						lines={sections[sectionTitle] || fallbackSectionLines(type, sectionTitle, artifact)}
					/>
				))}
				<SectionBox
					id={`sources-${type}`}
					title="Sources"
					lines={sourceLines(version)}
					compact
				/>
			</div>
		</section>
	);
}

function SectionBox({
	id,
	title,
	lines,
	compact,
}: {
	id?: string;
	title: string;
	lines: string[];
	compact?: boolean;
}) {
	return (
		<div id={id} className="rounded-lg border border-[var(--border)] bg-[#0b1220]/80 p-3">
			<div className="mb-2 flex items-center justify-between gap-2">
				<h3 className="text-xs font-semibold text-[var(--text-secondary)]">{title}</h3>
				<span className="text-[10px] tabular-nums text-[var(--text-muted)]">{lines.length}</span>
			</div>
			{lines.length ? (
				<ul className={`space-y-2 ${compact ? "max-h-36 overflow-auto pr-1" : ""}`}>
					{lines.slice(0, compact ? 20 : 5).map((line, index) => (
						<li key={`${title}-${index}`} className="text-xs leading-5 text-[var(--text-secondary)]">
							<span className="mr-2 text-[var(--text-muted)]">-</span>
							{line}
						</li>
					))}
				</ul>
			) : (
				<p className="text-xs leading-5 text-[var(--text-muted)]">No generated lines in this section yet.</p>
			)}
		</div>
	);
}

function TriggerFlow() {
	return (
		<section className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4">
			<div className="flex items-center justify-between gap-3">
				<div>
					<h2 className="text-sm font-semibold text-[var(--text)]">Automatic Triggers</h2>
					<p className="mt-1 text-xs text-[var(--text-muted)]">How generated memory moves without adding another store</p>
				</div>
			</div>
			<div className="mt-4 space-y-3">
				<div className="grid gap-3 xl:grid-cols-[1fr_auto_1.05fr_auto_1fr] xl:items-center">
					<div className="grid gap-2">
						<FlowCard label="Curate changed records" detail="records_created / updated / archived" />
						<FlowCard label="Daily daemon" detail="skips fresh artifacts" />
					</div>
					<FlowArrow />
					<FlowCard label="Both briefs refresh" detail="Project Brief + Working Memory" highlighted />
					<FlowArrow />
					<div className="grid gap-2">
						<FlowCard label="Project Brief" detail="context brief · long-term memory" />
						<FlowCard label="Working Memory" detail="short-term recent memory" />
					</div>
				</div>
				<div className="grid gap-3 xl:grid-cols-[1fr_auto_1.05fr_auto_1fr] xl:items-center">
					<FlowCard label="Ingest" detail="new source evidence lands first" />
					<FlowArrow subtle />
					<FlowCard label="Database only" detail="no direct artifact refresh" />
					<div className="hidden xl:block" />
					<div className="hidden xl:block" />
				</div>
			</div>
		</section>
	);
}

function FlowCard({ label, detail, highlighted }: { label: string; detail: string; highlighted?: boolean }) {
	return (
		<div className={`rounded-lg border p-3 ${highlighted ? "border-[var(--accent-blue)]/25 bg-[var(--accent-blue)]/10" : "border-[var(--border)] bg-[#0b1220]/80"}`}>
			<p className="text-xs font-medium text-[var(--text)]">{label}</p>
			<p className="mt-1 text-[11px] leading-4 text-[var(--text-muted)]">{detail}</p>
		</div>
	);
}

function FlowArrow({ subtle }: { subtle?: boolean }) {
	return (
		<div className={`flex items-center justify-center text-sm ${subtle ? "text-[var(--text-muted)]/50" : "text-[var(--text-muted)]"}`} aria-hidden="true">
			<span className="hidden xl:inline">→</span>
			<span className="xl:hidden">↓</span>
		</div>
	);
}

function VersionRail({
	versions,
	activeVersions,
	compareLatest,
	onToggleCompare,
	onSelect,
}: {
	versions: MemoryArtifactVersion[];
	activeVersions: Partial<Record<MemoryArtifactType, string>>;
	compareLatest: boolean;
	onToggleCompare: () => void;
	onSelect: (version: MemoryArtifactVersion) => void;
}) {
	return (
		<aside className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] lg:sticky lg:top-8 lg:self-start">
			<div className="border-b border-[var(--border)] p-4">
				<div className="flex items-start justify-between gap-3">
					<div>
						<h2 className="text-sm font-semibold text-[var(--text)]">Version History</h2>
						<p className="mt-1 text-xs text-[var(--text-muted)]">Select prior generated artifacts</p>
					</div>
					<button
						type="button"
						onClick={onToggleCompare}
						className={`rounded-md border px-2 py-1 text-[10px] font-medium uppercase tracking-wide transition-colors ${compareLatest ? "border-[var(--accent-blue)]/40 bg-[var(--accent-blue)]/10 text-[var(--accent-blue)]" : "border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text-secondary)]"}`}
					>
						Compare
					</button>
				</div>
			</div>
			<div className="max-h-[46rem] space-y-2 overflow-auto p-3">
				{versions.length ? versions.map((version) => {
					const active = activeVersions[version.type] === version.id || (!activeVersions[version.type] && version.current);
					return (
						<button
							key={`${version.type}-${version.id}-${version.content_path}`}
							type="button"
							onClick={() => onSelect(version)}
							className={`w-full rounded-lg border p-3 text-left transition-colors ${active ? "border-[var(--accent-blue)]/40 bg-[var(--accent-blue)]/10" : "border-[var(--border)] bg-[#0b1220]/70 hover:border-white/15"}`}
						>
							<div className="flex items-center justify-between gap-2">
								<span className={`rounded-md px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${version.type === "context_brief" ? "bg-[var(--accent-blue)]/15 text-[var(--accent-blue)]" : "bg-[var(--accent-teal)]/15 text-[var(--accent-teal)]"}`}>
									{version.type === "context_brief" ? "Brief" : "Working"}
								</span>
								{version.current && <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">Latest</span>}
							</div>
							<p className="mt-2 text-xs font-medium text-[var(--text)]">{formatDateTime(version.generated_at)}</p>
							<p className="mt-1 text-[11px] text-[var(--text-muted)]">{version.trigger || "unknown"} · {version.records_included} records</p>
						</button>
					);
				}) : (
					<p className="p-3 text-xs leading-5 text-[var(--text-muted)]">No generated versions found yet.</p>
				)}
			</div>
		</aside>
	);
}

function StatusChip({ label, value, tone }: { label: string; value: string; tone?: "good" | "warn" }) {
	return (
		<div className="rounded-md border border-[var(--border)] bg-[#0b1220]/70 px-3 py-2">
			<div className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">{label}</div>
			<div className={`mt-1 text-xs font-medium ${tone === "good" ? "text-[var(--accent-teal)]" : tone === "warn" ? "text-amber-300" : "text-[var(--text-secondary)]"}`}>
				{value}
			</div>
		</div>
	);
}

function MetaLine({ label, value }: { label: string; value: string }) {
	return (
		<div className="rounded-md bg-white/[0.025] px-3 py-2">
			<div className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">{label}</div>
			<div className="mt-1 truncate text-xs font-medium text-[var(--text-secondary)]">{value}</div>
		</div>
	);
}

function FreshnessBadge({ availability }: { availability?: string }) {
	const current = availability === "available";
	return (
		<span className={`inline-flex min-h-7 items-center rounded-md px-2.5 text-[11px] font-medium ${current ? "bg-[var(--accent-teal)]/15 text-[var(--accent-teal)]" : "bg-amber-400/10 text-amber-300"}`}>
			{availability || "missing"}
		</span>
	);
}

function ActionButton({ children, onClick }: { children: ReactNode; onClick: () => void }) {
	return (
		<button
			type="button"
			onClick={onClick}
			className="min-h-9 rounded-md border border-[var(--border)] px-3 text-xs font-medium text-[var(--text-secondary)] transition-colors hover:bg-white/[0.04] hover:text-[var(--text)]"
		>
			{children}
		</button>
	);
}

function selectedVersion(artifact?: MemoryArtifact, versionId?: string) {
	if (!artifact) return undefined;
	return artifact.versions.find((version) => version.id === versionId) || artifact.current;
}

function latestTimestamp(artifacts: Array<MemoryArtifact | undefined>) {
	const timestamps = artifacts
		.map((artifact) => artifact?.current.generated_at || "")
		.filter(Boolean)
		.sort()
		.reverse();
	return timestamps[0] || "";
}

function parseMemorySections(content: string, type: MemoryArtifactType) {
	const result: Record<string, string[]> = {};
	const matches = Array.from(content.matchAll(/^##\s+(.+)$/gm));
	for (let index = 0; index < matches.length; index += 1) {
		const match = matches[index];
		const title = match[1]?.trim() || "";
		const start = (match.index || 0) + match[0].length;
		const end = matches[index + 1]?.index ?? content.length;
		const body = content.slice(start, end);
		result[title] = body
			.split("\n")
			.map((line) => line.trim())
			.filter((line) => line.startsWith("- "))
			.map((line) => line.replace(/^-+\s*/, "").trim())
			.filter(Boolean);
	}
	return result;
}

function fallbackSectionLines(type: MemoryArtifactType, title: string, artifact?: MemoryArtifact) {
	if (!artifact?.current.content) {
		return [`No ${PANEL_COPY[type].title} artifact has been generated yet.`];
	}
	return [];
}

function sourceLines(version?: MemoryArtifactVersion) {
	if (!version?.included_record_ids.length) return [];
	return version.included_record_ids;
}

function formatDateTime(value?: string) {
	if (!value) return "none";
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return value;
	return new Intl.DateTimeFormat(undefined, {
		month: "short",
		day: "numeric",
		hour: "2-digit",
		minute: "2-digit",
	}).format(date);
}

function scrollToSources(type: MemoryArtifactType) {
	document.getElementById(`sources-${type}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
}
