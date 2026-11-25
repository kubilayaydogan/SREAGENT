import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ApiError, api } from "@/services/api";
import { qs } from "@/lib/utils";
import type {
  Application,
  Approval,
  Conversation,
  ConversationDetail,
  DashboardSummary,
  Evidence,
  Feedback,
  Incident,
  IncidentDetail,
  Investigation,
  InvestigationDetail,
  KnownIssue,
  KnownIssueDetail,
  LlmModelState,
  LlmObservability,
  LlmRange,
  Message,
  Page,
  RangeKey,
  Rca,
  RcaDetail,
  Runbook,
  SystemInfo,
  ToolExecution,
  WorkingMemory,
} from "@/types/api";

export function errorToast(e: unknown, title = "İşlem başarısız") {
  const err = e as ApiError;
  toast.error(title, {
    description: `${err.message ?? String(e)}${err.correlationId ? ` · ref ${err.correlationId}` : ""}`,
  });
}

export const keys = {
  apps: ["apps"] as const,
  info: ["info"] as const,
  llmModel: ["llm-model"] as const,
  dashboard: (range: RangeKey, app?: string) => ["dashboard", range, app ?? ""] as const,
  conversations: (q?: string) => ["conversations", q ?? ""] as const,
  conversation: (id: string) => ["conversation", id] as const,
  messages: (id: string) => ["messages", id] as const,
  investigations: (p: Record<string, unknown>) => ["investigations", p] as const,
  investigation: (id: string) => ["investigation", id] as const,
  incidents: (p: Record<string, unknown>) => ["incidents", p] as const,
  incident: (id: string) => ["incident", id] as const,
  rcas: (p: Record<string, unknown>) => ["rcas", p] as const,
  rca: (id: string) => ["rca", id] as const,
};

export const useApplications = () =>
  useQuery({ queryKey: keys.apps, queryFn: () => api.get<Application[]>("/applications?kind=business"), staleTime: 300_000 });

export const useSystemInfo = () =>
  useQuery({ queryKey: keys.info, queryFn: () => api.get<SystemInfo>("/system/info"), staleTime: 60_000 });

export const useLlmModel = () =>
  useQuery({ queryKey: keys.llmModel, queryFn: () => api.get<LlmModelState>("/system/llm-model"), staleTime: 30_000 });

/** Yönetici: Claude modelini (Opus/Sonnet) değiştirir; backend ve worker bir sonraki sorguda yeni modeli kullanır. */
export function useSetLlmModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (model: string) => api.put<LlmModelState>("/system/llm-model", { model }),
    onSuccess: (d) => {
      qc.setQueryData(keys.llmModel, d);
      qc.invalidateQueries({ queryKey: keys.info });
      toast.success(`Model değişti: ${d.options.find((o) => o.id === d.model)?.label ?? d.model}`);
    },
    onError: (e) => errorToast(e, "Model değiştirilemedi"),
  });
}

export const useDashboard = (range: RangeKey, app?: string) =>
  useQuery({
    queryKey: keys.dashboard(range, app),
    queryFn: () => api.get<DashboardSummary>(`/dashboard/summary${qs({ range, application_id: app })}`),
    refetchInterval: 60_000,
    placeholderData: keepPreviousData,
  });

/** Risk score geçmişi: zamanlanmış health check investigation'ları (her biri deterministik risk score taşır).
 *  Mevcut /investigations API'si; yeni backend sözleşmesi yok. Health check tüm uygulamaları kapsar (application_id
 *  boş); uygulama kırılımı her raporun risk.apps alanındadır. */
export function useRiskHistory(hours = 24) {
  const since = new Date(Math.floor(Date.now() / 300_000) * 300_000 - hours * 3_600_000).toISOString();
  const p = { trigger: "scheduled", since, size: 200, sort: "-created_at" };
  return useQuery({
    queryKey: ["risk-history", hours],
    queryFn: () => api.get<Page<Investigation>>(`/investigations${qs(p)}`),
    refetchInterval: 60_000,
    placeholderData: keepPreviousData,
  });
}

// ---------------------------------------------------------------- konuşmalar
export const useConversations = (q?: string) =>
  useQuery({
    queryKey: keys.conversations(q),
    queryFn: () => api.get<Page<Conversation>>(`/conversations${qs({ q, size: 50 })}`),
    placeholderData: keepPreviousData,
  });

