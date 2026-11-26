/** Agent Middleware API istemcisi (backend: api/routes/middleware.py).
 *  Üç kavram ayrıdır: tanım (kodda kayıtlı, salt okunur), yapılandırma (versiyonlu) ve execution kaydı.
 *  Ölçülmeyen / kaydedilmeyen değer `null` gelir; arayüz "yakalanmadı" ya da "desteklenmiyor" yazar. */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/services/api";
import { qs } from "@/lib/utils";

export type MwRange = "1h" | "24h" | "7d" | "30d";
export type Decision = "allow" | "block" | "modify" | "short_circuit" | "not_applicable";

export interface JsonSchema {
  type?: string;
  title?: string;
  description?: string;
  properties?: Record<string, JsonSchema>;
  items?: JsonSchema;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  minItems?: number;
  maxItems?: number;
}

export interface MwStats {
  executions: number;
  blocked: number;
  errors: number;
  modified: number;
  overhead_p50_ms: number | null;
  overhead_p95_ms: number | null;
}

export interface CatalogItem {
  name: string;
  title: string;
  kind: string;
  description: string;
  hooks: string[];
  mandatory: boolean;
  security: boolean;
  default_order: number;
  dry_run: "supported" | "simulated" | "unsupported";
  policy_ref: string | null;
  params_schema: JsonSchema;
  scope_schema: JsonSchema;
  enabled: boolean;
  order: number;
  scope: Record<string, string[]>;
  scope_label: string;
  version: number;
  updated_at: string | null;
  updated_by: string | null;
  last: { at: string; decision: Decision | null; result: string; reason: string | null; execution_id: string } | null;
  stats: MwStats;
}

export interface HookInfo {
  supported: boolean;
  attach: string;
  adapter: string;
  langchain_builtin: boolean;
  order: string;
}

export interface Execution {
  execution_id: string;
  run_id: string | null;
  run_kind: string | null;
  thread_id: string | null;
  trace_id: string | null;
  span_id: string | null;
  parent_execution_id: string | null;
  sequence: number;
  depth: number;
  entry_type: "agent" | "call" | "middleware";
  middleware: string | null;
  middleware_version: number | null;
  hook: string | null;
  target_kind: string | null;
  target: string | null;
  task: string | null;
  connector: string | null;
  tool_call_id: string | null;
  attempt_no: number | null;
  started_at: string;
  ended_at: string | null;
  overhead_ms: number | null;
  wrapped_ms: number | null;
  result: "success" | "error";
  decision: Decision | null;
  outcome: string | null;
  reason: string | null;
  error_code: string | null;
  policy: string | null;
  changed_fields: string[];
  truncated: boolean;
  payload_captured: boolean;
}

export interface DiffRow {
  path: string;
  change: "added" | "removed" | "changed";
  old: unknown;
  new: unknown;
}

export interface ExecutionDetail extends Execution {
  before: unknown;
  after: unknown;
  extra: Record<string, unknown>;
  diff: DiffRow[] | null;
  cache: string;
  parent: Execution | null;
  links: { agent_trace: string | null; timeline: string | null; mcp_call: string | null; llm_monitoring: string | null };
}

export interface MwDetail extends CatalogItem {
  can_manage: boolean;
  config_json: Record<string, unknown>;
  hook_info: Record<string, HookInfo>;
  versions: { version: number; change: string; snapshot: Record<string, unknown>; actor: string; note: string | null; created_at: string }[];
  hook_stats: { hook: string; executions: number; blocked: number; errors: number; overhead_p50_ms: number | null; overhead_p95_ms: number | null }[];
  recent: Execution[];
}

export interface Overview {
  range: MwRange;
  since: string;
  registered: number;
  enabled: number;
  mandatory: number;
  executions: number;
  blocked: number;
  errors: number;
  modified: number;
  wrapped_calls: number;
  runs: number;
  pending_policy_decisions: number | null;
  pending_policy_note: string;
  overhead_ms: { p50: number | null; p95: number | null };
  wrapped_ms: { p50: number | null; p95: number | null; measured: number };
  trend: { t: string; executions: number; blocked: number; errors: number }[];
  trend_bucket: string;
  by_hook: { hook: string; executions: number; overhead_p95_ms: number | null }[];
}

export interface TimelineNode extends Execution {
  children: TimelineNode[];
}

export interface Timeline {
  run_id: string;
  root: Execution | null;
  status: string;
  tree: TimelineNode[];
  count: number;
  nested_runs: Execution[];
  parallel_groups: string[][];
  config_versions: Record<string, number> | null;
  lost_records: number | null;
  ordering: string;
}

