import { formatRecordKind, formatRecordRole, formatScopeLabel, humanizeToken } from "@/lib/labels";
import type { ContextRecord } from "@/lib/types";

interface RecordEditorProps {
  record: ContextRecord;
}

export default function RecordEditor({ record }: RecordEditorProps) {
  const rolePayload = parseRolePayload(record.role_payload);
  return (
    <div className="flex h-full flex-col rounded-lg border border-[var(--border)] bg-[var(--bg-subtle)]">
      <div className="flex items-center justify-between border-b border-[var(--border)] px-5 py-3">
        <h2 id="record-editor-title" className="text-sm font-semibold text-[var(--text)]">
          Record
        </h2>
        <span className="rounded bg-white/[0.06] px-2 py-0.5 text-[11px] font-medium text-[var(--text-muted)]">
          Read-only
        </span>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
        <Field label="Title" value={record.title || "Untitled"} />

        <div className="flex flex-wrap items-center gap-3">
          <Pill label="Type" value={formatRecordKind(record.record_kind)} />
          {record.record_role && record.record_role !== "general" && (
            <Pill label="Role" value={formatRecordRole(record.record_role)} />
          )}
          {record.project && <Pill label="Project" value={formatScopeLabel(record.project)} />}
          <Pill label="Status" value={record.status || "unknown"} />
        </div>

        <div>
          <p className="mb-1.5 text-xs font-medium text-[var(--text-secondary)]">Record ID</p>
          <p className="break-all font-mono text-xs text-[var(--text-muted)]">{record.record_id}</p>
        </div>

        {(record.source_session_id || record.source) && (
          <div>
            <p className="mb-1.5 text-xs font-medium text-[var(--text-secondary)]">Source</p>
            <p className="break-all font-mono text-xs text-[var(--text-muted)]">
              {record.source_session_id || record.source}
            </p>
          </div>
        )}

        <div>
          <p className="mb-1.5 text-xs font-medium text-[var(--text-secondary)]">Body</p>
          <div className="min-h-32 whitespace-pre-wrap rounded-md border border-[var(--border)] bg-[var(--bg-card)] px-3 py-2 text-sm leading-relaxed text-[var(--text)]">
            {record.body || "No body stored for this record."}
          </div>
        </div>

        {rolePayload.length > 0 && (
          <div>
            <p className="mb-1.5 text-xs font-medium text-[var(--text-secondary)]">Role Payload</p>
            <div className="space-y-2 rounded-md border border-[var(--border)] bg-[var(--bg-card)] px-3 py-2">
              {rolePayload.map(([key, value]) => (
                <div key={key} className="grid gap-1 text-sm sm:grid-cols-[130px_minmax(0,1fr)]">
                  <span className="text-xs font-medium text-[var(--text-muted)]">{humanizeToken(key)}</span>
                  <span className="whitespace-pre-wrap break-words text-[var(--text)]">{value}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Created" value={record.created_at || "unknown"} />
          <Field label="Updated" value={record.updated_at || "unknown"} />
        </div>
      </div>
    </div>
  );
}

function parseRolePayload(payload?: string | null): Array<[string, string]> {
  const text = payload?.trim();
  if (!text) return [];
  try {
    const parsed = JSON.parse(text) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return [["payload", text]];
    return Object.entries(parsed).map(([key, value]) => [key, renderPayloadValue(value)]);
  } catch {
    return [["payload", text]];
  }
}

function renderPayloadValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(renderPayloadValue).join("\n");
  if (value && typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value ?? "");
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="mb-1.5 text-xs font-medium text-[var(--text-secondary)]">{label}</p>
      <p className="rounded-md border border-[var(--border)] bg-[var(--bg-card)] px-3 py-2 text-sm text-[var(--text)]">
        {value}
      </p>
    </div>
  );
}

function Pill({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="mb-1.5 text-xs font-medium text-[var(--text-secondary)]">{label}</p>
      <span className="inline-block rounded bg-white/[0.06] px-2 py-1 text-xs text-[var(--text-muted)]">
        {value}
      </span>
    </div>
  );
}
