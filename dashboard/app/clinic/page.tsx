"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useProjectScope } from "@/lib/projectScope";
import type {
	RunClinicAction,
	RunClinicArtifact,
	RunClinicFinding,
	RunClinicMetrics,
	RunClinicResponse,
	RunClinicVersion,
} from "@/lib/types";
import ProjectScope from "@/components/ProjectScope";

const STAGES = ["scope", "execution", "debugging", "verification", "handoff", "context"];

export default function ClinicPage() {
	return (
		<Suspense fallback={<div className="text-sm text-[var(--text-muted)]">Loading…</div>}>
			<ClinicContent />
		</Suspense>
	);
}

function ClinicContent() {
	const [data, setData] = useState<RunClinicResponse | null>(null);
	const { project, setProject } = useProjectScope();
	const [activeVersionId, setActiveVersionId] = useState("");
	const [loading, setLoading] = useState(true);
	const [error, setError] = useState<string | null>(null);
	const loadSeqRef = useRef(0);

	const load = useCallback(async (selectedProject?: string) => {
		const seq = loadSeqRef.current + 1;
		loadSeqRef.current = seq;
		setLoading(true);
		setError(null);
		try {
			const payload = await api.getRunClinic(selectedProject || project || undefined);
			if (seq !== loadSeqRef.current) return;
			setData(payload);
			if (!project && payload.selected_project) setProject(payload.selected_project);
			setActiveVersionId("");
		} catch (err) {
			if (seq === loadSeqRef.current) {
				setData(null);
				setActiveVersionId("");
				setError(err instanceof Error ? err.message : "Failed to load Run Clinic");
			}
		} finally {
			if (seq === loadSeqRef.current) setLoading(false);
		}
	}, [project, setProject]);

	useEffect(() => {
		load();
	}, [load]);

	const artifact = data?.artifact || null;
	const activeVersion = selectedClinicVersion(artifact, activeVersionId);
	const report = activeVersion?.report || {};
	const metrics = report.metrics || emptyMetrics();
	const findings = Array.isArray(report.findings) ? report.findings : [];
	const actions = Array.isArray(report.recommended_actions) ? report.recommended_actions : [];

	return (
		<>
			<div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
				<div>
					<h1 className="text-lg font-semibold text-[var(--text)]">Clinic</h1>
					<p className="mt-0.5 text-xs text-[var(--text-muted)]">
						Project diagnosis from persisted context patterns, activity, and evidence density
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

			{loading && !data ? (
				<div className="mt-8 grid gap-3 lg:grid-cols-3">
					<div className="h-56 animate-pulse rounded-lg border border-[var(--border)] bg-white/[0.03]" />
					<div className="h-56 animate-pulse rounded-lg border border-[var(--border)] bg-white/[0.03] lg:col-span-2" />
				</div>
			) : artifact && activeVersion ? (
				<div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_18rem]">
					<div className="min-w-0 space-y-4">
						<ClinicHero
							data={data}
							artifact={artifact}
							version={activeVersion}
							headline={String(report.headline || "No Clinic diagnosis generated yet.")}
							summary={Array.isArray(report.summary) ? report.summary : []}
							metrics={metrics}
						/>

						<div className="grid gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
							<ReadinessPanel
								score={numberOrZero(report.readiness_score ?? metrics.readiness_score)}
								metrics={metrics}
							/>
							<EvidencePanel metrics={metrics} />
						</div>

						<div className="grid gap-4 xl:grid-cols-2">
							<StageMap metrics={metrics} />
							<ChangePulse metrics={metrics} />
						</div>

						<div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
							<FindingsPanel findings={findings} />
							<ActionsPanel actions={actions} />
						</div>

						<MarkdownPanel content={activeVersion.content} />
					</div>

					<VersionRail
						versions={artifact.versions}
						activeVersionId={activeVersion.id}
						onSelect={setActiveVersionId}
					/>
				</div>
			) : (
				<div className="mt-8 rounded-lg border border-[var(--border)] bg-[var(--bg-card)] px-6 py-10 text-center">
					<p className="text-sm font-medium text-[var(--text)]">No Clinic generated yet.</p>
					<p className="mt-1 text-xs text-[var(--text-muted)]">
						Run `lerim clinic refresh` for this project to create the first diagnostic artifact.
					</p>
				</div>
			)}
		</>
	);
}

