// Backend API sözleşmesi (sre_platform/api/schemas.py ile eşleşir)

export type Role = "admin" | "sre" | "viewer";
export type InvestigationStatus = "PENDING" | "RUNNING" | "COMPLETED" | "PARTIAL" | "FAILED" | "CANCELLED";
export type Severity = "low" | "medium" | "high" | "critical" | "unknown";
export type Confidence = "high" | "medium" | "low";
export type Trigger = "chat" | "manual" | "scheduled" | "watch" | "import";
export type IncidentStatus = "OPEN" | "INVESTIGATING" | "MITIGATED" | "RESOLVED";
export type Verdict = "CORRECT" | "PARTIALLY_CORRECT" | "INCORRECT";
export type HealthStatus = "healthy" | "degraded" | "down" | "unknown";
export type RangeKey = "15m" | "1h" | "6h" | "24h";

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

export interface ApiErrorBody {
  error: string;
  message: string;
  correlation_id: string;
  details?: unknown;
}

export interface User {
  id: string;
  username: string;
  display_name: string;
  role: Role;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface TokenOut {
  access_token: string;
  token_type: "bearer";
  expires_at: string;
  user: User;
}

export interface Application {
  id: string;
  name: string;
  description: string | null;
  kind: "business" | "tooling";
  host: string | null;
  db_role: string | null;
}

export interface SeriesPoint {
  t: string;
  rpm: number | null;
  error_rate: number | null;
  mean_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  pool_ratio: number | null;
}

export interface PoolInfo {
  max_ratio: number | null;
  last_ratio: number | null;
  pending_max: number | null;
}

export interface AppHealth {
  status: HealthStatus;
  reasons: string[];
  throughput_rpm: number | null;
  error_rate: number | null;
  mean_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  span_samples: number;
  pool: PoolInfo;
  series: SeriesPoint[];
}

export interface Overall {
  throughput_rpm?: number | null;
  error_rate?: number | null;
  mean_ms?: number | null;
  p95_ms_worst?: number | null;
  p99_ms_worst?: number | null;
  db_connection_ratio?: number | null;
  db_connection_ratio_max?: number | null;
}

export interface DashboardSummary {
  range: RangeKey;
  start: string;
  end: string;
  available: boolean;
  error: string | null;
  sources: Record<string, string> | null;
  interval_minutes: number | null;
  monitored_applications: number;
  status_counts: Partial<Record<HealthStatus, number>>;
  active_incidents: number;
  open_investigations: number;
  overall: Overall;
  db: { status: string; last_ratio: number | null; max_ratio: number | null; used: number | null; slots: number | null } | null;
  db_series: { t: string; ratio: number | null }[];
  applications: Record<string, AppHealth>;
  recent_investigations: Investigation[];
  recent_incidents: Incident[];
  incident_counts: Partial<Record<IncidentStatus, number>>;
  investigation_stats_7d: { by_status: Record<string, number>; open: number; total: number };
}

export interface Conversation {
  id: string;
  title: string;
  thread_id: string;
  application_id: string | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
}

export interface ConversationFocus {
  apps?: string[];
  mechanism?: string | null;
  window?: { start: string; end: string };
  investigation?: string | null;
  intent?: string;
  turn?: number;
  updated_at?: string;
}

export interface TurnDigest {
  turn: number;
  at: string;
  question: string;
  intent: string | null;
  apps: string[];
  investigation: string | null;
  mechanism: string | null;
  answer_head: string;
}

export interface WorkingMemory {
  focus: ConversationFocus | null;
  digests: TurnDigest[];
  messages_in_state: number;
  max_messages: number;
  max_digests: number;
  pending: string[];
}

export interface ConversationDetail extends Conversation {
  active_run_id: string | null;
  resumable: boolean;
  pending_steps: string[];
}

export interface InvestigationSummary {
  investigation_id: string;
  run_id: string;
  status: InvestigationStatus;
  severity: Severity;
  risk_score: number | null;
  data_coverage: number | null;
  mechanism: string | null;
  confidence: Confidence | null;
  source_apps: string[];
  affected_apps: string[];
  window: [string, string];
  data_gaps: string[];
  impacted: string[];
  duration_ms: number | null;
  error: string | null;
}

export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  metadata: {
    intent?: string;
    suggestions?: string[];
    investigation?: InvestigationSummary | null;
    answer_source?: string;
    memory_counts?: Record<string, number> | null;
  };
  investigation_id: string | null;
  created_at: string;
}

