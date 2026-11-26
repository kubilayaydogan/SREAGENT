/** Agent Middleware: kodda kayıtlı middleware'lerin yapılandırması ve execution kayıtları.
 *  Üç kavram ayrı gösterilir: tanım (kod, salt okunur) · yapılandırma (versiyonlu, audit'li) · execution kaydı.
 *  Agent özel LangGraph StateGraph kullanır (create_agent yok); hook'lar gerçek çağrı yollarına adapter ile bağlıdır.
 *  Kaydedilmeyen alan "yakalanmadı", desteklenmeyen alan "desteklenmiyor" yazar; örnek veri gösterilmez. */
import { ArrowDown, ArrowUp, ExternalLink, FlaskConical, Lock, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { RangeTabs, RefreshControl, SearchInput } from "@/components/filters";
import { JsonView } from "@/components/JsonView";
import { MetricStrip, StatTile } from "@/components/StatTile";
import { TimeSeriesChart } from "@/components/charts/TimeSeriesChart";
import { TableWrap } from "@/components/table";
import { Dialog, TabPanel, Tabs } from "@/components/ui/overlay";
import { Badge, Button, Card, CardBody, CardHeader, Input, Mono, Select, Textarea } from "@/components/ui/primitives";
import { EmptyState, ErrorState, Notice, Page, PageHeader, Pagination, TableSkeleton } from "@/components/ui/states";
import { SectionForm } from "@/features/settings/SectionForm";
import {
  useDryRun,
  useMwCatalog,
  useMwDetail,
  useMwExecution,
  useMwExecutions,
  useMwHooks,
  useMwOverview,
  useMwRuns,
  useMwTimeline,
  useReorderMw,
  useUpdateMw,
  type CatalogItem,
  type Decision,
  type Execution,
  type JsonSchema,
  type MwRange,
  type TimelineNode,
} from "@/features/middleware/api";
import { ApiError } from "@/services/api";
import { errorToast } from "@/hooks/queries";
import { DiffTable } from "@/pages/AgentTracePage";
import { cn, fmtDuration, fmtNum, fmtTime } from "@/lib/utils";

const TABS = [
  { value: "overview", label: "Overview" },
  { value: "catalog", label: "Catalog" },
  { value: "executions", label: "Execution Explorer" },
  { value: "timeline", label: "Run Timeline" },
  { value: "dryrun", label: "Dry-run" },
  { value: "settings", label: "Settings" },
];
const RANGES: { value: MwRange; label: string; title: string }[] = [
  { value: "1h", label: "1h", title: "Son 1 saat" },
  { value: "24h", label: "24h", title: "Son 24 saat" },
  { value: "7d", label: "7d", title: "Son 7 gün" },
  { value: "30d", label: "30d", title: "Son 30 gün" },
];
const SERIES = ["var(--color-app-reservation)", "var(--color-app-checkin)", "var(--color-app-flightops)"];
const NOT_CAPTURED = "yakalanmadı";
const ms = (v: number | null | undefined) => (v == null ? NOT_CAPTURED : v < 1 ? `${fmtNum(v, 3)} ms` : fmtDuration(v));
const short = (s: string | null | undefined, n = 8) => (s ? (s.length > n ? `${s.slice(0, n)}…` : s) : "—");

/** Karar: ikon + metin (renk tek başına anlam taşımaz). */
const DECISION: Record<Decision, { label: string; mark: string; tone: string }> = {
  allow: { label: "allow", mark: "✓", tone: "text-st-good-ink" },
  block: { label: "block", mark: "⛔", tone: "text-st-critical-ink" },
  modify: { label: "modify", mark: "✎", tone: "text-st-warning-ink" },
  short_circuit: { label: "short_circuit", mark: "↩", tone: "text-st-warning-ink" },
  not_applicable: { label: "not_applicable", mark: "–", tone: "text-muted" },
};

function DecisionTag({ d }: { d: Decision | null | undefined }) {
  if (!d) return <span className="text-xs text-muted">—</span>;
  const x = DECISION[d];
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap text-xs", x.tone)}>
      <span aria-hidden>{x.mark}</span>
      {x.label}
    </span>
  );
}

function ResultTag({ r }: { r: string | null | undefined }) {
  if (!r) return <span className="text-xs text-muted">—</span>;
  return <span className={cn("text-xs whitespace-nowrap", r === "error" ? "text-st-critical-ink" : "text-ink-2")}>{r === "error" ? "✗ error" : "success"}</span>;
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

function FormField({ label, children, className, hint }: { label: string; children: ReactNode; className?: string; hint?: string }) {
  return (
    <label className={cn("block space-y-1", className)}>
      <span className="text-xs text-muted">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-muted">{hint}</span>}
    </label>
  );
}

function errText(e: unknown) {
  if (e instanceof ApiError) return e.message;
  return e instanceof Error ? e.message : String(e);
}

// ================================================================================================= sayfa
export default function AgentMiddlewarePage() {
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") || "overview";
  const range = (sp.get("range") as MwRange) || "24h";
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
  const ov = useMwOverview(range);
  return (
    <Page wide>
      <PageHeader
        title="Agent Middleware"
        description="Kodda kayıtlı middleware'lerin yapılandırması, LLM ve MCP çağrılarındaki kararları, overhead'i ve run içindeki gerçek yürütme sırası"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <RangeTabs<MwRange> value={range} onChange={(v) => set("range", v)} options={RANGES} />
            <RefreshControl onRefresh={() => void ov.refetch()} fetching={ov.isFetching} updatedAt={ov.dataUpdatedAt} auto="30 sn" />
          </div>
        }
      />
      <Tabs value={tab} onValueChange={(v) => set("tab", v)} tabs={TABS}>
        <TabPanel value="overview">
          <OverviewTab range={range} />
        </TabPanel>
        <TabPanel value="catalog">
          <CatalogTab range={range} open={sp.get("mw")} onOpen={(n) => set("mw", n)} />
        </TabPanel>
        <TabPanel value="executions">
          <ExecutionsTab range={range} sp={sp} set={set} />
        </TabPanel>
        <TabPanel value="timeline">
          <TimelineTab range={range} run={sp.get("run")} onRun={(r) => set("run", r)} />
        </TabPanel>
        <TabPanel value="dryrun">
          <DryRunTab />
        </TabPanel>
        <TabPanel value="settings">
          <SectionForm
            section="agent_middleware"
            title="Middleware Execution Recording"
            description="Varsayılan: yalnız metadata kaydedilir. Payload yakalama açılırsa önceki / sonraki görünüm kayıttan önce maskelenir ve boyutu sınırlanır. Değişiklik versiyonlanır, audit'e yazılır ve bir sonraki run'dan itibaren geçerlidir."
          />
        </TabPanel>
      </Tabs>
    </Page>
  );
}

