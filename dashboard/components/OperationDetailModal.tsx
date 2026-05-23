"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { api } from "@/lib/api";
import { formatScopeLabel, formatStatusLabel, humanizeToken } from "@/lib/labels";
import type { TimelineOperation, LogEntry } from "@/lib/types";
import LogViewer from "@/components/LogViewer";

interface OperationDetailModalProps {
  operation: TimelineOperation;
  onClose: () => void;
}

export default function OperationDetailModal({
  operation,
  onClose,
}: OperationDetailModalProps) {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [logsLoading, setLogsLoading] = useState(true);
  const [logsError, setLogsError] = useState<string | null>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  const isIngest = operation.type === "ingest";
  const isFailed = operation.status === "failed";
  const label = isIngest ? "Ingest Operation" : "Curate Operation";
  const projects = operationProjectNames(operation);

  /* Fetch time-window logs */
  const loadLogs = useCallback(async () => {
    setLogsLoading(true);
    setLogsError(null);
    try {
      const params: Record<string, string> = {
        since: operation.started_at,
        limit: "50",
      };
      if (operation.completed_at) {
        params.until = operation.completed_at;
      }
      const data = await api.getLogs(params);
      setLogs(data.logs);
    } catch (err) {
      setLogsError(err instanceof Error ? err.message : "Failed to load operation logs");
    } finally {
      setLogsLoading(false);
    }
  }, [operation.started_at, operation.completed_at]);

  useEffect(() => {
    loadLogs();
  }, [loadLogs]);

  /* Close on Escape */
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose]);

  /* Prevent body scroll */
  useEffect(() => {
    document.body.style.overflow = "hidden";
    closeButtonRef.current?.focus();
    return () => {
      document.body.style.overflow = "";
    };
  }, []);

  function handleBackdropClick(e: React.MouseEvent) {
    if (e.target === backdropRef.current) onClose();
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
        aria-labelledby="operation-detail-title"
        className="relative my-4 max-h-[calc(100vh-2rem)] w-full max-w-2xl overflow-y-auto rounded-xl border border-[var(--border)]"
        style={{ backgroundColor: "var(--bg-subtle)" }}
      >
        {/* Close button */}
        <button
          ref={closeButtonRef}
          type="button"
          onClick={onClose}
          className="absolute right-4 top-4 flex min-h-11 min-w-11 items-center justify-center rounded-md p-1.5 text-[var(--text-muted)] transition-colors hover:bg-[rgba(255,255,255,0.06)] hover:text-[var(--text-secondary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
          aria-label="Close"
        >
          <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>

        {/* Header */}
        <div className="border-b border-[var(--border)] p-6 pr-12">
          <div className="flex items-center gap-2">
            <span className={`inline-block h-2.5 w-2.5 rounded-full ${statusDotColor(operation.status)}`} />
            <h2 id="operation-detail-title" className="text-base font-semibold text-[var(--text)]">{label}</h2>
            <span className={`text-sm ${
              isFailed ? "text-red-400"
              : operation.status === "interrupted" ? "text-amber-400"
              : operation.status === "started" || operation.status === "running" ? "text-amber-400"
              : "text-emerald-400"
            }`}>
              {formatStatusLabel(operation.status)}
            </span>
          </div>

          {/* Metadata chips */}
          <div className="mt-3 flex flex-wrap gap-2">
            {operation.trigger && <Chip label="Trigger" value={operation.trigger} />}
            <Chip label="Started" value={formatDateTime(operation.started_at)} />
            {operation.completed_at && (
              <Chip label="Completed" value={formatDateTime(operation.completed_at)} />
            )}
            {operation.duration_ms != null && (
              <Chip label="Duration" value={formatDurationMs(operation.duration_ms)} />
            )}
            {projects.length > 0 && (
              <Chip
                label="Scopes"
                value={projects.length > 3 ? `${projects.slice(0, 3).map(formatScopeLabel).join(", ")} +${projects.length - 3}` : projects.map(formatScopeLabel).join(", ")}
              />
            )}
          </div>
        </div>

        {/* Details section */}
        <div className="p-6 space-y-5">
          <DetailsSection details={operation.details} status={operation.status} />

          {/* Time-window logs */}
          <div>
            <h3 className="mb-2 text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
              Application Logs
            </h3>
            <p className="mb-3 text-[11px] text-[var(--text-muted)]">
              Logs recorded between {formatDateTime(operation.started_at)}
              {operation.completed_at ? ` and ${formatDateTime(operation.completed_at)}` : ""}
            </p>
            {logsLoading ? (
              <div className="rounded-lg border border-[var(--border)] p-8 text-center text-sm text-[var(--text-muted)]">
                Loading logs…
              </div>
            ) : logsError ? (
              <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-4 py-3 text-sm text-amber-300">
                Could not load operation logs. {logsError}
              </div>
            ) : logs.length > 0 ? (
              <LogViewer logs={logs} />
            ) : (
              <div className="rounded-lg border border-[var(--border)] p-8 text-center text-sm text-[var(--text-muted)]">
                No application logs recorded during this operation.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ---- Sub-components -------------------------------------------------- */

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md bg-[var(--bg-card)] px-2.5 py-1 text-xs">
      <span className="text-[var(--text-muted)]">{label}:</span>
      <span className="font-medium text-[var(--text-secondary)]">{value}</span>
    </span>
  );
}

function DetailsSection({
  details,
  status,
}: {
  details: Record<string, unknown> | null;
  status: string;
}) {
  const isFailed = status === "failed";
  const isCompleted = status === "completed" || status === "ok" || status === "success";
  const isInterrupted = status === "interrupted";
  const isStarted = status === "started" || status === "running";

  if (!details || Object.keys(details).length === 0) {
    if (isCompleted) {
      return (
        <div className="flex items-center gap-2 rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-4 py-3 text-sm text-emerald-400">
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
          Completed successfully
        </div>
      );
    }
    if (isInterrupted) {
      return (
        <div className="flex items-center gap-2 rounded-lg border border-orange-500/20 bg-orange-500/5 px-4 py-3 text-sm text-orange-400">
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          Interrupted — daemon was restarted before this operation completed
        </div>
      );
    }
    if (isStarted) {
      return (
        <div className="flex items-center gap-2 rounded-lg border border-amber-500/20 bg-amber-500/5 px-4 py-3 text-sm text-amber-400">
          <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
          In progress
        </div>
      );
    }
    return null;
  }

  const errorStr = typeof details.error === "string" ? details.error : null;
  const projects = projectMetricMap(details);
  const extraKeys = Object.entries(details).filter(
    ([k]) => k !== "error" && k !== "projects" && k !== "projects_metrics",
  );

  return (
    <div className="space-y-3">
      <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
        {isFailed ? "Error Details" : "Run Details"}
      </h3>

      {/* Top-level error */}
      {errorStr && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/5 px-4 py-3">
          <pre className="whitespace-pre-wrap break-all font-mono text-sm text-red-400">
            {errorStr}
          </pre>
        </div>
      )}

      {/* Per-project breakdown */}
      {projects && (
        <div className="space-y-2">
          {Object.entries(projects).map(([project, data]) => {
            const projError = typeof data.error === "string" ? data.error : null;
            return (
              <div
                key={project}
                className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] px-4 py-3"
              >
                <div className="mb-2 text-xs font-semibold text-[var(--text)]">{formatScopeLabel(project)}</div>
                {projError ? (
                  <pre className="whitespace-pre-wrap break-all font-mono text-xs text-red-400">
                    {projError}
                  </pre>
                ) : (
                  <DetailKVList data={data} />
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Remaining top-level keys */}
      {extraKeys.length > 0 && (
        <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] px-4 py-3">
          <DetailKVList data={Object.fromEntries(extraKeys)} />
        </div>
      )}
    </div>
  );
}

/* ---- Helpers --------------------------------------------------------- */

/** Renders a flat or nested key-value object as a clean grid */
function DetailKVList({ data }: { data: Record<string, unknown> }) {
  // Separate scalars, nested objects, and long strings (paths)
  const scalars: [string, string][] = [];
  const nested: [string, Record<string, unknown>][] = [];
  const paths: [string, string][] = [];

  for (const [key, val] of Object.entries(data)) {
    if (val !== null && typeof val === "object" && !Array.isArray(val)) {
      nested.push([key, val as Record<string, unknown>]);
    } else if (typeof val === "string" && (val.startsWith("/") || val.length > 60)) {
      paths.push([key, val]);
    } else {
      scalars.push([key, formatDetailValue(val)]);
    }
  }

  return (
    <div className="space-y-2 text-xs">
      {/* Scalar metrics in a grid */}
      {scalars.length > 0 && (
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
          {scalars.map(([key, val]) => (
            <div key={key} className="flex items-baseline gap-1.5">
              <span className="text-[var(--text-muted)]">{formatKey(key)}:</span>
              <span className="font-medium text-[var(--text-secondary)]">{val}</span>
            </div>
          ))}
        </div>
      )}

      {/* Nested objects (counts, artifacts, etc.) */}
      {nested.map(([key, obj]) => {
        const hasLongValues = Object.values(obj).some(
          (v) => typeof v === "string" && (v.startsWith("/") || v.length > 50),
        );
        return (
          <div key={key}>
            <div className="mb-1 text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
              {formatKey(key)}
            </div>
            <div
              className={`rounded bg-[var(--bg-subtle)] px-3 py-2 ${
                hasLongValues ? "space-y-1" : "grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3"
              }`}
            >
              {Object.entries(obj).map(([k, v]) => (
                <div key={k} className="flex items-baseline gap-1.5">
                  <span className="shrink-0 text-[var(--text-muted)]">{formatKey(k)}:</span>
                  <span
                    className={`font-medium text-[var(--text-secondary)] ${
                      typeof v === "string" && v.startsWith("/") ? "break-all font-mono text-[10px]" : ""
                    }`}
                  >
                    {formatDetailValue(v)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        );
      })}

      {/* Long strings / paths */}
      {paths.map(([key, val]) => (
        <div key={key} className="flex items-baseline gap-1.5">
          <span className="shrink-0 text-[var(--text-muted)]">{formatKey(key)}:</span>
          <span className="break-all font-mono text-[10px] text-[var(--text-secondary)]">{val}</span>
        </div>
      ))}
    </div>
  );
}

function formatDetailValue(val: unknown): string {
  if (Array.isArray(val)) return `${val.length} items`;
  if (typeof val === "boolean") return val ? "Yes" : "No";
  if (typeof val === "string" && /^\d{4}-\d{2}-\d{2}T/.test(val)) {
    try {
      return new Date(val).toLocaleString(undefined, {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
      });
    } catch { return val; }
  }
  if (typeof val === "object" && val !== null) return `${Object.keys(val).length} fields`;
  return String(val);
}

function statusDotColor(status: string): string {
  if (status === "completed" || status === "ok" || status === "success") return "bg-emerald-400";
  if (status === "failed") return "bg-red-400";
  if (status === "interrupted") return "bg-orange-400";
  if (status === "running" || status === "started") return "bg-amber-400";
  return "bg-gray-400";
}

function formatDateTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

function formatDurationMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

function projectMetricMap(details: Record<string, unknown>): Record<string, Record<string, unknown>> | null {
  const source =
    details.projects && typeof details.projects === "object"
      ? details.projects
      : details.projects_metrics && typeof details.projects_metrics === "object"
        ? details.projects_metrics
        : null;
  return source as Record<string, Record<string, unknown>> | null;
}

function operationProjectNames(operation: TimelineOperation): string[] {
  const names = new Set<string>();
  if (operation.scope_label && operation.scope_label.trim()) {
    names.add(operation.scope_label.trim());
  }
  if (operation.project_label && operation.project_label.trim()) {
    names.add(operation.project_label.trim());
  }
  for (const project of operation.scope_projects || []) {
    if (project.trim()) names.add(project.trim());
  }
  if (operation.project && operation.project.trim()) {
    names.add(operation.project.trim());
  }
  const details = operation.details;
  if (!details) return Array.from(names).sort();
  const directProject = details.project;
  if (typeof directProject === "string" && directProject.trim()) {
    names.add(directProject.trim());
  }
  const metrics = projectMetricMap(details);
  if (metrics) {
    for (const project of Object.keys(metrics)) {
      if (project.trim()) names.add(project.trim());
    }
  }
  return Array.from(names).sort();
}

function formatKey(key: string): string {
  return humanizeToken(key);
}
