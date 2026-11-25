import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { errorToast } from "@/hooks/queries";
import { api } from "@/services/api";
import type { Page, Role, User } from "@/types/api";

/** Sunucu tarafı oturum (P0.2). `current`: bu tarayıcıdaki oturum. */
export interface SessionInfo {
  id: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  ip: string | null;
  user_agent: string | null;
  current: boolean;
}

/** Service account API anahtarı (P2.2). Düz anahtar (`token`) yalnız oluşturma cevabında gelir. */
export interface ApiKeyInfo {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  owner: string | null;
  created_by: string;
  created_at: string;
  expires_at: string;
  last_used_at: string | null;
  last_used_ip: string | null;
  revoked_at: string | null;
  revoked_reason: string | null;
  status: "active" | "expired" | "revoked";
  token?: string;
}

export const akeys = {
  all: ["account"] as const,
  apiKeys: (inactive: boolean) => ["account", "api-keys", inactive] as const,
  sessions: ["account", "sessions"] as const,
  perms: ["account", "permissions"] as const,
  users: (p: Record<string, unknown>) => ["account", "users", p] as const,
  userSessions: (id: string) => ["account", "users", id, "sessions"] as const,
};

export const useMySessions = () => useQuery({ queryKey: akeys.sessions, queryFn: () => api.get<SessionInfo[]>("/auth/sessions") });
export const useMyPermissions = () =>
  useQuery({ queryKey: akeys.perms, queryFn: () => api.get<{ role: Role; permissions: string[] }>("/auth/permissions"), staleTime: 60_000 });
export const useUsers = (page: number) =>
  useQuery({ queryKey: akeys.users({ page }), queryFn: () => api.get<Page<User>>(`/users?page=${page}&size=50`), placeholderData: keepPreviousData });
export const useApiKeys = (inactive: boolean) =>
  useQuery({
    queryKey: akeys.apiKeys(inactive),
    queryFn: () => api.get<{ items: ApiKeyInfo[]; allowed_scopes: string[]; max_days: number }>(`/api-keys?include_inactive=${inactive}`),
  });
export const useUserSessions =(id: string | null) =>
  useQuery({ queryKey: akeys.userSessions(id ?? ""), queryFn: () => api.get<SessionInfo[]>(`/users/${id}/sessions`), enabled: !!id });

/** Hesap / kullanıcı mutasyonları: başarıda ilgili sorgular tazelenir, hata toast'la gösterilir. */
export function useAccountAction<TVars, TRes = unknown>(fn: (v: TVars) => Promise<TRes>, errTitle: string, ok?: (r: TRes) => string | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: akeys.all });
      const m = ok?.(r);
      if (m) toast.success(m);
    },
    onError: (e) => errorToast(e, errTitle),
  });
}

/** Tarayıcı user-agent'ından kısa etiket (ör. "Chrome · Windows"). */
export function uaLabel(ua: string | null): string {
  if (!ua) return "bilinmeyen istemci";
  const b = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : /curl|python|httpx/i.test(ua) ? "API istemcisi" : "Tarayıcı";
  const os = /Windows/.test(ua) ? "Windows" : /Mac OS X/.test(ua) ? "macOS" : /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Linux/.test(ua) ? "Linux" : "";
  return os ? `${b} · ${os}` : b;
}
