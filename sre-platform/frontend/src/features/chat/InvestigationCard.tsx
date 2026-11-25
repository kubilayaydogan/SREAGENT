import { Activity, AlertTriangle, FileText, ExternalLink, Siren } from "lucide-react";
import { Link, useNavigate } from "react-router";
import { AppTag, ConfidenceBadge, InvestigationStatusBadge, SeverityBadge } from "@/components/badges";
import { Button } from "@/components/ui/primitives";
import { useGenerateRca, useIncidentMutations } from "@/hooks/queries";
import { mechanismLabel } from "@/lib/steps";
import { fmtDuration, fmtPct, fmtTime } from "@/lib/utils";
import { RiskValue } from "@/components/risk";
import { useAuth } from "@/services/auth";
import type { InvestigationSummary } from "@/types/api";
import { FeedbackBar } from "./FeedbackBar";

/** Araştırma özeti kartı (sohbet içinde). Tüm değerler kayıttan gelir; eksikler açıkça belirtilir. */
export function InvestigationCard({ inv }: { inv: InvestigationSummary }) {
  const { can } = useAuth();
  const nav = useNavigate();
  const rca = useGenerateRca();
  const { create } = useIncidentMutations();
  const done = inv.status === "COMPLETED" || inv.status === "PARTIAL";
  const apps = [...new Set([...(inv.source_apps ?? []), ...(inv.affected_apps ?? [])])];

  return (
    <div className="mt-3 rounded-md border border-line">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        <span className="text-xs font-medium text-ink">Investigation</span>
        <InvestigationStatusBadge status={inv.status} />
        <SeverityBadge severity={inv.severity} />
        <ConfidenceBadge confidence={inv.confidence} />
        <Link to={`/investigations/${inv.investigation_id}`} className="ml-auto inline-flex items-center gap-1 text-xs text-muted hover:text-ink">
          detay <ExternalLink className="size-3" />
        </Link>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 px-3 py-2 text-xs sm:grid-cols-4">
        <div>
          <dt className="text-muted">Olası mechanism</dt>
          <dd className="text-ink">{inv.mechanism ? mechanismLabel(inv.mechanism) : "tespit edilmedi"}</dd>
        </div>
        <div>
          <dt className="text-muted">Risk score</dt>
          <dd><RiskValue score={inv.risk_score} showBand /></dd>
        </div>
        <div>
          <dt className="text-muted">Data coverage</dt>
          <dd className={inv.data_coverage != null && inv.data_coverage < 0.6 ? "text-st-warning-ink" : "text-ink"}>{fmtPct(inv.data_coverage, 0)}</dd>
        </div>
        <div>
          <dt className="text-muted">Pencere</dt>
          <dd className="text-ink">
            {fmtTime(inv.window?.[0], false)}–{fmtTime(inv.window?.[1], false)} · {fmtDuration(inv.duration_ms)}
          </dd>
        </div>
      </dl>
      {apps.length > 0 && (
        <div className="flex flex-wrap gap-3 px-3 pb-2">
          {apps.map((a) => (
            <AppTag key={a} id={a} />
          ))}
        </div>
      )}
      {inv.data_gaps?.length > 0 && (
        <div className="mx-3 mb-2 flex gap-1.5 rounded-sm border border-l-2 border-st-warning/50 px-2 py-1.5 text-2xs text-ink-2">
          <AlertTriangle className="mt-px size-3.5 shrink-0 text-st-warning" />
          <span>Veri boşlukları: {inv.data_gaps.slice(0, 4).join("; ")}{inv.data_gaps.length > 4 ? ` (+${inv.data_gaps.length - 4})` : ""}</span>
        </div>
      )}
      {inv.error && <div className="mx-3 mb-2 font-mono text-2xs text-st-critical-ink">{inv.error}</div>}
      <div className="flex flex-wrap items-center gap-2 border-t border-line px-3 py-2">
        <Button size="sm" variant="ghost" onClick={() => nav(`/activity/${inv.investigation_id}`)}>
          <Activity /> Adımlar
        </Button>
        {can("sre") && done && (
          <Button
            size="sm"
            variant="ghost"
            loading={rca.isPending}
            onClick={() => rca.mutate({ investigationId: inv.investigation_id }, { onSuccess: (r) => nav(`/rca/${r.id}`) })}
          >
            <FileText /> RCA üret
          </Button>
        )}
        {can("sre") && done && inv.severity !== "low" && (
          <Button
            size="sm"
            variant="ghost"
            loading={create.isPending}
            onClick={() =>
              create.mutate(
                {
                  title: `${mechanismLabel(inv.mechanism)} — ${apps.join(", ") || "airlab"}`.slice(0, 300),
                  severity: inv.severity,
                  application_id: inv.affected_apps?.[0] ?? inv.source_apps?.[0] ?? null,
                  investigation_id: inv.investigation_id,
                },
                { onSuccess: (d) => nav(`/incidents/${d.id}`) },
              )
            }
          >
            <Siren /> Incident aç
          </Button>
        )}
        {done && <FeedbackBar investigationId={inv.investigation_id} className="ml-auto" />}
      </div>
    </div>
  );
}
