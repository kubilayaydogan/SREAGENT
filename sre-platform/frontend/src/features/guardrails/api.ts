/** Guardrails API istemcisi (backend: api/routes/guardrails.py).
 *  Güvenlik kararları backend'de verilir; arayüz yalnız gösterir ve istek gönderir. Ölçülmeyen değer `null` gelir. */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/services/api";
import { qs } from "@/lib/utils";
import { errorToast } from "@/hooks/queries";

export type GrRange = "1h" | "24h" | "7d" | "30d";
export type GrDecision = "passed" | "blocked" | "warned" | "redacted" | "sanitized" | "approval_required" | "terminated" | "error";
export type GrMode = "monitor" | "enforce" | "disabled";

export interface Assignment {
  scope: string;
  value: string | null;
}

export interface Policy {
  id: string;
  key: string;
  name: string;
  description: string | null;
  category: string;
  type: string;
  type_title: string;
  severity: string;
  mode: GrMode;
  status: string;
  mandatory: boolean;
  builtin: boolean;
  stages: string[];
  action: string;
  priority: number;
  params: Record<string, unknown>;
  bound_to: string | null;
  enforced_by: string;
  assignments: Assignment[];
  scope: string;
  version: number;
  latest_version: number;
  created_by: string | null;
  updated_by: string | null;
  created_at: string | null;
  updated_at: string | null;
  last_triggered_at: string | null;
}

export interface PolicyVersion {
  version: number;
  change: string;
  note: string | null;
  checksum: string;
  created_by: string | null;
  created_at: string;
  active: boolean;
  snapshot: Record<string, unknown>;
}

export interface EvaluatorInfo {
  type: string;
  title: string;
  category: string;
  stages: string[];
  description: string;
  actions: string[];
  model_based: boolean;
  params_schema: Record<string, unknown>;
}

export interface PolicyDetail extends Policy {
  versions: PolicyVersion[];
  evaluator: EvaluatorInfo | null;
}

export interface EffectiveItem {
  key: string;
  name: string;
  category: string;
  type: string;
  mandatory: boolean;
  mode: string;
  action: string;
  severity: string;
  stages: string[];
  inherited_from: string;
  scope_values: string[];
  version: number;
  bound_to: string | null;
  priority: number;
}

export interface Meta {
  evaluators: EvaluatorInfo[];
  stages: { key: string; label: string }[];
  categories: string[];
  actions: string[];
  modes: string[];
  severities: string[];
  scopes: string[];
}

export interface Violation {
  id: string;
  severity: string;
  category: string;
  policy_key: string;
  agent_id: string | null;
  agent_key: string | null;
  run_id: string | null;
  node: string | null;
  stage: string | null;
  mcp_server: string | null;
  tool: string | null;
  reason: string | null;
  code: string | null;
  decision: GrDecision;
  action_taken: string | null;
  enforced: boolean;
  first_seen: string;
  last_seen: string;
  occurrences: number;
  status: string;
  assigned_to: string | null;
  history: { ts: string; by: string; status: string; assigned_to: string | null; note: string | null }[];
  fingerprint: string;
  last_evaluation_id: string | null;
}

export interface EvalRow {
  id: string;
  ts: string;
  run_id: string | null;
  thread_id: string | null;
  run_kind: string | null;
  agent_id: string | null;
  agent_key: string | null;
  agent_version: string | null;
  user_id: string | null;
  node: string | null;
  stage: string;
  policy_key: string;
  policy_version: number | null;
  category: string;
  decision: GrDecision;
  enforced: boolean;
  severity: string | null;
  action: string | null;
  reason: string | null;
  code: string | null;
  count: number;
  duration_ms: number | null;
  duration_max_ms: number | null;
  mcp_server: string | null;
  tool: string | null;
  model: string | null;
  error: string | null;
  bound_to: string | null;
  approval_id: string | null;
  violation_id: string | null;
}

export interface EvalDetail extends EvalRow {
  matched: Record<string, unknown>[];
  thresholds: Record<string, { limit: unknown; actual: unknown }>;
  changed_fields: string[];
  snapshot: { before?: unknown; after?: unknown } | null;
  context: Record<string, unknown> | null;
  latency_buckets: number[] | null;
  latency_labels: string[];
  provider: string | null;
  correlation_id: string | null;
  trace_id: string | null;
  parent_span_id: string | null;
  mcp_call_id: string | null;
  llm_call_id: string | null;
  user_role: string | null;
  environment: string | null;
  tenant_id: string;
  policy: { id: string; name: string; mandatory: boolean } | null;
  run_evaluations?: EvalRow[];
}

