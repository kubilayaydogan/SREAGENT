import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

export type ThemePref = "light" | "dark" | "system";
type Resolved = "light" | "dark";
const KEY = "sre.theme";

function systemTheme(): Resolved {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function readPref(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" || v === "system" ? v : "system";
  } catch {
    return "system";
  }
}

/** Tema <html data-theme> üzerinden uygulanır; ilk render.dan önce main.tsx aynı kuralı senkron uygular (CSP satır içi betiğe izin vermez). */
export function applyTheme(t: Resolved) {
  document.documentElement.dataset.theme = t;
}

const Ctx = createContext<{ pref: ThemePref; theme: Resolved; setPref: (p: ThemePref) => void }>({
  pref: "system",
  theme: "dark",
  setPref: () => {},
});

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [pref, setPrefState] = useState<ThemePref>(readPref);
  const [sys, setSys] = useState<Resolved>(systemTheme);
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!mq) return;
    const on = () => setSys(mq.matches ? "dark" : "light");
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  const theme: Resolved = pref === "system" ? sys : pref;
  useEffect(() => applyTheme(theme), [theme]);
  const setPref = useCallback((p: ThemePref) => {
    setPrefState(p);
    try {
      localStorage.setItem(KEY, p);
    } catch {
      /* depolama kapalı: yalnız bu oturum */
    }
  }, []);
  return <Ctx.Provider value={{ pref, theme, setPref }}>{children}</Ctx.Provider>;
}

export const useTheme = () => useContext(Ctx);

/** İlk render'dan önce (main.tsx) — kayıtlı tercih yoksa sistem teması. */
export function applyInitialTheme() {
  const p = readPref();
  applyTheme(p === "system" ? systemTheme() : p);
}
