/* Agent Trace: bir agent çalıştırmasının LangGraph node / state / stream, LLM ve Pydantic olayları.
   Liste → /agent-trace ; detay → /agent-trace/:key (?ev=<event_id>&f=<kategori>). Payload'lar sunucuda maskelenmiştir. */
import { ArrowRight, ExternalLink, Radio } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { JsonView } from "@/components/JsonView";
import { FilterBar, TableWrap, useUrlFilters } from "@/components/table";
import { Tabs, TabPanel } from "@/components/ui/overlay";
import { Badge, buttonVariants, Card, Input, Mono, Segmented, Select, Skeleton } from "@/components/ui/primitives";
import { EmptyState, ErrorState, Notice, Page, PageHeader, Pagination, TableSkeleton } from "@/components/ui/states";
import { useAgentTrace, useAgentTraces, useNodeExec, useTraceCall, useTraceEvent } from "@/hooks/queries";
import { streamRun } from "@/services/sse";
import { cn, fmtDuration, fmtTime } from "@/lib/utils";
import type { NodeExec, Snapshot, TraceCapture, TraceDetail, TraceEventFull, TraceEventMeta, TraceEventType, TraceRun } from "@/types/api";

// ---------------------------------------------------------------- sözlük
export const EVENT_TR: Record<TraceEventType, string> = {
  run_start: "Run başladı",
  run_end: "Run bitti",
  node_start: "Node başladı · State In",
  node_end: "Node Update",
  state_out: "State Out",
  transition: "Geçiş",
  routing: "Routing kararı",
  stream: "Stream olayı",
  llm_request: "LLM Request",
  llm_provider_request: "Provider Request",
  llm_response: "LLM Response",
  parser_input: "Parser Input",
  parser_output: "Validated Output",
  validation_error: "Validation Error",
  fallback: "Fallback",
};
type Cat = "all" | "node" | "llm" | "pydantic" | "stream" | "errors";
const CATS: Record<Exclude<Cat, "all" | "errors">, TraceEventType[]> = {
  node: ["node_start", "node_end", "state_out", "transition", "routing", "run_start", "run_end", "fallback"],
  llm: ["llm_request", "llm_provider_request", "llm_response"],
  pydantic: ["parser_input", "parser_output", "validation_error"],
  stream: ["stream"],
};
const isError = (e: TraceEventMeta) => e.status === "error" || e.event_type === "validation_error" || !!e.summary?.capture_failed;
const inCat = (e: TraceEventMeta, c: Cat) => (c === "all" ? true : c === "errors" ? isError(e) : CATS[c].includes(e.event_type));

export const CAPTURE_TR: Record<TraceCapture, string> = {
  complete: "Tam kayıt",
  truncated: "Kırpılmış payload var",
  partial: "Kısmi kayıt",
  failed: "Kayıt başarısız",
  none: "Kaydedilmemiş",
};
const STATUS_TR: Record<string, string> = {
  running: "çalışıyor",
  completed: "tamamlandı",
  failed: "başarısız",
  interrupted: "yarıda kaldı",
  partial: "kısmi",
  cancelled: "iptal edildi",
  queued: "kuyrukta",
};
const KIND_TR = { analysis: "Analiz", chat_turn: "Sohbet turu" } as const;
const NOT_RECORDED = "Bu çalıştırma için trace verisi kaydedilmemiş";
const UNOBSERVED = "Gözlemlenemedi";

function StatusDot({ status }: { status: string }) {
  const tone =
    status === "completed" ? "bg-st-good" : status === "running" ? "bg-st-warning animate-pulse" : status === "interrupted" || status === "partial" ? "bg-st-warning" : "bg-st-critical";
  return (
    <span className="inline-flex items-center gap-1.5 text-xs whitespace-nowrap">
      <span aria-hidden className={cn("size-1.5 rounded-full", tone)} />
      {STATUS_TR[status] ?? status}
    </span>
  );
}

// ---------------------------------------------------------------- liste
const FILTER_KEYS = ["kind", "status", "q", "recorded"];

export default function AgentTracePage() {
  const { key } = useParams();
  return key ? <TraceDetailView traceKey={key} /> : <TraceList />;
}

