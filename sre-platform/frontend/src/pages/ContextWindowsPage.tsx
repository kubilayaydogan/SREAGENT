import { ConceptHelp } from "@/components/ConceptHelp";
import { AlertTriangle, CheckCircle2, CircleSlash, ExternalLink, RotateCcw, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { RangeTabs } from "@/components/filters";
import { MetricStrip, StatTile } from "@/components/StatTile";
import { FilterBar, TableWrap, useUrlFilters } from "@/components/table";
import { Dialog } from "@/components/ui/overlay";
import { Badge, Card, Input, Mono, Segmented, Select, Skeleton } from "@/components/ui/primitives";
import { EmptyState, ErrorState, Notice, Page, PageHeader, Pagination, TableSkeleton } from "@/components/ui/states";
import {
  type CwCall,
  type CwCallDetail,
  type CwComponent,
  type CwDecision,
  type CwRange,
  type CwStatus,
  useCwCall,
  useCwCalls,
  useCwFacets,
  useCwOverview,
} from "@/features/contextwindows/api";
import { cn, fmtMs, fmtNum, fmtPct, fmtTime, shortId } from "@/lib/utils";

const RANGE_OPTS: { value: CwRange; label: string; title: string }[] = [
  { value: "1h", label: "1h", title: "Son 1 saat" },
  { value: "24h", label: "24h", title: "Son 24 saat" },
  { value: "7d", label: "7d", title: "Son 7 gün" },
  { value: "30d", label: "30d", title: "Son 30 gün" },
];
const FILTER_KEYS = ["run_id", "node", "model", "status"];
const PAGE_SIZE = 50;
const UNKNOWN = "Bilinmiyor";

export const STATUS_TR: Record<CwStatus, string> = {
  ok: "başarılı",
  error: "hata",
  context_limit: "context limit",
  retried: "retry edildi",
  cancelled: "iptal edildi",
};
const STATUS_ICON: Record<CwStatus, ReactNode> = {
  ok: <CheckCircle2 className="size-3.5 text-st-good" />,
  error: <XCircle className="size-3.5 text-st-critical" />,
  context_limit: <AlertTriangle className="size-3.5 text-st-critical" />,
  retried: <RotateCcw className="size-3.5 text-st-warning" />,
  cancelled: <CircleSlash className="size-3.5 text-st-warning" />,
};

export function StatusLabel({ status }: { status: CwStatus }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs whitespace-nowrap">
      {STATUS_ICON[status]}
      <span className={status === "error" || status === "context_limit" ? "text-st-critical-ink" : "text-ink-2"}>{STATUS_TR[status] ?? status}</span>
    </span>
  );
}

/** Değerin kaynağı: sağlayıcı ölçümü, yerel tahmin ya da bilinmiyor. */
type Src = "provider" | "estimate" | "config" | "unknown";
const SRC_TR: Record<Src, string> = { provider: "sağlayıcı ölçümü", estimate: "tahmini", config: "platform ayarı", unknown: "bilinmiyor" };
function SrcBadge({ src }: { src: Src }) {
  return <Badge className={cn("text-2xs", src === "unknown" && "text-muted", src === "estimate" && "italic")}>{SRC_TR[src]}</Badge>;
}

function tokens(v: number | null | undefined) {
  return v === null || v === undefined ? <span className="text-muted">{UNKNOWN}</span> : fmtNum(v);
}

export function FillCell({ value, capacity }: { value: number | null; capacity: number | null }) {
  if (value === null || value === undefined)
    return (
      <span className="text-xs text-muted" title={capacity ? "Sağlayıcı usage bildirmedi" : "Model kapasitesi bilinmiyor; yüzde hesaplanmadı"}>
        {UNKNOWN}
      </span>
    );
  const pct = Math.min(value, 1);
  return (
    <span className="inline-flex items-center gap-2 tabular-nums">
      <span className="relative h-1.5 w-16 overflow-hidden rounded-full bg-surface-3" aria-hidden>
        <span className={cn("absolute inset-y-0 left-0 rounded-full", value >= 0.9 ? "bg-st-critical" : value >= 0.7 ? "bg-st-warning" : "bg-accent")} style={{ width: `${Math.max(pct * 100, 1)}%` }} />
      </span>
      <span className="text-xs">{fmtPct(value, value < 0.01 ? 2 : 1)}</span>
    </span>
  );
}

