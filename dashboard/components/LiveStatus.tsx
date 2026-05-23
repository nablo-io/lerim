"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { api } from "@/lib/api";
import type { LiveStatusResponse } from "@/lib/types";

const POLL_INTERVAL_MS = 20000;

export default function LiveStatus() {
  const [status, setStatus] = useState<LiveStatusResponse | null>(null);
  const [error, setError] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const poll = useCallback(async () => {
    try {
      const data = await api.getLiveStatus();
      setStatus(data);
      setError(false);
    } catch {
      setError(true);
    }
  }, []);

  useEffect(() => {
    poll();
    timerRef.current = setInterval(poll, POLL_INTERVAL_MS);

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [poll]);

  if (error) {
    return (
      <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] px-4 py-2.5 text-xs text-[var(--text-muted)]">
        <span className="inline-flex items-center gap-1.5">
          <StatusDot color="#64748b" />
          Live status unavailable
        </span>
      </div>
    );
  }

  if (!status) {
    return (
      <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] px-4 py-2.5 text-xs text-[var(--text-muted)]">
        Loading live status…
      </div>
    );
  }

  if (!status.reachable) {
    return (
      <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] px-4 py-2.5 text-xs text-[var(--text-muted)]">
        <span className="inline-flex items-center gap-1.5">
          <StatusDot color="#64748b" />
          Container not reachable
        </span>
      </div>
    );
  }

  const { ingest_active, curate_active, queue, last_ingest, last_curate } = status;

  let activityLabel: string;
  let dotColor: string;
  let pulsing = false;

  if (ingest_active && curate_active) {
    activityLabel = "Ingesting + Curating";
    dotColor = "#3b82f6";
    pulsing = true;
  } else if (ingest_active) {
    const running = queue.running;
    activityLabel = running > 0 ? `Ingesting (${running} session${running !== 1 ? "s" : ""})` : "Ingesting";
    dotColor = "#3b82f6";
    pulsing = true;
  } else if (curate_active) {
    activityLabel = "Curating";
    dotColor = "#8b5cf6";
    pulsing = true;
  } else {
    activityLabel = "Idle";
    dotColor = "#10b981";
  }

  const queueParts: string[] = [];
  if (queue.pending > 0) queueParts.push(`${queue.pending} pending`);
  if (queue.running > 0) queueParts.push(`${queue.running} running`);
  if (queue.failed > 0) queueParts.push(`${queue.failed} failed`);
  const queueLabel = queueParts.length > 0 ? queueParts.join(", ") : null;
  const hasDeadLetter = queue.dead_letter > 0;

  return (
    <div className={`rounded-lg border px-4 py-2.5 ${hasDeadLetter ? "border-amber-500/30 bg-amber-500/5" : "border-[var(--border)] bg-[var(--bg-card)]"}`}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--text-secondary)]">
        {/* Activity status */}
        <span className="inline-flex items-center gap-1.5">
          <StatusDot color={hasDeadLetter ? "#f59e0b" : dotColor} pulsing={pulsing} />
          <span className="font-medium text-[var(--text)]">{hasDeadLetter ? "Blocked" : activityLabel}</span>
        </span>

        {/* Dead letter warning */}
        {hasDeadLetter && (
          <>
            <Separator />
            <span className="text-amber-400 font-medium">
              {queue.dead_letter} dead-letter job{queue.dead_letter !== 1 ? "s" : ""} blocking queue
            </span>
            <DeadLetterActions onDone={poll} />
          </>
        )}

        {/* Queue counts */}
        {queueLabel && (
          <>
            <Separator />
            <span>Queue: {queueLabel}</span>
          </>
        )}

        {/* Last ingest */}
        {last_ingest && last_ingest.started_at && (
          <>
            <Separator />
            <span>
              Last ingest:{" "}
              <span className={lastRunStatusClass(last_ingest.status)}>
                {relativeTime(last_ingest.started_at)}
              </span>
            </span>
          </>
        )}

        {/* Last curate */}
        {last_curate && last_curate.started_at && (
          <>
            <Separator />
            <span>
              Last curate:{" "}
              <span className={lastRunStatusClass(last_curate.status)}>
                {relativeTime(last_curate.started_at)}
              </span>
            </span>
          </>
        )}
      </div>
    </div>
  );
}

/* ---- Sub-components ------------------------------------------------ */

function StatusDot({ color, pulsing = false }: { color: string; pulsing?: boolean }) {
  return (
    <span
      className="inline-block h-2 w-2 rounded-full shrink-0"
      style={{
        backgroundColor: color,
        boxShadow: `0 0 4px ${color}60`,
        animation: pulsing ? "live-status-pulse 1.5s ease-in-out infinite" : undefined,
      }}
    />
  );
}

function Separator() {
  return <span className="text-[var(--text-muted)]" aria-hidden="true">&#183;</span>;
}

/* ---- Helpers ------------------------------------------------------- */

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

function lastRunStatusClass(status: string): string {
  if (status === "ok" || status === "success" || status === "completed") {
    return "text-[var(--text)]";
  }
  if (status === "failed") return "text-red-400";
  return "text-[var(--text)]";
}

function DeadLetterActions({ onDone }: { onDone: () => void }) {
  const [acting, setActing] = useState<"retry" | "skip" | null>(null);
  const [confirmSkip, setConfirmSkip] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  async function handle(action: "retry" | "skip") {
    if (action === "skip" && !confirmSkip) {
      setConfirmSkip(true);
      return;
    }
    setActing(action);
    setActionError(null);
    try {
      if (action === "retry") {
        await api.retryAllDeadLetter();
      } else {
        await api.skipAllDeadLetter();
      }
      setConfirmSkip(false);
      onDone();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Action failed. Check that the local Lerim runtime is running.");
    }
    setActing(null);
  }

  return (
    <span className="inline-flex items-center gap-1.5" aria-live="polite">
      <button
        type="button"
        onClick={() => handle("retry")}
        disabled={acting !== null}
        className="px-2 py-0.5 rounded text-[10px] font-medium bg-amber-500/20 text-amber-300 hover:bg-amber-500/30 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 disabled:opacity-50"
      >
        {acting === "retry" ? "Retrying…" : "Retry"}
      </button>
      <button
        type="button"
        onClick={() => handle("skip")}
        disabled={acting !== null}
        className={`px-2 py-0.5 rounded text-[10px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 disabled:opacity-50 ${
          confirmSkip
            ? "bg-red-500/20 text-red-300 hover:bg-red-500/30"
            : "bg-white/5 text-[var(--text-muted)] hover:bg-white/10"
        }`}
      >
        {acting === "skip" ? "Skipping…" : confirmSkip ? "Confirm skip" : "Skip"}
      </button>
      {actionError && <span className="text-[10px] text-red-300">{actionError}</span>}
    </span>
  );
}
