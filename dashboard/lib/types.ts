/* ------------------------------------------------------------------
   TypeScript types used by the local Lerim dashboard.
   ------------------------------------------------------------------ */

/* ----- Stats -------------------------------------------------------- */

export interface StatsTotals {
  runs: number;
  messages: number;
  tool_calls: number;
  errors: number;
  tokens: number;
  duration_ms: number;
}

export interface DailyStat {
  date: string;
  runs: number;
  messages: number;
  tool_calls: number;
  tokens: number;
}

export interface AgentStatEntry {
  runs: number;
  messages: number;
  tool_calls: number;
  errors: number;
  tokens: number;
}

export interface ToolUsageEntry {
  name: string;
  count: number;
}

export interface HourlyActivityEntry {
  hour: number;
  runs: number;
  tool_calls: number;
}

export interface ModelUsageEntry {
  tokens: number;
  sessions: number;
}

export interface StatsResponse {
  totals: StatsTotals;
  by_agent: Record<string, AgentStatEntry>;
  daily: DailyStat[];
  tool_usage: ToolUsageEntry[];
  hourly_activity: HourlyActivityEntry[];
  model_usage: Record<string, ModelUsageEntry>;
  data_readiness?: {
    transcript_sessions: number;
    model_usage_ready: boolean;
    tool_usage_ready: boolean;
    empty_reasons: {
      model_usage: string | null;
      tool_usage: string | null;
    };
  };
}

/* ----- Sessions ----------------------------------------------------- */

export interface Session {
  run_id: string;
  agent_type: string | null;
  ingestion_agent?: string | null;
  source_trace_ref?: string | null;
  project: string | null;
  start_time: string | null;
  status: string | null;
  processing_status: string | null;
  duration_ms: number | null;
  message_count: number;
  tool_call_count: number;
  error_count: number;
  total_tokens: number;
  summary_text: string | null;
}

