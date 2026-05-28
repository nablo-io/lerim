"use client";

import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import type { EChartsOption } from "echarts";
import ChartWrapper from "./ChartWrapper";
import { formatRecordKind, formatScopeLabel, formatStatusLabel } from "@/lib/labels";
import type { ContextRecordVersion } from "@/lib/types";

export type MemoryTimelineScope = "all" | "week" | "month";

interface MemoryTimelineChartProps {
	versions: ContextRecordVersion[];
	scope: MemoryTimelineScope;
	loading?: boolean;
}

interface MemoryPoint {
	key: string;
	label: string;
	rangeLabel: string;
	created: number;
	updated: number;
	archived: number;
	active: number;
	versions: ContextRecordVersion[];
	start: Date;
}

const FEATURED_KINDS = ["decision", "fact", "preference", "episode", "constraint"];

/** Render Lerim's memory lifecycle as creation, revision, pruning, and active total over time. */
export default function MemoryTimelineChart({
	versions,
	scope,
	loading = false,
}: MemoryTimelineChartProps) {
	const [project, setProject] = useState("");
	const [kind, setKind] = useState("");
	const points = useMemo(
		() => buildMemoryTimeline(versions, scope, project, kind),
		[versions, scope, project, kind],
	);
	const defaultKey = points.length ? points[points.length - 1].key : "";
	const [selectedKey, setSelectedKey] = useState(defaultKey);
	const [selectedVersion, setSelectedVersion] = useState<ContextRecordVersion | null>(null);

	useEffect(() => {
		if (!points.some((point) => point.key === selectedKey)) {
			setSelectedKey(defaultKey);
		}
	}, [defaultKey, points, selectedKey]);

	useEffect(() => {
		if (selectedVersion && !versions.some((version) => version.version_id === selectedVersion.version_id)) {
			setSelectedVersion(null);
		}
	}, [selectedVersion, versions]);

	const projects = useMemo(() => uniqueSorted(versions.map((version) => version.project)), [versions]);
	const kinds = useMemo(() => {
		const available = new Set(uniqueSorted(versions.map((version) => version.record_kind)));
		const extras = Array.from(available).filter((value) => !FEATURED_KINDS.includes(value));
		return [...FEATURED_KINDS, ...extras];
	}, [versions]);
	const selectedPoint = points.find((point) => point.key === selectedKey) || points[points.length - 1];
	const hasData = points.some((point) => point.created || point.updated || point.archived || point.active);
	const option = useMemo(() => buildChartOption(points), [points]);
	const onEvents = useMemo(
		() => ({
			click: (params: unknown) => {
				const index = typeof (params as { dataIndex?: unknown }).dataIndex === "number"
					? (params as { dataIndex: number }).dataIndex
					: -1;
				if (points[index]) setSelectedKey(points[index].key);
			},
		}),
		[points],
	);

	return (
		<>
			<section className="mt-6 border-y border-[var(--border)] py-5">
				<div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
					<div>
						<h2 className="text-base font-semibold text-[var(--text)]">Record Timeline</h2>
						<p className="mt-1 text-xs text-[var(--text-muted)]">
							Created, revised, archived, and active records
						</p>
					</div>

					<div className="flex flex-wrap items-center gap-2">
						<select
							aria-label="Project"
							value={project}
							onChange={(event) => setProject(event.target.value)}
							className="min-h-11 rounded-md border border-[var(--border)] bg-[var(--bg-card)] px-3 text-xs text-[var(--text)] outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
						>
							<option value="">All projects</option>
							{projects.map((item) => (
								<option key={item} value={item}>
									{formatScopeLabel(item)}
								</option>
							))}
						</select>
					</div>
				</div>

				<div className="mt-3 flex flex-wrap gap-2">
					<button
						type="button"
						onClick={() => setKind("")}
						className={`min-h-9 rounded-full px-3 text-xs font-medium transition-colors ${
							kind === ""
								? "bg-[var(--accent-blue)] text-white"
								: "bg-white/[0.04] text-[var(--text-muted)] hover:bg-white/[0.08] hover:text-[var(--text-secondary)]"
						}`}
					>
						All types
					</button>
					{kinds.map((item) => (
						<button
							key={item}
							type="button"
							onClick={() => setKind(kind === item ? "" : item)}
							className={`min-h-9 rounded-full px-3 text-xs font-medium transition-colors ${
								kind === item
									? "bg-[var(--accent-blue)] text-white"
									: "bg-white/[0.04] text-[var(--text-muted)] hover:bg-white/[0.08] hover:text-[var(--text-secondary)]"
							}`}
						>
							{formatRecordKind(item)}
						</button>
					))}
				</div>

				<div className="mt-3 grid gap-3 xl:grid-cols-[minmax(0,1fr)_22rem]">
					<ChartWrapper
						option={option}
						height={360}
						loading={loading}
						empty={!loading && !hasData}
						emptyText="No memory changes yet"
						onEvents={onEvents}
					/>
					<MemoryDrilldown point={selectedPoint} onSelect={setSelectedVersion} />
				</div>
			</section>
			{selectedVersion && (
				<MemoryDetailModal version={selectedVersion} onClose={() => setSelectedVersion(null)} />
			)}
		</>
	);
}

