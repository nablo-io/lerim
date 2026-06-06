"use client";

import { Suspense, useEffect, useState, useCallback, useRef } from "react";
import { api } from "@/lib/api";
import { useProjectScope } from "@/lib/projectScope";
import type { ContextRecordVersion, PipelineReportResponse, StatsResponse } from "@/lib/types";
import StatsCard from "@/components/StatsCard";
import AgentDistributionChart from "@/components/charts/AgentDistributionChart";
import DailySessionsChart from "@/components/charts/DailySessionsChart";
import HourlyActivityChart from "@/components/charts/HourlyActivityChart";
import ToolUsageChart from "@/components/charts/ToolUsageChart";
import DailyMetricsChart from "@/components/charts/DailyMetricsChart";
import ModelUsageChart from "@/components/charts/ModelUsageChart";
import MemoryTimelineChart, { type MemoryTimelineScope } from "@/components/charts/MemoryTimelineChart";
import ProjectScope from "@/components/ProjectScope";

const INSIGHT_SCOPES: Array<{ value: MemoryTimelineScope; label: string }> = [
	{ value: "week", label: "Week" },
	{ value: "month", label: "Month" },
	{ value: "all", label: "All" },
];

export default function AnalyticsPage() {
	return (
		<Suspense fallback={<div className="text-sm text-[var(--text-muted)]">Loading…</div>}>
			<AnalyticsContent />
		</Suspense>
	);
}

function AnalyticsContent() {
	const { project, setProject } = useProjectScope();
	const [statsScope, setStatsScope] = useState<MemoryTimelineScope>("all");
	const [stats, setStats] = useState<StatsResponse | null>(null);
	const [report, setReport] = useState<PipelineReportResponse | null>(null);
		const [memoryVersions, setMemoryVersions] = useState<ContextRecordVersion[]>([]);
		const [loading, setLoading] = useState(true);
		const [error, setError] = useState<string | null>(null);
		const loadSeqRef = useRef(0);

		const load = useCallback(async () => {
			const seq = loadSeqRef.current + 1;
			loadSeqRef.current = seq;
			setLoading(true);
			setError(null);
			try {
			const [statsData, reportData, versionsData] = await Promise.all([
				api.getStats(statsScope, true, project || undefined),
					api.getPipelineReport(project || undefined).catch(() => null),
					api.getRecordVersions(project ? { limit: "5000", project } : { limit: "5000" }),
				]);
				if (seq !== loadSeqRef.current) return;
				setStats(statsData);
				setReport(reportData);
				setMemoryVersions(versionsData.versions);
			} catch (err) {
				if (seq === loadSeqRef.current) {
					setStats(null);
					setReport(null);
					setMemoryVersions([]);
					setError(err instanceof Error ? err.message : "Failed to load stats");
				}
			} finally {
				if (seq === loadSeqRef.current) setLoading(false);
			}
		}, [project, statsScope]);

	useEffect(() => {
		load();
	}, [load]);

	const memoryTotals = memoryRecordTotals(memoryVersions);
	const totalRuns = stats?.totals.runs ?? 0;
	const totalRecords = Math.max(report?.records_total ?? 0, memoryTotals.total);
	const totalErrors = stats?.totals.errors ?? 0;
	const activeRecords = Math.max(report?.records_active ?? 0, memoryTotals.active);
	const hasModelUsage = stats ? Object.keys(stats.model_usage).length > 0 : false;
	const hasToolUsage = stats ? stats.tool_usage.length > 0 : false;

	/* Trend indicators (simple heuristic: compare to average) */
	const errorRate =
		stats && stats.totals.messages > 0
			? ((stats.totals.errors / stats.totals.messages) * 100).toFixed(1)
			: "0.0";

	return (
		<>
			{/* Header */}
			<div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
				<div>
					<h1 className="text-lg font-semibold text-[var(--text)]">Insights</h1>
					<p className="mt-0.5 text-xs text-[var(--text-muted)]">
						Record growth, transcript activity, and runtime quality signals
					</p>
				</div>
				<div className="flex flex-wrap items-center gap-2">
					<ProjectScope value={project} onChange={setProject} />
					<span className="text-[11px] font-medium uppercase tracking-wide text-[var(--text-muted)]">
						Time window
					</span>
					<div className="inline-flex min-h-11 items-center rounded-md border border-[var(--border)] bg-[var(--bg-card)]">
						{INSIGHT_SCOPES.map(({ value, label }, index) => (
							<button
								key={value}
								type="button"
								onClick={() => setStatsScope(value)}
								className={`min-h-11 px-3 text-xs font-medium transition-colors ${
									statsScope === value
										? "bg-white/[0.08] text-[var(--text)]"
										: "text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
								} ${index === 0 ? "rounded-l-md" : ""} ${index === INSIGHT_SCOPES.length - 1 ? "rounded-r-md" : ""}`}
							>
								{label}
							</button>
						))}
					</div>
				</div>
			</div>

			{error && (
				<div className="mt-4 rounded-lg border border-red-500/20 bg-red-500/5 px-4 py-3 text-sm text-red-400">
					{error}
				</div>
			)}

			{loading && !stats ? (
				<div className="mt-8 text-center text-sm text-[var(--text-muted)]">
					Loading…
				</div>
			) : stats && totalRuns === 0 && totalRecords === 0 && memoryVersions.length === 0 ? (
				<div className="mt-8 rounded-lg border border-[var(--border)] bg-[var(--bg-card)] px-6 py-10 text-center">
					<p className="text-sm font-medium text-[var(--text)]">No impact data yet.</p>
					<p className="mt-1 text-xs text-[var(--text-muted)]">
						These charts are derived from imported session transcripts, messages, and the context records created from them.
						Run ingestion first, then curation will add context growth and quality signals.
					</p>
				</div>
			) : stats ? (
				<>
					{/* 4 stat cards */}
					<div className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
						<StatsCard
							label="Source Sessions"
							value={totalRuns}
							secondary={
								stats.daily.length > 0
									? `${(totalRuns / Math.max(stats.daily.length, 1)).toFixed(1)}/day avg`
									: undefined
							}
						/>
						<StatsCard
							label="Records"
							value={totalRecords}
						/>
						<StatsCard
							label="Runtime Errors"
							value={totalErrors}
							secondary={`${errorRate}% error rate`}
						/>
						<StatsCard
							label="Active Records"
							value={formatCompact(activeRecords)}
							secondary={`${formatCompact(stats.totals.tokens)} tokens`}
						/>
					</div>

					{stats.data_readiness && (
						<ReadinessNotice stats={stats} />
					)}

					<MemoryTimelineChart
						versions={memoryVersions}
						scope={statsScope}
						loading={loading}
					/>

					<section className="mt-6">
						<div>
							<h2 className="text-base font-semibold text-[var(--text)]">Source Activity</h2>
							<p className="mt-1 text-xs text-[var(--text-muted)]">
								Sessions, agents, messages, and runtime quality
							</p>
						</div>

						<div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">
							<DailySessionsChart daily={stats.daily} byAgent={stats.by_agent} />
							<AgentDistributionChart byAgent={stats.by_agent} />
						</div>

						<div className={`mt-3 grid grid-cols-1 gap-3 ${hasModelUsage ? "lg:grid-cols-2" : ""}`}>
							<HourlyActivityChart data={stats.hourly_activity} />
							{hasModelUsage && <ModelUsageChart data={stats.model_usage} />}
						</div>

						<div className="mt-3">
							<DailyMetricsChart daily={stats.daily} />
						</div>

						{hasToolUsage && (
							<div className="mt-3">
								<ToolUsageChart data={stats.tool_usage} />
							</div>
						)}
					</section>
				</>
			) : null}
		</>
	);
}

