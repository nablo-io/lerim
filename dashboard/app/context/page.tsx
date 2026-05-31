"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { api } from "@/lib/api";
import { formatRecordKind, formatRecordRole, formatScopeLabel } from "@/lib/labels";
import type { ContextRecord, IntelligenceResponse } from "@/lib/types";
import RecordEditor from "@/components/RecordEditor";

export default function ContextPage() {
	/* ---- filter state ---- */
	const [searchQuery, setSearchQuery] = useState("");
	const [debouncedQuery, setDebouncedQuery] = useState("");
	const [project, setProject] = useState("");
	const [selectedType, setSelectedType] = useState("");
	const [selectedRole, setSelectedRole] = useState("");
	const [statusFilter, setStatusFilter] = useState("active");

	/* ---- filter options from server ---- */
	const [filterTypes, setFilterTypes] = useState<string[]>([]);
	const [filterRoles, setFilterRoles] = useState<string[]>([]);
	const [filterProjects, setFilterProjects] = useState<string[]>([]);

	/* ---- data state ---- */
	const [records, setRecords] = useState<ContextRecord[]>([]);
	const [total, setTotal] = useState(0);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	/* ---- editor state ---- */
	const [selected, setSelected] = useState<ContextRecord | null>(null);

	/* ---- intelligence state ---- */
	const [intel, setIntel] = useState<IntelligenceResponse | null>(null);
	const [intelLoading, setIntelLoading] = useState(true);
	const [intelCollapsed, setIntelCollapsed] = useState(false);

	/* ---- refs for scrolling ---- */
	const intelSectionRef = useRef<HTMLDivElement>(null);

	/* ---- debounce search ---- */
	const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
	useEffect(() => {
		if (debounceRef.current) clearTimeout(debounceRef.current);
		debounceRef.current = setTimeout(() => {
			setDebouncedQuery(searchQuery);
		}, 300);
		return () => {
			if (debounceRef.current) clearTimeout(debounceRef.current);
		};
	}, [searchQuery]);

	/* ---- load filter options ---- */
	useEffect(() => {
		api
			.getRecordFilters()
			.then((f) => {
				setFilterTypes(f.types);
				setFilterRoles(f.roles);
				setFilterProjects(f.projects);
			})
			.catch(() => {
				/* silent -- filters just won't populate */
			});
	}, []);

	/* ---- load records ---- */
	const load = useCallback(async () => {
		setLoading(true);
		setError(null);
		try {
			const params: Record<string, string> = { limit: "200" };
			if (debouncedQuery) params.q = debouncedQuery;
			if (project) params.project = project;
			if (selectedType) params.record_kind = selectedType;
			if (selectedRole) params.record_role = selectedRole;
			if (statusFilter) params.status = statusFilter;
			const data = await api.getRecords(params);
			setRecords(data.records);
			setTotal(data.total);
		} catch (err) {
			setError(err instanceof Error ? err.message : "Failed to load records");
		} finally {
			setLoading(false);
		}
	}, [debouncedQuery, project, selectedRole, selectedType, statusFilter]);

	useEffect(() => {
		load();
	}, [load]);

	/* ---- load intelligence ---- */
	useEffect(() => {
		setIntelLoading(true);
		api
			.getIntelligence(10)
			.then((result) => setIntel(result))
			.catch(() => {
				/* silent -- banner just won't show */
			})
			.finally(() => setIntelLoading(false));
	}, []);

	const handleSelect = (record: ContextRecord) => {
		setSelected(record);
	};

	const operationalFilterRoles = filterRoles.filter((role) => role !== "general");

	const scrollToIntel = () => {
		setIntelCollapsed(false);
		setTimeout(() => {
			intelSectionRef.current?.scrollIntoView({ behavior: "smooth" });
		}, 50);
	};

	return (
		<>
			{/* ---- Health Banner ---- */}
			{intelLoading && (
				<div className="h-16 rounded-xl bg-white/[0.04] animate-pulse" />
			)}
			{!intelLoading && intel && <HealthBanner intel={intel} onBadgeClick={scrollToIntel} />}

			{/* ---- Header ---- */}
			<div className="flex flex-wrap items-center justify-between gap-3 mt-4">
					<div>
						<h1 className="text-lg font-semibold text-[var(--text)]">
							Records
						</h1>
					{!loading && (
						<p className="mt-0.5 text-xs text-[var(--text-muted)]">
							{total} record{total !== 1 ? "s" : ""}
						</p>
					)}
				</div>

				{/* Filters */}
				<div className="flex flex-wrap items-center gap-3">
					<select
						aria-label="Record status"
						value={statusFilter}
						onChange={(e) => setStatusFilter(e.target.value)}
						className="min-h-9 rounded-md border border-[var(--border)] bg-[var(--bg-card)] px-2.5 py-1.5 text-xs text-[var(--text)] outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
					>
						<option value="active">Active</option>
						<option value="archived">Archived</option>
						<option value="">All statuses</option>
					</select>

					<select
						aria-label="Project"
						value={project}
						onChange={(e) => setProject(e.target.value)}
						className="min-h-9 rounded-md border border-[var(--border)] bg-[var(--bg-card)] px-2.5 py-1.5 text-xs text-[var(--text)] outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
					>
						<option value="">All projects</option>
						{filterProjects.map((p) => (
							<option key={p} value={p}>
								{formatScopeLabel(p)}
							</option>
						))}
					</select>
				</div>
			</div>

			{/* ---- Search ---- */}
					<div className="mt-4">
						<div className="relative">
							<SearchIcon className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--text-muted)]" />
							<input
								aria-label="Search records"
								type="text"
								value={searchQuery}
								onChange={(e) => setSearchQuery(e.target.value)}
								placeholder="Search records…"
								className="w-full rounded-md border border-[var(--border)] bg-[var(--bg-card)] py-2 pl-9 pr-3 text-sm text-[var(--text)] outline-none transition-colors placeholder:text-[var(--text-muted)] focus:border-[var(--accent-blue)] focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
							/>
						</div>
					</div>

					{/* ---- Type pill buttons ---- */}
					<div className="mt-3 flex flex-wrap gap-1.5">
						<button
							onClick={() => setSelectedType("")}
							className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
								selectedType === ""
									? "bg-[var(--accent-blue)] text-white"
									: "bg-white/[0.04] text-[var(--text-muted)] hover:bg-white/[0.08] hover:text-[var(--text-secondary)]"
							}`}
						>
							All types
						</button>
						{filterTypes.map((t) => (
							<button
								key={t}
								onClick={() => setSelectedType(selectedType === t ? "" : t)}
								className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
									selectedType === t
										? "bg-[var(--accent-blue)] text-white"
										: "bg-white/[0.04] text-[var(--text-muted)] hover:bg-white/[0.08] hover:text-[var(--text-secondary)]"
								}`}
							>
								{formatRecordKind(t)}
							</button>
						))}
					</div>

					{operationalFilterRoles.length > 0 && (
						<div className="mt-2 flex flex-wrap gap-1.5">
							<button
								onClick={() => setSelectedRole("")}
								className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
									selectedRole === ""
										? "bg-teal-400 text-slate-950"
										: "bg-white/[0.04] text-[var(--text-muted)] hover:bg-white/[0.08] hover:text-[var(--text-secondary)]"
								}`}
							>
								All roles
							</button>
							{operationalFilterRoles.map((role) => (
								<button
									key={role}
									onClick={() => setSelectedRole(selectedRole === role ? "" : role)}
									className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
										selectedRole === role
											? "bg-teal-400 text-slate-950"
											: "bg-white/[0.04] text-[var(--text-muted)] hover:bg-white/[0.08] hover:text-[var(--text-secondary)]"
									}`}
								>
									{formatRecordRole(role)}
								</button>
							))}
						</div>
					)}

					{/* ---- Error ---- */}
					{error && (
						<div className="mt-4 rounded-lg border border-red-500/20 bg-red-500/5 px-4 py-3 text-sm text-red-400">
							{error}
						</div>
					)}

					{/* ---- Two-column layout ---- */}
					<div className="mt-4 flex min-h-[560px] flex-col gap-4 lg:h-[calc(100vh-260px)] lg:flex-row">
						{/* Left: record list (40%) */}
						<div className="max-h-[520px] shrink-0 overflow-y-auto rounded-lg border border-[var(--border)] lg:max-h-none lg:w-[40%]">
							{loading ? (
								<div className="flex h-32 items-center justify-center text-sm text-[var(--text-muted)]">
									Loading…
								</div>
							) : records.length === 0 ? (
								<div className="flex h-32 items-center justify-center text-sm text-[var(--text-muted)]">
									No records found.
								</div>
							) : (
								<div className="divide-y divide-[var(--border)]">
									{records.map((record) => {
										const isSelected = selected?.record_id === record.record_id;
										return (
											<div
												key={record.record_id}
												className={`flex w-full items-start gap-2 border-l-2 px-4 py-3 text-left transition-colors ${
													isSelected
														? "border-l-[var(--accent-blue)] bg-[var(--accent-blue)]/10"
														: "border-l-transparent hover:bg-white/[0.02]"
												}`}
											>
												<button
													type="button"
													onClick={() => handleSelect(record)}
													className="min-w-0 flex-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
												>
													<h3 className="truncate text-sm font-medium text-[var(--text)]">
														{record.title || "(untitled)"}
													</h3>
													<p className="mt-0.5 line-clamp-1 text-xs text-[var(--text-muted)]">
														{record.body || "No content"}
													</p>
													<div className="mt-1.5 flex items-center gap-2 text-[11px] text-[var(--text-muted)]">
														<span className="rounded bg-white/[0.06] px-1.5 py-0.5">
															{formatRecordKind(record.record_kind)}
														</span>
														{record.record_role && record.record_role !== "general" && (
															<span className="rounded bg-teal-400/10 px-1.5 py-0.5 text-teal-200">
																{formatRecordRole(record.record_role)}
															</span>
														)}
														{record.project && (
															<span className="rounded bg-white/[0.06] px-1.5 py-0.5">
																{formatScopeLabel(record.project)}
															</span>
														)}
														{record.confidence != null && (
															<span>{Math.round(record.confidence * 100)}%</span>
														)}
													</div>
												</button>
												<div className="flex shrink-0 items-center gap-1">
													<StatusDot status={record.status} />
												</div>
											</div>
										);
									})}
								</div>
							)}
						</div>

						{/* Right: editor (60%) */}
						<div className="flex-1 min-w-0 overflow-y-auto">
							{selected ? (
								<RecordEditor
									key={selected.record_id}
									record={selected}
								/>
							) : (
								<div className="flex h-full items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--bg-subtle)]">
									<div className="text-center">
										<div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-white/[0.04]">
											<BrainIcon className="h-6 w-6 text-[var(--text-muted)]" />
										</div>
										<p className="text-sm text-[var(--text-muted)]">
											Select a record to view
										</p>
									</div>
								</div>
							)}
						</div>
					</div>

					{/* ---- Intelligence Section ---- */}
					<div ref={intelSectionRef} className="mt-8">
						<IntelligenceSection
							intel={intel}
							loading={intelLoading}
							collapsed={intelCollapsed}
							onToggle={() => setIntelCollapsed((c) => !c)}
						/>
					</div>
			</>
		);
	}