/** List the records changed in the selected bucket so the chart is useful after the first click. */
function MemoryDrilldown({ point, onSelect }: { point?: MemoryPoint; onSelect: (version: ContextRecordVersion) => void }) {
	const items = [...(point?.versions || [])].sort((left, right) =>
		String(right.changed_at).localeCompare(String(left.changed_at)),
	);

	return (
		<aside className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4">
			<div className="flex items-start justify-between gap-3">
				<div>
					<h3 className="text-sm font-medium text-[var(--text)]">Changed Memories</h3>
					<p className="mt-1 text-xs text-[var(--text-muted)]">
						{point ? point.rangeLabel : "No time bucket selected."}
					</p>
				</div>
				{point && (
					<div className="shrink-0 rounded-md border border-[var(--border)] px-2 py-1 text-right">
						<p className="text-sm font-semibold tabular-nums text-[var(--text)]">{point.active}</p>
						<p className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">active</p>
					</div>
				)}
			</div>

			{items.length > 0 ? (
				<div className="mt-4 max-h-[18rem] space-y-2 overflow-y-auto pr-1">
					{items.slice(0, 8).map((version) => (
						<button
							key={`${version.version_id}-${version.record_id}`}
							type="button"
							onClick={() => onSelect(version)}
							className="block min-h-16 w-full rounded-md border border-[var(--border)] px-3 py-2 text-left transition-colors hover:border-white/20 hover:bg-white/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
							aria-label={`Open details for ${version.title || "untitled memory"}`}
						>
							<div className="flex min-w-0 items-center gap-2">
								<span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${changeBadgeClass(version.change_kind)}`}>
									{changeLabel(version.change_kind)}
								</span>
								<span className="min-w-0 truncate text-xs font-medium text-[var(--text)]">
									{version.title || "(untitled)"}
								</span>
							</div>
							<div className="mt-1 flex flex-wrap gap-1.5 text-[11px] text-[var(--text-muted)]">
								<span>{formatRecordKind(version.record_kind)}</span>
								{version.project && <span>{formatScopeLabel(version.project)}</span>}
							</div>
						</button>
					))}
					{items.length > 8 && (
						<p className="text-xs text-[var(--text-muted)]">
							{items.length - 8} more change{items.length - 8 === 1 ? "" : "s"} in this bucket.
						</p>
					)}
				</div>
			) : (
				<p className="mt-8 text-center text-sm text-[var(--text-muted)]">
					No memory changes in this bucket.
				</p>
			)}
		</aside>
	);
}

/** Show the full selected memory version without taking users away from Insights. */
function MemoryDetailModal({ version, onClose }: { version: ContextRecordVersion; onClose: () => void }) {
	const backdropRef = useRef<HTMLDivElement>(null);
	const closeButtonRef = useRef<HTMLButtonElement>(null);
	const typedFields = memoryTypedFields(version);
	const provenanceFields = memoryProvenanceFields(version);

	useEffect(() => {
		function handleKey(event: KeyboardEvent) {
			if (event.key === "Escape") onClose();
		}
		document.addEventListener("keydown", handleKey);
		return () => document.removeEventListener("keydown", handleKey);
	}, [onClose]);

	useEffect(() => {
		document.body.style.overflow = "hidden";
		closeButtonRef.current?.focus();
		return () => {
			document.body.style.overflow = "";
		};
	}, []);

	function handleBackdropClick(event: MouseEvent<HTMLDivElement>) {
		if (event.target === backdropRef.current) onClose();
	}

	return (
		<div
			ref={backdropRef}
			onClick={handleBackdropClick}
			className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto overscroll-contain p-4"
			style={{ backgroundColor: "rgba(0, 0, 0, 0.7)" }}
		>
			<div
				role="dialog"
				aria-modal="true"
				aria-labelledby="memory-detail-title"
				className="relative my-4 max-h-[calc(100vh-2rem)] w-full max-w-3xl overflow-y-auto rounded-xl border border-[var(--border)] bg-[var(--bg-subtle)]"
			>
				<button
					ref={closeButtonRef}
					type="button"
					onClick={onClose}
					className="absolute right-4 top-4 flex min-h-11 min-w-11 items-center justify-center rounded-md p-1.5 text-[var(--text-muted)] transition-colors hover:bg-white/[0.06] hover:text-[var(--text-secondary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
					aria-label="Close memory details"
				>
					<svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
						<line x1="18" y1="6" x2="6" y2="18" />
						<line x1="6" y1="6" x2="18" y2="18" />
					</svg>
				</button>

				<div className="border-b border-[var(--border)] p-5 pr-16 sm:p-6 sm:pr-16">
					<div className="flex min-w-0 flex-wrap items-center gap-2">
						<span className={`rounded px-2 py-1 text-[11px] font-medium ${changeBadgeClass(version.change_kind)}`}>
							{changeLabel(version.change_kind)}
						</span>
						<span className="rounded bg-white/[0.06] px-2 py-1 text-[11px] font-medium text-[var(--text-muted)]">
							v{version.version_no || 1}
						</span>
					</div>
					<h2 id="memory-detail-title" className="mt-3 text-base font-semibold leading-6 text-[var(--text)]">
						{version.title || "(untitled memory)"}
					</h2>
					<div className="mt-3 flex flex-wrap gap-2">
						<MemoryChip label="Type" value={formatRecordKind(version.record_kind)} />
						{version.project && <MemoryChip label="Project" value={formatScopeLabel(version.project)} />}
						<MemoryChip label="Status" value={formatStatusLabel(version.status)} />
						<MemoryChip label="Changed" value={formatDateTime(version.changed_at)} />
					</div>
				</div>

				<div className="space-y-5 p-5 sm:p-6">
					<MemoryTextBlock label="Body" value={version.body || "No body stored for this memory."} />

					{typedFields.length > 0 && (
						<div>
							<h3 className="mb-2 text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
								Typed Fields
							</h3>
							<div className="grid gap-3 sm:grid-cols-2">
								{typedFields.map((field) => (
									<MemoryTextBlock key={field.label} label={field.label} value={field.value} compact />
								))}
							</div>
						</div>
					)}

					<div>
						<h3 className="mb-2 text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
							Provenance
						</h3>
						<div className="grid gap-3 sm:grid-cols-2">
							{provenanceFields.map((field) => (
								<MemoryMetaField key={field.label} label={field.label} value={field.value} mono={field.mono} />
							))}
						</div>
					</div>
				</div>
			</div>
		</div>
	);
}

/** Render one compact metadata chip in the memory modal header. */
function MemoryChip({ label, value }: { label: string; value: string }) {
	return (
		<span className="inline-flex min-h-8 items-center gap-1.5 rounded-md bg-[var(--bg-card)] px-2.5 py-1 text-xs">
			<span className="text-[var(--text-muted)]">{label}:</span>
			<span className="font-medium text-[var(--text-secondary)]">{value}</span>
		</span>
	);
}

/** Render paragraph-like memory fields with readable wrapping. */
function MemoryTextBlock({ label, value, compact = false }: { label: string; value: string; compact?: boolean }) {
	return (
		<div>
			<p className="mb-1.5 text-xs font-medium text-[var(--text-secondary)]">{label}</p>
			<div className={`whitespace-pre-wrap rounded-md border border-[var(--border)] bg-[var(--bg-card)] px-3 py-2 text-sm leading-relaxed text-[var(--text)] ${compact ? "min-h-20" : "min-h-32"}`}>
				{value}
			</div>
		</div>
	);
}

/** Render one provenance row, using monospace only for identifiers and serialized refs. */
function MemoryMetaField({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
	return (
		<div>
			<p className="mb-1.5 text-xs font-medium text-[var(--text-secondary)]">{label}</p>
			<p className={`min-h-10 break-words rounded-md border border-[var(--border)] bg-[var(--bg-card)] px-3 py-2 text-xs text-[var(--text-muted)] ${mono ? "font-mono" : ""}`}>
				{value}
			</p>
		</div>
	);
}

/** Pull structured memory fields into the detail modal when the API row has them. */
function memoryTypedFields(version: ContextRecordVersion): Array<{ label: string; value: string }> {
	return [
		{ label: "Decision", value: version.decision },
		{ label: "Why", value: version.why },
		{ label: "Alternatives", value: version.alternatives },
		{ label: "Consequences", value: version.consequences },
		{ label: "User intent", value: version.user_intent },
		{ label: "What happened", value: version.what_happened },
		{ label: "Outcomes", value: version.outcomes },
	].filter((field): field is { label: string; value: string } => Boolean(field.value));
}

/** Pull source and version metadata into the modal without overwhelming the header. */
function memoryProvenanceFields(version: ContextRecordVersion): Array<{ label: string; value: string; mono?: boolean }> {
	const fields = [
		{ label: "Record ID", value: version.record_id, mono: true },
		{ label: "Version ID", value: version.version_id, mono: true },
		{ label: "Source session", value: version.source_session_id, mono: true },
		{ label: "Changed by session", value: version.changed_by_session_id, mono: true },
		{ label: "Change reason", value: version.change_reason },
		{ label: "Source", value: version.source },
		{ label: "Created", value: version.created_at ? formatDateTime(version.created_at) : null },
		{ label: "Updated", value: version.updated_at ? formatDateTime(version.updated_at) : null },
		{ label: "Valid from", value: version.valid_from ? formatDateTime(version.valid_from) : null },
		{ label: "Valid until", value: version.valid_until ? formatDateTime(version.valid_until) : null },
		{ label: "Superseded by", value: version.superseded_by_record_id, mono: true },
		{ label: "Source event refs", value: version.source_event_refs, mono: true },
		{ label: "Evidence refs", value: version.evidence_refs, mono: true },
	];
	return fields
		.filter((field) => Boolean(field.value))
		.map((field) => ({
			label: field.label,
			value: String(field.value),
			mono: field.mono,
		}));
}

/** Build the ECharts config from already-aggregated memory points. */
function buildChartOption(points: MemoryPoint[]): EChartsOption {
	return {
		tooltip: {
			trigger: "axis",
			axisPointer: { type: "shadow" },
			formatter: (value: unknown) => formatTooltip(value, points),
		},
		legend: {
			top: 0,
			textStyle: { color: "#94a3b8", fontSize: 11 },
		},
		grid: { left: 12, right: 16, top: 56, bottom: 44, containLabel: true },
		xAxis: {
			type: "category",
			name: "Time",
			nameLocation: "middle",
			nameGap: 30,
			data: points.map((point) => point.label),
			axisLine: { lineStyle: { color: "rgba(255,255,255,0.06)" } },
			axisLabel: { color: "#94a3b8", fontSize: 11 },
			nameTextStyle: { color: "#64748b", fontSize: 11 },
		},
		yAxis: [
			{
				type: "value",
				name: "Changes",
				nameTextStyle: { color: "#94a3b8", fontSize: 11 },
				splitLine: { lineStyle: { color: "rgba(255,255,255,0.04)" } },
				axisLabel: { color: "#94a3b8", fontSize: 11 },
			},
			{
				type: "value",
				name: "Active",
				nameTextStyle: { color: "#94a3b8", fontSize: 11 },
				splitLine: { show: false },
				axisLabel: { color: "#94a3b8", fontSize: 11 },
			},
		],
		series: [
			{
				name: "New",
				type: "bar",
				stack: "changes",
				data: points.map((point) => point.created),
				itemStyle: { color: "#3b82f6", borderRadius: [4, 4, 0, 0] },
			},
			{
				name: "Updated",
				type: "bar",
				stack: "changes",
				data: points.map((point) => point.updated),
				itemStyle: { color: "#10b981" },
			},
			{
				name: "Archived",
				type: "bar",
				stack: "changes",
				data: points.map((point) => point.archived),
				itemStyle: { color: "#f59e0b" },
			},
			{
				name: "Active memories",
				type: "line",
				yAxisIndex: 1,
				smooth: true,
				data: points.map((point) => point.active),
				lineStyle: { color: "#e2e8f0", width: 2 },
				itemStyle: { color: "#e2e8f0" },
				symbol: "circle",
				symbolSize: 6,
			},
		],
	};
}

/** Aggregate record-version events into daily or weekly buckets with a running active count. */
function buildMemoryTimeline(
	versions: ContextRecordVersion[],
	scope: MemoryTimelineScope,
	project: string,
	kind: string,
): MemoryPoint[] {
	const filtered = versions
		.filter((version) => (!project || version.project === project) && (!kind || version.record_kind === kind))
		.map((version) => ({ version, changedAt: parseDate(version.changed_at) }))
		.filter((item): item is { version: ContextRecordVersion; changedAt: Date } => Boolean(item.changedAt))
		.sort((left, right) => left.changedAt.getTime() - right.changedAt.getTime());
	const buckets = buildBuckets(scope, filtered[0]?.changedAt);
	const firstStart = buckets[0]?.start.getTime() ?? 0;
	const pointByKey = new Map(buckets.map((point) => [point.key, point]));

	for (const { version, changedAt } of filtered) {
		if (changedAt.getTime() < firstStart) continue;
		const point = pointByKey.get(bucketKey(changedAt, scope));
		if (!point) continue;
		point.versions.push(version);
		if (version.change_kind === "create") point.created += 1;
		else if (version.change_kind === "archive" || version.change_kind === "supersede") point.archived += 1;
		else point.updated += 1;
	}

	const latestByRecord = new Map<string, ContextRecordVersion>();
	let cursor = 0;
	for (const point of buckets) {
		const bucketEnd = addDays(point.start, scope === "all" ? 7 : 1).getTime();
		while (cursor < filtered.length && filtered[cursor].changedAt.getTime() < bucketEnd) {
			latestByRecord.set(filtered[cursor].version.record_id, filtered[cursor].version);
			cursor += 1;
		}
		point.active = Array.from(latestByRecord.values()).filter(isActiveVersion).length;
	}
	return buckets;
}

/** Create empty day or week buckets so the x-axis stays stable even on quiet days. */
function buildBuckets(scope: MemoryTimelineScope, firstDate?: Date): MemoryPoint[] {
	const now = startOfUtcDay(new Date());
	const allStart = firstDate ? startOfUtcWeek(firstDate) : now;
	const start = scope === "week" ? addDays(now, -6) : scope === "month" ? addDays(now, -29) : allStart;
	const stepDays = scope === "all" ? 7 : 1;
	const points: MemoryPoint[] = [];
	for (let cursor = new Date(start); cursor <= now; cursor = addDays(cursor, stepDays)) {
		const key = bucketKey(cursor, scope);
		points.push({
			key,
			label: scope === "all" ? formatShortDate(cursor) : formatDayLabel(cursor),
			rangeLabel: scope === "all" ? `${formatShortDate(cursor)} - ${formatShortDate(addDays(cursor, 6))}` : formatLongDate(cursor),
			created: 0,
			updated: 0,
			archived: 0,
			active: 0,
			versions: [],
			start: new Date(cursor),
		});
	}
	return points;
}

/** Format the native ECharts tooltip payload into the sketchnote's daily summary. */
function formatTooltip(value: unknown, points: MemoryPoint[]): string {
	const rows = Array.isArray(value) ? value : [value];
	const first = rows[0] as { dataIndex?: number } | undefined;
	const point = typeof first?.dataIndex === "number" ? points[first.dataIndex] : undefined;
	if (!point) return "";
	return [
		`<strong>${point.rangeLabel}</strong>`,
		`${point.created} new`,
		`${point.updated} updated`,
		`${point.archived} archived`,
		`${point.active} active`,
	].join("<br/>");
}

/** Return compact unique string options from nullable API values. */
function uniqueSorted(values: Array<string | null | undefined>): string[] {
	return Array.from(new Set(values.filter((value): value is string => Boolean(value)))).sort();
}

/** Return whether a version represents an active record state at the end of a bucket. */
function isActiveVersion(version: ContextRecordVersion): boolean {
	return version.status !== "archived" && version.change_kind !== "archive" && version.change_kind !== "supersede";
}

/** Map raw change kinds to the concise visible labels used in the drilldown. */
function changeLabel(changeKind: string): string {
	if (changeKind === "create") return "New";
	if (changeKind === "archive" || changeKind === "supersede") return "Archived";
	return "Updated";
}

/** Pick a quiet badge color that matches the chart series. */
function changeBadgeClass(changeKind: string): string {
	if (changeKind === "create") return "bg-blue-500/15 text-blue-300";
	if (changeKind === "archive" || changeKind === "supersede") return "bg-amber-500/15 text-amber-300";
	return "bg-emerald-500/15 text-emerald-300";
}

/** Parse a timestamp defensively without inventing a fallback date. */
function parseDate(value?: string | null): Date | null {
	const timestamp = Date.parse(String(value || ""));
	return Number.isFinite(timestamp) ? new Date(timestamp) : null;
}

/** Resolve the chart bucket key for the requested aggregation level. */
function bucketKey(date: Date, scope: MemoryTimelineScope): string {
	const bucket = scope === "all" ? startOfUtcWeek(date) : startOfUtcDay(date);
	return bucket.toISOString().slice(0, 10);
}

/** Return UTC midnight for date-only chart grouping. */
function startOfUtcDay(date: Date): Date {
	return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
}

/** Return Monday UTC for weekly all-time chart grouping. */
function startOfUtcWeek(date: Date): Date {
	const day = startOfUtcDay(date);
	const offset = (day.getUTCDay() + 6) % 7;
	return addDays(day, -offset);
}

/** Add days without drifting across local timezone boundaries. */
function addDays(date: Date, days: number): Date {
	return new Date(date.getTime() + days * 24 * 60 * 60 * 1000);
}

/** Format compact date labels for chart axes and weekly ranges. */
function formatShortDate(date: Date): string {
	return date.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
}

/** Format day labels for week and month scopes. */
function formatDayLabel(date: Date): string {
	return date.toLocaleDateString(undefined, { weekday: "short", day: "numeric", timeZone: "UTC" });
}

/** Format selected daily drilldown headers. */
function formatLongDate(date: Date): string {
	return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

/** Format timestamps for detail views while preserving invalid raw values for debugging. */
function formatDateTime(value?: string | null): string {
	if (!value) return "unknown";
	const timestamp = Date.parse(value);
	if (!Number.isFinite(timestamp)) return value;
	return new Date(timestamp).toLocaleString(undefined, {
		month: "short",
		day: "numeric",
		year: "numeric",
		hour: "numeric",
		minute: "2-digit",
		timeZone: "UTC",
		timeZoneName: "short",
	});
}