export default function ContextWindowsPage() {
  const { get: g, set, clear } = useUrlFilters();
  const range = (g("range") as CwRange) || "24h";
  const sort = (g("sort") as "time" | "fill") || "time";
  const page = Number(g("page") || 1);
  const ov = useCwOverview(range);
  const facets = useCwFacets(range);
  const calls = useCwCalls({
    range,
    run_id: g("run_id") || undefined,
    node: g("node") || undefined,
    model: g("model") || undefined,
    status: g("status") || undefined,
    sort,
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
  });
  const active = FILTER_KEYS.filter((k) => g(k)).length;
  const d = ov.data;
  const selected = g("call") || null;

  return (
    <Page>
      <PageHeader
        title="Context Windows"
        description="Her LLM çağrısında modelin okuma alanının ne kadarının dolduğu ve neyle dolduğu"
        meta={<ConceptHelp k="context" />}
        actions={<RangeTabs value={range} onChange={(v) => set("range", v)} options={RANGE_OPTS} />}
      />
      <MetricStrip className="mb-4 grid-cols-2 md:grid-cols-5">
        <StatTile loading={ov.isLoading} label="Toplam çağrı" value={fmtNum(d?.calls)} sub={d ? `${fmtNum(d.attempts)} HTTP denemesi` : undefined} />
        <StatTile
          loading={ov.isLoading}
          label="En yüksek ölçülen doluluk"
          value={d?.max_actual_fill == null ? UNKNOWN : fmtPct(d.max_actual_fill, 2)}
          missing={d?.max_actual_fill == null}
          sub={d?.max_call ? `${d.max_call.node ?? "—"} · ${d.max_call.model ?? "—"}` : d && d.capacity_unknown ? `${fmtNum(d.capacity_unknown)} çağrıda kapasite bilinmiyor` : undefined}
        />
        <StatTile loading={ov.isLoading} label="Context limit hatası" value={fmtNum(d?.context_limit_errors)} tone={d?.context_limit_errors ? "text-st-critical-ink" : undefined} />
        <StatTile loading={ov.isLoading} label="Retry edilen deneme" value={fmtNum(d?.retried_attempts)} sub={d ? `${fmtNum(d.errors)} hata` : undefined} />
        <StatTile loading={ov.isLoading} label="Usage bildirilmeyen" value={fmtNum(d?.usage_missing)} sub="sağlayıcı token sayısı yok" />
      </MetricStrip>
      {ov.error && <ErrorState error={ov.error} onRetry={() => ov.refetch()} />}
      <Card>
        <FilterBar
          active={active}
          onClear={() => clear(FILTER_KEYS)}
          right={
            <div className="flex items-center gap-3">
              <Segmented
                label="sıralama"
                value={sort}
                onChange={(v) => set("sort", v === "time" ? "" : v)}
                options={[
                  { value: "time", label: "Zaman" },
                  { value: "fill", label: "En yüksek doluluk" },
                ]}
              />
              {calls.data && <span className="text-xs text-muted tabular-nums">{calls.data.total} kayıt</span>}
            </div>
          }
        >
          <Input aria-label="run_id" placeholder="run_id" value={g("run_id")} onChange={(e) => set("run_id", e.target.value.trim())} className="w-56 font-mono" />
          <Select aria-label="node" value={g("node")} onChange={(e) => set("node", e.target.value)} className="w-36">
            <option value="">Tüm node'lar</option>
            {facets.data?.nodes.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </Select>
          <Select aria-label="model" value={g("model")} onChange={(e) => set("model", e.target.value)} className="w-44">
            <option value="">Tüm modeller</option>
            {facets.data?.models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </Select>
          <Select aria-label="durum" value={g("status")} onChange={(e) => set("status", e.target.value)} className="w-36">
            <option value="">Tüm durumlar</option>
            {(Object.keys(STATUS_TR) as CwStatus[]).map((s) => (
              <option key={s} value={s}>
                {STATUS_TR[s]}
              </option>
            ))}
          </Select>
        </FilterBar>
        {calls.isLoading ? (
          <TableSkeleton cols={9} />
        ) : calls.error ? (
          <ErrorState error={calls.error} onRetry={() => calls.refetch()} />
        ) : !calls.data?.items.length ? (
          <EmptyState
            title="LLM çağrısı bulunamadı"
            description={active ? "Filtreleri değiştirmeyi ya da temizlemeyi deneyin." : "Bu aralıkta context window kaydı yok. Kayıt bu özellik devreye alındıktan sonraki çağrılar için tutulur; eski run'lar listelenmez."}
          />
        ) : (
          <>
            <TableWrap fetching={calls.isFetching}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th className="num">Zaman</th>
                    <th>Run</th>
                    <th>Node</th>
                    <th className="num">Sıra</th>
                    <th>Model</th>
                    <th className="num">Girdi token</th>
                    <th className="num">Çıktı token</th>
                    <th>Context doluluğu</th>
                    <th>Durum</th>
                  </tr>
                </thead>
                <tbody>
                  {calls.data.items.map((c) => (
                    <tr key={c.call_id} className={cn("cursor-pointer", selected === c.call_id && "bg-surface-2")} onClick={() => set("call", c.call_id)} aria-label={`çağrı ${c.call_id}`}>
                      <td className="num whitespace-nowrap">{fmtTime(c.started_at)}</td>
                      <td>
                        <Mono className="text-2xs">{c.run_id ? shortId(c.run_id) : "run dışı"}</Mono>
                      </td>
                      <td className="text-xs">{c.node ?? "—"}</td>
                      <td className="num text-xs">
                        {c.call_seq ?? "—"}
                        {c.attempt_count > 1 && <span className="text-muted"> · deneme {c.attempt_no + 1}/{c.attempt_count}</span>}
                      </td>
                      <td className="text-xs">
                        {c.model ?? "—"}
                        {c.streaming && <span className="ml-1 text-2xs text-muted">stream</span>}
                      </td>
                      <td className="num">{tokens(c.input_tokens)}</td>
                      <td className="num">{tokens(c.output_tokens)}</td>
                      <td>
                        <FillCell value={c.actual_fill} capacity={c.capacity} />
                      </td>
                      <td>
                        <StatusLabel status={c.status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
            <Pagination page={page} pages={Math.ceil(calls.data.total / PAGE_SIZE)} total={calls.data.total} onPage={(p) => set("page", String(p))} />
          </>
        )}
      </Card>
      <CallDialog id={selected} onClose={() => set("call", "")} onSelect={(id) => set("call", id)} />
    </Page>
  );
}

// ---------------------------------------------------------------- detay paneli
function Row({ label, value, src, sub }: { label: string; value: ReactNode; src?: Src; sub?: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-line py-1.5 last:border-0">
      <div className="min-w-0">
        <div className="text-xs text-ink-2">{label}</div>
        {sub && <div className="text-2xs text-muted">{sub}</div>}
      </div>
      <div className="flex shrink-0 items-center gap-2 text-right text-[13px] tabular-nums">
        {value}
        {src && <SrcBadge src={src} />}
      </div>
    </div>
  );
}

function CallDialog({ id, onClose, onSelect }: { id: string | null; onClose: () => void; onSelect: (id: string) => void }) {
  const q = useCwCall(id);
  const d = q.data;
  return (
    <Dialog open={!!id} onOpenChange={(v) => !v && onClose()} title="LLM çağrısı — context window" description={d ? `${d.node ?? "—"} · ${d.model ?? "—"}` : undefined} wide>
      {q.isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-24" />
          <Skeleton className="h-40" />
        </div>
      ) : q.error ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : d ? (
        <CallDetail d={d} onSelect={onSelect} />
      ) : null}
    </Dialog>
  );
}

function CallDetail({ d, onSelect }: { d: CwCallDetail; onSelect: (id: string) => void }) {
  const capKnown = d.capacity !== null;
  const usageKnown = d.usage_source === "provider";
  return (
    <div className="space-y-4 text-[13px]">
      {d.status === "context_limit" && (
        <Notice tone="critical">
          Context limit: {d.error_code === "model_context_window_exceeded" ? "üretim context window sınırına ulaştı (stop_reason model_context_window_exceeded)." : "girdi modelin context window'unu aşıyor (prompt is too long)."}
          {d.error_message && <span className="block font-mono text-2xs">{d.error_message}</span>}
        </Notice>
      )}
      {d.status === "error" && <Notice tone="warning">Çağrı hata ile bitti: {d.error_code ?? "—"}{d.http_status ? ` (HTTP ${d.http_status})` : ""}</Notice>}
      {d.status === "retried" && <Notice tone="warning">Bu deneme retry edildi ({d.error_code ?? "—"}{d.http_status ? `, HTTP ${d.http_status}` : ""}). Sağlayıcı bu deneme için usage bildirmez.</Notice>}

      <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs md:grid-cols-4">
        {(
          [
            ["Run", d.run_id ? <Mono>{d.run_id}</Mono> : "run dışı"],
            ["Node / görev", `${d.node ?? "—"} / ${d.task ?? "—"}`],
            ["Çağrı sırası", d.call_seq ?? "—"],
            ["Deneme", `${d.attempt_no + 1} / ${d.attempt_count}`],
            ["Sağlayıcı / model", `${d.provider} / ${d.model ?? "—"}${d.served_model ? ` → ${d.served_model}` : ""}`],
            ["Başlangıç", fmtTime(d.started_at)],
            ["Süre", fmtMs(d.duration_ms)],
            ["Durum", <StatusLabel status={d.status} />],
            ["Streaming", d.streaming ? `evet · ${fmtNum(d.stream?.chunks)} chunk${d.stream?.completed ? " · tamamlandı" : d.stream?.interrupted ? " · kesildi" : ""}` : "hayır"],
            ["stop_reason", d.stop_reason ?? "—"],
            ["HTTP", d.http_status ?? "—"],
            ["call_id", <Mono className="text-2xs">{d.call_id}</Mono>],
          ] as [string, ReactNode][]
        ).map(([k, v]) => (
          <div key={k} className="min-w-0">
            <dt className="text-muted">{k}</dt>
            <dd className="truncate">{v}</dd>
          </div>
        ))}
      </dl>

      <div className="grid gap-4 md:grid-cols-2">
        <section aria-label="Planlanan bütçe">
          <h3 className="mb-1 text-xs font-semibold text-ink">Planlanan bütçe (çağrı öncesi)</h3>
          <Row label="Context window kapasitesi" sub={d.capacity_note ?? d.capacity_source ?? undefined} value={tokens(d.capacity)} src={capKnown ? "provider" : "unknown"} />
          <Row label="Tahmini girdi" sub={d.estimate_method ?? "payload yakalanmadı"} value={tokens(d.estimated_input)} src={d.estimated_input !== null ? "estimate" : "unknown"} />
          <Row label="Ayrılan çıktı bütçesi" sub="platformun gönderdiği max_tokens" value={tokens(d.reserved_output)} src={d.reserved_output !== null ? "config" : "unknown"} />
          <Row label="Modelin çıktı sınırı" sub="Models API max_tokens" value={tokens(d.model_max_output)} src={d.model_max_output !== null ? "provider" : "unknown"} />
          <Row label="Planlanan doluluk" sub="(tahmini girdi + ayrılan çıktı) / kapasite" value={<FillCell value={d.planned_fill} capacity={d.capacity} />} src={d.planned_fill !== null ? "estimate" : "unknown"} />
        </section>
        <section aria-label="Gerçekleşen kullanım">
          <h3 className="mb-1 text-xs font-semibold text-ink">Gerçekleşen kullanım (çağrı sonrası)</h3>
          <Row label="Girdi token" sub="cache okuma ve cache yazma dahil toplam" value={tokens(d.input_tokens)} src={usageKnown ? "provider" : "unknown"} />
          <Row label="↳ cache okuma" sub="girdinin alt kalemi" value={tokens(d.cache_read_tokens)} src={d.cache_read_tokens !== null ? "provider" : "unknown"} />
          <Row label="↳ cache yazma" sub="girdinin alt kalemi" value={tokens(d.cache_creation_tokens)} src={d.cache_creation_tokens !== null ? "provider" : "unknown"} />
          <Row label="Çıktı token" sub="thinking dahil" value={tokens(d.output_tokens)} src={usageKnown ? "provider" : "unknown"} />
          <Row label="↳ thinking (reasoning)" sub="çıktının alt kalemi; ayrı bildirilmediyse bilinmiyor" value={tokens(d.reasoning_tokens)} src={d.reasoning_tokens !== null ? "provider" : "unknown"} />
          <Row label="Gerçekleşen doluluk" sub="(girdi + çıktı) / kapasite" value={<FillCell value={d.actual_fill} capacity={d.capacity} />} src={d.actual_fill !== null ? "provider" : "unknown"} />
        </section>
      </div>

      <Distribution d={d} />
      <Decisions items={d.context_decisions} />

      {d.attempt_count > 1 && (
        <section aria-label="Denemeler">
          <h3 className="mb-1 text-xs font-semibold text-ink">Aynı çağrının HTTP denemeleri (SDK retry)</h3>
          <ul className="divide-y divide-line rounded-md border border-line">
            {d.attempts.map((a: CwCall) => (
              <li key={a.call_id}>
                <button className={cn("flex w-full items-center justify-between gap-3 px-3 py-1.5 text-left text-xs hover:bg-surface-2", a.call_id === d.call_id && "bg-surface-2")} onClick={() => onSelect(a.call_id)}>
                  <span>deneme {a.attempt_no + 1}</span>
                  <StatusLabel status={a.status} />
                  <span className="text-muted">{a.error_code ?? ""}</span>
                  <span className="tabular-nums text-muted">{fmtMs(a.duration_ms)}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="flex flex-wrap gap-3 border-t border-line pt-3 text-xs">
        {d.links.agent_trace && (
          <Link className="inline-flex items-center gap-1 text-accent hover:underline" to={d.links.agent_trace}>
            <ExternalLink className="size-3.5" /> Agent Trace ({d.links.agent_trace_level === "call" ? "bu çağrı" : "run"})
          </Link>
        )}
        {d.links.middleware_timeline && (
          <Link className="inline-flex items-center gap-1 text-accent hover:underline" to={d.links.middleware_timeline}>
            <ExternalLink className="size-3.5" /> Middleware Run Timeline
          </Link>
        )}
        {d.links.llm_monitoring && (
          <Link className="inline-flex items-center gap-1 text-accent hover:underline" to={d.links.llm_monitoring}>
            <ExternalLink className="size-3.5" /> LLM Monitoring
          </Link>
        )}
      </div>
    </div>
  );
}

const METHOD_SRC: Record<CwComponent["method"], Src> = { estimate: "estimate", provider_minus_estimate: "provider", unknown: "unknown" };
const METHOD_NOTE: Record<CwComponent["method"], string> = {
  estimate: "yerel tahmin",
  provider_minus_estimate: "sağlayıcı toplamı − tahminler",
  unknown: "sağlayıcı toplamı yok",
};

function Distribution({ d }: { d: CwCallDetail }) {
  const comps = d.components;
  if (!comps?.length)
    return (
      <section aria-label="Girdi dağılımı">
        <h3 className="mb-1 text-xs font-semibold text-ink">Girdi dağılımı</h3>
        <p className="text-xs text-muted">Ölçülmedi: bu deneme için sağlayıcıya giden payload yakalanmadı (ör. retry edilen deneme ya da eski kayıt).</p>
      </section>
    );
  const max = Math.max(1, ...comps.map((c) => c.tokens ?? 0));
  return (
    <section aria-label="Girdi dağılımı">
      <h3 className="mb-1 text-xs font-semibold text-ink">Girdi dağılımı</h3>
      <p className="mb-2 text-2xs text-muted">
        Yalnız modele gerçekten gönderilen içerik sayılır. Bileşenler tahminidir ({d.estimate_method ?? "—"}); sağlayıcının girdi toplamıyla fark "Diğer / sağlayıcı ek yükü" satırındadır.
      </p>
      {d.estimate_over ? <Notice tone="info">Bileşen tahminleri sağlayıcının girdi toplamını {fmtNum(d.estimate_over)} token aşıyor; "Diğer" 0 gösterilir.</Notice> : null}
      <ul className="mt-2 space-y-1.5" aria-label="bileşenler">
        {comps.map((c) => (
          <li key={c.key} className="grid grid-cols-[11rem_1fr_auto] items-center gap-3">
            <span className="truncate text-xs text-ink-2" title={c.refs.length ? c.refs.join(", ") : undefined}>
              {c.label}
            </span>
            <span className="relative h-3 overflow-hidden rounded-sm bg-surface-2" aria-hidden>
              {c.tokens !== null && (
                <span className={cn("absolute inset-y-0 left-0 rounded-sm", c.key === "other" ? "bg-line-strong" : "bg-accent")} style={{ width: `${Math.max((c.tokens / max) * 100, c.tokens ? 1 : 0)}%` }} />
              )}
            </span>
            <span className="flex items-center gap-2 text-xs tabular-nums">
              {tokens(c.tokens)}
              <span className="text-2xs text-muted">{METHOD_NOTE[c.method]}</span>
              <SrcBadge src={METHOD_SRC[c.method]} />
            </span>
            {c.refs.length > 0 && (
              <span className="col-span-3 -mt-1 truncate pl-1 font-mono text-2xs text-muted" title={c.refs.join(", ")}>
                {c.refs.slice(0, 8).join(" · ")}
                {c.refs.length > 8 ? ` +${c.refs.length - 8}` : ""}
              </span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function refText(r: string | Record<string, string | null>) {
  if (typeof r === "string") return r;
  return [r.ref ?? r.id ?? r.kind, r.count ? `(${r.count})` : null, r.chars ? `(${r.chars} karakter)` : null, r.reason ? `— ${r.reason}` : null].filter(Boolean).join(" ");
}

function Decisions({ items }: { items: CwDecision[] | null }) {
  return (
    <section aria-label="Context kararları">
      <h3 className="mb-1 text-xs font-semibold text-ink">Dahil edilen / özetlenen / çıkarılan içerik</h3>
      {!items?.length ? (
        <p className="text-xs text-muted">Ölçülmedi: bu çağrı için context hazırlama akışı karar kaydı üretmedi.</p>
      ) : (
        <ul className="space-y-2">
          {items.map((it, i) => (
            <li key={i} className="rounded-md border border-line px-3 py-2 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <Mono>{it.section}</Mono>
                {it.status && <Badge className="text-2xs">{it.status}</Badge>}
                {it.token_estimate !== null && it.token_estimate !== undefined && <span className="text-2xs text-muted">~{fmtNum(it.token_estimate)} token (tahmini)</span>}
                {it.reason && <span className="text-muted">{it.reason}</span>}
              </div>
              {(
                [
                  ["Dahil edildi", it.included],
                  ["Özetlendi", it.summarized],
                  ["Çıkarıldı", it.dropped],
                ] as [string, CwDecision["included"]][]
              )
                .filter(([, v]) => v?.length)
                .map(([k, v]) => (
                  <div key={k} className="mt-1">
                    <span className="text-muted">{k}: </span>
                    <span className="font-mono text-2xs">{v.slice(0, 20).map(refText).join(" · ")}</span>
                  </div>
                ))}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