/* ==================================================================
   Health Banner
   ================================================================== */

function HealthBanner({
	intel,
	onBadgeClick,
}: {
	intel: IntelligenceResponse;
	onBadgeClick: () => void;
}) {
	const score = intel.health_score;
	const scoreColor =
		score >= 70
			? "text-green-400"
			: score >= 40
				? "text-yellow-400"
				: "text-red-400";
	const scoreBorderColor =
		score >= 70
			? "border-green-500/30"
			: score >= 40
				? "border-yellow-500/30"
				: "border-red-500/30";
	const scoreBgColor =
		score >= 70
			? "bg-green-500/5"
			: score >= 40
				? "bg-yellow-500/5"
				: "bg-red-500/5";

	const contradictions = intel.contradictions.length;
	const signals = intel.signals.length;

	return (
		<div
			className={`rounded-lg border ${scoreBorderColor} ${scoreBgColor} px-5 py-3`}
		>
			<div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
				{/* Top row: health score + stats */}
				<div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
					<span className="flex items-center gap-1.5">
						<span className="text-[var(--text-secondary)]">Health:</span>
						<span className={`font-semibold ${scoreColor}`}>
							{score}/100
						</span>
					</span>
					<Separator />
					<span className="text-[var(--text-secondary)]">
						{intel.record_stats.active} active
					</span>
					<Separator />
					<span className="text-[var(--text-secondary)]">
						{intel.record_stats.archived} archived
					</span>
					<Separator />
					<span className="text-[var(--text-secondary)]">
						avg: {intel.record_stats.avg_confidence.toFixed(2)}
					</span>
				</div>

				{/* Bottom row: badges + details link */}
				<div className="flex flex-wrap items-center gap-3">
					{contradictions > 0 && (
						<button
							onClick={onBadgeClick}
							className="rounded-full bg-red-500/10 px-2.5 py-0.5 text-xs font-medium text-red-400 hover:bg-red-500/20 transition-colors"
						>
							{contradictions} contradiction{contradictions !== 1 ? "s" : ""}
						</button>
					)}
					{signals > 0 && (
						<button
							onClick={onBadgeClick}
							className="rounded-full bg-blue-500/10 px-2.5 py-0.5 text-xs font-medium text-blue-400 hover:bg-blue-500/20 transition-colors"
						>
							{signals} weak signal{signals !== 1 ? "s" : ""}
						</button>
					)}
					<button
						onClick={onBadgeClick}
						className="text-xs text-[var(--accent-blue)] hover:underline"
					>
						View details
					</button>
				</div>
			</div>
		</div>
	);
}