export interface SessionDetail extends Session {
  repo_name: string | null;
  machine_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface SessionsResponse {
  sessions: Session[];
  total: number;
}

/* ----- Records ----------------------------------------------------- */

export interface ContextRecord {
  record_id: string;
  title: string | null;
  body: string | null;
  record_kind: string | null;
  project: string | null;
  tags: string[];
  confidence: number | null;
  status: string;
  source: string | null;
  source_session_id: string | null;
  source_event_refs?: string | null;
  evidence_refs?: string | null;
  ingestion_agent?: string | null;
  source_trace_ref?: string | null;
  changed_by_session_id?: string | null;
  change_reason?: string | null;
  valid_from?: string | null;
  valid_until?: string | null;
  superseded_by_record_id?: string | null;
  decision?: string | null;
  why?: string | null;
  alternatives?: string | null;
  consequences?: string | null;
  user_intent?: string | null;
  what_happened?: string | null;
  outcomes?: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface RecordsResponse {
  records: ContextRecord[];
  total: number;
}

export interface ContextRecordVersion extends ContextRecord {
  version_id: string;
  version_no: number;
  change_kind: string;
  changed_at: string;
}

export interface RecordVersionsResponse {
  versions: ContextRecordVersion[];
  total: number;
}

export interface RecordFiltersResponse {
  types: string[];
  projects: string[];
}

/* ----- Logs --------------------------------------------------------- */

export interface LogEntry {
  ts: string;
  level: string;
  module: string | null;
  message: string | null;
  project: string | null;
}

export interface LogsResponse {
  logs: LogEntry[];
  total: number;
}

/* ----- Transcript / Messages ---------------------------------------- */

export interface TranscriptMessage {
  role: string;
  content: string;
  timestamp: string;
  tool_calls: Array<{ name: string; id: string }>;
}

export interface MessagesResponse {
  messages: TranscriptMessage[];
  total: number;
}

/* ----- Operation Status --------------------------------------------- */

export interface PipelineLastIngest {
  time: string | null;
  agent: string | null;
  project: string | null;
}

export interface PipelineStatusResponse {
  last_ingest: PipelineLastIngest | null;
  sessions: { total: number; last_24h: number; last_7d: number };
  records: { total: number; active: number };
  logs: { total: number; errors: number };
}

/* ----- Timeline ----------------------------------------------------- */

export interface TimelineOperation {
  type: "ingest" | "curate";
  status: string;
  started_at: string;
  completed_at: string | null;
  trigger: string | null;
  project?: string | null;
  project_label?: string | null;
  scope_label?: string | null;
  scope_projects?: string[];
  duration_ms: number | null;
  details: Record<string, unknown> | null;
}

export interface TimelineRecord {
  record_id: string;
  title: string | null;
  action: "new" | "updated" | "archived";
  record_kind: string | null;
  project: string | null;
  confidence: number | null;
}

export interface TimelineEvent {
  /** Latest activity in this hour bucket (max of session / record / service_run times), for relative "Xm ago" */
  time: string;
  sessions_count: number;
  records_new: number;
  records_updated: number;
  records_archived: number;
  records: TimelineRecord[];
  operations: TimelineOperation[];
}

export interface TimelineDay {
  date: string;
  label: string;
  events: TimelineEvent[];
  summary: { sessions: number; records_new: number; records_updated: number; records_archived: number };
}

export interface TimelineResponse {
  days: TimelineDay[];
  totals: { new: number; updated: number; archived: number };
}

/* ----- Operation Report --------------------------------------------- */

export interface PipelineRunInfo {
  status: string;
  time: string | null;
  completed_at: string | null;
  trigger: string | null;
  duration_ms: number | null;
}

export interface PipelineReportResponse {
  sessions: number;
  messages: number;
  tool_calls: number;
  errors: number;
  tokens: number;
  days_span: number;
  messages_per_day: number;
  records_total: number;
  records_active: number;
  records_archived: number;
  last_ingest: PipelineRunInfo | null;
  last_curate: PipelineRunInfo | null;
}

/* ----- Record Changelog --------------------------------------------- */

export interface ChangelogEntry {
  record_id: string;
  title: string | null;
  action: "new" | "updated" | "archived";
  record_kind: string | null;
  project: string | null;
  confidence: number | null;
  time: string;
}

export interface RecordChangelogResponse {
  entries: ChangelogEntry[];
  summary: { new: number; updated: number; archived: number };
  total: number;
}

/* ----- Graph Explorer ----------------------------------------------- */

export interface GraphNode {
  id: string;
  label: string;
  kind: "record";
  confidence?: number | null;
  status?: string;
  record_kind?: string;
  project?: string;
  tags?: string[];
  body?: string;
  summary?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  semantic_cluster?: string | null;
  community_cluster?: string | null;
  combined_cluster?: string | null;
}

export interface GraphEdge {
  id?: string;
  source: string;
  target: string;
  kind: "supports" | "refines" | "depends_on" | "contradicts" | "same_topic" | "evidence_for" | "supersedes" | "related";
  label?: string | null;
  rationale?: string | null;
  weight?: number;
  evidence_record_ids?: string[];
  status?: string;
}

export interface GraphOptionsResponse {
  types: string[];
  projects: string[];
  tags: string[];
  statuses: string[];
  relation_kinds: string[];
  node_labels?: Array<{ name: string; count: number }>;
  relationship_types?: Array<{ name: string; count: number }>;
  property_keys?: Array<{ name: string; count: number }>;
  date_min: string | null;
  date_max: string | null;
}

export interface GraphQueryResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  total_records: number;
  matching_records?: number;
  returned_nodes?: number;
  returned_edges?: number;
  truncated?: boolean;
  dropped_edge_count?: number;
  graph_node_count?: number;
  active_edge_count?: number;
  connected_node_count?: number;
  projection_ready?: boolean;
  used_record_fallback?: boolean;
  graph_mode?: "empty" | "learned_graph" | "mixed" | "record_fallback" | string;
}

/* ----- Live Status -------------------------------------------------- */

export interface LiveStatusResponse {
	reachable: boolean;
	timestamp: string;
	ingest_active: boolean;
	curate_active: boolean;
	queue: {
		pending: number;
		running: number;
		failed: number;
		dead_letter: number;
		done: number;
	};
	last_ingest: {
		status: string;
		started_at: string | null;
		trigger: string | null;
	} | null;
	last_curate: {
		status: string;
		started_at: string | null;
		trigger: string | null;
	} | null;
}

/* ----- Team --------------------------------------------------------- */

export interface TeamInfo {
  team_id: string;
  name: string;
  plan: string;
  created_at: string | null;
  version?: string | null;
  tokens: number;
  usage: {
    sessions: number;
    records: number;
    logs: number;
  };
}

/* Intelligence metrics */

export interface IntelligenceSignal {
  topic: string;
  summary_count: number;
  recommendation: string;
  project: string;
  run_date: string | null;
}

export interface IntelligenceContradiction {
  record_a: string;
  record_b: string;
  resolution: string;
  project: string;
  run_date: string | null;
}

export interface IntelligenceGap {
  topic: string;
  summary_refs: string[];
  coverage: string;
  project: string;
  run_date: string | null;
}

export interface IntelligenceCrossAgent {
  agents: string[];
  topic: string;
  insight: string;
  project: string;
  run_date: string | null;
}

export interface IntelligenceCurateEntry {
  started_at: string | null;
  status: string;
  project: string;
  counts: Record<string, number>;
}

export interface IntelligenceResponse {
  health_score: number;
  record_stats: {
    total: number;
    active: number;
    archived: number;
    avg_confidence: number;
    stale_count: number;
  };
  signals: IntelligenceSignal[];
  contradictions: IntelligenceContradiction[];
  gaps: IntelligenceGap[];
  cross_agent: IntelligenceCrossAgent[];
  curate_history: IntelligenceCurateEntry[];
}

/* ----- Activity Feed ------------------------------------------------ */

export interface ActivityRecord {
	record_id: string;
	title: string;
	record_kind: string;
	confidence: number;
	action: string;
	body?: string;
	tags?: string[];
	source_speaker?: string;
	durability?: string;
}

export interface ActivitySession {
	run_id: string;
	agent_type: string;
	project: string;
	repo_name: string;
	summary_text: string;
	start_time: string | null;
	message_count: number;
	total_tokens: number;
	tool_call_count: number;
	processing_status: string;
	processing_error: string | null;
	records_created: ActivityRecord[];
	records_skipped: number;
}

export interface ActivityFeedItem {
	id?: string;
	type: "ingest" | "curate";
	started_at: string | null;
	completed_at: string | null;
	status: string;
	duration_s: number | null;
	// Ingest-specific
	sessions?: ActivitySession[];
	total_records_created?: number;
	total_sessions?: number;
	// Curate-specific
	counts?: { created: number; updated: number; archived: number };
	actions?: { action: string; description: string }[];
}

export interface ActivityFeedResponse {
	items: ActivityFeedItem[];
}

/* ----- Generated Memory Artifacts ---------------------------------- */

export type MemoryArtifactType = "context_brief" | "working_memory";

export interface MemoryArtifactStatus {
	availability: string;
	project: string;
	project_id: string;
	repo_path: string;
	generated_at: string | null;
	age_seconds: number | null;
	age: string;
	records_included: number;
	records_changed_since_generation: number;
	records_missing_since_generation: number;
	current_file: string;
	current_manifest: string;
	latest_run_folder: string | null;
	suggested_action: string;
	window_hours?: number;
	window_started_at?: string | null;
	recent_versions_considered?: number;
}

export interface MemoryArtifactVersion {
	id: string;
	type: MemoryArtifactType;
	label: string;
	filename: string;
	content: string;
	content_path: string;
	manifest_path: string;
	current: boolean;
	generated_at: string;
	trigger: string;
	status: string;
	run_folder: string;
	records_included: number;
	records_considered: number;
	recent_versions_considered: number;
	included_record_ids: string[];
}

export interface MemoryArtifact {
	type: MemoryArtifactType;
	label: string;
	status: MemoryArtifactStatus;
	current: MemoryArtifactVersion;
	versions: MemoryArtifactVersion[];
}

export interface MemoryArtifactsResponse {
	projects: string[];
	selected_project: string;
	project_id: string;
	repo_path: string;
	artifacts: {
		context_brief?: MemoryArtifact;
		working_memory?: MemoryArtifact;
	};
	versions: MemoryArtifactVersion[];
	error?: string;
}