export interface Approval {
  id: string;
  agent_id: string | null;
  agent_key: string | null;
  run_id: string | null;
  thread_id: string | null;
  requested_action: string;
  target_resource: string;
  risk_level: string;
  requested_by: string;
  required_role: string;
  requested_at: string;
  expires_at: string;
  status: string;
  reason: string | null;
  decision_reason: string | null;
  decided_by: string | null;
  decided_at: string | null;
  action_params: Record<string, unknown>;
  action_hash: string;
  policy_key: string | null;
  policy_version: number | null;
  consumed_at: string | null;
  result: string | null;
  evaluation_id: string | null;
}

export interface Overview {
  range: GrRange;
  total_evaluations: number;
  violations: number;
  blocked: number;
  redacted: number;
  warned: number;
  approval_required: number;
  errors: number;
  timeouts: number;
  by_decision: Record<string, number>;
  avg_ms: number | null;
  max_ms: number | null;
  p95_ms_approx: number | null;
  histogram: { labels: string[]; counts: number[] };
  policies: { active: number; mandatory: number; monitor: number; disabled: number };
  pending_approvals: number;
  open_violations: Record<string, number>;
  series: { t: string; decision: string; n: number }[];
  top_policies: { policy_key: string; category: string; n: number }[];
  top_agents: { agent: string; n: number }[];
  recent_critical: Violation[];
  health: { writer_lost: number | null; writer_written: number | null; snapshot_revision: number | null };
}

export interface Analytics {
  range: GrRange;
  by_category: { category: string; decision: string; n: number }[];
  by_stage: { stage: string; decision: string; n: number }[];
  policies: {
    policy_key: string;
    category: string;
    evaluations: number;
    violations: number;
    rate: number | null;
    avg_ms: number | null;
    max_ms: number | null;
    p50_ms_approx: number | null;
    p95_ms_approx: number | null;
  }[];
  monitor_vs_enforce: { enforced: boolean; decision: string; n: number }[];
  latency_note: string;
  labels: {
    triage: { false_positive: number; true_positive: number; false_positive_rate: number | null };
    tests: { labeled: number; false_positive: number; false_negative: number; false_positive_rate: number | null; false_negative_rate: number | null };
    note: string;
  };
}

export interface GrConfig {
  revision: number;
  data: {
    default_mode: string;
    evaluation_timeout_ms: number;
    max_evaluation_retries: number;
    retention: { evaluation_days: number; snapshot_days: number; audit_days: number; test_run_days: number };
    masking: { capture_snapshots: boolean; max_snapshot_bytes: number };
    classifier: { mode: string };
    notifications: { enabled?: boolean; min_severity: string };
    approval_timeout_minutes: number;
    fail_mode: Record<string, string>;
    kill_switch: { global: boolean; agents: string[] };
    record_passed: boolean;
    optional_defaults: { mode: string };
    [k: string]: unknown;
  };
  updated_by: string | null;
  updated_at: string | null;
  fixed: { fail_closed_categories: string[]; mandatory_enforce: boolean; classifier_modes: string[] };
}

export interface TestEvaluation {
  policy_key: string;
  policy_name: string;
  category: string;
  mandatory: boolean;
  mode: string;
  action: string;
  decision: GrDecision;
  enforced: boolean;
  reason: string | null;
  code: string | null;
  matched: Record<string, unknown>[];
  thresholds: Record<string, { limit: unknown; actual: unknown }>;
  changed_fields: string[];
  duration_ms: number;
  error: string | null;
  would_request_approval: boolean;
}

export interface TestResult {
  decision: GrDecision;
  stopped: boolean;
  message: string | null;
  duration_ms: number;
  evaluations: TestEvaluation[];
  before: unknown;
  after: unknown;
  changed: boolean;
  evaluated_policies: number;
  side_effects: string;
  test_run_id?: string;
}

export interface TestSample {
  key: string;
  stage: string;
  text?: string;
  json?: Record<string, unknown>;
  tool?: string;
  mcp_server?: string;
}

export interface AuditItem {
  seq: number;
  ts: string;
  actor: string;
  action: string;
  target: string | null;
  detail: string | null;
  before: unknown;
  after: unknown;
  hash: string;
}

const base = "/guardrails";

export const useGrMeta = () => useQuery({ queryKey: ["gr-meta"], queryFn: () => api.get<Meta>(`${base}/evaluators`), staleTime: 300_000 });

export const useGrOverview = (range: GrRange) =>
  useQuery({ queryKey: ["gr-overview", range], queryFn: () => api.get<Overview>(`${base}/overview${qs({ range })}`), placeholderData: keepPreviousData, refetchInterval: 30_000 });

export const useGrAnalytics = (range: GrRange) =>
  useQuery({ queryKey: ["gr-analytics", range], queryFn: () => api.get<Analytics>(`${base}/analytics${qs({ range })}`), placeholderData: keepPreviousData });