function ClinicHero({
	data,
	artifact,
	version,
	headline,
	summary,
	metrics,
}: {
	data: RunClinicResponse | null;
	artifact: RunClinicArtifact;
	version: RunClinicVersion;
	headline: string;
	summary: string[];
	metrics: RunClinicMetrics;
}) {
	const activeRecords = numberOrZero(data?.active_record_count ?? metrics.active_records_total);
	const totalRecords = numberOrZero(data?.total_record_count ?? metrics.all_records_total ?? activeRecords);
	const archivedRecords = numberOrZero(data?.archived_record_count ?? metrics.archived_records_total);
	const changed = numberOrZero(artifact.status.records_changed_since_generation);
	return (
		<section className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4">
			<div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-start">
				<div className="min-w-0">
					<div className="flex flex-wrap items-center gap-2">
						<span className="rounded-md bg-[var(--accent-blue)]/15 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-[var(--accent-blue)]">
							Run Clinic
						</span>
						<span className="rounded-md border border-[var(--border)] px-2 py-1 text-[10px] font-medium uppercase tracking-wide text-[var(--text-secondary)]">
							{version.current ? "Latest" : "Selected version"}
						</span>
						<Freshness availability={artifact.status.availability} />
					</div>
					<h2 className="mt-3 text-base font-semibold leading-6 text-[var(--text)]">{headline}</h2>
					<div className="mt-3 grid gap-2">
						{summary.length ? summary.slice(0, 3).map((line, index) => (
							<p key={index} className="text-xs leading-5 text-[var(--text-secondary)]">{line}</p>
						)) : (
							<p className="text-xs leading-5 text-[var(--text-muted)]">No generated summary lines yet.</p>
						)}
					</div>
					{changed > 0 && (
						<p className="mt-3 rounded-md border border-amber-400/20 bg-amber-400/10 px-3 py-2 text-xs leading-5 text-amber-200">
							{changed.toLocaleString()} record changes since this Clinic was generated. {artifact.status.suggested_action}
						</p>
					)}
				</div>
				<div className="grid grid-cols-2 gap-2 text-xs text-[var(--text-secondary)] lg:w-72 lg:grid-cols-1">
					<Meta label="Generated" value={formatDateTime(version.generated_at)} />
					<Meta label="Window" value={`${version.window_days || 30} days`} />
					<Meta label="Current Records" value={`${activeRecords.toLocaleString()} active / ${totalRecords.toLocaleString()} total`} />
					<Meta label="Archived" value={archivedRecords.toLocaleString()} />
					<Meta label="Clinic Evidence" value={`${version.records_included} cited / ${version.records_considered} active sampled`} />
					<Meta label="Project" value={data?.selected_project || "Project"} />
				</div>
			</div>
			<p className="mt-4 truncate text-[11px] text-[var(--text-muted)]">{data?.repo_path || ""}</p>
		</section>
	);
}

