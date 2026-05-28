import type {
  ActivityFeedResponse,
  ContextRecord,
  ContextRecordVersion,
  GraphQueryResponse,
  IntelligenceResponse,
  LiveStatusResponse,
  LogsResponse,
  MemoryArtifactsResponse,
  MessagesResponse,
  PipelineReportResponse,
  PipelineStatusResponse,
  RecordFiltersResponse,
  RecordVersionsResponse,
  RecordsResponse,
  Session,
  SessionDetail,
  SessionsResponse,
  StatsResponse,
  TeamInfo,
  TimelineEvent,
  TimelineOperation,
  TimelineResponse,
} from "./types";

const API_BASE = "";

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!res.ok) {
    let detail = `API error: ${res.status}`;
    try {
      const payload = await res.json();
      if (typeof payload.error === "string") detail = payload.error;
      else if (typeof payload.message === "string") detail = payload.message;
    } catch {
      // Keep the status-only fallback when the response is not JSON.
    }
    throw new Error(detail);
  }
  return res.json();
}

function toQuery(params?: Record<string, string>) {
  const qs = new URLSearchParams(params).toString();
  return qs ? `?${qs}` : "";
}

function normalizeSession(run: Record<string, unknown>): Session {
  return {
    run_id: String(run.run_id || ""),
    agent_type: asNullableString(run.agent_type),
    ingestion_agent: asNullableString(run.agent_type),
    source_trace_ref: asNullableString(run.session_path),
    project: asNullableString(run.project),
    start_time: asNullableString(run.started_at),
    status: asNullableString(run.status),
    processing_status: asNullableString(run.status),
    duration_ms: asNullableNumber(run.duration_ms),
    message_count: asNumber(run.message_count),
    tool_call_count: asNumber(run.tool_call_count),
    error_count: asNumber(run.error_count),
    total_tokens: asNumber(run.total_tokens),
    summary_text: asNullableString(run.preview) || asNullableString(run.snippet),
  };
}

function normalizeSessionDetail(run: Record<string, unknown>): SessionDetail {
  const session = normalizeSession(run);
  const timestamp = session.start_time || new Date().toISOString();
  return {
    ...session,
    repo_name: asNullableString(run.repo_name),
    machine_id: null,
    created_at: timestamp,
    updated_at: timestamp,
  };
}

function normalizeRecord(row: Record<string, unknown>): ContextRecord {
  const project = asNullableString(row.scope_label) || asNullableString(row.project_id);
  return {
    record_id: String(row.record_id || ""),
    title: asNullableString(row.title),
    body: asNullableString(row.body),
    record_kind: asNullableString(row.kind),
    project,
    tags: [],
    confidence: null,
    status: String(row.status || "active"),
    source: asNullableString(row.source_name),
    source_session_id: asNullableString(row.source_session_id),
    source_event_refs: asNullableString(row.source_event_refs),
    evidence_refs: asNullableString(row.evidence_refs),
    ingestion_agent: asNullableString(row.source_profile),
    source_trace_ref: asNullableString(row.source_session_id),
    changed_by_session_id: null,
    change_reason: null,
    valid_from: asNullableString(row.valid_from),
    valid_until: asNullableString(row.valid_until),
    superseded_by_record_id: asNullableString(row.superseded_by_record_id),
    decision: asNullableString(row.decision),
    why: asNullableString(row.why),
    alternatives: asNullableString(row.alternatives),
    consequences: asNullableString(row.consequences),
    user_intent: asNullableString(row.user_intent),
    what_happened: asNullableString(row.what_happened),
    outcomes: asNullableString(row.outcomes),
    created_at: asNullableString(row.created_at),
    updated_at: asNullableString(row.updated_at),
  };
}

/** Normalize one record-version row from the deterministic query API. */
function normalizeRecordVersion(row: Record<string, unknown>): ContextRecordVersion {
  return {
    ...normalizeRecord(row),
    version_id: String(row.version_id || ""),
    version_no: asNumber(row.version_no),
    change_kind: String(row.change_kind || ""),
    change_reason: asNullableString(row.change_reason),
    changed_at: String(row.changed_at || row.updated_at || row.created_at || ""),
    changed_by_session_id: asNullableString(row.changed_by_session_id),
  };
}