function Separator() {
	return (
		<span className="text-[var(--text-muted)]" aria-hidden>
			&middot;
		</span>
	);
}

/* ==================================================================
   Intelligence Section (collapsible, shown below record list)
   ================================================================== */

function IntelligenceSection({
	intel,
	loading,
	collapsed,
	onToggle,
}: {
	intel: IntelligenceResponse | null;
	loading: boolean;
	collapsed: boolean;
	onToggle: () => void;
}) {
	if (loading) {
		return (
			<div className="flex h-20 items-center justify-center text-sm text-[var(--text-muted)]">
				Loading record health…
			</div>
		);
	}

	if (!intel) return null;

	return (
		<div className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)]">
			{/* Section header with collapse toggle */}
			<button
				onClick={onToggle}
				className="flex w-full items-center justify-between px-5 py-3 text-left"
			>
				<div className="flex items-center gap-3">
					<div className="h-px w-6 bg-[var(--border)]" />
					<h2 className="text-sm font-semibold text-[var(--text)]">
						Record Health
					</h2>
					<div className="h-px flex-1 bg-[var(--border)]" />
				</div>
				<span className="text-xs text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors">
					{collapsed ? "Expand" : "Collapse"} {collapsed ? "\u25B8" : "\u25BE"}
				</span>
			</button>

			{!collapsed && (
				<div className="px-5 pb-5 space-y-5">
					{/* 2-column grid for analysis panels */}
					<div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
						{/* Contradictions */}
						<IntelPanel
							title="Contradictions"
							count={intel.contradictions.length}
							borderColor="border-l-red-500"
							countBg="bg-red-500/10 text-red-400"
						>
							{intel.contradictions.length === 0 ? (
								<EmptyState text="No contradictions detected" />
							) : (
								intel.contradictions.map((c, i) => (
									<div key={i} className="rounded-md border border-[var(--border)] p-3 text-sm">
										<div className="flex items-start gap-2">
											<span className="mt-0.5 text-red-400 shrink-0">!</span>
											<div>
												<div className="text-[var(--text)]">
													&ldquo;{c.record_a}&rdquo; vs &ldquo;{c.record_b}&rdquo;
												</div>
												{c.resolution && (
													<div className="mt-1 text-xs text-[var(--text-secondary)]">
														Resolution: {c.resolution}
													</div>
												)}
												<div className="mt-1 text-xs text-[var(--text-muted)]">
													{c.project}
												</div>
											</div>
										</div>
									</div>
								))
							)}
						</IntelPanel>

						{/* Weak Signals */}
						<IntelPanel
							title="Weak Signals"
							count={intel.signals.length}
							borderColor="border-l-blue-500"
							countBg="bg-blue-500/10 text-blue-400"
						>
							{intel.signals.length === 0 ? (
								<EmptyState text="No recurring signals detected" />
							) : (
								intel.signals.map((s, i) => (
									<div key={i} className="rounded-md border border-[var(--border)] p-3 text-sm">
										<div className="flex items-start justify-between gap-2">
											<div>
												<div className="text-[var(--text)]">{s.topic}</div>
												<div className="mt-1 text-xs text-[var(--text-secondary)]">
													{s.recommendation}
												</div>
												<div className="mt-1 text-xs text-[var(--text-muted)]">
													{s.project}
												</div>
											</div>
											<span className="shrink-0 rounded-full bg-blue-500/10 px-2 py-0.5 text-xs text-blue-400">
												{s.summary_count} sessions
											</span>
										</div>
									</div>
								))
							)}
						</IntelPanel>

						{/* Knowledge Gaps */}
						<IntelPanel
							title="Knowledge Gaps"
							count={intel.gaps.length}
							borderColor="border-l-yellow-500"
							countBg="bg-yellow-500/10 text-yellow-400"
						>
							{intel.gaps.length === 0 ? (
								<EmptyState text="No knowledge gaps detected" />
							) : (
								intel.gaps.map((g, i) => (
									<div key={i} className="rounded-md border border-[var(--border)] p-3 text-sm">
										<div className="text-[var(--text)]">{g.topic}</div>
										<div className="mt-1 text-xs text-[var(--text-secondary)]">
											{g.coverage}
										</div>
										<div className="mt-1 text-xs text-[var(--text-muted)]">
											{g.project}
										</div>
									</div>
								))
							)}
						</IntelPanel>

						{/* Cross-Agent Insights */}
						<IntelPanel
							title="Cross-Agent Insights"
							count={intel.cross_agent.length}
							borderColor="border-l-purple-500"
							countBg="bg-purple-500/10 text-purple-400"
						>
							{intel.cross_agent.length === 0 ? (
								<EmptyState text="No cross-agent patterns detected" />
							) : (
								intel.cross_agent.map((ca, i) => (
									<div key={i} className="rounded-md border border-[var(--border)] p-3 text-sm">
										<div className="flex items-start justify-between gap-2">
											<div>
												<div className="text-[var(--text)]">{ca.topic}</div>
												<div className="mt-1 text-xs text-[var(--text-secondary)]">
													{ca.insight}
												</div>
												<div className="mt-1 text-xs text-[var(--text-muted)]">
													{ca.project}
												</div>
											</div>
											<div className="flex shrink-0 gap-1">
												{ca.agents.map((agent) => (
													<span key={agent} className="rounded-full bg-purple-500/10 px-2 py-0.5 text-xs text-purple-400">
														{agent}
													</span>
												))}
											</div>
										</div>
									</div>
								))
							)}
						</IntelPanel>
					</div>

					{/* Curation History */}
					<div>
						<h3 className="mb-2 text-xs font-medium text-[var(--text-secondary)]">
							Recent Curation Runs
						</h3>
						{intel.curate_history.length > 0 ? (
							<div className="space-y-1.5">
								{intel.curate_history.map((entry, i) => (
									<div
										key={i}
										className="flex items-center justify-between rounded-md border border-[var(--border)] px-3 py-2 text-sm"
									>
										<div className="flex items-center gap-3">
											<span
												className={`h-2 w-2 shrink-0 rounded-full ${
													entry.status === "completed" ? "bg-green-400" : "bg-yellow-400"
												}`}
											/>
											<span className="text-[var(--text)]">{entry.project}</span>
											{entry.started_at && (
												<span className="text-xs text-[var(--text-muted)]">
													{timeAgo(entry.started_at)}
												</span>
											)}
										</div>
										<div className="flex gap-3 text-xs text-[var(--text-secondary)]">
											{Object.entries(entry.counts).map(([k, v]) =>
												v > 0 ? (
													<span key={k}>
														{k}: {v}
													</span>
												) : null
											)}
										</div>
									</div>
								))}
							</div>
						) : (
							<div className="py-3 text-center text-xs text-[var(--text-muted)]">
								No curation runs recorded yet
							</div>
						)}
					</div>
				</div>
			)}
		</div>
	);
}

