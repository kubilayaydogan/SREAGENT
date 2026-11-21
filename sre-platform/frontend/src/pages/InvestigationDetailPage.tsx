import { AlertTriangle, BookOpen, FileText, GitCompare, Siren } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { AppTag, ConfidenceBadge, InvestigationStatusBadge, SeverityBadge } from "@/components/badges";
import { Markdown } from "@/components/Markdown";
import { EvidenceTable, ToolTable } from "@/components/RecordTables";
import { Button, Card, CardBody, CardHeader, Skeleton } from "@/components/ui/primitives";
import { TabPanel, Tabs } from "@/components/ui/overlay";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { FeedbackBar } from "@/features/chat/FeedbackBar";
import { useEvidence, useGenerateRca, useIncidentMutations, useInvestigation, useRcas, useRecommendations, useSimilar, useTools } from "@/hooks/queries";
import { useAuth } from "@/services/auth";
import { mechanismLabel } from "@/lib/steps";
import { fmtDuration, fmtNum, fmtPct, fmtRelative, fmtTime, shortId } from "@/lib/utils";
import type { InvestigationDetail } from "@/types/api";

function Summary({ inv }: { inv: InvestigationDetail }) {
  const r = inv.report ?? {};
  const mechs = r.mechanisms ?? [];
  const impacted = Object.entries(r.impacted ?? {});
  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <Card className="xl:col-span-2">
        <CardHeader title="Açıklama" description={inv.explanation_source ? `kaynak: ${inv.explanation_source}` : undefined} />
        <CardBody>{inv.explanation ? <Markdown>{inv.explanation}</Markdown> : <p className="text-sm text-muted">Açıklama üretilmedi.</p>}</CardBody>
      </Card>
      <div className="space-y-4">
        <Card>
          <CardHeader title="Kök neden hipotezi" />
          <CardBody className="space-y-2 text-sm">
            <p className="text-ink">{inv.root_cause_hypothesis ?? "Tanımlı mekanizmalarla açıklanamadı."}</p>
            <ConfidenceBadge confidence={inv.confidence} />
            <p className="text-[11px] text-muted">Korelasyon temelli hipotezdir; doğrulama gerektirir.</p>
          </CardBody>
        </Card>
        {!!r.data_gaps?.length && (
          <Card>
            <CardHeader title="Veri boşlukları" />
            <CardBody>
              <ul className="space-y-1 text-xs text-ink-2">
                {r.data_gaps.map((g) => (
                  <li key={g} className="flex gap-1.5">
                    <AlertTriangle className="mt-px size-3.5 shrink-0 text-st-warning" />
                    {g}
                  </li>
                ))}
              </ul>
            </CardBody>
          </Card>
        )}
      </div>
      <Card className="xl:col-span-2">
        <CardHeader title="Mekanizmalar" description="Deterministik ilişkilendirme sonucu" />
        {mechs.length ? (
          <ul className="divide-y divide-line">
            {mechs.map((m) => (
              <li key={m.id} className="px-4 py-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-ink">{m.title}</span>
                  {m.id === inv.primary_mechanism && <span className="rounded bg-accent/15 px-1.5 text-[11px] text-[#8ab8f0]">birincil</span>}
                  {m.secondary_to && <span className="text-[11px] text-muted">ikincil: {mechanismLabel(m.secondary_to)}</span>}
                </div>
                {m.mechanism && <p className="mt-1 text-xs text-ink-2">{m.mechanism}</p>}
                <div className="mt-1 flex flex-wrap gap-3 text-xs text-muted">
                  {!!m.source_apps?.length && (
                    <span className="inline-flex items-center gap-1.5">
                      kaynak: {m.source_apps.map((a) => <AppTag key={a} id={a} />)}
                    </span>
                  )}
                  {!!m.affected_apps?.length && (
                    <span className="inline-flex items-center gap-1.5">
                      etkilenen: {m.affected_apps.map((a) => <AppTag key={a} id={a} />)}
                    </span>
                  )}
                  {!!m.evidence_ids?.length && <span className="font-mono">kanıt: {m.evidence_ids.join(", ")}</span>}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState title="Mekanizma tespit edilmedi" description="Baseline'a göre anlamlı bir bozulma veya tanımlı bir mekanizma bulunamadı." />
        )}
      </Card>
      <Card>
        <CardHeader title="Etkilenen uygulamalar" />
        <CardBody className="space-y-2">
          {impacted.length ? (
            impacted.map(([a, v]) => (
              <div key={a}>
                <AppTag id={a} />
                <ul className="mt-0.5 ml-3.5 list-disc pl-3 text-xs text-ink-2">
                  {(v.reasons ?? []).map((x) => (
                    <li key={x}>{x}</li>
                  ))}
                </ul>
              </div>
            ))
          ) : (
            <p className="text-xs text-muted">Etki tespit edilmedi.</p>
          )}
          {!!r.human_review?.length && (
            <div className="border-t border-line pt-2">
              <div className="mb-1 text-xs font-medium text-ink">İnsan incelemesi önerileri</div>
              <ul className="list-disc pl-4 text-xs text-ink-2">
                {r.human_review.map((x) => (
                  <li key={x}>{x}</li>
                ))}
              </ul>
            </div>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

/** Geçmiş RCA karşılaştırma: aynı mekanizma / uygulamadaki benzer araştırmalar + bilgi bankası önerileri. */
function Compare({ inv }: { inv: InvestigationDetail }) {
  const sim = useSimilar(inv.id);
  const rec = useRecommendations(inv.id);
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader title="Benzer geçmiş araştırmalar" description="Aynı mekanizma ve uygulama kümesi, yakınlığa göre" />
        {sim.isLoading ? (
          <Skeleton className="m-4 h-32" />
        ) : sim.error ? (
          <ErrorState error={sim.error} />
        ) : sim.data?.length ? (
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted">
              <tr className="border-b border-line">
                <th className="px-4 py-2 font-medium">Araştırma</th>
                <th className="px-4 py-2 font-medium">Mekanizma</th>
                <th className="px-4 py-2 text-right font-medium">Risk</th>
                <th className="px-4 py-2 font-medium">Önem</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-b border-line bg-accent/5">
                <td className="px-4 py-2 text-xs text-ink">bu araştırma</td>
                <td className="px-4 py-2 text-xs">{mechanismLabel(inv.primary_mechanism)}</td>
                <td className="px-4 py-2 text-right tabular-nums">{inv.risk_score == null ? "—" : fmtNum(Number(inv.risk_score), 1)}</td>
                <td className="px-4 py-2">
                  <SeverityBadge severity={inv.severity} />
                </td>
              </tr>
              {sim.data.map((s) => (
                <tr key={s.id} className="border-b border-line last:border-0 hover:bg-surface-2">
                  <td className="px-4 py-2">
                    <Link to={`/investigations/${s.id}`} className="text-ink hover:underline">
                      {s.title}
                    </Link>
                    <div className="text-[11px] text-muted">{fmtRelative(s.created_at)}</div>
                  </td>
                  <td className="px-4 py-2 text-xs">{mechanismLabel(s.primary_mechanism)}</td>
                  <td className="px-4 py-2 text-right tabular-nums">{s.risk_score == null ? "—" : fmtNum(Number(s.risk_score), 1)}</td>
                  <td className="px-4 py-2">
                    <SeverityBadge severity={s.severity} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <EmptyState icon={<GitCompare className="size-7" />} title="Benzer geçmiş kayıt yok" description="Bu mekanizma/uygulama kombinasyonu ilk kez görülüyor." />
        )}
      </Card>
      <Card>
        <CardHeader title="Bilgi bankası önerileri" description="Bilinen sorunlar ve runbook'lar" />
        {rec.isLoading ? (
          <Skeleton className="m-4 h-32" />
        ) : !rec.data || (!rec.data.known_issues.length && !rec.data.runbooks.length) ? (
          <EmptyState icon={<BookOpen className="size-7" />} title="Eşleşen kayıt yok" />
        ) : (
          <ul className="divide-y divide-line text-sm">
            {rec.data.known_issues.map((k) => (
              <li key={k.id} className="px-4 py-2.5">
                <Link to={`/knowledge?tab=issues&id=${k.id}`} className="text-ink hover:underline">
                  {k.title}
                </Link>
                <div className="text-xs text-muted">Bilinen sorun{k.resolution ? ` · çözüm: ${k.resolution.slice(0, 120)}` : ""}</div>
              </li>
            ))}
            {rec.data.runbooks.map((r) => (
              <li key={r.id} className="px-4 py-2.5">
                <Link to={`/knowledge?tab=runbooks&id=${r.id}`} className="text-ink hover:underline">
                  {r.title}
                </Link>
                <div className="text-xs text-muted">
                  Runbook · risk {r.risk_level}
                  {r.requires_approval && " · onay gerekli"}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

export default function InvestigationDetailPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const { can } = useAuth();
  const [tab, setTab] = useState("summary");
  const inv = useInvestigation(id);
  const ev = useEvidence(tab === "evidence" ? id : undefined);
  const tools = useTools(tab === "tools" ? id : undefined);
  const rcas = useRcas({ investigation_id: id, size: 20 });
  const gen = useGenerateRca();
  const { create } = useIncidentMutations();

  if (inv.error) return <ErrorState className="mt-10" error={inv.error} onRetry={() => inv.refetch()} />;
  const d = inv.data;
  const done = d && (d.status === "COMPLETED" || d.status === "PARTIAL");

  return (
    <div className="mx-auto max-w-[1500px] p-4 lg:p-6">
      <div className="mb-5">
        <Link to="/investigations" className="text-xs text-muted hover:text-ink">
          ← Araştırma geçmişi
        </Link>
        {!d ? (
          <Skeleton className="mt-2 h-8 w-1/2" />
        ) : (
          <div className="mt-1 flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h1 className="text-xl font-semibold text-ink">{d.title}</h1>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
                <InvestigationStatusBadge status={d.status} />
                <SeverityBadge severity={d.severity} />
                <span>risk {d.risk_score == null ? "hesaplanamadı" : fmtNum(Number(d.risk_score), 1)}</span>
                <span>· kapsam {fmtPct(d.data_coverage == null ? null : Number(d.data_coverage), 0)}</span>
                <span>
                  · {fmtTime(d.window_start)} → {fmtTime(d.window_end, false)}
                </span>
                <span>· {fmtDuration(d.duration_ms)}</span>
                <span className="font-mono">· {d.run_id}</span>
                {d.conversation_id && (
                  <Link className="hover:text-ink" to={`/investigate/${d.conversation_id}`}>
                    · konuşma {shortId(d.conversation_id)}
                  </Link>
                )}
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {done && <FeedbackBar investigationId={d.id} />}
              <Button size="sm" variant="outline" onClick={() => nav(`/activity/${d.id}`)}>
                Adımlar
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
            </div>
          </div>
        )}
      </div>
      {d?.status === "PARTIAL" && (
        <div role="status" className="mb-4 flex gap-2 rounded-md border border-st-warning/40 bg-st-warning/10 px-4 py-2.5 text-xs text-ink-2">
          <AlertTriangle className="size-4 shrink-0 text-st-warning" /> Kısmi veri: bazı Dynatrace sorguları başarısız oldu veya veri dönmedi. Sonuçlar eksik kanıtla üretildi.
        </div>
      )}
      {d?.error && <div className="mb-4 rounded-md border border-st-critical/40 bg-st-critical/10 px-4 py-2.5 font-mono text-xs text-ink">{d.error}</div>}
      <Tabs
        value={tab}
        onValueChange={setTab}
        tabs={[
          { value: "summary", label: "Özet" },
          { value: "evidence", label: "Kanıtlar" },
          { value: "tools", label: "Araç çalıştırmaları" },
          { value: "compare", label: "Geçmişle karşılaştır" },
          { value: "rca", label: `RCA raporları${rcas.data?.total ? ` (${rcas.data.total})` : ""}` },
        ]}
      >
        <TabPanel value="summary">{d ? <Summary inv={d} /> : <Skeleton className="h-64" />}</TabPanel>
        <TabPanel value="evidence">
          <Card>{ev.isLoading ? <Skeleton className="m-4 h-40" /> : ev.error ? <ErrorState error={ev.error} /> : <EvidenceTable rows={ev.data ?? []} />}</Card>
        </TabPanel>
        <TabPanel value="tools">
          <Card>{tools.isLoading ? <Skeleton className="m-4 h-40" /> : tools.error ? <ErrorState error={tools.error} /> : <ToolTable rows={tools.data ?? []} />}</Card>
        </TabPanel>
        <TabPanel value="compare">{d && <Compare inv={d} />}</TabPanel>
        <TabPanel value="rca">
          <Card>
            {rcas.data?.items.length ? (
              <ul className="divide-y divide-line">
                {rcas.data.items.map((r) => (
                  <li key={r.id}>
                    <Link to={`/rca/${r.id}`} className="block px-4 py-3 hover:bg-surface-2">
                      <div className="text-sm text-ink">{r.title}</div>
                      <div className="text-xs text-muted">
                        {fmtTime(r.generated_at)} · {r.generated_by}
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState title="Bu araştırma için RCA raporu yok" description={done ? "Yukarıdan 'RCA üret' ile oluşturabilirsiniz." : "RCA yalnız tamamlanmış araştırmalar için üretilebilir."} />
            )}
          </Card>
        </TabPanel>
      </Tabs>
    </div>
  );
}
