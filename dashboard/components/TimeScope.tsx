"use client";

const SCOPES = [
  { value: "today", label: "Today" },
  { value: "week", label: "Week" },
  { value: "month", label: "Month" },
  { value: "all", label: "All" },
] as const;

interface TimeScopeProps {
  value: string;
  onChange: (scope: string) => void;
}

export default function TimeScope({ value, onChange }: TimeScopeProps) {
  return (
    <div className="inline-flex items-center rounded-md border border-[var(--border)] bg-[var(--bg-card)]" role="group" aria-label="Time range">
      {SCOPES.map(({ value: v, label }) => (
        <button
          key={v}
          type="button"
          aria-pressed={value === v}
          onClick={() => onChange(v)}
          className={`px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-blue)] ${
            value === v
              ? "bg-white/[0.08] text-[var(--text)]"
              : "text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
          } ${v === "today" ? "rounded-l-md" : ""} ${v === "all" ? "rounded-r-md" : ""}`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
