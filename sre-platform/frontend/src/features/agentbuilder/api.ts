/** Agent Builder API istemcisi (backend: api/routes/agents.py, builder/*).
 *  Tanımlar bağlantılara kimlikle (llm_profile_id / connector_id) başvurur; API anahtarı hiçbir yanıtta yoktur. */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/services/api";
import { errorToast } from "@/hooks/queries";
import { qs } from "@/lib/utils";

export const SYSTEM_AGENT_ID = "5be00000-0000-4000-8000-000000000001";

export type AgentMode = "chat" | "scheduled";
export type RunMode = AgentMode | "test";
export type NodeType = "start" | "llm" | "mcp_tool" | "condition" | "memory_retrieval" | "structured_output" | "result";
export type Severity = "error" | "warning";

export interface Issue {
  code: string;
  message: string;
  node_id: string | null;
  field: string | null;
  severity: Severity;
}

export interface LastRun {
  run_id: string;
  status: string;
  mode: RunMode;
  started_at: string;
  version_label: string | null;
  error: string | null;
}

export interface AgentSummary {
  id: string;
  key: string;
  name: string;
  description: string | null;
  kind: "system" | "custom";
  status: "draft" | "published" | "archived";
  modes: AgentMode[];
  active_version: number | null;
  published_version_label: string | null;
  model: { model: string | null; profile: string | null; provider: string | null } | null;
  draft_revision: number;
  has_unpublished_changes: boolean;
  last_run: LastRun | null;
  is_default_chat: boolean;
  can_copy: boolean;
  can_export: boolean;
  copy_unavailable_reason: string | null;
  created_by: string | null;
  updated_by: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface Variable {
  name: string;
  default: string;
  description: string;
}

export interface WfNode {
  id: string;
  type: NodeType | string;
  label: string;
  config: Record<string, unknown>;
  position: { x: number; y: number };
}

export interface WfEdge {
  id: string;
  source: string;
  target: string;
  branch: "true" | "false" | null;
}

export interface Definition {
  format: 1;
  settings: {
    model: { llm_profile_id: string | null; model: string | null; max_tokens: number | null; timeout_s: number | null; effort: "low" | "medium" | "high" | null; temperature: number | null };
    instructions: { system_prompt: string; task_instructions: string; variables: Variable[] };
    tools: { connector_id: string | null; allowed_tools: string[]; call_timeout_s: number };
    memory: { source: "none" | "platform"; scope: string[]; top_k: number; history_turns: number; context_budget_tokens: number };
    policies: { optional: Record<string, boolean> };
    io: { input_schema: Record<string, unknown> | null; output_schema: Record<string, unknown> | null; streaming: boolean; scheduled_input: string; scheduled_output_format: "markdown" | "json" };
    limits: { timeout_s: number; max_steps: number; max_tool_calls: number; max_llm_calls: number; token_budget: number; retries: number };
  };
  workflow: { nodes: WfNode[]; edges: WfEdge[] };
  layout: { viewport: { x: number; y: number; zoom: number } };
}

export interface ResolvedModel {
  profile_id: string | null;
  profile: string | null;
  provider: string | null;
  model: string | null;
  source: "node" | "agent" | string;
}

export interface VersionRow {
  version: number;
  checksum: string;
  note: string | null;
  published_by: string | null;
  published_at: string;
  draft_revision: number;
  active: boolean;
}

export interface Dependency {
  kind: string;
  name: string;
  fix?: string;
}

export interface AgentDetail extends AgentSummary {
  versions: VersionRow[];
  draft: Definition | null;
  source: Record<string, unknown>;
  issues: Issue[];
  resolved_models?: Record<string, ResolvedModel>;
  dependencies: Dependency[];
}

export interface CatalogNode {
  type: string;
  label: string;
  description?: string;
  status: "available" | "unavailable";
  reason?: string;
}

export interface CatalogProfile {
  id: string;
  name: string;
  provider: string;
  model: string;
  enabled: boolean;
  max_tokens: number | null;
  timeout_s: number | null;
  effort: string | null;
  model_max_tokens: number | null;
  capabilities: Record<string, boolean> | null;
  supports_temperature: boolean;
}

export interface CatalogConnector {
  id: string;
  name: string;
  provider: string | null;
  enabled: boolean | null;
  allowed_tools: string[];
  tools: { name: string; read_only?: boolean | null; destructive?: boolean | null; executable: boolean; description?: string | null }[];
}

export interface CatalogMiddleware {
  name: string;
  title: string;
  kind: string;
  mandatory: boolean;
  description: string;
  platform_enabled: boolean;
}

export interface Catalog {
  nodes: CatalogNode[];
  unavailable_nodes: CatalogNode[];
  operators: string[];
  efforts: string[];
  llm_profiles: CatalogProfile[];
  allowed_models: string[];
  connectors: CatalogConnector[];
  middleware: CatalogMiddleware[];
  memory_scopes: string[];
  temperature_note: string;
}

export interface NodeRun {
  node_id: string;
  type: string;
  label?: string;
  status: "running" | "ok" | "failed" | string;
  started_at?: string;
  duration_ms?: number | null;
  model?: string | null;
  model_source?: string;
  provider?: string | null;
  error_code?: string | null;
  error?: string | null;
  input_tokens?: number;
  output_tokens?: number;
  branch?: string | null;
}

export interface AgentRunRow {
  run_id: string;
  agent_id: string | null;
  agent_name: string | null;
  agent_key: string | null;
  agent_version: number | null;
  version_label: string | null;
  mode: RunMode;
  trigger: string | null;
  status: "running" | "completed" | "failed" | "rejected" | "cancelled";
  error_code: string | null;
  error: string | null;
  models: { node_id?: string; provider?: string | null; model?: string | null; profile?: string | null; source?: string }[];
  nodes: NodeRun[];
  usage: { input_tokens?: number; output_tokens?: number; calls?: number; tokens?: number; max_calls?: number; max_tokens?: number };
  output: unknown;
  conversation_id: string | null;
  investigation_id: string | null;
  schedule_name: string | null;
  retry_of: string | null;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  links: { agent_trace: string; context_windows: string; middleware: string };
}

export interface AgentResult {
  run_id: string;
  status: string;
  error: { code?: string; message?: string; node_id?: string | null } | null;
  duration_ms: number | null;
  nodes: NodeRun[];
  models: AgentRunRow["models"];
  usage: AgentRunRow["usage"];
  version_label: string | null;
  mode: RunMode;
  output: unknown;
}

export interface Schedule {
  name: string;
  kind: "system" | "agent";
  agent_id: string | null;
  agent_name: string | null;
  interval_minutes: number;
  enabled: boolean;
  input: string;
  variables: Record<string, string>;
  created_by?: string | null;
  next_run_at: string | null;
  last_run_at: string | null;
}

export interface ImportPreview {
  agent: { name: string; description: string | null; modes: AgentMode[] };
  agent_version: number | null;
  schema_version: number;
  node_count: number;
  edge_count: number;
  dependencies: {
    llm_profiles: { ref: string; name: string | null; provider: string; model: string | null; used_by: string[] }[];
    connectors: { ref: string; name: string | null; provider: string; tools: { name: string; hash: string | null }[] }[];
    middleware: { name: string; mandatory: boolean; enabled: boolean }[];
    memory_source: "none" | "platform";
  };
  mapping: Record<string, string | null>;
  name_conflict: boolean;
  structural_issues: Issue[];
  mapping_issues: Issue[];
  candidates: {
    llm_profiles: { id: string; name: string; model: string; provider: string; enabled: boolean }[];
    connectors: { id: string; name: string; provider: string | null; enabled: boolean | null; allowed_tools: string[] | null }[];
  };
}

export const abKeys = {
  list: (p: object) => ["agents", "list", p] as const,
  detail: (id: string) => ["agents", "detail", id] as const,
  catalog: ["agents", "catalog"] as const,
  runs: (p: object) => ["agents", "runs", p] as const,
  schedules: ["agents", "schedules"] as const,
};

export const useAgents = (p: { mode?: AgentMode; runnable?: boolean; archived?: boolean } = {}, enabled = true) =>
  useQuery({
    queryKey: abKeys.list(p),
    queryFn: () => api.get<{ items: AgentSummary[]; default_chat_agent_id: string }>(`/agents${qs({ ...p })}`),
    enabled,
    staleTime: 15_000,
  });

export const useAgent = (id: string | undefined) =>
  useQuery({ queryKey: abKeys.detail(id ?? ""), queryFn: () => api.get<AgentDetail>(`/agents/${id}`), enabled: !!id });

export const useCatalog = (enabled = true) =>
  useQuery({ queryKey: abKeys.catalog, queryFn: () => api.get<Catalog>("/agents/catalog"), enabled, staleTime: 60_000 });

export const useAgentRuns = (p: { agent_id?: string; mode?: string; version?: string; status?: string; limit?: number; offset?: number }) =>
  useQuery({
    queryKey: abKeys.runs(p),
    queryFn: () => api.get<{ items: AgentRunRow[]; total: number }>(`/agents/runs${qs({ ...p })}`),
    placeholderData: keepPreviousData,
  });

export const useSchedules = (enabled = true) =>
  useQuery({ queryKey: abKeys.schedules, queryFn: () => api.get<{ items: Schedule[] }>("/agents/schedules"), enabled });

/** Agent mutasyonları: başarıda ilgili listeler yenilenir; hata toast'ı çağırana bırakılabilir (`silent`). */
export function useAbMutation<TIn, TOut>(fn: (v: TIn) => Promise<TOut>, errTitle: string, opts: { silent?: boolean; onSuccess?: (r: TOut, v: TIn) => void } = {}) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (r, v) => {
      void qc.invalidateQueries({ queryKey: ["agents"] });
      opts.onSuccess?.(r, v);
    },
    onError: (e) => {
      if (!opts.silent) errorToast(e, errTitle);
    },
  });
}

