interface StatsCardProps {
  label: string;
  value: string | number;
  sub?: string;
  secondary?: string;
}

export default function StatsCard({ label, value, sub, secondary }: StatsCardProps) {
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-5">
      <p className="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider">
        {label}
      </p>
      <p className="mt-2 text-2xl font-semibold tabular-nums text-[var(--text)]">
        {typeof value === "number" ? value.toLocaleString() : value}
      </p>
      {secondary && (
        <p className="mt-1 text-xs text-[var(--text-secondary)]">{secondary}</p>
      )}
      {sub && <p className="mt-1 text-xs text-[var(--text-muted)]">{sub}</p>}
    </div>
  );
}
