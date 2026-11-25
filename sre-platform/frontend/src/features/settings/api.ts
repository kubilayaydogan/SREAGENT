import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { errorToast } from "@/hooks/queries";
import { api } from "@/services/api";
import type { DateFormat } from "@/lib/utils";

// ------------------------------------------------------------------ tipler (backend: api/routes/settings.py)
export type SectionId =
  | "general"
  | "agent"
  | "scheduling"
  | "risk"
  | "llm"
  | "observability"
  | "notifications"
  | "retention";
export type FieldSource = "locked" | "admin" | "env" | "default";
export type ApplyMode = "next_run" | "next_tick" | "restart";
export type FieldKind = "bool" | "int" | "float" | "string" | "enum" | "object";

export interface FieldInfo {
  value: unknown;
  inherited: unknown;
  source: FieldSource;
  locked: boolean;
  restart_pending: boolean;
  env: string | null;
  apply: ApplyMode;
  help: string;
  unit: string | null;
  label: string;
  kind: FieldKind;
  options: string[] | null;
  min: number | null;
  max: number | null;
  nullable: boolean;
}

export interface SectionView {
  section: SectionId;
  revision: number;
  updated_by: string | null;
  updated_at: string | null;
  values: Record<string, unknown>;
  fields: Record<string, FieldInfo>;
  status: Record<string, unknown>;
}

export interface Overview {
  sections: { section: SectionId; revision: number; updated_by: string | null; updated_at: string | null; restart_pending: string[]; locked: string[]; overridden: string[] }[];
  connectors: number;
  llm_profiles: number;
  secret_store: boolean;
  environment: string;
}

export interface PublicPrefs {
  display_name: string;
  timezone: string;
  date_format: DateFormat;
  language: string;
}

export interface SecretInfo {
  ref: string | null;
  kind: "env" | "secret" | null;
  available: boolean | null;
  problem?: string | null;
}

export interface LastCheck {
  ok?: boolean;
  kind?: string;
  message?: string;
  at?: string;
  last_success_at?: string | null;
  elapsed_ms?: number;
  stale?: boolean;
  [k: string]: unknown;
}

export interface ToolInfo {
  name: string;
  description?: string | null;
  read_only?: boolean | null;
  destructive?: boolean | null;
  allowed: boolean;
}

export interface Connector {
  id: string;
  name: string;
  provider: string;
  transport: string;
  endpoint: string;
  auth_type: "none" | "bearer";
  secret: SecretInfo;
  timeout_s: number;
  retries: number;
  enabled: boolean;
  read_only: boolean;
  analysis_source: boolean;
  allowed_tools: string[];
  tools: ToolInfo[];
  /** Onaydan sonra tanımı değişen tool'lar (P1.7). Yeniden onaylanana kadar agent kullanmaz. */
  needs_review?: string[];
  last_check: LastCheck | null;
  managed: string | null;
  revision: number;
  updated_at: string | null;
}

export interface ProviderOption {
  id: string;
  label: string;
  supported?: boolean;
  analysis?: boolean;
  adapter?: string | null;
  reason?: string;
}

export interface ConnectorList {
  items: Connector[];
  providers: ProviderOption[];
  transports: ProviderOption[];
  outbound_allowlist: string[];
}

export interface LlmProfile {
  id: string;
  name: string;
  provider: string;
  provider_label: string;
  model: string;
  base_url: string | null;
  secret: SecretInfo;
  timeout_s: number;
  max_tokens: number;
  effort: string | null;
  retries: number;
  monthly_budget_usd: number | null;
  month_cost_usd: number | null;
  enabled: boolean;
  verified: boolean;
  last_check: LastCheck | null;
  managed: string | null;
  revision: number;
}

export interface LlmProfileList {
  items: LlmProfile[];
  providers: ProviderOption[];
  routing: { chat: string | null; rca: string | null };
  cost_source: string | null;
}