/* ==================================================================
   Intelligence sub-components
   ================================================================== */

function IntelPanel({
	title,
	count,
	borderColor,
	countBg,
	children,
}: {
	title: string;
	count: number;
	borderColor: string;
	countBg: string;
	children: React.ReactNode;
}) {
	return (
		<div className={`rounded-lg border border-[var(--border)] border-l-2 ${borderColor} bg-[var(--bg-subtle)] p-4`}>
			<div className="mb-3 flex items-center justify-between">
				<h3 className="text-sm font-medium text-[var(--text)]">{title}</h3>
				<span className={`rounded-full px-2 py-0.5 text-xs font-medium ${countBg}`}>
					{count}
				</span>
			</div>
			<div className="space-y-2">{children}</div>
		</div>
	);
}

function EmptyState({ text }: { text: string }) {
	return (
		<div className="py-3 text-center text-xs text-[var(--text-muted)]">
			{text}
		</div>
	);
}

/* ==================================================================
   Utility helpers
   ================================================================== */

function timeAgo(iso: string): string {
	const diff = Date.now() - new Date(iso).getTime();
	if (isNaN(diff)) return "unknown";
	const mins = Math.floor(diff / 60000);
	if (mins < 1) return "just now";
	if (mins < 60) return `${mins}m ago`;
	const hrs = Math.floor(mins / 60);
	if (hrs < 24) return `${hrs}h ago`;
	const days = Math.floor(hrs / 24);
	return `${days}d ago`;
}

