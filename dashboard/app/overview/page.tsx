"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type {
	ActivityFeedItem,
	IntelligenceResponse,
	PipelineReportResponse,
	PipelineStatusResponse,
	StatsResponse,
} from "@/lib/types";
import LiveStatus from "@/components/LiveStatus";

export default function OverviewPage() {
	const [status, setStatus] = useState<PipelineStatusResponse | null>(null);
	const [report, setReport] = useState<PipelineReportResponse | null>(null);
	const [stats, setStats] = useState<StatsResponse | null>(null);
	const [intel, setIntel] = useState<IntelligenceResponse | null>(null);
	const [activity, setActivity] = useState<ActivityFeedItem[]>([]);
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);

	const load = useCallback(async () => {
		setLoading(true);
		setError(null);
		try {
			const [statusData, reportData, statsData, intelData, activityData] = await Promise.all([
				api.getPipelineStatus(),
				api.getPipelineReport().catch(() => null),
				api.getStats("week", true).catch(() => null),
				api.getIntelligence(8).catch(() => null),
				api.getActivityFeed(7, 8).catch(() => ({ items: [] })),
			]);
			setStatus(statusData);
			setReport(reportData);
			setStats(statsData);
			setIntel(intelData);
			setActivity(activityData.items);
		} catch (err) {
			setError(err instanceof Error ? err.message : "Failed to load overview");
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => {
		load();
	}, [load]);

	const sessions = status?.sessions.total ?? stats?.totals.runs ?? 0;
	const records = status?.records.total ?? report?.records_total ?? 0;
	const activeRecords = status?.records.active ?? report?.records_active ?? 0;
	const errors = status?.logs.errors ?? stats?.totals.errors ?? 0;
	const health = intel?.health_score ?? null;

	return (
		<>
			<div className="flex flex-wrap items-center justify-between gap-3">
				<div>
					<h1 className="text-lg font-semibold text-[var(--text)]">Overview</h1>
					<p className="mt-0.5 text-xs text-[var(--text-muted)]">
						Record health, graph readiness, and recent agent processing activity
					</p>
				</div>
				<button
					type="button"
					onClick={load}
					disabled={loading}
					className="rounded-md border border-[var(--border)] px-3 py-1.5 text-xs text-[var(--text-secondary)] transition-colors hover:text-[var(--text)] disabled:cursor-not-allowed disabled:opacity-40"
				>
					{loading ? "Refreshing…" : "Refresh"}
				</button>
			</div>

			<div className="mt-4">
				<LiveStatus />
			</div>

			{error && (
				<div className="mt-4 rounded-lg border border-red-500/20 bg-red-500/5 px-4 py-3 text-sm text-red-400">
					{error}
				</div>
			)}

			<div className="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
				<MetricCard label="Source Sessions" value={sessions} />
				<MetricCard label="Records" value={records} secondary={`${activeRecords.toLocaleString()} active`} />
				<MetricCard label="Record Health" value={health == null ? "–" : `${health}/100`} />
				<MetricCard label="Runtime Errors" value={errors} accent={errors > 0 ? "red" : undefined} />
			</div>

			<div className="mt-6 grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
				<section className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4">
					<div className="mb-3 flex items-center justify-between">
						<h2 className="text-sm font-medium text-[var(--text)]">Recent Operations</h2>
						<Link className="text-xs text-[var(--accent-blue)] hover:underline" href="/operations">
							View all
						</Link>
					</div>
					{loading && activity.length === 0 ? (
						<div className="space-y-2">
							{Array.from({ length: 4 }).map((_, index) => (
								<div key={index} className="h-9 animate-pulse rounded bg-white/[0.04]" />
							))}
						</div>
					) : activity.length > 0 ? (
						<div className="space-y-2">
							{activity.slice(0, 6).map((item) => (
								<OperationRow key={item.id || item.started_at || item.type} item={item} />
							))}
						</div>
					) : (
						<p className="py-6 text-center text-sm text-[var(--text-muted)]">
							No operations recorded yet.
						</p>
					)}
				</section>

				<section className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4">
					<div className="mb-3 flex items-center justify-between">
						<h2 className="text-sm font-medium text-[var(--text)]">Where To Go Next</h2>
					</div>
					<div className="grid gap-2">
						<Shortcut href="/context-graph" title="Explore the graph" body="See clusters and relationships between extracted context records." />
						<Shortcut href="/context" title="Review context" body="Search and inspect evidence-backed records." />
						<Shortcut href="/traces" title="Review sources" body="Inspect the source transcripts that feed context and graph compilation." />
					</div>
				</section>
			</div>
		</>
	);
}

function MetricCard({
	label,
	value,
	secondary,
	accent,
}: {
	label: string;
	value: string | number;
	secondary?: string;
	accent?: "red";
}) {
	return (
		<div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4">
			<p className="text-[11px] font-medium uppercase tracking-wide text-[var(--text-muted)]">
				{label}
			</p>
			<p className={`mt-2 text-2xl font-semibold tabular-nums ${accent === "red" ? "text-red-400" : "text-[var(--text)]"}`}>
				{typeof value === "number" ? value.toLocaleString() : value}
			</p>
			{secondary && <p className="mt-1 text-xs text-[var(--text-secondary)]">{secondary}</p>}
		</div>
	);
}

function OperationRow({ item }: { item: ActivityFeedItem }) {
	const isIngest = item.type === "ingest";
	const color = isIngest ? "#3b82f6" : "#8b5cf6";
	const summary = isIngest
		? `${item.total_sessions || 0} session${item.total_sessions === 1 ? "" : "s"} · ${item.total_records_created || 0} records`
		: `${item.counts?.created || 0} created · ${item.counts?.updated || 0} updated · ${item.counts?.archived || 0} archived`;

	return (
		<div className="flex items-center gap-3 rounded-md border border-[var(--border)] px-3 py-2">
			<span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: color }} />
			<div className="min-w-0 flex-1">
				<div className="flex items-center gap-2">
					<span className="text-xs font-medium text-[var(--text)]">
						{isIngest ? "Ingest" : "Curate"}
					</span>
					<span className="text-[11px] text-[var(--text-muted)]">{item.status}</span>
				</div>
				<p className="truncate text-xs text-[var(--text-muted)]">{summary}</p>
			</div>
			<span className="shrink-0 text-[11px] text-[var(--text-muted)]">
				{item.started_at ? relativeTime(item.started_at) : ""}
			</span>
		</div>
	);
}

function Shortcut({ href, title, body }: { href: string; title: string; body: string }) {
	return (
		<Link
			href={href}
			className="rounded-md border border-[var(--border)] px-3 py-2 transition-colors hover:bg-white/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
		>
			<p className="text-sm font-medium text-[var(--text)]">{title}</p>
			<p className="mt-0.5 text-xs text-[var(--text-muted)]">{body}</p>
		</Link>
	);
}

function relativeTime(iso: string): string {
	const diff = Date.now() - new Date(iso).getTime();
	if (diff < 0) return "just now";
	const mins = Math.floor(diff / 60000);
	if (mins < 1) return "just now";
	if (mins < 60) return `${mins}m ago`;
	const hrs = Math.floor(mins / 60);
	if (hrs < 24) return `${hrs}h ago`;
	const days = Math.floor(hrs / 24);
	return `${days}d ago`;
}
