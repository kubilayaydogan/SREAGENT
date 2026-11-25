import { ExternalLink, FileText, GitCompare, ListChecks, Siren } from "lucide-react";
import { useEffect } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { AppTag, ConfidenceBadge, InvestigationStatusBadge, SeverityBadge } from "@/components/badges";
import { EvidenceTable, ToolTable } from "@/components/RecordTables";
import { RiskBar, RiskValue } from "@/components/risk";
import { Button, buttonVariants, Card, Field, Skeleton } from "@/components/ui/primitives";
import { TabPanel, Tabs } from "@/components/ui/overlay";
import { EmptyState, ErrorState, Notice, Page, PageHeader } from "@/components/ui/states";
import { FeedbackBar } from "@/features/chat/FeedbackBar";
import { Analysis, SECTIONS } from "@/features/investigation/Analysis";
import { useEvidence, useGenerateRca, useIncidentMutations, useInvestigation, useRcas, useSimilar, useTools, useTraceUrl } from "@/hooks/queries";
import { useAuth } from "@/services/auth";
import { toNum } from "@/lib/risk";
import { mechanismLabel } from "@/lib/steps";
import { fmtDuration, fmtPct, fmtRelative, fmtTime, shortId } from "@/lib/utils";
import type { InvestigationDetail, Trigger } from "@/types/api";

const TRIGGER_LABEL: Record<Trigger, string> = { chat: "AI Investigation", manual: "Manuel", scheduled: "Health check", watch: "Watch", import: "İçe aktarım" };

function Facts({ inv }: { inv: InvestigationDetail }) {
  const score = toNum(inv.risk_score);
  return (
    <div className="space-y-3">
      <Card className="p-3">
        <div className="text-xs text-muted">Risk score</div>
        <div className="mt-0.5 flex items-baseline gap-1.5">
          <RiskValue score={score} showBand className="text-2xl font-semibold" />
          {score != null && <span className="text-xs text-muted">/ 100</span>}
        </div>
        <RiskBar score={score} className="mt-2" />
        <dl className="mt-3 divide-y divide-line border-t border-line">
          <Field label="Önem">
            <SeverityBadge severity={inv.severity} />
          </Field>
          <Field label="Güven">
            <ConfidenceBadge confidence={inv.confidence} />
          </Field>
          <Field label="Data coverage">{fmtPct(toNum(inv.data_coverage), 0)}</Field>
          <Field label="Mechanism">{mechanismLabel(inv.primary_mechanism)}</Field>
          {!!inv.source_apps.length && (
            <Field label="Source">
              <span className="inline-flex flex-wrap justify-end gap-2">
                {inv.source_apps.map((a) => (
                  <AppTag key={a} id={a} />
                ))}
              </span>
            </Field>
          )}
          <Field label="Pencere">
            <span className="tabular-nums">
              {fmtTime(inv.window_start, false)}–{fmtTime(inv.window_end, false)}
            </span>
          </Field>
          {inv.baseline_start && (
            <Field label="Baseline">
              <span className="tabular-nums">
                {fmtTime(inv.baseline_start, false)}–{fmtTime(inv.baseline_end, false)}
              </span>
            </Field>
          )}
          <Field label="Süre">{fmtDuration(inv.duration_ms)}</Field>
          <Field label="Tetikleyici">{TRIGGER_LABEL[inv.trigger] ?? inv.trigger}</Field>
          <Field label="Run">
            <span className="font-mono text-2xs" title={inv.run_id}>
              {inv.run_id}
            </span>
          </Field>
        </dl>
      </Card>
      <nav aria-label="bölümler" className="hidden px-1 lg:block">
        <div className="mb-1 text-xs font-medium text-muted">Bölümler</div>
        <ul className="space-y-px text-xs">
          {SECTIONS.map(([id, label]) => (
            <li key={id}>
              <a href={`#sec-${id}`} className="block rounded-sm px-1.5 py-0.5 text-ink-2 hover:bg-surface-2 hover:text-ink">
                {label}
              </a>
            </li>
          ))}
        </ul>
      </nav>
    </div>
  );
}