/* ==================================================================
   Inline helper components (unchanged from original)
   ================================================================== */

function StatusDot({ status }: { status: string }) {
	const color =
		status === "active"
			? "bg-[var(--accent-teal)]"
			: status === "archived"
				? "bg-[var(--text-muted)]"
				: "bg-amber-400";
	return (
		<span
			className={`mt-1 h-2 w-2 shrink-0 rounded-full ${color}`}
			title={status}
		/>
	);
}

function SearchIcon({ className }: { className?: string }) {
	return (
		<svg
			className={className}
			xmlns="http://www.w3.org/2000/svg"
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			strokeWidth={2}
			strokeLinecap="round"
			strokeLinejoin="round"
		>
			<circle cx="11" cy="11" r="8" />
			<line x1="21" y1="21" x2="16.65" y2="16.65" />
		</svg>
	);
}

function BrainIcon({ className }: { className?: string }) {
	return (
		<svg
			className={className}
			xmlns="http://www.w3.org/2000/svg"
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			strokeWidth={2}
			strokeLinecap="round"
			strokeLinejoin="round"
		>
			<path d="M12 2a7 7 0 0 0-7 7c0 3 2 5.5 4 7l1.5 1.5h3L15 16c2-1.5 4-4 4-7a7 7 0 0 0-7-7z" />
			<path d="M12 22v-4" />
			<path d="M9 18h6" />
		</svg>
	);
}
