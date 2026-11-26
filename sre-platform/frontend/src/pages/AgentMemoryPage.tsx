/** Agent Memory: memory kayıtlarını, retrieval pipeline'ını, checkpoint'leri ve memory olaylarını tek yerde gösterir.
 *  Değerler backend'in ölçtüğü gerçek verilerdir; ölçülmeyen değer "—" gösterilir. Embedding / vector search
 *  kurulmadığı için Semantic ve Hybrid stratejileri devre dışıdır. */
import { ConceptHelp } from "@/components/ConceptHelp";
import { Archive, Check, Database, Download, ExternalLink, Plus, RotateCcw, ShieldCheck, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { RefreshControl, RangeTabs, SearchInput } from "@/components/filters";
import { JsonView } from "@/components/JsonView";
import { MetricStrip, StatTile } from "@/components/StatTile";
import { TimeSeriesChart } from "@/components/charts/TimeSeriesChart";
import { TableWrap } from "@/components/table";
import { Dialog, TabPanel, Tabs } from "@/components/ui/overlay";
import { Badge, Button, Card, CardBody, CardHeader, Disclosure, Input, Label, Mono, Select, Textarea } from "@/components/ui/primitives";
import { EmptyState, ErrorState, Notice, Page, PageHeader, Pagination, TableSkeleton } from "@/components/ui/states";
import { errorToast } from "@/hooks/queries";
import { DiffTable } from "@/pages/AgentTracePage";
import { SectionForm } from "@/features/settings/SectionForm";
import {
  exportUrl,
  useCreateRecord,
  useDeleteRecord,
  useMemAgents,
  useMemApprovals,
  useMemCheckpoint,
  useMemCheckpoints,
  useMemCompare,
  useMemEvent,
  useMemEvents,
  useMemGovernance,
  useMemOverview,
  useMemRecord,
  useMemRecords,
  useMemRetrieval,
  useMemRetrievals,
  useMemStrategies,
  useMemThread,
  useMemThreads,
  useMemVersions,
  usePlayground,
  useRecordOp,
  useUpdateRecord,
  useWorkingMemory,
  useWorkingRuns,
  type MemoryEventRow,
  type MemoryType,
  type MemRange,
  type StateDiff,
} from "@/features/memory/api";
import { api, ApiError } from "@/services/api";
import { useAuth } from "@/services/auth";
import { streamRun } from "@/services/sse";
import { cn, fmtDuration, fmtNum, fmtTime } from "@/lib/utils";

const TABS = [
  { value: "overview", label: "Overview" },
  { value: "working", label: "Working Memory" },
  { value: "short", label: "Short-Term Memory" },
  { value: "long", label: "Long-Term Memory" },
  { value: "checkpoints", label: "Checkpoints" },
  { value: "retrieval", label: "Memory Retrieval" },
  { value: "activity", label: "Memory Activity" },
  { value: "governance", label: "Memory Governance" },
  { value: "settings", label: "Settings" },
];
const RANGES: { value: MemRange; label: string; title: string }[] = [
  { value: "1h", label: "1h", title: "Son 1 saat" },
  { value: "24h", label: "24h", title: "Son 24 saat" },
  { value: "7d", label: "7d", title: "Son 7 gün" },
  { value: "30d", label: "30d", title: "Son 30 gün" },
];
/** Sabit sıralı kategorik renkler (doğrulanmış uygulama paleti; varlığa bağlı, sıraya göre değil). */
const SERIES = ["var(--color-app-reservation)", "var(--color-app-checkin)", "var(--color-app-flightops)", "var(--color-app-baggage)"];

const pct = (v: number | null | undefined) => (v == null ? "—" : `${fmtNum(v * 100, 1)}%`);
const ms = (v: number | null | undefined) => (v == null ? "—" : fmtDuration(v));
const bytes = (n: number) => (n >= 1 << 20 ? `${fmtNum(n / (1 << 20), 1)} MB` : n >= 1024 ? `${fmtNum(n / 1024, 0)} KB` : `${n} B`);
const short = (s: string | null | undefined, n = 10) => (s ? (s.length > n ? `${s.slice(0, n)}…` : s) : "—");

function StatusDot({ status }: { status: string }) {
  const tone =
    status === "healthy" || status === "ok" || status === "active" || status === "approved" || status === "verified" || status === "enforced" || status === "enabled"
      ? "bg-st-good"
      : status === "pending" || status === "unverified" || status === "archived" || status === "not_configured" || status === "running"
        ? "bg-st-warning"
        : status === "disabled" || status === "unknown" || status === "not_applicable" || status === "not_required"
          ? "bg-st-unknown"
          : "bg-st-critical";
  return (
    <span className="inline-flex items-center gap-1.5 text-xs whitespace-nowrap">
      <span aria-hidden className={cn("size-1.5 rounded-full", tone)} />
      <span className="text-ink-2">{status}</span>
    </span>
  );
}

/** Form alanı: görünür etiket + `<label>` sarmalı (erişilebilir ad). */
function FormField({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <label className={cn("block space-y-1", className)}>
      <span className="text-xs text-muted">{label}</span>
      {children}
    </label>
  );
}

function KV({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="min-w-0 break-all text-ink-2">{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

function Diff({ diff }: { diff: StateDiff | undefined }) {
  if (!diff) return null;
  if (!Array.isArray(diff)) return <div className="text-xs text-muted">{diff.reason}</div>;
  return <DiffTable changes={diff} />;
}

// ================================================================================================= sayfa
export default function AgentMemoryPage() {
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") || "overview";
  const agent = sp.get("agent") || "sre-agent";
  const range = (sp.get("range") as MemRange) || "24h";
  const set = (k: string, v: string | null) =>
    setSp(
      (p) => {
        const n = new URLSearchParams(p);
        if (v) n.set(k, v);
        else n.delete(k);
        return n;
      },
      { replace: true },
    );
  const agents = useMemAgents();
  const ov = useMemOverview(agent, range);
  const env = ov.data?.environment || "lab";

  return (
    <Page wide>
      <PageHeader
        title="Agent Memory"
        description="Agent'ın önceki konuşma ve çalıştırmalardan ne hatırladığı ve bu bilgiyi ne zaman kullandığı"
        meta={<ConceptHelp k="memory" />}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Select aria-label="Agent" value={agent} onChange={(e) => set("agent", e.target.value)} className="w-52">
              {(agents.data?.items ?? [{ agent_id: "sre-agent", name: "SRE Agent (embedded)" }]).map((a) => (
                <option key={a.agent_id} value={a.agent_id}>
                  {a.name}
                </option>
              ))}
            </Select>
            <Select aria-label="Environment" value={env} disabled className="w-28" title="Tek environment (lab)">
              <option value={env}>{env}</option>
            </Select>
            <RangeTabs<MemRange> value={range} onChange={(v) => set("range", v)} options={RANGES} />
            <RefreshControl onRefresh={() => void ov.refetch()} fetching={ov.isFetching} updatedAt={ov.dataUpdatedAt} auto="30 sn" />
          </div>
        }
      />
      <Tabs value={tab} onValueChange={(v) => set("tab", v)} tabs={TABS}>
        <TabPanel value="overview">
          <OverviewTab agent={agent} range={range} />
        </TabPanel>
        <TabPanel value="working">
          <WorkingTab />
        </TabPanel>
        <TabPanel value="short">
          <ShortTermTab agent={agent} />
        </TabPanel>
        <TabPanel value="long">
          <LongTermTab agent={agent} />
        </TabPanel>
        <TabPanel value="checkpoints">
          <CheckpointsTab agent={agent} />
        </TabPanel>
        <TabPanel value="retrieval">
          <RetrievalTab agent={agent} range={range} />
        </TabPanel>
        <TabPanel value="activity">
          <ActivityTab agent={agent} range={range} />
        </TabPanel>
        <TabPanel value="governance">
          <GovernanceTab />
        </TabPanel>
        <TabPanel value="settings">
          <SectionForm
            section="agent_memory"
            title="Memory Settings"
            description="Varsayılan değerler memory modülünden önceki davranışla aynıdır. Değişiklik versiyonlanır ve audit'e yazılır; bir sonraki agent çalışmasından itibaren uygulanır."
          />
        </TabPanel>
      </Tabs>
    </Page>
  );
}

// ================================================================================================= Overview
function OverviewTab({ agent, range }: { agent: string; range: MemRange }) {
  const { data: d, isLoading, error, refetch } = useMemOverview(agent, range);
  if (error) return <ErrorState error={error} onRetry={() => refetch()} />;
  const m = d?.metrics;
  const c = d?.charts;
  const def = d?.definitions ?? {};
  return (
    <div className="space-y-4">
      <MetricStrip className="grid-cols-2 md:grid-cols-4 xl:grid-cols-8">
        <StatTile loading={isLoading} label="Total Memory Records" value={fmtNum(m?.total_memory_records)} sub={def.total_memory_records && "ayrık kaynakların toplamı"} />
        <StatTile loading={isLoading} label="Active Threads" value={fmtNum(m?.active_threads)} sub={`toplam ${fmtNum(m?.total_threads)}`} />
        <StatTile loading={isLoading} label="Total Checkpoints" value={fmtNum(m?.total_checkpoints)} />
        <StatTile loading={isLoading} label="Semantic" value={fmtNum(m?.semantic)} />
        <StatTile loading={isLoading} label="Episodic" value={fmtNum(m?.episodic)} />
        <StatTile loading={isLoading} label="Procedural" value={fmtNum(m?.procedural)} />
        <StatTile loading={isLoading} label="Memory Reads" value={fmtNum(m?.reads)} sub={`checkpoint load ${fmtNum(m?.checkpoint_loads)}`} />
        <StatTile loading={isLoading} label="Memory Writes" value={fmtNum(m?.writes)} sub={`checkpoint write ${fmtNum(m?.checkpoint_writes)}`} />
        <StatTile loading={isLoading} label="Retrieval Success Rate" value={pct(m?.retrieval_success_rate)} missing={m?.retrieval_success_rate == null} sub={`${fmtNum(m?.retrievals)} retrieval`} />
        <StatTile loading={isLoading} label="Empty Retrieval Rate" value={pct(m?.empty_retrieval_rate)} missing={m?.empty_retrieval_rate == null} />
        <StatTile loading={isLoading} label="Read Latency p50 / p95" value={`${ms(m?.read_latency.p50)} / ${ms(m?.read_latency.p95)}`} missing={!m?.read_latency.n} />
        <StatTile loading={isLoading} label="Write Latency p50 / p95" value={`${ms(m?.write_latency.p50)} / ${ms(m?.write_latency.p95)}`} missing={!m?.write_latency.n} />
        <StatTile loading={isLoading} label="Retrieval Latency p95" value={ms(m?.retrieval_latency.p95)} missing={m?.retrieval_latency.p95 == null} sub={`p99 ${ms(m?.retrieval_latency.p99)}`} />
        <StatTile loading={isLoading} label="Context Tokens" value={fmtNum(m?.context_tokens)} sub="tahmin (karakter / 4)" />
        <StatTile loading={isLoading} label="Memory Storage" value={m ? bytes(m.storage_total) : "—"} sub="PostgreSQL tablo boyutu" />
        <StatTile loading={isLoading} label="Expired / Rejected" value={`${fmtNum(m?.expired)} / ${fmtNum(m?.rejected)}`} />
      </MetricStrip>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Memory Reads & Writes" description="Memory olayları (checkpoint yazımları ayrı seri)" />
          <CardBody>
            <TimeSeriesChart
              emptyText="Bu aralıkta memory olayı yok"
              format={(v) => fmtNum(v)}
              series={[
                { id: "reads", label: "Reads", color: SERIES[0], points: (c?.reads_writes ?? []).map((r) => ({ t: r.t, v: r.reads })) },
                { id: "writes", label: "Writes", color: SERIES[1], points: (c?.reads_writes ?? []).map((r) => ({ t: r.t, v: r.writes })) },
                { id: "ckpt", label: "Checkpoint writes", color: SERIES[2], points: (c?.reads_writes ?? []).map((r) => ({ t: r.t, v: r.checkpoints })) },
              ]}
            />
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Retrieval Latency" description="Retrieval pipeline süresi (agent + analysis; playground hariç)" />
          <CardBody>
            <TimeSeriesChart
              emptyText="Bu aralıkta retrieval yok"
              unit="ms"
              format={(v) => ms(v)}
              series={[
                { id: "p50", label: "p50", color: SERIES[0], points: (c?.retrieval_latency ?? []).map((r) => ({ t: r.t, v: r.p50 })) },
                { id: "p95", label: "p95", color: SERIES[1], points: (c?.retrieval_latency ?? []).map((r) => ({ t: r.t, v: r.p95 })) },
              ]}
            />
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Memory Growth" description="Oluşturulan long-term kayıt sayısı" />
          <CardBody>
            <TimeSeriesChart
              emptyText="Bu aralıkta yeni kayıt yok"
              format={(v) => fmtNum(v)}
              series={[{ id: "created", label: "Created", color: "var(--color-chart-single)", points: (c?.growth ?? []).map((r) => ({ t: r.t, v: r.created })) }]}
            />
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Context Token Usage" description="LLM isteğine eklenen memory bölümleri (tahmini token)" />
          <CardBody>
            <TimeSeriesChart
              emptyText="Bu aralıkta context'e memory eklenmedi"
              format={(v) => fmtNum(v)}
              series={[{ id: "tokens", label: "Tokens", color: "var(--color-chart-single)", points: (c?.context_tokens ?? []).map((r) => ({ t: r.t, v: r.tokens })) }]}
            />
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader title="Memory Type Distribution" />
          <CardBody>
            <Bars rows={(c?.distribution ?? []).map((r) => ({ label: r.memory_type, value: r.count }))} />
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Validation Results" description="Seçili aralık" />
          <CardBody>
            <Bars rows={(c?.validation ?? []).map((r) => ({ label: r.result, value: r.count }))} empty="Bu aralıkta validation olayı yok" />
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Memory Usage by Namespace" description="Checkpoint olayları hariç" />
          <CardBody>
            <Bars rows={(c?.by_namespace ?? []).map((r) => ({ label: r.namespace, value: r.count }))} empty="Bu aralıkta olay yok" />
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Memory Backend Health" />
          <CardBody>
            {d ? (
              <table className="data-table">
                <tbody>
                  {(["checkpointer", "store", "memory_tables", "event_publisher", "vector_index"] as const).map((k) => {
                    const h = d.health[k];
                    return (
                      <tr key={k}>
                        <td className="text-xs">{k}</td>
                        <td>{h ? <StatusDot status={h.status} /> : "—"}</td>
                        <td className="text-xs text-muted tabular-nums">
                          {h?.latency_ms != null ? `${h.latency_ms} ms` : k === "event_publisher" ? `published ${h?.published ?? 0} · lost ${h?.lost ?? 0}` : (h?.note ?? h?.error ?? "")}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : (
              <TableSkeleton rows={4} cols={3} />
            )}
            {d?.health.backends && (
              <div className="mt-3">
                <KV rows={Object.entries(d.health.backends).map(([k, v]) => [k, v ?? "yok"])} />
              </div>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Storage & Records by Backing" />
          <CardBody className="grid gap-4 sm:grid-cols-2">
            <KV rows={Object.entries(m?.by_backing ?? {}).map(([k, v]) => [k, fmtNum(v)])} />
            <KV rows={Object.entries(m?.storage_bytes ?? {}).map(([k, v]) => [k, bytes(v)])} />
          </CardBody>
        </Card>
      </div>
      <Disclosure title="Metric tanımları" meta="hesaplama yöntemi">
        <KV rows={Object.entries(def).map(([k, v]) => [k, v])} />
      </Disclosure>
    </div>
  );
}

function Bars({ rows, empty = "Veri yok" }: { rows: { label: string; value: number }[]; empty?: string }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  if (!rows.length || rows.every((r) => !r.value)) return <div className="py-6 text-center text-xs text-muted">{empty}</div>;
  return (
    <ul className="space-y-1.5">
      {rows.map((r) => (
        <li key={r.label} className="grid grid-cols-[minmax(0,10rem)_1fr_3rem] items-center gap-2 text-xs" title={`${r.label}: ${r.value}`}>
          <span className="truncate text-ink-2">{r.label}</span>
          <span className="h-2 rounded-r bg-surface-2">
            <span className="block h-2 rounded-r" style={{ width: `${(r.value / max) * 100}%`, background: "var(--color-chart-single)" }} />
          </span>
          <span className="text-right text-ink tabular-nums">{fmtNum(r.value)}</span>
        </li>
      ))}
    </ul>
  );
}

// ================================================================================================= Working
function WorkingTab() {
  const runs = useWorkingRuns();
  const [sel, setSel] = useState<string | null>(null);
  const cur = sel ?? runs.data?.items[0]?.run_id ?? null;
  const live = runs.data?.items.find((r) => r.run_id === cur)?.status === "running";
  const wm = useWorkingMemory(cur, live);
  return (
    <div className="grid gap-4 lg:grid-cols-[20rem_1fr]">
      <Card>
        <CardHeader title="Agent çalışmaları" description="Çalışan run'lar üstte; Agent Trace kayıtları" />
        <div className="max-h-[70vh] overflow-auto">
          {runs.isLoading ? (
            <TableSkeleton rows={6} cols={2} />
          ) : !runs.data?.items.length ? (
            <EmptyState title="Kayıtlı agent çalışması yok" />
          ) : (
            <ul>
              {runs.data.items.map((r) => (
                <li key={r.run_id}>
                  <button
                    type="button"
                    onClick={() => setSel(r.run_id)}
                    className={cn("flex w-full items-center justify-between gap-2 border-b border-line px-3 py-2 text-left text-xs hover:bg-surface-2", r.run_id === cur && "bg-surface-2")}
                  >
                    <span className="min-w-0">
                      <Mono className="block truncate">{r.run_id}</Mono>
                      <span className="text-muted">
                        {r.kind} · {fmtTime(r.started_at)}
                      </span>
                    </span>
                    <StatusDot status={r.status} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Card>
      <div className="min-w-0 space-y-4">
        {!cur ? (
          <EmptyState title="Bir agent çalışması seçin" />
        ) : wm.error ? (
          <ErrorState error={wm.error} />
        ) : !wm.data ? (
          <TableSkeleton rows={6} cols={3} />
        ) : (
          <>
            <Card>
              <CardHeader
                title="Working Memory"
                description={wm.data.source}
                actions={
                  <Link to={wm.data.trace_link} className="inline-flex items-center gap-1 text-xs text-accent hover:underline">
                    Agent Trace <ExternalLink className="size-3" />
                  </Link>
                }
              />
              <CardBody>
                <KV
                  rows={[
                    ["Run ID", <Mono>{wm.data.run_id}</Mono>],
                    ["Thread ID", <Mono>{wm.data.thread_id ?? "—"}</Mono>],
                    ["Agent ID", wm.data.agent_id],
                    ["Execution status", <StatusDot status={wm.data.execution_status} />],
                    ["Current node", wm.data.current_node ?? "—"],
                    ["Current iteration", wm.data.current_iteration ?? "—"],
                    ["Pending tasks", wm.data.pending_tasks.join(", ") || "—"],
                    ["Completed tasks", wm.data.completed_tasks.join(" → ") || "—"],
                  ]}
                />
                {live && <Notice tone="info" className="mt-3">Çalışma sürüyor — 2 sn'de bir yenilenir.</Notice>}
              </CardBody>
            </Card>
            <div className="grid gap-4 xl:grid-cols-2">
              {Object.entries(wm.data.fields).map(([k, v]) => (
                <Card key={k}>
                  <CardHeader title={k} />
                  <CardBody>{Object.keys(v).length ? <JsonView value={v} open={1} maxH="max-h-72" /> : <div className="text-xs text-muted">Bu çalışmada yok</div>}</CardBody>
                </Card>
              ))}
            </div>
            <Card>
              <CardHeader title="Node executions" />
              <TableWrap>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Node</th>
                      <th>Step</th>
                      <th>Status</th>
                      <th className="text-right">Duration</th>
                      <th>Updated keys</th>
                    </tr>
                  </thead>
                  <tbody>
                    {wm.data.nodes.map((n) => (
                      <tr key={n.node_exec_id}>
                        <td className="text-xs">{n.node}</td>
                        <td className="text-xs tabular-nums">{n.step ?? "—"}</td>
                        <td>
                          <StatusDot status={n.status} />
                        </td>
                        <td className="text-right text-xs tabular-nums">{ms(n.duration_ms)}</td>
                        <td className="font-mono text-2xs text-ink-2">{(n.update_keys ?? []).join(", ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            </Card>
            <Card>
              <CardHeader title="Current State" description="Son graph adımının state'i (maskeli kopya)" />
              <CardBody>
                <JsonView value={wm.data.current_state} open={1} />
              </CardBody>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}

// ================================================================================================= Short-term
function ShortTermTab({ agent }: { agent: string }) {
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [page, setPage] = useState(1);
  const [sel, setSel] = useState<string | null>(null);
  const th = useMemThreads(agent, { q: q || undefined, kind: kind || undefined, page, size: 50 });
  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
          <SearchInput value={q} onChange={(v) => (setQ(v), setPage(1))} placeholder="Thread ID ara…" className="w-64" />
          <Select aria-label="Execution type" value={kind} onChange={(e) => (setKind(e.target.value), setPage(1))} className="w-44">
            <option value="">Tüm execution türleri</option>
            <option value="chat">Interactive (chat)</option>
            <option value="analysis">Analysis</option>
          </Select>
          <span className="ml-auto text-xs text-muted tabular-nums">{th.data?.total ?? 0} thread</span>
        </div>
        {th.error ? (
          <ErrorState error={th.error} />
        ) : th.isLoading ? (
          <TableSkeleton rows={8} cols={8} />
        ) : !th.data?.items.length ? (
          <EmptyState title="Checkpoint'i olan thread yok" />
        ) : (
          <TableWrap fetching={th.isFetching} maxH="max-h-[50vh]">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Thread ID</th>
                  <th>Execution type</th>
                  <th>User</th>
                  <th>Created</th>
                  <th>Last activity</th>
                  <th className="text-right">Messages</th>
                  <th className="text-right">Checkpoints</th>
                  <th className="text-right">Est. tokens</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {th.data.items.map((t) => (
                  <tr key={t.thread_id} onClick={() => setSel(t.thread_id)} className={cn("cursor-pointer", sel === t.thread_id && "bg-surface-2")}>
                    <td>
                      <Mono className="text-2xs">{short(t.thread_id, 28)}</Mono>
                      {t.title && <div className="truncate text-xs text-muted">{t.title}</div>}
                    </td>
                    <td className="text-xs">{t.execution_type}</td>
                    <td className="font-mono text-2xs">{short(t.user_id, 8)}</td>
                    <td className="text-xs">{fmtTime(t.created_at)}</td>
                    <td className="text-xs">{fmtTime(t.last_activity)}</td>
                    <td className="text-right text-xs tabular-nums">{t.message_count ?? "—"}</td>
                    <td className="text-right text-xs tabular-nums">{t.checkpoint_count}</td>
                    <td className="text-right text-xs tabular-nums">{t.estimated_tokens ?? "—"}</td>
                    <td>
                      <StatusDot status={t.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
        {th.data && th.data.pages > 1 && <Pagination page={page} pages={th.data.pages} total={th.data.total} onPage={setPage} />}
      </Card>
      {sel && <ThreadDetailView id={sel} />}
    </div>
  );
}

function ThreadDetailView({ id }: { id: string }) {
  const { data: d, error, isLoading } = useMemThread(id);
  if (error) return <ErrorState error={error} />;
  if (isLoading || !d) return <TableSkeleton rows={6} cols={4} />;
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title={d.title ?? "Thread"}
          description={d.note}
          actions={
            <Link to={`?tab=checkpoints&thread=${encodeURIComponent(d.thread_id)}`} className="text-xs text-accent hover:underline">
              Checkpoint'leri aç
            </Link>
          }
        />
        <CardBody className="grid gap-4 md:grid-cols-2">
          <KV
            rows={[
              ["Thread ID", <Mono>{d.thread_id}</Mono>],
              ["Execution type", d.execution_type],
              ["Checkpoints", d.checkpoint_count],
              ["Active", d.active ? "evet" : "hayır"],
              ["Investigation", d.investigation_id ? <Link className="text-accent hover:underline" to={`/investigations/${d.investigation_id}`}>{short(d.investigation_id, 8)}</Link> : "—"],
            ]}
          />
          {d.context_window && (
            <KV
              rows={[
                ["Messages in state", `${d.context_window.messages_in_state} / ${d.context_window.max_messages}`],
                ["State token estimate", `${fmtNum(d.context_window.token_estimate)} (${d.context_window.token_estimate_method})`],
                ["Trimming", d.context_window.trimming],
              ]}
            />
          )}
        </CardBody>
      </Card>
      {d.messages.length > 0 && (
        <Card>
          <CardHeader title="Messages" description="Platform mesaj kaydı; context_status: mesaj state penceresinde mi" />
          <TableWrap maxH="max-h-[50vh]">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Type</th>
                  <th>Content</th>
                  <th className="text-right">Tokens</th>
                  <th>Context</th>
                  <th>Links</th>
                </tr>
              </thead>
              <tbody>
                {d.messages.map((m) => (
                  <tr key={m.message_id}>
                    <td className="text-xs whitespace-nowrap">{fmtTime(m.created_at)}</td>
                    <td className="text-xs">{m.type}</td>
                    <td className="max-w-xl text-xs break-words text-ink-2">{m.content.length > 400 ? `${m.content.slice(0, 400)}…` : m.content}</td>
                    <td className="text-right text-xs tabular-nums">{m.token_estimate}</td>
                    <td className="text-xs text-muted">{m.context_status}</td>
                    <td className="space-x-2 text-xs whitespace-nowrap">
                      {m.agent_trace_run && (
                        <Link className="text-accent hover:underline" to={`/agent-trace/${m.agent_trace_run}`}>
                          Agent Trace
                        </Link>
                      )}
                      {m.langsmith_url && (
                        <a className="text-accent hover:underline" href={m.langsmith_url} target="_blank" rel="noreferrer">
                          LangSmith
                        </a>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </Card>
      )}
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Conversation Summary" description="Digest'ler (LLM çağrısız tur özetleri)" />
          <CardBody>{d.conversation_summary?.length ? <JsonView value={d.conversation_summary} open={1} maxH="max-h-80" /> : <div className="text-xs text-muted">Özet yok</div>}</CardBody>
        </Card>
        <Card>
          <CardHeader title="Current Context" />
          <CardBody>
            <JsonView value={d.current_context ?? {}} open={1} maxH="max-h-80" />
          </CardBody>
        </Card>
        {d.tool_calls && (
          <Card>
            <CardHeader title="Tool Calls" description={d.tool_calls_note} />
            <TableWrap maxH="max-h-72">
              <table className="data-table">
                <tbody>
                  {d.tool_calls.map((t) => (
                    <tr key={t.id}>
                      <td className="text-xs">{t.tool}</td>
                      <td className="text-xs">{t.outcome ?? t.status}</td>
                      <td className="text-right text-xs tabular-nums">{ms(t.duration_ms)}</td>
                      <td className="text-xs">
                        <Link className="text-accent hover:underline" to={`/mcp-calls?call=${t.id}`}>
                          detay
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          </Card>
        )}
        {d.system_messages && (
          <Card>
            <CardHeader title="System Messages" description={d.system_messages_note ?? undefined} />
            <CardBody className="space-y-2">
              {d.system_messages.map((s) => (
                <Disclosure key={s.name} title={s.name} meta={`${fmtNum(s.length)} karakter`}>
                  {s.content ? <pre className="max-h-64 overflow-auto text-2xs whitespace-pre-wrap">{s.content}</pre> : <span className="text-xs text-muted">gizli</span>}
                </Disclosure>
              ))}
            </CardBody>
          </Card>
        )}
      </div>
    </div>
  );
}

// ================================================================================================= Long-term
const VER_LABEL: Record<string, string> = { verified: "verified", unverified: "unverified", rejected: "rejected", not_applicable: "—" };

function LongTermTab({ agent }: { agent: string }) {
  const { can } = useAuth();
  const [type, setType] = useState<MemoryType | "">("");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("active");
  const [ver, setVer] = useState("");
  const [backing, setBacking] = useState("");
  const [page, setPage] = useState(1);
  const [sel, setSel] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const rec = useMemRecords(agent, { memory_type: type || undefined, q: q || undefined, status, verification: ver || undefined, backing: backing || undefined, page, size: 50 });
  const appr = useMemApprovals();
  const doExport = async () => {
    try {
      const data = await api.get<unknown>(exportUrl(agent, type || undefined));
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      a.download = `agent-memory-${agent}-${type || "all"}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      errorToast(e, "Export başarısız");
    }
  };
  return (
    <div className="space-y-4">
      {!!appr.data?.items.length && (
        <Notice tone="warning">
          {appr.data.items.length} procedural memory önerisi onay bekliyor (four-eyes: öneren onaylayamaz).{" "}
          {appr.data.items.slice(0, 3).map((a) => (
            <button key={a.id} type="button" className="mr-2 underline" onClick={() => setSel(a.memory_id!)}>
              {a.title}
            </button>
          ))}
        </Notice>
      )}
      <Card>
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
          <SearchInput value={q} onChange={(v) => (setQ(v), setPage(1))} placeholder="Başlık / içerik ara…" className="w-64" />
          <Select aria-label="Memory type" value={type} onChange={(e) => (setType(e.target.value as MemoryType | ""), setPage(1))} className="w-40">
            <option value="">Tüm türler</option>
            <option value="semantic">Semantic</option>
            <option value="episodic">Episodic</option>
            <option value="procedural">Procedural</option>
          </Select>
          <Select aria-label="Status" value={status} onChange={(e) => (setStatus(e.target.value), setPage(1))} className="w-32">
            <option value="active">active</option>
            <option value="archived">archived</option>
            <option value="expired">expired</option>
            <option value="all">all</option>
          </Select>
          <Select aria-label="Verification" value={ver} onChange={(e) => (setVer(e.target.value), setPage(1))} className="w-36">
            <option value="">Tüm doğrulamalar</option>
            <option value="verified">verified</option>
            <option value="unverified">unverified</option>
            <option value="rejected">rejected</option>
          </Select>
          <Select aria-label="Backing" value={backing} onChange={(e) => (setBacking(e.target.value), setPage(1))} className="w-48">
            <option value="">Tüm kaynaklar</option>
            {["sre.memory_records", "sre.known_issues", "sre.runbooks", "sre.agent_episodes", "langgraph.store"].map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </Select>
          <div className="ml-auto flex items-center gap-2">
            <span className="text-xs text-muted tabular-nums">{rec.data?.total ?? 0} kayıt</span>
            {can("admin") && (
              <Button size="sm" variant="outline" onClick={doExport}>
                <Download /> Export
              </Button>
            )}
            {can("sre") && (
              <Button size="sm" variant="primary" onClick={() => setCreating(true)}>
                <Plus /> New memory
              </Button>
            )}
          </div>
        </div>
        {rec.error ? (
          <ErrorState error={rec.error} />
        ) : rec.isLoading ? (
          <TableSkeleton rows={8} cols={8} />
        ) : !rec.data?.items.length ? (
          <EmptyState title="Filtreyle eşleşen kayıt yok" />
        ) : (
          <TableWrap fetching={rec.isFetching}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Type</th>
                  <th>Namespace</th>
                  <th>Source</th>
                  <th>Verification</th>
                  <th>Approval</th>
                  <th>Status</th>
                  <th>Updated</th>
                  <th className="text-right">Version</th>
                </tr>
              </thead>
              <tbody>
                {rec.data.items.map((r) => (
                  <tr key={r.memory_id} onClick={() => setSel(r.memory_id)} className="cursor-pointer">
                    <td className="max-w-md">
                      <div className="truncate text-xs text-ink">{r.title}</div>
                      <Mono className="text-2xs text-muted">{short(r.memory_id, 22)}</Mono>
                    </td>
                    <td className="text-xs">{r.memory_type}</td>
                    <td className="font-mono text-2xs text-ink-2">{r.namespace}</td>
                    <td className="text-xs">
                      {r.source}
                      {r.source_trust === "untrusted" && <Badge className="ml-1">untrusted</Badge>}
                    </td>
                    <td>
                      <StatusDot status={VER_LABEL[r.verification_status] ?? r.verification_status} />
                    </td>
                    <td>
                      <StatusDot status={r.approval_status} />
                    </td>
                    <td>
                      <StatusDot status={r.status} />
                    </td>
                    <td className="text-xs whitespace-nowrap">{fmtTime(r.updated_at ?? r.created_at)}</td>
                    <td className="text-right text-xs tabular-nums">{r.version ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
        {rec.data && rec.data.pages > 1 && <Pagination page={page} pages={rec.data.pages} total={rec.data.total} onPage={setPage} />}
        {rec.data && <div className="border-t border-line px-3 py-1.5 text-2xs text-muted">{rec.data.note}</div>}
      </Card>
      {sel && <RecordDialog agent={agent} id={sel} onClose={() => setSel(null)} />}
      {creating && <CreateDialog agent={agent} onClose={() => setCreating(false)} />}
    </div>
  );
}

function PolicyErrors({ error }: { error: unknown }) {
  if (!(error instanceof ApiError)) return error ? <Notice tone="critical">{String(error)}</Notice> : null;
  const det = (Array.isArray(error.details) ? error.details : []) as { code?: string; detail?: string }[];
  return (
    <Notice tone="critical">
      <div className="font-medium">{error.message}</div>
      {det.map((d, i) => (
        <div key={i} className="text-xs">
          {d.code}: {d.detail}
        </div>
      ))}
    </Notice>
  );
}

function CreateDialog({ agent, onClose }: { agent: string; onClose: () => void }) {
  const create = useCreateRecord(agent);
  const [f, setF] = useState({ memory_type: "semantic" as MemoryType, title: "", content: "", category: "", source: "manual", tags: "", evidence: "", confidence: "" });
  const submit = () =>
    create.mutate(
      {
        memory_type: f.memory_type,
        title: f.title,
        content: f.content,
        category: f.category || undefined,
        source: f.source,
        tags: f.tags.split(",").map((x) => x.trim()).filter(Boolean),
        evidence_references: f.evidence.split(",").map((x) => x.trim()).filter(Boolean),
        confidence_score: f.confidence ? Number(f.confidence) : undefined,
      },
      { onSuccess: onClose },
    );
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="New memory"
      description="Kayıt policy'den geçer: secret, prompt injection ve poisoning kontrolleri; PII redaction. Procedural kayıt onaya gönderilir."
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Vazgeç
          </Button>
          <Button variant="primary" loading={create.isPending} disabled={!f.title.trim() || !f.content.trim()} onClick={submit}>
            Kaydet
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-3">
          <FormField label="Memory type">
            <Select value={f.memory_type} onChange={(e) => setF({ ...f, memory_type: e.target.value as MemoryType })}>
              <option value="semantic">Semantic</option>
              <option value="procedural">Procedural (onaylı runbook)</option>
            </Select>
          </FormField>
          <FormField label="Category">
            <Input value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })} placeholder="topology" />
          </FormField>
          <FormField label="Source">
            <Select value={f.source} onChange={(e) => setF({ ...f, source: e.target.value })}>
              {["manual", "import", "agent_extraction", "mcp", "log", "external"].map((s) => (
                <option key={s}>{s}</option>
              ))}
            </Select>
          </FormField>
        </div>
        <FormField label="Title">
          <Input value={f.title} maxLength={300} onChange={(e) => setF({ ...f, title: e.target.value })} />
        </FormField>
        <FormField label="Content">
          <Textarea rows={6} maxLength={8000} value={f.content} onChange={(e) => setF({ ...f, content: e.target.value })} />
        </FormField>
        <div className="grid gap-3 sm:grid-cols-3">
          <FormField label="Tags (virgülle)">
            <Input value={f.tags} onChange={(e) => setF({ ...f, tags: e.target.value })} />
          </FormField>
          <FormField label="Evidence references">
            <Input value={f.evidence} onChange={(e) => setF({ ...f, evidence: e.target.value })} placeholder="inv:<id>" />
          </FormField>
          <FormField label="Confidence (0–1)">
            <Input type="number" min={0} max={1} step={0.05} value={f.confidence} onChange={(e) => setF({ ...f, confidence: e.target.value })} />
          </FormField>
        </div>
        <PolicyErrors error={create.error} />
      </div>
    </Dialog>
  );
}

function RecordDialog({ agent, id, onClose }: { agent: string; id: string; onClose: () => void }) {
  const { can } = useAuth();
  const r = useMemRecord(agent, id);
  const vers = useMemVersions(agent, id);
  const op = useRecordOp(agent);
  const del = useDeleteRecord(agent);
  const upd = useUpdateRecord(agent);
  const [edit, setEdit] = useState<{ title: string; content: string } | null>(null);
  const [confirm, setConfirm] = useState("");
  const d = r.data;
  const act = (o: string, version_id?: string) => op.mutate({ id, op: o, version_id });
  const has = (a: string) => d?.actions.includes(a);
  return (
    <Dialog open wide onOpenChange={(o) => !o && onClose()} title={d?.title ?? "Memory kaydı"} description={d ? `${d.memory_type} · ${d.backing}` : undefined}>
      {r.error ? (
        <ErrorState error={r.error} />
      ) : !d ? (
        <TableSkeleton rows={6} cols={2} />
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2">
            {can("sre") && has("update") && (
              <Button size="sm" variant="outline" onClick={() => setEdit({ title: d.title, content: d.content })}>
                Düzenle
              </Button>
            )}
            {can("sre") && has("propose_update") && (
              <Button size="sm" variant="outline" onClick={() => setEdit({ title: d.title, content: d.content })}>
                Değişiklik öner
              </Button>
            )}
            {can("sre") && has("verify") && (
              <Button size="sm" variant="outline" onClick={() => act("verify")}>
                <ShieldCheck /> Verify
              </Button>
            )}
            {can("sre") && has("reject_verification") && (
              <Button size="sm" variant="outline" onClick={() => act("reject_verification")}>
                <X /> Reject
              </Button>
            )}
            {can("sre") && has("archive") && (
              <Button size="sm" variant="outline" onClick={() => act("archive")}>
                <Archive /> Archive
              </Button>
            )}
            {can("sre") && has("restore") && (
              <Button size="sm" variant="outline" onClick={() => act("restore")}>
                <RotateCcw /> Restore
              </Button>
            )}
          </div>
          {edit && (
            <Card>
              <CardBody className="space-y-2">
                <FormField label="Title">
                  <Input value={edit.title} onChange={(e) => setEdit({ ...edit, title: e.target.value })} />
                </FormField>
                <FormField label="Content">
                  <Textarea rows={6} value={edit.content} onChange={(e) => setEdit({ ...edit, content: e.target.value })} />
                </FormField>
                <PolicyErrors error={upd.error} />
                <div className="flex justify-end gap-2">
                  <Button size="sm" variant="outline" onClick={() => setEdit(null)}>
                    Vazgeç
                  </Button>
                  <Button
                    size="sm"
                    variant="primary"
                    loading={upd.isPending}
                    onClick={() => upd.mutate({ id, body: { title: edit.title, content: edit.content, version: d.version } }, { onSuccess: () => setEdit(null) })}
                  >
                    {d.memory_type === "procedural" ? "Onaya gönder" : "Kaydet"}
                  </Button>
                </div>
              </CardBody>
            </Card>
          )}
          {d.pending_revisions && d.pending_revisions.length > 0 && (
            <Card>
              <CardHeader title="Onay bekleyen öneriler" description="Canlı runbook onaylanana kadar değişmez" />
              <CardBody className="space-y-2">
                {d.pending_revisions.map((v) => (
                  <div key={v.id} className="rounded border border-line p-2 text-xs">
                    <div className="mb-1 flex items-center justify-between">
                      <span>
                        v{v.version} · {v.change} · öneren {v.actor} · {fmtTime(v.created_at)}
                      </span>
                      {can("admin") && (
                        <span className="flex gap-1">
                          <Button size="sm" variant="primary" onClick={() => act("approve", v.id)}>
                            <Check /> Approve
                          </Button>
                          <Button size="sm" variant="outline" onClick={() => act("reject", v.id)}>
                            <X /> Reject
                          </Button>
                        </span>
                      )}
                    </div>
                    <JsonView value={v.snapshot} open={1} maxH="max-h-48" />
                  </div>
                ))}
              </CardBody>
            </Card>
          )}
          <div className="grid gap-4 md:grid-cols-2">
            <KV
              rows={[
                ["Memory ID", <Mono>{d.memory_id}</Mono>],
                ["Tenant / Agent", `${d.tenant_id} / ${d.agent_id}`],
                ["Namespace", <Mono>{d.namespace}</Mono>],
                ["Memory type", d.memory_type],
                ["Category", d.category],
                ["Source", `${d.source} (${d.source_trust})`],
                ["Source run / thread", d.source_run_id ? `${d.source_run_id} / ${d.source_thread_id ?? "—"}` : "—"],
                ["Evidence references", d.evidence_references.join(", ") || "—"],
                ["Confidence score", d.confidence_score ?? "—"],
              ]}
            />
            <KV
              rows={[
                ["Verification", <StatusDot status={d.verification_status} />],
                ["Approval", <StatusDot status={d.approval_status} />],
                ["Status", <StatusDot status={d.status} />],
                ["Created", `${fmtTime(d.created_at)}${d.created_by ? ` · ${d.created_by}` : ""}`],
                ["Updated", fmtTime(d.updated_at)],
                ["Last accessed", fmtTime(d.last_accessed_at)],
                ["Access count", d.access_count ?? "—"],
                ["Expires", fmtTime(d.expires_at)],
                ["Version", d.version ?? "—"],
                ["Embedding", `${d.embedding_status}${d.embedding_model ? ` · ${d.embedding_model}` : ""}`],
                ["Tags", d.tags.join(", ") || "—"],
              ]}
            />
          </div>
          <Card>
            <CardHeader title="Content" />
            <CardBody>
              <pre className="text-xs whitespace-pre-wrap text-ink-2">{d.content}</pre>
            </CardBody>
          </Card>
          {Object.keys(d.structured_data ?? {}).length > 0 && <JsonView title="Structured data" value={d.structured_data} open={1} maxH="max-h-64" />}
          {Object.keys(d.metadata ?? {}).length > 0 && <JsonView title="Metadata" value={d.metadata} open={1} maxH="max-h-48" />}
          <Card>
            <CardHeader title="Version history" description="Değişmez geçmiş (silme anı dahil)" />
            {!vers.data?.items.length ? (
              <div className="px-3 py-3 text-xs text-muted">Versiyon kaydı yok</div>
            ) : (
              <table className="data-table">
                <tbody>
                  {vers.data.items.map((v) => (
                    <tr key={v.id}>
                      <td className="text-xs tabular-nums">v{v.version}</td>
                      <td className="text-xs">{v.change}</td>
                      <td>
                        <StatusDot status={v.status} />
                      </td>
                      <td className="text-xs">{v.actor}</td>
                      <td className="text-xs">{fmtTime(v.created_at)}</td>
                      <td className="text-xs text-muted">{v.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
          {can("admin") && d.backing === "sre.memory_records" && (
            <Card>
              <CardHeader title="Kalıcı silme" description="Geri döndürülemez. Versiyon geçmişi korunur. Onay için kayıt kimliğini aynen yazın." />
              <CardBody className="flex flex-wrap items-end gap-2">
                <div className="min-w-72 flex-1">
                  <Label htmlFor="mem-del">Memory ID</Label>
                  <Input id="mem-del" value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder={d.memory_id} />
                </div>
                <Button variant="danger" size="sm" disabled={confirm !== d.memory_id} loading={del.isPending} onClick={() => del.mutate(id, { onSuccess: onClose })}>
                  <Trash2 /> Sil
                </Button>
              </CardBody>
            </Card>
          )}
        </div>
      )}
    </Dialog>
  );
}

// ================================================================================================= Checkpoints
function CheckpointsTab({ agent }: { agent: string }) {
  const [sp, setSp] = useSearchParams();
  const thread = sp.get("thread");
  const [cp, setCp] = useState<string | null>(null);
  const [cmp, setCmp] = useState<string>("");
  const threads = useMemThreads(agent, { page: 1, size: 100 });
  const list = useMemCheckpoints(thread);
  const det = useMemCheckpoint(thread, cp);
  const compare = useMemCompare(thread, cp, cmp || null);
  useEffect(() => setCp(null), [thread]);
  const setThread = (t: string) =>
    setSp(
      (p) => {
        const n = new URLSearchParams(p);
        n.set("thread", t);
        return n;
      },
      { replace: true },
    );
  return (
    <div className="space-y-4">
      <Notice tone="info">
        Checkpoint Explorer salt okunurdur. Replay, fork ve resume sunulmaz: bir graph adımını yeniden çalıştırmak MCP ve Slack gibi dış sistemlerde yan etki oluşturabilir.
      </Notice>
      <Card>
        <div className="flex flex-wrap items-center gap-2 px-3 py-2">
          <Label htmlFor="cp-thread">Thread</Label>
          <Select id="cp-thread" value={thread ?? ""} onChange={(e) => setThread(e.target.value)} className="w-[32rem] max-w-full">
            <option value="">Thread seçin…</option>
            {(threads.data?.items ?? []).map((t) => (
              <option key={t.thread_id} value={t.thread_id}>
                {t.thread_id} · {t.execution_type} · {t.checkpoint_count} checkpoint
              </option>
            ))}
          </Select>
          {list.data && (
            <span className="text-xs text-muted">
              graph {list.data.graph ?? "bilinmiyor"} · backend {list.data.storage_backend}
            </span>
          )}
        </div>
      </Card>
      {thread && (
        <div className="grid gap-4 xl:grid-cols-[28rem_1fr]">
          <Card>
            <CardHeader title="Checkpoint timeline" description={list.data?.next_nodes_note ?? "en yeni üstte"} />
            {list.error ? (
              <ErrorState error={list.error} />
            ) : list.isLoading ? (
              <TableSkeleton rows={8} cols={3} />
            ) : (
              <TableWrap maxH="max-h-[65vh]">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Step</th>
                      <th>Checkpoint</th>
                      <th>Source</th>
                      <th>Next</th>
                    </tr>
                  </thead>
                  <tbody>
                    {list.data?.items.map((c) => (
                      <tr key={`${c.checkpoint_ns}:${c.checkpoint_id}`} onClick={() => setCp(c.checkpoint_id)} className={cn("cursor-pointer", cp === c.checkpoint_id && "bg-surface-2")}>
                        <td className="text-xs tabular-nums">{c.step ?? "—"}</td>
                        <td>
                          <Mono className="text-2xs">{short(c.checkpoint_id, 20)}</Mono>
                          <div className="text-2xs text-muted">{fmtTime(c.created_at)}</div>
                        </td>
                        <td className="text-xs">{c.source ?? "—"}</td>
                        <td className="text-xs">{c.next?.join(", ") || (c.next ? "END" : "—")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}
          </Card>
          <div className="min-w-0 space-y-4">
            {!cp ? (
              <EmptyState title="Bir checkpoint seçin" icon={<Database />} />
            ) : det.error ? (
              <ErrorState error={det.error} />
            ) : !det.data ? (
              <TableSkeleton rows={6} cols={2} />
            ) : (
              <>
                <Card>
                  <CardHeader title="Checkpoint" />
                  <CardBody>
                    <KV
                      rows={[
                        ["Checkpoint ID", <Mono>{det.data.checkpoint_id}</Mono>],
                        ["Namespace", det.data.checkpoint_ns || "(root)"],
                        ["Parent", det.data.parent_checkpoint_id ? <button type="button" className="font-mono text-accent hover:underline" onClick={() => setCp(det.data!.parent_checkpoint_id)}>{det.data.parent_checkpoint_id}</button> : "—"],
                        ["Next checkpoint", det.data.next_checkpoint_id ? <button type="button" className="font-mono text-accent hover:underline" onClick={() => setCp(det.data!.next_checkpoint_id)}>{det.data.next_checkpoint_id}</button> : "—"],
                        ["Children (fork)", det.data.children.length > 1 ? det.data.children.join(", ") : det.data.children.length ? "1" : "—"],
                        ["Subgraph namespaces", det.data.subgraph_namespaces.join(", ") || "—"],
                        ["Step / Source", `${det.data.step ?? "—"} / ${det.data.source ?? "—"}`],
                        ["Created", fmtTime(det.data.created_at)],
                        ["Next nodes", det.data.next_nodes ? det.data.next_nodes.join(", ") || "END" : "gözlemlenemedi"],
                        ["Storage backend", det.data.storage_backend],
                        ["Size", `${bytes(det.data.size_bytes)}${det.data.truncated ? " (kısaltıldı)" : ""}`],
                      ]}
                    />
                  </CardBody>
                </Card>
                <Card>
                  <CardHeader title="State diff (parent → bu checkpoint)" />
                  <CardBody>
                    <Diff diff={det.data.diff} />
                  </CardBody>
                </Card>
                <Card>
                  <CardHeader
                    title="Compare"
                    actions={
                      <Select aria-label="Karşılaştırılacak checkpoint" value={cmp} onChange={(e) => setCmp(e.target.value)} className="w-72">
                        <option value="">Checkpoint seçin…</option>
                        {list.data?.items
                          .filter((c) => c.checkpoint_id !== cp)
                          .map((c) => (
                            <option key={c.checkpoint_id} value={c.checkpoint_id}>
                              step {c.step ?? "—"} · {short(c.checkpoint_id, 18)}
                            </option>
                          ))}
                      </Select>
                    }
                  />
                  <CardBody>{cmp ? compare.data ? <Diff diff={compare.data.diff} /> : <TableSkeleton rows={3} cols={4} /> : <div className="text-xs text-muted">İki checkpoint arasındaki state farkı</div>}</CardBody>
                </Card>
                <JsonView title="State values" value={det.data.channel_values} open={1} />
                <div className="grid gap-4 xl:grid-cols-2">
                  <JsonView title="Metadata" value={det.data.metadata} open={1} maxH="max-h-72" />
                  <JsonView title="Channel versions" value={det.data.channel_versions} open={1} maxH="max-h-72" />
                </div>
                {det.data.pending_writes.length > 0 && <JsonView title="Pending writes" value={det.data.pending_writes} open={1} maxH="max-h-72" />}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ================================================================================================= Retrieval
function RetrievalTab({ agent, range }: { agent: string; range: MemRange }) {
  const [origin, setOrigin] = useState("");
  const [page, setPage] = useState(1);
  const [sel, setSel] = useState<string | null>(null);
  const list = useMemRetrievals(agent, { origin: origin || undefined, range, page, size: 50 });
  return (
    <div className="space-y-4">
      <Playground agent={agent} />
      <Card>
        <CardHeader
          title="Retrieval history"
          description="Agent'ın gerçek retrieval işlemleri: pipeline aşamaları, seçilen / dışlanan kayıtlar ve context injection"
          actions={
            <Select aria-label="Origin" value={origin} onChange={(e) => (setOrigin(e.target.value), setPage(1))} className="w-36">
              <option value="">Tüm kaynaklar</option>
              <option value="agent">agent (chat)</option>
              <option value="analysis">analysis</option>
            </Select>
          }
        />
        {list.error ? (
          <ErrorState error={list.error} />
        ) : list.isLoading ? (
          <TableSkeleton rows={6} cols={8} />
        ) : !list.data?.items.length ? (
          <EmptyState title="Bu aralıkta retrieval yok" />
        ) : (
          <TableWrap fetching={list.isFetching} maxH="max-h-[50vh]">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Origin</th>
                  <th>Strategy</th>
                  <th>Run</th>
                  <th className="text-right">Top K</th>
                  <th className="text-right">Returned</th>
                  <th className="text-right">Latency</th>
                  <th>Injection</th>
                  <th className="text-right">Tokens</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((r) => (
                  <tr key={r.retrieval_id} onClick={() => setSel(r.retrieval_id)} className="cursor-pointer">
                    <td className="text-xs whitespace-nowrap">{fmtTime(r.ts)}</td>
                    <td className="text-xs">{r.origin}</td>
                    <td className="text-xs">{r.strategy}</td>
                    <td className="font-mono text-2xs">{short(r.run_id, 16)}</td>
                    <td className="text-right text-xs tabular-nums">{r.top_k ?? "—"}</td>
                    <td className="text-right text-xs tabular-nums">{r.returned_count}</td>
                    <td className="text-right text-xs tabular-nums">{ms(r.duration_ms)}</td>
                    <td className="text-xs">{r.injection_status ?? "—"}</td>
                    <td className="text-right text-xs tabular-nums">{r.injected_tokens ?? "—"}</td>
                    <td>
                      <StatusDot status={r.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
        {list.data && list.data.pages > 1 && <Pagination page={page} pages={list.data.pages} total={list.data.total} onPage={setPage} />}
      </Card>
      {sel && <RetrievalDialog id={sel} onClose={() => setSel(null)} />}
    </div>
  );
}

function Playground({ agent }: { agent: string }) {
  const strat = useMemStrategies();
  const pg = usePlayground();
  const [f, setF] = useState({ query: "", strategy: "keyword", types: ["semantic", "episodic", "procedural"] as MemoryType[], top_k: 5, threshold: "", since: "", namespace: "" });
  const toggle = (t: MemoryType) => setF({ ...f, types: f.types.includes(t) ? f.types.filter((x) => x !== t) : [...f.types, t] });
  const run = () =>
    pg.mutate({
      agent,
      query: f.query,
      strategy: f.strategy,
      memory_types: f.types,
      top_k: f.top_k,
      similarity_threshold: f.threshold ? Number(f.threshold) : null,
      since_days: f.since ? Number(f.since) : null,
      namespace: f.namespace || null,
    });
  return (
    <Card>
      <CardHeader title="Retrieval Playground" description="Salt okunur deneme: agent state'i, access_count ve memory olayları değişmez" />
      <CardBody className="space-y-3">
        <div className="grid gap-3 md:grid-cols-[1fr_14rem_6rem]">
          <FormField label="Query">
            <Input value={f.query} onChange={(e) => setF({ ...f, query: e.target.value })} onKeyDown={(e) => e.key === "Enter" && f.query && run()} placeholder="ör. reservation pool" />
          </FormField>
          <FormField label="Retrieval strategy">
            <Select value={f.strategy} onChange={(e) => setF({ ...f, strategy: e.target.value })}>
              {(strat.data?.implemented ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
              {(strat.data?.not_implemented ?? []).map((s) => (
                <option key={s.id} value={s.id} disabled>
                  {s.label}
                </option>
              ))}
            </Select>
          </FormField>
          <FormField label="Top K">
            <Input type="number" min={1} max={50} value={f.top_k} onChange={(e) => setF({ ...f, top_k: Number(e.target.value) || 5 })} />
          </FormField>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex gap-2 text-xs">
            {(["semantic", "episodic", "procedural"] as MemoryType[]).map((t) => (
              <label key={t} className="inline-flex items-center gap-1">
                <input type="checkbox" checked={f.types.includes(t)} onChange={() => toggle(t)} /> {t}
              </label>
            ))}
          </div>
          <FormField label="Similarity threshold (fingerprint)" className="w-52">
            <Input type="number" min={0} max={1} step={0.05} value={f.threshold} onChange={(e) => setF({ ...f, threshold: e.target.value })} placeholder="0.35" />
          </FormField>
          <FormField label="Son N gün" className="w-28">
            <Input type="number" min={1} value={f.since} onChange={(e) => setF({ ...f, since: e.target.value })} />
          </FormField>
          <FormField label="Namespace" className="w-64">
            <Input value={f.namespace} onChange={(e) => setF({ ...f, namespace: e.target.value })} placeholder="sre-agent/semantic/topology" />
          </FormField>
          <Button variant="primary" disabled={!f.query || !f.types.length} loading={pg.isPending} onClick={run}>
            Çalıştır
          </Button>
        </div>
        <PolicyErrors error={pg.error} />
        {pg.data && (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-4 text-xs text-muted">
              <span>Latency {ms(pg.data.latency_ms)}</span>
              <span>
                Context token estimate {fmtNum(pg.data.context_token_estimate)} ({pg.data.token_estimate_method})
              </span>
              <span>{pg.data.note}</span>
            </div>
            {!pg.data.results.length ? (
              <EmptyState title="Sonuç yok" />
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Title</th>
                    <th>Type</th>
                    <th className="text-right">Score</th>
                    <th>Score kind</th>
                    <th>Verification</th>
                  </tr>
                </thead>
                <tbody>
                  {pg.data.results.map((r, i) => (
                    <tr key={r.memory_id}>
                      <td className="text-xs tabular-nums">{i + 1}</td>
                      <td className="text-xs">
                        {r.title}
                        <Mono className="block text-2xs text-muted">{r.memory_id}</Mono>
                      </td>
                      <td className="text-xs">{r.memory_type}</td>
                      <td className="text-right text-xs tabular-nums">{r.similarity ?? r.rank ?? "—"}</td>
                      <td className="text-xs text-muted">{r.score_kind}</td>
                      <td className="text-xs">{r.verification_status ?? r.outcome ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <Disclosure title="Pipeline" meta={`${pg.data.stages.length} aşama`}>
              <JsonView value={pg.data.stages} open={2} maxH="max-h-72" />
            </Disclosure>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function RetrievalDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const { data: d, error } = useMemRetrieval(id);
  return (
    <Dialog open wide onOpenChange={(o) => !o && onClose()} title="Retrieval" description={d ? `${d.origin} · ${d.strategy} · ${fmtTime(d.ts)}` : undefined}>
      {error ? (
        <ErrorState error={error} />
      ) : !d ? (
        <TableSkeleton rows={6} cols={2} />
      ) : (
        <div className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <KV
              rows={[
                ["Retrieval ID", <Mono>{d.retrieval_id}</Mono>],
                ["Run / Thread", `${d.run_id ?? "—"} / ${d.thread_id ?? "—"}`],
                ["Node", d.node],
                ["Query", d.query],
                ["Namespaces", d.namespaces?.join(", ")],
                ["Memory types", d.memory_types?.join(", ")],
              ]}
            />
            <KV
              rows={[
                ["Top K", d.top_k],
                ["Returned", d.returned_count],
                ["Latency", ms(d.duration_ms)],
                ["Status", <StatusDot status={d.status} />],
                ["Injection", d.injection_status],
                ["Query embedding", `${d.query_embedding.status} — ${d.query_embedding.note}`],
              ]}
            />
          </div>
          {d.run_id && (
            <Link className="inline-flex items-center gap-1 text-xs text-accent hover:underline" to={`/agent-trace/${d.run_id}`}>
              Agent Trace <ExternalLink className="size-3" />
            </Link>
          )}
          <Card>
            <CardHeader title="Pipeline" description="Query → Namespace Filter → Metadata Filter → Search → Ranking → Validation → Context Injection" />
            <CardBody className="space-y-2">
              {(d.stages ?? []).map((s, i) => (
                <Disclosure key={i} title={`${i + 1}. ${s.stage}`} meta={s.duration_ms != null ? ms(s.duration_ms) : undefined} defaultOpen={i === 0}>
                  <JsonView value={s} open={2} maxH="max-h-72" />
                </Disclosure>
              ))}
            </CardBody>
          </Card>
          <div className="grid gap-4 xl:grid-cols-2">
            <JsonView title="Selected" value={d.selected ?? []} open={2} maxH="max-h-80" />
            <JsonView title="Excluded" value={d.excluded ?? []} open={2} maxH="max-h-80" />
          </div>
          <JsonView title="Context injection" value={d.injection ?? {}} open={2} maxH="max-h-80" />
          <EventsTable rows={d.events} onSelect={() => undefined} />
        </div>
      )}
    </Dialog>
  );
}

// ================================================================================================= Activity
function EventsTable({ rows, onSelect }: { rows: MemoryEventRow[]; onSelect: (id: string) => void }) {
  if (!rows.length) return <EmptyState title="Olay yok" />;
  return (
    <TableWrap maxH="max-h-[60vh]">
      <table className="data-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Event</th>
            <th>Memory type</th>
            <th>Namespace</th>
            <th>Node</th>
            <th>Run / Thread</th>
            <th className="text-right">Duration</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.event_id} onClick={() => onSelect(e.event_id)} className="cursor-pointer">
              <td className="text-xs whitespace-nowrap">{fmtTime(e.ts)}</td>
              <td className="font-mono text-2xs">{e.event_type}</td>
              <td className="text-xs">{e.memory_type ?? "—"}</td>
              <td className="font-mono text-2xs text-ink-2">{e.namespace ?? "—"}</td>
              <td className="text-xs">{e.node ?? "—"}</td>
              <td className="font-mono text-2xs">{short(e.run_id ?? e.thread_id, 18)}</td>
              <td className="text-right text-xs tabular-nums">{ms(e.duration_ms)}</td>
              <td>
                <StatusDot status={e.status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

function ActivityTab({ agent, range }: { agent: string; range: MemRange }) {
  const [type, setType] = useState("");
  const [status, setStatus] = useState("");
  const [run, setRun] = useState("");
  const [page, setPage] = useState(1);
  const [sel, setSel] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const [liveRows, setLiveRows] = useState<MemoryEventRow[]>([]);
  const [liveState, setLiveState] = useState("closed");
  const ev = useMemEvents(agent, { type: type ? [type] : undefined, status: status || undefined, run_id: run || undefined, range, page, size: 100 });
  useEffect(() => {
    if (!live) return;
    const ac = new AbortController();
    setLiveRows([]);
    streamRun(`/api/memory/events/stream?agent=${encodeURIComponent(agent)}`, {
      signal: ac.signal,
      maxRetries: 20,
      onStatus: (s) => setLiveState(s),
      onEvent: (e) => {
        const r = e as unknown as MemoryEventRow & { type: string };
        if (r.type === "memory_event") setLiveRows((xs) => (xs.some((x) => x.event_id === r.event_id) ? xs : [r, ...xs].slice(0, 500)));
      },
    }).catch(() => setLiveState("closed"));
    return () => ac.abort();
  }, [live, agent]);
  const rows = useMemo(() => {
    const m = new Map<string, MemoryEventRow>();
    for (const r of [...(live ? liveRows : []), ...(ev.data?.items ?? [])]) if (!m.has(r.event_id)) m.set(r.event_id, r);
    return [...m.values()]
      .filter((r) => (!type || r.event_type === type) && (!status || r.status === status) && (!run || r.run_id === run))
      .sort((a, b) => b.ts.localeCompare(a.ts));
  }, [ev.data, liveRows, live, type, status, run]);
  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
          <Select aria-label="Event type" value={type} onChange={(e) => (setType(e.target.value), setPage(1))} className="w-64">
            <option value="">Tüm olay türleri</option>
            {(ev.data?.event_types ?? []).map((t) => (
              <option key={t} value={t} disabled={!!ev.data?.not_emitted[t]}>
                {t}
                {ev.data?.not_emitted[t] ? " (üretilmiyor)" : ""}
              </option>
            ))}
          </Select>
          <Select aria-label="Status" value={status} onChange={(e) => (setStatus(e.target.value), setPage(1))} className="w-32">
            <option value="">Tüm durumlar</option>
            {["ok", "error", "rejected", "pending"].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </Select>
          <Input aria-label="Run ID" value={run} onChange={(e) => (setRun(e.target.value), setPage(1))} placeholder="Run ID" className="w-56" />
          <label className="ml-auto inline-flex items-center gap-1.5 text-xs">
            <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} /> Live
            {live && <span className="text-muted">({liveState})</span>}
          </label>
          <span className="text-xs text-muted tabular-nums">{ev.data?.total ?? 0} olay</span>
        </div>
        {ev.error ? <ErrorState error={ev.error} /> : ev.isLoading ? <TableSkeleton rows={8} cols={8} /> : <EventsTable rows={rows} onSelect={setSel} />}
        {ev.data && ev.data.pages > 1 && <Pagination page={page} pages={ev.data.pages} total={ev.data.total} onPage={setPage} />}
      </Card>
      {ev.data && Object.keys(ev.data.not_emitted).length > 0 && (
        <div className="text-2xs text-muted">
          Üretilmeyen olaylar:{" "}
          {Object.entries(ev.data.not_emitted)
            .map(([k, v]) => `${k} (${v})`)
            .join(" · ")}
        </div>
      )}
      {sel && <EventDialog id={sel} onClose={() => setSel(null)} />}
    </div>
  );
}

function EventDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const { data: e, error } = useMemEvent(id);
  return (
    <Dialog open wide onOpenChange={(o) => !o && onClose()} title={e?.event_type ?? "Memory event"} description={e ? fmtTime(e.ts) : undefined}>
      {error ? (
        <ErrorState error={error} />
      ) : !e ? (
        <TableSkeleton rows={6} cols={2} />
      ) : (
        <div className="space-y-4">
          <KV
            rows={[
              ["Event ID", <Mono>{e.event_id}</Mono>],
              ["Tenant / Agent", `${e.tenant_id} / ${e.agent_id}`],
              ["Run ID", e.run_id ? <Link className="font-mono text-accent hover:underline" to={`/agent-trace/${e.run_id}`}>{e.run_id}</Link> : "—"],
              ["Thread ID", e.thread_id],
              ["Trace ID", e.trace_id],
              ["Node", e.node],
              ["Memory ID", e.memory_id],
              ["Memory type", e.memory_type],
              ["Namespace", e.namespace],
              ["Status", <StatusDot status={e.status} />],
              ["Duration", ms(e.duration_ms)],
              ["Error", e.error],
              ["Correlation ID", e.correlation_id],
              ["Retrieval ID", e.retrieval_id],
              ["Actor", e.actor],
            ]}
          />
          <JsonView title="Payload (maskeli)" value={e.payload ?? {}} open={2} />
        </div>
      )}
    </Dialog>
  );
}

// ================================================================================================= Governance
function GovernanceTab() {
  const { data: d, error, isLoading } = useMemGovernance();
  if (error) return <ErrorState error={error} />;
  if (isLoading || !d) return <TableSkeleton rows={8} cols={3} />;
  return (
    <div className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Controls" description="Uygulanan kontroller (enforced: kapatılamaz)" />
          <table className="data-table">
            <tbody>
              {d.controls.map((c) => (
                <tr key={c.id}>
                  <td className="text-xs">{c.id}</td>
                  <td>
                    <StatusDot status={c.status} />
                  </td>
                  <td className="text-xs text-muted">{c.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card>
          <CardHeader title="Permissions" description={`Onay bekleyen öneri: ${d.pending_approvals}`} />
          <table className="data-table">
            <tbody>
              {d.permissions.map((p) => (
                <tr key={p.permission}>
                  <td className="font-mono text-2xs">{p.permission}</td>
                  <td className="text-xs">{p.roles.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
      <Card>
        <CardHeader title="Policy violations & rejections" description="Son 50; içerik maskeli" />
        <EventsTable rows={d.violations} onSelect={() => undefined} />
      </Card>
      <Card>
        <CardHeader title="Memory audit log" description="Son 50 kayıt (append-only audit tablosu)" />
        {!d.audit.length ? (
          <EmptyState title="Audit kaydı yok" />
        ) : (
          <TableWrap maxH="max-h-[50vh]">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Actor</th>
                  <th>Action</th>
                  <th>Target</th>
                  <th>Result</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {d.audit.map((a, i) => (
                  <tr key={i}>
                    <td className="text-xs whitespace-nowrap">{fmtTime(a.ts)}</td>
                    <td className="text-xs">{a.actor}</td>
                    <td className="font-mono text-2xs">{a.action}</td>
                    <td className="font-mono text-2xs">{a.target}</td>
                    <td>
                      <StatusDot status={a.result} />
                    </td>
                    <td className="text-xs text-muted">{a.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
    </div>
  );
}