export const useGrPolicies = (f: { category?: string; q?: string; status?: string }) =>
  useQuery({ queryKey: ["gr-policies", f], queryFn: () => api.get<{ items: Policy[] }>(`${base}/policies${qs({ ...f })}`), placeholderData: keepPreviousData });

export const useGrPolicy = (id: string | null) =>
  useQuery({ queryKey: ["gr-policy", id], queryFn: () => api.get<PolicyDetail>(`${base}/policies/${encodeURIComponent(id!)}`), enabled: !!id });

export const useGrEffective = (f: { agent_id?: string; environment?: string; mcp_server?: string; tool?: string; node?: string }) =>
  useQuery({
    queryKey: ["gr-effective", f],
    queryFn: () => api.get<{ revision: number; items: EffectiveItem[]; rule: string; context: Record<string, unknown> }>(`${base}/effective${qs({ ...f })}`),
    placeholderData: keepPreviousData,
  });

export interface LogFilters {
  range: GrRange;
  decision?: string;
  policy?: string;
  category?: string;
  stage?: string;
  agent?: string;
  run_id?: string;
  q?: string;
  limit: number;
  offset: number;
}

export const useGrLogs = (f: LogFilters) =>
  useQuery({ queryKey: ["gr-logs", f], queryFn: () => api.get<{ total: number; items: EvalRow[] }>(`${base}/evaluations${qs({ ...f })}`), placeholderData: keepPreviousData });

export const useGrLog = (id: string | null) =>
  useQuery({ queryKey: ["gr-log", id], queryFn: () => api.get<EvalDetail>(`${base}/evaluations/${id}`), enabled: !!id });

export const useGrViolations = (f: { status?: string; severity?: string; category?: string; q?: string; limit: number; offset: number }) =>
  useQuery({ queryKey: ["gr-violations", f], queryFn: () => api.get<{ total: number; items: Violation[] }>(`${base}/violations${qs({ ...f })}`), placeholderData: keepPreviousData });

export const useGrApprovals = (f: { status?: string; limit: number; offset: number }) =>
  useQuery({ queryKey: ["gr-approvals", f], queryFn: () => api.get<{ total: number; items: Approval[] }>(`${base}/approvals${qs({ ...f })}`), placeholderData: keepPreviousData });

export const useGrConfig = () => useQuery({ queryKey: ["gr-config"], queryFn: () => api.get<GrConfig>(`${base}/config`) });

export const useGrSamples = () => useQuery({ queryKey: ["gr-samples"], queryFn: () => api.get<{ items: TestSample[] }>(`${base}/test/samples`), staleTime: 300_000 });

export const useGrTestHistory = () =>
  useQuery({ queryKey: ["gr-test-history"], queryFn: () => api.get<{ items: { id: string; created_at: string; created_by: string; stage: string; decision: string; expected_decision: string | null; test_type: string }[] }>(`${base}/test/history`) });

export const useGrAudit = () => useQuery({ queryKey: ["gr-audit"], queryFn: () => api.get<{ items: AuditItem[] }>(`${base}/audit`) });
export const useGrAuditVerify = () => useQuery({ queryKey: ["gr-audit-verify"], queryFn: () => api.get<{ ok: boolean; checked: number; broken_at: number | null }>(`${base}/audit/verify`) });

function useInvalidate(keys: string[]) {
  const qc = useQueryClient();
  return () => {
    for (const k of keys) void qc.invalidateQueries({ queryKey: [k] });
  };
}

const POLICY_KEYS = ["gr-policies", "gr-policy", "gr-effective", "gr-overview", "gr-audit"];

export function useUpdatePolicy() {
  const inv = useInvalidate(POLICY_KEYS);
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) => api.patch<PolicyDetail>(`${base}/policies/${encodeURIComponent(id)}`, body),
    onError: (e: unknown) => errorToast(e),
    onSuccess: () => {
      inv();
      toast.success("Policy kaydedildi; yeni koşulardan itibaren geçerli");
    },
  });
}

export function useCreatePolicy() {
  const inv = useInvalidate(POLICY_KEYS);
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => api.post<PolicyDetail>(`${base}/policies`, body),
    onError: (e: unknown) => errorToast(e),
    onSuccess: () => {
      inv();
      toast.success("Policy oluşturuldu");
    },
  });
}

export function useClonePolicy() {
  const inv = useInvalidate(POLICY_KEYS);
  return useMutation({
    mutationFn: ({ id, key, name }: { id: string; key: string; name?: string }) => api.post<PolicyDetail>(`${base}/policies/${encodeURIComponent(id)}/clone`, { key, name }),
    onError: (e: unknown) => errorToast(e),
    onSuccess: () => {
      inv();
      toast.success("Policy kopyalandı");
    },
  });
}

