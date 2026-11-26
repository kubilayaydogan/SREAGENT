/** Guardrails: policy yönetimi, değerlendirme kayıtları, ihlal triage'ı, policy tester, human approval, analytics, ayarlar.
 *  Tüm güvenlik kararları backend'de deterministik verilir; bu sayfa yalnız gösterir ve yetkili istek gönderir.
 *  Mandatory policy'ler arayüzde lock ikonu ile işaretlenir ve zayıflatılamaz (backend de reddeder).
 *  Ölçülmeyen değer "—" yazar; FP/FN oranı yalnız etiketli veriden gelir. */
import { AlertTriangle, Ban, CheckCircle2, Copy, History, Lock, Play, Power, ShieldAlert, ShieldCheck, Undo2 } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { RangeTabs, RefreshControl, SearchInput } from "@/components/filters";
import { JsonView } from "@/components/JsonView";
import { MetricStrip, StatTile } from "@/components/StatTile";
import { TimeSeriesChart, type SeriesDef } from "@/components/charts/TimeSeriesChart";
import { TableWrap } from "@/components/table";
import { Dialog, TabPanel, Tabs } from "@/components/ui/overlay";
import { Badge, Button, Card, CardBody, CardHeader, Field, Input, Label, Mono, Select, Textarea } from "@/components/ui/primitives";
import { EmptyState, ErrorState, Notice, Page, PageHeader, Pagination, TableSkeleton } from "@/components/ui/states";
import {
  jsonDiff,
  useAssignPolicy,
  useCancelApproval,
  useClonePolicy,
  useDecideApproval,
  useGrAnalytics,
  useGrApprovals,
  useGrAudit,
  useGrAuditVerify,
  useGrConfig,
  useGrEffective,
  useGrLog,
  useGrLogs,
  useGrMeta,
  useGrOverview,
  useGrPolicies,
  useGrPolicy,
  useGrSamples,
  useGrTestHistory,
  useGrViolations,
  useKillSwitch,
  useRollbackPolicy,
  useRunTest,
  useSaveConfig,
  useTriage,
  useUpdatePolicy,
  type Assignment,
  type GrDecision,
  type GrRange,
  type LogFilters,
  type Policy,
  type TestResult,
  type Violation,
} from "@/features/guardrails/api";
import { DiffTable } from "@/pages/AgentTracePage";
import { useAuth } from "@/services/auth";
import { cn, fmtNum, fmtPct, fmtRelative, fmtTime, shortId } from "@/lib/utils";

const RANGES: { value: GrRange; label: string }[] = [
  { value: "1h", label: "1s" },
  { value: "24h", label: "24s" },
  { value: "7d", label: "7g" },
  { value: "30d", label: "30g" },
];

const TABS = [
  { value: "overview", label: "Overview" },
  { value: "policies", label: "Policies" },
  { value: "logs", label: "Evaluation Logs" },
  { value: "violations", label: "Violations" },
  { value: "tester", label: "Policy Tester" },
  { value: "approvals", label: "Human Approvals" },
  { value: "analytics", label: "Analytics" },
  { value: "settings", label: "Settings" },
];

const DECISION_TR: Record<string, string> = {
  passed: "geçti",
  blocked: "engellendi",
  warned: "uyarı",
  redacted: "maskelendi",
  sanitized: "temizlendi",
  approval_required: "onay gerekli",
  terminated: "sonlandırıldı",
  error: "hata",
};

const fmtMsSmall = (v: number | null | undefined) => (v == null ? "—" : v < 1 ? `${v.toFixed(3)} ms` : `${v.toFixed(1)} ms`);

export function DecisionLabel({ d, enforced = true }: { d: GrDecision | string; enforced?: boolean }) {
  const tone =
    d === "blocked" || d === "terminated" || d === "error"
      ? "text-st-critical-ink"
      : d === "approval_required" || d === "warned"
        ? "text-st-warning-ink"
        : d === "redacted" || d === "sanitized"
          ? "text-st-serious-ink"
          : "text-ink-2";
  const icon =
    d === "passed" ? <CheckCircle2 className="size-3.5" /> : d === "blocked" || d === "terminated" ? <Ban className="size-3.5" /> : <AlertTriangle className="size-3.5" />;
  return (
    <span className={cn("inline-flex items-center gap-1 text-xs whitespace-nowrap", tone)}>
      {icon}
      {DECISION_TR[d] ?? d}
      {!enforced && d !== "passed" && <span className="text-2xs text-muted">(monitor)</span>}
    </span>
  );
}

function SevLabel({ s }: { s: string | null | undefined }) {
  if (!s) return <span className="text-muted">—</span>;
  const tone = s === "critical" ? "text-st-critical-ink font-medium" : s === "high" ? "text-st-serious-ink" : s === "medium" ? "text-st-warning-ink" : "text-ink-2";
  return <span className={cn("text-xs", tone)}>{s}</span>;
}

function ModeLabel({ p }: { p: Pick<Policy, "mode" | "mandatory"> }) {
  return (
    <span className="inline-flex items-center gap-1 text-xs">
      {p.mandatory && <Lock className="size-3 text-muted" aria-label="mandatory" />}
      <span className={cn(p.mode === "enforce" ? "text-ink" : p.mode === "monitor" ? "text-st-warning-ink" : "text-muted")}>{p.mode}</span>
    </span>
  );
}

export default function GuardrailsPage() {
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") ?? "overview";
  const range = (sp.get("range") as GrRange) ?? "24h";
  const set = (k: string, v: string | null) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v);
    else n.delete(k);
    setSp(n, { replace: true });
  };
  const { can } = useAuth();
  const isAdmin = can("admin");
  const isSre = can("sre");
  return (
    <Page wide>
      <PageHeader
        title="Guardrails"
        description="Agent girdisi, model context'i, tool / MCP çağrıları, memory ve çıktı için deterministik güvenlik policy'leri. Kararı kural motoru verir; LLM güvenlik kararı vermez."
        actions={
          (tab === "overview" || tab === "logs" || tab === "analytics") && <RangeTabs<GrRange> value={range} onChange={(v) => set("range", v)} options={RANGES} />
        }
      />
      <Tabs value={tab} onValueChange={(v) => set("tab", v)} tabs={TABS}>
        <TabPanel value="overview">{tab === "overview" && <OverviewTab range={range} onOpen={(t, k, v) => setSp({ tab: t, [k]: v, range })} />}</TabPanel>
        <TabPanel value="policies">{tab === "policies" && <PoliciesTab admin={isAdmin} />}</TabPanel>
        <TabPanel value="logs">{tab === "logs" && <LogsTab range={range} initial={{ run_id: sp.get("run_id") ?? undefined, policy: sp.get("policy") ?? undefined }} />}</TabPanel>
        <TabPanel value="violations">{tab === "violations" && <ViolationsTab canTriage={isSre} />}</TabPanel>
        <TabPanel value="tester">{tab === "tester" && <TesterTab />}</TabPanel>
        <TabPanel value="approvals">{tab === "approvals" && <ApprovalsTab canDecide={isAdmin} />}</TabPanel>
        <TabPanel value="analytics">{tab === "analytics" && <AnalyticsTab range={range} />}</TabPanel>
        <TabPanel value="settings">{tab === "settings" && <SettingsTab admin={isAdmin} />}</TabPanel>
      </Tabs>
    </Page>
  );
}

// ------------------------------------------------------------------------------------------- Overview

