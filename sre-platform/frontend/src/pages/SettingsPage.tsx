import type { ComponentType } from "react";
import { NavLink, Navigate, useParams } from "react-router";
import { Page, PageHeader } from "@/components/ui/states";
import { useSettingsOverview, type SectionId } from "@/features/settings/api";
import { ConnectorsSection } from "@/features/settings/Connectors";
import { LlmProvidersSection } from "@/features/settings/LlmProviders";
import { SecuritySection } from "@/features/settings/Security";
import {
  AgentSection,
  GeneralSection,
  NotificationsSection,
  ObservabilitySection,
  RetentionSection,
  RiskSection,
  SchedulingSection,
} from "@/features/settings/sections";
import { useAuth } from "@/services/auth";
import { cn } from "@/lib/utils";

/** İkincil gezinme. `section`: backend ayar bölümü (revizyon/restart rozeti için); connector ve audit gibi sayfaların yok. */
export const SETTINGS_NAV: { id: string; label: string; section?: SectionId; el: ComponentType }[] = [
  { id: "general", label: "General & Appearance", section: "general", el: GeneralSection },
  { id: "agent", label: "Agent", section: "agent", el: AgentSection },
  { id: "scheduling", label: "Scheduling", section: "scheduling", el: SchedulingSection },
  { id: "risk", label: "Risk Scoring", section: "risk", el: RiskSection },
  { id: "mcp", label: "MCP Servers", el: ConnectorsSection },
  { id: "llm", label: "LLM Providers", section: "llm", el: LlmProvidersSection },
  { id: "observability", label: "Observability", section: "observability", el: ObservabilitySection },
  { id: "notifications", label: "Notifications", section: "notifications", el: NotificationsSection },
  { id: "retention", label: "Retention & Memory", section: "retention", el: RetentionSection },
  { id: "security", label: "Security & Audit", el: SecuritySection },
];

export default function SettingsPage() {
  const { section = "general" } = useParams();
  const { can } = useAuth();
  const ov = useSettingsOverview();
  const cur = SETTINGS_NAV.find((n) => n.id === section);
  if (!cur) return <Navigate to="/settings/general" replace />;
  const info = (id?: SectionId) => ov.data?.sections.find((s) => s.section === id);
  const El = cur.el;
  return (
    <Page>
      <PageHeader
        title="Settings"
        description={can("admin") ? "Platform yapılandırması. Değişiklikler revizyonlanır ve audit'e yazılır." : "Salt okunur görünüm — değişiklik için admin rolü gerekir."}
        meta={ov.data && <span>Ortam: {ov.data.environment}</span>}
      />
      <div className="grid gap-4 lg:grid-cols-[200px_minmax(0,1fr)]">
        <nav aria-label="Settings bölümleri" className="lg:sticky lg:top-4 lg:self-start">
          <ul className="flex gap-0.5 overflow-x-auto lg:flex-col">
            {SETTINGS_NAV.map((n) => {
              const i = info(n.section);
              return (
                <li key={n.id}>
                  <NavLink
                    to={`/settings/${n.id}`}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center justify-between gap-2 rounded-md px-2 py-1.5 text-[13px] whitespace-nowrap",
                        isActive ? "bg-surface-3 font-medium text-ink" : "text-ink-2 hover:bg-surface-2 hover:text-ink",
                      )
                    }
                  >
                    <span>{n.label}</span>
                    {i && i.restart_pending.length > 0 && (
                      <span className="size-1.5 rounded-full bg-st-warning" title="Restart bekleyen değişiklik var" aria-label="restart bekliyor" />
                    )}
                    {i && i.restart_pending.length === 0 && i.overridden.length > 0 && (
                      <span className="text-2xs text-muted" title="Admin tarafından değiştirilen alan sayısı">
                        {i.overridden.length}
                      </span>
                    )}
                  </NavLink>
                </li>
              );
            })}
          </ul>
        </nav>
        <section aria-label={cur.label} className="min-w-0">
          <El key={cur.id} />
        </section>
      </div>
    </Page>
  );
}