// ================================================================================================= Overview
function OverviewTab({ range }: { range: MwRange }) {
  const { data: d, isLoading, error, refetch } = useMwOverview(range);
  const hooks = useMwHooks();
  if (error) return <ErrorState error={error} onRetry={() => refetch()} />;
  return (
    <div className="space-y-4">
      <MetricStrip className="grid-cols-2 md:grid-cols-4 xl:grid-cols-8">
        <StatTile loading={isLoading} label="Registered" value={fmtNum(d?.registered)} sub={`${fmtNum(d?.mandatory)} zorunlu`} />
        <StatTile loading={isLoading} label="Enabled" value={fmtNum(d?.enabled)} />
        <StatTile loading={isLoading} label="Executions" value={fmtNum(d?.executions)} sub={`${fmtNum(d?.runs)} run`} />
        <StatTile loading={isLoading} label="Blocked" value={fmtNum(d?.blocked)} />
        <StatTile loading={isLoading} label="Modified" value={fmtNum(d?.modified)} />
        <StatTile loading={isLoading} label="Middleware Errors" value={fmtNum(d?.errors)} />
        <StatTile loading={isLoading} label="Pending Policy Decisions" value="desteklenmiyor" missing sub={d?.pending_policy_note} />
        <StatTile loading={isLoading} label="Wrapped Calls" value={fmtNum(d?.wrapped_calls)} sub="model + tool" />
      </MetricStrip>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Middleware overhead" description="Çağrı başına middleware'lerin toplam kendi işlem süresi (sarılan çağrı hariç)" />
          <CardBody>
            <KV rows={[["p50", ms(d?.overhead_ms.p50)], ["p95", ms(d?.overhead_ms.p95)]]} />
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Wrapped call duration" description="Sarılan LLM / MCP çağrısının kendi süresi (engellenen çağrı ölçülmez)" />
          <CardBody>
            <KV rows={[["p50", ms(d?.wrapped_ms.p50)], ["p95", ms(d?.wrapped_ms.p95)], ["ölçülen çağrı", fmtNum(d?.wrapped_ms.measured)]]} />
          </CardBody>
        </Card>
      </div>
      <Card>
        <CardHeader title="Executions & blocks" description={`Middleware execution'ları (${d?.trend_bucket === "day" ? "günlük" : "saatlik"})`} />
        <CardBody>
          <TimeSeriesChart
            emptyText="Bu aralıkta middleware execution'ı yok"
            format={(v) => fmtNum(v)}
            series={[
              { id: "exec", label: "Executions", color: SERIES[0], points: (d?.trend ?? []).map((r) => ({ t: r.t, v: r.executions })) },
              { id: "blocked", label: "Blocked", color: SERIES[1], points: (d?.trend ?? []).map((r) => ({ t: r.t, v: r.blocked })) },
              { id: "errors", label: "Errors", color: SERIES[2], points: (d?.trend ?? []).map((r) => ({ t: r.t, v: r.errors })) },
            ]}
          />
        </CardBody>
      </Card>
      <HookMap hooks={hooks.data} byHook={d?.by_hook ?? []} />
    </div>
  );
}