export const MODE_TR: Record<RunMode, string> = { chat: "Soru-Cevap", scheduled: "Zamanlanmış", test: "Test" };
export const STATUS_TR: Record<string, string> = {
  draft: "Taslak",
  published: "Yayımlandı",
  archived: "Arşivlendi",
  running: "Çalışıyor",
  completed: "Tamamlandı",
  failed: "Başarısız",
  rejected: "Reddedildi",
  cancelled: "İptal edildi",
};
export const NODE_TR: Record<string, string> = {
  start: "Başlangıç",
  llm: "LLM",
  mcp_tool: "MCP Tool",
  condition: "Koşul",
  memory_retrieval: "Memory Retrieval",
  structured_output: "Structured Output",
  result: "Sonuç",
};
export const OPERATOR_TR: Record<string, string> = {
  eq: "eşittir",
  neq: "eşit değildir",
  contains: "içerir",
  not_contains: "içermez",
  gt: ">",
  gte: "≥",
  lt: "<",
  lte: "≤",
  exists: "var",
  not_exists: "yok",
  empty: "boş",
  not_empty: "boş değil",
};
export const NO_VALUE_OPS = new Set(["exists", "not_exists", "empty", "not_empty"]);

/** Paketi dosya olarak indirir (Content-Disposition adı kullanılır). Hata ApiError olarak fırlar. */
export async function downloadPackage(agentId: string, version: number | null, fallbackName: string) {
  const text = await api.get<string | object>(`/agents/${agentId}/export${qs({ version: version ?? undefined })}`);
  const body = typeof text === "string" ? text : JSON.stringify(text, null, 2);
  const blob = new Blob([body], { type: "application/json;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${fallbackName}-${version ? `v${version}` : "draft"}.agent.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}