function normalizeStats(raw: Record<string, unknown>): StatsResponse {
  const totals = objectValue(raw.totals);
  const byAgentRaw = objectValue(raw.by_agent);
  const dailyRaw = arrayValue(raw.daily_activity);
  const hourlyRaw = arrayValue(raw.hourly_activity);
  const toolUsageRaw = objectValue(raw.tool_usage);
  const modelUsageRaw = objectValue(raw.model_usage);

  return {
    totals: {
      runs: asNumber(totals.runs),
      messages: asNumber(totals.messages),
      tool_calls: asNumber(totals.tool_calls),
      errors: asNumber(totals.errors),
      tokens: asNumber(totals.tokens),
      duration_ms: asNumber(totals.duration_ms),
    },
    by_agent: Object.fromEntries(
      Object.entries(byAgentRaw).map(([name, value]) => {
        const item = objectValue(value);
        return [
          name,
          {
            runs: asNumber(item.runs),
            messages: asNumber(item.messages),
            tool_calls: asNumber(item.tool_calls),
            errors: asNumber(item.errors),
            tokens: asNumber(item.tokens),
          },
        ];
      }),
    ),
    daily: dailyRaw.map((value) => {
      const item = objectValue(value);
      return {
        date: String(item.date || ""),
        runs: asNumber(item.sessions) || countAgentRuns(item),
        messages: asNumber(item.messages),
        tool_calls: asNumber(item.tool_calls),
        tokens: asNumber(item.tokens),
      };
    }),
    tool_usage: Object.entries(toolUsageRaw).map(([name, count]) => ({
      name,
      count: asNumber(count),
    })),
    hourly_activity: hourlyRaw.map((value) => {
      const item = objectValue(value);
      return {
        hour: asNumber(item.hour),
        runs: asNumber(item.sessions),
        tool_calls: asNumber(item.tool_calls),
      };
    }),
    model_usage: Object.fromEntries(
      Object.entries(modelUsageRaw).map(([name, value]) => {
        const item = objectValue(value);
        return [
          name,
          {
            tokens: asNumber(item.total),
            sessions: asNumber(item.sessions),
          },
        ];
      }),
    ),
    data_readiness: {
      transcript_sessions: dailyRaw.length,
      model_usage_ready: Object.keys(modelUsageRaw).length > 0,
      tool_usage_ready: Object.keys(toolUsageRaw).length > 0,
      empty_reasons: {
        model_usage: Object.keys(modelUsageRaw).length ? null : "No model metadata found in indexed source traces yet.",
        tool_usage: Object.keys(toolUsageRaw).length ? null : "No tool-call metadata found in indexed source traces yet.",
      },
    },
  };
}

function normalizePipelineStatus(raw: Record<string, unknown>): PipelineStatusResponse {
  const latest = objectOrNull(raw.latest_ingest);
  const queue = objectValue(raw.queue);
  return {
    last_ingest: latest
      ? {
          time: asNullableString(latest.started_at),
          agent: "ingest",
          project: projectLabelFromRun(latest),
        }
      : null,
    sessions: {
      total: asNumber(raw.sessions_indexed_count),
      last_24h: 0,
      last_7d: 0,
    },
    records: {
      total: asNumber(raw.record_count),
      active: asNumber(raw.record_count),
    },
    logs: {
      total: 0,
      errors: asNumber(queue.failed) + asNumber(queue.dead_letter),
    },
  };
}

function normalizeLiveStatus(raw: Record<string, unknown>): LiveStatusResponse {
  const queue = objectValue(raw.queue);
  return {
    reachable: true,
    timestamp: String(raw.timestamp || new Date().toISOString()),
    ingest_active: Boolean(raw.ingest_active),
    curate_active: Boolean(raw.curate_active),
    queue: {
      pending: asNumber(queue.pending),
      running: asNumber(queue.running),
      failed: asNumber(queue.failed),
      dead_letter: asNumber(queue.dead_letter),
      done: asNumber(queue.done),
    },
    last_ingest: normalizeLiveRun(raw.last_ingest),
    last_curate: normalizeLiveRun(raw.last_curate),
  };
}

function normalizeLiveRun(value: unknown) {
  const run = objectOrNull(value);
  if (!run) return null;
  return {
    status: String(run.status || "unknown"),
    started_at: asNullableString(run.started_at),
    trigger: asNullableString(run.trigger),
  };
}

function normalizeReport(raw: Record<string, unknown>): PipelineReportResponse {
  const totals = objectValue(objectValue(raw.aggregates).totals);
  return {
    sessions: asNumber(totals.sessions),
    messages: asNumber(totals.messages),
    tool_calls: asNumber(totals.tool_calls),
    errors: asNumber(totals.errors),
    tokens: asNumber(totals.tokens),
    days_span: 0,
    messages_per_day: 0,
    records_total: 0,
    records_active: 0,
    records_archived: 0,
    last_ingest: null,
    last_curate: null,
  };
}