function ReadinessPanel({ score, metrics }: { score: number; metrics: RunClinicMetrics }) {
	const clamped = Math.max(0, Math.min(100, score));
	const circumference = 2 * Math.PI * 44;
	const offset = circumference - (clamped / 100) * circumference;
	const verdict = readinessVerdict(clamped, metrics);
	return (
		<section className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4">
			<h2 className="text-sm font-semibold text-[var(--text)]">Clinic Readiness</h2>
			<div className="mt-5 flex items-center justify-center">
				<div className="relative h-32 w-32">
					<svg className="h-32 w-32 -rotate-90" viewBox="0 0 112 112">
						<circle cx="56" cy="56" r="44" stroke="rgba(255,255,255,0.08)" strokeWidth="10" fill="none" />
						<circle
							cx="56"
							cy="56"
							r="44"
							stroke="var(--accent-teal)"
							strokeWidth="10"
							fill="none"
							strokeLinecap="round"
							strokeDasharray={circumference}
							strokeDashoffset={offset}
						/>
					</svg>
					<div className="absolute inset-0 flex flex-col items-center justify-center">
						<span className="text-3xl font-semibold tabular-nums text-[var(--text)]">{clamped}</span>
						<span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">score</span>
					</div>
				</div>
			</div>
			<div className="mt-4 rounded-lg border border-[var(--border)] bg-[#0b1220]/70 p-3">
				<p className="text-xs font-semibold text-[var(--text)]">{verdict.title}</p>
				<p className="mt-1 text-[11px] leading-4 text-[var(--text-muted)]">{verdict.detail}</p>
			</div>
		</section>
	);
}

function EvidencePanel({ metrics }: { metrics: RunClinicMetrics }) {
	const totals = [
		["Active records", metrics.active_records_sampled, metrics.active_records_total, "sampled"],
		["Historical records", metrics.archived_records_total || 0, metrics.all_records_total || metrics.active_records_total, "archived"],
		["Versions", metrics.recent_versions_sampled, metrics.recent_versions_total, "sampled"],
		["Sessions", metrics.recent_sessions_sampled, metrics.recent_sessions_total, "sampled"],
	];
	return (
		<section className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4">
			<h2 className="text-sm font-semibold text-[var(--text)]">Evidence Mix</h2>
			<div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
				{totals.map(([label, sampled, total, unit]) => (
					<div key={String(label)} className="rounded-lg border border-[var(--border)] bg-[#0b1220]/80 p-3">
						<p className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">{label}</p>
						<p className="mt-2 text-2xl font-semibold tabular-nums text-[var(--text)]">{Number(sampled)}</p>
						<p className="mt-1 text-[11px] text-[var(--text-muted)]">
							{Number(total)} total · {String(unit)}
						</p>
					</div>
				))}
			</div>
			<div className="mt-4 grid gap-2">
				<CountBars title="Record kinds" values={metrics.kind_counts} />
				<CountBars title="Operational roles" values={metrics.role_counts} />
			</div>
		</section>
	);
}

function StageMap({ metrics }: { metrics: RunClinicMetrics }) {
	const maxValue = Math.max(1, ...Object.values(metrics.stage_scores || {}));
	return (
		<section className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4">
			<h2 className="text-sm font-semibold text-[var(--text)]">Friction Map</h2>
			<div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3">
				{STAGES.map((stage) => {
					const value = Number(metrics.stage_scores?.[stage] || 0);
					const intensity = value / maxValue;
					return (
						<div
							key={stage}
							className="min-h-24 rounded-lg border border-[var(--border)] p-3"
							style={{ backgroundColor: `rgba(45, 212, 191, ${0.04 + intensity * 0.16})` }}
						>
							<p className="text-xs font-medium capitalize text-[var(--text)]">{stage}</p>
							<p className="mt-5 text-2xl font-semibold tabular-nums text-[var(--text)]">{value}</p>
							<p className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">signals</p>
						</div>
					);
				})}
			</div>
		</section>
	);
}

