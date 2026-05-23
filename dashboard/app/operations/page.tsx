"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";
import { formatRecordKind, formatScopeLabel, formatStatusLabel } from "@/lib/labels";
import type {
  PipelineStatusResponse,
  PipelineReportResponse,
  TimelineResponse,
  TimelineDay,
  TimelineEvent,
  TimelineRecord,
  TimelineOperation,
  ContextRecord,
  LogEntry,
} from "@/lib/types";
import RecordEditor from "@/components/RecordEditor";
import OperationDetailModal from "@/components/OperationDetailModal";
import LiveStatus from "@/components/LiveStatus";
import LogViewer from "@/components/LogViewer";
import { useToast } from "@/components/Toast";

/** Same as LiveStatus poll, keeping operation status aligned after ingest. */
const PIPELINE_POLL_MS = 3000;

/* ====================================================================
   Operations Page
   ==================================================================== */

export default function OperationsPage() {
  const { addToast } = useToast();
  const [status, setStatus] = useState<PipelineStatusResponse | null>(null);
  const [report, setReport] = useState<PipelineReportResponse | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [selectedRecord, setSelectedRecord] = useState<ContextRecord | null>(null);
  const [selectedOperation, setSelectedOperation] = useState<TimelineOperation | null>(null);
  const [timeline, setTimeline] = useState<TimelineResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [recordFilter, setRecordFilter] = useState<"all" | "active" | "archived">("all");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [statusData, timelineData, reportData, logData] = await Promise.all([
        api.getPipelineStatus(),
        api.getTimeline(14).catch(() => null),
        api.getPipelineReport().catch(() => null),
        api.getLogs({ limit: "20" }).catch(() => ({ logs: [], total: 0 })),
      ]);
      setStatus(statusData);
      setTimeline(timelineData);
      setReport(reportData);
      setLogs(logData.logs);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load operations");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const [timelineData, statusData, logData] = await Promise.all([
          api.getTimeline(14).catch(() => null),
          api.getPipelineStatus(),
          api.getLogs({ limit: "20" }).catch(() => ({ logs: [], total: 0 })),
        ]);
        if (!cancelled) {
          setTimeline(timelineData);
          setStatus(statusData);
          setLogs(logData.logs);
        }
      } catch {
        /* ignore */
      }
    };
    const id = setInterval(poll, PIPELINE_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  /* Open record in modal */
  const openRecord = useCallback(async (recordId: string) => {
    try {
      const found = await api.getRecord(recordId);
      if (found) setSelectedRecord(found);
      else addToast({ type: "error", message: "Record not found" });
    } catch {
      addToast({ type: "error", message: "Failed to load record" });
    }
  }, [addToast]);

  const errorHealth = getErrorHealth(status);

  /* Find the first event index for the pulse */
  const firstDayIndex = timeline && timeline.days.length > 0 ? 0 : -1;

  /* Extract latest ingest/curate runs from timeline. */
  const latestOps = getLatestOperations(timeline);

  /* Apply record status filter to timeline data */
  const filteredTimeline = timeline ? {
    ...timeline,
    days: timeline.days.map(day => {
      const filteredEvents = day.events.map(event => {
        const filteredRecords = event.records.filter(record => {
          if (recordFilter === "all") return true;
          if (recordFilter === "active") return record.action !== "archived";
          return record.action === "archived";
        });
        return {
          ...event,
          records: filteredRecords,
          records_new: filteredRecords.filter(record => record.action === "new").length,
          records_updated: filteredRecords.filter(record => record.action === "updated").length,
          records_archived: filteredRecords.filter(record => record.action === "archived").length,
        };
      });
      return {
        ...day,
        events: filteredEvents,
        summary: {
          ...day.summary,
          records_new: filteredEvents.reduce((sum, e) => sum + e.records_new, 0),
          records_updated: filteredEvents.reduce((sum, e) => sum + e.records_updated, 0),
          records_archived: filteredEvents.reduce((sum, e) => sum + e.records_archived, 0),
        },
      };
    }),
  } : null;

  return (
    <>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-[var(--text)]">Operations</h1>
          <p className="mt-0.5 text-xs text-[var(--text-muted)]">
            Runtime health, ingest history, and curation runs
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="rounded-md border border-[var(--border)] px-3 py-1.5 text-xs text-[var(--text-secondary)] transition-colors hover:text-[var(--text)] disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-red-500/20 bg-red-500/5 px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      {loading && !status ? (
        <div className="mt-8 flex flex-col items-center gap-3 text-sm text-[var(--text-muted)]">
          <div className="space-y-3 w-full max-w-md">
            <div className="h-10 animate-pulse rounded-lg bg-white/[0.04]" />
            <div className="h-10 animate-pulse rounded-lg bg-white/[0.04]" />
            <div className="h-6 animate-pulse rounded-lg bg-white/[0.04]" />
          </div>
          <span>Loading operations data…</span>
        </div>
      ) : status ? (
        <>
          {/* -- Compact Health + Live Status ----------------------------- */}
          <div className="mt-4">
            <LiveStatus />
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-[var(--border)] bg-[var(--bg-card)] px-4 py-2.5 text-xs text-[var(--text-secondary)]">
            <span>
              <span className="font-medium tabular-nums text-[var(--text)]">
                {status.sessions.total.toLocaleString()}
              </span>{" "}
              sessions
            </span>
            <Divider />
            <span>
              <span className="font-medium tabular-nums text-[var(--text)]">
                {status.records.total.toLocaleString()}
              </span>{" "}
              records
            </span>
            <Divider />
            <span className="inline-flex items-center gap-1.5">
              {status.logs.errors > 0 && <HealthDot health={errorHealth} size={6} />}
              <span
                className={
                  status.logs.errors > 0
                    ? "font-medium tabular-nums text-red-400"
                    : "font-medium tabular-nums text-[var(--text)]"
                }
              >
                {status.logs.errors}
              </span>{" "}
              errors
            </span>
          </div>

          {/* -- Operation Sparkline (14-day) ----------------------------- */}
          {timeline && timeline.days.length > 0 && (
            <OperationSparkline days={timeline.days} />
          )}

          {/* -- Record Status Filter Pills ------------------------------- */}
          <div className="mt-6 flex gap-1 mb-2">
            {(["all", "active", "archived"] as const).map(f => (
              <button key={f} onClick={() => setRecordFilter(f)}
                className={`rounded-md px-3 py-1 text-xs capitalize transition-colors ${
                  recordFilter === f ? "bg-[var(--accent-blue)]/15 text-[var(--accent-blue)]" : "text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
                }`}>{f}</button>
            ))}
          </div>

          {/* -- Operation Timeline -------------------------------------- */}
          <section className="mt-2">
            <h2 className="mb-4 text-sm font-medium text-[var(--text-secondary)]">
              Operation Timeline
            </h2>
            {filteredTimeline && filteredTimeline.days.length > 0 ? (
              <div className="space-y-6">
                {filteredTimeline.days.map((day, dayIdx) => (
                  <TimelineDaySection
                    key={day.date}
                    day={day}
                    isFirstDay={dayIdx === firstDayIndex}
                    onRecordClick={(recordId) => openRecord(recordId)}
                    onOperationClick={setSelectedOperation}
                  />
                ))}
              </div>
            ) : (
              <EmptyState message="No operations yet" />
            )}
          </section>

          {/* -- Compiler Report -------------------------------------- */}
          {report && (
            <section className="mt-6">
              <h2 className="mb-4 text-sm font-medium text-[var(--text-secondary)]">
                Compiler Report
              </h2>
              <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4">
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                  <ReportMetric label="Sessions" value={report.sessions} />
                  <ReportMetric label="Messages" value={report.messages.toLocaleString()} />
                  <ReportMetric label="Tool Calls" value={report.tool_calls.toLocaleString()} />
                  <ReportMetric label="Errors" value={report.errors} accent={report.errors > 0 ? "red" : undefined} />
                  <ReportMetric label="Tokens" value={formatTokens(report.tokens)} />
                  <ReportMetric label="Days Active" value={report.days_span} />
                  <ReportMetric label="Msgs / Day" value={report.messages_per_day} />
                  <ReportMetric label="Records" value={`${report.records_active} active · ${report.records_archived} archived`} />
                </div>

              </div>
            </section>
          )}

          {/* -- Latest Compiler Jobs --------------------------------- */}
          {(latestOps.ingest || latestOps.curate) && (
            <section className="mt-6">
              <h2 className="mb-4 text-sm font-medium text-[var(--text-secondary)]">
                Latest Compiler Jobs
              </h2>
              <div className="grid gap-3 sm:grid-cols-2">
                <RunControlCard
                  type="ingest"
                  run={latestOps.ingest ? { status: latestOps.ingest.status, time: latestOps.ingest.started_at, duration_ms: latestOps.ingest.duration_ms } : null}
                  description="Reads source runs and writes durable context records"
                  command="lerim ingest"
                />
                <RunControlCard
                  type="curate"
                  run={latestOps.curate ? { status: latestOps.curate.status, time: latestOps.curate.started_at, duration_ms: latestOps.curate.duration_ms } : null}
                  description="Refreshes context quality and links related records"
                  command="lerim curate"
                />
              </div>
            </section>
          )}

          <section className="mt-6">
            <h2 className="mb-4 text-sm font-medium text-[var(--text-secondary)]">
              Runtime Logs
            </h2>
            <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-3">
              {logs.length > 0 ? (
                <LogViewer logs={logs} />
              ) : (
                <EmptyState message="No logs recorded yet" />
              )}
            </div>
          </section>

        </>
      ) : null}

      {/* Record detail modal */}
      {selectedRecord && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto overscroll-contain bg-black/60 p-4 backdrop-blur-sm"
          onClick={() => setSelectedRecord(null)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="record-editor-title"
            className="relative max-h-[calc(100vh-2rem)] w-full max-w-2xl overflow-y-auto rounded-xl border border-[var(--border)] bg-[var(--bg-subtle)] p-6 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              onClick={() => setSelectedRecord(null)}
              aria-label="Close"
              className="absolute right-4 top-4 flex min-h-11 min-w-11 items-center justify-center rounded-md text-[var(--text-muted)] transition-colors hover:text-[var(--text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
            >
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
            <RecordEditor
              key={selectedRecord.record_id}
              record={selectedRecord}
            />
          </div>
        </div>
      )}

      {/* Operation detail modal */}
      {selectedOperation && (
        <OperationDetailModal
          operation={selectedOperation}
          onClose={() => setSelectedOperation(null)}
        />
      )}
    </>
  );
}