function normalizeTimeline(raw: Record<string, unknown>): TimelineResponse {
  const rows = arrayValue(raw.recent_activity);
  const eventsByDay = new Map<string, TimelineEvent[]>();
  for (const value of rows) {
    const item = objectValue(value);
    const time = String(item.time || item.started_at || new Date().toISOString());
    const day = time.slice(0, 10);
    const operation = normalizeOperation(item);
    const event: TimelineEvent = {
      time,
      sessions_count: asNumber(item.sessions_analyzed) || asNumber(item.sessions_extracted),
      records_new: asNumber(item.records_created),
      records_updated: asNumber(item.records_updated),
      records_archived: asNumber(item.records_archived),
      records: [],
      operations: [operation],
    };
    const events = eventsByDay.get(day) || [];
    events.push(event);
    eventsByDay.set(day, events);
  }
  const days = Array.from(eventsByDay.entries())
    .sort(([left], [right]) => right.localeCompare(left))
    .map(([date, events]) => ({
      date,
      label: date,
      events,
      summary: {
        sessions: events.reduce((sum, event) => sum + event.sessions_count, 0),
        records_new: events.reduce((sum, event) => sum + event.records_new, 0),
        records_updated: events.reduce((sum, event) => sum + event.records_updated, 0),
        records_archived: events.reduce((sum, event) => sum + event.records_archived, 0),
      },
    }));
  return {
    days,
    totals: {
      new: days.reduce((sum, day) => sum + day.summary.records_new, 0),
      updated: days.reduce((sum, day) => sum + day.summary.records_updated, 0),
      archived: days.reduce((sum, day) => sum + day.summary.records_archived, 0),
    },
  };
}

function normalizeOperation(item: Record<string, unknown>): TimelineOperation {
  const type = String(item.op_type || item.job_type || "ingest") === "curate" ? "curate" : "ingest";
  return {
    type,
    status: String(item.status || "unknown"),
    started_at: String(item.time || item.started_at || new Date().toISOString()),
    completed_at: asNullableString(item.completed_at),
    trigger: asNullableString(item.trigger),
    project: projectLabelFromRun(item),
    project_label: projectLabelFromRun(item),
    scope_label: projectLabelFromRun(item),
    scope_projects: arrayValue(item.projects).map(String),
    duration_ms: asNullableNumber(item.duration_ms),
    details: item,
  };
}

function normalizeActivityFeed(raw: Record<string, unknown>): ActivityFeedResponse {
  return {
    items: arrayValue(raw.recent_activity).map((value, index) => {
      const item = objectValue(value);
      const type = String(item.op_type || item.job_type || "ingest") === "curate" ? "curate" : "ingest";
      return {
        id: String(item.id || `${type}-${index}`),
        type,
        status: String(item.status || "unknown"),
        started_at: String(item.time || item.started_at || ""),
        completed_at: asNullableString(item.completed_at),
        duration_s: asNullableNumber(item.duration_ms) == null ? null : Math.round(asNumber(item.duration_ms) / 1000),
        total_sessions: asNumber(item.sessions_analyzed) || asNumber(item.sessions_extracted),
        total_records_created: asNumber(item.records_created),
        counts: {
          created: asNumber(item.records_created),
          updated: asNumber(item.records_updated),
          archived: asNumber(item.records_archived),
        },
      };
    }),
  };
}

function normalizeIntelligence(status: Record<string, unknown>, records: ContextRecord[]): IntelligenceResponse {
  const active = records.filter((record) => record.status !== "archived").length;
  const archived = records.length - active;
  const curateRuns = arrayValue(status.recent_activity)
    .map(objectValue)
    .filter((item) => String(item.op_type || item.job_type) === "curate")
    .slice(0, 5);
  return {
    health_score: records.length === 0 ? 0 : Math.max(0, Math.min(100, Math.round((active / records.length) * 100))),
    record_stats: {
      total: records.length,
      active,
      archived,
      avg_confidence: 0,
      stale_count: 0,
    },
    signals: [],
    contradictions: [],
    gaps: [],
    cross_agent: [],
    curate_history: curateRuns.map((run) => ({
      started_at: asNullableString(run.time) || asNullableString(run.started_at),
      status: String(run.status || "unknown"),
      project: projectLabelFromRun(run) || "all",
      counts: objectValue(run.curate_counts) as Record<string, number>,
    })),
  };
}

