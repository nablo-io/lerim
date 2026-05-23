"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type { SessionDetail } from "@/lib/types";

export default function SessionDetailPage() {
  const params = useParams<{ runId: string }>();
  const router = useRouter();
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!params.runId) return;
    setLoading(true);
    api
      .getSession(params.runId)
      .then(setSession)
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Failed to load session")
      )
      .finally(() => setLoading(false));
  }, [params.runId]);

  if (loading) {
    return <div className="text-center text-sm text-[var(--text-muted)]">Loading…</div>;
  }

  if (error) {
    return (
      <div className="rounded-lg border border-red-500/20 bg-red-500/5 px-4 py-3 text-sm text-red-400">
        {error}
      </div>
    );
  }

  if (!session) return null;

  return (
    <>
      {/* Back button */}
      <button
        onClick={() => router.push("/traces")}
        className="mb-6 flex items-center gap-1.5 text-xs text-[var(--text-muted)] transition-colors hover:text-[var(--text-secondary)]"
      >
        <svg
          className="h-3.5 w-3.5"
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <line x1="19" y1="12" x2="5" y2="12" />
          <polyline points="12 19 5 12 12 5" />
        </svg>
        Back to traces
      </button>

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-lg font-semibold text-[var(--text)]">
            {session.agent_type}
          </h1>
          <p className="mt-0.5 text-xs text-[var(--text-muted)] font-mono">
            {session.run_id}
          </p>
        </div>
      </div>

      {/* Metadata grid */}
      <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetaCard label="Started" value={session.start_time ? formatDateTime(session.start_time) : "\u2014"} />
        <MetaCard
          label="Duration"
          value={
            session.duration_ms != null
              ? formatDurationMs(session.duration_ms)
              : "\u2014"
          }
        />
        <MetaCard label="Project" value={session.project || "\u2014"} />
        <MetaCard label="Repo" value={session.repo_name || "\u2014"} />
      </div>

      {/* Counts */}
      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetaCard label="Messages" value={String(session.message_count)} />
        <MetaCard label="Tool calls" value={String(session.tool_call_count)} />
        <MetaCard
          label="Errors"
          value={String(session.error_count)}
          highlight={session.error_count > 0}
        />
        <MetaCard
          label="Tokens"
          value={session.total_tokens.toLocaleString()}
        />
      </div>

      {/* Summary */}
      {session.summary_text && (
        <div className="mt-6">
          <h2 className="mb-2 text-sm font-medium text-[var(--text-secondary)]">Summary</h2>
          <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4 text-sm leading-relaxed text-[var(--text)]">
            {session.summary_text}
          </div>
        </div>
      )}

      {/* Extra metadata */}
      <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3">
        {session.machine_id && (
          <MetaCard label="Machine" value={session.machine_id} />
        )}
        <MetaCard label="Created" value={formatDateTime(session.created_at)} />
        <MetaCard label="Updated" value={formatDateTime(session.updated_at)} />
      </div>
    </>
  );
}

/* ---- Helpers ------------------------------------------------------- */

function MetaCard({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight?: boolean;
}) {
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] px-4 py-3">
      <p className="text-[10px] font-medium uppercase tracking-wider text-[var(--text-muted)]">
        {label}
      </p>
      <p
        className={`mt-1 text-sm font-medium ${highlight ? "text-red-400" : "text-[var(--text)]"}`}
      >
        {value}
      </p>
    </div>
  );
}

function formatDateTime(iso: string): string {
  try {
    const d = new Date(iso);
    return (
      d.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
      }) +
      " " +
      d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
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
