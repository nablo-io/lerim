"use client";

import { useRouter } from "next/navigation";
import type { Session } from "@/lib/types";

interface SessionTableProps {
  sessions: Session[];
  sort: string;
  order: "asc" | "desc";
  onSort: (column: string) => void;
  onOpen?: (runId: string) => void;
}

const SORTABLE_COLUMNS: { key: string; label: string; align?: "right" }[] = [
  { key: "start_time", label: "Time" },
  { key: "agent_type", label: "Agent" },
  { key: "", label: "Status" },
  { key: "", label: "Project" },
  { key: "message_count", label: "Msgs", align: "right" },
  { key: "tool_call_count", label: "Tools", align: "right" },
  { key: "error_count", label: "Errors", align: "right" },
  { key: "total_tokens", label: "Tokens", align: "right" },
  { key: "duration_ms", label: "Duration", align: "right" },
];

export default function SessionTable({
  sessions,
  sort,
  order,
  onSort,
  onOpen,
}: SessionTableProps) {
  const router = useRouter();

  if (sessions.length === 0) {
    return (
      <div className="rounded-lg border border-[var(--border)] p-12 text-center text-sm text-[var(--text-muted)]">
        No sessions found.
      </div>
    );
  }

  function renderSortIndicator(columnKey: string) {
    if (!columnKey || sort !== columnKey) return null;
    return (
      <span className="ml-1 text-[var(--accent-blue)]">
        {order === "asc" ? "\u25B2" : "\u25BC"}
      </span>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--border)]">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-[var(--border)] text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
            {SORTABLE_COLUMNS.map((col, idx) => {
              const isSortable = col.key !== "";
              const isActive = sort === col.key;
              return (
                <th
                  key={`${col.label}-${idx}`}
                  scope="col"
                  className={`px-4 py-3 ${col.align === "right" ? "text-right" : ""} ${
                    isActive ? "text-[var(--accent-blue)]" : ""
                  }`}
                >
                  {isSortable ? (
                    <button
                      type="button"
                      onClick={() => onSort(col.key)}
                      className={`select-none rounded-sm transition-colors hover:text-[var(--text-secondary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)] ${
                        col.align === "right" ? "ml-auto block" : ""
                      }`}
                      aria-label={`Sort by ${col.label}`}
                    >
                      {col.label}
                      {renderSortIndicator(col.key)}
                    </button>
                  ) : (
                    col.label
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sessions.map((s) => {
            const status = s.processing_status || "indexed";
            const openSession = () =>
              onOpen ? onOpen(s.run_id) : router.push(`/traces/${s.run_id}`);
            return (
              <tr
                key={s.run_id}
                role="button"
                tabIndex={0}
                aria-label={`Open session ${s.run_id}`}
                onClick={openSession}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    openSession();
                  }
                }}
                className="cursor-pointer border-b border-[rgba(255,255,255,0.04)] transition-colors hover:bg-[var(--bg-card)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--accent-blue)]"
              >
                <td className="whitespace-nowrap px-4 py-3 text-[var(--text-secondary)]">
                  {formatTime(s.start_time)}
                </td>
                <td className="px-4 py-3 text-[var(--text)]">
                  {s.agent_type || "unknown"}
                </td>
                <td className="px-4 py-3">
                  <StatusBadge status={status} />
                </td>
                <td className="px-4 py-3 text-[var(--text-secondary)]">
                  {s.project || "\u2014"}
                </td>
                <td className="px-4 py-3 text-right tabular-nums text-[var(--text)]">
                  {s.message_count}
                </td>
                <td className="px-4 py-3 text-right tabular-nums text-[var(--text)]">
                  {s.tool_call_count}
                </td>
                <td className="px-4 py-3 text-right tabular-nums">
                  <span
                    className={
                      s.error_count > 0
                        ? "text-red-400"
                        : "text-[var(--text-muted)]"
                    }
                  >
                    {s.error_count}
                  </span>
                </td>
                <td className="px-4 py-3 text-right tabular-nums text-[var(--text-secondary)]">
                  {(s.total_tokens ?? 0).toLocaleString()}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums text-[var(--text-secondary)]">
                  {s.duration_ms != null ? formatDurationMs(s.duration_ms) : "\u2014"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ---- Helpers -------------------------------------------------------- */

function StatusBadge({ status }: { status: string }) {
  const lower = status.toLowerCase();
  let cls: string;
  let label: string;
  switch (lower) {
    case "processed":
      cls = "border-[var(--accent-teal)]/20 bg-[rgba(16,185,129,0.10)] text-[var(--accent-teal)]";
      label = "Processed";
      break;
    case "indexed":
      cls = "border-gray-500/20 bg-gray-500/10 text-gray-400";
      label = "Indexed";
      break;
    case "queued":
      cls = "border-blue-500/20 bg-blue-500/10 text-blue-400";
      label = "Queued";
      break;
    case "processing":
      cls = "border-amber-500/20 bg-amber-500/10 text-amber-400 animate-pulse";
      label = "Processing";
      break;
    case "failed":
      cls = "border-red-500/20 bg-red-500/10 text-red-400";
      label = "Failed";
      break;
    case "blocked":
      cls = "border-amber-500/20 bg-amber-500/10 text-amber-400";
      label = "Blocked";
      break;
    default:
      cls = "border-gray-500/20 bg-gray-500/10 text-gray-400";
      label = status;
      break;
  }
  return (
    <span className={`inline-block rounded-full border px-2 py-0.5 text-[11px] font-medium ${cls}`}>
      {label}
    </span>
  );
}

function formatTime(iso: string | null): string {
  if (!iso) return "\u2014";
  try {
    const d = new Date(iso);
    return (
      d.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
      }) +
      " " +
      d.toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
      })
    );
  } catch {
    return iso;
  }
}

function formatDurationMs(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  if (m < 60) return `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}