export interface SecretsList {
  store_available: boolean;
  stored: {
    ref: string;
    name: string;
    hint: string | null;
    created_by: string;
    created_at: string;
    rotated_at: string | null;
    used_by: string[];
    status: "ok" | "rotation_due" | "expiring" | "expired";
    expires_at: string | null;
    rotation_days: number | null;
    rotation_due_at: string | null;
    description: string | null;
  }[];
  env: (SecretInfo & { name: string; used_by: string[] })[];
}

export interface AuditItem {
  id: number;
  ts: string;
  actor: string;
  action: string;
  section: string;
  target: string | null;
  revision: number | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  result: string;
  detail: string | null;
  actor_type?: string | null;
  source_ip?: string | null;
  reason_code?: string | null;
  run_id?: string | null;
}

/** P2.3: SIEM iletimi ve güvenlik alarm kuralları durumu (worker işi security-ops). */
export interface SecurityOps {
  siem: {
    enabled: boolean;
    signed: boolean;
    cursor: number | null;
    lag: number | null;
    started_at: string | null;
    last_ok: { ts: string; count: number; last_id: number } | null;
    failing: { since: string; error: string } | false | null;
  };
  secrets?: { expired: string[]; expiring: string[]; rotation_due: string[] };
  alerts: {
    enabled: boolean;
    window_minutes: number;
    slack: boolean;
    rules: { rule: string; label: string; actions: string[]; threshold: number; last_fired: string | null }[];
  };
}

export interface AuditSummary {
  hours: number;
  total: number;
  items: { action: string; reason_code: string | null; count: number; last_ts: string }[];
}

export interface Revision {
  revision: number;
  actor: string;
  note: string | null;
  created_at: string;
  data: Record<string, unknown>;
}

export interface JobStatus {
  name: string;
  type: string;
  enabled: boolean;
  interval_seconds: number;
  next_runs: string[];
  running: boolean;
  locked_by: string | null;
  last_run: { status: string; started_at: string; finished_at: string | null; error: string | null } | null;
}

export interface RiskFixtureRow {
  id: string;
  title: string;
  note: string;
  current: { score: number | null; band: string | null; coverage: number; missing: string[]; components: Record<string, number | null> };
  draft: { score: number | null; band: string | null; coverage: number; missing: string[]; components: Record<string, number | null> };
  delta: number | null;
}

export interface MemoryStatus {
  checkpointer: { backend: string; durable: boolean; schema: string; scope: string; threads: number; checkpoints: number; bytes: number };
  store: { backend: string; durable: boolean; schema: string; scope: string; items: number; bytes: number };
  supported_backends: { id: string; label: string; status: string; note: string }[];
  switching: string;
  note: string;
  active_threads: string[];
  retention: { checkpoints: string; investigations_days: number };
  episodes: {
    total: number;
    by_outcome: Record<"unverified" | "confirmed" | "partially_confirmed" | "rejected", number>;
    with_resolution: number;
    table: string;
    scope: string;
    retention: string;
  };
  working_memory: { max_messages: number; max_digests: number; scope: string };
}

export interface SecurityInfo {
  roles: { role: string; can: string[] }[];
  permissions: { permission: string; roles: string[] }[];
  dlp: Record<string, { secrets: boolean; pii: boolean }>;
  llm_model_allowlist: string[];
  security_ops?: SecurityOps;
  secret_store: { available: boolean; reason: string | null; env_allowlist: string[] };
  outbound_allowlist: string[];
  locks: Record<string, Record<string, unknown>>;
  remediation: string;
  audit_retention: string;
}

export interface TestResult {
  ok: boolean;
  kind?: string;
  message?: string;
  detail?: string;
  warnings?: string[];
  [k: string]: unknown;
}

// ------------------------------------------------------------------ sorgular
export const skeys = {
  all: ["settings"] as const,
  overview: ["settings", "overview"] as const,
  pub: ["settings", "public"] as const,
  section: (s: string) => ["settings", "section", s] as const,
  revisions: (s: string) => ["settings", "revisions", s] as const,
  connectors: ["settings", "connectors"] as const,
  llm: ["settings", "llm-providers"] as const,
  secrets: ["settings", "secrets"] as const,
  audit: (p: Record<string, unknown>) => ["settings", "audit", p] as const,
  jobs: ["settings", "jobs"] as const,
  memory: ["settings", "memory"] as const,
  security: ["settings", "security"] as const,
};