export interface RunOut {
  run_id: string;
  kind: "chat" | "investigate";
  stream_url: string;
  conversation_id?: string | null;
  investigation_id?: string | null;
}

export interface Investigation {
  id: string;
  run_id: string;
  trigger: Trigger;
  title: string;
  description: string | null;
  status: InvestigationStatus;
  severity: Severity;
  risk_score: string | number | null;
  data_coverage: string | number | null;
  confidence: Confidence | null;
  primary_mechanism: string | null;
  source_apps: string[];
  affected_apps: string[];
  application_id: string | null;
  conversation_id: string | null;
  user_id: string | null;
  window_start: string;
  window_end: string;
  baseline_start: string | null;
  baseline_end: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  root_cause_hypothesis: string | null;
  error: string | null;
  created_at: string;
}

export interface LockChainNode {
  app: string;
  level: number;
  n: number;
  waits_on: { app: string; n: number }[];
}

export interface Mechanism {
  id: string;
  title: string;
  mechanism?: string;
  source_apps?: string[];
  affected_apps?: string[];
  evidence_ids?: string[];
  secondary_to?: string | null;
  confidence?: Confidence;
  signals?: string[];
  chain_path?: string;
  chain_depth?: number;
  lock_chain?: LockChainNode[];
  edges_total?: number;
  [k: string]: unknown;
}

/** Rapordaki risk bloğu (airlab_agent/scoring.py score_all çıktısı). */
export interface ReportRiskApp {
  score: number | null;
  band: string | null;
  coverage: number;
  missing: string[];
  components: Record<string, number | string | null>;
}

/** Rapordaki DQL kanıt özeti (ham kayıtlar evidence tablosundadır). */
export interface ReportEvidence {
  id: string;
  signal: string;
  status: string;
  records: number | null;
  dql?: string | null;
  error?: string | null;
  dynatrace_query_id?: string | null;
}

export type FeatureBlock = Record<string, number | null>;
export interface FeatureWindow {
  apps?: Record<string, FeatureBlock>;
  db?: FeatureBlock;
  hosts?: Record<string, FeatureBlock>;
}

export interface Report {
  id?: string;
  kind?: string;
  status?: "complete" | "partial";
  generated_at?: string;
  risk?: { overall?: { score: number | null; band: string | null; coverage?: number; scored_apps?: number; total_apps?: number }; apps?: Record<string, ReportRiskApp> };
  data_coverage?: { ratio?: number; signals_ok?: number; signals_total?: number; gaps?: Record<string, string> };
  causality?: { primary_mechanism?: string | null; confidence?: Confidence | null; source_apps?: string[]; affected_apps?: string[] };
  mechanisms?: Mechanism[];
  data_gaps?: string[];
  human_review?: string[];
  alternatives?: string[];
  impacted?: Record<string, { reasons?: string[]; latency_ratio?: number; latency_source?: string }>;
  unexplained_impacted?: string[];
  windows?: { name: string; start: string; end: string }[];
  evidence?: ReportEvidence[];
  true_zero_signals?: string[];
  features_summary?: { window?: FeatureWindow; baseline?: FeatureWindow };
  errors?: unknown[];
  notes?: string[];
  budget?: { elapsed_s?: number; llm_calls?: number; queries_used?: number; input_tokens?: number; output_tokens?: number };
  [k: string]: unknown;
}

