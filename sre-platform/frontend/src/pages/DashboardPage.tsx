import { ArrowDownRight, ArrowUpRight, ChevronDown, ChevronRight, Minus } from "lucide-react";
import { Fragment, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { AppTag, HealthBadge, IncidentStatusBadge, InvestigationStatusBadge, SEVERITY_RANK, SeverityBadge } from "@/components/badges";
import { Sparkline, TimeSeriesChart, type SeriesDef } from "@/components/charts/TimeSeriesChart";
import { AppSelect, RangeTabs, RefreshControl } from "@/components/filters";
import { RiskBreakdown, RiskValue } from "@/components/risk";
import { MetricStrip, StatTile } from "@/components/StatTile";
import { Card, CardHeader, Segmented, Skeleton } from "@/components/ui/primitives";
import { EmptyState, ErrorState, Notice, Page, PageHeader } from "@/components/ui/states";
import { useDashboard, useInvestigation, useRiskHistory } from "@/hooks/queries";
import { appColor, appLabel, CHART } from "@/lib/colors";
import { bandOf, BAND_TEXT, RISK_BANDS, toNum, type AppRisk } from "@/lib/risk";
import { mechanismLabel } from "@/lib/steps";
import { cn, fmtMs, fmtNum, fmtPct, fmtRelative, fmtTime } from "@/lib/utils";
import type { AppHealth, DashboardSummary, HealthStatus, RangeKey, SeriesPoint } from "@/types/api";

type Metric = keyof Pick<SeriesPoint, "rpm" | "p95_ms" | "error_rate" | "mean_ms">;

const METRICS: { value: Metric; label: string; unit: string; fmt: (v: number | null) => string }[] = [
  { value: "p95_ms", label: "P95 latency", unit: "ms", fmt: fmtMs },
  { value: "mean_ms", label: "Ortalama", unit: "ms", fmt: fmtMs },
  { value: "rpm", label: "Throughput", unit: "istek/dk", fmt: (v) => fmtNum(v, 1) },
  { value: "error_rate", label: "Error rate", unit: "%", fmt: (v) => fmtPct(v, 1) },
];

const STATUS_RANK: Record<HealthStatus, number> = { down: 3, degraded: 2, unknown: 1, healthy: 0 };

function seriesOf(apps: Record<string, AppHealth>, m: Metric): SeriesDef[] {
  return Object.entries(apps).map(([id, a]) => ({ id, points: a.series.map((p) => ({ t: p.t, v: p[m] })) }));
}

function DataNotice({ d }: { d: DashboardSummary }) {
  if (!d.available)
    return (
      <Notice tone="critical" className="mb-3">
        <b className="font-medium text-ink">Dynatrace verisi alınamadı.</b> Metrikler gösterilmiyor; hiçbir değer tahmin edilmez.
        {d.error && <div className="mt-0.5 font-mono text-2xs text-muted">{d.error}</div>}
      </Notice>
    );
  const failed = Object.entries(d.sources ?? {}).filter(([, s]) => s !== "OK" && s !== "EMPTY");
  if (!failed.length) return null;
  return (
    <Notice tone="warning" className="mb-3">
      Kısmi veri: {failed.map(([k, s]) => `${k} (${s})`).join(", ")} query başarısız; ilgili değerler "—" olarak gösterilir.
    </Notice>
  );
}

function Delta({ cur, prev }: { cur: number | null; prev: number | null }) {
  if (cur == null || prev == null) return null;
  const d = cur - prev;
  if (Math.abs(d) < 0.5)
    return (
      <span className="inline-flex items-center gap-0.5">
        <Minus className="size-3" /> değişim yok
      </span>
    );
  const Up = d > 0 ? ArrowUpRight : ArrowDownRight;
  return (
    <span className={cn("inline-flex items-center gap-0.5", d > 0 ? "text-st-serious-ink" : "text-st-good-ink")}>
      <Up className="size-3" />
      {d > 0 ? "+" : "−"}
      {fmtNum(Math.abs(d), 1)} önceki ölçüme göre
    </span>
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
  const dash = useDashboard(range, app || undefined);
  const risk = useRiskHistory(24);
  const { data: d, isLoading: loading, error } = dash;
  const [metric, setMetric] = useState<Metric>("p95_ms");

  const hist = risk.data?.items ?? [];
  const latest = hist.find((i) => i.status === "COMPLETED" || i.status === "PARTIAL");
  const prev = latest ? hist.slice(hist.indexOf(latest) + 1).find((i) => i.status === "COMPLETED" || i.status === "PARTIAL") : undefined;
  const latestDetail = useInvestigation(latest?.id);
  const riskApps = (latestDetail.data?.report?.risk?.apps ?? {}) as Record<string, AppRisk>;
  const score = toNum(latest?.risk_score);
  const band = bandOf(score);

  const o = d?.overall ?? {};
  const counts = d?.status_counts ?? {};
  const apps = d?.applications ?? {};
  const worstP95 = useMemo(() => {
    let best: [string, number] | null = null;
    for (const [id, a] of Object.entries(apps)) if (a.p95_ms != null && (!best || a.p95_ms > best[1])) best = [id, a.p95_ms];
    return best;
  }, [apps]);
  const incidents = useMemo(() => [...(d?.recent_incidents ?? [])].sort((a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity]), [d?.recent_incidents]);
  const critInc = incidents.filter((i) => i.severity === "critical" || i.severity === "high").length;
  const total = d?.monitored_applications ?? 0;
  const m = METRICS.find((x) => x.value === metric)!;

  const riskSeries: SeriesDef[] = [
    {
      id: "overall",
      label: "Overall risk score",
      color: CHART.single,
      points: [...hist].reverse().filter((i) => i.status === "COMPLETED" || i.status === "PARTIAL").map((i) => ({ t: i.created_at, v: toNum(i.risk_score) })),
    },
  ];

  return (
    <Page>
      <PageHeader
        title="Overview"
        meta={
          d ? (
            <>
              <span className="tabular-nums">
                {fmtTime(d.start)} → {fmtTime(d.end, false)}
              </span>
              <span>Dynatrace · {d.interval_minutes} dk aralık</span>
            </>
          ) : undefined
        }
        actions={
          <>
            <AppSelect value={app} onChange={(v) => set("app", v)} />
            <RangeTabs value={range} onChange={(v) => set("range", v)} />
            <RefreshControl
              onRefresh={() => {
                dash.refetch();
                risk.refetch();
              }}
              fetching={dash.isFetching}
              updatedAt={dash.dataUpdatedAt}
              auto="60 sn"
            />
          </>
        }
      />
      {error && !d ? (
        <Card>
          <ErrorState error={error} onRetry={() => dash.refetch()} />
        </Card>
      ) : (
        <div className="space-y-3">
          {d && <DataNotice d={d} />}

          <MetricStrip className="grid-cols-2 sm:grid-cols-3 xl:grid-cols-6">
            <StatTile
              label="Overall risk score"
              loading={risk.isLoading}
              value={score == null ? "—" : fmtNum(score, 0)}
              unit="/ 100"
              missing={score == null}
              tone={band ? BAND_TEXT[band] : undefined}
              sub={
                latest ? (
                  prev && score != null && toNum(prev.risk_score) != null ? (
                    <Delta cur={score} prev={toNum(prev.risk_score)} />
                  ) : (
                    `${fmtTime(latest.created_at, false)} ölçümü`
                  )
                ) : (
                  "Health check ölçümü yok"
                )
              }
            />
            <StatTile
              label="Aktif incident"
              loading={loading}
              value={fmtNum(d?.active_incidents)}
              tone={critInc ? "text-st-critical-ink" : undefined}
              sub={
                d?.active_incidents ? (
                  <Link to="/incidents?status=OPEN,INVESTIGATING,MITIGATED" className="hover:text-ink">
                    {critInc ? `${critInc} kritik / yüksek` : "listeyi aç"}
                  </Link>
                ) : (
                  "açık incident yok"
                )
              }
            />
            <StatTile
              label="İzlenen servis"
              loading={loading}
              value={
                <span>
                  {counts.healthy ?? 0}
                  <span className="text-base font-normal text-muted">/{total}</span>
                </span>
              }
              sub={
                (counts.degraded ?? 0) + (counts.down ?? 0) > 0
                  ? [counts.down ? `${counts.down} erişilemiyor` : "", counts.degraded ? `${counts.degraded} bozulmuş` : ""].filter(Boolean).join(" · ")
                  : counts.unknown
                    ? `${counts.unknown} serviste veri yok`
                    : "tümü sağlıklı"
              }
              tone={counts.down ? "text-st-critical-ink" : counts.degraded ? "text-st-warning-ink" : undefined}
            />
            <StatTile label="Error rate" loading={loading} value={fmtPct(o.error_rate, 2)} missing={o.error_rate == null} sub="5xx / toplam istek" />
            <StatTile
              label="P95 latency"
              loading={loading}
              value={fmtMs(o.p95_ms_worst)}
              missing={o.p95_ms_worst == null}
              sub={worstP95 ? `en yüksek: ${appLabel(worstP95[0])} · P99 ${fmtMs(o.p99_ms_worst)}` : "span latency"}
            />
            <StatTile
              label="Throughput"
              loading={loading}
              value={o.throughput_rpm == null ? "—" : fmtNum(o.throughput_rpm, 0)}
              unit="istek/dk"
              missing={o.throughput_rpm == null}
              sub={`ortalama yanıt ${fmtMs(o.mean_ms)}`}
            />
          </MetricStrip>

          <div className="grid gap-3 xl:grid-cols-3">
            <Card className="xl:col-span-2">
              <CardHeader
                title="Risk score trendi"
                description="Zamanlanmış health check · son 24 saat · deterministik scoring"
                actions={
                  latest && (
                    <Link to={`/investigations/${latest.id}`} className="text-xs text-muted hover:text-ink">
                      son ölçüm {fmtTime(latest.created_at, false)} →
                    </Link>
                  )
                }
              />
              <div className="p-3">
                {risk.isLoading ? (
                  <Skeleton className="h-[200px]" />
                ) : risk.error ? (
                  <ErrorState error={risk.error} onRetry={() => risk.refetch()} />
                ) : (
                  <TimeSeriesChart
                    height={200}
                    series={riskSeries}
                    yMax={100}
                    step
                    minPoints={3}
                    format={(v) => fmtNum(v, 0)}
                    refLines={[
                      { y: RISK_BANDS.low, label: "orta" },
                      { y: RISK_BANDS.medium, label: "yüksek" },
                      { y: RISK_BANDS.high, label: "kritik" },
                    ]}
                    emptyText="Son 24 saatte health check ölçümü yok"
                  />
                )}
              </div>
            </Card>

            <Card className="flex flex-col">
              <CardHeader
                title="Aktif incident'lar"
                description={d ? `${d.active_incidents} aktif · önem sırasına göre` : undefined}
                actions={
                  <Link to="/incidents" className="text-xs text-muted hover:text-ink">
                    tümü →
                  </Link>
                }
              />
              {loading ? (
                <div className="space-y-2 p-3">
                  {[0, 1, 2].map((i) => (
                    <Skeleton key={i} className="h-9" />
                  ))}
                </div>
              ) : incidents.length ? (
                <ul className="divide-y divide-line">
                  {incidents.map((i) => (
                    <li key={i.id}>
                      <Link
                        to={`/incidents/${i.id}`}
                        className={cn(
                          "flex items-start gap-2.5 border-l-2 px-3 py-2 hover:bg-surface-2",
                          i.severity === "critical" ? "border-st-critical" : i.severity === "high" ? "border-st-serious" : "border-transparent",
                        )}
                      >
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-[13px] text-ink">{i.title}</div>
                          <div className="mt-0.5 flex items-center gap-2 text-xs text-muted">
                            {i.application_id ? <AppTag id={i.application_id} /> : "—"}
                            <span>{fmtRelative(i.detected_at)}</span>
                          </div>
                        </div>
                        <div className="flex shrink-0 flex-col items-end gap-1">
                          <SeverityBadge severity={i.severity} />
                          <IncidentStatusBadge status={i.status} />
                        </div>
                      </Link>
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState className="flex-1 py-6" title="Aktif incident yok" />
              )}
            </Card>
          </div>

          <ServicesTable d={d} loading={loading} riskApps={riskApps} riskAt={latest?.created_at} selected={app} onSelect={(id) => set("app", app === id ? "" : id)} />

          <div className="grid gap-3 xl:grid-cols-3">
            <Card className="xl:col-span-2">
              <CardHeader
                title="Servis metric'leri"
                description="Tek eksen · boşluk = o aralıkta veri yok"
                actions={<Segmented label="metric" value={metric} onChange={setMetric} options={METRICS.map((x) => ({ value: x.value, label: x.label }))} />}
              />
              <div className="p-3">
                {loading ? (
                  <Skeleton className="h-[220px]" />
                ) : (
                  <TimeSeriesChart
                    height={220}
                    unit={m.unit}
                    series={seriesOf(apps, metric)}
                    format={m.fmt}
                    emptyText={d?.available === false ? "Dynatrace verisi alınamadı" : "Bu aralıkta veri yok"}
                  />
                )}
              </div>
            </Card>
            <Card>
              <CardHeader
                title="PostgreSQL connection"
                description={d?.db?.used != null ? `${fmtNum(d.db.used)} / ${fmtNum(d.db.slots)} bağlantı · tepe ${fmtPct(o.db_connection_ratio_max, 0)}` : "kullanılan / max_connections"}
                actions={<span className="text-[13px] font-semibold text-ink tabular-nums">{fmtPct(o.db_connection_ratio, 0)}</span>}
              />
              <div className="p-3">
                {loading ? (
                  <Skeleton className="h-[220px]" />
                ) : (
                  <TimeSeriesChart
                    height={220}
                    yMax={1}
                    unit="%"
                    series={[{ id: "db", label: "PG connection", color: CHART.single, points: (d?.db_series ?? []).map((p) => ({ t: p.t, v: p.ratio })) }]}
                    format={(v) => fmtPct(v, 0)}
                  />
                )}
              </div>
            </Card>
          </div>

          <Card>
            <CardHeader
              title="Son investigation'lar"
              description={d ? `${d.open_investigations} çalışıyor · 7 günde ${d.investigation_stats_7d.total}` : undefined}
              actions={
                <Link to="/investigations" className="text-xs text-muted hover:text-ink">
                  tümü →
                </Link>
              }
            />
            {(d?.recent_investigations ?? []).length ? (
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Investigation</th>
                      <th>Problem</th>
                      <th className="num">Risk</th>
                      <th>Önem</th>
                      <th>Durum</th>
                      <th className="num">Zaman</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d!.recent_investigations.map((i) => (
                      <tr key={i.id}>
                        <td className="primary max-w-[28rem]">
                          <Link to={`/investigations/${i.id}`} className="block truncate hover:underline">
                            {i.title}
                          </Link>
                        </td>
                        <td className="text-xs">{mechanismLabel(i.primary_mechanism)}</td>
                        <td className="num">
                          <RiskValue score={toNum(i.risk_score)} />
                        </td>
                        <td>
                          <SeverityBadge severity={i.severity} />
                        </td>
                        <td>
                          <InvestigationStatusBadge status={i.status} />
                        </td>
                        <td className="num text-xs text-muted">{fmtRelative(i.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              !loading && <EmptyState title="Henüz investigation yok" description="AI Investigation ekranından bir soru sorun." />
            )}
          </Card>
        </div>
      )}
    </Page>
  );
}

type SortKey = "status" | "risk" | "rpm" | "err" | "p95" | "p99" | "pool";

function ServicesTable({ d, loading, riskApps, riskAt, selected, onSelect }: {
  d: DashboardSummary | undefined;
  loading: boolean;
  riskApps: Record<string, AppRisk>;
  riskAt?: string;
  selected: string;
  onSelect: (id: string) => void;
}) {
  const [sort, setSort] = useState<{ k: SortKey; desc: boolean }>({ k: "status", desc: true });
  const [open, setOpen] = useState<string | null>(null);
  const rows = useMemo(() => {
    const v = (id: string, a: AppHealth): number => {
      switch (sort.k) {
        case "status":
          return STATUS_RANK[a.status] * 1000 + (riskApps[id]?.score ?? -1);
        case "risk":
          return riskApps[id]?.score ?? -1;
        case "rpm":
          return a.throughput_rpm ?? -1;
        case "err":
          return a.error_rate ?? -1;
        case "p95":
          return a.p95_ms ?? -1;
        case "p99":
          return a.p99_ms ?? -1;
        case "pool":
          return a.pool.last_ratio ?? -1;
      }
    };
    return Object.entries(d?.applications ?? {}).sort(([ia, a], [ib, b]) => (sort.desc ? v(ib, b) - v(ia, a) : v(ia, a) - v(ib, b)));
  }, [d?.applications, riskApps, sort]);

  const Th = ({ k, children, num }: { k: SortKey; children: string; num?: boolean }) => (
    <th className={num ? "num" : undefined} aria-sort={sort.k === k ? (sort.desc ? "descending" : "ascending") : "none"}>
      <button className="inline-flex items-center gap-0.5 hover:text-ink" onClick={() => setSort((s) => ({ k, desc: s.k === k ? !s.desc : true }))}>
        {children}
        {sort.k === k && <ChevronDown className={cn("size-3", !sort.desc && "rotate-180")} />}
      </button>
    </th>
  );

  return (
    <Card>
      <CardHeader
        title="Servis sağlığı"
        description={`Durum kuralları deterministik · risk: son health check${riskAt ? ` (${fmtTime(riskAt, false)})` : ""} · satırı açarak bileşenleri inceleyin`}
      />
      {loading ? (
        <div className="space-y-2 p-3">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-7" />
          ))}
        </div>
      ) : rows.length ? (
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th className="w-6" />
                <th>Servis</th>
                <Th k="status">Durum</Th>
                <Th k="risk" num>
                  Risk
                </Th>
                <Th k="rpm" num>
                  Throughput
                </Th>
                <Th k="err" num>
                  Error rate
                </Th>
                <Th k="p95" num>
                  P95
                </Th>
                <th>P95 trend</th>
                <Th k="p99" num>
                  P99
                </Th>
                <Th k="pool" num>
                  Conn. pool
                </Th>
                <th>Neden</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(([id, a]) => {
                const r = riskApps[id];
                const isOpen = open === id;
                return (
                  <Fragment key={id}>
                    <tr className={cn(selected === id && "[&>td]:bg-surface-2")}>
                      <td className="!pr-0">
                        {r && (
                          <button onClick={() => setOpen(isOpen ? null : id)} aria-expanded={isOpen} aria-label={`${appLabel(id)} risk bileşenleri`} className="rounded-sm p-0.5 text-muted hover:text-ink">
                            {isOpen ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
                          </button>
                        )}
                      </td>
                      <td>
                        <button className="hover:underline" onClick={() => onSelect(id)} title="Bu servise filtrele">
                          <AppTag id={id} className="text-[13px] text-ink" />
                        </button>
                      </td>
                      <td>
                        <HealthBadge status={a.status} />
                      </td>
                      <td className="num">
                        <RiskValue score={r?.score ?? null} />
                      </td>
                      <td className="num">{a.throughput_rpm == null ? "—" : `${fmtNum(a.throughput_rpm, 1)}/dk`}</td>
                      <td className="num">{fmtPct(a.error_rate, 2)}</td>
                      <td className="num">{fmtMs(a.p95_ms)}</td>
                      <td>
                        <Sparkline values={a.series.map((p) => p.p95_ms)} color={appColor(id)} label={`${appLabel(id)} P95 trendi`} />
                      </td>
                      <td className="num">{fmtMs(a.p99_ms)}</td>
                      <td className="num">{fmtPct(a.pool.last_ratio, 0)}</td>
                      <td className="max-w-xs text-xs text-muted">{a.reasons.join("; ") || "—"}</td>
                    </tr>
                    {isOpen && r && (
                      <tr>
                        <td />
                        <td colSpan={10} className="!bg-surface-2">
                          <RiskBreakdown risk={r} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState title="Servis metric'i yok" description={d?.available === false ? "Dynatrace'e ulaşılamıyor." : "Bu aralıkta telemetri gelmedi."} />
      )}
    </Card>
  );
}
