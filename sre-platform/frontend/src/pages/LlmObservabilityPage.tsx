import { AlertTriangle, ExternalLink } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { TimeSeriesChart } from "@/components/charts/TimeSeriesChart";
import { MetricStrip, StatTile } from "@/components/StatTile";
import { RangeTabs, RefreshControl } from "@/components/filters";
import { buttonVariants, Card, CardBody, CardHeader } from "@/components/ui/primitives";
import { TabPanel, Tabs } from "@/components/ui/overlay";
import { EmptyState, ErrorState, Page, PageHeader, TableSkeleton } from "@/components/ui/states";
import { useLlmObservability } from "@/hooks/queries";
import { CHART } from "@/lib/colors";
import { cn, fmtMs, fmtNum, fmtPct, fmtRelative, fmtTime, shortId } from "@/lib/utils";
import type { LlmAgg, LlmCallError, LlmObservability, LlmRange, LlmTrace } from "@/types/api";

const RANGES: { value: LlmRange; label: string; title: string }[] = [
  { value: "24h", label: "24h", title: "Son 24 saat" },
  { value: "7d", label: "7d", title: "Son 7 gün" },
  { value: "30d", label: "30d", title: "Son 30 gün" },
];

export const CALL_LABEL: Record<string, string> = {
  route_question: "Soru sınıflandırma",
  interpret_question: "Soru yorumlama",
  answer: "Sohbet cevabı",
  explain_findings: "RCA açıklaması",
};
export const TRIGGER_LABEL: Record<string, string> = {
  chat: "Sohbet",
  scheduled: "Periyodik kontrol",
  watch: "Watch",
  "watch-incident": "Watch incident",
  manual: "Manuel",
  analyze: "Analiz (eski)",
  ask: "Soru yorumlama (eski)",
  rca: "RCA raporu",
  "watch-recovery": "Watch recovery",
  "eval-real": "Eval",
  diğer: "Diğer",
};
const ROOT_LABEL: Record<string, string> = {
  "chat-turn": "Sohbet turu",
  "airlab-scheduled": "Periyodik analiz",
  "airlab-watch-incident": "Watch incident analizi",
  "airlab-chat": "Sohbet analizi",
};

export const fmtUsd = (v: number | null | undefined) =>
  v == null ? "—" : `$${v.toLocaleString("en-US", { minimumFractionDigits: v < 1 ? 4 : 2, maximumFractionDigits: v < 1 ? 4 : 2 })}`;
export const fmtTok = (v: number | null | undefined) =>
  v == null ? "—" : v.toLocaleString("en-US", { notation: "compact", maximumFractionDigits: 1 });   // 393.5K (B = bin/billion karışmasın)

const BUCKET_MS: Record<string, number> = { "1 hour": 3_600_000, "6 hours": 21_600_000, "1 day": 86_400_000 };

/** Boş kovaları doldurur: çağrı/maliyet o kovada gerçekten 0'dır; latency ise ölçülmemiştir (boşluk, 0 değil). */
export function fillSeries(d: Pick<LlmObservability, "start" | "end" | "bucket" | "series">) {
  const step = BUCKET_MS[d.bucket] ?? 3_600_000;
  const by = new Map(d.series.map((p) => [Math.floor(Date.parse(p.t) / step) * step, p]));
  const out: { t: string; calls: number; cost: number; p95: number | null }[] = [];
  for (let t = Math.floor(Date.parse(d.start) / step) * step; t <= Date.parse(d.end); t += step) {
    const p = by.get(t);
    out.push({ t: new Date(t).toISOString(), calls: p?.calls ?? 0, cost: p?.cost_usd ?? 0, p95: p?.p95_ms ?? null });
  }
  return out;
}

function SyncStatus({ d }: { d: LlmObservability }) {
  const { last_ok_at, last_error, last_error_at } = d.sync;
  const failing = last_error && (!last_ok_at || (last_error_at ?? "") > last_ok_at);
  return (
    <div className="text-xs text-muted">
      {last_ok_at ? <>Son senkron {fmtRelative(last_ok_at)}</> : "Henüz senkron yapılmadı"}
      {failing && (
        <div role="alert" className="mt-1 inline-flex items-center gap-1.5 text-st-serious-ink">
          <AlertTriangle className="size-3.5" /> Son senkron başarısız ({fmtRelative(last_error_at)}): {last_error}
        </div>
      )}
    </div>
  );
}