/* ====================================================================
   Sub-components
   ==================================================================== */

/* ---- Compiler Report helpers --------------------------------------- */

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function ReportMetric({
  label,
  value,
  accent,
}: {
  label: string;
  value: string | number;
  accent?: "red";
}) {
  return (
    <div className="rounded-md bg-[var(--bg-subtle)] px-3 py-2">
      <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
        {label}
      </div>
      <div
        className={`mt-0.5 text-lg font-semibold ${
          accent === "red" ? "text-red-400" : "text-[var(--text)]"
        }`}
      >
        {value}
      </div>
    </div>
  );
}

function formatRelative(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

/* ---- Latest Run Card ----------------------------------------------- */

function RunControlCard({
  type,
  run,
  description,
  command,
}: {
  type: "ingest" | "curate";
  run: { status: string; time: string | null; duration_ms: number | null } | null;
  description: string;
  command: string;
}) {
  const isIngest = type === "ingest";
  const label = isIngest ? "Ingest" : "Curate";
  const accentColor = isIngest ? "#3b82f6" : "#8b5cf6";

  const statusOk = run?.status === "ok" || run?.status === "success" || run?.status === "completed";
  const statusFailed = run?.status === "failed";
  const dotColor = !run ? "bg-gray-500" : statusOk ? "bg-emerald-400" : statusFailed ? "bg-red-400" : "bg-amber-400";
  const statusLabel = !run ? "Never run" : run.status;
  const timeAgo = run?.time ? formatRelative(run.time) : null;
  const dur = run?.duration_ms != null ? `${(run.duration_ms / 1000).toFixed(1)}s` : null;

  return (
    <div
      className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4"
      style={{ borderLeftWidth: 3, borderLeftColor: accentColor }}
    >
      <div className="flex items-center gap-2">
        <span className={`inline-block h-2.5 w-2.5 rounded-full ${dotColor}`} />
        <span className="text-sm font-semibold text-[var(--text)]">{label}</span>
        <span className={`text-xs ${statusFailed ? "text-red-400" : statusOk ? "text-emerald-400" : "text-[var(--text-muted)]"}`}>
          {statusLabel}
        </span>
      </div>

      <p className="mt-1.5 text-xs text-[var(--text-muted)]">{description}</p>

      {run && (
        <div className="mt-2.5 flex flex-wrap items-center gap-3 text-xs text-[var(--text-secondary)]">
          {timeAgo && <span>Last run: {timeAgo}</span>}
          {dur && <span>Duration: {dur}</span>}
        </div>
      )}

      <div className="mt-3 rounded bg-[var(--bg-subtle)] px-3 py-2">
        <span className="text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
          Run in terminal
        </span>
        <code className="mt-0.5 block font-mono text-xs text-[var(--accent-blue)]">
          {command}
        </code>
      </div>
    </div>
  );
}

/* ---- Operation Sparkline ------------------------------------------- */

function OperationSparkline({ days }: { days: TimelineDay[] }) {
  /* Use all days (up to 14), padded right if fewer */
  const maxSessions = Math.max(1, ...days.map((d) => d.summary.sessions));
  return (
    <div className="mt-3 rounded-lg border border-[var(--border)] bg-[var(--bg-card)] px-4 py-2.5">
      <span className="mb-1.5 block text-[10px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
        14-day operations
      </span>
      <div className="flex items-end gap-0.5 h-6">
        {days.map((day) => {
          const height = Math.max(2, (day.summary.sessions / maxSessions) * 24);
          return (
            <div
              key={day.date}
              className="flex-1 rounded-t bg-[var(--accent-blue)]/40"
              style={{ height }}
              title={`${day.label}: ${day.summary.sessions} sessions`}
            />
          );
        })}
      </div>
    </div>
  );
}

/* ---- Timeline Day -------------------------------------------------- */

function TimelineDaySection({
  day,
  isFirstDay,
  onRecordClick,
  onOperationClick,
}: {
  day: TimelineDay;
  isFirstDay: boolean;
  onRecordClick: (recordId: string) => void;
  onOperationClick: (op: TimelineOperation) => void;
}) {
  const isToday = day.label === "Today";
  return (
    <div>
      {/* Day header */}
      <div className="mb-2 flex items-center gap-3">
        <span className="text-sm font-semibold text-[var(--text)]">{day.label}</span>
        <span className="text-xs text-[var(--text-muted)]">
          {day.summary.sessions} sessions / {day.summary.records_new} new, {day.summary.records_updated} updated
        </span>
      </div>

      {/* Events on the timeline */}
      {day.events.length > 0 ? (
        <div className="ml-1 border-l-2 border-[var(--border)] pl-5 space-y-1">
          {day.events.map((event, idx) => (
            <TimelineEventRow
              key={`${day.date}-${idx}`}
              event={event}
              isLatest={isFirstDay && idx === 0}
              isToday={isToday}
              onRecordClick={onRecordClick}
              onOperationClick={onOperationClick}
            />
          ))}
        </div>
      ) : (
        <div className="ml-1 border-l-2 border-[var(--border)] pl-5 py-3">
          <span className="text-xs italic text-[var(--text-muted)]">
            &mdash;&mdash; no operations &mdash;&mdash;
          </span>
        </div>
      )}
    </div>
  );
}

/* ---- Timeline Event Row -------------------------------------------- */

function formatDurationMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function OperationErrorDetails({ details }: { details: Record<string, unknown> }) {
  const errors: string[] = [];

  // Top-level error string
  if (typeof details.error === "string") {
    errors.push(details.error);
  }

  const projectDetails =
    details.projects && typeof details.projects === "object"
      ? details.projects
      : details.projects_metrics && typeof details.projects_metrics === "object"
        ? details.projects_metrics
        : null;

  if (projectDetails) {
    for (const [proj, val] of Object.entries(projectDetails as Record<string, unknown>)) {
      if (val && typeof val === "object" && "error" in val) {
        errors.push(`[${proj}] ${(val as { error: string }).error}`);
      }
    }
  }

  if (errors.length === 0) return null;

  return (
    <div className="mt-1 space-y-0.5">
      {errors.map((err, i) => (
        <div
          key={i}
          className="flex items-start gap-1.5 rounded bg-red-500/10 px-2 py-1 text-[11px] text-red-400"
        >
          <svg className="mt-0.5 h-3 w-3 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <span className="break-all">{err}</span>
        </div>
      ))}
    </div>
  );
}

function OperationBadge({ op }: { op: TimelineOperation }) {
  const isIngest = op.type === "ingest";
  const isFailed = op.status === "failed";
  const isInterrupted = op.status === "interrupted";
  const bg = isFailed || isInterrupted ? "rgba(239,68,68,0.15)" : isIngest ? "rgba(59,130,246,0.15)" : "rgba(139,92,246,0.15)";
  const color = isFailed || isInterrupted ? "#ef4444" : isIngest ? "#3b82f6" : "#8b5cf6";
  const label = isIngest ? "Ingest" : "Curate";

  return (
    <span
      className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium leading-tight"
      style={{ backgroundColor: bg, color }}
    >
      {label}
      <span style={{ opacity: 0.7 }}>&middot;</span>
      {formatStatusLabel(op.status)}
      {op.duration_ms != null && (
        <>
          <span style={{ opacity: 0.7 }}>&middot;</span>
          {formatDurationMs(op.duration_ms)}
        </>
      )}
    </span>
  );
}

function operationProjects(op: TimelineOperation): string[] {
  const names = new Set<string>();
  if (op.scope_label && op.scope_label.trim()) {
    names.add(op.scope_label.trim());
  }
  if (op.project_label && op.project_label.trim()) {
    names.add(op.project_label.trim());
  }
  for (const project of op.scope_projects || []) {
    if (project.trim()) names.add(project.trim());
  }
  if (op.project && op.project.trim()) {
    names.add(op.project.trim());
  }
  const details = op.details;
  if (!details || typeof details !== "object") return Array.from(names).sort();
  const directProject = details.project;
  if (typeof directProject === "string" && directProject.trim()) {
    names.add(directProject.trim());
  }
  for (const key of ["projects", "projects_metrics"]) {
    const value = details[key];
    if (value && typeof value === "object" && !Array.isArray(value)) {
      for (const project of Object.keys(value as Record<string, unknown>)) {
        if (project.trim()) names.add(project.trim());
      }
    }
  }
  return Array.from(names).sort();
}

function OperationProjectPills({ operations }: { operations: TimelineOperation[] }) {
  const projects = Array.from(new Set(operations.flatMap(operationProjects))).sort();
  if (projects.length === 0) return null;
  const visible = projects.slice(0, 3);
  const hidden = projects.length - visible.length;
  return (
    <div className="mt-1.5 flex min-w-0 flex-wrap items-center gap-1">
      <span className="text-[10px] uppercase text-[var(--text-muted)]">Scopes</span>
      {visible.map((project) => (
        <ProjectBadge key={project} value={project} />
      ))}
      {hidden > 0 && (
        <span className="rounded px-1.5 py-0.5 text-[10px] bg-white/[0.05] text-[var(--text-muted)]">
          +{hidden}
        </span>
      )}
    </div>
  );
}

function TimelineEventRow({
  event,
  isLatest,
  isToday,
  onRecordClick,
  onOperationClick,
}: {
  event: TimelineEvent;
  isLatest: boolean;
  isToday: boolean;
  onRecordClick: (recordId: string) => void;
  onOperationClick: (op: TimelineOperation) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const hasSessions = event.sessions_count > 0;
  const totalRecords = event.records_new + event.records_updated + event.records_archived;
  const ops = event.operations ?? [];
  const hasOperations = ops.length > 0;

  // Determine dot and accent color based on operations when available
  let dotColor: string;
  let accentColor: string;
  if (hasOperations) {
    const hasIngest = ops.some((o) => o.type === "ingest");
    const hasCurate = ops.some((o) => o.type === "curate");
    if (hasIngest && hasCurate) {
      dotColor = "var(--accent-blue)";
      accentColor = "var(--accent-blue)";
    } else if (hasCurate) {
      dotColor = "#8b5cf6";
      accentColor = "#8b5cf6";
    } else {
      dotColor = "var(--accent-blue)";
      accentColor = "var(--accent-blue)";
    }
  } else {
    dotColor = hasSessions ? "var(--accent-blue)" : "var(--accent-teal)";
    accentColor = hasSessions ? "var(--accent-blue)" : "var(--accent-teal)";
  }

  const displayTime = formatEventTime(event.time, isToday);
  const canExpand = totalRecords > 0 || (hasOperations && ops.some((o) => o.trigger || o.details));

  return (
    <div
      className={`relative rounded-lg border-l-[3px] py-2 px-3 transition-colors ${
        expanded ? "bg-white/[0.02]" : "hover:bg-white/[0.02]"
      }`}
      style={{ borderLeftColor: accentColor }}
    >
      {/* Dot */}
      <span
        className="absolute -left-[27px] top-[12px] block h-2.5 w-2.5 rounded-full"
        style={{
          backgroundColor: dotColor,
          boxShadow: `0 0 4px ${dotColor}60`,
          ...(isLatest ? { animation: "timeline-pulse 2s ease-in-out infinite" } : {}),
        }}
      />

      {/* Event row */}
      <div className="flex w-full items-start gap-2 text-left min-w-0">
        <span className="w-14 shrink-0 pt-0.5 text-xs tabular-nums text-[var(--text-muted)]">
          {displayTime}
        </span>
        <div className="min-w-0 flex-1">
          <button
            type="button"
            onClick={() => canExpand && setExpanded(!expanded)}
            className="flex w-full min-w-0 items-start gap-2 rounded-sm text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
            disabled={!canExpand}
            aria-expanded={canExpand ? expanded : undefined}
          >
            <span className="min-w-0 flex-1 text-xs text-[var(--text-secondary)]">
              {hasSessions && (
                <>
                  <span className="font-medium text-[var(--text)]">{event.sessions_count}</span> session{event.sessions_count !== 1 ? "s" : ""}
                </>
              )}
              {hasSessions && totalRecords > 0 && <span className="mx-1 text-[var(--text-muted)]">/</span>}
              {event.records_new > 0 && (
                <span className="text-[#10b981]">{event.records_new} new</span>
              )}
              {event.records_new > 0 && event.records_updated > 0 && <span className="mx-0.5">/</span>}
              {event.records_updated > 0 && (
                <span className="text-[#3b82f6]">{event.records_updated} updated</span>
              )}
              {(event.records_new > 0 || event.records_updated > 0) && event.records_archived > 0 && (
                <span className="mx-0.5">/</span>
              )}
              {event.records_archived > 0 && (
                <span className="text-[var(--text-muted)]">{event.records_archived} archived</span>
              )}
            </span>
            {canExpand && (
              <svg
                className={`h-3 w-3 shrink-0 text-[var(--text-muted)] transition-transform duration-200 ${expanded ? "rotate-90" : ""}`}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
                aria-hidden="true"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 18l6-6-6-6" />
              </svg>
            )}
          </button>

          {hasOperations && (
            <div className="mt-1 flex min-w-0 flex-wrap items-center gap-1">
              {ops.map((op, i) => (
                <button
                  key={`${op.type}-${i}`}
                  type="button"
                  className="shrink-0 rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
                  onClick={() => onOperationClick(op)}
                  aria-label={`View ${op.type} operation details`}
                >
                  <OperationBadge op={op} />
                </button>
              ))}
            </div>
          )}
          <OperationProjectPills operations={ops} />
        </div>
      </div>

      {/* Expanded details */}
      {expanded && (
        <div className="mt-2 ml-14 space-y-1">
          {/* Operation details */}
          {hasOperations && ops.map((op, i) => (
            <div key={`op-detail-${op.type}-${i}`} className="rounded px-2 py-1">
              <div className="flex items-center gap-2 text-[11px] text-[var(--text-secondary)]">
                <span
                  className="font-medium"
                  style={{ color: op.type === "ingest" ? "#3b82f6" : "#8b5cf6" }}
                >
                  {op.type === "ingest" ? "Ingest" : "Curate"}
                </span>
                <span>&middot;</span>
                <span>status: {formatStatusLabel(op.status)}</span>
                {op.trigger && (
                  <>
                    <span>&middot;</span>
                    <span>trigger: {op.trigger}</span>
                  </>
                )}
                {op.duration_ms != null && (
                  <>
                    <span>&middot;</span>
                    <span>{formatDurationMs(op.duration_ms)}</span>
                  </>
                )}
                <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); onOperationClick(op); }}
                    className="ml-auto rounded-sm text-[10px] text-[var(--accent-blue)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
                  >
                    View details &rarr;
                  </button>
              </div>
              {/* Error details from details_json */}
              {op.details && <OperationErrorDetails details={op.details} />}
            </div>
          ))}
          {/* Record rows */}
          {event.records.map((record) => (
            <RecordRow key={record.record_id} record={record} onClick={onRecordClick} />
          ))}
        </div>
      )}
    </div>
  );
}