export interface RunRow extends Execution {
  executions: number;
  blocked: number;
  errors: number;
  lost_records: number | null;
}

export interface HooksInfo {
  hooks: (HookInfo & { hook: string })[];
  unsupported: { name: string; reason: string }[];
  kinds: string[];
  decisions: Decision[];
  ordering: { before: string; wrap: string; after: string };
  framework: string;
}

export interface DryRunResult {
  supported: boolean;
  reason?: string;
  hooks?: string[];
  mode?: string;
  hook?: string;
  decision?: Decision;
  code?: string | null;
  changed_fields?: string[];
  policy?: string | null;
  error?: string | null;
  before?: unknown;
  after?: unknown;
  duration_ms?: number;
  note?: string;
  param_errors?: string[];
}

export interface ExecFilters {
  range: MwRange;
  middleware?: string;
  hook?: string;
  decision?: string;
  result?: string;
  run_id?: string;
  target?: string;
  tool_call_id?: string;
  entry_type?: string;
  limit: number;
  offset: number;
}

const base = "/middleware";

export const useMwOverview = (range: MwRange) =>
  useQuery({ queryKey: ["mw-overview", range], queryFn: () => api.get<Overview>(`${base}/overview${qs({ range })}`), placeholderData: keepPreviousData, refetchInterval: 30_000 });

export const useMwCatalog = (range: MwRange) =>
  useQuery({ queryKey: ["mw-catalog", range], queryFn: () => api.get<{ items: CatalogItem[]; can_manage: boolean }>(`${base}/catalog${qs({ range })}`), placeholderData: keepPreviousData });

export const useMwDetail = (name: string | null, range: MwRange) =>
  useQuery({ queryKey: ["mw-detail", name, range], queryFn: () => api.get<MwDetail>(`${base}/catalog/${encodeURIComponent(name!)}${qs({ range })}`), enabled: !!name });

export const useMwHooks = () => useQuery({ queryKey: ["mw-hooks"], queryFn: () => api.get<HooksInfo>(`${base}/hooks`), staleTime: 300_000 });

export const useMwExecutions = (f: ExecFilters) =>
  useQuery({
    queryKey: ["mw-executions", f],
    queryFn: () => api.get<{ total: number; items: Execution[] }>(`${base}/executions${qs({ ...f })}`),
    placeholderData: keepPreviousData,
  });

export const useMwExecution = (id: string | null) =>
  useQuery({ queryKey: ["mw-execution", id], queryFn: () => api.get<ExecutionDetail>(`${base}/executions/${id}`), enabled: !!id });

export const useMwRuns = (range: MwRange, kind?: string) =>
  useQuery({ queryKey: ["mw-runs", range, kind], queryFn: () => api.get<{ total: number; items: RunRow[] }>(`${base}/runs${qs({ range, kind, limit: 100 })}`), placeholderData: keepPreviousData });

export const useMwTimeline = (runId: string | null) =>
  useQuery({ queryKey: ["mw-timeline", runId], queryFn: () => api.get<Timeline>(`${base}/runs/${encodeURIComponent(runId!)}/timeline`), enabled: !!runId });

function useInvalidate() {
  const qc = useQueryClient();
  return () => {
    for (const k of ["mw-catalog", "mw-detail", "mw-overview"]) void qc.invalidateQueries({ queryKey: [k] });
  };
}

export interface ConfigPatch {
  version: number;
  enabled?: boolean;
  order?: number;
  params?: Record<string, unknown>;
  scope?: Record<string, string[]>;
  note?: string;
}

export function useUpdateMw() {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: ({ name, body }: { name: string; body: ConfigPatch }) => api.patch<CatalogItem>(`${base}/catalog/${encodeURIComponent(name)}`, body),
    onSuccess: () => {
      inv();
      toast.success("Yapılandırma kaydedildi; yeni run'lardan itibaren geçerli");
    },
  });
}

export function useReorderMw() {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (body: { items: { name: string; order: number; version: number }[]; note?: string }) => api.post(`${base}/reorder`, body),
    onSuccess: () => {
      inv();
      toast.success("Sıralama kaydedildi");
    },
  });
}

export function useDryRun() {
  return useMutation({
    mutationFn: (body: { name: string; hook: string; input: Record<string, unknown>; params?: Record<string, unknown>; state?: Record<string, number> }) =>
      api.post<DryRunResult>(`${base}/dry-run`, body),
  });
}
