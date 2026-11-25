import { AlertTriangle, ExternalLink } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { TimeSeriesChart } from "@/components/charts/TimeSeriesChart";
import { StatTile } from "@/components/StatTile";
import { buttonVariants, Card, CardBody, CardHeader } from "@/components/ui/primitives";
import { TabPanel, Tabs } from "@/components/ui/overlay";
import { EmptyState, ErrorState, PageHeader, TableSkeleton } from "@/components/ui/states";
import { useLlmObservability } from "@/hooks/queries";
import { CHART } from "@/lib/colors";
import { cn, fmtMs, fmtNum, fmtPct, fmtRelative, fmtTime, shortId } from "@/lib/utils";
import type { LlmAgg, LlmCallError, LlmObservability, LlmRange, LlmTrace } from "@/types/api";

const RANGES: { value: LlmRange; label: string }[] = [
  { value: "24h", label: "Son 24 saat" },
  { value: "7d", label: "Son 7 gün" },
  { value: "30d", label: "Son 30 gün" },
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
  analyze: "CLI analiz",
  ask: "CLI soru",
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

function RangeSwitch({ value, onChange }: { value: LlmRange; onChange: (v: LlmRange) => void }) {
  return (
    <div role="radiogroup" aria-label="zaman aralığı" className="inline-flex rounded-md border border-line-strong bg-surface-2 p-0.5">
      {RANGES.map((r) => (
        <button
          key={r.value}
          role="radio"
          aria-checked={value === r.value}
          onClick={() => onChange(r.value)}
          className={cn("rounded px-2.5 py-1 text-xs font-medium transition-colors", value === r.value ? "bg-surface-3 text-ink" : "text-muted hover:text-ink")}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}

function SyncStatus({ d }: { d: LlmObservability }) {
  const { last_ok_at, last_error, last_error_at } = d.sync;
  const failing = last_error && (!last_ok_at || (last_error_at ?? "") > last_ok_at);
  return (
    <div className="text-xs text-muted">
      {last_ok_at ? <>Son senkron {fmtRelative(last_ok_at)}</> : "Henüz senkron yapılmadı"}
      {failing && (
        <div role="alert" className="mt-1 inline-flex items-center gap-1.5 text-st-serious">
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
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted">
          <tr className="border-b border-line">
            <th className="px-3 py-2 font-medium">{keyLabel}</th>
            <th className="px-3 py-2 text-right font-medium">Çağrı</th>
            <th className="px-3 py-2 text-right font-medium">Hata</th>
            <th className="px-3 py-2 text-right font-medium">Token (girdi / çıktı)</th>
            <th className="px-3 py-2 text-right font-medium">p50 / p95</th>
            <th className="px-3 py-2 text-right font-medium">Maliyet</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="border-b border-line hover:bg-surface-2">
              <td className="px-3 py-2 text-ink">
                {label(r.key)}
                {label(r.key) !== r.key && <div className="font-mono text-[11px] text-muted">{r.key}</div>}
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-ink-2">{fmtNum(r.calls)}</td>
              <td className={cn("px-3 py-2 text-right tabular-nums", r.errors ? "text-st-critical" : "text-ink-2")}>{fmtNum(r.errors)}</td>
              <td className="px-3 py-2 text-right tabular-nums text-ink-2">
                {fmtTok(r.input_tokens)} / {fmtTok(r.output_tokens)}
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-ink-2">
                {fmtMs(r.p50_ms)} / {fmtMs(r.p95_ms)}
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-ink">{fmtUsd(r.cost_usd)}</td>
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
          araştırma {shortId(investigationId)}
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
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted">
          <tr className="border-b border-line">
            <th className="px-3 py-2 font-medium">Zaman</th>
            <th className="px-3 py-2 font-medium">Trace</th>
            <th className="px-3 py-2 font-medium">Model</th>
            <th className="px-3 py-2 text-right font-medium">Süre</th>
            <th className="px-3 py-2 text-right font-medium">LLM çağrısı</th>
            <th className="px-3 py-2 text-right font-medium">Token</th>
            <th className="px-3 py-2 text-right font-medium">Maliyet</th>
            <th className="px-3 py-2" />
          </tr>
        </thead>
        <tbody>
          {rows.map((t) => (
            <tr key={t.id} className={cn("border-b border-line align-top hover:bg-surface-2", t.status === "error" && "bg-st-critical/5")}>
              <td className="whitespace-nowrap px-3 py-2 text-xs text-ink-2">{fmtTime(t.start_time)}</td>
              <td className="px-3 py-2">
                <div className="text-ink">
                  {ROOT_LABEL[t.name] ?? CALL_LABEL[t.name] ?? t.name}
                  {t.trigger && <span className="ml-1.5 text-xs text-muted">· {TRIGGER_LABEL[t.trigger] ?? t.trigger}</span>}
                </div>
                {t.status === "error" && <div className="mt-0.5 max-w-md truncate text-xs text-st-critical" title={t.error ?? ""}>hata: {t.error ?? "—"}</div>}
                {t.status === "pending" && <div className="mt-0.5 text-xs text-muted">sürüyor</div>}
              </td>
              <td className="px-3 py-2 font-mono text-[11px] text-ink-2">{t.model ?? "—"}</td>
              <td className="px-3 py-2 text-right tabular-nums text-ink-2">{fmtMs(t.latency_ms)}</td>
              <td className="px-3 py-2 text-right tabular-nums text-ink-2">{fmtNum(t.llm_calls)}</td>
              <td className="px-3 py-2 text-right tabular-nums text-ink-2">{fmtTok(t.tokens)}</td>
              <td className="px-3 py-2 text-right tabular-nums text-ink">{fmtUsd(t.cost_usd)}</td>
              <td className="px-3 py-2">
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
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted">
          <tr className="border-b border-line">
            <th className="px-3 py-2 font-medium">Zaman</th>
            <th className="px-3 py-2 font-medium">Çağrı</th>
            <th className="px-3 py-2 font-medium">Model</th>
            <th className="px-3 py-2 font-medium">Hata</th>
            <th className="px-3 py-2 text-right font-medium">Süre</th>
            <th className="px-3 py-2" />
          </tr>
        </thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.id} className="border-b border-line align-top hover:bg-surface-2">
              <td className="whitespace-nowrap px-3 py-2 text-xs text-ink-2">{fmtTime(e.start_time)}</td>
              <td className="px-3 py-2 text-ink">{CALL_LABEL[e.name] ?? e.name}</td>
              <td className="px-3 py-2 font-mono text-[11px] text-ink-2">{e.model ?? "—"}</td>
              <td className="max-w-md px-3 py-2 text-xs text-st-critical">{e.error ?? "—"}</td>
              <td className="px-3 py-2 text-right tabular-nums text-ink-2">{fmtMs(e.latency_ms)}</td>
              <td className="px-3 py-2">
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
    <div className="mx-auto max-w-[1500px] space-y-4 p-4 lg:p-6">
      <PageHeader
        title="LLM Monitoring"
        description="LangSmith trace'lerinden periyodik senkronlanan özet: çağrı, token, maliyet, latency ve hatalar. Prompt/cevap içeriği platformda tutulmaz."
        actions={<RangeSwitch value={range} onChange={setRange} />}
      />
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {d && <SyncStatus d={d} />}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
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
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        {(
          [
            ["LLM çağrısı", "calls", (v: number | null) => fmtNum(v)],
            ["Maliyet (USD)", "cost", fmtUsd],
            ["p95 LLM latency", "p95", fmtMs],
          ] as const
        ).map(([title, k, fmt]) => (
          <Card key={k}>
            <CardHeader title={title} />
            <CardBody>
              <TimeSeriesChart
                height={180}
                series={[{ id: k, label: title, color: CHART.single, points: points.map((p) => ({ t: p.t, v: p[k] })) }]}
                format={fmt}
                emptyText="Bu aralıkta LLM çağrısı yok"
              />
            </CardBody>
          </Card>
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
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
        <CardHeader title="Tetikleyici bazında" description="Trace başına: sohbet turu, periyodik kontrol, watch…" />
        <CardBody className="p-0">
          {!d ? (
            <TableSkeleton rows={3} cols={5} />
          ) : !d.by_trigger.length ? (
            <EmptyState title="Bu aralıkta trace yok" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-muted">
                  <tr className="border-b border-line">
                    <th className="px-3 py-2 font-medium">Tetikleyici</th>
                    <th className="px-3 py-2 text-right font-medium">Trace</th>
                    <th className="px-3 py-2 text-right font-medium">Hata</th>
                    <th className="px-3 py-2 text-right font-medium">p95 süre</th>
                    <th className="px-3 py-2 text-right font-medium">Maliyet</th>
                  </tr>
                </thead>
                <tbody>
                  {d.by_trigger.map((r) => (
                    <tr key={r.trigger} className="border-b border-line hover:bg-surface-2">
                      <td className="px-3 py-2 text-ink">{TRIGGER_LABEL[r.trigger] ?? r.trigger}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-ink-2">{fmtNum(r.traces)}</td>
                      <td className={cn("px-3 py-2 text-right tabular-nums", r.errors ? "text-st-critical" : "text-ink-2")}>{fmtNum(r.errors)}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-ink-2">{fmtMs(r.p95_ms)}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-ink">{fmtUsd(r.cost_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardBody>
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
    </div>
  );
}
