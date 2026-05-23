"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import ReactMarkdown from "react-markdown";
import { api } from "@/lib/api";
import type { SessionDetail, TranscriptMessage } from "@/lib/types";

interface RunViewerModalProps {
  runId: string;
  onClose: () => void;
}

export default function RunViewerModal({ runId, onClose }: RunViewerModalProps) {
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [messages, setMessages] = useState<TranscriptMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  /* Fetch session detail + messages in parallel */
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [sessionData, messagesData] = await Promise.all([
        api.getSession(runId),
        api.getSessionMessages(runId),
      ]);
      setSession(sessionData);
      setMessages(messagesData.messages);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load session");
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    load();
  }, [load]);

  /* Close on Escape */
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose]);

  /* Prevent body scroll while modal is open */
  useEffect(() => {
    document.body.style.overflow = "hidden";
    closeButtonRef.current?.focus();
    return () => {
      document.body.style.overflow = "";
    };
  }, []);

  /* Close on backdrop click */
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
        aria-labelledby="run-viewer-title"
        className="relative my-4 max-h-[calc(100vh-2rem)] w-full max-w-3xl overflow-y-auto rounded-xl border border-[var(--border)]"
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
          <svg
            className="h-5 w-5"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>

        {/* Loading state */}
        {loading && (
          <div className="flex items-center justify-center py-24">
            <span className="text-sm text-[var(--text-muted)]">Loading session…</span>
          </div>
        )}

        {/* Error state */}
        {error && !loading && (
          <div className="p-6">
            <div className="rounded-lg border border-red-500/20 bg-red-500/5 px-4 py-3 text-sm text-red-400">
              {error}
            </div>
          </div>
        )}

        {/* Content */}
        {!loading && !error && session && (
          <>
            {/* Header */}
            <div className="border-b border-[var(--border)] p-6 pr-12">
              <h2 id="run-viewer-title" className="text-base font-semibold text-[var(--text)]">
                Session: {session.agent_type || "Unknown Agent"}
              </h2>
              <p className="mt-1 font-mono text-xs text-[var(--text-muted)]">
                {session.run_id}
              </p>

              {/* Metadata chips */}
              <div className="mt-4 flex flex-wrap gap-2">
                {session.agent_type && (
                  <Chip label="Agent" value={session.agent_type} />
                )}
                {session.project && (
                  <Chip label="Project" value={session.project} />
                )}
                <Chip
                  label="Duration"
                  value={
                    session.duration_ms != null
                      ? formatDurationMs(session.duration_ms)
                      : "--"
                  }
                />
                <Chip
                  label="Tokens"
                  value={session.total_tokens.toLocaleString()}
                />
                <Chip
                  label="Messages"
                  value={String(session.message_count)}
                />
                <Chip
                  label="Tools"
                  value={String(session.tool_call_count)}
                />
                {session.error_count > 0 && (
                  <Chip
                    label="Errors"
                    value={String(session.error_count)}
                    variant="error"
                  />
                )}
              </div>
            </div>

            {/* Transcript area */}
            <div className="p-6">
              <h3 className="mb-4 text-sm font-medium text-[var(--text-secondary)]">
                Chat Transcript
              </h3>

              {messages.length === 0 ? (
                <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] px-6 py-12 text-center">
                  <svg
                    className="mx-auto h-8 w-8 text-[var(--text-muted)]"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={1.5}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                  </svg>
                  <p className="mt-3 text-sm text-[var(--text-muted)]">
                    No transcript available
                  </p>
                  <p className="mt-1 text-xs text-[var(--text-muted)]">
                    Transcript data will appear here once the CLI ships session recordings.
                  </p>
                </div>
              ) : (
                <div className="space-y-3">
                  {messages.map((msg, idx) => (
                    <MessageBubble
                      key={idx}
                      message={msg}
                    />
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/* ---- Sub-components ------------------------------------------------- */

function Chip({
  label,
  value,
  variant,
}: {
  label: string;
  value: string;
  variant?: "error";
}) {
  const base =
    "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs";
  const style =
    variant === "error"
      ? "border border-red-500/20 bg-red-500/10 text-red-400"
      : "border border-[var(--border)] bg-[var(--bg-card)] text-[var(--text-secondary)]";

  return (
    <span className={`${base} ${style}`}>
      <span className="text-[var(--text-muted)]">{label}:</span>
      <span className="font-medium">{value}</span>
    </span>
  );
}

function MessageBubble({
  message,
}: {
  message: TranscriptMessage;
}) {
  const role = message.role.toLowerCase();
  const hasToolCalls = message.tool_calls.length > 0;

  /* Determine alignment and styling based on role */
  if (role === "system") {
    return (
      <div className="flex justify-center">
        <div className="max-w-md rounded-lg border border-[var(--border)] bg-[var(--bg-card)] px-4 py-2 text-center">
          <p className="text-xs text-[var(--text-muted)]">{message.content}</p>
          {message.timestamp && (
            <p className="mt-1 text-[10px] text-[var(--text-muted)]">
              {formatTimestamp(message.timestamp)}
            </p>
          )}
        </div>
      </div>
    );
  }

  const isUser = role === "user" || role === "human";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-lg border px-4 py-3 ${
          isUser
            ? "border-[rgba(255,255,255,0.08)] bg-[rgba(255,255,255,0.06)]"
            : hasToolCalls
              ? "border-[rgba(59,130,246,0.15)] bg-[rgba(59,130,246,0.06)]"
              : "border-[var(--border)] bg-[var(--bg-card)]"
        }`}
      >
        {/* Role label */}
        <div className="mb-1.5 flex items-center gap-2">
          <span
            className={`text-[10px] font-semibold uppercase tracking-wider ${
              isUser ? "text-[var(--accent-teal)]" : "text-[var(--accent-blue)]"
            }`}
          >
            {role}
          </span>
          {message.timestamp && (
            <span className="text-[10px] text-[var(--text-muted)]">
              {formatTimestamp(message.timestamp)}
            </span>
          )}
        </div>

        {/* Content */}
        {message.content && (
          <div className="prose prose-invert prose-sm max-w-none prose-headings:text-[var(--text)] prose-p:text-[var(--text-secondary)] prose-code:text-[var(--accent-teal)] prose-pre:bg-[var(--bg)] prose-strong:text-[var(--text)] prose-a:text-[var(--accent-blue)] text-[var(--text)]">
            <ReactMarkdown>{message.content}</ReactMarkdown>
          </div>
        )}

        {/* Tool calls */}
        {hasToolCalls && (
          <div className="mt-2 space-y-1">
            {message.tool_calls.map((tc, j) => (
              <details key={tc.id || `${tc.name}-${j}`} className="rounded border border-[var(--border)] bg-[var(--bg)]/50">
                <summary className="cursor-pointer px-3 py-1.5 text-xs font-medium text-[var(--accent-blue)]">
                  Tool: {tc.name}
                </summary>
                <div className="border-t border-[var(--border)] px-3 py-2 text-xs text-[var(--text-muted)] font-mono">
                  ID: {tc.id}
                </div>
              </details>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* ---- Helpers -------------------------------------------------------- */

function formatDurationMs(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  if (m < 60) return `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function formatTimestamp(ts: string): string {
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return ts;
    return d.toLocaleTimeString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return ts;
  }
}
