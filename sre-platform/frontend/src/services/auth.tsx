import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { Role, TokenOut, User } from "@/types/api";
import { api, setUnauthorizedHandler, tokenStore } from "./api";

interface AuthCtx {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  can: (role: Role) => boolean;
}

const RANK: Record<Role, number> = { viewer: 0, sre: 1, admin: 2 };
const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const qc = useQueryClient();

  const logout = useCallback(() => {
    // Sunucu tarafı oturum iptali (token yalnız tarayıcıdan silinmez); ağ hatası çıkışı engellemez
    if (tokenStore.get()) void api.post("/auth/logout").catch(() => undefined);
    tokenStore.clear();
    setUser(null);
    qc.clear();
  }, [qc]);

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    if (!tokenStore.get()) {
      setLoading(false);
      return;
    }
    api
      .get<User>("/auth/me")
      .then(setUser)
      .catch(() => tokenStore.clear())
      .finally(() => setLoading(false));
    return () => setUnauthorizedHandler(null);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const r = await api.post<TokenOut>("/auth/login", { username, password });
    tokenStore.set(r.access_token, r.expires_at);
    setUser(r.user);
  }, []);

  const value = useMemo<AuthCtx>(
    () => ({ user, loading, login, logout, can: (r) => !!user && RANK[user.role] >= RANK[r] }),
    [user, loading, login, logout],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAuth AuthProvider içinde kullanılmalı");
  return c;
}
