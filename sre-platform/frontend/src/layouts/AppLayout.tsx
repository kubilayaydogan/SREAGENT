import { Activity, BookOpen, Bot, ClipboardCheck, FileText, Gauge, History, LayoutDashboard, LogOut, Menu, Siren, X } from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet } from "react-router";
import { Badge, Select } from "@/components/ui/primitives";
import { useLlmModel, useSetLlmModel, useSystemInfo } from "@/hooks/queries";
import { useAuth } from "@/services/auth";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/investigate", label: "AI Investigation", icon: Bot },
  { to: "/activity", label: "Agent Activity", icon: Activity },
  { to: "/investigations", label: "Araştırma Geçmişi", icon: History },
  { to: "/incidents", label: "Incident'lar", icon: Siren },
  { to: "/rca", label: "RCA Raporları", icon: FileText },
  { to: "/knowledge", label: "Bilgi Bankası", icon: BookOpen },
  { to: "/approvals", label: "Onaylar", icon: ClipboardCheck },
  { to: "/llm", label: "LLM Monitoring", icon: Gauge, role: "sre" as const },
];

const ROLE_LABEL = { admin: "Yönetici", sre: "SRE", viewer: "İzleyici" } as const;

function SystemStatus() {
  const { data, isError } = useSystemInfo();
  if (isError) return <Badge className="border-st-critical/50 text-[#f08a8a]">Backend erişilemiyor</Badge>;
  if (!data) return null;
  return (
    <div className="flex flex-wrap gap-1.5 text-[11px]">
      <Badge title={data.dynatrace_environment ?? ""} className={data.mcp_configured ? "" : "border-st-warning/50 text-st-warning"}>
        MCP {data.mcp_configured ? "yapılandırıldı" : "yapılandırılmadı"}
      </Badge>
      {data.llm_enabled ? <ModelSwitch fallback={data.llm_model} /> : (
        <Badge className="border-st-warning/50 text-st-warning">LLM kapalı (deterministik)</Badge>
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
  if (user?.role !== "admin" || !data) return <Badge title={who}>LLM {label}</Badge>;
  return (
    <label className="flex items-center gap-1.5" title={who}>
      <span className="text-muted">LLM</span>
      <Select
        aria-label="Claude modeli"
        className="h-7 py-0 text-[11px]"
        value={current ?? ""}
        disabled={set.isPending}
        onChange={(e) => set.mutate(e.target.value)}
      >
        {data.options.map((o) => (
          <option key={o.id} value={o.id}>{o.label}</option>
        ))}
      </Select>
    </label>
  );
}

export function AppLayout() {
  const { user, logout, can } = useAuth();
  const [open, setOpen] = useState(false);
  return (
    <div className="flex h-full">
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-30 flex w-60 flex-col border-r border-line bg-surface transition-transform lg:static lg:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-14 items-center gap-2 border-b border-line px-4">
          <div className="grid size-7 place-items-center rounded-md bg-accent text-white">
            <Activity className="size-4" />
          </div>
          <div className="leading-tight">
            <div className="text-sm font-semibold text-ink">airlab SRE</div>
            <div className="text-[11px] text-muted">Operations Platform</div>
          </div>
          <button className="ml-auto text-muted lg:hidden" onClick={() => setOpen(false)} aria-label="menüyü kapat">
            <X className="size-4" />
          </button>
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto p-2" aria-label="ana menü">
          {NAV.filter((n) => !("role" in n) || !n.role || can(n.role)).map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              onClick={() => setOpen(false)}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors",
                  isActive ? "bg-surface-3 text-ink" : "text-ink-2 hover:bg-surface-2 hover:text-ink",
                )
              }
            >
              <n.icon className="size-4" />
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="space-y-3 border-t border-line p-3">
          <SystemStatus />
          <div className="flex items-center justify-between">
            <div className="min-w-0">
              <div className="truncate text-sm text-ink">{user?.display_name}</div>
              <div className="text-[11px] text-muted">
                {user?.username} · {user ? ROLE_LABEL[user.role] : ""}
              </div>
            </div>
            <button onClick={logout} className="rounded p-1.5 text-muted hover:bg-surface-2 hover:text-ink" aria-label="çıkış yap" title="Çıkış">
              <LogOut className="size-4" />
            </button>
          </div>
        </div>
      </aside>
      {open && <div className="fixed inset-0 z-20 bg-black/50 lg:hidden" onClick={() => setOpen(false)} />}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 items-center border-b border-line px-4 lg:hidden">
          <button onClick={() => setOpen(true)} aria-label="menüyü aç" className="text-ink-2">
            <Menu className="size-5" />
          </button>
          <span className="ml-3 text-sm font-semibold">airlab SRE</span>
        </header>
        <main className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