function AggTable({ rows, keyLabel, label }: { rows: (LlmAgg & { key: string })[]; keyLabel: string; label: (k: string) => string }) {
  if (!rows.length) return <EmptyState title="Bu aralıkta LLM çağrısı yok" />;
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr>
            <th>{keyLabel}</th>
            <th className="num">Çağrı</th>
            <th className="num">Hata</th>
            <th className="num">Token (girdi / çıktı)</th>
            <th className="num">p50 / p95</th>
            <th className="num">Maliyet</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}>
              <td className="primary">
                {label(r.key)}
                {label(r.key) !== r.key && <div className="font-mono text-2xs text-muted">{r.key}</div>}
              </td>
              <td className="num">{fmtNum(r.calls)}</td>
              <td className={cn("num", r.errors ? "text-st-critical-ink" : undefined)}>{fmtNum(r.errors)}</td>
              <td className="num">
                {fmtTok(r.input_tokens)} / {fmtTok(r.output_tokens)}
              </td>
              <td className="num">
                {fmtMs(r.p50_ms)} / {fmtMs(r.p95_ms)}
              </td>
              <td className="num text-ink">{fmtUsd(r.cost_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Links({ url, investigationId, conversationId }: { url: string | null; investigationId?: string | null; conversationId?: string | null }) {
  return (
    <div className="flex flex-wrap items-center justify-end gap-2 text-xs">
      {investigationId && (
        <Link className="text-ink-2 hover:text-ink" to={`/investigations/${investigationId}`}>
          investigation {shortId(investigationId)}
        </Link>
      )}
      {conversationId && (
        <Link className="text-ink-2 hover:text-ink" to={`/investigate/${conversationId}`}>
          konuşma {shortId(conversationId)}
        </Link>
      )}
      {url && (
        <a href={url} target="_blank" rel="noreferrer" className={buttonVariants({ variant: "ghost", size: "sm" })} aria-label="LangSmith'te aç">
          <ExternalLink /> LangSmith
        </a>
      )}
    </div>
  );
}

function TraceTable({ rows, empty }: { rows: LlmTrace[]; empty: string }) {
  if (!rows.length) return <EmptyState title={empty} />;
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr>
            <th>Zaman</th>
            <th>Trace</th>
            <th>Model</th>
            <th className="num">Süre</th>
            <th className="num">LLM çağrısı</th>
            <th className="num">Token</th>
            <th className="num">Maliyet</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((t) => (
            <tr key={t.id} className={cn("[&>td]:align-top", t.status === "error" && "[&>td:first-child]:shadow-[inset_2px_0_0_var(--color-st-critical)]")}>
              <td className="text-xs whitespace-nowrap">{fmtTime(t.start_time)}</td>
              <td>
                <div className="text-ink">
                  {ROOT_LABEL[t.name] ?? CALL_LABEL[t.name] ?? t.name}
                  {t.trigger && <span className="ml-1.5 text-xs text-muted">· {TRIGGER_LABEL[t.trigger] ?? t.trigger}</span>}
                </div>
                {t.status === "error" && <div className="mt-0.5 max-w-md truncate text-xs text-st-critical-ink" title={t.error ?? ""}>hata: {t.error ?? "—"}</div>}
                {t.status === "pending" && <div className="mt-0.5 text-xs text-muted">sürüyor</div>}
              </td>
              <td className="font-mono text-2xs">{t.model ?? "—"}</td>
              <td className="num">{fmtMs(t.latency_ms)}</td>
              <td className="num">{fmtNum(t.llm_calls)}</td>
              <td className="num">{fmtTok(t.tokens)}</td>
              <td className="num text-ink">{fmtUsd(t.cost_usd)}</td>
              <td>
                <Links url={t.url} investigationId={t.investigation_id} conversationId={t.conversation_id} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CallErrorTable({ rows }: { rows: LlmCallError[] }) {
  if (!rows.length) return <EmptyState title="Bu aralıkta hatalı LLM çağrısı yok" />;
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr>
            <th>Zaman</th>
            <th>Çağrı</th>
            <th>Model</th>
            <th>Hata</th>
            <th className="num">Süre</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.id} className="[&>td]:align-top">
              <td className="text-xs whitespace-nowrap">{fmtTime(e.start_time)}</td>
              <td className="primary">{CALL_LABEL[e.name] ?? e.name}</td>
              <td className="font-mono text-2xs">{e.model ?? "—"}</td>
              <td className="max-w-md text-xs text-st-critical-ink">{e.error ?? "—"}</td>
              <td className="num">{fmtMs(e.latency_ms)}</td>
              <td>
                <Links url={e.url} investigationId={e.investigation_id} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function LlmObservabilityPage() {
  const [range, setRange] = useState<LlmRange>("24h");
  const [tab, setTab] = useState("cost");
  const q = useLlmObservability(range);
  const d = q.data;
  const points = useMemo(() => (d ? fillSeries(d) : []), [d]);
  const t = d?.totals;

  return (
    <Page className="space-y-3">
      <PageHeader
        title="LLM Monitoring"
        description="LangSmith trace'lerinden periyodik senkronlanan özet · prompt/cevap içeriği platformda tutulmaz"
        meta={d && <SyncStatus d={d} />}
        actions={
          <>
            <RangeTabs value={range} onChange={setRange} options={RANGES} />
            <RefreshControl onRefresh={() => q.refetch()} fetching={q.isFetching} updatedAt={q.dataUpdatedAt} auto="60 sn" />
          </>
        }
      />
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}

      <MetricStrip className="grid-cols-2 md:grid-cols-3 xl:grid-cols-6">
        <StatTile label="LLM çağrısı" loading={!d} value={fmtNum(t?.calls)} sub={t && `${fmtNum(t.traces)} trace`} />
        <StatTile
          label="Error rate"
          loading={!d}
          missing={t?.error_rate == null}
          value={fmtPct(t?.error_rate ?? null, 1)}
          sub={t && `${fmtNum(t.errors)} çağrı · ${fmtNum(t.trace_errors)} trace hatası`}
        />
        <StatTile label="Maliyet" loading={!d} value={fmtUsd(t?.cost_usd)} sub="LangSmith fiyat tahmini" />
        <StatTile label="Token (girdi / çıktı)" loading={!d} value={t ? `${fmtTok(t.input_tokens)} / ${fmtTok(t.output_tokens)}` : "—"} />
        <StatTile label="p95 LLM latency" loading={!d} missing={t?.p95_ms == null} value={fmtMs(t?.p95_ms)} sub={t && `p50 ${fmtMs(t.p50_ms)}`} />
        <StatTile label="p95 ilk token" loading={!d} missing={t?.p95_first_token_ms == null} value={fmtMs(t?.p95_first_token_ms)} sub="yalnız stream edilen cevaplar" />
      </MetricStrip>

      <div className="grid gap-3 xl:grid-cols-3">
        {(
          [
            ["LLM çağrısı", "calls", (v: number | null) => fmtNum(v), "çağrı / kova"],
            ["Maliyet", "cost", fmtUsd, "USD / kova"],
            ["p95 LLM latency", "p95", fmtMs, "ms"],
          ] as const
        ).map(([title, k, fmt, unit]) => (
          <Card key={k}>
            <CardHeader title={title} description={d ? `kova: ${d.bucket}` : undefined} />
            <CardBody>
              <TimeSeriesChart
                height={170}
                unit={unit}
                series={[{ id: k, label: title, color: CHART.single, points: points.map((p) => ({ t: p.t, v: p[k] })) }]}
                format={fmt}
                emptyText="Bu aralıkta LLM çağrısı yok"
              />
            </CardBody>
          </Card>
        ))}
      </div>

      <div className="grid gap-3 xl:grid-cols-2">
        <Card>
          <CardHeader title="Model bazında" />
          <CardBody className="p-0">
            {d ? <AggTable rows={d.by_model.map((r) => ({ ...r, key: r.model }))} keyLabel="Model" label={(k) => k} /> : <TableSkeleton rows={3} cols={6} />}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Çağrı türü bazında" />
          <CardBody className="p-0">
            {d ? <AggTable rows={d.by_call.map((r) => ({ ...r, key: r.name }))} keyLabel="Çağrı" label={(k) => CALL_LABEL[k] ?? k} /> : <TableSkeleton rows={4} cols={6} />}
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader title="Tetikleyici bazında" description="Trace başına: sohbet turu, health check, watch…" />
        <CardBody className="p-0">
          {!d ? (
            <TableSkeleton rows={3} cols={5} />
          ) : !d.by_trigger.length ? (
            <EmptyState title="Bu aralıkta trace yok" />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Tetikleyici</th>
                    <th className="num">Trace</th>
                    <th className="num">Hata</th>
                    <th className="num">p95 süre</th>
                    <th className="num">Maliyet</th>
                  </tr>
                </thead>
                <tbody>
                  {d.by_trigger.map((r) => (
                    <tr key={r.trigger}>
                      <td className="primary">{TRIGGER_LABEL[r.trigger] ?? r.trigger}</td>
                      <td className="num">{fmtNum(r.traces)}</td>
                      <td className={cn("num", r.errors ? "text-st-critical-ink" : undefined)}>{fmtNum(r.errors)}</td>
                      <td className="num">{fmtMs(r.p95_ms)}</td>
                      <td className="num text-ink">{fmtUsd(r.cost_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardBody className="pb-0">
          <Tabs
            value={tab}
            onValueChange={setTab}
            tabs={[
              { value: "cost", label: "En pahalı trace'ler" },
              { value: "slow", label: "En yavaş trace'ler" },
              { value: "errors", label: `Hatalı trace'ler${d ? ` (${d.errors.length})` : ""}` },
              { value: "llm_errors", label: `LLM hataları${d ? ` (${d.llm_errors.length})` : ""}` },
            ]}
          >
            <TabPanel value="cost">{d ? <TraceTable rows={d.top_cost} empty="Bu aralıkta trace yok" /> : <TableSkeleton />}</TabPanel>
            <TabPanel value="slow">{d ? <TraceTable rows={d.slowest} empty="Bu aralıkta trace yok" /> : <TableSkeleton />}</TabPanel>
            <TabPanel value="errors">{d ? <TraceTable rows={d.errors} empty="Bu aralıkta hatalı trace yok" /> : <TableSkeleton />}</TabPanel>
            <TabPanel value="llm_errors">{d ? <CallErrorTable rows={d.llm_errors} /> : <TableSkeleton />}</TabPanel>
          </Tabs>
        </CardBody>
      </Card>
    </Page>
  );
}