function HookMap({ hooks, byHook }: { hooks: ReturnType<typeof useMwHooks>["data"]; byHook: { hook: string; executions: number; overhead_p95_ms: number | null }[] }) {
  if (!hooks) return null;
  const stats = Object.fromEntries(byHook.map((h) => [h.hook, h]));
  return (
    <Card>
      <CardHeader title="Hook noktaları ve sıralama semantiği" description={hooks.framework} />
      <CardBody className="space-y-3">
        <div className="grid gap-2 text-xs md:grid-cols-3">
          <div><span className="text-muted">before_*: </span>{hooks.ordering.before}</div>
          <div><span className="text-muted">wrap_*: </span>{hooks.ordering.wrap}</div>
          <div><span className="text-muted">after_*: </span>{hooks.ordering.after}</div>
        </div>
        <TableWrap maxH="max-h-none">
          <table className="data-table">
            <thead>
              <tr>
                <th>Hook</th>
                <th>Bağlandığı gerçek çağrı yolu</th>
                <th>Adapter</th>
                <th>LangChain hook</th>
                <th className="num">Executions</th>
                <th className="num">Overhead p95</th>
              </tr>
            </thead>
            <tbody>
              {hooks.hooks.map((h) => (
                <tr key={h.hook}>
                  <td><Mono>{h.hook}</Mono></td>
                  <td className="text-xs">{h.attach}</td>
                  <td className="text-xs">{h.adapter}</td>
                  <td className="text-xs">{h.langchain_builtin ? "evet" : "hayır (adapter)"}</td>
                  <td className="num">{fmtNum(stats[h.hook]?.executions ?? 0)}</td>
                  <td className="num">{ms(stats[h.hook]?.overhead_p95_ms)}</td>
                </tr>
              ))}
              {hooks.unsupported.map((u) => (
                <tr key={u.name} className="text-muted">
                  <td className="text-xs">{u.name}</td>
                  <td className="text-xs" colSpan={5}>desteklenmiyor — {u.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      </CardBody>
    </Card>
  );
}

// ================================================================================================= Catalog
function CatalogTab({ range, open, onOpen }: { range: MwRange; open: string | null; onOpen: (n: string | null) => void }) {
  const { data, isLoading, error, refetch } = useMwCatalog(range);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [hook, setHook] = useState("");
  const [status, setStatus] = useState("");
  const [scope, setScope] = useState("");
  const reorder = useReorderMw();
  if (error) return <ErrorState error={error} onRetry={() => refetch()} />;
  const items = data?.items ?? [];
  const rows = items.filter(
    (i) =>
      (!q || `${i.name} ${i.title} ${i.description}`.toLowerCase().includes(q.toLowerCase())) &&
      (!kind || i.kind === kind) &&
      (!hook || i.hooks.includes(hook)) &&
      (!status || (status === "enabled" ? i.enabled : status === "disabled" ? !i.enabled : i.mandatory)) &&
      (!scope || (scope === "global" ? i.scope_label.startsWith("global") : !i.scope_label.startsWith("global"))),
  );
  const move = (i: CatalogItem, dir: -1 | 1) => {
    const idx = items.findIndex((x) => x.name === i.name);
    const other = items[idx + dir];
    if (!other) return;
    reorder.mutate(
      { items: [{ name: i.name, order: other.order, version: i.version }, { name: other.name, order: i.order, version: other.version }] },
      { onError: (e) => errorToast(e) },
    );
  };
  const kinds = [...new Set(items.map((i) => i.kind))];
  const hooks = [...new Set(items.flatMap((i) => i.hooks))];
  return (
    <div className="space-y-3">
      <Notice tone="info">
        Yalnız kodda kayıtlı ve onaylı middleware'ler yapılandırılabilir; arayüzden kod çalıştırılamaz. Zorunlu güvenlik middleware'leri kapatılamaz ve kapsamı daraltılamaz. Değişiklik yeni run'lardan itibaren geçerlidir.
      </Notice>
      <div className="flex flex-wrap items-center gap-2">
        <SearchInput value={q} onChange={setQ} placeholder="Ad / açıklama" className="w-56" />
        <Select aria-label="Tür" value={kind} onChange={(e) => setKind(e.target.value)} className="w-36">
          <option value="">Tüm türler</option>
          {kinds.map((k) => <option key={k} value={k}>{k}</option>)}
        </Select>
        <Select aria-label="Hook" value={hook} onChange={(e) => setHook(e.target.value)} className="w-44">
          <option value="">Tüm hook'lar</option>
          {hooks.map((k) => <option key={k} value={k}>{k}</option>)}
        </Select>
        <Select aria-label="Durum" value={status} onChange={(e) => setStatus(e.target.value)} className="w-36">
          <option value="">Tüm durumlar</option>
          <option value="enabled">enabled</option>
          <option value="disabled">disabled</option>
          <option value="mandatory">mandatory</option>
        </Select>
        <Select aria-label="Kapsam" value={scope} onChange={(e) => setScope(e.target.value)} className="w-36">
          <option value="">Tüm kapsamlar</option>
          <option value="global">global</option>
          <option value="scoped">daraltılmış</option>
        </Select>
      </div>
      <Card>
        {isLoading ? (
          <TableSkeleton rows={7} cols={9} />
        ) : rows.length === 0 ? (
          <EmptyState title="Eşleşen middleware yok" description="Filtreleri temizleyin." />
        ) : (
          <TableWrap>
            <table className="data-table">
              <thead>
                <tr>
                  <th className="num">Order</th>
                  <th>Middleware</th>
                  <th>Type</th>
                  <th>Hooks</th>
                  <th>Status</th>
                  <th>Scope</th>
                  <th className="num">Version</th>
                  <th>Last updated</th>
                  <th>Last result</th>
                  <th className="num">Executions</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((i) => (
                  <tr key={i.name} className="cursor-pointer" onClick={() => onOpen(i.name)}>
                    <td className="num">{i.order}</td>
                    <td className="max-w-[22rem]">
                      <div className="font-medium text-ink">{i.title}</div>
                      <div className="truncate text-xs text-muted" title={i.description}>
                        <Mono>{i.name}</Mono> · {i.description}
                      </div>
                    </td>
                    <td className="text-xs">{i.kind}</td>
                    <td className="text-xs">{i.hooks.join(", ")}</td>
                    <td className="text-xs whitespace-nowrap">
                      {i.mandatory ? (
                        <span className="inline-flex items-center gap-1"><Lock className="size-3" aria-hidden />mandatory</span>
                      ) : i.enabled ? "✓ enabled" : "○ disabled"}
                    </td>
                    <td className="text-xs">{i.scope_label}</td>
                    <td className="num">v{i.version}</td>
                    <td className="text-xs whitespace-nowrap">{i.updated_at ? `${fmtTime(i.updated_at)} · ${i.updated_by}` : "kod varsayılanı"}</td>
                    <td>{i.last ? <DecisionTag d={i.last.decision} /> : <span className="text-xs text-muted">çalışmadı</span>}</td>
                    <td className="num">{fmtNum(i.stats.executions)}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      {data?.can_manage && (
                        <span className="inline-flex gap-0.5">
                          <Button size="icon" variant="ghost" aria-label={`${i.name} yukarı taşı`} disabled={reorder.isPending || items[0]?.name === i.name} onClick={() => move(i, -1)}>
                            <ArrowUp />
                          </Button>
                          <Button size="icon" variant="ghost" aria-label={`${i.name} aşağı taşı`} disabled={reorder.isPending || items.at(-1)?.name === i.name} onClick={() => move(i, 1)}>
                            <ArrowDown />
                          </Button>
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      <DetailDialog name={open} range={range} onClose={() => onOpen(null)} />
    </div>
  );
}

// ------------------------------------------------------------------------------------------------- Detail
function DetailDialog({ name, range, onClose }: { name: string | null; range: MwRange; onClose: () => void }) {
  const { data: d, isLoading, error, refetch } = useMwDetail(name, range);
  const [edit, setEdit] = useState(false);
  useEffect(() => setEdit(false), [name]);
  return (
    <Dialog open={!!name} onOpenChange={(o) => !o && onClose()} title={d?.title ?? name ?? ""} description={d?.description} wide>
      {error ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading || !d ? (
        <TableSkeleton rows={6} cols={3} />
      ) : edit ? (
        <ConfigForm item={d} onDone={() => setEdit(false)} />
      ) : (
        <div className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <div className="space-y-2">
              <KV
                rows={[
                  ["name", <Mono key="n">{d.name}</Mono>],
                  ["type", d.kind],
                  ["status", d.mandatory ? "mandatory (kapatılamaz)" : d.enabled ? "enabled" : "disabled"],
                  ["failure policy", d.security ? "fail-closed: hata verirse çağrı engellenir" : "fail-open: hata kaydedilir, çağrı devam eder"],
                  ["order", d.order],
                  ["scope", d.scope_label],
                  ["config version", `v${d.version}`],
                  ["dry-run", d.dry_run],
                  ["policy", d.policy_ref ?? "—"],
                ]}
              />
              {d.can_manage && (
                <Button size="sm" onClick={() => setEdit(true)}>
                  Yapılandırmayı düzenle
                </Button>
              )}
            </div>
            <div>
              <div className="mb-1 text-xs text-muted">Hook'lar (bağlandığı gerçek çağrı yolu)</div>
              <ul className="space-y-1 text-xs">
                {Object.entries(d.hook_info).map(([h, info]) => (
                  <li key={h}>
                    <Mono>{h}</Mono> — {info.attach} <span className="text-muted">({info.adapter}; LangChain hook değil)</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <JsonView title="Masked configuration" value={d.config_json} />
            <JsonView title="Parameter schema (backend)" value={d.params_schema} open={1} />
          </div>
          <Card>
            <CardHeader title="Performance & errors" description="Seçili aralık, hook bazında" />
            <CardBody>
              {d.hook_stats.length === 0 ? (
                <div className="text-xs text-muted">Bu aralıkta execution yok</div>
              ) : (
                <table className="data-table">
                  <thead><tr><th>Hook</th><th className="num">Executions</th><th className="num">Blocked</th><th className="num">Errors</th><th className="num">Overhead p50</th><th className="num">p95</th></tr></thead>
                  <tbody>
                    {d.hook_stats.map((h) => (
                      <tr key={h.hook}><td><Mono>{h.hook}</Mono></td><td className="num">{fmtNum(h.executions)}</td><td className="num">{fmtNum(h.blocked)}</td><td className="num">{fmtNum(h.errors)}</td><td className="num">{ms(h.overhead_p50_ms)}</td><td className="num">{ms(h.overhead_p95_ms)}</td></tr>
                    ))}
                  </tbody>
                </table>
              )}
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="Configuration history" />
            <CardBody>
              {d.versions.length === 0 ? (
                <div className="text-xs text-muted">Kod varsayılanı (v0); değişiklik yapılmadı</div>
              ) : (
                <table className="data-table">
                  <thead><tr><th className="num">Version</th><th>Change</th><th>Actor</th><th>Time</th><th>Note</th></tr></thead>
                  <tbody>
                    {d.versions.map((v) => (
                      <tr key={v.version}><td className="num">v{v.version}</td><td className="text-xs">{v.change}</td><td className="text-xs">{v.actor}</td><td className="text-xs">{fmtTime(v.created_at)}</td><td className="text-xs">{v.note ?? "—"}</td></tr>
                    ))}
                  </tbody>
                </table>
              )}
            </CardBody>
          </Card>
          <Card>
            <CardHeader title="Recent executions" />
            <CardBody>
              <ExecTable items={d.recent} onOpen={() => undefined} compact />
            </CardBody>
          </Card>
        </div>
      )}
    </Dialog>
  );
}

/** Parametre formu backend'in JSON şemasından üretilir; doğrulama backend'dedir (hatası formda görünür). */
function ConfigForm({ item, onDone }: { item: CatalogItem; onDone: () => void }) {
  const upd = useUpdateMw();
  const [enabled, setEnabled] = useState(item.enabled);
  const [order, setOrder] = useState(String(item.order));
  const [params, setParams] = useState<Record<string, unknown>>(() => {
    const v = (item as unknown as { config_json?: { params?: Record<string, unknown> } }).config_json?.params;
    return { ...(v ?? {}) };
  });
  const [scope, setScope] = useState<Record<string, string>>(() =>
    Object.fromEntries(["kinds", "targets", "tasks", "connectors"].map((k) => [k, (item.scope?.[k] ?? []).join(", ")])),
  );
  const [note, setNote] = useState("");
  const props = item.params_schema.properties ?? {};
  const err = upd.error ? errText(upd.error) : null;
  const save = () => {
    const sc = Object.fromEntries(Object.entries(scope).map(([k, v]) => [k, v.split(",").map((x) => x.trim()).filter(Boolean)]));
    upd.mutate(
      { name: item.name, body: { version: item.version, enabled, order: Number(order), params, ...(item.mandatory ? {} : { scope: sc }), note: note || undefined } },
      { onSuccess: onDone },
    );
  };
  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <FormField label="Enabled" hint={item.mandatory ? "Zorunlu güvenlik middleware'i: kapatılamaz" : undefined}>
          <Select value={enabled ? "1" : "0"} onChange={(e) => setEnabled(e.target.value === "1")} disabled={item.mandatory}>
            <option value="1">enabled</option>
            <option value="0">disabled</option>
          </Select>
        </FormField>
        <FormField label="Order (1–1000; küçük önce / en dışta)">
          <Input type="number" min={1} max={1000} value={order} onChange={(e) => setOrder(e.target.value)} />
        </FormField>
      </div>
      {Object.keys(props).length === 0 ? (
        <div className="text-xs text-muted">Bu middleware'in parametresi yok.</div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {Object.entries(props).map(([k, s]) => (
            <SchemaInput key={k} name={k} schema={s} value={params[k]} onChange={(v) => setParams((p) => ({ ...p, [k]: v }))} />
          ))}
        </div>
      )}
      {!item.mandatory && (
        <div className="grid gap-3 sm:grid-cols-2">
          {Object.keys(scope).map((k) => (
            <FormField key={k} label={`Scope: ${k} (virgülle; boş = hepsi)`}>
              <Input value={scope[k]} onChange={(e) => setScope((s) => ({ ...s, [k]: e.target.value }))} />
            </FormField>
          ))}
        </div>
      )}
      <FormField label="Değişiklik notu (audit)">
        <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} />
      </FormField>
      {err && <Notice tone="critical">{err}</Notice>}
      <div className="flex gap-2">
        <Button variant="primary" loading={upd.isPending} onClick={save}>
          Kaydet (v{item.version} → v{item.version + 1})
        </Button>
        <Button variant="ghost" onClick={onDone}>
          Vazgeç
        </Button>
      </div>
    </div>
  );
}

function SchemaInput({ name, schema, value, onChange }: { name: string; schema: JsonSchema; value: unknown; onChange: (v: unknown) => void }) {
  const label = schema.title ?? name;
  const hint = [schema.description, schema.minimum != null || schema.maximum != null ? `aralık ${schema.minimum ?? "…"}–${schema.maximum ?? "…"}` : null].filter(Boolean).join(" · ") || undefined;
  if (schema.type === "boolean")
    return (
      <FormField label={label} hint={hint}>
        <Select value={value ? "1" : "0"} onChange={(e) => onChange(e.target.value === "1")}>
          <option value="1">true</option>
          <option value="0">false</option>
        </Select>
      </FormField>
    );
  if (schema.type === "integer" || schema.type === "number")
    return (
      <FormField label={label} hint={hint}>
        <Input type="number" value={value == null ? "" : String(value)} min={schema.minimum} max={schema.maximum} onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />
      </FormField>
    );
  if (schema.type === "array")
    return (
      <FormField label={`${label} (virgülle)`} hint={hint}>
        <Input value={Array.isArray(value) ? value.join(", ") : ""} onChange={(e) => onChange(e.target.value.split(",").map((x) => x.trim()).filter(Boolean))} />
      </FormField>
    );
  return (
    <FormField label={label} hint={hint}>
      <Input value={value == null ? "" : String(value)} onChange={(e) => onChange(e.target.value)} />
    </FormField>
  );
}

// ================================================================================================= Execution Explorer
function ExecTable({ items, onOpen, compact }: { items: Execution[]; onOpen: (id: string) => void; compact?: boolean }) {
  if (!items.length) return <div className="py-6 text-center text-xs text-muted">Kayıt yok</div>;
  return (
    <TableWrap maxH={compact ? "max-h-80" : undefined}>
      <table className="data-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Middleware</th>
            <th>Hook</th>
            <th>Target</th>
            <th>Decision</th>
            <th>Result</th>
            <th className="num">Overhead</th>
            <th className="num">Wrapped</th>
            <th>Reason</th>
            {!compact && <th>Run</th>}
          </tr>
        </thead>
        <tbody>
          {items.map((e) => (
            <tr key={e.execution_id} className="cursor-pointer" onClick={() => onOpen(e.execution_id)}>
              <td className="text-xs whitespace-nowrap">{fmtTime(e.started_at)}</td>
              <td className="text-xs">{e.middleware ? <>{e.middleware} <span className="text-muted">v{e.middleware_version ?? 0}</span></> : <span className="text-muted">{e.entry_type}</span>}</td>
              <td className="text-xs"><Mono>{e.hook ?? "—"}</Mono></td>
              <td className="max-w-[14rem] truncate text-xs" title={`${e.target_kind ?? ""} ${e.target ?? ""} ${e.task ?? ""}`}>
                {e.target_kind ? `${e.target_kind}: ${e.target ?? "—"}` : "—"}{e.task ? ` · ${e.task}` : ""}
              </td>
              <td><DecisionTag d={e.decision} /></td>
              <td><ResultTag r={e.result} /></td>
              <td className="num">{ms(e.overhead_ms)}</td>
              <td className="num">{e.wrapped_ms == null ? <span className="text-muted">—</span> : ms(e.wrapped_ms)}</td>
              <td className="max-w-[16rem] truncate text-xs" title={e.reason ?? ""}>{e.error_code ? <Mono>{e.error_code}</Mono> : null} {e.reason ?? ""}</td>
              {!compact && <td className="text-xs"><Mono>{short(e.run_id, 14)}</Mono></td>}
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

function ExecutionsTab({ range, sp, set }: { range: MwRange; sp: URLSearchParams; set: (k: string, v: string | null) => void }) {
  const cat = useMwCatalog(range);
  const hooks = useMwHooks();
  const page = Number(sp.get("page") || 1);
  const f = {
    range,
    middleware: sp.get("m") || undefined,
    hook: sp.get("hook") || undefined,
    decision: sp.get("decision") || undefined,
    result: sp.get("result") || undefined,
    run_id: sp.get("run_id") || undefined,
    tool_call_id: sp.get("tool_call_id") || undefined,
    entry_type: sp.get("entry") || "middleware",
    limit: 50,
    offset: (page - 1) * 50,
  };
  const { data, isLoading, error, refetch, isFetching } = useMwExecutions(f);
  const [open, setOpen] = useState<string | null>(sp.get("exec"));
  const upd = (k: string, v: string) => {
    set(k, v || null);
    set("page", null);
  };
  if (error) return <ErrorState error={error} onRetry={() => refetch()} />;
  const pages = Math.max(1, Math.ceil((data?.total ?? 0) / 50));
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select aria-label="Middleware" value={f.middleware ?? ""} onChange={(e) => upd("m", e.target.value)} className="w-48">
          <option value="">Tüm middleware'ler</option>
          {(cat.data?.items ?? []).map((i) => <option key={i.name} value={i.name}>{i.name}</option>)}
        </Select>
        <Select aria-label="Hook" value={f.hook ?? ""} onChange={(e) => upd("hook", e.target.value)} className="w-44">
          <option value="">Tüm hook'lar</option>
          {(hooks.data?.hooks ?? []).map((h) => <option key={h.hook} value={h.hook}>{h.hook}</option>)}
        </Select>
        <Select aria-label="Decision" value={f.decision ?? ""} onChange={(e) => upd("decision", e.target.value)} className="w-40">
          <option value="">Tüm kararlar</option>
          {Object.keys(DECISION).map((d) => <option key={d} value={d}>{d}</option>)}
        </Select>
        <Select aria-label="Result" value={f.result ?? ""} onChange={(e) => upd("result", e.target.value)} className="w-32">
          <option value="">Tüm sonuçlar</option>
          <option value="success">success</option>
          <option value="error">error</option>
        </Select>
        <Select aria-label="Kayıt türü" value={f.entry_type} onChange={(e) => upd("entry", e.target.value)} className="w-44">
          <option value="middleware">middleware hook</option>
          <option value="call">model / tool çağrısı</option>
          <option value="agent">run kökü</option>
          <option value="all">hepsi</option>
        </Select>
        <SearchInput value={f.run_id ?? ""} onChange={(v) => upd("run_id", v)} placeholder="run_id" className="w-56" />
        <SearchInput value={f.tool_call_id ?? ""} onChange={(v) => upd("tool_call_id", v)} placeholder="tool_call_id" className="w-56" />
      </div>
      <Card>
        {isLoading ? <TableSkeleton rows={8} cols={10} /> : <ExecTable items={data?.items ?? []} onOpen={setOpen} />}
        {isFetching && !isLoading && <span className="sr-only">Yenileniyor</span>}
      </Card>
      {data && data.total > 50 && <Pagination page={page} pages={pages} total={data.total} onPage={(p) => set("page", String(p))} />}
      <ExecutionDialog id={open} onClose={() => setOpen(null)} />
    </div>
  );
}

function ExecutionDialog({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { data: e, isLoading, error, refetch } = useMwExecution(id);
  const cont = e
    ? e.outcome === "blocked" || e.outcome === "short_circuit"
      ? "engellendi (çağrı yapılmadı)"
      : e.outcome === "blocked_inner"
        ? "iç katmanda engellendi"
        : e.outcome === "error"
          ? "çağrı hata ile bitti"
          : "devam ettirildi"
    : "";
  return (
    <Dialog open={!!id} onOpenChange={(o) => !o && onClose()} title="Middleware execution" description={id ?? ""} wide>
      {error ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading || !e ? (
        <TableSkeleton rows={6} cols={2} />
      ) : (
        <div className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <KV
              rows={[
                ["execution_id", <Mono key="e">{e.execution_id}</Mono>],
                ["run_id", e.run_id ? <Mono key="r">{e.run_id}</Mono> : NOT_CAPTURED],
                ["thread_id", e.thread_id ?? NOT_CAPTURED],
                ["trace_id", e.trace_id ?? NOT_CAPTURED],
                ["span_id", "desteklenmiyor"],
                ["parent_execution_id", e.parent_execution_id ? <Mono key="p">{e.parent_execution_id}</Mono> : "— (kök)"],
                ["sequence / depth", `${e.sequence} / ${e.depth}`],
                ["middleware", e.middleware ? `${e.middleware} v${e.middleware_version ?? 0}` : e.entry_type],
                ["hook", e.hook ?? "—"],
                ["target", e.target_kind ? `${e.target_kind}: ${e.target ?? "—"} (${e.connector ?? "—"})` : "—"],
                ["task", e.task ?? "—"],
                ["tool_call_id", e.tool_call_id ?? "—"],
                ["attempt_no", e.attempt_no ?? "—"],
              ]}
            />
            <KV
              rows={[
                ["start / end", `${fmtTime(e.started_at)} → ${e.ended_at ? fmtTime(e.ended_at) : NOT_CAPTURED}`],
                ["middleware overhead", ms(e.overhead_ms)],
                ["wrapped call", e.wrapped_ms == null ? "ölçülmedi (çağrı yapılmadı ya da hook çağrıyı sarmıyor)" : ms(e.wrapped_ms)],
                ["result", <ResultTag key="r" r={e.result} />],
                ["decision", <DecisionTag key="d" d={e.decision} />],
                ["outcome", cont],
                ["cache'den döndü mü", "desteklenmiyor (cache yok)"],
                ["reason", e.reason ?? "—"],
                ["error code", e.error_code ?? "—"],
                ["policy", e.policy ?? "—"],
                ["changed fields", e.changed_fields.length ? e.changed_fields.join(", ") : "—"],
              ]}
            />
          </div>
          <div className="flex flex-wrap gap-2 text-xs">
            {e.links.timeline && <Link className="inline-flex items-center gap-1 underline" to={e.links.timeline}>Run Timeline <ExternalLink className="size-3" /></Link>}
            {e.links.agent_trace && <Link className="inline-flex items-center gap-1 underline" to={e.links.agent_trace}>Agent Trace <ExternalLink className="size-3" /></Link>}
            {e.links.mcp_call && <Link className="inline-flex items-center gap-1 underline" to={e.links.mcp_call}>MCP Çağrısı <ExternalLink className="size-3" /></Link>}
            {e.links.llm_monitoring && <Link className="inline-flex items-center gap-1 underline" to={e.links.llm_monitoring}>LLM Monitoring <ExternalLink className="size-3" /></Link>}
          </div>
          {e.truncated && <Notice tone="warning">Payload boyut sınırını aştı; görünüm kırpıldı (truncated).</Notice>}
          {e.payload_captured ? (
            <>
              <div className="grid gap-4 lg:grid-cols-2">
                <JsonView title="Before (maskeli)" value={e.before ?? NOT_CAPTURED} />
                <JsonView title="After (maskeli)" value={e.after ?? NOT_CAPTURED} />
              </div>
              <Card>
                <CardHeader title="JSON diff" />
                <CardBody>{e.diff ? <DiffTable changes={e.diff} /> : <div className="text-xs text-muted">{NOT_CAPTURED}</div>}</CardBody>
              </Card>
            </>
          ) : (
            <Notice tone="info">Payload {NOT_CAPTURED}: varsayılan olarak yalnız metadata kaydedilir (Settings › Capture Payloads).</Notice>
          )}
          {Object.keys(e.extra ?? {}).length > 0 && <JsonView title="Ek bilgi (stream, attempts, süre dağılımı)" value={e.extra} open={1} />}
        </div>
      )}
    </Dialog>
  );
}

// ================================================================================================= Run Timeline
function TimelineTab({ range, run, onRun }: { range: MwRange; run: string | null; onRun: (r: string | null) => void }) {
  const [kind, setKind] = useState("");
  const runs = useMwRuns(range, kind || undefined);
  const cur = run ?? runs.data?.items[0]?.run_id ?? null;
  const tl = useMwTimeline(cur);
  const [open, setOpen] = useState<string | null>(null);
  const parallel = useMemo(() => new Set((tl.data?.parallel_groups ?? []).flat()), [tl.data]);
  if (runs.error) return <ErrorState error={runs.error} onRetry={() => runs.refetch()} />;
  return (
    <div className="grid gap-4 xl:grid-cols-[22rem_1fr]">
      <Card>
        <CardHeader
          title="Runs"
          actions={
            <Select aria-label="Run türü" value={kind} onChange={(e) => setKind(e.target.value)} className="w-32">
              <option value="">hepsi</option>
              <option value="analysis">analysis</option>
              <option value="chat_turn">chat_turn</option>
            </Select>
          }
        />
        {runs.isLoading ? (
          <TableSkeleton rows={8} cols={2} />
        ) : !runs.data?.items.length ? (
          <EmptyState title="Bu aralıkta run yok" />
        ) : (
          <ul className="max-h-[70vh] divide-y divide-line overflow-auto">
            {runs.data.items.map((r) => (
              <li key={r.execution_id}>
                <button type="button" onClick={() => onRun(r.run_id)} aria-current={r.run_id === cur}
                  className={cn("w-full px-3 py-2 text-left text-xs hover:bg-surface-2", r.run_id === cur && "bg-surface-2")}>
                  <div className="flex items-center justify-between gap-2">
                    <Mono>{short(r.run_id, 26)}</Mono>
                    <span>{r.outcome}</span>
                  </div>
                  <div className="text-muted">
                    {r.run_kind} · {fmtTime(r.started_at)} · {fmtNum(r.executions)} exec · {fmtNum(r.blocked)} block
                  </div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>
      <Card>
        <CardHeader
          title={cur ? `Run ${short(cur, 40)}` : "Run seçin"}
          description={tl.data ? `Sıralama: ${tl.data.ordering} · ${tl.data.count} kayıt` : undefined}
          actions={cur ? <Link className="text-xs underline" to={`/agent-trace/${encodeURIComponent(cur)}`}>Agent Trace</Link> : null}
        />
        <CardBody>
          {!cur ? (
            <EmptyState title="Run seçilmedi" />
          ) : tl.error ? (
            <ErrorState error={tl.error} onRetry={() => tl.refetch()} />
          ) : tl.isLoading || !tl.data ? (
            <TableSkeleton rows={10} cols={4} />
          ) : (
            <div className="space-y-3">
              <div className="flex flex-wrap gap-3 text-xs text-muted">
                <span>durum: <span className="text-ink-2">{tl.data.status}</span></span>
                <span>kayıp kayıt: {tl.data.lost_records ?? NOT_CAPTURED}</span>
                {tl.data.parallel_groups.length > 0 && <span>{tl.data.parallel_groups.length} paralel tool grubu (∥ işaretli)</span>}
              </div>
              {tl.data.nested_runs.length > 0 && (
                <Notice tone="info">
                  İç run'lar:{" "}
                  {tl.data.nested_runs.map((n) => (
                    <button key={n.execution_id} type="button" className="mr-2 underline" onClick={() => onRun(n.run_id)}>
                      {n.run_kind} {short(n.run_id, 20)}
                    </button>
                  ))}
                </Notice>
              )}
              <ol className="space-y-0.5" aria-label="Yürütme sırası">
                {tl.data.tree.map((n) => (
                  <TreeNode key={n.execution_id} n={n} depth={0} parallel={parallel} onOpen={setOpen} />
                ))}
              </ol>
            </div>
          )}
        </CardBody>
      </Card>
      <ExecutionDialog id={open} onClose={() => setOpen(null)} />
    </div>
  );
}

function TreeNode({ n, depth, parallel, onOpen }: { n: TimelineNode; depth: number; parallel: Set<string>; onOpen: (id: string) => void }) {
  const label =
    n.entry_type === "agent"
      ? `agent ${n.run_kind ?? ""} — ${n.outcome ?? ""}`
      : n.entry_type === "call"
        ? `${n.target_kind} çağrısı: ${n.target ?? "—"}${n.task ? ` · ${n.task}` : ""}`
        : `${n.hook} › ${n.middleware}`;
  return (
    <li>
      <button type="button" onClick={() => onOpen(n.execution_id)}
        className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left text-xs hover:bg-surface-2" style={{ paddingLeft: `${depth * 16 + 4}px` }}>
        <span className="w-8 shrink-0 text-right text-muted tabular-nums">#{n.sequence}</span>
        {parallel.has(n.execution_id) && <span title="Paralel çalışan tool çağrısı" aria-label="paralel">∥</span>}
        <span className={cn("min-w-0 flex-1 truncate", n.entry_type !== "middleware" && "font-medium text-ink")}>{label}</span>
        {n.decision && <DecisionTag d={n.decision} />}
        {n.result === "error" && <ResultTag r="error" />}
        <span className="w-24 shrink-0 text-right text-muted tabular-nums" title="overhead / wrapped">
          {n.overhead_ms != null ? ms(n.overhead_ms) : ""}{n.wrapped_ms != null ? ` · ${ms(n.wrapped_ms)}` : ""}
        </span>
      </button>
      {n.children.length > 0 && (
        <ol>
          {n.children.map((c) => (
            <TreeNode key={c.execution_id} n={c} depth={depth + 1} parallel={parallel} onOpen={onOpen} />
          ))}
        </ol>
      )}
    </li>
  );
}

// ================================================================================================= Dry-run
function DryRunTab() {
  const cat = useMwCatalog("24h");
  const run = useDryRun();
  const items = cat.data?.items ?? [];
  const [name, setName] = useState("tool_param_validation");
  const m = items.find((i) => i.name === name);
  const [hook, setHook] = useState("wrap_tool_call");
  const [target, setTarget] = useState("execute-dql");
  const [connector, setConnector] = useState("dynatrace");
  const [dql, setDql] = useState("fetch logs | limit 10");
  const [message, setMessage] = useState("");
  const [state, setState] = useState("");
  const [params, setParams] = useState("");
  useEffect(() => {
    if (m && !m.hooks.includes(hook)) setHook(m.hooks[0]);
  }, [m, hook]);
  const submit = () => {
    let p: Record<string, unknown> | undefined;
    let st: Record<string, number> | undefined;
    try {
      p = params.trim() ? JSON.parse(params) : undefined;
      st = state.trim() ? JSON.parse(state) : undefined;
    } catch {
      errorToast(new Error("Parametre / state geçerli JSON olmalı"));
      return;
    }
    const isTool = hook === "wrap_tool_call";
    run.mutate({
      name,
      hook,
      input: isTool ? { kind: "tool", target, connector, dql } : { kind: "model", target: "model", task: "dry_run", ...(message ? { messages: [["human", message]] } : {}) },
      params: p,
      state: st,
    });
  };
  const r = run.data;
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card>
        <CardHeader title="Sentetik girdi" description="Gerçek LLM / MCP çağrısı ve yan etki yoktur; kayıt üretmez" />
        <CardBody className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <FormField label="Middleware">
              <Select value={name} onChange={(e) => setName(e.target.value)}>
                {items.map((i) => <option key={i.name} value={i.name}>{i.name} ({i.dry_run})</option>)}
              </Select>
            </FormField>
            <FormField label="Hook">
              <Select value={hook} onChange={(e) => setHook(e.target.value)}>
                {(m?.hooks ?? [hook]).map((h) => <option key={h} value={h}>{h}</option>)}
              </Select>
            </FormField>
          </div>
          {hook === "wrap_tool_call" ? (
            <>
              <div className="grid gap-3 sm:grid-cols-2">
                <FormField label="Connector"><Input value={connector} onChange={(e) => setConnector(e.target.value)} /></FormField>
                <FormField label="Tool"><Input value={target} onChange={(e) => setTarget(e.target.value)} /></FormField>
              </div>
              <FormField label="DQL"><Textarea rows={3} value={dql} onChange={(e) => setDql(e.target.value)} /></FormField>
              <FormField label='State (JSON, ör. {"queries_used": 40, "max_queries": 40})'><Input value={state} onChange={(e) => setState(e.target.value)} /></FormField>
            </>
          ) : (
            <FormField label="Model mesajı (human)"><Textarea rows={3} value={message} onChange={(e) => setMessage(e.target.value)} /></FormField>
          )}
          <FormField label="Parametre override (JSON, isteğe bağlı)"><Input value={params} onChange={(e) => setParams(e.target.value)} placeholder='{"max_chars": 2000}' /></FormField>
          <Button variant="primary" onClick={submit} loading={run.isPending}>
            <FlaskConical /> Dry-run
          </Button>
          {run.error && <Notice tone="critical">{errText(run.error)}</Notice>}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Sonuç" />
        <CardBody>
          {!r ? (
            <div className="text-xs text-muted">Henüz çalıştırılmadı</div>
          ) : !r.supported ? (
            <Notice tone="warning">Dry-run desteklenmiyor: {r.reason}</Notice>
          ) : r.param_errors ? (
            <Notice tone="critical">Parametre doğrulama hatası: {r.param_errors.join("; ")}</Notice>
          ) : (
            <div className="space-y-3">
              <KV
                rows={[
                  ["mode", r.mode === "simulated" ? "simulated (dış çağrının yerine sentetik girdi)" : r.mode],
                  ["hook", r.hook],
                  ["decision", <DecisionTag key="d" d={r.decision} />],
                  ["reason", r.reason ?? "—"],
                  ["code", r.code ?? "—"],
                  ["changed fields", r.changed_fields?.length ? r.changed_fields.join(", ") : "—"],
                  ["policy", r.policy ?? "—"],
                  ["error", r.error ?? "—"],
                  ["duration", ms(r.duration_ms)],
                ]}
              />
              <div className="grid gap-3 lg:grid-cols-2">
                <JsonView title="Before (maskeli)" value={r.before} />
                <JsonView title="After (maskeli)" value={r.after} />
              </div>
              <p className="flex items-center gap-1 text-xs text-muted"><ShieldCheck className="size-3" aria-hidden /> {r.note}</p>
            </div>
          )}
          <Badge className="mt-3">Kayıtlı middleware dışında hiçbir kod çalıştırılmaz</Badge>
        </CardBody>
      </Card>
    </div>
  );
}