export const useConversation = (id?: string) =>
  useQuery({
    queryKey: keys.conversation(id ?? ""),
    queryFn: () => api.get<ConversationDetail>(`/conversations/${id}`),
    enabled: !!id,
  });

export const useMessages = (id?: string) =>
  useQuery({
    queryKey: keys.messages(id ?? ""),
    queryFn: () => api.get<Page<Message>>(`/conversations/${id}/messages?size=200`),
    enabled: !!id,
  });

export const useWorkingMemory = (id?: string) =>
  useQuery({
    queryKey: ["wm", id ?? ""],
    queryFn: () => api.get<WorkingMemory>(`/conversations/${id}/memory`),
    enabled: !!id,
  });

export function useClearFocus(id?: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.del(`/conversations/${id}/memory/focus`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["wm", id ?? ""] }),
    onError: (e) => errorToast(e, "Odak temizlenemedi"),
  });
}

export function useConversationMutations() {
  const qc = useQueryClient();
  const inv = () => qc.invalidateQueries({ queryKey: ["conversations"] });
  return {
    rename: useMutation({
      mutationFn: (v: { id: string; title?: string; archived?: boolean }) =>
        api.patch<Conversation>(`/conversations/${v.id}`, { title: v.title, archived: v.archived }),
      onSuccess: inv,
      onError: (e) => errorToast(e),
    }),
  };
}

// ---------------------------------------------------------------- araştırmalar
export const useInvestigations = (p: Record<string, unknown>) =>
  useQuery({
    queryKey: keys.investigations(p),
    queryFn: () => api.get<Page<Investigation>>(`/investigations${qs(p)}`),
    placeholderData: keepPreviousData,
  });

export const useInvestigation = (id?: string, live = false) =>
  useQuery({
    queryKey: keys.investigation(id ?? ""),
    queryFn: () => api.get<InvestigationDetail>(`/investigations/${id}`),
    enabled: !!id,
    refetchInterval: (q) => (live || ["PENDING", "RUNNING"].includes(q.state.data?.status ?? "") ? 3000 : false),
  });

export const useLlmObservability = (range: LlmRange) =>
  useQuery({
    queryKey: ["llm-observability", range],
    queryFn: () => api.get<LlmObservability>(`/observability/llm?range=${range}`),
    placeholderData: keepPreviousData,
    refetchInterval: 60_000,
  });

export const useTraceUrl = (id?: string) =>
  useQuery({
    queryKey: ["trace", id],
    queryFn: () => api.get<{ url: string | null }>(`/investigations/${id}/trace`),
    enabled: !!id,
    staleTime: 5 * 60_000,
  });

export const useEvidence = (id?: string) =>
  useQuery({ queryKey: ["evidence", id], queryFn: () => api.get<Evidence[]>(`/investigations/${id}/evidence`), enabled: !!id });

export const useTools = (id?: string, live = false) =>
  useQuery({
    queryKey: ["tools", id],
    queryFn: () => api.get<ToolExecution[]>(`/investigations/${id}/tools`),
    enabled: !!id,
    refetchInterval: live ? 2000 : false,
  });

export const useSimilar = (id?: string) =>
  useQuery({ queryKey: ["similar", id], queryFn: () => api.get<Investigation[]>(`/investigations/${id}/similar`), enabled: !!id });

export const useRecommendations = (id?: string) =>
  useQuery({
    queryKey: ["recommendations", id],
    queryFn: () => api.get<{ known_issues: KnownIssue[]; runbooks: Runbook[] }>(`/investigations/${id}/recommendations`),
    enabled: !!id,
  });

export const useFeedback = (id?: string) =>
  useQuery({ queryKey: ["feedback", id], queryFn: () => api.get<Feedback[]>(`/investigations/${id}/feedback`), enabled: !!id });

export function useSubmitFeedback(invId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { verdict: string; rating?: number | null; comment?: string | null; rca_id?: string | null }) =>
      api.post<Feedback>("/feedback", { investigation_id: invId, ...b }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["feedback", invId] });
      toast.success("Değerlendirmeniz kaydedildi");
    },
    onError: (e) => errorToast(e, "Değerlendirme kaydedilemedi"),
  });
}

// ---------------------------------------------------------------- incident'lar
export const useIncidents = (p: Record<string, unknown>) =>
  useQuery({
    queryKey: keys.incidents(p),
    queryFn: () => api.get<Page<Incident>>(`/incidents${qs(p)}`),
    placeholderData: keepPreviousData,
  });