function ReadinessNotice({ stats }: { stats: StatsResponse }) {
	const readiness = stats.data_readiness;
	if (!readiness) return null;
	const reasons = [
		readiness.empty_reasons.model_usage,
		readiness.empty_reasons.tool_usage,
	].filter((value): value is string => Boolean(value));
	if (reasons.length === 0) return null;

	return (
		<div className="mt-4 rounded-lg border border-[var(--border)] bg-[var(--bg-card)] px-4 py-3">
			<div className="text-xs font-medium text-[var(--text-secondary)]">
				Transcript-derived diagnostics are partial
			</div>
			<div className="mt-1 text-xs leading-5 text-[var(--text-muted)]">
				{reasons.join(" ")}
			</div>
		</div>
	);
}

function formatCompact(value: number): string {
	if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
	if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
	return value.toLocaleString();
}

/** Count current total and active records from the latest visible version per record. */
function memoryRecordTotals(versions: ContextRecordVersion[]) {
	const latestStatus = new Map<string, string>();
	for (const version of [...versions].sort((left, right) => String(left.changed_at).localeCompare(String(right.changed_at)))) {
		const archived = version.change_kind === "archive" || version.change_kind === "supersede" || version.status === "archived";
		latestStatus.set(version.record_id, archived ? "archived" : "active");
	}
	return {
		total: latestStatus.size,
		active: Array.from(latestStatus.values()).filter((status) => status === "active").length,
	};
}