/* ---- Record Row (shared in timeline) ------------------------------- */

function RecordRow({
  record,
  onClick,
}: {
  record: TimelineRecord;
  onClick: (recordId: string) => void;
}) {
  return (
    <button
      onClick={() => onClick(record.record_id)}
      className="flex w-full items-center gap-2 rounded px-2 py-1 text-left transition-colors hover:bg-white/[0.03]"
    >
      <ActionIcon action={record.action} />
      <span className="truncate text-xs text-[var(--text)]">
        {record.title || "Untitled record"}
      </span>
      {record.record_kind && record.record_kind !== "archived" && <TypeBadge value={record.record_kind} />}
      {record.project && <ProjectBadge value={record.project} />}
      {record.confidence != null && (
        <ConfidenceBar confidence={record.confidence} />
      )}
    </button>
  );
}

/* ---- Small shared components --------------------------------------- */

function Divider() {
  return <span className="text-[var(--text-muted)]" aria-hidden="true">&#183;</span>;
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] px-6 py-10 text-center text-sm text-[var(--text-muted)]">
      {message}
    </div>
  );
}

type Health = "green" | "yellow" | "red";

const HEALTH_COLORS: Record<Health, string> = {
  green: "#10b981",
  yellow: "#f59e0b",
  red: "#ef4444",
};

