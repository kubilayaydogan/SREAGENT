import { AlertTriangle, Database, Gauge, Layers, Search, Siren, Timer, TrendingUp } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { AppTag, HealthBadge, IncidentStatusBadge, InvestigationStatusBadge, SeverityBadge } from "@/components/badges";
import { TimeSeriesChart, type SeriesDef } from "@/components/charts/TimeSeriesChart";
import { AppSelect, RangeTabs } from "@/components/filters";
import { StatTile } from "@/components/StatTile";
import { Card, CardBody, CardHeader, Skeleton } from "@/components/ui/primitives";
import { EmptyState, ErrorState, PageHeader } from "@/components/ui/states";
import { useDashboard } from "@/hooks/queries";
import { mechanismLabel } from "@/lib/steps";
import { CHART } from "@/lib/colors";
import { fmtMs, fmtNum, fmtPct, fmtRelative, fmtTime } from "@/lib/utils";
import type { AppHealth, DashboardSummary, RangeKey, SeriesPoint } from "@/types/api";

type Metric = keyof Pick<SeriesPoint, "rpm" | "p95_ms" | "error_rate" | "mean_ms">;

function seriesOf(apps: Record<string, AppHealth>, m: Metric): SeriesDef[] {
  return Object.entries(apps).map(([id, a]) => ({ id, points: a.series.map((p) => ({ t: p.t, v: p[m] })) }));
}

function DataBanner({ d }: { d: DashboardSummary }) {
  if (!d.available)
    return (
      <div role="alert" className="mb-4 flex items-start gap-2 rounded-md border border-st-critical/40 bg-st-critical/10 px-4 py-3 text-sm text-ink">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-st-critical" />
        <div>
          <b>Dynatrace verisi alınamadı.</b> Metrikler gösterilmiyor; hiçbir değer tahmin edilmez.
          {d.error && <div className="mt-0.5 font-mono text-xs text-muted">{d.error}</div>}
        </div>
      </div>
    );
  const failed = Object.entries(d.sources ?? {}).filter(([, s]) => s !== "OK" && s !== "EMPTY");
  if (!failed.length) return null;
  return (
    <div role="status" className="mb-4 flex items-start gap-2 rounded-md border border-st-warning/40 bg-st-warning/10 px-4 py-2.5 text-xs text-ink-2">
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-st-warning" />
      <span>
        Kısmi veri: {failed.map(([k, s]) => `${k} (${s})`).join(", ")} sorgusu başarısız; ilgili değerler "—" olarak gösterilir.
      </span>
    </div>
  );
}