export const useSettingsOverview = () => useQuery({ queryKey: skeys.overview, queryFn: () => api.get<Overview>("/settings") });
export const usePublicPrefs = (enabled = true) =>
  useQuery({ queryKey: skeys.pub, queryFn: () => api.get<PublicPrefs>("/settings/public"), staleTime: 300_000, enabled });
export const useSection = (s: SectionId) => useQuery({ queryKey: skeys.section(s), queryFn: () => api.get<SectionView>(`/settings/${s}`) });
export const useRevisions = (s: SectionId, enabled: boolean) =>
  useQuery({ queryKey: skeys.revisions(s), queryFn: () => api.get<{ items: Revision[] }>(`/settings/${s}/revisions`), enabled });
export const useConnectors = () => useQuery({ queryKey: skeys.connectors, queryFn: () => api.get<ConnectorList>("/settings/connectors") });
export const useLlmProfiles = () => useQuery({ queryKey: skeys.llm, queryFn: () => api.get<LlmProfileList>("/settings/llm-providers") });
export const useSecrets = () => useQuery({ queryKey: skeys.secrets, queryFn: () => api.get<SecretsList>("/settings/secrets") });
export const useJobs = () =>
  useQuery({ queryKey: skeys.jobs, queryFn: () => api.get<{ items: JobStatus[] }>("/settings/scheduling/jobs"), refetchInterval: 15_000 });
export const useMemoryStatus = () => useQuery({ queryKey: skeys.memory, queryFn: () => api.get<MemoryStatus>("/settings/memory") });
export const useSecurity = () => useQuery({ queryKey: skeys.security, queryFn: () => api.get<SecurityInfo>("/settings/security") });
export const useAudit = (p: { section?: string; action?: string; result?: string; page: number; size?: number }) =>
  useQuery({
    queryKey: skeys.audit(p),
    queryFn: () => {
      const q = new URLSearchParams({ page: String(p.page), size: String(p.size ?? 50) });
      if (p.section) q.set("section", p.section);
      if (p.action) q.set("action", p.action);
      if (p.result) q.set("result", p.result);
      return api.get<{ items: AuditItem[]; total: number; page: number; pages: number }>(`/settings/audit?${q}`);
    },
    placeholderData: keepPreviousData,
  });
export const useAuditSummary = (hours = 24) =>
  useQuery({ queryKey: skeys.audit({ summary: hours }), queryFn: () => api.get<AuditSummary>(`/settings/audit/summary?hours=${hours}`) });

function useInvalidate() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: skeys.all });
}

/** Bölüm kaydı. 409 (başkası kaydetti) çağıran tarafından ayrıca ele alınır. */
export function useSaveSection(s: SectionId) {
  const inv = useInvalidate();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { revision: number; values: Record<string, unknown>; reset?: string[]; note?: string }) =>
      api.patch<SectionView>(`/settings/${s}`, body),
    onSuccess: (d) => {
      qc.setQueryData(skeys.section(s), d);
      inv();
      if (s === "llm") qc.invalidateQueries({ queryKey: ["llm-model"] });
      toast.success(`Kaydedildi (revizyon ${d.revision})`);
    },
  });
}

export function useRollback(s: SectionId) {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (b: { revision: number; to_revision: number }) => api.post<SectionView>(`/settings/${s}/rollback`, b),
    onSuccess: (d) => {
      inv();
      toast.success(`Geri alındı → yeni revizyon ${d.revision}`);
    },
    onError: (e) => errorToast(e, "Rollback başarısız"),
  });
}

/** Genel amaçlı ayar eylemi: başarıda Settings sorgularını yeniler, hatayı toast'lar. */
export function useSettingsAction<TVars, TRes = unknown>(fn: (v: TVars) => Promise<TRes>, errTitle: string, ok?: (r: TRes) => string | null) {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: fn,
    onSuccess: (r) => {
      inv();
      const m = ok?.(r);
      if (m) toast.success(m);
    },
    onError: (e) => errorToast(e, errTitle),
  });
}