export function useRollbackPolicy() {
  const inv = useInvalidate(POLICY_KEYS);
  return useMutation({
    mutationFn: ({ id, version, note }: { id: string; version: number; note?: string }) =>
      api.post<PolicyDetail>(`${base}/policies/${encodeURIComponent(id)}/versions/${version}/rollback`, { note }),
    onError: (e: unknown) => errorToast(e),
    onSuccess: () => {
      inv();
      toast.success("Sürüm yeniden aktif edildi (rollback)");
    },
  });
}

export function useAssignPolicy() {
  const inv = useInvalidate(POLICY_KEYS);
  return useMutation({
    mutationFn: ({ id, version, assignments }: { id: string; version: number; assignments: Assignment[] }) =>
      api.put<PolicyDetail>(`${base}/policies/${encodeURIComponent(id)}/assignments`, { version, assignments }),
    onError: (e: unknown) => errorToast(e),
    onSuccess: () => {
      inv();
      toast.success("Kapsam güncellendi");
    },
  });
}

export function useTriage() {
  const inv = useInvalidate(["gr-violations", "gr-overview"]);
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: { status?: string; assigned_to?: string; note?: string } }) => api.patch<Violation>(`${base}/violations/${id}`, body),
    onError: (e: unknown) => errorToast(e),
    onSuccess: () => {
      inv();
      toast.success("İhlal güncellendi");
    },
  });
}

export function useDecideApproval() {
  const inv = useInvalidate(["gr-approvals", "gr-overview"]);
  return useMutation({
    mutationFn: ({ id, approve, reason }: { id: string; approve: boolean; reason?: string }) =>
      api.post<Approval>(`${base}/approvals/${id}/${approve ? "approve" : "reject"}`, { reason }),
    onError: (e: unknown) => errorToast(e),
    onSuccess: (a) => {
      inv();
      toast.success(a.status === "approved" ? "Onaylandı" : "Reddedildi");
    },
  });
}

export function useCancelApproval() {
  const inv = useInvalidate(["gr-approvals"]);
  return useMutation({
    mutationFn: (id: string) => api.post<Approval>(`${base}/approvals/${id}/cancel`, {}),
    onError: (e: unknown) => errorToast(e),
    onSuccess: () => {
      inv();
      toast.success("İstek iptal edildi");
    },
  });
}

export function useRunTest() {
  const inv = useInvalidate(["gr-test-history"]);
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => api.post<TestResult>(`${base}/test`, body),
    onError: (e: unknown) => errorToast(e),
    onSuccess: () => inv(),
  });
}

export function useSaveConfig() {
  const inv = useInvalidate(["gr-config", "gr-audit"]);
  return useMutation({
    mutationFn: (body: { revision: number; data: Record<string, unknown> }) => api.put<GrConfig>(`${base}/config`, body),
    onError: (e: unknown) => errorToast(e),
    onSuccess: () => {
      inv();
      toast.success("Guardrail yapılandırması kaydedildi");
    },
  });
}

export function useKillSwitch() {
  const inv = useInvalidate(["gr-config", "gr-audit", "gr-overview"]);
  return useMutation({
    mutationFn: (body: { revision: number; global: boolean; agents: string[]; reason: string }) => api.post<GrConfig>(`${base}/kill-switch`, body),
    onError: (e: unknown) => errorToast(e),
    onSuccess: (c) => {
      inv();
      toast.success(c.data.kill_switch.global ? "Kill switch AÇIK: yeni koşular başlamaz" : "Kill switch güncellendi");
    },
  });
}

/** JSON diff (Before → After): yol, değişiklik türü, eski / yeni. */
export interface DiffRow {
  path: string;
  change: "added" | "removed" | "changed";
  old?: unknown;
  new?: unknown;
}

export function jsonDiff(a: unknown, b: unknown, path = "$", out: DiffRow[] = []): DiffRow[] {
  if (out.length > 500) return out;
  const isObj = (x: unknown) => x !== null && typeof x === "object";
  if (isObj(a) && isObj(b) && Array.isArray(a) === Array.isArray(b)) {
    const ka = Object.keys(a as object);
    const kb = Object.keys(b as object);
    for (const k of new Set([...ka, ...kb])) {
      const p = Array.isArray(a) ? `${path}[${k}]` : `${path}.${k}`;
      const va = (a as Record<string, unknown>)[k];
      const vb = (b as Record<string, unknown>)[k];
      if (!(k in (a as object))) out.push({ path: p, change: "added", new: vb });
      else if (!(k in (b as object))) out.push({ path: p, change: "removed", old: va });
      else jsonDiff(va, vb, p, out);
    }
    return out;
  }
  if (JSON.stringify(a) !== JSON.stringify(b)) out.push({ path, change: "changed", old: a, new: b });
  return out;
}