export default function DashboardPage() {
  const [sp, setSp] = useSearchParams();
  const range = (sp.get("range") as RangeKey) || "1h";
  const app = sp.get("app") ?? "";
  const set = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v);
    else n.delete(k);
    setSp(n, { replace: true });
  };
  const { data: d, isLoading, error, refetch, isFetching } = useDashboard(range, app || undefined);
  const [metric, setMetric] = useState<Metric>("p95_ms");

  const o = d?.overall ?? {};
  const counts = d?.status_counts ?? {};
  const loading = isLoading;

  return (
    <div className="mx-auto max-w-[1500px] p-4 lg:p-6">
      <PageHeader
        title="Dashboard"
        description={
          d ? (
            <>
              {fmtTime(d.start)} → {fmtTime(d.end, false)} · Dynatrace · {d.interval_minutes} dk aralık
              {isFetching && " · yenileniyor…"}
            </>
          ) : (
            "Uygulama sağlığı ve son SRE etkinliği"
          )
        }
        actions={
          <>
            <AppSelect value={app} onChange={(v) => set("app", v)} />
            <RangeTabs value={range} onChange={(v) => set("range", v)} />
          </>
        }
      />
      {error && !d ? (
        <Card>
          <ErrorState error={error} onRetry={() => refetch()} />
        </Card>
      ) : (
        <>
          {d && <DataBanner d={d} />}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <StatTile label="İzlenen uygulama" icon={<Layers />} loading={loading} value={fmtNum(d?.monitored_applications)} />
            <StatTile
              label="Sağlıklı / Bozulmuş / Erişilemiyor"
              icon={<Gauge />}
              loading={loading}
              value={
                <span>
                  {counts.healthy ?? 0}
                  <span className="text-muted"> / </span>
                  {counts.degraded ?? 0}
                  <span className="text-muted"> / </span>
                  {counts.down ?? 0}
                </span>
              }
              sub={counts.unknown ? `${counts.unknown} uygulamada veri yok` : "durum Dynatrace metric'lerinden"}
            />
            <StatTile label="Aktif incident" icon={<Siren />} loading={loading} value={fmtNum(d?.active_incidents)} sub={<Link to="/incidents?status=OPEN,INVESTIGATING,MITIGATED" className="hover:text-ink">listeyi aç →</Link>} />
            <StatTile label="Açık araştırma" icon={<Search />} loading={loading} value={fmtNum(d?.open_investigations)} sub={`7 günde ${d?.investigation_stats_7d.total ?? 0} araştırma`} />
            <StatTile label="Ortalama yanıt" icon={<Timer />} loading={loading} value={fmtMs(o.mean_ms)} missing={o.mean_ms == null} sub="HTTP, istek ağırlıklı" />
            <StatTile label="Throughput" icon={<TrendingUp />} loading={loading} value={o.throughput_rpm == null ? "—" : `${fmtNum(o.throughput_rpm, 1)}/dk`} missing={o.throughput_rpm == null} sub="istek / dakika" />
            <StatTile label="P95 (en kötü uygulama)" loading={loading} value={fmtMs(o.p95_ms_worst)} missing={o.p95_ms_worst == null} sub="span latency" />
            <StatTile label="P99 (en kötü uygulama)" loading={loading} value={fmtMs(o.p99_ms_worst)} missing={o.p99_ms_worst == null} sub="span latency" />
            <StatTile label="Error rate" loading={loading} value={fmtPct(o.error_rate, 2)} missing={o.error_rate == null} sub="5xx / toplam" />
            <StatTile
              label="PostgreSQL connection kullanımı"
              icon={<Database />}
              loading={loading}
              value={fmtPct(o.db_connection_ratio, 0)}
              missing={o.db_connection_ratio == null}
              sub={d?.db?.used != null ? `${fmtNum(d.db.used)} / ${fmtNum(d.db.slots)} bağlantı · tepe ${fmtPct(o.db_connection_ratio_max, 0)}` : "veri yok"}
              className="col-span-2"
            />
          </div>

          <div className="mt-4 grid gap-4 xl:grid-cols-3">
            <Card className="xl:col-span-2">
              <CardHeader
                title="Uygulama metric'leri"
                description="Tek eksen; boşluk = o aralıkta veri yok"
                actions={
                  <div role="radiogroup" aria-label="metric" className="inline-flex rounded-md border border-line-strong bg-surface-2 p-0.5">
                    {(
                      [
                        ["p95_ms", "P95"],
                        ["mean_ms", "Ortalama"],
                        ["rpm", "Throughput"],
                        ["error_rate", "Error rate"],
                      ] as [Metric, string][]
                    ).map(([k, l]) => (
                      <button key={k} role="radio" aria-checked={metric === k} onClick={() => setMetric(k)} className={`rounded px-2 py-0.5 text-xs ${metric === k ? "bg-surface-3 text-ink" : "text-muted hover:text-ink"}`}>
                        {l}
                      </button>
                    ))}
                  </div>
                }
              />
              <CardBody>
                {loading ? (
                  <Skeleton className="h-[240px]" />
                ) : (
                  <TimeSeriesChart
                    height={240}
                    series={seriesOf(d?.applications ?? {}, metric)}
                    format={metric === "error_rate" ? (v) => fmtPct(v, 1) : metric === "rpm" ? (v) => fmtNum(v, 1) : fmtMs}
                    emptyText={d?.available === false ? "Dynatrace verisi alınamadı" : "Bu aralıkta veri yok"}
                  />
                )}
              </CardBody>
            </Card>
            <Card>
              <CardHeader title="PostgreSQL bağlantı oranı" description="kullanılan / max_connections" />
              <CardBody>
                {loading ? (
                  <Skeleton className="h-[240px]" />
                ) : (
                  <TimeSeriesChart
                    height={240}
                    yMax={1}
                    series={[{ id: "db", label: "PG bağlantı", color: CHART.single, points: (d?.db_series ?? []).map((p) => ({ t: p.t, v: p.ratio })) }]}
                    format={(v) => fmtPct(v, 0)}
                  />
                )}
              </CardBody>
            </Card>
          </div>

          <Card className="mt-4">
            <CardHeader title="Uygulama sağlığı" description="Durum kuralları deterministik (eşikler: watch yapılandırması)" />
            {loading ? (
              <div className="space-y-2 p-4">
                {[0, 1, 2, 3].map((i) => (
                  <Skeleton key={i} className="h-8" />
                ))}
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-left text-xs text-muted">
                    <tr className="border-b border-line">
                      <th className="px-4 py-2 font-medium">Uygulama</th>
                      <th className="px-4 py-2 font-medium">Durum</th>
                      <th className="px-4 py-2 text-right font-medium">Throughput</th>
                      <th className="px-4 py-2 text-right font-medium">Hata</th>
                      <th className="px-4 py-2 text-right font-medium">Ortalama</th>
                      <th className="px-4 py-2 text-right font-medium">P95</th>
                      <th className="px-4 py-2 text-right font-medium">P99</th>
                      <th className="px-4 py-2 text-right font-medium">Havuz</th>
                      <th className="px-4 py-2 font-medium">Neden</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(d?.applications ?? {}).map(([id, a]) => (
                      <tr key={id} className="border-b border-line last:border-0 hover:bg-surface-2">
                        <td className="px-4 py-2">
                          <button className="hover:underline" onClick={() => set("app", app === id ? "" : id)}>
                            <AppTag id={id} />
                          </button>
                        </td>
                        <td className="px-4 py-2">
                          <HealthBadge status={a.status} />
                        </td>
                        <td className="px-4 py-2 text-right tabular-nums">{a.throughput_rpm == null ? "—" : `${fmtNum(a.throughput_rpm, 1)}/dk`}</td>
                        <td className="px-4 py-2 text-right tabular-nums">{fmtPct(a.error_rate, 2)}</td>
                        <td className="px-4 py-2 text-right tabular-nums">{fmtMs(a.mean_ms)}</td>
                        <td className="px-4 py-2 text-right tabular-nums">{fmtMs(a.p95_ms)}</td>
                        <td className="px-4 py-2 text-right tabular-nums">{fmtMs(a.p99_ms)}</td>
                        <td className="px-4 py-2 text-right tabular-nums">{fmtPct(a.pool.last_ratio, 0)}</td>
                        <td className="max-w-xs px-4 py-2 text-xs text-muted">{a.reasons.join("; ") || "—"}</td>
                      </tr>
                    ))}
                    {!Object.keys(d?.applications ?? {}).length && (
                      <tr>
                        <td colSpan={9}>
                          <EmptyState title="Uygulama metric'i yok" description={d?.available === false ? "Dynatrace'e ulaşılamıyor." : "Bu aralıkta telemetri gelmedi."} />
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Son araştırmalar" actions={<Link to="/investigations" className="text-xs text-muted hover:text-ink">tümü →</Link>} />
              <ul className="divide-y divide-line">
                {(d?.recent_investigations ?? []).map((i) => (
                  <li key={i.id}>
                    <Link to={`/investigations/${i.id}`} className="flex items-center gap-3 px-4 py-2.5 hover:bg-surface-2">
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm text-ink">{i.title}</div>
                        <div className="text-xs text-muted">
                          {mechanismLabel(i.primary_mechanism)} · {fmtRelative(i.created_at)}
                        </div>
                      </div>
                      <SeverityBadge severity={i.severity} />
                      <InvestigationStatusBadge status={i.status} />
                    </Link>
                  </li>
                ))}
                {!loading && !d?.recent_investigations.length && <EmptyState title="Henüz araştırma yok" description="AI Investigation ekranından bir soru sorun." />}
              </ul>
            </Card>
            <Card>
              <CardHeader title="Açık incident'lar" actions={<Link to="/incidents" className="text-xs text-muted hover:text-ink">tümü →</Link>} />
              <ul className="divide-y divide-line">
                {(d?.recent_incidents ?? []).map((i) => (
                  <li key={i.id}>
                    <Link to={`/incidents/${i.id}`} className="flex items-center gap-3 px-4 py-2.5 hover:bg-surface-2">
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm text-ink">{i.title}</div>
                        <div className="text-xs text-muted">
                          {i.application_id ?? "—"} · {fmtRelative(i.detected_at)}
                        </div>
                      </div>
                      <SeverityBadge severity={i.severity} />
                      <IncidentStatusBadge status={i.status} />
                    </Link>
                  </li>
                ))}
                {!loading && !d?.recent_incidents.length && <EmptyState title="Açık incident yok" />}
              </ul>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
