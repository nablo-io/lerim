"use client";

import type { LogEntry } from "@/lib/types";

interface LogViewerProps {
  logs: LogEntry[];
}

const LEVEL_COLORS: Record<string, string> = {
  error: "text-red-400",
  warn: "text-amber-400",
  warning: "text-amber-400",
  info: "text-[var(--accent-blue)]",
  debug: "text-[var(--text-muted)]",
};

export default function LogViewer({ logs }: LogViewerProps) {
  if (logs.length === 0) {
    return (
      <div className="rounded-lg border border-[var(--border)] p-12 text-center text-sm text-[var(--text-muted)]">
        No logs found.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--border)]">
      <table className="w-full table-fixed text-left text-sm">
        <thead>
          <tr className="border-b border-[var(--border)] text-xs font-medium uppercase tracking-wider text-[var(--text-muted)]">
            <th scope="col" className="w-28 px-4 py-3">Timestamp</th>
            <th scope="col" className="w-24 px-4 py-3">Level</th>
            <th scope="col" className="w-32 px-4 py-3">Module</th>
            <th scope="col" className="px-4 py-3">Message</th>
          </tr>
        </thead>
        <tbody className="font-mono text-xs">
          {logs.map((log, i) => (
            <tr
              key={`${log.ts}-${i}`}
              className="border-b border-[rgba(255,255,255,0.04)] transition-colors hover:bg-[var(--bg-card)]"
            >
              <td className="whitespace-nowrap px-4 py-2.5 text-[var(--text-muted)]">
                {formatTimestamp(log.ts)}
              </td>
              <td className="px-4 py-2.5">
                <span
                  className={`font-medium uppercase ${LEVEL_COLORS[log.level.toLowerCase()] || "text-[var(--text-secondary)]"}`}
                >
                  {log.level}
                </span>
              </td>
              <td className="whitespace-nowrap px-4 py-2.5 text-[var(--text-secondary)]">
                {log.module || "\u2014"}
              </td>
              <td className="min-w-0 px-4 py-2.5 text-[var(--text)]">
                <LogMessage message={log.message || ""} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LogMessage({ message }: { message: string }) {
  const trimmed = message.trim();
  if (!trimmed) return <span className="text-[var(--text-muted)]">—</span>;

  const isLong = trimmed.length > 180 || trimmed.includes("\n");
  if (!isLong) {
    return <span className="block truncate text-[var(--text-secondary)]">{trimmed}</span>;
  }

  return (
    <details className="group">
      <summary className="cursor-pointer list-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]">
        <span className="block truncate text-[var(--text-secondary)] group-open:hidden">
          {trimmed.slice(0, 220)}
          {trimmed.length > 220 ? "…" : ""}
        </span>
        <span className="hidden text-[var(--accent-blue)] group-open:inline">Collapse message</span>
      </summary>
      <pre className="mt-2 max-h-80 overflow-auto rounded-md border border-[var(--border)] bg-black/20 p-3 whitespace-pre-wrap break-words text-[11px] leading-5 text-[var(--text-secondary)]">
        {trimmed}
      </pre>
    </details>
  );
}

function formatTimestamp(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}
