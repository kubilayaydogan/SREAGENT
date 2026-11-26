/** Agent Memory API istemcisi (backend: api/routes/memory.py). Tüm değerler backend'in ürettiği gerçek verilerdir;
 *  bulunmayan değer `null` gelir ve arayüzde "—" gösterilir (sayı uydurulmaz). */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { errorToast } from "@/hooks/queries";
import { api } from "@/services/api";
import { qs } from "@/lib/utils";
import type { Page } from "@/types/api";

export type MemoryType = "semantic" | "episodic" | "procedural";
export type MemRange = "1h" | "24h" | "7d" | "30d";

export interface AgentInfo {
  agent_id: string;
  name: string;
  kind: string;
  namespace_prefix: string;
  settings_section: string | null;
  description: string;
}

export interface Latency {
  p50: number | null;
  p95: number | null;
  p99?: number | null;
  n?: number;
}

export interface Overview {
  agent_id: string;
  range: MemRange;
  since: string;
  environment: string | null;
  definitions: Record<string, string>;
  settings: Record<string, unknown>;
  metrics: {
    total_memory_records: number;
    by_backing: Record<string, number>;
    active_threads: number;
    total_threads: number;
    total_checkpoints: number;
    semantic: number;
    episodic: number;
    procedural: number;
    reads: number;
    writes: number;
    checkpoint_writes: number;
    checkpoint_loads: number;
    retrievals: number;
    retrieval_success_rate: number | null;
    empty_retrieval_rate: number | null;
    read_latency: Latency;
    write_latency: Latency;
    checkpoint_write_latency: Latency;
    retrieval_latency: Latency;
    context_tokens: number;
    storage_bytes: Record<string, number>;
    storage_total: number;
    expired: number;
    rejected: number;
    events_by_type: Record<string, number>;
  };
  health: Record<string, { status: string; latency_ms?: number; error?: string; note?: string; [k: string]: unknown }> & {
    backends?: Record<string, string | null>;
  };
  charts: {
    bucket: string;
    growth: { t: string; created: number }[];
    reads_writes: { t: string; reads: number; writes: number; checkpoints: number }[];
    distribution: { memory_type: string; count: number }[];
    retrieval_latency: { t: string; p50: number | null; p95: number | null }[];
    context_tokens: { t: string; tokens: number }[];
    validation: { result: string; count: number }[];
    by_agent: { agent_id: string; count: number }[];
    by_namespace: { namespace: string; count: number }[];
  };
}

export interface MemoryRecord {
  memory_id: string;
  backing: string;
  tenant_id: string;
  agent_id: string;
  namespace: string;
  memory_type: MemoryType | "short_term";
  category: string | null;
  title: string;
  content: string;
  structured_data: Record<string, unknown>;
  source: string;
  source_trust: "trusted" | "untrusted";
  source_run_id: string | null;
  source_thread_id: string | null;
  source_trace_id: string | null;
  evidence_references: string[];
  confidence_score: number | null;
  verification_status: string;
  approval_status: string;
  status: string;
  created_by?: string | null;
  created_at: string | null;
  updated_at: string | null;
  last_accessed_at: string | null;
  access_count: number | null;
  expires_at: string | null;
  version: number | null;
  tags: string[];
  embedding_model: string | null;
  embedding_status: string;
  metadata: Record<string, unknown>;
  actions: string[];
  pending_revisions?: MemoryVersion[];
}

export interface MemoryVersion {
  id: string;
  version: number;
  change: string;
  status: string;
  actor: string | null;
  reason: string | null;
  decided_by: string | null;
  decided_at: string | null;
  created_at: string;
  snapshot: Record<string, unknown>;
  memory_id?: string;
  title?: string;
}

export interface RecordList extends Page<MemoryRecord> {
  by_backing: Record<string, number>;
  note: string;
}

export interface ThreadRow {
  thread_id: string;
  agent_id: string;
  user_id: string | null;
  created_at: string | null;
  last_activity: string | null;
  message_count: number | null;
  checkpoint_count: number;
  estimated_tokens: number | null;
  execution_type: string;
  status: string;
  title: string | null;
  conversation_id: string | null;
  investigation_id: string | null;
}

export interface ThreadMessage {
  message_id: string;
  role: string;
  type: string;
  created_at: string;
  content: string;
  token_estimate: number;
  context_status: string;
  investigation_id: string | null;
  agent_trace_run: string | null;
  langsmith_url: string | null;
  memory_counts: Record<string, number> | null;
}

