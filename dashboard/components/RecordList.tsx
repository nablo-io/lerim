"use client";

import { formatRecordKind, formatScopeLabel } from "@/lib/labels";
import type { ContextRecord } from "@/lib/types";

interface RecordListProps {
  records: ContextRecord[];
}

export default function RecordList({ records }: RecordListProps) {
  if (records.length === 0) {
    return (
      <div className="rounded-lg border border-[var(--border)] p-12 text-center text-sm text-[var(--text-muted)]">
        No records found.
      </div>
    );
  }

  return (
    <div className="grid gap-3">
      {records.map((m) => (
        <div
          key={m.record_id}
          className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4"
        >
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <h3 className="text-sm font-medium text-[var(--text)] truncate">
                {m.title || "(untitled)"}
              </h3>
              <p className="mt-1 text-xs text-[var(--text-muted)] line-clamp-2">
                {m.body || ""}
              </p>
            </div>
            <StatusBadge status={m.status} />
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-[var(--text-muted)]">
            <span className="rounded bg-white/[0.06] px-1.5 py-0.5">{formatRecordKind(m.record_kind)}</span>
            {m.project && (
              <span className="rounded bg-white/[0.06] px-1.5 py-0.5">
                {formatScopeLabel(m.project)}
              </span>
            )}
            {m.record_kind !== "summary" && (
              <span>Confidence: {m.confidence != null ? `${Math.round(m.confidence * 100)}%` : "\u2014"}</span>
            )}
            <span>{m.created_at ? formatDate(m.created_at) : "\u2014"}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    active: "text-[var(--accent-teal)] bg-[rgba(16,185,129,0.1)]",
    archived: "text-[var(--text-secondary)] bg-[rgba(148,163,184,0.1)]",
    pending: "text-amber-400 bg-amber-400/10",
  };
  const cls = colors[status] || "text-[var(--text-secondary)] bg-[rgba(148,163,184,0.1)]";

  return (
    <span className={`shrink-0 rounded px-1.5 py-0.5 text-[11px] font-medium ${cls}`}>
      {status}
    </span>
  );
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  } catch {
    return iso;
  }
}
