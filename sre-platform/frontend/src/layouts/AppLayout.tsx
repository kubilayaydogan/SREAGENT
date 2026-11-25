import {
  Activity,
  BookOpen,
  Bot,
  ChevronRight,
  ClipboardCheck,
  FileText,
  Gauge,
  History,
  LayoutDashboard,
  LogOut,
  Menu,
  Monitor,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  Siren,
  Sun,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router";
import { Select } from "@/components/ui/primitives";
import { useLlmModel, useSetLlmModel, useSystemInfo } from "@/hooks/queries";
import { useAuth } from "@/services/auth";
import { useTheme, type ThemePref } from "@/lib/theme";
import { cn, setDisplayPrefs } from "@/lib/utils";
import { usePublicPrefs } from "@/features/settings/api";
import type { Role } from "@/types/api";

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
  role?: Role;
}

const NAV: { group: string | null; items: NavItem[] }[] = [
  { group: null, items: [{ to: "/", label: "Overview", icon: LayoutDashboard, end: true }] },
  {
    group: "Operasyon",
    items: [
      { to: "/incidents", label: "Incident'lar", icon: Siren },
      { to: "/approvals", label: "Onaylar", icon: ClipboardCheck },
    ],
  },
  {
    group: "Investigation",
    items: [
      { to: "/investigate", label: "AI Investigation", icon: Bot },
      { to: "/investigations", label: "Investigation geçmişi", icon: History },
      { to: "/activity", label: "Agent Activity", icon: Activity },
      { to: "/rca", label: "RCA raporları", icon: FileText },
    ],
  },
  { group: "Bilgi", items: [{ to: "/knowledge", label: "Bilgi bankası", icon: BookOpen }] },
  {
    group: "Platform",
    items: [
      { to: "/llm", label: "LLM Monitoring", icon: Gauge, role: "sre" },
      { to: "/settings", label: "Settings", icon: Settings, role: "sre" },
    ],
  },
];

/** Breadcrumb: route'un ilk parçası → grup › sayfa; detay sayfalarında kayıt kimliği sayfanın kendi başlığındadır. */
function useCrumbs(): string[] {
  const { pathname } = useLocation();
  const seg = "/" + (pathname.split("/")[1] ?? "");
  for (const g of NAV)
    for (const n of g.items)
      if (n.to === seg || (n.to === "/" && seg === "/")) return [g.group, n.label].filter(Boolean) as string[];
  if (seg === "/account") return ["Hesabım"];
  return [];
}

const ROLE_LABEL = { admin: "Yönetici", sre: "SRE", viewer: "İzleyici" } as const;
const SIDEBAR_KEY = "sre.sidebar";

function SystemStatus() {
  const { data, isError } = useSystemInfo();
  if (isError)
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-st-critical-ink">
        <span aria-hidden className="size-1.5 rounded-full bg-st-critical" /> Backend erişilemiyor
      </span>
    );
  if (!data) return null;
  return (
    <div className="flex items-center gap-3 text-xs">
      <span className="inline-flex items-center gap-1.5 text-ink-2" title={data.dynatrace_environment ?? ""}>
        <span aria-hidden className={cn("size-1.5 rounded-full", data.mcp_configured ? "bg-st-good" : "bg-st-warning")} />
        Dynatrace MCP {data.mcp_configured ? "" : "yapılandırılmadı"}
      </span>
      {data.llm_enabled ? (
        <ModelSwitch fallback={data.llm_model} />
      ) : (
        <span className="inline-flex items-center gap-1.5 text-ink-2">
          <span aria-hidden className="size-1.5 rounded-full bg-st-warning" /> LLM kapalı (deterministik)
        </span>
      )}
    </div>
  );
}

/** Claude modeli: yönetici seçer (Opus/Sonnet), diğer roller yalnız görür. Seçim backend ve worker'da hemen geçerli. */
function ModelSwitch({ fallback }: { fallback: string | null }) {
  const { user } = useAuth();
  const { data } = useLlmModel();
  const set = useSetLlmModel();
  const current = data?.model ?? fallback;
  const label = data?.options.find((o) => o.id === current)?.label ?? current ?? "";
  const who = data?.changed_by ? `Son değiştiren: ${data.changed_by}${data.changed_at ? " · " + new Date(data.changed_at).toLocaleString("tr-TR") : ""}` : "";
  if (user?.role !== "admin" || !data)
    return (
      <span className="text-ink-2" title={who}>
        LLM {label}
      </span>
    );
  return (
    <label className="flex items-center gap-1.5" title={who}>
      <span className="text-muted">LLM</span>
      <Select aria-label="Claude modeli" className="h-6 w-auto py-0 text-xs" value={current ?? ""} disabled={set.isPending} onChange={(e) => set.mutate(e.target.value)}>
        {data.options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label}
          </option>
        ))}
      </Select>
    </label>
  );
}

const THEME_NEXT: Record<ThemePref, ThemePref> = { system: "light", light: "dark", dark: "system" };
const THEME_LABEL: Record<ThemePref, string> = { system: "Tema: sistem", light: "Tema: light", dark: "Tema: dark" };

function ThemeToggle() {
  const { pref, setPref } = useTheme();
  const Icon = pref === "light" ? Sun : pref === "dark" ? Moon : Monitor;
  return (
    <button
      onClick={() => setPref(THEME_NEXT[pref])}
      aria-label={THEME_LABEL[pref]}
      title={`${THEME_LABEL[pref]} (değiştirmek için tıklayın)`}
      className="grid size-7 place-items-center rounded-md text-muted hover:bg-surface-2 hover:text-ink"
    >
      <Icon className="size-4" />
    </button>
  );
}

