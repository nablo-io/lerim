"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";
import type { PipelineReportResponse, StatsResponse } from "@/lib/types";
import StatsCard from "@/components/StatsCard";
import AgentDistributionChart from "@/components/charts/AgentDistributionChart";
import DailySessionsChart from "@/components/charts/DailySessionsChart";
import HourlyActivityChart from "@/components/charts/HourlyActivityChart";
import ToolUsageChart from "@/components/charts/ToolUsageChart";
import DailyMetricsChart from "@/components/charts/DailyMetricsChart";
import ModelUsageChart from "@/components/charts/ModelUsageChart";

const SCOPES = [
	{ value: "all", label: "All" },
	{ value: "week", label: "Week" },
	{ value: "month", label: "Month" },
] as const;

export default function AnalyticsPage() {
	const [scope, setScope] = useState("all");
	const [stats, setStats] = useState<StatsResponse | null>(null);
	const [report, setReport] = useState<PipelineReportResponse | null>(null);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	const load = useCallback(async () => {
		setLoading(true);
		setError(null);
		try {
			const [statsData, reportData] = await Promise.all([
				api.getStats(scope, true),
				api.getPipelineReport().catch(() => null),
			]);
			setStats(statsData);
			setReport(reportData);
		} catch (err) {
			setError(err instanceof Error ? err.message : "Failed to load stats");
		} finally {
			setLoading(false);
		}
	}, [scope]);

	useEffect(() => {
		load();
	}, [load]);

	const totalRuns = stats?.totals.runs ?? 0;
	const totalRecords = report?.records_total ?? 0;
	const totalErrors = stats?.totals.errors ?? 0;
	const activeRecords = report?.records_active ?? 0;

	/* Trend indicators (simple heuristic: compare to average) */
	const errorRate =
		stats && stats.totals.messages > 0
			? ((stats.totals.errors / stats.totals.messages) * 100).toFixed(1)
			: "0.0";

	return (
		<>
			{/* Header */}
			<div className="flex items-center justify-between">
				<div>
					<h1 className="text-lg font-semibold text-[var(--text)]">Usage Diagnostics</h1>
					<p className="mt-0.5 text-xs text-[var(--text-muted)]">
						Transcript activity, context volume, and runtime quality signals
					</p>
				</div>
				<div className="inline-flex items-center rounded-md border border-[var(--border)] bg-[var(--bg-card)]">
					{SCOPES.map(({ value: v, label }) => (
						<button
							key={v}
							onClick={() => setScope(v)}
							className={`px-3 py-1.5 text-xs font-medium transition-colors ${
								scope === v
									? "bg-white/[0.08] text-[var(--text)]"
									: "text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
							} ${v === "all" ? "rounded-l-md" : ""} ${v === "month" ? "rounded-r-md" : ""}`}
						>
							{label}
						</button>
					))}
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
			) : stats && totalRuns === 0 && totalRecords === 0 ? (
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
							label="Context Records"
							value={totalRecords}
						/>
						<StatsCard
							label="Runtime Errors"
							value={totalErrors}
							secondary={`${errorRate}% error rate`}
						/>
						<StatsCard
							label="Active Context"
							value={formatCompact(activeRecords)}
							secondary={`${formatCompact(stats.totals.tokens)} tokens`}
						/>
					</div>

					{stats.data_readiness && (
						<ReadinessNotice stats={stats} />
					)}

					{/* Charts: 2-column grid */}
					<div className="mt-6 grid grid-cols-1 gap-3 lg:grid-cols-2">
						<DailySessionsChart daily={stats.daily} byAgent={stats.by_agent} />
						<AgentDistributionChart byAgent={stats.by_agent} />
					</div>

					{/* Second row: 2-column grid */}
					<div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2">
						<HourlyActivityChart data={stats.hourly_activity} />
						<ModelUsageChart data={stats.model_usage} />
					</div>

					{/* Full-width charts */}
					<div className="mt-3">
						<DailyMetricsChart daily={stats.daily} />
					</div>

					<div className="mt-3">
						<ToolUsageChart data={stats.tool_usage} />
					</div>
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
