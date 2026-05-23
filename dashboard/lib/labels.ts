const RECORD_KIND_LABELS: Record<string, string> = {
  decision: "Decision",
  constraint: "Constraint",
  preference: "Preference",
  fact: "Fact",
  reference: "Reference",
  episode: "Episode",
};

const STATUS_LABELS: Record<string, string> = {
  active: "Active",
  archived: "Archived",
  indexed: "Indexed",
  queued: "Queued",
  processing: "Processing",
  processed: "Processed",
  failed: "Failed",
  blocked: "Blocked",
  completed: "Completed",
  success: "Succeeded",
  ok: "Succeeded",
  running: "Running",
  started: "Started",
  interrupted: "Interrupted",
};

export function humanizeToken(value?: string | null, fallback = "Unknown"): string {
  const trimmed = value?.trim();
  if (!trimmed) return fallback;
  return trimmed
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

export function formatRecordKind(kind?: string | null): string {
  const trimmed = kind?.trim();
  if (!trimmed) return "Unknown kind";
  return RECORD_KIND_LABELS[trimmed] ?? humanizeToken(trimmed);
}

export function formatScopeLabel(scope?: string | null): string {
  const trimmed = scope?.trim();
  return trimmed || "Unscoped";
}

export function formatStatusLabel(status?: string | null): string {
  const trimmed = status?.trim();
  if (!trimmed) return "Unknown";
  return STATUS_LABELS[trimmed] ?? humanizeToken(trimmed);
}