export interface ConfigSnapshot {
  hash: string;
  trigger: string;
  revisions: Record<string, number>;
  agent: Record<string, unknown>;
  connector: Record<string, unknown>;
  llm: { enabled: boolean; chat: Record<string, unknown> | null; rca: Record<string, unknown> | null; model_override: string | null };
  tracing: { langsmith: boolean; sampling_rate: number };
}

export interface InvestigationDetail extends Investigation {
  explanation: string | null;
  explanation_source: string | null;
  report: Report | null;
  /** Koşu başında dondurulan Settings (secret içermez). Settings modülünden önceki kayıtlarda yok. */
  config_snapshot?: ConfigSnapshot | null;
}

export interface Evidence {
  id: string;
  evidence_key: string;
  evidence_type: string;
  source: string;
  signal: string | null;
  data_status: string | null;
  application_id: string | null;
  entity_id: string | null;
  trace_id: string | null;
  span_id: string | null;
  metric_name: string | null;
  query_reference: string | null;
  dynatrace_query_id: string | null;
  timestamp: string | null;
  content: string | null;
  metadata: Record<string, unknown>;
}

export interface ToolExecution {
  id: string;
  step: string | null;
  tool_name: string;
  signal: string | null;
  tool_arguments: Record<string, unknown>;
  execution_status: "STARTED" | "SUCCEEDED" | "FAILED" | "SKIPPED";
  result_status: string | null;
  record_count: number | null;
  dynatrace_query_id: string | null;
  evidence_key: string | null;
  started_at: string | null;
  duration_ms: number | null;
  error_message: string | null;
  created_at: string;
}

export interface Incident {
  id: string;
  investigation_id: string | null;
  application_id: string | null;
  title: string;
  description: string | null;
  severity: Severity;
  status: IncidentStatus;
  source: string;
  conditions: string[];
  detected_at: string;
  acknowledged_at: string | null;
  mitigated_at: string | null;
  resolved_at: string | null;
  root_cause: string | null;
  recommended_actions: string[];
  created_at: string;
  updated_at: string;
}

export interface IncidentEvent {
  id: string;
  at: string;
  kind: string;
  from_status: string | null;
  to_status: string | null;
  user_id: string | null;
  message: string | null;
}

export interface Rca {
  id: string;
  investigation_id: string;
  incident_id: string | null;
  application_id: string | null;
  title: string;
  summary: string;
  confidence: Confidence | null;
  generated_by: string;
  generated_at: string;
}

export interface IncidentDetail extends Incident {
  timeline: IncidentEvent[];
  investigations: (Investigation & { relation: string })[];
  rca_reports: Rca[];
  allowed_transitions: IncidentStatus[];
}

export interface RcaDetail extends Rca {
  problem_description: string | null;
  timeline: { at: string | null; event: string }[];
  impact_analysis: string | null;
  suspected_root_cause: string | null;
  supporting_evidence: {
    key: string;
    mechanism: string | null;
    type: string | null;
    signal: string | null;
    summary: string | null;
    dynatrace_query_id?: string | null;
    trace_id?: string;
  }[];
  contributing_factors: string[];
  recommended_actions: string[];
  alternatives: string[];
  data_gaps: string[];
  uncertainty: string | null;
  markdown: string;
}

export interface Feedback {
  id: string;
  investigation_id: string;
  rca_id: string | null;
  user_id: string;
  verdict: Verdict;
  is_correct: boolean;
  rating: number | null;
  comment: string | null;
  created_at: string;
  updated_at: string;
}

export interface KnownIssue {
  id: string;
  application_id: string | null;
  mechanism: string | null;
  title: string;
  description: string | null;
  symptoms: string | null;
  root_cause: string | null;
  resolution: string | null;
  tags: string[];
  verified: boolean;
  origin: string;
  created_at: string;
  updated_at: string;
}

