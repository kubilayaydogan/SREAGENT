import type { ApiErrorBody } from "@/types/api";

const TOKEN_KEY = "sre.token";
const EXP_KEY = "sre.token_exp";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public correlationId?: string,
    public details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn;
}

export const tokenStore = {
  get(): string | null {
    const t = localStorage.getItem(TOKEN_KEY);
    const exp = localStorage.getItem(EXP_KEY);
    if (!t || !exp || new Date(exp).getTime() <= Date.now()) return null;
    return t;
  },
  set(token: string, exp: string) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(EXP_KEY, exp);
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(EXP_KEY);
  },
};

export function newRequestId(): string {
  return `ui-${crypto.randomUUID?.() ?? Math.random().toString(36).slice(2)}`.slice(0, 40);
}

export function authHeaders(): Record<string, string> {
  const t = tokenStore.get();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function toError(res: Response): Promise<ApiError> {
  let body: Partial<ApiErrorBody> = {};
  try {
    body = await res.json();
  } catch {
    /* gövde JSON değil (ör. proxy 502) */
  }
  const msg =
    body.message ??
    (res.status === 502 || res.status === 504 ? "Backend'e ulaşılamıyor" : `İstek başarısız (HTTP ${res.status})`);
  return new ApiError(res.status, body.error ?? "http_error", msg, body.correlation_id ?? res.headers.get("X-Correlation-ID") ?? undefined, body.details);
}

export async function request<T>(method: string, path: string, body?: unknown, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method,
      ...init,
      headers: {
        Accept: "application/json",
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        "X-Request-ID": newRequestId(),
        ...authHeaders(),
        ...(init?.headers ?? {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "network_error", "Ağ hatası: sunucuya bağlanılamadı");
  }
  if (res.status === 401 && path !== "/auth/login") {
    tokenStore.clear();
    onUnauthorized?.();
  }
  if (!res.ok) throw await toError(res);
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") ?? "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

export const api = {
  get: <T>(path: string, init?: RequestInit) => request<T>("GET", path, undefined, init),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  put: <T>(path: string, body: unknown) => request<T>("PUT", path, body),
};

export async function downloadText(path: string, filename: string) {
  const text = await request<string>("GET", path);
  const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}