export const useIncident = (id?: string) =>
  useQuery({ queryKey: keys.incident(id ?? ""), queryFn: () => api.get<IncidentDetail>(`/incidents/${id}`), enabled: !!id });

export function useIncidentMutations(id?: string) {
  const qc = useQueryClient();
  const done = (d: IncidentDetail) => {
    qc.setQueryData(keys.incident(d.id), d);
    qc.invalidateQueries({ queryKey: ["incidents"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  };
  return {
    create: useMutation({
      mutationFn: (b: Record<string, unknown>) => api.post<IncidentDetail>("/incidents", b),
      onSuccess: (d) => {
        done(d);
        toast.success("Incident oluşturuldu");
      },
      onError: (e) => errorToast(e, "Incident oluşturulamadı"),
    }),
    patch: useMutation({
      mutationFn: (b: Record<string, unknown>) => api.patch<IncidentDetail>(`/incidents/${id}`, b),
      onSuccess: (d) => {
        done(d);
        toast.success("Incident güncellendi");
      },
      onError: (e) => errorToast(e, "Incident güncellenemedi"),
    }),
    link: useMutation({
      mutationFn: (b: { investigation_id: string; relation?: string }) =>
        api.post<IncidentDetail>(`/incidents/${id}/investigations`, b),
      onSuccess: done,
      onError: (e) => errorToast(e),
    }),
  };
}

// ---------------------------------------------------------------- RCA
export const useRcas = (p: Record<string, unknown>) =>
  useQuery({ queryKey: keys.rcas(p), queryFn: () => api.get<Page<Rca>>(`/rca${qs(p)}`), placeholderData: keepPreviousData });

export const useRca = (id?: string) =>
  useQuery({ queryKey: keys.rca(id ?? ""), queryFn: () => api.get<RcaDetail>(`/rca/${id}`), enabled: !!id });

export function useGenerateRca() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { investigationId: string; incident_id?: string | null; use_llm?: boolean }) =>
      api.post<RcaDetail>(`/rca/${v.investigationId}/generate`, { incident_id: v.incident_id ?? null, use_llm: v.use_llm ?? true }),
    onSuccess: (d) => {
      qc.setQueryData(keys.rca(d.id), d);
      qc.invalidateQueries({ queryKey: ["rcas"] });
      qc.invalidateQueries({ queryKey: ["incident"] });
      toast.success("RCA raporu üretildi");
    },
    onError: (e) => errorToast(e, "RCA üretilemedi"),
  });
}

// ---------------------------------------------------------------- bilgi bankası / onay
export const useKnownIssues = (p: Record<string, unknown>) =>
  useQuery({
    queryKey: ["known-issues", p],
    queryFn: () => api.get<Page<KnownIssue>>(`/known-issues${qs(p)}`),
    placeholderData: keepPreviousData,
  });

export const useKnownIssue = (id?: string) =>
  useQuery({ queryKey: ["known-issue", id], queryFn: () => api.get<KnownIssueDetail>(`/known-issues/${id}`), enabled: !!id });

export const useRunbooks = (p: Record<string, unknown>) =>
  useQuery({ queryKey: ["runbooks", p], queryFn: () => api.get<Page<Runbook>>(`/runbooks${qs(p)}`), placeholderData: keepPreviousData });

export const useApprovals = (status?: string) =>
  useQuery({ queryKey: ["approvals", status], queryFn: () => api.get<Page<Approval>>(`/approvals${qs({ status, size: 50 })}`) });

export function useApprovalMutations() {
  const qc = useQueryClient();
  const inv = () => qc.invalidateQueries({ queryKey: ["approvals"] });
  return {
    request: useMutation({
      mutationFn: (b: Record<string, unknown>) => api.post<Approval>("/approvals", b),
      onSuccess: () => {
        inv();
        toast.success("Onay talebi oluşturuldu");
      },
      onError: (e) => errorToast(e, "Onay talebi oluşturulamadı"),
    }),
    decide: useMutation({
      mutationFn: (v: { id: string; approve: boolean; comment?: string }) =>
        api.post<Approval>(`/approvals/${v.id}/decision`, { approve: v.approve, comment: v.comment }),
      onSuccess: () => {
        inv();
        toast.success("Karar kaydedildi");
      },
      onError: (e) => errorToast(e, "Karar kaydedilemedi"),
    }),
    cancel: useMutation({
      mutationFn: (id: string) => api.post<Approval>(`/approvals/${id}/cancel`),
      onSuccess: inv,
      onError: (e) => errorToast(e),
    }),
  };
}
