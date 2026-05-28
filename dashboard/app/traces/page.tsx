"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { api } from "@/lib/api";
import type { Session } from "@/lib/types";
import SessionTable from "@/components/SessionTable";
import RunViewerModal from "@/components/RunViewerModal";
import TimeScope from "@/components/TimeScope";

const PAGE_SIZE = 20;
const DEFAULT_SORT = "start_time";
const DEFAULT_ORDER: "asc" | "desc" = "desc";

export default function SourcesPage() {
  const [scope, setScope] = useState("all");
  const [agent, setAgent] = useState("");
  const [sessions, setSessions] = useState<Session[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  /* Search */
  const [statusFilter, setStatusFilter] = useState("");

  const [searchInput, setSearchInput] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  /* Repo filter */
  const [repoFilter, setRepoFilter] = useState("");

  /* Sorting */
  const [sort, setSort] = useState(DEFAULT_SORT);
  const [order, setOrder] = useState<"asc" | "desc">(DEFAULT_ORDER);

  /* Pagination */
  const [offset, setOffset] = useState(0);

  /* Run viewer modal */
  const [viewingRunId, setViewingRunId] = useState<string | null>(null);

  /* Retry-all loading guard */
  const [retrying, setRetrying] = useState(false);

  /* Collect unique agent names for the filter dropdown */
  const agents = Array.from(new Set(sessions.map((s) => s.agent_type).filter((a): a is string => a != null))).sort();

  /* Debounced search input */
  function handleSearchChange(value: string) {
    setSearchInput(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setSearchQuery(value);
      setOffset(0);
    }, 300);
  }

  /* Sorting handler: first click asc, second desc, third reset */
  function handleSort(column: string) {
    if (sort === column) {
      if (order === "asc") {
        setOrder("desc");
      } else {
        /* Reset to default */
        setSort(DEFAULT_SORT);
        setOrder(DEFAULT_ORDER);
      }
    } else {
      setSort(column);
      setOrder("asc");
    }
    setOffset(0);
  }

  /* Clear all filters */
  function handleClear() {
    setSearchInput("");
    setSearchQuery("");
    setRepoFilter("");
    setStatusFilter("");
    setSort(DEFAULT_SORT);
    setOrder(DEFAULT_ORDER);
    setOffset(0);
  }

  /* Reset offset when filters change */
  useEffect(() => {
    setOffset(0);
  }, [scope, agent, repoFilter, statusFilter]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = {
        scope,
        limit: String(PAGE_SIZE),
        offset: String(offset),
        sort,
        order,
      };
      if (agent) params.agent_type = agent;
      if (repoFilter) params.repo = repoFilter;
      if (statusFilter) params.processing_status = statusFilter;

      let data;
      if (searchQuery.trim()) {
        params.q = searchQuery.trim();
        data = await api.search(params);
      } else {
        data = await api.getSessions(params);
      }

      setSessions(data.sessions);
      setTotal(data.total);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load sessions"
      );
    } finally {
      setLoading(false);
    }
  }, [scope, agent, searchQuery, repoFilter, statusFilter, sort, order, offset]);

  useEffect(() => {
    load();
  }, [load]);

  /* Pagination helpers */
  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = Math.min(offset + PAGE_SIZE, total);
  const hasPrev = offset > 0;
  const hasNext = offset + PAGE_SIZE < total;

  const hasActiveFilters =
    searchInput !== "" || repoFilter !== "" || statusFilter !== "";

  return (
    <>
      {/* Header row */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-[var(--text)]">
            Sources
          </h1>
          {!loading && (
            <p className="mt-0.5 text-xs text-[var(--text-muted)]">
              {total} source session{total !== 1 ? "s" : ""} feeding context compilation
            </p>
          )}
        </div>

        <div className="flex items-center gap-3">
          {/* Agent filter */}
          <select
            aria-label="Agent"
            name="agent"
            value={agent}
            onChange={(e) => setAgent(e.target.value)}
            className="rounded-md border border-[var(--border)] bg-[var(--bg-card)] px-2.5 py-1.5 text-xs text-[var(--text)] outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
          >
            <option value="">All agents</option>
            {agents.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>

          {/* Processing status filter */}
          <select
            aria-label="Processing status"
            name="processing_status"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="rounded-md border border-[var(--border)] bg-[var(--bg-card)] px-2.5 py-1.5 text-xs text-[var(--text)] outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
          >
            <option value="">All statuses</option>
            <option value="indexed">Indexed</option>
            <option value="queued">Queued</option>
            <option value="processing">Processing</option>
            <option value="processed">Processed</option>
            <option value="failed">Failed</option>
            <option value="blocked">Blocked</option>
          </select>

          <TimeScope value={scope} onChange={setScope} />
        </div>
      </div>

      {/* Search + filter bar */}
      <div className="mt-4 flex flex-wrap items-center gap-3">
        {/* Search input */}
        <div className="relative flex-1 min-w-[200px] max-w-md">
          <svg
            className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--text-muted)]"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M21 21l-4.35-4.35m0 0A7.5 7.5 0 1 0 5.1 5.1a7.5 7.5 0 0 0 11.55 11.55z"
            />
          </svg>
          <input
            type="text"
            aria-label="Search sessions"
            name="session_search"
            autoComplete="off"
            value={searchInput}
            onChange={(e) => handleSearchChange(e.target.value)}
            placeholder="Search sessions…"
            className="w-full rounded-md border border-[var(--border)] bg-[var(--bg-subtle)] py-1.5 pl-9 pr-3 text-xs text-[var(--text)] placeholder-[var(--text-muted)] outline-none transition-colors focus:border-[var(--accent-blue)] focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
          />
        </div>

        {/* Repo filter */}
        <input
          type="text"
          aria-label="Repository filter"
          name="repo_filter"
          autoComplete="off"
          value={repoFilter}
          onChange={(e) => setRepoFilter(e.target.value)}
          placeholder="Repo filter…"
          className="min-w-[140px] rounded-md border border-[var(--border)] bg-[var(--bg-subtle)] px-3 py-1.5 text-xs text-[var(--text)] placeholder-[var(--text-muted)] outline-none transition-colors focus:border-[var(--accent-blue)] focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
        />

        {/* Clear button */}
        {hasActiveFilters && (
          <button
            type="button"
            onClick={handleClear}
            className="rounded-md border border-[var(--border)] px-3 py-1.5 text-xs text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-card)] hover:text-[var(--text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
          >
            Clear
          </button>
        )}
      </div>

      {/* Blocked sessions banner */}
      {!loading && (() => {
        const blockedSessions = sessions.filter((s) => s.processing_status === "blocked");
        if (blockedSessions.length === 0) return null;
        return (
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-500/30 bg-amber-500/5 px-4 py-3">
            <span className="text-sm text-amber-400">
              {blockedSessions.length} blocked session{blockedSessions.length !== 1 ? "s" : ""} — extraction failed after retries
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={retrying}
                onClick={async () => {
                  setRetrying(true);
                  try {
                    await Promise.all(blockedSessions.map((s) => api.retrySession(s.run_id)));
                    load();
                  } finally {
                    setRetrying(false);
                  }
                }}
                className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-1 text-xs font-medium text-amber-400 transition-colors hover:bg-amber-500/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {retrying ? "Retrying…" : "Retry All"}
              </button>
              <button
                type="button"
                onClick={() => setStatusFilter("blocked")}
                className="rounded-md border border-[var(--border)] px-3 py-1 text-xs font-medium text-[var(--text-secondary)] transition-colors hover:bg-[var(--bg-card)] hover:text-[var(--text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
              >
                View Blocked
              </button>
            </div>
          </div>
        );
      })()}

      {/* Processing stats summary */}
      {!loading && sessions.length > 0 && (() => {
        const counts: Record<string, number> = {};
        for (const s of sessions) {
          const st = s.processing_status || "indexed";
          counts[st] = (counts[st] || 0) + 1;
        }
        const parts: string[] = [];
        if (counts.processed) parts.push(`${counts.processed} processed`);
        if (counts.indexed) parts.push(`${counts.indexed} indexed`);
        if (counts.queued) parts.push(`${counts.queued} queued`);
        if (counts.processing) parts.push(`${counts.processing} processing`);
        if (counts.failed) parts.push(`${counts.failed} failed`);
        if (counts.blocked) parts.push(`${counts.blocked} blocked`);
        if (parts.length === 0) return null;
        return (
          <div className="mt-3 text-xs text-[var(--text-muted)]">
            Status: {parts.join(" \u00B7 ")}
          </div>
        );
      })()}

      {/* Error */}
      {error && (
        <div className="mt-4 rounded-lg border border-red-500/20 bg-red-500/5 px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      {/* Table */}
      <div className="mt-4">
        {loading ? (
          <div className="rounded-lg border border-[var(--border)] p-6">
            <div className="space-y-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="flex items-center gap-4">
                  <div className="h-3 w-20 animate-pulse rounded bg-white/[0.06]" />
                  <div className="h-3 w-16 animate-pulse rounded bg-white/[0.06]" />
                  <div className="h-3 w-14 animate-pulse rounded-full bg-white/[0.06]" />
                  <div className="ml-auto h-3 w-12 animate-pulse rounded bg-white/[0.06]" />
                </div>
              ))}
            </div>
          </div>
        ) : !hasActiveFilters && sessions.length === 0 ? (
          <div className="rounded-lg border border-[var(--border)] p-12 text-center">
            <p className="text-sm text-[var(--text-muted)]">No source sessions imported yet.</p>
            <p className="mt-1 text-xs text-[var(--text-muted)]">
              This page lists imported session transcripts and messages. Learned records appear in Records and Graph after ingestion and curation.
            </p>
          </div>
        ) : hasActiveFilters && sessions.length === 0 ? (
          <div className="rounded-lg border border-[var(--border)] p-12 text-center">
            <p className="text-sm text-[var(--text-muted)]">No source sessions match the current filters.</p>
            <button
              type="button"
              onClick={handleClear}
              className="mt-2 rounded-sm text-xs text-[var(--accent-blue)] transition-colors hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)]"
            >
              Clear all filters
            </button>
          </div>
        ) : (
          <SessionTable
            sessions={sessions}
            sort={sort}
            order={order}
            onSort={handleSort}
            onOpen={setViewingRunId}
          />
        )}
      </div>

      {/* Pagination */}
      {!loading && total > 0 && (
        <div className="mt-4 flex items-center justify-center gap-4">
          <button
            type="button"
            disabled={!hasPrev}
            onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            className={`rounded-md border border-[var(--border)] px-3 py-1.5 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)] ${
              hasPrev
                ? "text-[var(--text-secondary)] hover:bg-[var(--bg-card)] hover:text-[var(--text)]"
                : "cursor-not-allowed text-[var(--text-muted)] opacity-50"
            }`}
          >
            Previous
          </button>

          <span className="text-xs text-[var(--text-secondary)]">
            Showing {rangeStart}-{rangeEnd} of {total}
          </span>

          <button
            type="button"
            disabled={!hasNext}
            onClick={() => setOffset((o) => o + PAGE_SIZE)}
            className={`rounded-md border border-[var(--border)] px-3 py-1.5 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)] ${
              hasNext
                ? "text-[var(--text-secondary)] hover:bg-[var(--bg-card)] hover:text-[var(--text)]"
                : "cursor-not-allowed text-[var(--text-muted)] opacity-50"
            }`}
          >
            Next
          </button>
        </div>
      )}

      {/* Run viewer modal */}
      {viewingRunId && (
        <RunViewerModal
          runId={viewingRunId}
          onClose={() => setViewingRunId(null)}
        />
      )}
    </>
  );
}