function ChangePulse({ metrics }: { metrics: RunClinicMetrics }) {
	const days = metrics.changes_by_day || [];
	const maxValue = Math.max(1, ...days.map((item) => Number(item.changes || 0)));
	return (
		<section className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4">
			<h2 className="text-sm font-semibold text-[var(--text)]">Change Pulse</h2>
			<div className="mt-4 flex h-40 items-end gap-1 rounded-lg border border-[var(--border)] bg-[#0b1220]/70 p-3">
				{days.length ? days.slice(-30).map((item) => (
					<div key={item.date} className="flex min-w-0 flex-1 flex-col items-center justify-end gap-2">
						<div
							title={`${item.date}: ${item.changes}`}
							className="w-full rounded-t bg-[var(--accent-blue)]/75"
							style={{ height: `${Math.max(8, (Number(item.changes || 0) / maxValue) * 112)}px` }}
						/>
					</div>
				)) : (
					<p className="self-center text-xs text-[var(--text-muted)]">No recent record versions in the Clinic window.</p>
				)}
			</div>
			<div className="mt-3 grid grid-cols-4 gap-2 text-xs">
				<MiniStat label="Messages" value={metrics.session_totals.messages} />
				<MiniStat label="Tools" value={metrics.session_totals.tool_calls} />
				<MiniStat label="Errors" value={metrics.session_totals.errors} />
				<MiniStat label="Tokens" value={metrics.session_totals.tokens} compact />
			</div>
		</section>
	);
}

function FindingsPanel({ findings }: { findings: RunClinicFinding[] }) {
	return (
		<section className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4">
			<h2 className="text-sm font-semibold text-[var(--text)]">Diagnosis</h2>
			<div className="mt-4 space-y-3">
				{findings.length ? findings.map((finding, index) => (
					<article key={`${finding.title}-${index}`} className="rounded-lg border border-[var(--border)] bg-[#0b1220]/80 p-3">
						<div className="flex flex-wrap items-center gap-2">
							<Badge tone={finding.severity}>{finding.severity || "medium"}</Badge>
							<span className="rounded-md border border-[var(--border)] px-2 py-1 text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
								{finding.pattern_type || "pattern"}
							</span>
						</div>
						<h3 className="mt-3 text-sm font-semibold text-[var(--text)]">{finding.title}</h3>
						<p className="mt-2 text-xs leading-5 text-[var(--text-secondary)]">{finding.summary}</p>
						<p className="mt-2 text-xs leading-5 text-[var(--text-muted)]">{finding.why_it_matters}</p>
						<RecordIds ids={finding.evidence_record_ids} />
					</article>
				)) : (
					<p className="text-xs leading-5 text-[var(--text-muted)]">No findings generated yet.</p>
				)}
			</div>
		</section>
	);
}

function ActionsPanel({ actions }: { actions: RunClinicAction[] }) {
	const instrumentation = actions.filter(isInstrumentationAction);
	const projectMoves = actions.filter((action) => !isInstrumentationAction(action));
	return (
		<section className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4">
			<h2 className="text-sm font-semibold text-[var(--text)]">Recommended Moves</h2>
			<div className="mt-4 space-y-4">
				{actions.length ? (
					<>
						<ActionGroup title="Context & Memory" actions={projectMoves} />
						<ActionGroup title="Measurement & Instrumentation" actions={instrumentation} />
					</>
				) : (
					<p className="text-xs leading-5 text-[var(--text-muted)]">No recommended moves generated yet.</p>
				)}
			</div>
		</section>
	);
}

function ActionGroup({ title, actions }: { title: string; actions: RunClinicAction[] }) {
	if (!actions.length) return null;
	return (
		<div>
			<p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-[var(--text-muted)]">{title}</p>
			<div className="space-y-3">
				{actions.map((action, index) => (
					<article key={`${title}-${action.title}-${index}`} className="rounded-lg border border-[var(--border)] bg-[#0b1220]/80 p-3">
						<div className="flex flex-wrap items-center gap-2">
							<Badge tone={action.priority}>{action.priority || "medium"}</Badge>
							<span className="rounded-md border border-[var(--border)] px-2 py-1 text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
								{action.action_type || "workflow"}
							</span>
						</div>
						<h3 className="mt-3 text-sm font-semibold text-[var(--text)]">{action.title}</h3>
						<p className="mt-2 text-xs leading-5 text-[var(--text-secondary)]">{action.summary}</p>
						<RecordIds ids={action.evidence_record_ids} />
					</article>
				))}
			</div>
		</div>
	);
}