export interface KnownIssueDetail extends KnownIssue {
  related_incidents: Incident[];
  related_investigations: Investigation[];
}

export interface Runbook {
  id: string;
  application_id: string | null;
  mechanism: string | null;
  title: string;
  description: string | null;
  procedure: string;
  risk_level: "low" | "medium" | "high";
  requires_approval: boolean;
  tags: string[];
  origin: string;
  created_at: string;
  updated_at: string;
}

export interface Approval {
  id: string;
  action_type: string;
  target: string;
  justification: string;
  status: "PENDING" | "APPROVED" | "REJECTED" | "EXPIRED" | "CANCELLED";
  investigation_id: string | null;
  incident_id: string | null;
  runbook_id: string | null;
  requested_by: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_comment: string | null;
  expires_at: string | null;
  created_at: string;
}

export interface LlmModelState {
  enabled: boolean;
  model: string | null;
  rca_model: string | null;
  options: { id: string; label: string }[];
  changed_by: string | null;
  changed_at: string | null;
}

export interface SystemInfo {
  environment: string;
  llm_enabled: boolean;
  llm_model: string | null;
  mcp_configured: boolean;
  dynatrace_environment: string | null;
  langsmith_tracing: boolean;
  limits: Record<string, number>;
  active_runs: number;
}

// ---------------------------------------------------------------- SSE
export type AgentEventType =
  | "investigation_started"
  | "tool_started"
  | "tool_completed"
  | "tool_failed"
  | "agent_thinking"
  | "token"
  | "evidence_found"
  | "investigation_completed"
  | "error"
  | "message_saved"
  | "memory_recalled"
  | "suggestions"
  | "done";

export interface AgentEvent<T = Record<string, unknown>> {
  type: AgentEventType;
  ts: string;
  seq: number;
  data: T;
}

export interface Job {
  id: string;
  name: string;
  job_type: string;
  interval_seconds: number;
  enabled: boolean;
  next_run_at: string;
  last_run_at: string | null;
  locked_by: string | null;
  locked_until: string | null;
}

export interface JobRun {
  id: string;
  job_id: string;
  worker_id: string;
  status: "RUNNING" | "SUCCEEDED" | "FAILED";
  started_at: string;
  finished_at: string | null;
  result: Record<string, unknown>;
  error: string | null;
}

// ---------------------------------------------------------------- LLM izleme (LangSmith senkronu)
export type LlmRange = "24h" | "7d" | "30d";

export interface LlmAgg {
  calls: number;
  errors: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  p50_ms: number | null;
  p95_ms: number | null;
}

export interface LlmTrace {
  id: string;
  name: string;
  trigger: string | null;
  model: string | null;
  status: "success" | "error" | "pending";
  error: string | null;
  start_time: string;
  latency_ms: number | null;
  investigation_id: string | null;
  conversation_id: string | null;
  cost_usd: number;
  tokens: number;
  llm_calls: number;
  url: string | null;
}

export interface LlmCallError {
  id: string;
  trace_id: string;
  name: string;
  model: string | null;
  error: string | null;
  start_time: string;
  latency_ms: number | null;
  investigation_id: string | null;
  url: string | null;
}

export interface LlmObservability {
  range: LlmRange;
  start: string;
  end: string;
  bucket: string;
  sync: { last_ok_at: string | null; last_error: string | null; last_error_at: string | null; project: string | null };
  totals: LlmAgg & { p95_first_token_ms: number | null; traces: number; trace_errors: number; error_rate: number | null };
  by_model: (LlmAgg & { model: string })[];
  by_call: (LlmAgg & { name: string })[];
  by_trigger: { trigger: string; traces: number; errors: number; cost_usd: number; p95_ms: number | null }[];
  series: (LlmAgg & { t: string })[];
  top_cost: LlmTrace[];
  slowest: LlmTrace[];
  errors: LlmTrace[];
  llm_errors: LlmCallError[];
}