function OverviewTab({ range, onOpen }: { range: GrRange; onOpen: (tab: string, k: string, v: string) => void }) {
  const { data: o, isLoading, error, refetch, isFetching, dataUpdatedAt } = useGrOverview(range);
  const series: SeriesDef[] = useMemo(() => {
    if (!o) return [];
    const by = new Map<string, Map<string, number>>();
    for (const r of o.series) {
      if (r.decision === "passed") continue;
      if (!by.has(r.decision)) by.set(r.decision, new Map());
      by.get(r.decision)!.set(r.t, (by.get(r.decision)!.get(r.t) ?? 0) + r.n);
    }
    const ts = [...new Set(o.series.map((r) => r.t))].sort();
    return [...by.entries()].map(([d, m]) => ({ id: d, label: DECISION_TR[d] ?? d, points: ts.map((t) => ({ t, v: m.get(t) ?? 0 })) }));
  }, [o]);
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />;
  const hMax = Math.max(1, ...(o?.histogram.counts ?? [0]));
  const openTotal = o ? Object.values(o.open_violations).reduce((a, b) => a + b, 0) : 0;
  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <RefreshControl onRefresh={() => void refetch()} fetching={isFetching} updatedAt={dataUpdatedAt} auto="30 sn" />
      </div>
      <MetricStrip>
        <StatTile label="Değerlendirme" value={fmtNum(o?.total_evaluations)} loading={isLoading} sub="geçenler koşu başına toplanır" />
        <StatTile label="İhlal" value={fmtNum(o?.violations)} loading={isLoading} />
        <StatTile label="Engellenen" value={fmtNum(o?.blocked)} loading={isLoading} tone={o?.blocked ? "text-st-critical-ink" : undefined} />
        <StatTile label="Maskelenen" value={fmtNum(o?.redacted)} loading={isLoading} />
        <StatTile label="Uyarı" value={fmtNum(o?.warned)} loading={isLoading} />
        <StatTile label="Onay bekleyen" value={fmtNum(o?.pending_approvals)} loading={isLoading} tone={o?.pending_approvals ? "text-st-warning-ink" : undefined} />
        <StatTile label="Hata / timeout" value={o ? `${fmtNum(o.errors)} / ${fmtNum(o.timeouts)}` : "—"} loading={isLoading} />
        <StatTile label="Ort. / p95 latency" value={o ? `${fmtMsSmall(o.avg_ms)} / ${fmtMsSmall(o.p95_ms_approx)}` : "—"} missing={o?.avg_ms == null} loading={isLoading} sub="p95 histogram kovasından (yaklaşık)" />
      </MetricStrip>
      <div className="grid gap-3 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="İhlal kararları (zaman)" description="Geçen değerlendirmeler hariç; karar türüne göre" />
          <CardBody>
            <TimeSeriesChart series={series} format={(v) => fmtNum(v)} emptyText="Bu aralıkta ihlal yok" unit="adet" />
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Latency dağılımı" description="Tek değerlendirme süresi (ms kovası)" />
          <CardBody className="space-y-1">
            {(o?.histogram.labels ?? []).map((l, i) => (
              <div key={l} className="flex items-center gap-2 text-xs" title={`${l}: ${o!.histogram.counts[i]}`}>
                <span className="w-16 shrink-0 text-right font-mono text-2xs text-muted">{l}</span>
                <div className="h-3 flex-1 rounded-sm bg-surface-2">
                  <div className="h-3 rounded-sm bg-accent/70" style={{ width: `${(o!.histogram.counts[i] / hMax) * 100}%` }} />
                </div>
                <span className="w-12 text-right tabular-nums">{fmtNum(o!.histogram.counts[i])}</span>
              </div>
            ))}
            {!o?.histogram.labels.length && <div className="text-xs text-muted">Veri yok</div>}
          </CardBody>
        </Card>
      </div>
      <div className="grid gap-3 lg:grid-cols-3">
        <Card>
          <CardHeader title="Policy durumu" />
          <CardBody className="space-y-1 text-xs">
            <Field label="Aktif">{fmtNum(o?.policies.active)}</Field>
            <Field label="Mandatory">{fmtNum(o?.policies.mandatory)}</Field>
            <Field label="Monitor modunda">{fmtNum(o?.policies.monitor)}</Field>
            <Field label="Kapalı">{fmtNum(o?.policies.disabled)}</Field>
            <Field label="Açık ihlal">
              {fmtNum(openTotal)}
              {o && openTotal > 0 && <span className="ml-1 text-muted">({Object.entries(o.open_violations).map(([k, v]) => `${k}: ${v}`).join(", ")})</span>}
            </Field>
            <Field label="Writer kaybı">{o?.health.writer_lost == null ? "—" : fmtNum(o.health.writer_lost)}</Field>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="En çok tetiklenen policy'ler" />
          <CardBody>
            {o?.top_policies.length ? (
              <table className="data-table">
                <tbody>
                  {o.top_policies.map((t) => (
                    <tr key={t.policy_key} className="cursor-pointer" onClick={() => onOpen("logs", "policy", t.policy_key)}>
                      <td className="font-mono text-2xs">{t.policy_key}</td>
                      <td className="text-xs text-muted">{t.category}</td>
                      <td className="text-right tabular-nums">{fmtNum(t.n)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="text-xs text-muted">Bu aralıkta ihlal yok</div>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Agent'lara göre ihlal" />
          <CardBody>
            {o?.top_agents.length ? (
              <table className="data-table">
                <tbody>
                  {o.top_agents.map((t) => (
                    <tr key={t.agent}>
                      <td className="text-xs">{t.agent}</td>
                      <td className="text-right tabular-nums">{fmtNum(t.n)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="text-xs text-muted">Bu aralıkta ihlal yok</div>
            )}
          </CardBody>
        </Card>
      </div>
      <Card>
        <CardHeader title="Son kritik ihlaller" />
        <CardBody>
          {o?.recent_critical.length ? <ViolationTable items={o.recent_critical} onPick={() => onOpen("violations", "q", "")} /> : <div className="text-xs text-muted">Kritik ihlal yok</div>}
        </CardBody>
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------------------------------- Policies

function PoliciesTab({ admin }: { admin: boolean }) {
  const [category, setCategory] = useState("");
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<string | null>(null);
  const [view, setView] = useState<"list" | "effective">("list");
  const { data: meta } = useGrMeta();
  const { data, isLoading, error, refetch, isFetching } = useGrPolicies({ category: category || undefined, q: q || undefined });
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select aria-label="kategori" value={category} onChange={(e) => setCategory(e.target.value)} className="w-44">
          <option value="">Tüm kategoriler</option>
          {(meta?.categories ?? []).map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </Select>
        <SearchInput value={q} onChange={setQ} placeholder="key / ad ara…" className="w-56" />
        <div className="ml-auto flex gap-1">
          <Button size="sm" variant={view === "list" ? "secondary" : "ghost"} onClick={() => setView("list")}>
            Liste
          </Button>
          <Button size="sm" variant={view === "effective" ? "secondary" : "ghost"} onClick={() => setView("effective")}>
            Effective policy
          </Button>
        </div>
      </div>
      {!admin && <Notice tone="info">Policy değişikliği yalnız admin rolüyle yapılır; bu görünüm salt okunur.</Notice>}
      {view === "effective" ? (
        <EffectiveView />
      ) : error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : isLoading ? (
        <TableSkeleton cols={8} />
      ) : !data?.items.length ? (
        <EmptyState title="Policy bulunamadı" />
      ) : (
        <Card>
          <TableWrap fetching={isFetching}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Policy</th>
                  <th>Kategori</th>
                  <th>Mode</th>
                  <th>Aksiyon</th>
                  <th>Severity</th>
                  <th>Kapsam</th>
                  <th>Sürüm</th>
                  <th>Son tetiklenme</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((p) => (
                  <tr key={p.id} className="cursor-pointer" onClick={() => setSel(p.id)}>
                    <td>
                      <div className="text-[13px] text-ink">{p.name}</div>
                      <Mono className="text-2xs">{p.key}</Mono>
                      {p.bound_to && <span className="ml-1 text-2xs text-muted">· {p.bound_to} katmanında uygulanır</span>}
                    </td>
                    <td className="text-xs">{p.category}</td>
                    <td>
                      <ModeLabel p={p} />
                      {p.status !== "active" && <span className="ml-1 text-2xs text-muted">({p.status})</span>}
                    </td>
                    <td className="text-xs">{p.action}</td>
                    <td>
                      <SevLabel s={p.severity} />
                    </td>
                    <td className="text-xs">{p.scope}</td>
                    <td className="text-xs tabular-nums">v{p.version}</td>
                    <td className="text-xs text-muted">{p.last_triggered_at ? fmtRelative(p.last_triggered_at) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </Card>
      )}
      {sel && <PolicyDialog id={sel} admin={admin} onClose={() => setSel(null)} />}
    </div>
  );
}

function EffectiveView() {
  const [f, setF] = useState({ agent_id: "", environment: "", mcp_server: "", tool: "", node: "" });
  const clean = Object.fromEntries(Object.entries(f).filter(([, v]) => v)) as typeof f;
  const { data, isLoading, error } = useGrEffective(clean);
  return (
    <Card>
      <CardHeader title="Effective policy" description={data?.rule ?? "Seçilen bağlam için geçerli policy'ler ve kalıtım seviyesi"} />
      <CardBody className="space-y-3">
        <div className="flex flex-wrap gap-2">
          {(["agent_id", "environment", "mcp_server", "tool", "node"] as const).map((k) => (
            <Input key={k} aria-label={k} placeholder={k} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} className="w-40" />
          ))}
        </div>
        {error ? (
          <ErrorState error={error} />
        ) : isLoading ? (
          <TableSkeleton cols={6} />
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Policy</th>
                <th>Kalıtım</th>
                <th>Mode</th>
                <th>Aksiyon</th>
                <th>Aşamalar</th>
                <th>Öncelik</th>
              </tr>
            </thead>
            <tbody>
              {(data?.items ?? []).map((p) => (
                <tr key={p.key}>
                  <td>
                    <Mono>{p.key}</Mono>
                    <div className="text-2xs text-muted">{p.name}</div>
                  </td>
                  <td className="text-xs">
                    {p.inherited_from}
                    {p.scope_values.length > 0 && <span className="text-muted"> ({p.scope_values.join(", ")})</span>}
                  </td>
                  <td>
                    <ModeLabel p={{ mode: p.mode as Policy["mode"], mandatory: p.mandatory }} />
                  </td>
                  <td className="text-xs">{p.action}</td>
                  <td className="text-2xs text-muted">{p.stages.join(", ")}</td>
                  <td className="text-xs tabular-nums">{p.priority}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardBody>
    </Card>
  );
}

const SCOPES = ["global", "tenant", "environment", "agent", "mcp_server", "tool", "graph_node"];

function PolicyDialog({ id, admin, onClose }: { id: string; admin: boolean; onClose: () => void }) {
  const { data: p, isLoading, error } = useGrPolicy(id);
  const { data: meta } = useGrMeta();
  const upd = useUpdatePolicy();
  const clone = useClonePolicy();
  const rb = useRollbackPolicy();
  const assign = useAssignPolicy();
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);
  const [params, setParams] = useState<string>("");
  const [note, setNote] = useState("");
  const [cloneKey, setCloneKey] = useState("");
  const [asg, setAsg] = useState<Assignment[] | null>(null);
  const [tab, setTab] = useState("def");
  if (!p) {
    return (
      <Dialog open onOpenChange={(v) => !v && onClose()} title="Policy" wide>
        {error ? <ErrorState error={error} /> : isLoading ? <TableSkeleton rows={4} cols={2} /> : null}
      </Dialog>
    );
  }
  const d = draft ?? { name: p.name, description: p.description ?? "", mode: p.mode, action: p.action, severity: p.severity, status: p.status, priority: p.priority };
  const paramsText = params || JSON.stringify(p.params, null, 2);
  let paramsErr: string | null = null;
  let parsed: unknown = null;
  try {
    parsed = JSON.parse(paramsText);
  } catch (e) {
    paramsErr = (e as Error).message;
  }
  const locked = p.mandatory;
  const actions = p.evaluator?.actions ?? meta?.actions ?? [p.action];
  const save = () => {
    const body: Record<string, unknown> = { version: p.version, note: note || undefined };
    for (const [k, v] of Object.entries(d)) if (v !== (p as unknown as Record<string, unknown>)[k] && !(k === "description" && v === (p.description ?? ""))) body[k] = v;
    if (JSON.stringify(parsed) !== JSON.stringify(p.params)) body.params = parsed;
    upd.mutate(
      { id: p.id, body },
      {
        onSuccess: () => {
          setDraft(null);
          setParams("");
          setNote("");
        },
      },
    );
  };
  const a = asg ?? p.assignments;
  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      title={p.name}
      description={`${p.key} · ${p.category} · v${p.version}${p.mandatory ? " · mandatory" : ""}`}
      wide
      footer={
        admin && tab === "def" ? (
          <>
            <Input aria-label="değişiklik notu" placeholder="değişiklik notu" value={note} onChange={(e) => setNote(e.target.value)} className="w-64" />
            <Button variant="primary" onClick={save} loading={upd.isPending} disabled={!!paramsErr || p.builtin && !!p.bound_to && false}>
              Kaydet (yeni sürüm)
            </Button>
          </>
        ) : undefined
      }
    >
      <Tabs
        value={tab}
        onValueChange={setTab}
        tabs={[
          { value: "def", label: "Tanım" },
          { value: "scope", label: "Kapsam" },
          { value: "versions", label: `Sürümler (${p.versions.length})` },
        ]}
      >
        <TabPanel value="def" className="space-y-3">
          {locked && (
            <Notice tone="info">
              <Lock className="mr-1 inline size-3.5" />
              Mandatory policy: her kapsamda enforce çalışır; kapatılamaz, monitor'e alınamaz, aksiyonu allow / warn'a zayıflatılamaz. Parametreler değiştirilebilir.
            </Notice>
          )}
          {p.bound_to && <Notice tone="info">Bu policy {p.bound_to} katmanında uygulanır; burada yalnız görünür ve parametreleri o katmanın ayarlarından gelir.</Notice>}
          <p className="text-xs text-ink-2">{p.evaluator?.description ?? p.description}</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Label>Ad</Label>
              <Input value={String(d.name)} disabled={!admin} onChange={(e) => setDraft({ ...d, name: e.target.value })} />
            </div>
            <div>
              <Label>Mode</Label>
              <Select value={String(d.mode)} disabled={!admin || locked} onChange={(e) => setDraft({ ...d, mode: e.target.value })}>
                {["monitor", "enforce", "disabled"].map((m) => (
                  <option key={m}>{m}</option>
                ))}
              </Select>
            </div>
            <div>
              <Label>Aksiyon</Label>
              <Select value={String(d.action)} disabled={!admin} onChange={(e) => setDraft({ ...d, action: e.target.value })}>
                {actions.map((m) => (
                  <option key={m}>{m}</option>
                ))}
              </Select>
            </div>
            <div>
              <Label>Severity</Label>
              <Select value={String(d.severity)} disabled={!admin} onChange={(e) => setDraft({ ...d, severity: e.target.value })}>
                {(meta?.severities ?? ["low", "medium", "high", "critical"]).map((m) => (
                  <option key={m}>{m}</option>
                ))}
              </Select>
            </div>
            <div>
              <Label>Durum</Label>
              <Select value={String(d.status)} disabled={!admin || locked} onChange={(e) => setDraft({ ...d, status: e.target.value })}>
                {["active", "inactive", "archived"].map((m) => (
                  <option key={m}>{m}</option>
                ))}
              </Select>
            </div>
            <div>
              <Label>Öncelik (küçük önce)</Label>
              <Input type="number" min={1} max={1000} value={Number(d.priority)} disabled={!admin} onChange={(e) => setDraft({ ...d, priority: Number(e.target.value) })} />
            </div>
          </div>
          <div>
            <Label>Açıklama</Label>
            <Textarea rows={2} value={String(d.description)} disabled={!admin} onChange={(e) => setDraft({ ...d, description: e.target.value })} />
          </div>
          <div>
            <Label>Parametreler (JSON; backend şemaya göre doğrular)</Label>
            <Textarea rows={8} className="font-mono text-2xs" value={paramsText} disabled={!admin} onChange={(e) => setParams(e.target.value)} />
            {paramsErr && <div className="mt-1 text-2xs text-st-critical-ink">Geçersiz JSON: {paramsErr}</div>}
          </div>
          <div className="text-2xs text-muted">
            Aşamalar: {p.stages.join(", ")} · oluşturan {p.created_by ?? "sistem"} · güncelleyen {p.updated_by ?? "—"} {p.updated_at && `(${fmtTime(p.updated_at)})`}
          </div>
          {admin && !p.bound_to && (
            <div className="flex items-center gap-2 border-t border-line pt-3">
              <Input aria-label="yeni key" placeholder="kopya için yeni key" value={cloneKey} onChange={(e) => setCloneKey(e.target.value)} className="w-56" />
              <Button size="sm" disabled={cloneKey.trim().length < 3} loading={clone.isPending} onClick={() => clone.mutate({ id: p.id, key: cloneKey.trim() }, { onSuccess: () => setCloneKey("") })}>
                <Copy /> Clone
              </Button>
            </div>
          )}
        </TabPanel>
        <TabPanel value="scope" className="space-y-2">
          <p className="text-xs text-muted">Kapsam kalıtımı: global → tenant → environment → agent → mcp_server → tool → graph_node. Mandatory policy global kapsamdan çıkarılamaz.</p>
          {a.map((x, i) => (
            <div key={i} className="flex gap-2">
              <Select aria-label="scope" value={x.scope} disabled={!admin} onChange={(e) => setAsg(a.map((y, j) => (j === i ? { ...y, scope: e.target.value, value: e.target.value === "global" ? null : y.value } : y)))} className="w-40">
                {SCOPES.map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </Select>
              <Input aria-label="değer" disabled={!admin || x.scope === "global"} value={x.value ?? ""} onChange={(e) => setAsg(a.map((y, j) => (j === i ? { ...y, value: e.target.value } : y)))} className="w-56" />
              {admin && a.length > 1 && (
                <Button size="sm" variant="ghost" onClick={() => setAsg(a.filter((_, j) => j !== i))}>
                  Kaldır
                </Button>
              )}
            </div>
          ))}
          {admin && (
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={() => setAsg([...a, { scope: "agent", value: "" }])}>
                Kapsam ekle
              </Button>
              <Button size="sm" variant="primary" disabled={!asg} loading={assign.isPending} onClick={() => assign.mutate({ id: p.id, version: p.version, assignments: a }, { onSuccess: () => setAsg(null) })}>
                Kapsamı kaydet
              </Button>
            </div>
          )}
        </TabPanel>
        <TabPanel value="versions">
          <table className="data-table">
            <thead>
              <tr>
                <th>Sürüm</th>
                <th>Değişiklik</th>
                <th>Not</th>
                <th>Yapan</th>
                <th>Zaman</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {p.versions.map((v) => (
                <tr key={v.version}>
                  <td className="text-xs tabular-nums">
                    v{v.version} {v.active && <Badge>aktif</Badge>}
                  </td>
                  <td className="text-xs">{v.change}</td>
                  <td className="max-w-48 truncate text-xs text-muted" title={v.note ?? ""}>
                    {v.note ?? "—"}
                  </td>
                  <td className="text-xs">{v.created_by ?? "sistem"}</td>
                  <td className="text-xs text-muted">{fmtTime(v.created_at)}</td>
                  <td>
                    {admin && !v.active && (
                      <Button size="sm" variant="ghost" loading={rb.isPending} onClick={() => rb.mutate({ id: p.id, version: v.version, note: `v${v.version} rollback` })}>
                        <Undo2 /> Rollback
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-2xs text-muted">Rollback eski sürümün içeriğiyle yeni bir sürüm oluşturur; geçmiş silinmez.</p>
        </TabPanel>
      </Tabs>
    </Dialog>
  );
}

// ------------------------------------------------------------------------------------------- Logs

function LogsTab({ range, initial }: { range: GrRange; initial: { run_id?: string; policy?: string } }) {
  const { data: meta } = useGrMeta();
  const [f, setF] = useState<LogFilters>({ range, limit: 50, offset: 0, run_id: initial.run_id, policy: initial.policy });
  const flt = { ...f, range };
  const { data, isLoading, error, refetch, isFetching, dataUpdatedAt } = useGrLogs(flt);
  const [sel, setSel] = useState<string | null>(null);
  const upd = (k: keyof LogFilters, v: string) => setF({ ...f, [k]: v || undefined, offset: 0 });
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select aria-label="karar" value={f.decision ?? ""} onChange={(e) => upd("decision", e.target.value)} className="w-40">
          <option value="">Tüm kararlar</option>
          {Object.entries(DECISION_TR).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </Select>
        <Select aria-label="kategori" value={f.category ?? ""} onChange={(e) => upd("category", e.target.value)} className="w-40">
          <option value="">Tüm kategoriler</option>
          {(meta?.categories ?? []).map((c) => (
            <option key={c}>{c}</option>
          ))}
        </Select>
        <Select aria-label="aşama" value={f.stage ?? ""} onChange={(e) => upd("stage", e.target.value)} className="w-40">
          <option value="">Tüm aşamalar</option>
          {(meta?.stages ?? []).map((s) => (
            <option key={s.key} value={s.key}>
              {s.key}
            </option>
          ))}
        </Select>
        <Input aria-label="policy" placeholder="policy key" value={f.policy ?? ""} onChange={(e) => upd("policy", e.target.value)} className="w-40" />
        <Input aria-label="run id" placeholder="run id" value={f.run_id ?? ""} onChange={(e) => upd("run_id", e.target.value)} className="w-48" />
        <SearchInput value={f.q ?? ""} onChange={(v) => upd("q", v)} placeholder="reason / tool ara…" className="w-48" />
        <div className="ml-auto">
          <RefreshControl onRefresh={() => void refetch()} fetching={isFetching} updatedAt={dataUpdatedAt} />
        </div>
      </div>
      <p className="text-2xs text-muted">Geçen (passed) değerlendirmeler koşu başına toplanır: tek satırda adet ve latency dağılımı görünür. İhlaller tek tek kaydedilir; snapshot'lar maskelenmiştir.</p>
      {error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : isLoading ? (
        <TableSkeleton cols={8} />
      ) : !data?.items.length ? (
        <EmptyState title="Kayıt yok" description="Bu filtrelerle değerlendirme kaydı bulunamadı." />
      ) : (
        <Card>
          <TableWrap fetching={isFetching}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Zaman</th>
                  <th>Policy</th>
                  <th>Aşama / node</th>
                  <th>Karar</th>
                  <th>Neden</th>
                  <th>Agent / run</th>
                  <th>Tool</th>
                  <th className="text-right">Adet</th>
                  <th className="text-right">Süre</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((e) => (
                  <tr key={e.id} className="cursor-pointer" onClick={() => setSel(e.id)}>
                    <td className="text-xs whitespace-nowrap text-muted">{fmtTime(e.ts)}</td>
                    <td>
                      <Mono className="text-2xs">{e.policy_key}</Mono>
                      {e.policy_version != null && <span className="text-2xs text-muted"> v{e.policy_version}</span>}
                    </td>
                    <td className="text-xs">
                      {e.stage}
                      {e.node && <span className="text-muted"> · {e.node}</span>}
                    </td>
                    <td>
                      <DecisionLabel d={e.decision} enforced={e.enforced} />
                    </td>
                    <td className="max-w-64 truncate text-xs" title={e.reason ?? ""}>
                      {e.reason ?? "—"}
                    </td>
                    <td className="text-xs">
                      {e.agent_key ?? e.run_kind ?? "—"}
                      <div className="font-mono text-2xs text-muted">{shortId(e.run_id)}</div>
                    </td>
                    <td className="text-2xs">{e.tool ? `${e.mcp_server ?? ""}/${e.tool}` : "—"}</td>
                    <td className="text-right text-xs tabular-nums">{fmtNum(e.count)}</td>
                    <td className="text-right text-xs tabular-nums">{fmtMsSmall(e.duration_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
          <Pagination page={Math.floor(f.offset / f.limit) + 1} pages={Math.max(1, Math.ceil(data.total / f.limit))} total={data.total} onPage={(pg) => setF({ ...f, offset: (pg - 1) * f.limit })} />
        </Card>
      )}
      {sel && <LogDialog id={sel} onClose={() => setSel(null)} onPick={setSel} />}
    </div>
  );
}

function LogDialog({ id, onClose, onPick }: { id: string; onClose: () => void; onPick: (id: string) => void }) {
  const { data: e, isLoading, error } = useGrLog(id);
  const [tab, setTab] = useState("summary");
  const diff = useMemo(() => (e?.snapshot ? jsonDiff(e.snapshot.before ?? null, e.snapshot.after ?? null) : []), [e]);
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()} title="Değerlendirme detayı" description={e ? `${e.policy_key} · ${e.stage} · ${fmtTime(e.ts)}` : undefined} wide>
      {error ? (
        <ErrorState error={error} />
      ) : isLoading || !e ? (
        <TableSkeleton rows={5} cols={2} />
      ) : (
        <Tabs
          value={tab}
          onValueChange={setTab}
          tabs={[
            { value: "summary", label: "Özet" },
            { value: "diff", label: "Before / After" },
            { value: "run", label: `Koşu (${e.run_evaluations?.length ?? 0})` },
            { value: "raw", label: "JSON" },
          ]}
        >
          <TabPanel value="summary" className="space-y-1 text-xs">
            <Field label="Karar">
              <DecisionLabel d={e.decision} enforced={e.enforced} />
            </Field>
            <Field label="Policy">
              <Mono>{e.policy_key}</Mono> v{e.policy_version ?? "—"} {e.policy?.mandatory && <Lock className="inline size-3" />}
            </Field>
            <Field label="Kategori / severity">
              {e.category} / <SevLabel s={e.severity} />
            </Field>
            <Field label="Aksiyon">{e.action ?? "—"}</Field>
            <Field label="Neden">{e.reason ?? "—"}</Field>
            <Field label="Kod">{e.code ?? "—"}</Field>
            <Field label="Agent">{[e.agent_key, e.agent_version && `v${e.agent_version}`, e.run_kind].filter(Boolean).join(" · ") || "—"}</Field>
            <Field label="Run / thread">
              <Mono>{e.run_id ?? "—"}</Mono> / <Mono>{e.thread_id ?? "—"}</Mono>
            </Field>
            <Field label="Kullanıcı rolü">{e.user_role ?? "—"}</Field>
            <Field label="Tool">{e.tool ? `${e.mcp_server ?? ""}/${e.tool}` : "—"}</Field>
            <Field label="Süre">
              {fmtMsSmall(e.duration_ms)}
              {e.count > 1 && ` (max ${fmtMsSmall(e.duration_max_ms)}, ${e.count} değerlendirme)`}
            </Field>
            <Field label="Trace">{e.trace_id ? <Mono>{e.trace_id}</Mono> : "—"}</Field>
            {e.error && <Field label="Hata">{e.error}</Field>}
            {Object.keys(e.thresholds ?? {}).length > 0 && (
              <div className="pt-2">
                <div className="mb-1 font-medium text-ink">Threshold'lar</div>
                <table className="data-table">
                  <tbody>
                    {Object.entries(e.thresholds).map(([k, t]) => (
                      <tr key={k}>
                        <td className="font-mono text-2xs">{k}</td>
                        <td className="text-xs">limit {String(t.limit)}</td>
                        <td className="text-xs">gerçek {String(t.actual)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {e.matched.length > 0 && <JsonView value={e.matched} title="Eşleşmeler (maskeli)" open={1} maxH="max-h-48" />}
            {e.latency_buckets && (
              <Field label="Latency kovaları">
                {e.latency_labels.map((l, i) => `${l}: ${e.latency_buckets![i]}`).join(" · ")}
              </Field>
            )}
          </TabPanel>
          <TabPanel value="diff" className="space-y-3">
            {e.snapshot ? (
              <>
                <DiffTable changes={diff.map((d) => ({ ...d, old: d.old, new: d.new }))} />
                <div className="grid gap-2 sm:grid-cols-2">
                  <JsonView value={e.snapshot.before ?? null} title="Before (maskeli)" open={2} maxH="max-h-64" />
                  <JsonView value={e.snapshot.after ?? null} title="After (maskeli)" open={2} maxH="max-h-64" />
                </div>
              </>
            ) : (
              <div className="text-xs text-muted">Snapshot yakalanmadı (geçen değerlendirme, retention ya da snapshot kapalı).</div>
            )}
          </TabPanel>
          <TabPanel value="run">
            {e.run_evaluations?.length ? (
              <table className="data-table">
                <tbody>
                  {e.run_evaluations.map((r) => (
                    <tr key={r.id} className={cn("cursor-pointer", r.id === e.id && "bg-surface-2")} onClick={() => onPick(r.id)}>
                      <td className="text-xs text-muted">{fmtTime(r.ts, false)}</td>
                      <td className="text-xs">{r.stage}</td>
                      <td className="font-mono text-2xs">{r.policy_key}</td>
                      <td>
                        <DecisionLabel d={r.decision} enforced={r.enforced} />
                      </td>
                      <td className="text-right text-xs tabular-nums">{r.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="text-xs text-muted">Bu kayıt bir koşuya bağlı değil.</div>
            )}
          </TabPanel>
          <TabPanel value="raw">
            <JsonView value={e} open={1} />
          </TabPanel>
        </Tabs>
      )}
    </Dialog>
  );
}

// ------------------------------------------------------------------------------------------- Violations

const V_FLOW: Record<string, string[]> = {
  open: ["acknowledged", "investigating", "resolved", "false_positive"],
  acknowledged: ["investigating", "resolved", "false_positive"],
  investigating: ["resolved", "false_positive"],
  resolved: ["open"],
  false_positive: ["open"],
};

function ViolationTable({ items, onPick }: { items: Violation[]; onPick: (v: Violation) => void }) {
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Son görülme</th>
          <th>Severity</th>
          <th>Policy</th>
          <th>Neden</th>
          <th>Agent / tool</th>
          <th>Karar</th>
          <th className="text-right">Tekrar</th>
          <th>Durum</th>
        </tr>
      </thead>
      <tbody>
        {items.map((v) => (
          <tr key={v.id} className="cursor-pointer" onClick={() => onPick(v)}>
            <td className="text-xs whitespace-nowrap text-muted">{fmtRelative(v.last_seen)}</td>
            <td>
              <SevLabel s={v.severity} />
            </td>
            <td className="font-mono text-2xs">{v.policy_key}</td>
            <td className="max-w-72 truncate text-xs" title={v.reason ?? ""}>
              {v.reason ?? "—"}
            </td>
            <td className="text-xs">
              {v.agent_key ?? "—"}
              {v.tool && <span className="text-muted"> · {v.tool}</span>}
            </td>
            <td>
              <DecisionLabel d={v.decision} enforced={v.enforced} />
            </td>
            <td className="text-right text-xs tabular-nums">{fmtNum(v.occurrences)}</td>
            <td className="text-xs">{v.status}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ViolationsTab({ canTriage }: { canTriage: boolean }) {
  const [f, setF] = useState({ status: "open", severity: "", q: "", limit: 50, offset: 0 });
  const { data, isLoading, error, refetch, isFetching } = useGrViolations({ ...f, status: f.status || undefined, severity: f.severity || undefined, q: f.q || undefined });
  const [sel, setSel] = useState<Violation | null>(null);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select aria-label="durum" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value, offset: 0 })} className="w-40">
          <option value="">Tüm durumlar</option>
          {Object.keys(V_FLOW).map((s) => (
            <option key={s}>{s}</option>
          ))}
        </Select>
        <Select aria-label="severity" value={f.severity} onChange={(e) => setF({ ...f, severity: e.target.value, offset: 0 })} className="w-36">
          <option value="">Tüm severity</option>
          {["critical", "high", "medium", "low"].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </Select>
        <SearchInput value={f.q} onChange={(q) => setF({ ...f, q, offset: 0 })} placeholder="policy / neden ara…" className="w-56" />
      </div>
      <p className="text-2xs text-muted">Aynı policy + agent + tool + kod parmak izine sahip ihlaller tek kayıtta toplanır (tekrar sayısı artar). False positive işaretlemesi Analytics'teki FP oranını besler.</p>
      {error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : isLoading ? (
        <TableSkeleton cols={8} />
      ) : !data?.items.length ? (
        <EmptyState title="İhlal yok" icon={<ShieldCheck />} />
      ) : (
        <Card>
          <TableWrap fetching={isFetching}>
            <ViolationTable items={data.items} onPick={setSel} />
          </TableWrap>
          <Pagination page={Math.floor(f.offset / f.limit) + 1} pages={Math.max(1, Math.ceil(data.total / f.limit))} total={data.total} onPage={(pg) => setF({ ...f, offset: (pg - 1) * f.limit })} />
        </Card>
      )}
      {sel && <ViolationDialog v={sel} canTriage={canTriage} onClose={() => setSel(null)} />}
    </div>
  );
}

function ViolationDialog({ v, canTriage, onClose }: { v: Violation; canTriage: boolean; onClose: () => void }) {
  const triage = useTriage();
  const [note, setNote] = useState("");
  const [assignee, setAssignee] = useState(v.assigned_to ?? "");
  const [, setSp] = useSearchParams();
  const go = (status?: string) =>
    triage.mutate({ id: v.id, body: { status, note: note || undefined, assigned_to: assignee !== (v.assigned_to ?? "") ? assignee : undefined } }, { onSuccess: onClose });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="İhlal" description={`${v.policy_key} · ${v.status}`} wide>
      <div className="space-y-1 text-xs">
        <Field label="Severity">
          <SevLabel s={v.severity} />
        </Field>
        <Field label="Karar">
          <DecisionLabel d={v.decision} enforced={v.enforced} /> {v.action_taken && <span className="text-muted">({v.action_taken})</span>}
        </Field>
        <Field label="Neden">{v.reason ?? "—"}</Field>
        <Field label="Agent / node / aşama">{[v.agent_key, v.node, v.stage].filter(Boolean).join(" · ") || "—"}</Field>
        <Field label="Tool">{v.tool ? `${v.mcp_server ?? ""}/${v.tool}` : "—"}</Field>
        <Field label="İlk / son görülme">
          {fmtTime(v.first_seen)} / {fmtTime(v.last_seen)} ({v.occurrences} kez)
        </Field>
        <Field label="Atanan">{v.assigned_to ?? "—"}</Field>
        {v.last_evaluation_id && (
          <Field label="Son değerlendirme">
            <button type="button" className="text-accent underline" onClick={() => setSp({ tab: "logs", run_id: v.run_id ?? "" })}>
              Koşunun kayıtlarını aç
            </button>
          </Field>
        )}
      </div>
      {v.history.length > 0 && (
        <div className="mt-3">
          <div className="mb-1 text-xs font-medium">Geçmiş</div>
          <ul className="space-y-0.5 text-2xs text-ink-2">
            {v.history.map((h, i) => (
              <li key={i}>
                {fmtTime(h.ts)} · {h.by} → {h.status}
                {h.assigned_to && ` (atanan ${h.assigned_to})`}
                {h.note && `: ${h.note}`}
              </li>
            ))}
          </ul>
        </div>
      )}
      {canTriage && (
        <div className="mt-3 space-y-2 border-t border-line pt-3">
          <div className="flex gap-2">
            <Input aria-label="atanan" placeholder="atanacak kullanıcı" value={assignee} onChange={(e) => setAssignee(e.target.value)} className="w-48" />
            <Input aria-label="not" placeholder="not" value={note} onChange={(e) => setNote(e.target.value)} className="flex-1" />
          </div>
          <div className="flex flex-wrap gap-2">
            {(V_FLOW[v.status] ?? []).map((s) => (
              <Button key={s} size="sm" variant={s === "false_positive" ? "outline" : "secondary"} loading={triage.isPending} onClick={() => go(s)}>
                {s}
              </Button>
            ))}
            {assignee !== (v.assigned_to ?? "") && (
              <Button size="sm" variant="ghost" onClick={() => go(undefined)}>
                Yalnız ata
              </Button>
            )}
          </div>
        </div>
      )}
    </Dialog>
  );
}

// ------------------------------------------------------------------------------------------- Tester

function TesterTab() {
  const { data: meta } = useGrMeta();
  const { data: samples } = useGrSamples();
  const { data: hist } = useGrTestHistory();
  const run = useRunTest();
  const [stage, setStage] = useState("input");
  const [text, setText] = useState("");
  const [data, setData] = useState("");
  const [ctx, setCtx] = useState({ agent_id: "", user_role: "sre", mcp_server: "", tool: "" });
  const [expected, setExpected] = useState("");
  const [isAttack, setIsAttack] = useState<"" | "yes" | "no">("");
  const [res, setRes] = useState<TestResult | null>(null);
  let dataErr: string | null = null;
  let parsed: unknown = undefined;
  if (data.trim()) {
    try {
      parsed = JSON.parse(data);
    } catch (e) {
      dataErr = (e as Error).message;
    }
  }
  const go = () =>
    run.mutate(
      {
        stage,
        text: text || undefined,
        data: parsed,
        context: Object.fromEntries(Object.entries(ctx).filter(([, v]) => v)),
        expected_decision: expected || undefined,
        is_attack: isAttack === "" ? undefined : isAttack === "yes",
      },
      { onSuccess: setRes },
    );
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <Card>
        <CardHeader title="Policy Tester" description="Dry-run: tool çağrılmaz, LLM çağrılmaz, kayıt yazılmaz (yalnız test geçmişi). Mandatory policy'ler de değerlendirilir." />
        <CardBody className="space-y-3">
          <div className="flex flex-wrap gap-2">
            <Select aria-label="aşama" value={stage} onChange={(e) => setStage(e.target.value)} className="w-44">
              {(meta?.stages ?? []).map((s) => (
                <option key={s.key} value={s.key}>
                  {s.key}
                </option>
              ))}
            </Select>
            <Select
              aria-label="örnek"
              value=""
              onChange={(e) => {
                const s = samples?.items.find((x) => x.key === e.target.value);
                if (!s) return;
                setStage(s.stage);
                setText(s.text ?? "");
                setData(s.json ? JSON.stringify(s.json, null, 2) : "");
                setCtx({ ...ctx, mcp_server: s.mcp_server ?? "", tool: s.tool ?? "" });
                setRes(null);
              }}
              className="w-56"
            >
              <option value="">Örnek senaryo yükle…</option>
              {(samples?.items ?? []).map((s) => (
                <option key={s.key} value={s.key}>
                  {s.key} ({s.stage})
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>Metin</Label>
            <Textarea rows={5} value={text} onChange={(e) => setText(e.target.value)} placeholder="kullanıcı girdisi, model çıktısı, MCP yanıtı…" />
          </div>
          <div>
            <Label>Yapısal veri (JSON, opsiyonel: tool argümanları, MCP yanıtı, memory kaydı)</Label>
            <Textarea rows={5} className="font-mono text-2xs" value={data} onChange={(e) => setData(e.target.value)} />
            {dataErr && <div className="mt-1 text-2xs text-st-critical-ink">Geçersiz JSON: {dataErr}</div>}
          </div>
          <div className="grid grid-cols-2 gap-2">
            {(["agent_id", "user_role", "mcp_server", "tool"] as const).map((k) => (
              <Input key={k} aria-label={k} placeholder={k} value={ctx[k]} onChange={(e) => setCtx({ ...ctx, [k]: e.target.value })} />
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Select aria-label="beklenen karar" value={expected} onChange={(e) => setExpected(e.target.value)} className="w-44">
              <option value="">Beklenen karar (etiket)</option>
              {Object.keys(DECISION_TR).map((k) => (
                <option key={k}>{k}</option>
              ))}
            </Select>
            <Select aria-label="saldırı mı" value={isAttack} onChange={(e) => setIsAttack(e.target.value as "" | "yes" | "no")} className="w-40">
              <option value="">Saldırı etiketi yok</option>
              <option value="yes">saldırı</option>
              <option value="no">normal</option>
            </Select>
            <Button variant="primary" className="ml-auto" onClick={go} loading={run.isPending} disabled={!!dataErr || (!text && parsed === undefined)}>
              <Play /> Çalıştır
            </Button>
          </div>
          <p className="text-2xs text-muted">Etiketli testler (beklenen karar / saldırı) Analytics'teki FP / FN oranını besler; etiketsiz testler oran hesabına girmez.</p>
        </CardBody>
      </Card>
      <div className="space-y-3">
        <Card>
          <CardHeader title="Sonuç" />
          <CardBody>
            {!res ? (
              <div className="text-xs text-muted">Henüz çalıştırılmadı.</div>
            ) : (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center gap-3 text-xs">
                  <DecisionLabel d={res.decision} />
                  {res.stopped && <Badge>koşu durur</Badge>}
                  <span className="text-muted">
                    {res.evaluated_policies} policy · {fmtMsSmall(res.duration_ms)} · yan etki: {res.side_effects}
                  </span>
                </div>
                {res.message && <Notice tone="warning">{res.message}</Notice>}
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Policy</th>
                      <th>Karar</th>
                      <th>Neden</th>
                      <th className="text-right">Süre</th>
                    </tr>
                  </thead>
                  <tbody>
                    {res.evaluations.map((ev) => (
                      <tr key={ev.policy_key}>
                        <td>
                          <Mono className="text-2xs">{ev.policy_key}</Mono> {ev.mandatory && <Lock className="inline size-3 text-muted" />}
                          <div className="text-2xs text-muted">
                            {ev.mode} · {ev.action}
                          </div>
                        </td>
                        <td>
                          <DecisionLabel d={ev.decision} enforced={ev.enforced} />
                          {ev.would_request_approval && <div className="text-2xs text-st-warning-ink">onay isteği açılırdı</div>}
                        </td>
                        <td className="max-w-64 text-xs break-words">{ev.error ?? ev.reason ?? "—"}</td>
                        <td className="text-right text-xs tabular-nums">{fmtMsSmall(ev.duration_ms)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {res.changed && (
                  <div>
                    <div className="mb-1 text-xs font-medium">Before / After</div>
                    <DiffTable changes={jsonDiff(res.before, res.after).map((d) => ({ ...d, old: d.old, new: d.new }))} />
                  </div>
                )}
              </div>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Test geçmişi" />
          <CardBody>
            {hist?.items.length ? (
              <table className="data-table">
                <tbody>
                  {hist.items.slice(0, 15).map((h) => (
                    <tr key={h.id}>
                      <td className="text-xs text-muted">{fmtRelative(h.created_at)}</td>
                      <td className="text-xs">{h.created_by}</td>
                      <td className="text-xs">{h.stage}</td>
                      <td>
                        <DecisionLabel d={h.decision} />
                      </td>
                      <td className="text-xs">
                        {h.expected_decision ? (h.expected_decision === h.decision ? <span className="text-ink-2">beklenen ✓</span> : <span className="text-st-critical-ink">beklenen {h.expected_decision}</span>) : <span className="text-muted">etiketsiz</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="text-xs text-muted">Kayıt yok</div>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------------------------------- Approvals

function ApprovalsTab({ canDecide }: { canDecide: boolean }) {
  const { user } = useAuth();
  const [f, setF] = useState({ status: "pending", limit: 50, offset: 0 });
  const { data, isLoading, error, refetch, isFetching } = useGrApprovals({ ...f, status: f.status || undefined });
  const decide = useDecideApproval();
  const cancel = useCancelApproval();
  const [dlg, setDlg] = useState<{ id: string; approve: boolean } | null>(null);
  const [reason, setReason] = useState("");
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <Select aria-label="durum" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value, offset: 0 })} className="w-40">
          <option value="">Tüm durumlar</option>
          {["pending", "approved", "rejected", "expired", "cancelled", "consumed"].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </Select>
        <div className="ml-auto">
          <RefreshControl onRefresh={() => void refetch()} fetching={isFetching} />
        </div>
      </div>
      <Notice tone="info">
        Onay dört göz kuralıyla verilir: isteyen kişi kendi isteğini onaylayamaz. Onay yalnız aynı aksiyon + parametre (action hash) için tek kez kullanılır ve süresi dolunca geçersizdir; kullanım anında policy yeniden değerlendirilir.
      </Notice>
      {error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : isLoading ? (
        <TableSkeleton cols={7} />
      ) : !data?.items.length ? (
        <EmptyState title="Onay isteği yok" />
      ) : (
        <Card>
          <TableWrap fetching={isFetching}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>İstenme</th>
                  <th>Aksiyon</th>
                  <th>Hedef</th>
                  <th>Risk</th>
                  <th>İsteyen</th>
                  <th>Gerekli rol</th>
                  <th>Durum</th>
                  <th>Bitiş</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.items.map((a) => (
                  <tr key={a.id}>
                    <td className="text-xs text-muted">{fmtRelative(a.requested_at)}</td>
                    <td>
                      <Mono className="text-2xs">{a.requested_action}</Mono>
                      <div className="text-2xs text-muted" title={JSON.stringify(a.action_params)}>
                        {a.policy_key} · hash {a.action_hash.slice(0, 10)}
                      </div>
                    </td>
                    <td className="text-xs">{a.target_resource}</td>
                    <td>
                      <SevLabel s={a.risk_level} />
                    </td>
                    <td className="text-xs">{a.requested_by}</td>
                    <td className="text-xs">{a.required_role}</td>
                    <td className="text-xs">
                      {a.status}
                      {a.decided_by && <div className="text-2xs text-muted">{a.decided_by}</div>}
                    </td>
                    <td className="text-xs text-muted">{fmtTime(a.expires_at)}</td>
                    <td className="whitespace-nowrap">
                      {a.status === "pending" && canDecide && a.requested_by !== user?.id && (
                        <>
                          <Button size="sm" variant="primary" onClick={() => setDlg({ id: a.id, approve: true })}>
                            Onayla
                          </Button>{" "}
                          <Button size="sm" variant="outline" onClick={() => setDlg({ id: a.id, approve: false })}>
                            Reddet
                          </Button>
                        </>
                      )}
                      {a.status === "pending" && a.requested_by === user?.id && (
                        <Button size="sm" variant="ghost" loading={cancel.isPending} onClick={() => cancel.mutate(a.id)}>
                          İptal
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
          <Pagination page={Math.floor(f.offset / f.limit) + 1} pages={Math.max(1, Math.ceil(data.total / f.limit))} total={data.total} onPage={(pg) => setF({ ...f, offset: (pg - 1) * f.limit })} />
        </Card>
      )}
      {dlg && (
        <Dialog
          open
          onOpenChange={(v) => !v && setDlg(null)}
          title={dlg.approve ? "Onayla" : "Reddet"}
          description="Karar audit kaydına yazılır."
          footer={
            <Button
              variant={dlg.approve ? "primary" : "danger"}
              loading={decide.isPending}
              disabled={!dlg.approve && reason.trim().length < 3}
              onClick={() =>
                decide.mutate(
                  { id: dlg.id, approve: dlg.approve, reason: reason || undefined },
                  {
                    onSuccess: () => {
                      setDlg(null);
                      setReason("");
                    },
                  },
                )
              }
            >
              {dlg.approve ? "Onayla" : "Reddet"}
            </Button>
          }
        >
          <Label>Gerekçe {dlg.approve ? "(opsiyonel)" : "(zorunlu)"}</Label>
          <Textarea rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />
        </Dialog>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------------- Analytics

function AnalyticsTab({ range }: { range: GrRange }) {
  const { data: a, isLoading, error, refetch } = useGrAnalytics(range);
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />;
  if (isLoading || !a) return <TableSkeleton cols={6} />;
  const pivot = (rows: { decision: string; n: number }[], key: (r: never) => string) => {
    const m = new Map<string, Record<string, number>>();
    for (const r of rows) {
      const k = key(r as never);
      m.set(k, { ...(m.get(k) ?? {}), [r.decision]: (m.get(k)?.[r.decision] ?? 0) + r.n });
    }
    return [...m.entries()];
  };
  const decs = ["passed", "warned", "redacted", "blocked", "approval_required", "error"];
  const Pivot = ({ title, rows }: { title: string; rows: [string, Record<string, number>][] }) => (
    <Card>
      <CardHeader title={title} />
      <CardBody>
        {rows.length ? (
          <table className="data-table">
            <thead>
              <tr>
                <th />
                {decs.map((d) => (
                  <th key={d} className="text-right">
                    {DECISION_TR[d]}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map(([k, v]) => (
                <tr key={k}>
                  <td className="text-xs">{k}</td>
                  {decs.map((d) => (
                    <td key={d} className="text-right text-xs tabular-nums">
                      {v[d] ? fmtNum(v[d]) : <span className="text-muted">·</span>}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="text-xs text-muted">Veri yok</div>
        )}
      </CardBody>
    </Card>
  );
  const L = a.labels;
  return (
    <div className="space-y-3">
      <MetricStrip>
        <StatTile label="FP oranı (triage)" value={fmtPct(L.triage.false_positive_rate)} missing={L.triage.false_positive_rate == null} sub={`${L.triage.false_positive} FP / ${L.triage.true_positive} TP etiketi`} />
        <StatTile label="FP oranı (etiketli test)" value={fmtPct(L.tests.false_positive_rate)} missing={L.tests.false_positive_rate == null} sub={`${L.tests.labeled} etiketli test`} />
        <StatTile label="FN oranı (etiketli test)" value={fmtPct(L.tests.false_negative_rate)} missing={L.tests.false_negative_rate == null} sub={`${L.tests.false_negative} kaçırılan saldırı`} />
      </MetricStrip>
      <p className="text-2xs text-muted">{L.note}</p>
      <div className="grid gap-3 lg:grid-cols-2">
        <Pivot title="Kategoriye göre" rows={pivot(a.by_category, (r: { category: string }) => r.category)} />
        <Pivot title="Aşamaya göre" rows={pivot(a.by_stage, (r: { stage: string }) => r.stage)} />
      </div>
      <Card>
        <CardHeader title="Policy bazında" description={a.latency_note} />
        <TableWrap maxH="max-h-none">
          <table className="data-table">
            <thead>
              <tr>
                <th>Policy</th>
                <th>Kategori</th>
                <th className="text-right">Değerlendirme</th>
                <th className="text-right">İhlal</th>
                <th className="text-right">Oran</th>
                <th className="text-right">Ort.</th>
                <th className="text-right">p50 ≈</th>
                <th className="text-right">p95 ≈</th>
                <th className="text-right">Max</th>
              </tr>
            </thead>
            <tbody>
              {a.policies.map((p) => (
                <tr key={p.policy_key}>
                  <td className="font-mono text-2xs">{p.policy_key}</td>
                  <td className="text-xs">{p.category}</td>
                  <td className="text-right text-xs tabular-nums">{fmtNum(p.evaluations)}</td>
                  <td className="text-right text-xs tabular-nums">{fmtNum(p.violations)}</td>
                  <td className="text-right text-xs tabular-nums">{fmtPct(p.rate)}</td>
                  <td className="text-right text-xs tabular-nums">{fmtMsSmall(p.avg_ms)}</td>
                  <td className="text-right text-xs tabular-nums">{fmtMsSmall(p.p50_ms_approx)}</td>
                  <td className="text-right text-xs tabular-nums">{fmtMsSmall(p.p95_ms_approx)}</td>
                  <td className="text-right text-xs tabular-nums">{fmtMsSmall(p.max_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      </Card>
      <Card>
        <CardHeader title="Monitor vs enforce" description="Monitor modundaki policy'ler karar üretir ama uygulamaz; enforce'a almadan önce etkisini burada görün." />
        <CardBody>
          <table className="data-table">
            <tbody>
              {a.monitor_vs_enforce.map((r, i) => (
                <tr key={i}>
                  <td className="text-xs">{r.enforced ? "enforce" : "monitor"}</td>
                  <td>
                    <DecisionLabel d={r.decision} enforced={r.enforced} />
                  </td>
                  <td className="text-right text-xs tabular-nums">{fmtNum(r.n)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardBody>
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------------------------------- Settings

function SettingsTab({ admin }: { admin: boolean }) {
  const { data: c, isLoading, error, refetch } = useGrConfig();
  const save = useSaveConfig();
  const kill = useKillSwitch();
  const { data: audit } = useGrAudit();
  const { data: verify, refetch: reverify, isFetching: verifying } = useGrAuditVerify();
  const [d, setD] = useState<Record<string, unknown> | null>(null);
  const [killReason, setKillReason] = useState("");
  const [killAgents, setKillAgents] = useState<string | null>(null);
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />;
  if (isLoading || !c) return <TableSkeleton rows={6} cols={2} />;
  const cur = (d ?? c.data) as GrConfigData;
  const patch = (path: string[], v: unknown) => {
    const n = structuredClone(cur) as Record<string, unknown>;
    let o = n;
    for (const k of path.slice(0, -1)) o = (o[k] = { ...((o[k] as Record<string, unknown>) ?? {}) }) as Record<string, unknown>;
    o[path[path.length - 1]] = v;
    setD(n);
  };
  const num = (label: string, path: string[], min: number, max: number, hint?: ReactNode) => {
    let v: unknown = cur;
    for (const k of path) v = (v as Record<string, unknown>)?.[k];
    return (
      <div>
        <Label>{label}</Label>
        <Input type="number" min={min} max={max} value={Number(v ?? 0)} disabled={!admin} onChange={(e) => patch(path, Number(e.target.value))} />
        {hint && <div className="mt-0.5 text-2xs text-muted">{hint}</div>}
      </div>
    );
  };
  const fixed = new Set(c.fixed.fail_closed_categories);
  const ks = c.data.kill_switch;
  return (
    <div className="space-y-3">
      <Card className={cn(ks.global && "border-st-critical")}>
        <CardHeader
          title={
            <span className="inline-flex items-center gap-1.5">
              <Power className="size-4" /> Kill switch
            </span>
          }
          description="Açıkken yeni agent koşuları başlamaz; çalışan koşular bir sonraki node / tool çağrısında sonlandırılır. Tüm değişiklikler audit kaydına yazılır."
        />
        <CardBody className="space-y-2">
          <div className="text-xs">
            Durum: {ks.global ? <span className="font-medium text-st-critical-ink">GLOBAL AÇIK</span> : <span>kapalı</span>}
            {ks.agents.length > 0 && <span className="text-muted"> · agent bazında: {ks.agents.join(", ")}</span>}
          </div>
          {admin && (
            <div className="flex flex-wrap items-center gap-2">
              <Input aria-label="agent listesi" placeholder="agent id'leri (virgülle)" value={killAgents ?? ks.agents.join(", ")} onChange={(e) => setKillAgents(e.target.value)} className="w-64" />
              <Input aria-label="gerekçe" placeholder="gerekçe (zorunlu)" value={killReason} onChange={(e) => setKillReason(e.target.value)} className="w-64" />
              {(["on", "off", "agents"] as const).map((m) => (
                <Button
                  key={m}
                  size="sm"
                  variant={m === "on" ? "danger" : "secondary"}
                  disabled={killReason.trim().length < 3 || (m === "on" && ks.global) || (m === "off" && !ks.global)}
                  loading={kill.isPending}
                  onClick={() =>
                    kill.mutate(
                      {
                        revision: c.revision,
                        global: m === "on" ? true : m === "off" ? false : ks.global,
                        agents: (killAgents ?? ks.agents.join(",")).split(",").map((x) => x.trim()).filter(Boolean),
                        reason: killReason.trim(),
                      },
                      {
                        onSuccess: () => {
                          setKillReason("");
                          setKillAgents(null);
                          setD(null);
                        },
                      },
                    )
                  }
                >
                  {m === "on" ? <ShieldAlert /> : null}
                  {m === "on" ? "Global aç" : m === "off" ? "Global kapat" : "Agent listesini kaydet"}
                </Button>
              ))}
            </div>
          )}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Yapılandırma" description={`revision ${c.revision}${c.updated_by ? ` · ${c.updated_by} · ${fmtTime(c.updated_at)}` : ""}`} />
        <CardBody className="space-y-4">
          {!admin && <Notice tone="info">Yalnız admin değiştirebilir.</Notice>}
          <div className="grid gap-3 sm:grid-cols-3">
            <div>
              <Label>Yeni opsiyonel policy varsayılan modu</Label>
              <Select value={String(cur.optional_defaults?.mode ?? "monitor")} disabled={!admin} onChange={(e) => patch(["optional_defaults", "mode"], e.target.value)}>
                <option>monitor</option>
                <option>enforce</option>
              </Select>
            </div>
            {num("Değerlendirme timeout (ms)", ["evaluation_timeout_ms"], 1, 5000, "Aşılırsa sonuç geçerli kalır, sağlık metric'inde timeout sayılır")}
            {num("Onay süresi (dk)", ["approval_timeout_minutes"], 1, 1440)}
            {num("Evaluation retention (gün)", ["retention", "evaluation_days"], 1, 365)}
            {num("Snapshot retention (gün)", ["retention", "snapshot_days"], 1, 365)}
            {num("Audit retention (gün)", ["retention", "audit_days"], 30, 3650)}
            {num("Test geçmişi retention (gün)", ["retention", "test_run_days"], 1, 365)}
            {num("Snapshot üst sınırı (byte)", ["masking", "max_snapshot_bytes"], 1024, 262144)}
            <div>
              <Label>Snapshot yakalama</Label>
              <Select value={cur.masking?.capture_snapshots ? "on" : "off"} disabled={!admin} onChange={(e) => patch(["masking", "capture_snapshots"], e.target.value === "on")}>
                <option value="on">açık (maskeli)</option>
                <option value="off">kapalı</option>
              </Select>
            </div>
            <div>
              <Label>Bildirim minimum severity</Label>
              <Select value={String(cur.notifications?.min_severity ?? "high")} disabled={!admin} onChange={(e) => patch(["notifications", "min_severity"], e.target.value)}>
                {["low", "medium", "high", "critical"].map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </Select>
            </div>
            <div>
              <Label>Classifier</Label>
              <Select value={String(cur.classifier?.mode ?? "deterministic")} disabled>
                {c.fixed.classifier_modes.map((m) => (
                  <option key={m}>{m}</option>
                ))}
              </Select>
              <div className="mt-0.5 text-2xs text-muted">Güvenlik kararı yalnız deterministik kurallarla verilir.</div>
            </div>
          </div>
          <div>
            <div className="mb-1 text-xs font-medium">Fail mode (değerlendirme hata verirse)</div>
            <table className="data-table">
              <tbody>
                {Object.entries(cur.fail_mode ?? {}).map(([k, v]) => (
                  <tr key={k}>
                    <td className="text-xs">{k}</td>
                    <td>
                      {fixed.has(k) ? (
                        <span className="inline-flex items-center gap-1 text-xs">
                          <Lock className="size-3 text-muted" /> closed (sabit)
                        </span>
                      ) : (
                        <Select aria-label={`${k} fail mode`} value={String(v)} disabled={!admin} onChange={(e) => patch(["fail_mode", k], e.target.value)} className="w-32">
                          <option>open</option>
                          <option>closed</option>
                        </Select>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {admin && (
            <div className="flex justify-end gap-2">
              <Button variant="ghost" disabled={!d} onClick={() => setD(null)}>
                Vazgeç
              </Button>
              <Button variant="primary" disabled={!d} loading={save.isPending} onClick={() => save.mutate({ revision: c.revision, data: cur as Record<string, unknown> }, { onSuccess: () => setD(null) })}>
                Kaydet
              </Button>
            </div>
          )}
        </CardBody>
      </Card>
      <Card>
        <CardHeader
          title={
            <span className="inline-flex items-center gap-1.5">
              <History className="size-4" /> Audit
            </span>
          }
          description="Append-only hash zinciri: kayıtlar değiştirilemez; doğrulama zinciri baştan hesaplar."
          actions={
            <Button size="sm" loading={verifying} onClick={() => void reverify()}>
              Zinciri doğrula
            </Button>
          }
        />
        <CardBody className="space-y-2">
          {verify && (
            <div className={cn("text-xs", verify.ok ? "text-ink-2" : "text-st-critical-ink")}>
              {verify.ok ? `Zincir sağlam (${verify.checked} kayıt).` : `Zincir bozuk: seq ${verify.broken_at}.`}
            </div>
          )}
          <TableWrap maxH="max-h-96">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Seq</th>
                  <th>Zaman</th>
                  <th>Yapan</th>
                  <th>Aksiyon</th>
                  <th>Hedef</th>
                  <th>Detay</th>
                </tr>
              </thead>
              <tbody>
                {(audit?.items ?? []).map((x) => (
                  <tr key={x.seq}>
                    <td className="text-xs tabular-nums">{x.seq}</td>
                    <td className="text-xs text-muted">{fmtTime(x.ts)}</td>
                    <td className="text-xs">{x.actor}</td>
                    <td className="font-mono text-2xs">{x.action}</td>
                    <td className="text-xs">{x.target ?? "—"}</td>
                    <td className="max-w-72 truncate text-xs text-muted" title={x.detail ?? ""}>
                      {x.detail ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        </CardBody>
      </Card>
    </div>
  );
}

type GrConfigData = import("@/features/guardrails/api").GrConfig["data"];