async function queryRecords(params?: Record<string, string>): Promise<RecordsResponse> {
  const payload = await apiFetch<Record<string, unknown>>("/api/query", {
    method: "POST",
    body: JSON.stringify({
      entity: "records",
      mode: "list",
      scope: "all",
      kind: params?.record_kind || undefined,
      status: params?.status || undefined,
      source_session_id: params?.source_session_id || undefined,
      order_by: "updated_at",
      limit: Number(params?.limit || 200),
      offset: Number(params?.offset || 0),
      include_total: true,
    }),
  });
  const records = arrayValue(payload.rows).map((row) => normalizeRecord(objectValue(row)));
  return {
    records,
    total: asNumber(payload.total) || records.length,
  };
}

/** Fetch record-version history for memory lifecycle charts. */
async function queryRecordVersions(params?: Record<string, string>): Promise<RecordVersionsResponse> {
  const payload = await apiFetch<Record<string, unknown>>("/api/query", {
    method: "POST",
    body: JSON.stringify({
      entity: "versions",
      mode: "list",
      scope: "all",
      kind: params?.record_kind || undefined,
      source_session_id: params?.source_session_id || undefined,
      order_by: "updated_at",
      limit: Number(params?.limit || 5000),
      offset: Number(params?.offset || 0),
      include_total: true,
    }),
  });
  const versions = arrayValue(payload.rows).map((row) => normalizeRecordVersion(objectValue(row)));
  return {
    versions,
    total: asNumber(payload.total) || versions.length,
  };
}