function HealthDot({ health, size = 10 }: { health: Health; size?: number }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: size,
        height: size,
        borderRadius: "50%",
        backgroundColor: HEALTH_COLORS[health],
        boxShadow: `0 0 6px ${HEALTH_COLORS[health]}80`,
        flexShrink: 0,
      }}
    />
  );
}

/* ---- Action Icons (inline SVG, 14px) ------------------------------- */

function ActionIcon({ action }: { action: "new" | "updated" | "archived" }) {
  const size = 14;
  if (action === "new") {
    return (
      <svg width={size} height={size} viewBox="0 0 16 16" fill="none" className="shrink-0">
        <rect x="2" y="1" width="12" height="14" rx="2" stroke="#10b981" strokeWidth="1.3" />
        <path d="M8 5v6M5 8h6" stroke="#10b981" strokeWidth="1.3" strokeLinecap="round" />
      </svg>
    );
  }
  if (action === "updated") {
    return (
      <svg width={size} height={size} viewBox="0 0 16 16" fill="none" className="shrink-0">
        <path
          d="M10.5 2.5l3 3-8 8H2.5v-3l8-8z"
          stroke="#3b82f6"
          strokeWidth="1.3"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      </svg>
    );
  }
  /* archived */
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" className="shrink-0">
      <rect x="1.5" y="2" width="13" height="3.5" rx="1" stroke="#64748b" strokeWidth="1.3" />
      <path d="M3 5.5v7.5a1 1 0 001 1h8a1 1 0 001-1V5.5" stroke="#64748b" strokeWidth="1.3" />
      <path d="M6.5 8.5h3" stroke="#64748b" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

/* ---- Badges -------------------------------------------------------- */

const RECORD_KIND_COLORS: Record<string, { bg: string; text: string }> = {
  decision: { bg: "rgba(139,92,246,0.15)", text: "#8b5cf6" },
  constraint: { bg: "rgba(239,68,68,0.15)", text: "#f87171" },
  preference: { bg: "rgba(245,158,11,0.15)", text: "#f59e0b" },
  fact: { bg: "rgba(59,130,246,0.15)", text: "#60a5fa" },
  reference: { bg: "rgba(20,184,166,0.15)", text: "#2dd4bf" },
};

const DEFAULT_TYPE_COLOR = { bg: "rgba(255,255,255,0.06)", text: "#94a3b8" };

function TypeBadge({ value }: { value: string }) {
  const colors = RECORD_KIND_COLORS[value] ?? DEFAULT_TYPE_COLOR;
  return (
    <span
      className="shrink-0 rounded px-1.5 py-0.5 text-[10px]"
      style={{ backgroundColor: colors.bg, color: colors.text }}
    >
      {formatRecordKind(value)}
    </span>
  );
}

function ProjectBadge({ value }: { value: string }) {
  return (
    <span className="shrink-0 rounded px-1.5 py-0.5 text-[10px] bg-[#10b981]/10 text-[#10b981]">
      {formatScopeLabel(value)}
    </span>
  );
}

/* ---- Confidence Mini Bar ------------------------------------------- */

function ConfidenceBar({ confidence }: { confidence: number }) {
  return (
    <div className="flex items-center gap-1 ml-auto shrink-0">
      <div className="h-1 w-12 rounded-full bg-white/[0.06] overflow-hidden">
        <div
          className="h-full rounded-full bg-[var(--accent-teal)]"
          style={{ width: `${confidence * 100}%` }}
        />
      </div>
      <span className="text-[10px] tabular-nums text-[var(--text-muted)]">
        {Math.round(confidence * 100)}%
      </span>
    </div>
  );
}

/* ====================================================================
   Helpers
   ==================================================================== */

/** Relative label for a row; `isoTime` is latest activity in the hour bucket from the API */
function formatEventTime(isoTime: string, isToday: boolean): string {
  if (!isoTime) return "";
  if (!isToday) {
    return new Date(isoTime).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  const diff = Date.now() - new Date(isoTime).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  return `${hours}h ago`;
}

function getLatestOperations(timeline: TimelineResponse | null): {
  ingest: TimelineOperation | null;
  curate: TimelineOperation | null;
} {
  let ingest: TimelineOperation | null = null;
  let curate: TimelineOperation | null = null;
  if (!timeline) return { ingest, curate };
  for (const day of timeline.days) {
    for (const event of day.events) {
      for (const op of event.operations) {
        if (op.type === "ingest" && !ingest) ingest = op;
        if (op.type === "curate" && !curate) curate = op;
        if (ingest && curate) return { ingest, curate };
      }
    }
  }
  return { ingest, curate };
}

function getErrorHealth(status: PipelineStatusResponse | null): Health {
  if (!status || status.logs.total === 0) return "green";
  const rate = status.logs.errors / status.logs.total;
  if (rate < 0.01) return "green";
  if (rate < 0.05) return "yellow";
  return "red";
}