function readCollapsed() {
  try {
    return localStorage.getItem(SIDEBAR_KEY) === "collapsed";
  } catch {
    return false;
  }
}

export function AppLayout() {
  const { user, logout, can } = useAuth();
  const [open, setOpen] = useState(false);
  const prefs = usePublicPrefs(!!user);
  if (prefs.data) setDisplayPrefs(prefs.data);          // render öncesi: alt sayfalar zamanı tercih edilen timezone'da gösterir
  const brand = prefs.data?.display_name ?? "airlab SRE";
  useEffect(() => {
    document.title = `${brand} Operations`;
  }, [brand]);
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const crumbs = useCrumbs();
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  const toggle = () => {
    setCollapsed((c) => {
      try {
        localStorage.setItem(SIDEBAR_KEY, c ? "expanded" : "collapsed");
      } catch {
        /* yok say */
      }
      return !c;
    });
  };
  const mini = collapsed && !open;

  return (
    <div className="flex h-full">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-surface focus:px-3 focus:py-1.5">
        İçeriğe geç
      </a>
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-30 flex flex-col border-r border-line bg-surface transition-[width,transform] duration-150 ease-out lg:static lg:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
          mini ? "w-12" : "w-56",
        )}
        aria-label="ana menü"
      >
        <div className={cn("flex h-11 shrink-0 items-center gap-2 border-b border-line", mini ? "justify-center px-0" : "px-3")}>
          <Link to="/" className="flex min-w-0 items-center gap-2" aria-label={`${brand} — Overview`}>
            <span className="grid size-6 shrink-0 place-items-center rounded-md bg-ink text-page">
              <Activity className="size-3.5" />
            </span>
            {!mini && <span className="truncate text-[13px] font-semibold text-ink">{brand}</span>}
          </Link>
          <button className="ml-auto text-muted lg:hidden" onClick={() => setOpen(false)} aria-label="menüyü kapat">
            <X className="size-4" />
          </button>
        </div>
        <nav className="flex-1 overflow-y-auto px-2 py-2" aria-label="sayfalar">
          {NAV.map((g, gi) => {
            const items = g.items.filter((n) => !n.role || can(n.role));
            if (!items.length) return null;
            return (
              <div key={gi} className={cn(gi > 0 && "mt-3")}>
                {g.group && !mini && <div className="mb-1 px-2 text-xs font-medium text-muted">{g.group}</div>}
                {g.group && mini && <div aria-hidden className="mx-2 mb-2 border-t border-line" />}
                <ul className="space-y-px">
                  {items.map((n) => (
                    <li key={n.to}>
                      <NavLink
                        to={n.to}
                        end={n.end}
                        title={mini ? n.label : undefined}
                        aria-label={mini ? n.label : undefined}
                        className={({ isActive }) =>
                          cn(
                            "relative flex h-7 items-center gap-2 rounded-md text-[13px] transition-colors duration-100",
                            mini ? "justify-center" : "px-2",
                            isActive
                              ? "bg-surface-3 font-medium text-ink before:absolute before:top-1.5 before:bottom-1.5 before:-left-2 before:w-0.5 before:rounded-r before:bg-accent"
                              : "text-ink-2 hover:bg-surface-2 hover:text-ink",
                          )
                        }
                      >
                        <n.icon className="size-4 shrink-0" />
                        {!mini && <span className="truncate">{n.label}</span>}
                      </NavLink>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </nav>
        <div className="hidden border-t border-line p-2 lg:block">
          <button
            onClick={toggle}
            className={cn("flex h-7 w-full items-center gap-2 rounded-md text-xs text-muted hover:bg-surface-2 hover:text-ink", mini ? "justify-center" : "px-2")}
            aria-label={collapsed ? "menüyü genişlet" : "menüyü daralt"}
            title={collapsed ? "Menüyü genişlet" : "Menüyü daralt"}
          >
            {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
            {!mini && "Daralt"}
          </button>
        </div>
      </aside>
      {open && <div className="fixed inset-0 z-20 bg-black/40 lg:hidden" onClick={() => setOpen(false)} />}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-11 shrink-0 items-center gap-3 border-b border-line bg-surface px-3 lg:px-4">
          <button onClick={() => setOpen(true)} aria-label="menüyü aç" className="text-ink-2 lg:hidden">
            <Menu className="size-5" />
          </button>
          <nav aria-label="konum" className="flex min-w-0 items-center gap-1 text-[13px]">
            {crumbs.map((c, i) => (
              <span key={c} className="inline-flex min-w-0 items-center gap-1">
                {i > 0 && <ChevronRight aria-hidden className="size-3.5 shrink-0 text-muted" />}
                <span className={cn("truncate", i === crumbs.length - 1 ? "font-medium text-ink" : "text-muted")}>{c}</span>
              </span>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <div className="hidden md:block">
              <SystemStatus />
            </div>
            <span aria-hidden className="hidden h-4 w-px bg-line md:block" />
            <ThemeToggle />
            <div className="flex items-center gap-2">
              <Link to="/account" title="Hesabım: oturumlar, parola, yetkiler" className="hidden rounded-md px-1.5 py-0.5 text-right leading-tight hover:bg-surface-2 sm:block">
                <div className="max-w-40 truncate text-xs text-ink">{user?.display_name}</div>
                <div className="text-2xs text-muted">{user ? ROLE_LABEL[user.role] : ""}</div>
              </Link>
              <button onClick={logout} className="grid size-7 place-items-center rounded-md text-muted hover:bg-surface-2 hover:text-ink" aria-label="çıkış yap" title="Çıkış">
                <LogOut className="size-4" />
              </button>
            </div>
          </div>
        </header>
        <main id="main" className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
