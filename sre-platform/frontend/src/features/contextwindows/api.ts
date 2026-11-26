/** Context Windows API istemcisi (backend: api/routes/context_windows.py).
 *  Kaydedilmeyen değer `null` gelir (sıfır değil); arayüz "Bilinmiyor" / "Ölçülmedi" yazar. */
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "@/services/api";
import { qs } from "@/lib/utils";

export type CwRange = "1h" | "24h" | "7d" | "30d";
export type CwStatus = "ok" | "error" | "context_limit" | "retried" | "cancelled";
export type CwMethod = "estimate" | "provider_minus_estimate" | "unknown";

export interface CwCall {
  call_id: string;
  logical_call_id: string;
  attempt_no: number;
  attempt_count: number;
  run_id: string | null;
  run_kind: string | null;
  node: string | null;
  task: string | null;
  call_seq: number | null;
  provider: string;
  model: string | null;
  served_model: string | null;
  streaming: boolean;
  started_at: string;
  duration_ms: number | null;
  status: CwStatus;
  error_code: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  capacity: number | null;
  planned_fill: number | null;
  actual_fill: number | null;
  usage_source: "provider" | "missing";
}

export interface CwComponent {
  key: string;
  label: string;
  tokens: number | null;
  method: CwMethod;
  refs: string[];
}

export interface CwDecision {
  section: string;
  status: string | null;
  reason: string | null;
  token_estimate: number | null;
  included: (string | Record<string, string | null>)[];
  summarized: (string | Record<string, string | null>)[];
  dropped: (string | Record<string, string | null>)[];
}

export interface CwCallDetail extends CwCall {
  thread_id: string | null;
  ended_at: string | null;
  error_message: string | null;
  http_status: number | null;
  stop_reason: string | null;
  request_id: string | null;
  capacity_source: string | null;
  capacity_note: string | null;
  model_max_output: number | null;
  reserved_output: number | null;
  estimated_input: number | null;
  estimate_method: string | null;
  estimate_over: number | null;
  cache_read_tokens: number | null;
  cache_creation_tokens: number | null;
  reasoning_tokens: number | null;
  components: CwComponent[] | null;
  context_decisions: CwDecision[] | null;
  stream: { chunks?: number; first_chunk_ms?: number | null; completed?: boolean; interrupted?: boolean } | null;
  trace_call_id: string | null;
  middleware_execution_id: string | null;
  attempts: CwCall[];
  links: { agent_trace: string | null; agent_trace_level: "call" | "run" | null; middleware_timeline: string | null; llm_monitoring: string | null };
  component_labels: Record<string, string>;
}

export interface CwOverview {
  range: CwRange;
  since: string;
  attempts: number;
  calls: number;
  max_actual_fill: number | null;
  max_call: CwCall | null;
  context_limit_errors: number;
  retried_attempts: number;
  errors: number;
  usage_missing: number;
  capacity_unknown: number;
}

export interface CwFilters {
  range: CwRange;
  run_id?: string;
  node?: string;
  model?: string;
  status?: string;
  sort: "time" | "fill" | "sequence";
  limit: number;
  offset: number;
}

const base = "/context-windows";

export const useCwOverview = (range: CwRange) =>
  useQuery({ queryKey: ["cw-overview", range], queryFn: () => api.get<CwOverview>(`${base}/overview${qs({ range })}`), placeholderData: keepPreviousData, refetchInterval: 30_000 });

export const useCwFacets = (range: CwRange) =>
  useQuery({ queryKey: ["cw-facets", range], queryFn: () => api.get<{ nodes: string[]; models: string[]; statuses: CwStatus[] }>(`${base}/facets${qs({ range })}`), staleTime: 60_000 });

export const useCwCalls = (f: CwFilters) =>
  useQuery({ queryKey: ["cw-calls", f], queryFn: () => api.get<{ total: number; items: CwCall[] }>(`${base}/calls${qs({ ...f })}`), placeholderData: keepPreviousData });

export const useCwCall = (id: string | null) =>
  useQuery({ queryKey: ["cw-call", id], queryFn: () => api.get<CwCallDetail>(`${base}/calls/${encodeURIComponent(id!)}`), enabled: !!id });
