"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { TeamInfo } from "@/lib/types";

export default function SettingsPage() {
  const [runtime, setRuntime] = useState<TeamInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getTeamInfo()
      .then((data) => {
        if (!cancelled) setRuntime(data);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load runtime settings");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <div className="mt-8 text-center text-sm text-[var(--text-muted)]">
        Loading...
      </div>
    );
  }

  if (error) {
    return (
      <div className="mt-8 rounded-lg border border-red-500/20 bg-red-500/5 px-4 py-3 text-sm text-red-400">
        {error}
      </div>
    );
  }

  return (
    <>
      <h1 className="text-lg font-semibold text-[var(--text)]">Settings</h1>

      <div className="mt-6 space-y-4">
        <section className="rounded-lg border border-[var(--border)] bg-[var(--bg-subtle)]">
          <div className="border-b border-[var(--border)] px-5 py-3">
            <h2 className="text-sm font-medium text-[var(--text)]">Runtime</h2>
          </div>
          <div className="space-y-3 px-5 py-4">
            <Row label="Name" value={runtime?.name ?? "Local Lerim Runtime"} />
            <Row label="Source" value={runtime?.plan ?? "local"} />
            <Row label="Version" value={runtime?.version ?? "unknown"} />
          </div>
        </section>

        <section className="rounded-lg border border-[var(--border)] bg-[var(--bg-subtle)]">
          <div className="border-b border-[var(--border)] px-5 py-3">
            <h2 className="text-sm font-medium text-[var(--text)]">Usage</h2>
          </div>
          <div className="divide-y divide-[var(--border)]">
            <UsageRow label="Sessions" value={runtime?.usage.sessions ?? 0} />
            <UsageRow label="Records" value={runtime?.usage.records ?? 0} />
          </div>
        </section>

        <section className="rounded-lg border border-[var(--border)] bg-[var(--bg-subtle)]">
          <div className="border-b border-[var(--border)] px-5 py-3">
            <h2 className="text-sm font-medium text-[var(--text)]">API</h2>
          </div>
          <div className="space-y-3 px-5 py-4 text-sm text-[var(--text-secondary)]">
            <Row label="Endpoint" value={process.env.LERIM_API_URL || "same-origin /api"} />
            <p className="text-xs text-[var(--text-muted)]">
              The open-source dashboard talks directly to the local `lerim serve` API.
            </p>
          </div>
        </section>
      </div>
    </>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-3">
      <span className="w-28 shrink-0 text-xs text-[var(--text-muted)]">{label}</span>
      <span className="text-sm text-[var(--text)]">{value}</span>
    </div>
  );
}

function UsageRow({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center gap-4 px-5 py-3">
      <span className="w-24 shrink-0 text-sm text-[var(--text)]">{label}</span>
      <span className="w-20 text-right font-mono text-sm tabular-nums text-[var(--text)]">
        {value.toLocaleString()}
      </span>
    </div>
  );
}
