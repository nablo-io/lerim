"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ProjectSummary } from "@/lib/types";

interface ProjectScopeProps {
  value: string;
  onChange: (project: string) => void;
  includeAll?: boolean;
  className?: string;
  label?: string;
}

export default function ProjectScope({
  value,
  onChange,
  includeAll = true,
  className = "",
  label = "Dashboard scope",
}: ProjectScopeProps) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setError(false);
    api
      .getProjects()
      .then((items) => {
        if (cancelled) return;
        setProjects(items);
        setError(false);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error || (!includeAll && projects.length === 0 && !loading)) {
    return null;
  }

  return (
    <label className="flex min-w-0 flex-wrap items-center gap-2">
      <span className="text-[11px] font-medium uppercase tracking-wide text-[var(--text-muted)]">
        {label}
      </span>
      <select
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={loading}
        className={`min-h-10 max-w-full rounded-md border border-[var(--border)] bg-[var(--bg-card)] px-3 text-xs text-[var(--text-secondary)] outline-none focus:border-[var(--accent-blue)] disabled:cursor-wait disabled:opacity-60 ${className}`}
      >
        {includeAll && <option value="">All projects</option>}
        {!includeAll && !value && <option value="">Select project</option>}
        {loading ? (
          <option value={value}>Loading projects...</option>
        ) : (
          projects.map((project) => (
            <option key={project.name} value={project.name}>
              {projectOptionLabel(project)}
            </option>
          ))
        )}
      </select>
    </label>
  );
}

function projectOptionLabel(project: ProjectSummary) {
  const active = Number(project.active_record_count ?? project.record_count ?? 0);
  const total = Number(project.total_record_count ?? active);
  if (total > active) {
    return `${project.name} · ${active.toLocaleString()} active / ${total.toLocaleString()} total`;
  }
  if (active > 0) {
    return `${project.name} · ${active.toLocaleString()} active`;
  }
  return project.name;
}