export interface ThreadDetail {
  thread_id: string;
  agent_id: string;
  checkpoint_count: number;
  execution_type: string;
  conversation_id?: string;
  investigation_id?: string;
  title?: string | null;
  user_id?: string;
  messages: ThreadMessage[];
  message_count?: number;
  system_messages?: { name: string; length: number; content: string | null }[];
  system_messages_note?: string | null;
  tool_calls?: { id: string; tool: string; method: string; status: string; outcome: string | null; record_count: number | null; duration_ms: number | null; started_at: string; investigation_id: string }[];
  tool_calls_note?: string;
  conversation_summary?: unknown[];
  current_context?: Record<string, unknown>;
  context_window?: { messages_in_state: number; max_messages: number; token_estimate: number; token_estimate_method: string; trimming: string };
  note?: string;
  trigger?: string;
  status?: string;
  active: boolean;
}

export interface CheckpointRow {
  checkpoint_id: string;
  checkpoint_ns: string;
  parent_checkpoint_id: string | null;
  step: number | null;
  source: string | null;
  created_at: string | null;
  next: string[] | null;
  tasks: { name: string; id: string; error: boolean; interrupts: number }[];
  metadata: Record<string, unknown>;
  writes: string[] | null;
}

export interface CheckpointList {
  thread_id: string;
  graph: string | null;
  storage_backend: string;
  items: CheckpointRow[];
  next_nodes_note: string | null;
  read_only: boolean;
  replay: string;
}

export interface DiffChange {
  path: string;
  change: "added" | "removed" | "changed";
  old: unknown;
  new: unknown;
}
/** JSON path düzeyinde değişiklik listesi; ilk checkpoint'te `{available: false}`. */
export type StateDiff = DiffChange[] | { available: false; reason: string };

export interface CheckpointDetail {
  thread_id: string;
  checkpoint_id: string;
  checkpoint_ns: string;
  parent_checkpoint_id: string | null;
  next_checkpoint_id: string | null;
  children: string[];
  subgraph_namespaces: string[];
  step: number | null;
  source: string | null;
  created_at: string | null;
  metadata: Record<string, unknown>;
  channel_values: Record<string, unknown>;
  channel_versions: Record<string, unknown>;
  versions_seen: Record<string, unknown>;
  pending_writes: { task_id: string; channel: string; value: unknown }[];
  next_nodes: string[] | null;
  graph: string | null;
  truncated: boolean;
  size_bytes: number;
  diff: StateDiff;
  storage_backend: string;
  read_only: boolean;
}

export interface WorkingRun {
  run_id: string;
  kind: string;
  status: string;
  thread_id: string | null;
  started_at: string;
  agent_id: string;
}

export interface WorkingMemory {
  run_id: string;
  thread_id: string | null;
  agent_id: string;
  kind: string;
  execution_status: string;
  current_node: string | null;
  current_iteration: number | null;
  fields: Record<string, Record<string, unknown>>;
  pending_tasks: string[];
  completed_tasks: string[];
  current_state: Record<string, unknown>;
  nodes: { node: string; node_exec_id: string; step: number | null; attempt: number | null; started_at: string; status: string; duration_ms?: number; update_keys?: string[]; has_error?: boolean }[];
  source: string;
  trace_link: string;
  read_only: boolean;
}

export interface RetrievalRow {
  retrieval_id: string;
  ts: string;
  agent_id: string;
  origin: string;
  run_id: string | null;
  thread_id: string | null;
  trace_id: string | null;
  node: string | null;
  strategy: string;
  namespaces: string[] | null;
  memory_types: string[] | null;
  top_k: number | null;
  returned_count: number;
  empty: boolean;
  duration_ms: number | null;
  status: string;
  error: string | null;
  injection_status: string | null;
  injected_tokens: number | null;
}

export interface RetrievalDetail extends RetrievalRow {
  query: string | null;
  filters: Record<string, unknown> | null;
  selected: Record<string, unknown>[] | null;
  excluded: Record<string, unknown>[] | null;
  stages: { stage: string; duration_ms?: number; output?: unknown; [k: string]: unknown }[] | null;
  injection: Record<string, unknown> | null;
  query_embedding: { status: string; note: string };
  events: MemoryEventRow[];
}

export interface MemoryEventRow {
  event_id: string;
  event_type: string;
  ts: string;
  agent_id: string;
  tenant_id: string;
  run_id: string | null;
  thread_id: string | null;
  trace_id: string | null;
  node: string | null;
  memory_id: string | null;
  memory_type: string | null;
  namespace: string | null;
  status: string;
  duration_ms: number | null;
  error: string | null;
  correlation_id: string | null;
  retrieval_id: string | null;
  actor: string | null;
  payload?: unknown;
}