function MarkdownPanel({ content }: { content: string }) {
	return (
		<section className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4">
			<h2 className="text-sm font-semibold text-[var(--text)]">Artifact Markdown</h2>
			<pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap rounded-lg border border-[var(--border)] bg-[#0b1220]/80 p-3 text-xs leading-5 text-[var(--text-secondary)]">
				{content || "No markdown generated yet."}
			</pre>
		</section>
	);
}

function VersionRail({
	versions,
	activeVersionId,
	onSelect,
}: {
	versions: RunClinicVersion[];
	activeVersionId: string;
	onSelect: (id: string) => void;
}) {
	return (
		<aside className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] xl:sticky xl:top-8 xl:self-start">
			<div className="border-b border-[var(--border)] p-4">
				<h2 className="text-sm font-semibold text-[var(--text)]">Clinic History</h2>
				<p className="mt-1 text-xs text-[var(--text-muted)]">Prior diagnostic artifacts</p>
			</div>
			<div className="max-h-[46rem] space-y-2 overflow-auto p-3">
				{versions.length ? versions.map((version) => (
					<button
						key={`${version.id}-${version.content_path}`}
						type="button"
						onClick={() => onSelect(version.id)}
						className={`w-full rounded-lg border p-3 text-left transition-colors ${activeVersionId === version.id ? "border-[var(--accent-blue)]/40 bg-[var(--accent-blue)]/10" : "border-[var(--border)] bg-[#0b1220]/70 hover:border-white/15"}`}
					>
						<div className="flex items-center justify-between gap-2">
							<span className="rounded-md bg-[var(--accent-blue)]/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--accent-blue)]">
								Clinic
							</span>
							{version.current && <span className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">Latest</span>}
						</div>
						<p className="mt-2 text-xs font-medium text-[var(--text)]">{formatDateTime(version.generated_at)}</p>
						<p className="mt-1 text-[11px] text-[var(--text-muted)]">
							{version.records_included} cited / {version.records_considered} sampled · {version.trigger || "unknown"}
						</p>
					</button>
				)) : (
					<p className="p-3 text-xs leading-5 text-[var(--text-muted)]">No generated Clinic versions found yet.</p>
				)}
			</div>
		</aside>
	);
}

function CountBars({ title, values }: { title: string; values: Record<string, number> }) {
	const entries = Object.entries(values || {}).sort((a, b) => b[1] - a[1]).slice(0, 6);
	const maxValue = Math.max(1, ...entries.map(([, value]) => value));
	return (
		<div className="rounded-lg border border-[var(--border)] bg-[#0b1220]/70 p-3">
			<p className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">{title}</p>
			<div className="mt-3 grid gap-2">
				{entries.length ? entries.map(([label, value]) => (
					<div key={label} className="grid grid-cols-[6rem_minmax(0,1fr)_2rem] items-center gap-2">
						<span className="truncate text-[11px] text-[var(--text-secondary)]">{label}</span>
						<div className="h-2 overflow-hidden rounded-full bg-white/[0.06]">
							<div className="h-full rounded-full bg-[var(--accent-teal)]" style={{ width: `${(value / maxValue) * 100}%` }} />
						</div>
						<span className="text-right text-[11px] tabular-nums text-[var(--text-muted)]">{value}</span>
					</div>
				)) : (
					<p className="text-[11px] text-[var(--text-muted)]">No values yet.</p>
				)}
			</div>
		</div>
	);
}

function MiniStat({ label, value, compact }: { label: string; value: number; compact?: boolean }) {
	return (
		<div className="rounded-md bg-white/[0.025] px-2 py-2">
			<div className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">{label}</div>
			<div className="mt-1 truncate text-xs font-medium tabular-nums text-[var(--text-secondary)]">
				{compact ? formatCompact(value) : value}
			</div>
		</div>
	);
}

function Meta({ label, value }: { label: string; value: string }) {
	return (
		<div className="rounded-md bg-white/[0.025] px-3 py-2">
			<div className="text-[10px] uppercase tracking-wide text-[var(--text-muted)]">{label}</div>
			<div className="mt-1 truncate text-xs font-medium text-[var(--text-secondary)]">{value}</div>
		</div>
	);
}

function Freshness({ availability }: { availability: string }) {
	const current = availability === "available";
	return (
		<span className={`rounded-md px-2 py-1 text-[10px] font-semibold uppercase tracking-wide ${current ? "bg-[var(--accent-teal)]/15 text-[var(--accent-teal)]" : "bg-amber-400/10 text-amber-300"}`}>
			{availability || "missing"}
		</span>
	);
}

function Badge({ tone, children }: { tone: string; children: string }) {
	const color = tone === "high"
		? "bg-red-500/10 text-red-300"
		: tone === "low"
			? "bg-[var(--accent-teal)]/15 text-[var(--accent-teal)]"
			: "bg-amber-400/10 text-amber-300";
	return (
		<span className={`rounded-md px-2 py-1 text-[10px] font-semibold uppercase tracking-wide ${color}`}>
			{children}
		</span>
	);
}

function RecordIds({ ids }: { ids: string[] }) {
	if (!ids?.length) return null;
	return (
		<div className="mt-3 flex flex-wrap gap-1">
			{ids.slice(0, 6).map((id) => (
				<span key={id} className="rounded-md bg-white/[0.04] px-2 py-1 font-mono text-[10px] text-[var(--text-muted)]">
					{id}
				</span>
			))}
		</div>
	);
}

function selectedClinicVersion(artifact: RunClinicArtifact | null, versionId: string) {
	if (!artifact) return undefined;
	return artifact.versions.find((version) => version.id === versionId) || artifact.current;
}

function readinessVerdict(score: number, metrics: RunClinicMetrics) {
	if (score <= 0) {
		return {
			title: "No diagnosis yet",
			detail: "Clinic needs persisted records before the score becomes useful.",
		};
	}
	if (score < 50) {
		return {
			title: "Directional only",
			detail: "Evidence is too thin for confident project diagnosis.",
		};
	}
	if (score < 75) {
		const reason = metrics.recent_sessions_total > 0 && metrics.session_totals.messages === 0
			? "measurement gaps"
			: "sparse evidence";
		return {
			title: "Usable, but limited",
			detail: `Treat the diagnosis as useful but constrained by ${reason}.`,
		};
	}
	return {
		title: "Ready for planning",
		detail: "Evidence coverage is strong enough for improvement planning.",
	};
}

function isInstrumentationAction(action: RunClinicAction) {
	const type = String(action.action_type || "").toLowerCase();
	return ["workflow", "eval_asset", "instrumentation", "telemetry", "measurement"].includes(type);
}

function numberOrZero(value: unknown) {
	const numberValue = Number(value);
	return Number.isFinite(numberValue) ? numberValue : 0;
}

function emptyMetrics(): RunClinicMetrics {
	return {
		active_records_sampled: 0,
		active_records_total: 0,
		archived_records_total: 0,
		all_records_total: 0,
		recent_versions_sampled: 0,
		recent_versions_total: 0,
		recent_sessions_sampled: 0,
		recent_sessions_total: 0,
		evidence_items: 0,
		readiness_score: 0,
		kind_counts: {},
		role_counts: {},
		stage_scores: {},
		changes_by_day: [],
		session_totals: { messages: 0, tool_calls: 0, errors: 0, tokens: 0 },
	};
}

function formatDateTime(value?: string) {
	if (!value) return "not generated";
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return value;
	return date.toLocaleString(undefined, {
		month: "short",
		day: "numeric",
		hour: "2-digit",
		minute: "2-digit",
	});
}

function formatCompact(value: number) {
	return Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(value || 0);
}