/** Geçmişle karşılaştırma: aynı mechanism / servis kümesindeki önceki investigation'lar. */
function Similar({ inv }: { inv: InvestigationDetail }) {
  const sim = useSimilar(inv.id);
  if (sim.isLoading) return <Skeleton className="m-3 h-32" />;
  if (sim.error) return <ErrorState error={sim.error} />;
  if (!sim.data?.length) return <EmptyState icon={<GitCompare />} title="Benzer geçmiş kayıt yok" description="Bu mechanism / servis kombinasyonu ilk kez görülüyor." />;
  const rows = [{ ...inv, _self: true }, ...sim.data.map((s) => ({ ...s, _self: false }))];
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr>
            <th>Investigation</th>
            <th>Mechanism</th>
            <th className="num">Risk</th>
            <th>Önem</th>
            <th className="num">Zaman</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.id} className={s._self ? "[&>td]:bg-accent/6" : undefined}>
              <td className="primary">
                {s._self ? (
                  <span className="text-xs text-muted">bu investigation</span>
                ) : (
                  <Link to={`/investigations/${s.id}`} className="hover:underline">
                    {s.title}
                  </Link>
                )}
              </td>
              <td className="text-xs">{mechanismLabel(s.primary_mechanism)}</td>
              <td className="num">
                <RiskValue score={toNum(s.risk_score)} />
              </td>
              <td>
                <SeverityBadge severity={s.severity} />
              </td>
              <td className="num text-xs text-muted">{fmtRelative(s.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function InvestigationDetailPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const { can } = useAuth();
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") ?? "analysis";
  const evKey = sp.get("ev");
  const setTab = (t: string, extra?: Record<string, string>) => {
    const n = new URLSearchParams(sp);
    if (t === "analysis") n.delete("tab");
    else n.set("tab", t);
    n.delete("ev");
    for (const [k, v] of Object.entries(extra ?? {})) n.set(k, v);
    setSp(n, { replace: true });
  };
  const inv = useInvestigation(id);
  const ev = useEvidence(tab === "evidence" ? id : undefined);
  const tools = useTools(tab === "tools" ? id : undefined);
  const trace = useTraceUrl(inv.data && ["COMPLETED", "PARTIAL", "FAILED"].includes(inv.data.status) ? id : undefined);
  const rcas = useRcas({ investigation_id: id, size: 20 });
  const gen = useGenerateRca();
  const { create } = useIncidentMutations();

  useEffect(() => {
    if (tab === "evidence" && evKey && ev.data) document.getElementById(`evidence-${evKey}`)?.scrollIntoView?.({ block: "center" });
  }, [tab, evKey, ev.data]);

  if (inv.error) return <ErrorState className="mt-10" error={inv.error} onRetry={() => inv.refetch()} />;
  const d = inv.data;
  const done = d && (d.status === "COMPLETED" || d.status === "PARTIAL");
  const nEv = ev.data?.length ?? d?.report?.evidence?.length; // kayıtlı evidence (detay kayıtları dahil) yüklüyse onu say

  return (
    <Page>
      <PageHeader
        crumbs={[{ label: "Investigation geçmişi", to: "/investigations" }, { label: d ? shortId(d.id) : "…" }]}
        title={d ? d.title : <Skeleton className="h-6 w-80" />}
        meta={
          d && (
            <>
              <InvestigationStatusBadge status={d.status} />
              <span className="tabular-nums">{fmtTime(d.created_at)}</span>
              {d.conversation_id && (
                <Link className="hover:text-ink" to={`/investigate/${d.conversation_id}`}>
                  konuşma {shortId(d.conversation_id)} →
                </Link>
              )}
            </>
          )
        }
        actions={
          d && (
            <>
              {done && <FeedbackBar investigationId={d.id} />}
              {trace.data?.url && (
                <a href={trace.data.url} target="_blank" rel="noreferrer" className={buttonVariants({ variant: "outline", size: "sm" })}>
                  <ExternalLink /> LangSmith'te aç
                </a>
              )}
              <Button size="sm" variant="outline" onClick={() => nav(`/activity/${d.id}`)}>
                <ListChecks /> Adımlar
              </Button>
              {can("sre") && done && (
                <Button size="sm" variant="outline" loading={gen.isPending} onClick={() => gen.mutate({ investigationId: d.id }, { onSuccess: (r) => nav(`/rca/${r.id}`) })}>
                  <FileText /> RCA üret
                </Button>
              )}
              {can("sre") && done && (
                <Button
                  size="sm"
                  variant="outline"
                  loading={create.isPending}
                  onClick={() =>
                    create.mutate(
                      { title: d.title.slice(0, 300), severity: d.severity, application_id: d.application_id ?? d.affected_apps[0] ?? d.source_apps[0] ?? null, investigation_id: d.id },
                      { onSuccess: (x) => nav(`/incidents/${x.id}`) },
                    )
                  }
                >
                  <Siren /> Incident aç
                </Button>
              )}
            </>
          )
        }
      />
      {d?.status === "PARTIAL" && (
        <Notice tone="warning" className="mb-3">
          Kısmi veri: bazı Dynatrace query'leri başarısız oldu veya veri dönmedi. Sonuçlar eksik evidence ile üretildi — ayrıntı Data Quality bölümünde.
        </Notice>
      )}
      {d?.error && (
        <Notice tone="critical" className="mb-3">
          <span className="font-mono">{d.error}</span>
        </Notice>
      )}
      <Tabs
        value={tab}
        onValueChange={(t) => setTab(t)}
        tabs={[
          { value: "analysis", label: "Analiz" },
          { value: "evidence", label: `Evidence${nEv ? ` (${nEv})` : ""}` },
          { value: "tools", label: "Tool executions" },
          { value: "similar", label: "Benzer olaylar" },
          { value: "rca", label: `RCA raporları${rcas.data?.total ? ` (${rcas.data.total})` : ""}` },
        ]}
      >
        <TabPanel value="analysis">
          {d ? (
            <div className="grid items-start gap-3 lg:grid-cols-[minmax(0,1fr)_17rem]">
              <Analysis inv={d} onEvidence={(k) => setTab("evidence", { ev: k })} />
              <div className="lg:sticky lg:top-3">
                <Facts inv={d} />
              </div>
            </div>
          ) : (
            <Skeleton className="h-96" />
          )}
        </TabPanel>
        <TabPanel value="evidence">
          <Card>{ev.isLoading ? <Skeleton className="m-3 h-40" /> : ev.error ? <ErrorState error={ev.error} /> : <EvidenceTable rows={ev.data ?? []} highlight={evKey} />}</Card>
        </TabPanel>
        <TabPanel value="tools">
          <Card>{tools.isLoading ? <Skeleton className="m-3 h-40" /> : tools.error ? <ErrorState error={tools.error} /> : <ToolTable rows={tools.data ?? []} />}</Card>
        </TabPanel>
        <TabPanel value="similar">
          <Card>{d && <Similar inv={d} />}</Card>
        </TabPanel>
        <TabPanel value="rca">
          <Card>
            {rcas.data?.items.length ? (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Rapor</th>
                    <th>Üreten</th>
                    <th className="num">Zaman</th>
                  </tr>
                </thead>
                <tbody>
                  {rcas.data.items.map((r) => (
                    <tr key={r.id}>
                      <td className="primary">
                        <Link to={`/rca/${r.id}`} className="hover:underline">
                          {r.title}
                        </Link>
                      </td>
                      <td className="text-xs">{r.generated_by}</td>
                      <td className="num text-xs text-muted">{fmtTime(r.generated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <EmptyState title="Bu investigation için RCA raporu yok" description={done ? "Üstteki 'RCA üret' ile oluşturabilirsiniz." : "RCA yalnız tamamlanmış investigation'lar için üretilebilir."} />
            )}
          </Card>
        </TabPanel>
      </Tabs>
    </Page>
  );
}