export interface EventList extends Page<MemoryEventRow> {
  event_types: string[];
  not_emitted: Record<string, string>;
}

export interface PlaygroundResult {
  strategy: string;
  results: { memory_id: string; memory_type: string; title: string; similarity: number | null; rank?: number | null; score_kind: string; verification_status?: string; shared?: unknown; outcome?: string }[];
  latency_ms: number;
  context_token_estimate: number;
  token_estimate_method: string;
  stages: { stage: string; output: unknown }[];
  read_only: boolean;
  note: string;
}

export interface Governance {
  controls: { id: string; status: string; detail?: string }[];
  permissions: { permission: string; roles: string[] }[];
  violations: MemoryEventRow[];
  audit: { ts: string; actor: string; action: string; target: string | null; result: string; detail: string | null }[];
  pending_approvals: number;
}

const base = "/memory";
const ag = (agent: string) => ({ agent });

export const useMemAgents = () =>
  useQuery({ queryKey: ["mem-agents"], queryFn: () => api.get<{ tenant_id: string; items: AgentInfo[]; agent_builder: string }>(`${base}/agents`), staleTime: 300_000 });

export const useMemOverview = (agent: string, range: MemRange) =>
  useQuery({
    queryKey: ["mem-overview", agent, range],
    queryFn: () => api.get<Overview>(`${base}/overview${qs({ agent, range })}`),
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  });

export interface RecordParams {
  memory_type?: MemoryType;
  q?: string;
  status?: string;
  verification?: string;
  backing?: string;
  page?: number;
  size?: number;
}

export const useMemRecords = (agent: string, p: RecordParams) =>
  useQuery({
    queryKey: ["mem-records", agent, p],
    queryFn: () => api.get<RecordList>(`${base}/records${qs({ ...ag(agent), ...p })}`),
    placeholderData: keepPreviousData,
  });

export const useMemRecord = (agent: string, id?: string | null) =>
  useQuery({
    queryKey: ["mem-record", agent, id ?? ""],
    queryFn: () => api.get<MemoryRecord>(`${base}/records/${encodeURIComponent(id!)}${qs(ag(agent))}`),
    enabled: !!id,
  });

export const useMemVersions = (agent: string, id?: string | null) =>
  useQuery({
    queryKey: ["mem-versions", agent, id ?? ""],
    queryFn: () => api.get<{ items: MemoryVersion[] }>(`${base}/records/${encodeURIComponent(id!)}/versions${qs(ag(agent))}`),
    enabled: !!id,
  });

export const useMemApprovals = () =>
  useQuery({ queryKey: ["mem-approvals"], queryFn: () => api.get<{ items: MemoryVersion[] }>(`${base}/approvals`) });

export const useMemThreads = (agent: string, p: { q?: string; kind?: string; page?: number; size?: number }) =>
  useQuery({
    queryKey: ["mem-threads", agent, p],
    queryFn: () => api.get<Page<ThreadRow>>(`${base}/threads${qs({ ...ag(agent), ...p })}`),
    placeholderData: keepPreviousData,
  });

export const useMemThread = (id?: string | null) =>
  useQuery({ queryKey: ["mem-thread", id ?? ""], queryFn: () => api.get<ThreadDetail>(`${base}/threads/${encodeURIComponent(id!)}`), enabled: !!id });

export const useMemCheckpoints = (thread?: string | null) =>
  useQuery({
    queryKey: ["mem-checkpoints", thread ?? ""],
    queryFn: () => api.get<CheckpointList>(`${base}/threads/${encodeURIComponent(thread!)}/checkpoints`),
    enabled: !!thread,
  });

export const useMemCheckpoint = (thread?: string | null, cp?: string | null, ns = "") =>
  useQuery({
    queryKey: ["mem-checkpoint", thread ?? "", cp ?? "", ns],
    queryFn: () => api.get<CheckpointDetail>(`${base}/threads/${encodeURIComponent(thread!)}/checkpoints/${encodeURIComponent(cp!)}${qs({ ns: ns || undefined })}`),
    enabled: !!thread && !!cp,
  });

export const useMemCompare = (thread?: string | null, a?: string | null, b?: string | null) =>
  useQuery({
    queryKey: ["mem-compare", thread ?? "", a ?? "", b ?? ""],
    queryFn: () => api.get<{ diff: StateDiff }>(`${base}/threads/${encodeURIComponent(thread!)}/compare${qs({ a, b })}`),
    enabled: !!thread && !!a && !!b && a !== b,
  });

export const useWorkingRuns = () =>
  useQuery({ queryKey: ["mem-working-runs"], queryFn: () => api.get<{ items: WorkingRun[] }>(`${base}/working`), refetchInterval: 5_000 });