export const api = {
  getStats: async (scope?: string, _extended?: boolean) => {
    const raw = await apiFetch<Record<string, unknown>>(`/api/runs/stats?scope=${scope || "week"}`);
    return normalizeStats(raw);
  },

  getSessions: async (params?: Record<string, string>) => {
    const localParams: Record<string, string> = {
      scope: params?.scope || "week",
      limit: params?.limit || "30",
      offset: params?.offset || "0",
    };
    if (params?.agent_type) localParams.agent_type = params.agent_type;
    const raw = await apiFetch<Record<string, unknown>>(`/api/runs${toQuery(localParams)}`);
    const sessions = arrayValue(raw.runs).map((run) => normalizeSession(objectValue(run)));
    const pagination = objectValue(raw.pagination);
    return {
      sessions,
      total: asNumber(pagination.total) || sessions.length,
    };
  },

  getSession: async (runId: string) => {
    const data = await api.getSessions({ scope: "all", limit: "200", offset: "0" });
    const found = data.sessions.find((session) => session.run_id === runId);
    if (!found) throw new Error("Session not found in the local dashboard window.");
    return normalizeSessionDetail(found as unknown as Record<string, unknown>);
  },

  getSessionMessages: async (runId: string) => {
    const raw = await apiFetch<Record<string, unknown>>(`/api/runs/${encodeURIComponent(runId)}/messages`);
    const messages = arrayValue(raw.messages).map((value) => {
      const item = objectValue(value);
      const toolName = asNullableString(item.tool_name);
      return {
        role: String(item.role || "assistant"),
        content: String(item.content || ""),
        timestamp: String(item.timestamp || ""),
        tool_calls: toolName ? [{ name: toolName, id: toolName }] : [],
      };
    });
    return { messages, total: messages.length } satisfies MessagesResponse;
  },

  retrySession: (runId: string) =>
    apiFetch<{ ok: boolean }>(`/api/jobs/${encodeURIComponent(runId)}/retry`, { method: "POST" }),

  skipSession: (runId: string) =>
    apiFetch<{ ok: boolean }>(`/api/jobs/${encodeURIComponent(runId)}/skip`, { method: "POST" }),

  search: async (params?: Record<string, string>) => {
    const localParams: Record<string, string> = {
      scope: params?.scope || "week",
      limit: params?.limit || "30",
      offset: params?.offset || "0",
      query: params?.q || "",
    };
    if (params?.agent_type) localParams.agent_type = params.agent_type;
    if (params?.repo) localParams.repo = params.repo;
    if (params?.processing_status) localParams.status = params.processing_status;
    const raw = await apiFetch<Record<string, unknown>>(`/api/search${toQuery(localParams)}`);
    const sessions = arrayValue(raw.results).map((run) => normalizeSession(objectValue(run)));
    const pagination = objectValue(raw.pagination);
    return {
      sessions,
      total: asNumber(pagination.total) || sessions.length,
    } satisfies SessionsResponse;
  },

  getRecordFilters: async (): Promise<RecordFiltersResponse> => {
    const data = await queryRecords({ limit: "500" });
    return {
      types: Array.from(new Set(data.records.map((record) => record.record_kind).filter((kind): kind is string => Boolean(kind)))).sort(),
      projects: Array.from(new Set(data.records.map((record) => record.project).filter((project): project is string => Boolean(project)))).sort(),
    };
  },

  getRecords: queryRecords,

  getRecordVersions: queryRecordVersions,

  getRecord: async (recordId: string) => {
    const data = await queryRecords({ limit: "500" });
    const found = data.records.find((record) => record.record_id === recordId);
    if (!found) throw new Error("Record not found.");
    return found;
  },

  getLogs: async (_params?: Record<string, string>): Promise<LogsResponse> => ({ logs: [], total: 0 }),

  getPipelineStatus: async () => {
    const raw = await apiFetch<Record<string, unknown>>("/api/status");
    return normalizePipelineStatus(raw);
  },

  getTimeline: async (_days?: number) => {
    const raw = await apiFetch<Record<string, unknown>>("/api/status");
    return normalizeTimeline(raw);
  },

  getPipelineReport: async () => {
    const raw = await apiFetch<Record<string, unknown>>("/api/refine/report");
    return normalizeReport(raw);
  },

  getTeamInfo: async (): Promise<TeamInfo> => {
    const [health, status] = await Promise.all([
      apiFetch<Record<string, unknown>>("/api/health"),
      apiFetch<Record<string, unknown>>("/api/status"),
    ]);
    return {
      team_id: "local",
      name: "Local Lerim Runtime",
      plan: String(objectValue(status.runtime).source || "local"),
      created_at: null,
      tokens: 0,
      usage: {
        sessions: asNumber(status.sessions_indexed_count),
        records: asNumber(status.record_count),
        logs: 0,
      },
      version: asNullableString(health.version),
    };
  },

  queryGraph: async (body: { max_nodes?: number; max_edges?: number; connected_only?: boolean }): Promise<GraphQueryResponse> => {
    return apiFetch<GraphQueryResponse>("/api/graph/query", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  getIntelligence: async (_limit?: number) => {
    const [status, records] = await Promise.all([
      apiFetch<Record<string, unknown>>("/api/status"),
      queryRecords({ limit: "500" }),
    ]);
    return normalizeIntelligence(status, records.records);
  },

  getLiveStatus: async () => {
    const raw = await apiFetch<Record<string, unknown>>("/api/live");
    return normalizeLiveStatus(raw);
  },

  getMemoryArtifacts: async (project?: string): Promise<MemoryArtifactsResponse> => {
    const qs = project ? toQuery({ project }) : "";
    return apiFetch<MemoryArtifactsResponse>(`/api/memory-artifacts${qs}`);
  },

  retryAllDeadLetter: () =>
    apiFetch<{ retried: number }>("/api/jobs/retry-all", { method: "POST" }),

  skipAllDeadLetter: () =>
    apiFetch<{ skipped: number }>("/api/jobs/skip-all", { method: "POST" }),

  getActivityFeed: async (_days?: number, _limit?: number) => {
    const raw = await apiFetch<Record<string, unknown>>("/api/status");
    return normalizeActivityFeed(raw);
  },
};

function asNullableString(value: unknown): string | null {
  if (typeof value !== "string") return value == null ? null : String(value);
  const text = value.trim();
  return text ? text : null;
}

function asNullableNumber(value: unknown): number | null {
  if (value == null || value === "") return null;
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? numberValue : null;
}

function asNumber(value: unknown): number {
  return asNullableNumber(value) ?? 0;
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function objectOrNull(value: unknown): Record<string, unknown> | null {
  const object = objectValue(value);
  return Object.keys(object).length > 0 ? object : null;
}

function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function countAgentRuns(item: Record<string, unknown>): number {
  return Object.entries(item).reduce((sum, [key, value]) => {
    if (["date", "messages", "tool_calls", "tokens"].includes(key)) return sum;
    return sum + asNumber(value);
  }, 0);
}

function projectLabelFromRun(run: Record<string, unknown>): string | null {
  return asNullableString(run.project_label) || asNullableString(run.scope_label) || asNullableString(run.project);
}