function TraceList() {
  const { get: g, set, clear } = useUrlFilters();
  const page = Number(g("page") || 1);
  const params = { kind: g("kind") || undefined, status: g("status") || undefined, q: g("q") || undefined, recorded: g("recorded") || undefined, page, size: 50 };
  const { data, isLoading, error, refetch, isFetching } = useAgentTraces(params);
  const active = FILTER_KEYS.filter((k) => g(k)).length;
  return (
    <Page>
      <PageHeader title="Agent Trace" description="Agent çalıştırmalarının node, state, stream, LLM ve Pydantic olayları (maskeli kopya)" />
      <Card>
        <FilterBar active={active} onClear={() => clear(FILTER_KEYS)} right={data && <span className="text-xs text-muted tabular-nums">{data.total} çalıştırma</span>}>
          <Input aria-label="run_id ara" placeholder="run_id" value={g("q")} onChange={(e) => set("q", e.target.value.trim())} className="w-56 font-mono" />
          <Select aria-label="tür" value={g("kind")} onChange={(e) => set("kind", e.target.value)} className="w-36">
            <option value="">Tüm türler</option>
            <option value="analysis">Analiz</option>
            <option value="chat_turn">Sohbet turu</option>
          </Select>
          <Select aria-label="durum" value={g("status")} onChange={(e) => set("status", e.target.value)} className="w-36">
            <option value="">Tüm durumlar</option>
            {["running", "completed", "failed", "interrupted"].map((s) => (
              <option key={s} value={s}>
                {STATUS_TR[s]}
              </option>
            ))}
          </Select>
          <Select aria-label="kayıt" value={g("recorded")} onChange={(e) => set("recorded", e.target.value)} className="w-40">
            <option value="">Kayıtlı + kayıtsız</option>
            <option value="true">Trace kayıtlı</option>
            <option value="false">Kaydedilmemiş</option>
          </Select>
        </FilterBar>
        {isLoading ? (
          <TableSkeleton cols={6} />
        ) : error ? (
          <ErrorState error={error} onRetry={() => refetch()} />
        ) : !data?.items.length ? (
          <EmptyState title="Çalıştırma bulunamadı" description={active ? "Filtreleri değiştirmeyi ya da temizlemeyi deneyin." : "Henüz agent çalıştırması yok."} />
        ) : (
          <>
            <TableWrap fetching={isFetching}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Run ID</th>
                    <th>Tetikleyici</th>
                    <th className="num">Başlangıç</th>
                    <th>Durum</th>
                    <th className="num">Süre</th>
                    <th>Trace</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((r) => (
                    <tr key={r.run_id}>
                      <td>
                        <Link to={`/agent-trace/${encodeURIComponent(r.run_id)}`} className="font-mono text-xs text-accent-text hover:underline">
                          {r.run_id}
                        </Link>
                        <div className="text-2xs text-muted">
                          {KIND_TR[r.kind]}
                          {r.parent_run_id && " · alt çalıştırma"}
                        </div>
                      </td>
                      <td className="text-xs">{r.trigger ?? "—"}</td>
                      <td className="num text-xs text-muted">{fmtTime(r.started_at)}</td>
                      <td>
                        <StatusDot status={r.status} />
                      </td>
                      <td className="num text-xs">{fmtDuration(r.duration_ms)}</td>
                      <td className="text-xs">
                        {r.recorded ? (
                          <span className="tabular-nums">
                            {r.event_count} olay
                            {r.problems > 0 && <span className="ml-1.5 text-st-warning-ink">kısmi</span>}
                            {r.truncated_count > 0 && <span className="ml-1.5 text-muted">kırpılmış {r.truncated_count}</span>}
                          </span>
                        ) : (
                          <span className="text-muted">kaydedilmemiş</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
            <Pagination page={data.page} pages={data.pages} total={data.total} onPage={(p) => set("page", String(p))} />
          </>
        )}
      </Card>
    </Page>
  );
}

// ---------------------------------------------------------------- detay
function useLiveEvents(key: string, d: TraceDetail | undefined) {
  const qc = useQueryClient();
  const [live, setLive] = useState<TraceEventMeta[]>([]);
  const [state, setState] = useState<"idle" | "open" | "reconnecting" | "closed">("idle");
  const running = d?.run.recorded && d.run.status === "running";
  const startSeq = useRef(0);
  startSeq.current = d?.events.at(-1)?.sequence ?? 0;
  useEffect(() => {
    if (!running) return;
    const ac = new AbortController();
    streamRun(`/api/agent-traces/${encodeURIComponent(key)}/stream?after=${startSeq.current}`, {
      signal: ac.signal,
      onStatus: (s) => setState(s === "connecting" ? "reconnecting" : s),
      onEvent: (e) => {
        const ev = e as unknown as TraceEventMeta & { type: string };
        if (ev.type === "trace_event") setLive((xs) => [...xs, ev]);
        if (ev.type === "done") {
          void qc.invalidateQueries({ queryKey: ["agent-trace", key] });
          void qc.invalidateQueries({ queryKey: ["agent-trace-exec", key] });
          void qc.invalidateQueries({ queryKey: ["agent-trace-call", key] });
        }
      },
    }).catch(() => setState("closed"));
    return () => ac.abort();
  }, [key, running, qc]);
  const events = useMemo(() => {
    const m = new Map<string, TraceEventMeta>();
    for (const e of [...(d?.events ?? []), ...live]) m.set(e.event_id, e); // event_id ile tekilleştirme
    return [...m.values()].sort((a, b) => a.sequence - b.sequence);
  }, [d?.events, live]);
  return { events, liveState: running ? state : "idle" };
}

function TraceDetailView({ traceKey }: { traceKey: string }) {
  const { data, isLoading, error, refetch } = useAgentTrace(traceKey);
  const { events, liveState } = useLiveEvents(traceKey, data);
  const [sp, setSp] = useSearchParams();
  const cat = (sp.get("f") as Cat) || "all";
  const sel = sp.get("ev");
  const setParam = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v);
    else n.delete(k);
    setSp(n, { replace: true });
  };
  if (isLoading) return <Page><Skeleton className="h-64" /></Page>;
  if (error || !data) return <Page><ErrorState error={error} onRetry={() => refetch()} /></Page>;
  const run = data.run;
  // ?call=<call_id> (Context Windows bağlantısı): o LLM çağrısının istek olayı seçilir
  const callParam = sp.get("call");
  const selected =
    events.find((e) => e.event_id === sel) ??
    (callParam ? (events.find((e) => e.call_id === callParam && e.event_type === "llm_request") ?? null) : null);
  return (
    <Page wide>
      <PageHeader
        title="Agent Trace"
        crumbs={[{ label: "Agent Trace", to: "/agent-trace" }, { label: run.run_id }]}
        description={<Mono>{run.run_id}</Mono>}
        actions={
          <div className="flex gap-2">
            {run.investigation_id && (
              <Link to={`/investigations/${run.investigation_id}`} className={buttonVariants({ variant: "outline", size: "sm" })}>
                Investigation
              </Link>
            )}
            {data.langsmith_url && (
              <a href={data.langsmith_url} target="_blank" rel="noreferrer" className={buttonVariants({ variant: "outline", size: "sm" })}>
                <ExternalLink /> LangSmith
              </a>
            )}
          </div>
        }
      />
      <RunHeader d={data} liveState={liveState} />
      {!run.recorded ? (
        <Card className="mt-3">
          <EmptyState title={NOT_RECORDED} description="Agent Trace kaydı bu çalıştırmadan sonra devreye girdi; node, state ve LLM olayları saklanmadı." />
        </Card>
      ) : (
        <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(20rem,26rem)_1fr]">
          <Card className="min-w-0">
            <Timeline events={events} cat={cat} onCat={(c) => setParam("f", c === "all" ? "" : c)} selected={sel} onSelect={(id) => setParam("ev", id)} />
          </Card>
          <Card className="min-w-0 p-3">
            {selected ? (
              <EventPanel key={selected.event_id} traceKey={traceKey} ev={selected} live={liveState !== "idle"} onSelect={(id) => setParam("ev", id)} events={events} />
            ) : (
              <EmptyState title="Bir olay seçin" description="Zaman çizelgesinden bir node, LLM çağrısı ya da Pydantic olayı seçin." />
            )}
          </Card>
        </div>
      )}
    </Page>
  );
}

function RunHeader({ d, liveState }: { d: TraceDetail; liveState: string }) {
  const r = d.run;
  return (
    <Card className="p-3">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-xs sm:grid-cols-4">
        <Meta k="Tür">{KIND_TR[r.kind]}</Meta>
        <Meta k="Tetikleyici">{r.trigger ?? "—"}</Meta>
        <Meta k="Durum">
          <StatusDot status={r.status} />
          {liveState !== "idle" && (
            <span className="ml-2 inline-flex items-center gap-1 text-st-warning-ink">
              <Radio className="size-3" /> {liveState === "open" ? "canlı takip" : "yeniden bağlanıyor"}
            </span>
          )}
        </Meta>
        <Meta k="Süre">{fmtDuration(r.duration_ms)}</Meta>
        <Meta k="Başlangıç">{fmtTime(r.started_at)}</Meta>
        <Meta k="thread_id">
          <Mono>{r.thread_id ?? "—"}</Mono>
        </Meta>
        <Meta k="Girişim">{r.attempt ?? 1}</Meta>
        <Meta k="Kayıt">{CAPTURE_TR[r.capture]}</Meta>
      </dl>
      {(d.parent || d.children.length > 0) && (
        <div className="mt-2 flex flex-wrap gap-3 border-t border-line pt-2 text-xs">
          {d.parent && (
            <span>
              Üst çalıştırma: <RunLink r={d.parent} />
            </span>
          )}
          {d.children.map((c) => (
            <span key={c.run_id}>
              Alt çalıştırma ({KIND_TR[c.kind]}): <RunLink r={c} />
            </span>
          ))}
        </div>
      )}
      {r.recorded && r.capture !== "complete" && (
        <Notice tone={r.capture === "truncated" ? "info" : "warning"} className="mt-2">
          {r.capture === "truncated" && `${r.truncated_count} olayın payload'ı boyut sınırı nedeniyle kırpıldı (olayda işaretli).`}
          {r.capture === "partial" &&
            `Kısmi kayıt: ${r.lost_events ?? 0} olay depoya yazılamadı, ${r.capture_errors ?? 0} yakalama hatası${r.status === "interrupted" ? "; çalıştırma yarıda kaldı" : ""}.`}
          {r.capture === "failed" && "Kayıt başarısız: olaylar depoya yazılamadı. Agent çalışması bundan etkilenmedi."}
        </Notice>
      )}
      {r.error && <div className="mt-2 font-mono text-2xs text-st-critical-ink">{r.error}</div>}
    </Card>
  );
}

const Meta = ({ k, children }: { k: string; children: ReactNode }) => (
  <div className="min-w-0">
    <dt className="text-muted">{k}</dt>
    <dd className="truncate text-ink-2">{children}</dd>
  </div>
);
const RunLink = ({ r }: { r: TraceRun }) => (
  <Link to={`/agent-trace/${encodeURIComponent(r.run_id)}`} className="font-mono text-accent-text hover:underline">
    {r.run_id}
  </Link>
);

// ---------------------------------------------------------------- zaman çizelgesi
function summaryText(e: TraceEventMeta): string {
  const s = (e.summary ?? {}) as Record<string, unknown>;
  const j = (v: unknown) => (Array.isArray(v) ? v.join(", ") : String(v ?? ""));
  switch (e.event_type) {
    case "transition":
      return `${j(s.from)} → ${j(s.to) || "END"}`;
    case "routing":
      return `${s.function}() → ${j(s.result)}`;
    case "llm_request":
    case "llm_response":
      return [s.task, s.model, s.finish_reason, s.streamed ? "streaming" : ""].filter(Boolean).join(" · ");
    case "validation_error":
      return `${s.errors ?? 0} alan hatası`;
    case "parser_output":
      return String(s.model ?? "");
    case "stream":
      return [s.mode, s.type].filter(Boolean).join(" / ");
    case "state_out":
      return s.combined ? `birleşik: ${j(s.nodes)}` : j(s.nodes);
    case "fallback":
      return `${s.from ?? ""} → ${s.to ?? ""}`;
    case "node_end":
      return s.node_error ? String(s.node_error) : "";
    default:
      return "";
  }
}

function Timeline({ events, cat, onCat, selected, onSelect }: { events: TraceEventMeta[]; cat: Cat; onCat: (c: Cat) => void; selected: string | null; onSelect: (id: string) => void }) {
  const shown = events.filter((e) => inCat(e, cat));
  const groups: { step: number | null; items: TraceEventMeta[] }[] = [];
  for (const e of shown) {
    const g = groups.at(-1);
    if (g && g.step === e.step) g.items.push(e);
    else groups.push({ step: e.step, items: [e] });
  }
  const parallel = (step: number | null) => (step === null ? [] : [...new Set(events.filter((e) => e.step === step && e.event_type === "node_start").map((e) => e.node))]);
  return (
    <div>
      <div className="border-b border-line p-2">
        <Segmented<Cat>
          label="olay filtresi"
          size="xs"
          value={cat}
          onChange={onCat}
          options={[
            { value: "all", label: "Tümü" },
            { value: "node", label: "Node" },
            { value: "llm", label: "LLM" },
            { value: "pydantic", label: "Pydantic" },
            { value: "stream", label: "Stream" },
            { value: "errors", label: `Hatalar (${events.filter(isError).length})` },
          ]}
        />
      </div>
      <ol className="max-h-[calc(100vh-18rem)] overflow-auto py-1" aria-label="olay zaman çizelgesi">
        {!shown.length && <li className="p-3 text-xs text-muted">Bu filtrede olay yok.</li>}
        {groups.map((g, gi) => {
          const nodes = parallel(g.step);
          return (
            <li key={gi}>
              {g.step !== null && (
                <div className="sticky top-0 z-[1] bg-surface-2 px-3 py-1 text-2xs font-medium text-muted">
                  Graph adımı {g.step}
                  {nodes.length > 1 && <span className="ml-1.5 text-st-warning-ink">paralel: {nodes.join(", ")}</span>}
                </div>
              )}
              <ul>
                {g.items.map((e) => (
                  <li key={e.event_id}>
                    <button
                      type="button"
                      onClick={() => onSelect(e.event_id)}
                      aria-current={selected === e.event_id}
                      className={cn("flex w-full items-start gap-2 px-3 py-1 text-left text-xs hover:bg-surface-2", selected === e.event_id && "bg-surface-3")}
                    >
                      <span className="w-7 shrink-0 text-right font-mono text-2xs text-muted tabular-nums">{e.sequence}</span>
                      <span className="min-w-0 flex-1">
                        <span className={cn("font-medium", isError(e) ? "text-st-critical-ink" : "text-ink")}>{EVENT_TR[e.event_type] ?? e.event_type}</span>
                        {e.node && <span className="ml-1.5 font-mono text-ink-2">{e.node}</span>}
                        {e.attempt && e.attempt > 1 && <span className="ml-1 text-2xs text-muted">#{e.attempt}</span>}
                        {e.truncated && <Badge className="ml-1.5">kırpıldı</Badge>}
                        <span className="block truncate text-2xs text-muted">{summaryText(e)}</span>
                      </span>
                      {e.duration_ms !== null && <span className="shrink-0 text-2xs text-muted tabular-nums">{fmtDuration(e.duration_ms)}</span>}
                    </button>
                  </li>
                ))}
              </ul>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

// ---------------------------------------------------------------- olay paneli
const NODE_TYPES: TraceEventType[] = ["node_start", "node_end"];
const CALL_TYPES: TraceEventType[] = ["llm_request", "llm_provider_request", "llm_response", "parser_input", "parser_output", "validation_error"];

function EventPanel({ traceKey, ev, live, onSelect, events }: { traceKey: string; ev: TraceEventMeta; live: boolean; onSelect: (id: string) => void; events: TraceEventMeta[] }) {
  if (NODE_TYPES.includes(ev.event_type) && ev.node_exec_id) return <NodeExecPanel traceKey={traceKey} exec={ev.node_exec_id} live={live} onSelect={onSelect} events={events} />;
  if (CALL_TYPES.includes(ev.event_type) && (ev.related_call_id || ev.call_id))
    return <CallPanel traceKey={traceKey} call={ev.related_call_id ?? ev.call_id!} live={live} onSelect={onSelect} events={events} />;
  return <GenericEvent traceKey={traceKey} ev={ev} events={events} onSelect={onSelect} />;
}

function PanelTitle({ children, meta }: { children: ReactNode; meta?: ReactNode }) {
  return (
    <div className="mb-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <h2 className="text-sm font-semibold text-ink">{children}</h2>
      {meta && <span className="text-xs text-muted">{meta}</span>}
    </div>
  );
}

function SnapshotView({ title, s, note }: { title: string; s: Snapshot | undefined; note?: ReactNode }) {
  if (!s || !s.captured)
    return (
      <div className="rounded-md border border-dashed border-line p-3 text-xs text-muted">
        <div className="font-medium text-ink-2">{title}</div>
        {UNOBSERVED}
      </div>
    );
  return (
    <div className="min-w-0">
      {note && <div className="mb-1 text-2xs text-muted">{note}</div>}
      <JsonView title={s.truncated ? `${title} (kırpıldı)` : title} value={s.value} open={1} />
    </div>
  );
}

const fmtVal = (v: unknown) => (v === undefined ? "" : typeof v === "string" ? v : JSON.stringify(v));

export function DiffTable({ changes }: { changes: NodeExec["diff"]["changes"] }) {
  if (!changes.length) return <div className="text-xs text-muted">State değişmedi.</div>;
  const tone = { added: "text-st-good-ink", removed: "text-st-critical-ink", changed: "text-st-warning-ink" } as const;
  const tr = { added: "eklendi", removed: "silindi", changed: "değişti" } as const;
  return (
    <div className="max-h-[60vh] overflow-auto">
      <table className="data-table">
        <thead>
          <tr>
            <th>JSON path</th>
            <th>Değişiklik</th>
            <th>Eski değer</th>
            <th>Yeni değer</th>
          </tr>
        </thead>
        <tbody>
          {changes.map((c) => (
            <tr key={c.path}>
              <td className="font-mono text-2xs break-all">{c.path}</td>
              <td className={cn("text-xs", tone[c.change])}>{tr[c.change]}</td>
              <td className="max-w-64 font-mono text-2xs break-all text-ink-2">{fmtVal(c.old)}</td>
              <td className="max-w-64 font-mono text-2xs break-all text-ink-2">{fmtVal(c.new)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function NodeExecPanel({ traceKey, exec, live, onSelect, events }: { traceKey: string; exec: string; live: boolean; onSelect: (id: string) => void; events: TraceEventMeta[] }) {
  const { data: n, isLoading, error } = useNodeExec(traceKey, exec, live);
  const [tab, setTab] = useState("ba");
  if (isLoading) return <Skeleton className="h-64" />;
  if (error || !n) return <ErrorState error={error} />;
  const execStart = (id: string) => events.find((e) => e.node_exec_id === id && e.event_type === "node_start");
  const jump = (id: string) => {
    const e = execStart(id);
    if (e) onSelect(e.event_id);
  };
  const stream = events.filter((e) => e.node_exec_id === exec && e.event_type === "stream");
  const calls = [...new Map(events.filter((e) => e.node_exec_id === exec && e.event_type === "llm_request").map((e) => [e.call_id, e])).values()];
  const tabs = [
    { value: "ba", label: "Before / After" },
    { value: "update", label: "Node Update" },
    { value: "diff", label: `Diff${n.diff.available ? ` (${n.diff.changes.length})` : ""}` },
    { value: "stream", label: `Stream olayları (${stream.length})` },
  ];
  return (
    <div>
      <PanelTitle meta={`graph adımı ${n.step} · deneme #${n.attempt} · ${fmtDuration(n.duration_ms)}`}>
        Node <Mono>{n.node}</Mono>
        {n.status === "error" && <span className="ml-2 text-st-critical-ink">hata</span>}
      </PanelTitle>
      <div className="mb-3 flex flex-wrap items-center gap-1.5 text-xs text-ink-2">
        <span className="text-muted">Geçiş:</span>
        {n.transition_in ? (
          n.transition_in.from_exec_ids.length ? (
            n.transition_in.from.map((f, i) => (
              <button key={f} type="button" className="font-mono text-accent-text hover:underline" onClick={() => jump(n.transition_in!.from_exec_ids[i])}>
                {f}
              </button>
            ))
          ) : (
            <span className="font-mono">{n.transition_in.from.join(", ")}</span>
          )
        ) : (
          <span className="text-muted">{UNOBSERVED}</span>
        )}
        <ArrowRight className="size-3 text-muted" />
        <span className="font-mono font-medium text-ink">{n.node}</span>
        <ArrowRight className="size-3 text-muted" />
        {n.transition_out ? (
          n.transition_out.to.length ? (
            n.transition_out.to.map((t) => {
              const id = n.transition_out!.to_exec_ids.find((x) => execStart(x)?.node === t);
              return id ? (
                <button key={t} type="button" className="font-mono text-accent-text hover:underline" onClick={() => jump(id)}>
                  {t}
                </button>
              ) : (
                <span key={t} className="font-mono">
                  {t}
                </span>
              );
            })
          ) : (
            <span className="font-mono">END</span>
          )
        ) : (
          <span className="text-muted">{UNOBSERVED}</span>
        )}
        {n.transition_out?.routing &&
          Object.values(n.transition_out.routing)
            .flat()
            .map((r, i) => (
              <span key={i} className="ml-2 text-2xs text-muted">
                routing: {r.function}() → {fmtVal(r.result)}
              </span>
            ))}
      </div>
      {calls.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-2 text-xs">
          <span className="text-muted">LLM çağrıları:</span>
          {calls.map((c) => (
            <button key={c.event_id} type="button" className="text-accent-text hover:underline" onClick={() => onSelect(c.event_id)}>
              {String(c.summary?.task ?? "llm")} #{c.attempt}
            </button>
          ))}
        </div>
      )}
      <Tabs value={tab} onValueChange={setTab} tabs={tabs}>
        <TabPanel value="ba">
          <div className="grid gap-3 xl:grid-cols-2">
            <SnapshotView title="Before · State In" s={n.before} note="Node'a verilen state (graph adımı başı)" />
            <SnapshotView
              title={n.after.combined ? "Bu adımın birleşik state'i" : "After · State Out"}
              s={n.after}
              note={n.after.combined ? `Paralel node'ların reducer'larla birleşmiş state'i (${n.after.step_node_exec_ids.length} node)` : "Reducer'lar uygulandıktan sonra (graph adımı sonu)"}
            />
          </div>
        </TabPanel>
        <TabPanel value="update">
          {n.update.error && (
            <Notice tone="critical" className="mb-2">
              {n.update.error.error_type}: {n.update.error.message}
            </Notice>
          )}
          <SnapshotView title="Node Update (node'un döndürdüğü değer)" s={n.update} />
        </TabPanel>
        <TabPanel value="diff">
          {n.diff.available ? <DiffTable changes={n.diff.changes} /> : <div className="text-xs text-muted">Diff hesaplanamadı: {n.diff.reason}</div>}
          {n.after.combined && <div className="mt-2 text-2xs text-muted">After birleşik state olduğu için diff paralel node'ların değişikliklerini de içerir; bu node'un kendi katkısı Node Update sekmesindedir.</div>}
        </TabPanel>
        <TabPanel value="stream">
          <StreamList events={stream} traceKey={traceKey} />
        </TabPanel>
      </Tabs>
    </div>
  );
}

function StreamList({ events, traceKey }: { events: TraceEventMeta[]; traceKey: string }) {
  const [open, setOpen] = useState<string | null>(null);
  const { data } = useTraceEvent(traceKey, open);
  if (!events.length) return <div className="text-xs text-muted">Stream olayı yok.</div>;
  return (
    <div className="space-y-2">
      <table className="data-table">
        <thead>
          <tr>
            <th className="num">#</th>
            <th>Mode</th>
            <th>Tür</th>
            <th>Namespace</th>
            <th>Taşıdığı veri</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.event_id} className="cursor-pointer" onClick={() => setOpen(e.event_id)}>
              <td className="num text-2xs">{e.sequence}</td>
              <td className="font-mono text-xs">{String(e.summary?.mode ?? "")}</td>
              <td className="font-mono text-xs">{String(e.summary?.type ?? "")}</td>
              <td className="font-mono text-2xs text-muted">{open === e.event_id && data ? JSON.stringify(data.payload.namespace ?? []) : "…"}</td>
              <td className="text-xs">{open === e.event_id && data ? String(data.payload.data_ref ?? "") : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data && open && <JsonView title="Stream olayı" value={data.payload} open={2} />}
    </div>
  );
}

// ---------------------------------------------------------------- LLM / Pydantic
function SourceBadge({ source }: { source: "framework" | "provider" }) {
  return <Badge title={source === "framework" ? "LangChain'in modele verdiği / modelden aldığı veri" : "Sağlayıcı API'sine giden / gelen JSON"}>{source === "framework" ? "Framework payload" : "Provider payload"}</Badge>;
}

function NotCaptured({ children }: { children: ReactNode }) {
  return <div className="rounded-md border border-dashed border-line p-3 text-xs text-muted">Kaydedilemedi — {children}</div>;
}

function CallPanel({ traceKey, call, live, onSelect, events }: { traceKey: string; call: string; live: boolean; onSelect: (id: string) => void; events: TraceEventMeta[] }) {
  const { data, isLoading, error } = useTraceCall(traceKey, call, live);
  const [tab, setTab] = useState<string | null>(null);
  if (isLoading) return <Skeleton className="h-64" />;
  if (error || !data) return <ErrorState error={error} />;
  const by = (t: TraceEventType) => data.events.find((e) => e.event_type === t);
  const req = by("llm_request");
  const preq = by("llm_provider_request");
  const resp = by("llm_response");
  const pin = by("parser_input");
  const pout = by("parser_output");
  const verr = by("validation_error");
  const rp = (resp?.payload ?? {}) as Record<string, unknown>;
  const tabs = [
    req && { value: "req", label: "LLM Request" },
    { value: "preq", label: "Provider Request" },
    resp && { value: "resp", label: "LLM Response" },
    (pin || pout || verr) && { value: "parser", label: verr ? "Pydantic · Validation Error" : "Pydantic" },
  ].filter(Boolean) as { value: string; label: string }[];
  const active = tab ?? (tabs.find((t) => t.value === "parser" && verr) ?? tabs[0]).value;
  const head = req ?? resp ?? pin;
  return (
    <div>
      <PanelTitle meta={`${head?.node ? `node ${head.node} · ` : ""}deneme #${head?.attempt ?? 1}${resp?.duration_ms ? ` · ${fmtDuration(resp.duration_ms)}` : ""}`}>
        LLM çağrısı <Mono>{String(req?.payload.task ?? resp?.payload.task ?? "")}</Mono>
      </PanelTitle>
      <div className="mb-2 font-mono text-2xs text-muted">call_id {data.call_id}</div>
      <Tabs value={active} onValueChange={setTab} tabs={tabs}>
        {req && (
          <TabPanel value="req">
            <div className="mb-1.5 flex gap-2">
              <SourceBadge source="framework" />
              <span className="text-xs text-muted">model {String(req.payload.model ?? "—")}</span>
            </div>
            <JsonView title="LLM Request" value={req.payload} open={2} />
          </TabPanel>
        )}
        <TabPanel value="preq">
          <div className="mb-1.5">
            <SourceBadge source="provider" />
          </div>
          {preq ? <JsonView title="Provider Request" value={preq.payload.payload} open={1} /> : <NotCaptured>sağlayıcı adaptörü bu çağrıda request JSON'unu üretmedi (sahte model ya da sağlayıcı dışı çağrı).</NotCaptured>}
        </TabPanel>
        {resp && (
          <TabPanel value="resp">
            <div className="mb-1.5 flex flex-wrap gap-2 text-xs text-muted">
              <SourceBadge source="framework" />
              {rp.finish_reason ? <span>finish_reason {String(rp.finish_reason)}</span> : null}
              {resp.status === "error" && <span className="text-st-critical-ink">hata</span>}
            </div>
            <JsonView title="LLM Response (framework)" value={{ message: rp.message, usage: rp.usage, error: rp.error, llm_output: rp.llm_output }} open={2} />
            <div className="mt-3 mb-1.5">
              <SourceBadge source="provider" />
            </div>
            {rp.provider_response_status === "captured" ? (
              <JsonView title="Provider Response" value={rp.provider_response} open={1} />
            ) : rp.provider_response_status === "not_capturable_streaming" ? (
              <NotCaptured>streaming çağrısında sağlayıcı yanıtı tek JSON olarak oluşmaz; sıralı chunk'lar aşağıda.</NotCaptured>
            ) : (
              <NotCaptured>sağlayıcı yanıtı bu çağrıda adaptörden geçmedi.</NotCaptured>
            )}
            {Array.isArray(rp.chunks) && rp.chunks.length > 0 && (
              <div className="mt-3">
                <div className="mb-1 text-xs text-muted">
                  Streaming chunk'ları ({(rp.chunks as unknown[]).length} kayıtlı; sınır {String(rp.chunk_limit)}
                  {Number(rp.chunks_dropped) > 0 && `, ${String(rp.chunks_dropped)} chunk sınır nedeniyle kaydedilmedi`})
                </div>
                <JsonView title="Chunk'lar (sıralı)" value={rp.chunks} open={0} />
              </div>
            )}
          </TabPanel>
        )}
        {(pin || pout || verr) && (
          <TabPanel value="parser">
            <div className="grid gap-3 xl:grid-cols-2">
              <div className="min-w-0">
                {pin ? <JsonView title="Parser Input" value={{ text: pin.payload.text, json: pin.payload.json }} open={2} /> : <NotCaptured>parser girdisi callback'te görünmedi.</NotCaptured>}
              </div>
              <div className="min-w-0">
                {pout && (
                  <>
                    <div className="mb-1 text-xs text-muted">
                      Doğrulanan model <Mono>{String(pout.payload.model)}</Mono>
                    </div>
                    <JsonView title="Validated Output" value={pout.payload.validated} open={2} />
                  </>
                )}
                {verr && <ValidationErrorView e={verr} onSelect={onSelect} events={events} />}
              </div>
            </div>
          </TabPanel>
        )}
      </Tabs>
    </div>
  );
}

function ValidationErrorView({ e, onSelect, events }: { e: TraceEventFull; onSelect: (id: string) => void; events: TraceEventMeta[] }) {
  const p = e.payload as { error_type?: string; model?: string | null; errors?: { path: string; type: string; message: string }[]; message?: string };
  const f = e.followup;
  const retry = f?.kind === "retry" ? events.find((x) => x.call_id === f.call_id && x.event_type === "llm_request") : undefined;
  return (
    <div className="rounded-md border border-st-critical/40 p-2">
      <div className="mb-1 text-xs font-medium text-st-critical-ink">
        Validation Error {p.model && <span className="font-mono">({p.model})</span>}
      </div>
      {p.errors?.length ? (
        <table className="data-table">
          <thead>
            <tr>
              <th>Alan yolu</th>
              <th>Tür</th>
              <th>Mesaj (maskeli)</th>
            </tr>
          </thead>
          <tbody>
            {p.errors.map((x, i) => (
              <tr key={i}>
                <td className="font-mono text-2xs">{x.path || "(kök)"}</td>
                <td className="font-mono text-2xs">{x.type}</td>
                <td className="text-xs">{x.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="font-mono text-2xs">
          {p.error_type}: {p.message}
        </div>
      )}
      <div className="mt-2 text-xs">
        {f?.kind === "retry" ? (
          retry ? (
            <button type="button" className="text-accent-text hover:underline" onClick={() => onSelect(retry.event_id)}>
              Retry: deneme #{f.attempt} →
            </button>
          ) : (
            <span>Retry: deneme #{f.attempt}</span>
          )
        ) : f?.kind === "fallback" ? (
          <span>Fallback: {f.to}</span>
        ) : (
          <span className="text-muted">Bu hatadan sonra retry yok.</span>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- diğer olaylar
function GenericEvent({ traceKey, ev, events, onSelect }: { traceKey: string; ev: TraceEventMeta; events: TraceEventMeta[]; onSelect: (id: string) => void }) {
  const { data, isLoading, error } = useTraceEvent(traceKey, ev.event_id);
  if (isLoading) return <Skeleton className="h-48" />;
  if (error || !data) return <ErrorState error={error} />;
  const p = data.payload as Record<string, unknown>;
  const isState = ev.event_type === "state_out";
  const nodeLinks = isState ? ((p.node_exec_ids as string[]) ?? []).map((id) => events.find((e) => e.node_exec_id === id && e.event_type === "node_start")).filter(Boolean) : [];
  const title = isState ? (p.combined ? "Bu adımın birleşik state'i" : `State Out · graph adımı ${ev.step}`) : EVENT_TR[ev.event_type] ?? ev.event_type;
  return (
    <div>
      <PanelTitle meta={`#${ev.sequence} · ${fmtTime(ev.ts)}${ev.truncated ? " · kırpıldı" : ""}`}>{title}</PanelTitle>
      {nodeLinks.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-2 text-xs">
          <span className="text-muted">Bu adımda çalışan node'lar:</span>
          {nodeLinks.map((e) => (
            <button key={e!.event_id} type="button" className="font-mono text-accent-text hover:underline" onClick={() => onSelect(e!.event_id)}>
              {e!.node}
            </button>
          ))}
        </div>
      )}
      {p._capture_failed ? <NotCaptured>olay verisi maskelenemediği için yazılmadı ({String(p.error_type)}).</NotCaptured> : <JsonView title="Olay verisi" value={isState ? p.values : p} open={isState ? 1 : 2} />}
      {isState && (
        <div className="mt-2 text-xs text-muted">
          Sonraki: <span className="font-mono">{((p.next as string[]) ?? []).join(", ") || "END"}</span>
        </div>
      )}
    </div>
  );
}