export const useWorkingMemory = (run?: string | null, live = false) =>
  useQuery({
    queryKey: ["mem-working", run ?? ""],
    queryFn: () => api.get<WorkingMemory>(`${base}/working/${encodeURIComponent(run!)}`),
    enabled: !!run,
    refetchInterval: live ? 2_000 : false,
  });

export const useMemRetrievals = (agent: string, p: { run_id?: string; origin?: string; range?: string; page?: number; size?: number }) =>
  useQuery({
    queryKey: ["mem-retrievals", agent, p],
    queryFn: () => api.get<Page<RetrievalRow>>(`${base}/retrievals${qs({ ...ag(agent), ...p })}`),
    placeholderData: keepPreviousData,
  });

export const useMemRetrieval = (id?: string | null) =>
  useQuery({ queryKey: ["mem-retrieval", id ?? ""], queryFn: () => api.get<RetrievalDetail>(`${base}/retrievals/${id}`), enabled: !!id });

export const useMemEvents = (agent: string, p: { type?: string[]; run_id?: string; thread_id?: string; memory_id?: string; status?: string; range?: string; page?: number; size?: number }) =>
  useQuery({
    queryKey: ["mem-events", agent, p],
    queryFn: () => api.get<EventList>(`${base}/events${qs({ ...ag(agent), ...p })}`),
    placeholderData: keepPreviousData,
  });

export const useMemEvent = (id?: string | null) =>
  useQuery({ queryKey: ["mem-event", id ?? ""], queryFn: () => api.get<MemoryEventRow>(`${base}/events/${id}`), enabled: !!id });

export const useMemGovernance = () => useQuery({ queryKey: ["mem-governance"], queryFn: () => api.get<Governance>(`${base}/governance`) });

export const useMemStrategies = () =>
  useQuery({
    queryKey: ["mem-strategies"],
    queryFn: () => api.get<{ implemented: { id: string; label: string }[]; not_implemented: { id: string; label: string }[]; agent_strategy: string }>(`${base}/strategies`),
    staleTime: 300_000,
  });

function useInvalidate() {
  const qc = useQueryClient();
  return () => {
    for (const k of ["mem-records", "mem-record", "mem-versions", "mem-approvals", "mem-overview", "mem-governance", "mem-events"])
      void qc.invalidateQueries({ queryKey: [k] });
  };
}

export interface RecordInput {
  memory_type: MemoryType;
  title: string;
  content: string;
  category?: string;
  source?: string;
  tags?: string[];
  evidence_references?: string[];
  confidence_score?: number | null;
  structured_data?: Record<string, unknown>;
}

export function useCreateRecord(agent: string) {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (b: RecordInput) => api.post<MemoryRecord>(`${base}/records${qs(ag(agent))}`, b),
    onSuccess: (r) => {
      inv();
      toast.success(r.approval_status === "pending" ? "Öneri onaya gönderildi" : "Memory kaydı oluşturuldu");
    },
  });
}

export function useUpdateRecord(agent: string) {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<RecordInput> & { version?: number | null; reason?: string } }) =>
      api.patch<MemoryRecord>(`${base}/records/${encodeURIComponent(id)}${qs(ag(agent))}`, body),
    onSuccess: () => {
      inv();
      toast.success("Kayıt güncellendi");
    },
  });
}

export function useRecordOp(agent: string) {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: ({ id, op, reason, version_id }: { id: string; op: string; reason?: string; version_id?: string }) =>
      api.post<MemoryRecord>(`${base}/records/${encodeURIComponent(id)}/${op}${qs(ag(agent))}`, { reason, version_id }),
    onSuccess: () => inv(),
    onError: (e) => errorToast(e),
  });
}

export function useDeleteRecord(agent: string) {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (id: string) => api.post<{ deleted: string }>(`${base}/records-delete/${encodeURIComponent(id)}${qs(ag(agent))}`, { confirm: id }),
    onSuccess: () => {
      inv();
      toast.success("Kayıt silindi (versiyon geçmişi korunur)");
    },
    onError: (e) => errorToast(e),
  });
}

export interface PlaygroundInput {
  agent: string;
  query: string;
  strategy: string;
  memory_types: MemoryType[];
  top_k: number;
  similarity_threshold?: number | null;
  since_days?: number | null;
  namespace?: string | null;
  apps?: string[];
}

export function usePlayground() {
  return useMutation({ mutationFn: (b: PlaygroundInput) => api.post<PlaygroundResult>(`${base}/playground`, b) });
}

export const exportUrl = (agent: string, memory_type?: string) => `${base}/export${qs({ agent, memory_type })}`;
