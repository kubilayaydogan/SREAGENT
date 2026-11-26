/** Agent Builder: özel agent'ları görsel workflow olarak oluştur, test et, yayımla; Soru-Cevap ve schedule'larda kullan.
 *  Liste (gömülü SRE Agent sistem agent'ı olarak) · Editör (palet / canvas / ayarlar) · Çalıştırmalar.
 *  Yetki backend'de doğrulanır; buradaki rol kontrolleri yalnız arayüzü sadeleştirir. */
import { MODEL_SOURCE_LABEL, sourceLabel } from "@/lib/runconfig";
import { Archive, CheckCircle2, Circle, Copy, Download, ExternalLink, FileUp, FlaskConical, Lock, Pencil, Plus, RotateCcw, Save, ShieldCheck, Upload } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import type { Viewport } from "@xyflow/react";
import { TableWrap } from "@/components/table";
import { Dialog, TabPanel, Tabs } from "@/components/ui/overlay";
import { Badge, Button, Card, CardBody, Input, Label, Select, Textarea } from "@/components/ui/primitives";
import { EmptyState, ErrorState, Notice, Page, PageHeader, TableSkeleton } from "@/components/ui/states";
import { Canvas, NODE_ICON } from "@/features/agentbuilder/Canvas";
import { ImportDialog } from "@/features/agentbuilder/ImportDialog";
import { AgentSettings, NodeSettings, type Meta } from "@/features/agentbuilder/Panels";
import { TestPanel } from "@/features/agentbuilder/TestPanel";
import { checklist, NODE_GUIDE, TEMPLATES, type Template } from "@/features/agentbuilder/guide";
import {
  downloadPackage,
  MODE_TR,
  NODE_TR,
  STATUS_TR,
  SYSTEM_AGENT_ID,
  useAbMutation,
  useAgent,
  useAgentRuns,
  useAgents,
  useCatalog,
  type AgentDetail,
  type AgentMode,
  type AgentSummary,
  type Catalog,
  type Definition,
  type Issue,
  type ResolvedModel,
  type WfNode,
} from "@/features/agentbuilder/api";
import { errorToast } from "@/hooks/queries";
import { toast } from "sonner";
import { api, ApiError } from "@/services/api";
import { useAuth } from "@/services/auth";
import { cn, fmtDuration, fmtNum, fmtTime } from "@/lib/utils";

export default function AgentBuilderPage() {
  const { id } = useParams();
  return id ? <Editor id={id} /> : <ListView />;
}

// ================================================================ liste
function StatusTag({ status }: { status: string }) {
  const tone = status === "published" || status === "completed" ? "text-st-good-ink" : status === "failed" || status === "rejected" ? "text-st-critical-ink" : status === "archived" || status === "cancelled" ? "text-muted" : "text-st-warning-ink";
  const mark = status === "published" || status === "completed" ? "●" : status === "failed" || status === "rejected" ? "✗" : status === "archived" ? "○" : "◐";
  return (
    <span className={cn("inline-flex items-center gap-1 text-xs whitespace-nowrap", tone)}>
      <span aria-hidden>{mark}</span>
      {STATUS_TR[status] ?? status}
    </span>
  );
}

function ListView() {
  const { can } = useAuth();
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") ?? "agents";
  const [archived, setArchived] = useState(false);
  const list = useAgents({ archived });
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);
  const [archiveOf, setArchiveOf] = useState<AgentSummary | null>(null);
  const nav = useNavigate();
  const canBuild = can("sre");
  const isAdmin = can("admin");
  const items = list.data?.items ?? [];

  const copy = useAbMutation((a: AgentSummary) => api.post<AgentSummary>(`/agents/${a.id}/copy`, {}), "Kopyalanamadı", { onSuccess: (r) => nav(`/agent-builder/${r.id}`) });
  const setDefault = useAbMutation((id: string) => api.put("/agents/default", { agent_id: id }), "Varsayılan agent değiştirilemedi");

  async function exportDraft(a: AgentSummary) {
    try {
      await downloadPackage(a.id, null, a.key);
    } catch (e) {
      exportError(e);
    }
  }

  return (
    <Page wide>
      <PageHeader
        title="Agent Builder"
        description="Özel agent'ları görsel workflow olarak oluşturun, taslağı test edin, yayımlayın; Soru-Cevap ve zamanlanmış işlerde kullanın."
        actions={
          canBuild && (
            <>
              <Button variant="outline" onClick={() => setImporting(true)}>
                <FileUp /> Agent İçe Aktar
              </Button>
              <Button variant="primary" onClick={() => setCreating(true)}>
                <Plus /> Yeni agent
              </Button>
            </>
          )
        }
      />
      <Tabs
        value={tab}
        onValueChange={(v) => setSp((p) => (p.set("tab", v), p), { replace: true })}
        tabs={[
          { value: "agents", label: "Agent'lar" },
          { value: "runs", label: "Çalıştırmalar" },
        ]}
      >
        <TabPanel value="agents">
          <Card>
            <div className="flex flex-wrap items-center gap-3 border-b border-line px-3 py-2">
              <label className="flex items-center gap-2 text-xs text-ink-2">
                Soru-Cevap varsayılanı:
                <Select
                  aria-label="Soru-Cevap varsayılan agent'ı"
                  disabled={!isAdmin || setDefault.isPending}
                  value={list.data?.default_chat_agent_id ?? SYSTEM_AGENT_ID}
                  onChange={(e) => setDefault.mutate(e.target.value)}
                  className="h-7 w-56 text-xs"
                  title={isAdmin ? undefined : "Yalnız admin değiştirebilir"}
                >
                  {items
                    .filter((a) => a.modes.includes("chat") && a.status !== "archived" && (a.kind === "system" || a.active_version))
                    .map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                </Select>
              </label>
              <label className="ml-auto flex items-center gap-1.5 text-xs text-ink-2">
                <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} /> Arşivlenenleri göster
              </label>
            </div>
            {list.isLoading ? (
              <TableSkeleton cols={8} />
            ) : list.error ? (
              <ErrorState error={list.error} onRetry={() => list.refetch()} />
            ) : (
              <TableWrap fetching={list.isFetching}>
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Ad</th>
                      <th>Durum</th>
                      <th>Yayımlanmış sürüm</th>
                      <th>Model</th>
                      <th>Modlar</th>
                      <th>Son çalıştırma</th>
                      <th className="text-right">İşlemler</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((a) => (
                      <tr key={a.id}>
                        <td className="max-w-80">
                          <Link to={`/agent-builder/${a.id}`} className="font-medium text-accent-text hover:underline">
                            {a.name}
                          </Link>
                          {a.kind === "system" && (
                            <Badge className="ml-1.5" title="Gömülü SRE Agent; workflow platform kodunda">
                              <Lock className="size-3" /> Sistem
                            </Badge>
                          )}
                          {a.is_default_chat && <Badge className="ml-1.5">varsayılan</Badge>}
                          {a.description && <div className="line-clamp-2 text-2xs text-muted">{a.description}</div>}
                        </td>
                        <td>
                          <StatusTag status={a.status} />
                        </td>
                        <td className="text-xs">
                          {a.published_version_label ?? <span className="text-muted">—</span>}
                          {a.has_unpublished_changes && a.status !== "archived" && <div className="text-2xs text-st-warning-ink">yayımlanmamış değişiklik</div>}
                        </td>
                        <td className="text-xs">
                          <span className="font-mono">{a.model?.model ?? "—"}</span>
                          {a.model?.profile && <div className="text-2xs text-muted">{a.model.profile}</div>}
                        </td>
                        <td className="text-xs">{a.modes.map((m) => MODE_TR[m]).join(", ")}</td>
                        <td className="text-xs">
                          {a.last_run ? (
                            <>
                              <StatusTag status={a.last_run.status} />
                              <div className="text-2xs text-muted">
                                {fmtTime(a.last_run.started_at)} · {MODE_TR[a.last_run.mode]}
                              </div>
                            </>
                          ) : (
                            <span className="text-muted">—</span>
                          )}
                        </td>
                        <td className="whitespace-nowrap text-right">
                          <Button size="sm" variant="ghost" aria-label={`${a.name} düzenle`} onClick={() => nav(`/agent-builder/${a.id}`)} title={a.kind === "system" ? "Görüntüle" : "Düzenle"}>
                            <Pencil />
                          </Button>
                          {canBuild && (
                            <>
                              <Button size="sm" variant="ghost" aria-label={`${a.name} kopyala`} disabled={!a.can_copy} title={a.copy_unavailable_reason ?? "Kopyala"} onClick={() => copy.mutate(a)}>
                                <Copy />
                              </Button>
                              <Button size="sm" variant="ghost" aria-label={`${a.name} test`} disabled={a.kind === "system"} title={a.kind === "system" ? "Gömülü agent Soru-Cevap ekranından çalışır" : "Taslağı test et"} onClick={() => nav(`/agent-builder/${a.id}?panel=test`)}>
                                <FlaskConical />
                              </Button>
                              <Button size="sm" variant="ghost" aria-label={`${a.name} dışa aktar`} disabled={!a.can_export} title={a.copy_unavailable_reason ?? "Taslağı dışa aktar (JSON)"} onClick={() => void exportDraft(a)}>
                                <Download />
                              </Button>
                            </>
                          )}
                          {isAdmin && a.kind === "custom" && a.status !== "archived" && (
                            <Button size="sm" variant="ghost" aria-label={`${a.name} arşivle`} title="Arşivle" onClick={() => setArchiveOf(a)}>
                              <Archive />
                            </Button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}
          </Card>
        </TabPanel>
        <TabPanel value="runs">
          <RunsView agents={items} />
        </TabPanel>
      </Tabs>
      {creating && <CreateDialog onClose={() => setCreating(false)} />}
      <ImportDialog open={importing} onOpenChange={setImporting} agents={items} />
      {archiveOf && <ArchiveDialog agent={archiveOf} onClose={() => setArchiveOf(null)} />}
    </Page>
  );
}

function exportError(e: unknown) {
  const err = e as ApiError;
  if (err?.code === "sensitive_content" && Array.isArray(err.details)) {
    const fields = (err.details as { field: string; node_id: string | null }[]).map((d) => (d.node_id ? `${d.node_id}.${d.field}` : d.field)).join(", ");
    errorToast(new ApiError(err.status, err.code, `${err.message} Alanlar: ${fields}`, err.correlationId), "Dışa aktarma engellendi");
  } else errorToast(e, "Dışa aktarılamadı");
}

/** Yeni agent'ın taslağına şablonu uygular (oluşturma sonrası tek PUT; şablon yalnız başlangıç noktasıdır). */
async function applyTemplate(id: string, t: Template, cat: Catalog) {
  const d = await api.get<AgentDetail>(`/agents/${id}`);
  const def = t.build(d.draft!, cat);
  await api.put(`/agents/${id}/draft`, { revision: d.draft_revision, name: d.name, description: d.description, modes: d.modes, definition: def });
}

function CreateDialog({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const cat = useCatalog(true);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [tpl, setTpl] = useState("basic");
  const [modes, setModes] = useState<AgentMode[]>(["chat"]);
  const t = TEMPLATES.find((x) => x.key === tpl)!;
  const create = useAbMutation(
    async () => {
      const r = await api.post<AgentSummary>("/agents", { name, description: desc || t.description, modes });
      if (cat.data && tpl !== "basic") {
        try {
          await applyTemplate(r.id, t, cat.data);
        } catch (e) {
          errorToast(e, "Şablon uygulanamadı; boş workflow ile açıldı");
        }
      } else if (cat.data) await applyTemplate(r.id, t, cat.data).catch(() => undefined);
      return r;
    },
    "Agent oluşturulamadı",
    { onSuccess: (r) => (onClose(), nav(`/agent-builder/${r.id}?panel=guide`)) },
  );
  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      title="Yeni agent"
      description="Bir şablonla başlayın; editörde her adımın ne yaptığı ve neye bağlanacağı yazar. Model ve diğer ayarlar sonra değiştirilebilir."
      wide
      footer={
        <Button variant="primary" loading={create.isPending} disabled={!name.trim() || !modes.length} onClick={() => create.mutate(undefined)}>
          Oluştur
        </Button>
      }
    >
      <Label htmlFor="new-name">Ad</Label>
      <Input id="new-name" maxLength={100} value={name} onChange={(e) => setName(e.target.value)} className="mb-3" />
      <Label htmlFor="new-desc">Açıklama</Label>
      <Textarea id="new-desc" rows={2} maxLength={2000} value={desc} onChange={(e) => setDesc(e.target.value)} className="mb-3 w-full" />
      <Label>Başlangıç şablonu</Label>
      <div className="mb-3 grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="şablon">
        {TEMPLATES.map((x) => (
          <button
            key={x.key}
            type="button"
            role="radio"
            aria-checked={tpl === x.key}
            onClick={() => {
              setTpl(x.key);
              if (x.key === "report") setModes(["scheduled"]);
              else if (tpl === "report") setModes(["chat"]);
            }}
            className={cn("rounded-md border p-2 text-left text-xs", tpl === x.key ? "border-accent ring-2 ring-accent/30" : "border-line hover:bg-surface-2")}
          >
            <div className="font-medium text-ink">{x.title}</div>
            <div className="mt-0.5 text-2xs text-muted">{x.description}</div>
            <div className="mt-1 font-mono text-2xs text-accent-text">{x.flow}</div>
          </button>
        ))}
      </div>
      <Label>Çalışma modları</Label>
      {(["chat", "scheduled"] as AgentMode[]).map((m) => (
        <label key={m} className="mr-4 inline-flex items-center gap-1.5 text-xs">
          <input type="checkbox" checked={modes.includes(m)} onChange={(e) => setModes(e.target.checked ? [...modes, m] : modes.filter((x) => x !== m))} />
          {MODE_TR[m]}
        </label>
      ))}
      <p className="mt-1 text-2xs text-muted">Soru-Cevap: kullanıcı soru sorunca çalışır. Zamanlanmış: Settings › Scheduling'deki schedule belirli aralıkla çalıştırır.</p>
    </Dialog>
  );
}

function ArchiveDialog({ agent, onClose }: { agent: AgentSummary; onClose: () => void }) {
  const deps = useAgent(agent.id);
  const arch = useAbMutation(() => api.post(`/agents/${agent.id}/archive`), "Arşivlenemedi", { onSuccess: onClose });
  const list = deps.data?.dependencies ?? [];
  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      title={`Arşivle: ${agent.name}`}
      description="Arşivlenen agent seçicilerde görünmez ve çalıştırılamaz; kayıtları ve sürümleri saklanır."
      footer={
        <Button variant="primary" loading={arch.isPending} disabled={deps.isLoading || list.length > 0} onClick={() => arch.mutate(undefined)}>
          Arşivle
        </Button>
      }
    >
      {deps.isLoading ? (
        <p className="text-xs text-muted">Bağımlılıklar kontrol ediliyor…</p>
      ) : list.length ? (
        <Notice tone="warning">
          Bu agent kullanılıyor; önce aşağıdaki seçimleri değiştirin:
          <ul className="mt-1 list-disc pl-4">
            {list.map((d, i) => (
              <li key={i}>
                {d.name}
                {d.fix && <span className="text-muted"> — {d.fix}</span>}
              </li>
            ))}
          </ul>
        </Notice>
      ) : (
        <p className="text-xs">Bağımlılık yok; arşivlenebilir.</p>
      )}
    </Dialog>
  );
}

// ================================================================ çalıştırmalar
function RunsView({ agents, agentId }: { agents: AgentSummary[]; agentId?: string }) {
  const [f, setF] = useState({ agent_id: agentId ?? "", mode: "", version: "", status: "" });
  const runs = useAgentRuns({ ...Object.fromEntries(Object.entries(f).filter(([, v]) => v)), limit: 100 });
  const retry = useAbMutation((id: string) => api.post(`/agents/runs/${encodeURIComponent(id)}/retry`), "Tekrar çalıştırılamadı");
  const { can } = useAuth();
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        {!agentId && (
          <Select aria-label="agent filtresi" value={f.agent_id} onChange={(e) => setF({ ...f, agent_id: e.target.value })} className="h-7 w-48 text-xs">
            <option value="">Tüm agent'lar</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </Select>
        )}
        <Select aria-label="mod filtresi" value={f.mode} onChange={(e) => setF({ ...f, mode: e.target.value })} className="h-7 w-36 text-xs">
          <option value="">Tüm modlar</option>
          <option value="chat">Soru-Cevap</option>
          <option value="scheduled">Zamanlanmış</option>
          <option value="test">Test</option>
        </Select>
        <Input aria-label="sürüm filtresi" placeholder="sürüm (v2, draft, builtin)" value={f.version} onChange={(e) => setF({ ...f, version: e.target.value.trim() })} className="h-7 w-44 text-xs" />
        <Select aria-label="durum filtresi" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })} className="h-7 w-36 text-xs">
          <option value="">Tüm durumlar</option>
          {["running", "completed", "failed", "rejected", "cancelled"].map((s) => (
            <option key={s} value={s}>
              {STATUS_TR[s]}
            </option>
          ))}
        </Select>
        {runs.data && <span className="ml-auto text-xs text-muted tabular-nums">{runs.data.total} çalıştırma</span>}
      </div>
      {runs.isLoading ? (
        <TableSkeleton cols={8} />
      ) : runs.error ? (
        <ErrorState error={runs.error} onRetry={() => runs.refetch()} />
      ) : !runs.data?.items.length ? (
        <EmptyState title="Çalıştırma yok" description="Soru-Cevap, schedule ve test çalıştırmaları burada listelenir." />
      ) : (
        <TableWrap fetching={runs.isFetching}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Run</th>
                <th>Agent / sürüm</th>
                <th>Mod</th>
                <th>Durum</th>
                <th>Model</th>
                <th className="num">Token</th>
                <th className="num">Süre</th>
                <th className="num">Başlangıç</th>
                <th>Bağlantılar</th>
              </tr>
            </thead>
            <tbody>
              {runs.data.items.map((r) => (
                <tr key={r.run_id}>
                  <td className="max-w-56">
                    <span className="block truncate font-mono text-2xs" title={r.run_id}>
                      {r.run_id}
                    </span>
                    {r.schedule_name && <span className="text-2xs text-muted">{r.schedule_name}</span>}
                  </td>
                  <td className="text-xs">
                    {r.agent_name ?? r.agent_key ?? "—"} <span className="font-mono text-muted">{r.version_label}</span>
                  </td>
                  <td className="text-xs">{MODE_TR[r.mode] ?? r.mode}</td>
                  <td>
                    <StatusTag status={r.status} />
                    {r.error && (
                      <div className="line-clamp-2 max-w-64 text-2xs text-st-critical-ink" title={r.error}>
                        {r.error_code && <code>{r.error_code}</code>} {r.error}
                      </div>
                    )}
                  </td>
                  <td className="text-xs">
                    {r.models.length ? (
                      [...new Map(r.models.filter((m) => m.model).map((m) => [m.model, m])).values()].map((m) => (
                        <div key={m.model} title={`Seçim kaynağı: ${sourceLabel({ source: m.source ?? "", profile: m.profile })}`}>
                          <span className="font-mono">{m.model}</span>
                          {m.source && <span className="text-2xs text-muted"> · {MODEL_SOURCE_LABEL[m.source] ?? m.source}</span>}
                        </div>
                      ))
                    ) : (
                      <span className="text-muted" title="Bu çalıştırmada dil modeli çağrılmadı (deterministik akış ya da LLM kapalı)">
                        LLM kullanılmadı
                      </span>
                    )}
                  </td>
                  <td className="num text-xs">{r.usage?.input_tokens != null ? `${fmtNum(r.usage.input_tokens)} / ${fmtNum(r.usage.output_tokens ?? 0)}` : "—"}</td>
                  <td className="num text-xs">{fmtDuration(r.duration_ms)}</td>
                  <td className="num text-xs text-muted">{fmtTime(r.started_at)}</td>
                  <td className="whitespace-nowrap text-xs">
                    {r.investigation_id && (
                      <>
                        <Link className="text-accent-text hover:underline" to={`/investigations/${r.investigation_id}`}>
                          Sonuç
                        </Link>
                        {" · "}
                      </>
                    )}
                    <Link className="text-accent-text hover:underline" to={r.links.agent_trace}>
                      Trace
                    </Link>
                    {" · "}
                    <Link className="text-accent-text hover:underline" to={r.links.context_windows}>
                      Context
                    </Link>
                    {" · "}
                    <Link className="text-accent-text hover:underline" to={r.links.middleware}>
                      Middleware
                    </Link>
                    {r.mode === "scheduled" && (r.status === "failed" || r.status === "cancelled") && can("sre") && (
                      <Button size="sm" variant="ghost" className="ml-1" aria-label="aynı sürümle tekrar çalıştır" title="Aynı sürüm ve girdiyle tekrar çalıştır" onClick={() => retry.mutate(r.run_id)}>
                        <RotateCcw />
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}
    </Card>
  );
}

// ================================================================ editör
const DEFAULT_CONFIG: Record<string, (id: string) => Record<string, unknown>> = {
  llm: (id) => ({ prompt: "{{input}}", output_var: id, stream: true }),
  structured_output: (id) => ({ prompt: "{{input}}", output_var: id, schema: { type: "object", properties: { summary: { type: "string" } }, required: ["summary"], additionalProperties: false } }),
  mcp_tool: (id) => ({ tool: "execute-dql", args: { dqlQueryString: "" }, output_var: id }),
  memory_retrieval: (id) => ({ query: "{{input}}", output_var: id }),
  condition: () => ({ field: "", operator: "eq", value: "" }),
  result: () => ({ template: "" }),
};

function resolveLocal(def: Definition, n: WfNode, cat: Catalog | undefined): ResolvedModel {
  const pid = (n.config.llm_profile_id as string) || def.settings.model.llm_profile_id;
  const p = cat?.llm_profiles.find((x) => x.id === pid);
  return { profile_id: pid ?? null, profile: p?.name ?? null, provider: p?.provider ?? null, model: (n.config.model as string) || p?.model || null, source: n.config.llm_profile_id ? "node" : "agent" };
}

function Editor({ id }: { id: string }) {
  const q = useAgent(id);
  const cat = useCatalog(true);
  if (q.isLoading || cat.isLoading)
    return (
      <Page>
        <TableSkeleton rows={4} cols={3} />
      </Page>
    );
  if (q.error || cat.error)
    return (
      <Page>
        <ErrorState error={q.error ?? cat.error} onRetry={() => (q.refetch(), cat.refetch())} />
      </Page>
    );
  if (!q.data) return null;
  if (q.data.kind === "system") return <SystemAgentView a={q.data} />;
  return <DraftEditor key={`${q.data.id}`} a={q.data} cat={cat.data!} />;
}

function SystemAgentView({ a }: { a: AgentDetail }) {
  const agents = useAgents();
  return (
    <Page>
      <PageHeader title={a.name} crumbs={[{ label: "Agent Builder", to: "/agent-builder" }, { label: a.name }]} description={a.description ?? undefined} />
      <Notice tone="info">
        Gömülü SRE Agent sistem agent'ıdır: LangGraph workflow'u platform kodunda tanımlıdır, canvas'ta düzenlenemez, kopyalanamaz ve dışa aktarılamaz. Modeli
        Settings › LLM'deki platform modelidir. Soru-Cevap'ta varsayılan olarak, health-check schedule'ında zamanlanmış olarak çalışır.
      </Notice>
      <div className="mt-3 grid gap-3 md:grid-cols-3">
        <Card>
          <CardBody>
            <div className="text-xs text-muted">Modlar</div>
            <div className="text-[13px]">{a.modes.map((m) => MODE_TR[m]).join(", ")}</div>
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <div className="text-xs text-muted">Sürüm</div>
            <div className="text-[13px]">builtin</div>
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <div className="text-xs text-muted">Son çalıştırma</div>
            <div className="text-[13px]">{a.last_run ? `${STATUS_TR[a.last_run.status] ?? a.last_run.status} · ${fmtTime(a.last_run.started_at)}` : "—"}</div>
          </CardBody>
        </Card>
      </div>
      <div className="mt-3">
        <RunsView agents={agents.data?.items ?? []} agentId={a.id} />
      </div>
    </Page>
  );
}

type RightTab = "guide" | "node" | "agent" | "test" | "versions" | "issues";

function DraftEditor({ a, cat }: { a: AgentDetail; cat: Catalog }) {
  const { can } = useAuth();
  const [sp] = useSearchParams();
  const canBuild = can("sre");
  const isAdmin = can("admin");
  const archived = a.status === "archived";
  const [def, setDef] = useState<Definition>(a.draft!);
  const [meta, setMeta] = useState<Meta>({ name: a.name, description: a.description ?? "", modes: a.modes });
  const [rev, setRev] = useState(a.draft_revision);
  const [dirty, setDirty] = useState(false);
  const [issues, setIssues] = useState<Issue[]>(a.issues);
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<RightTab>(sp.get("panel") === "test" ? "test" : sp.get("panel") === "guide" || !a.versions.length ? "guide" : "agent");
  const [tested, setTested] = useState(a.last_run?.mode === "test" && a.last_run.status === "completed");
  const [runState, setRunState] = useState<Record<string, { status: string; duration_ms?: number | null }>>({});
  const [view, setView] = useState<{ version: number; def: Definition } | null>(null);
  const [publishOpen, setPublishOpen] = useState(false);
  const readOnly = !canBuild || archived || !!view;
  const shown = view?.def ?? def;

  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);

  const edit = (d: Definition) => (setDef(d), setDirty(true));
  const models = useMemo(() => {
    const m: Record<string, ResolvedModel> = {};
    for (const n of shown.workflow.nodes) if (n.type === "llm" || n.type === "structured_output") m[n.id] = resolveLocal(shown, n, cat);
    return m;
  }, [shown, cat]);
  const sel = shown.workflow.nodes.find((n) => n.id === selected) ?? null;
  const errors = issues.filter((i) => i.severity === "error");
  const warnings = issues.filter((i) => i.severity === "warning");

  const save = useAbMutation(
    () => api.put<{ draft_revision: number; issues: Issue[] }>(`/agents/${a.id}/draft`, { revision: rev, name: meta.name, description: meta.description || null, modes: meta.modes, definition: def }),
    "Kaydedilemedi",
    {
      silent: true,
      onSuccess: (r) => {
        setRev(r.draft_revision);
        setIssues(r.issues);
        setDirty(false);
      },
    },
  );
  useEffect(() => {
    const e = save.error as ApiError | null;
    if (!e) return;
    if (e.code === "agent_invalid" && Array.isArray(e.details)) {
      setIssues(e.details as Issue[]);
      setTab("issues");
    }
    errorToast(e, "Kaydedilemedi");
  }, [save.error]);

  const validate = useAbMutation(() => api.post<{ issues: Issue[] }>(`/agents/${a.id}/validate?stage=publish`), "Doğrulanamadı", {
    onSuccess: (r) => (setIssues(r.issues), setTab("issues")),
  });

  /** Seçili node'dan sonra yeni adım: sonraki adım varsa araya girer (A → yeni → B), yoksa sona eklenir. */
  function addAfter(src: string, type: string, branch: "true" | "false" | null) {
    if (readOnly) return;
    const s0 = def.workflow.nodes.find((n) => n.id === src);
    if (!s0) return;
    const used = new Set(def.workflow.nodes.map((n) => n.id));
    let i = 1;
    while (used.has(`${type}_${i}`)) i++;
    const nid = `${type}_${i}`;
    const old = def.workflow.edges.find((e) => e.source === src && (e.branch ?? null) === branch);
    const dy = branch === "true" ? -90 : branch === "false" ? 110 : 0;
    const node: WfNode = { id: nid, type, label: NODE_TR[type] ?? type, config: DEFAULT_CONFIG[type]?.(nid) ?? {}, position: { x: s0.position.x + 280, y: s0.position.y + dy } };
    // araya girerken sağdaki node'ları kaydır (üst üste binmesin)
    const shifted = old ? def.workflow.nodes.map((n) => (n.position.x > s0.position.x ? { ...n, position: { ...n.position, x: n.position.x + 280 } } : n)) : def.workflow.nodes;
    const edges = def.workflow.edges.filter((e) => e !== old);
    edges.push({ id: `e_${src}_${branch ? `${branch}_` : ""}${nid}`, source: src, target: nid, branch });
    // araya girerken eski hedef yeni adımdan sonra gelir (Koşul'da yeşil yol); Sonuç'tan sonra adım olamaz
    if (old && type !== "result") {
      const br = type === "condition" ? "true" : null;
      edges.push({ id: `e_${nid}_${br ? `${br}_` : ""}${old.target}`, source: nid, target: old.target, branch: br });
    }
    edit({ ...def, workflow: { nodes: [...shifted, node], edges } });
    setSelected(nid);
    setTab("node");
    toast.success(`${NODE_TR[type]} eklendi ve bağlandı`, { description: NODE_GUIDE[type]?.what });
  }

  function addNode(type: string, pos?: { x: number; y: number }) {
    if (readOnly) return;
    const used = new Set(def.workflow.nodes.map((n) => n.id));
    let i = 1;
    while (used.has(`${type}_${i}`)) i++;
    const nid = `${type}_${i}`;
    const maxX = Math.max(0, ...def.workflow.nodes.map((n) => n.position.x));
    const node: WfNode = { id: nid, type, label: NODE_TR[type] ?? type, config: DEFAULT_CONFIG[type]?.(nid) ?? {}, position: pos ?? { x: maxX + 260, y: 260 } };
    edit({ ...def, workflow: { ...def.workflow, nodes: [...def.workflow.nodes, node] } });
    setSelected(nid);
    setTab("node");
  }

  async function openVersion(n: number) {
    try {
      const v = await api.get<{ definition: Definition & { name?: string } }>(`/agents/${a.id}/versions/${n}`);
      const { name: _n, description: _d, modes: _m, ...rest } = v.definition as Definition & { name?: string; description?: string; modes?: string[] };
      void _n, void _d, void _m;
      setView({ version: n, def: rest as Definition });
      setSelected(null);
    } catch (e) {
      errorToast(e, "Sürüm açılamadı");
    }
  }

  const activate = useAbMutation((n: number) => api.post(`/agents/${a.id}/versions/${n}/activate`), "Sürüm aktif edilemedi");

  const steps = checklist(shown, { dirty, versions: a.versions.length, tested, errors: errors.length, modes: meta.modes });
  const doneCount = steps.filter((x) => x.done).length;
  const rightTabs = [
    { value: "guide", label: `Rehber ${doneCount}/${steps.length}` },
    ...(sel ? [{ value: "node", label: "Node" }] : []),
    { value: "agent", label: "Agent" },
    { value: "test", label: "Test" },
    { value: "versions", label: `Sürümler (${a.versions.length})` },
    { value: "issues", label: errors.length ? `Sorunlar (${errors.length})` : warnings.length ? `Uyarılar (${warnings.length})` : "Sorunlar" },
  ];

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2">
        <Link to="/agent-builder" className="text-xs text-muted hover:text-ink">
          Agent Builder ›
        </Link>
        <h1 className="text-[13px] font-semibold text-ink">{meta.name}</h1>
        <StatusTag status={a.status} />
        <span className="text-xs text-muted">
          {a.published_version_label ? `aktif ${a.published_version_label}` : "yayımlanmadı"} · taslak r{rev}
          {dirty && <span className="ml-1 text-st-warning-ink">· kaydedilmemiş değişiklik</span>}
        </span>
        {view && (
          <span className="rounded-sm bg-surface-3 px-2 py-0.5 text-xs">
            v{view.version} görüntüleniyor (salt okunur){" "}
            <button className="text-accent-text hover:underline" onClick={() => setView(null)}>
              taslağa dön
            </button>
          </span>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          {canBuild && !archived && !view && (
            <>
              <Button size="sm" variant="outline" loading={validate.isPending} onClick={() => validate.mutate(undefined)} disabled={dirty} title={dirty ? "Önce kaydedin" : "Yayın doğrulaması"}>
                <ShieldCheck /> Doğrula
              </Button>
              <Button size="sm" variant="outline" loading={save.isPending} disabled={!dirty} onClick={() => save.mutate(undefined)}>
                <Save /> Kaydet
              </Button>
            </>
          )}
          {canBuild && (
            <Button
              size="sm"
              variant="ghost"
              title={view ? `v${view.version} paketini indir` : "Taslağı JSON olarak indir"}
              onClick={() => void downloadPackage(a.id, view?.version ?? null, a.key).catch(exportError)}
            >
              <Download /> Dışa aktar
            </Button>
          )}
          {isAdmin && !archived && !view && (
            <Button size="sm" variant="primary" disabled={dirty} title={dirty ? "Önce kaydedin" : "Yeni değişmez sürüm oluştur"} onClick={() => setPublishOpen(true)}>
              <Upload /> Yayımla
            </Button>
          )}
        </div>
      </div>
      {archived && <Notice tone="warning" className="m-2">Arşivlenmiş agent düzenlenemez ve çalıştırılamaz.</Notice>}
      <div className="flex min-h-0 flex-1">
        <aside className="w-48 shrink-0 overflow-y-auto border-r border-line p-2" aria-label="node paleti">
          <div className="mb-1 text-2xs font-semibold uppercase tracking-wide text-muted">Node'lar</div>
          {cat.nodes
            .filter((n) => n.type !== "start")
            .map((n) => {
              const Icon = NODE_ICON[n.type];
              return (
                <button
                  key={n.type}
                  type="button"
                  draggable={!readOnly}
                  disabled={readOnly}
                  onDragStart={(e) => (e.dataTransfer.setData("application/x-agent-node", n.type), (e.dataTransfer.effectAllowed = "move"))}
                  onClick={() => addNode(n.type)}
                  title={n.description}
                  className="mb-1 flex w-full items-center gap-2 rounded-md border border-line px-2 py-1.5 text-left text-xs text-ink hover:bg-surface-2 disabled:opacity-50"
                >
                  {Icon && <Icon className="size-3.5 text-accent-text" />}
                  {n.label}
                </button>
              );
            })}
          <div className="mt-3 mb-1 text-2xs font-semibold uppercase tracking-wide text-muted">Kullanılamıyor</div>
          {cat.unavailable_nodes.map((n) => (
            <div key={n.type} className="mb-1 rounded-md border border-dashed border-line px-2 py-1.5 text-xs text-muted" title={n.reason}>
              {n.label}
              <div className="text-2xs">{n.reason}</div>
            </div>
          ))}
          <p className="mt-3 text-2xs text-muted">Sürükleyip bırakın ya da tıklayın. Bağlantı: çıkış noktasından giriş noktasına sürükleyin; silmek için seçip Delete.</p>
        </aside>
        <div className="min-w-0 flex-1">
          <Canvas
            key={view ? `v${view.version}` : "draft"}
            nodes={shown.workflow.nodes}
            edges={shown.workflow.edges}
            viewport={shown.layout.viewport}
            issues={view ? [] : issues}
            models={models}
            runState={view ? undefined : runState}
            selected={selected}
            readOnly={readOnly}
            onChange={(nodes, edges) => edit({ ...def, workflow: { nodes, edges } })}
            onViewport={(v: Viewport) => !view && !readOnly && setDef((d) => ({ ...d, layout: { viewport: v } }))}
            onSelect={(nid) => {
              setSelected(nid);
              if (nid) setTab("node");
              else if (tab === "node") setTab("agent");
            }}
            onDropNode={addNode}
          />
        </div>
        <aside className="w-[26rem] shrink-0 overflow-y-auto border-l border-line p-3" aria-label="ayarlar">
          <Tabs value={tab === "node" && !sel ? "agent" : tab} onValueChange={(v) => setTab(v as RightTab)} tabs={rightTabs}>
            {sel && (
              <TabPanel value="node">
                <NodeSettings
                  node={sel}
                  def={shown}
                  cat={cat}
                  issues={issues.filter((i) => i.node_id === sel.id)}
                  resolved={models[sel.id] ?? null}
                  readOnly={readOnly}
                  onAddAfter={(t, b) => addAfter(sel.id, t, b)}
                  onChange={(n) => edit({ ...def, workflow: { ...def.workflow, nodes: def.workflow.nodes.map((x) => (x.id === n.id ? n : x)) } })}
                  onDelete={() => {
                    edit({ ...def, workflow: { nodes: def.workflow.nodes.filter((x) => x.id !== sel.id), edges: def.workflow.edges.filter((e) => e.source !== sel.id && e.target !== sel.id) } });
                    setSelected(null);
                    setTab("agent");
                  }}
                />
              </TabPanel>
            )}
            <TabPanel value="guide">
              <GuidePanel
                steps={steps}
                readOnly={readOnly}
                onAction={(st) => {
                  if (!st.action) return;
                  if (st.action.kind === "node") {
                    setSelected(st.action.id);
                    setTab("node");
                  } else setTab(st.action.tab as RightTab);
                }}
                onTemplate={(t) => {
                  edit(t.build({ ...def }, cat));
                  setSelected(null);
                  toast.success(`Şablon uygulandı: ${t.title}`, { description: "Kaydetmeden önce adımları inceleyin." });
                }}
              />
            </TabPanel>
            <TabPanel value="agent">
              <AgentSettings def={shown} meta={meta} cat={cat} issues={issues} readOnly={readOnly} onDef={edit} onMeta={(m) => (setMeta(m), setDirty(true))} />
            </TabPanel>
            <TabPanel value="test">
              <TestPanel
                agentId={a.id}
                canTest={canBuild && !archived}
                blocked={dirty ? "Test kayıtlı taslağı çalıştırır; önce kaydedin." : null}
                onNodes={(m) => {
                  setRunState(m);
                  if (Object.keys(m).length && Object.values(m).every((x) => x.status === "ok")) setTested(true);
                }}
                onIssues={(i) => i.length && (setIssues(i), setTab("issues"))}
              />
            </TabPanel>
            <TabPanel value="versions">
              {!a.versions.length ? (
                <p className="text-xs text-muted">Henüz yayımlanmış sürüm yok. Normal çalıştırmalar yalnız yayımlanmış sürümü kullanır.</p>
              ) : (
                <ul className="space-y-1.5">
                  {a.versions.map((v) => (
                    <li key={v.version} className={cn("rounded-md border p-2 text-xs", v.active ? "border-accent" : "border-line")}>
                      <div className="flex items-center gap-2">
                        <b>v{v.version}</b>
                        {v.active && <Badge>aktif</Badge>}
                        <span className="text-muted">{fmtTime(v.published_at)}</span>
                        <span className="ml-auto font-mono text-2xs text-muted">{v.checksum}</span>
                      </div>
                      <div className="text-2xs text-muted">
                        {v.published_by} · taslak r{v.draft_revision}
                        {v.note && ` · ${v.note}`}
                      </div>
                      <div className="mt-1 flex gap-1">
                        <Button size="sm" variant="ghost" onClick={() => void openVersion(v.version)}>
                          İncele
                        </Button>
                        {isAdmin && !v.active && !archived && (
                          <Button size="sm" variant="outline" loading={activate.isPending} onClick={() => activate.mutate(v.version)}>
                            Aktif et
                          </Button>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </TabPanel>
            <TabPanel value="issues">
              {!issues.length ? (
                <p className="text-xs text-st-good-ink">Doğrulama sorunu yok.</p>
              ) : (
                <ul className="space-y-1">
                  {[...errors, ...warnings].map((i, k) => (
                    <li key={k} className={cn("text-xs", i.severity === "error" ? "text-st-critical-ink" : "text-st-warning-ink")}>
                      {i.node_id ? (
                        <button className="mr-1 font-mono underline" onClick={() => (setSelected(i.node_id), setTab("node"))}>
                          {i.node_id}
                        </button>
                      ) : (
                        i.field && <code className="mr-1">{i.field}</code>
                      )}
                      {i.message}
                    </li>
                  ))}
                </ul>
              )}
            </TabPanel>
          </Tabs>
          <div className="mt-4 border-t border-line pt-2 text-2xs text-muted">
            <Link to={`/agent-builder?tab=runs`} className="inline-flex items-center gap-1 text-accent-text hover:underline">
              Çalıştırmalar <ExternalLink className="size-3" />
            </Link>
          </div>
        </aside>
      </div>
      {publishOpen && (
        <PublishDialog
          agentId={a.id}
          revision={rev}
          onClose={() => setPublishOpen(false)}
          onIssues={(i) => (setIssues(i), setTab("issues"))}
        />
      )}
    </div>
  );
}

function GuidePanel({ steps, readOnly, onAction, onTemplate }: {
  steps: ReturnType<typeof checklist>;
  readOnly: boolean;
  onAction: (s: ReturnType<typeof checklist>[number]) => void;
  onTemplate: (t: Template) => void;
}) {
  const [confirm, setConfirm] = useState<Template | null>(null);
  const done = steps.filter((x) => x.done).length;
  const next = steps.find((x) => !x.done);
  return (
    <div className="space-y-4 text-xs">
      <section aria-label="kurulum adımları">
        <div className="mb-1 flex items-center justify-between">
          <b className="text-ink">Kurulum adımları</b>
          <span className="tabular-nums text-muted">
            {done}/{steps.length}
          </span>
        </div>
        <div className="mb-2 h-1.5 rounded-full bg-surface-3" aria-hidden>
          <div className="h-1.5 rounded-full bg-st-good" style={{ width: `${(done / steps.length) * 100}%` }} />
        </div>
        {next && (
          <p className="mb-2 rounded-sm bg-accent/5 px-2 py-1 text-ink">
            <b>Sıradaki:</b> {next.label}. <span className="text-ink-2">{next.hint}</span>
          </p>
        )}
        <ol className="space-y-1">
          {steps.map((st) => (
            <li key={st.key} className="flex items-start gap-1.5">
              {st.done ? <CheckCircle2 className="mt-px size-3.5 shrink-0 text-st-good-ink" aria-label="tamam" /> : <Circle className="mt-px size-3.5 shrink-0 text-muted" aria-label="bekliyor" />}
              <div className="min-w-0">
                {st.action && !st.done ? (
                  <button className="text-left text-accent-text hover:underline" onClick={() => onAction(st)}>
                    {st.label}
                  </button>
                ) : (
                  <span className={st.done ? "text-muted line-through" : "text-ink"}>{st.label}</span>
                )}
                {!st.done && <div className="text-2xs text-muted">{st.hint}</div>}
              </div>
            </li>
          ))}
        </ol>
      </section>
      <section aria-label="agent nasıl çalışır">
        <b className="text-ink">Agent nasıl çalışır?</b>
        <p className="mt-1 text-ink-2">
          Agent, soldan sağa akan adımlardan oluşur. Her adım bir iş yapar ve sonucunu bir <b>değişkene</b> yazar; sonraki adımlar bu sonucu <code>{"{{değişken}}"}</code> ile kullanır.
        </p>
        <ol className="mt-1.5 space-y-1 text-ink-2">
          <li>
            <b>1. Başlangıç</b>: kullanıcının sorusu <code>{"{{input}}"}</code> olarak gelir.
          </li>
          <li>
            <b>2. Veri topla</b> (isteğe bağlı): <i>MCP Tool</i> Dynatrace'ten veri çeker, <i>Memory Retrieval</i> geçmiş kayıtları getirir.
          </li>
          <li>
            <b>3. Karar ver</b> (isteğe bağlı): <i>Koşul</i> akışı ikiye ayırır (ör. veri yoksa farklı yanıt).
          </li>
          <li>
            <b>4. Düşün</b>: <i>LLM</i> toplanan bilgiyle yanıtı yazar; <i>Structured Output</i> JSON üretir.
          </li>
          <li>
            <b>5. Sonuç</b>: kullanıcıya dönen metin, çoğu zaman <code>{"{{answer}}"}</code>.
          </li>
        </ol>
        <p className="mt-1.5 rounded-sm bg-surface-2 px-2 py-1 font-mono text-2xs text-ink-2">Başlangıç → MCP Tool → Koşul → LLM → Sonuç</p>
        <p className="mt-1.5 text-2xs text-muted">
          Bağlamak için: bir node'un sağındaki noktadan sürükleyip sonraki node'un solundaki noktaya bırakın. Ya da node'u seçip &quot;Sonraki adımı ekle ve bağla&quot; düğmelerini kullanın.
        </p>
      </section>
      {!readOnly && (
        <section aria-label="şablonlar">
          <b className="text-ink">Şablondan başla</b>
          <p className="mt-0.5 text-2xs text-muted">Workflow'u hazır bir örnekle değiştirir; sonra istediğiniz gibi düzenleyin.</p>
          <div className="mt-1.5 space-y-1.5">
            {TEMPLATES.map((t) => (
              <button key={t.key} type="button" onClick={() => setConfirm(t)} className="w-full rounded-md border border-line p-2 text-left hover:bg-surface-2">
                <div className="font-medium text-ink">{t.title}</div>
                <div className="text-2xs text-muted">{t.description}</div>
                <div className="mt-0.5 font-mono text-2xs text-accent-text">{t.flow}</div>
              </button>
            ))}
          </div>
        </section>
      )}
      {confirm && (
        <Dialog
          open
          onOpenChange={(v) => !v && setConfirm(null)}
          title={`Şablon: ${confirm.title}`}
          description="Mevcut workflow (node'lar ve bağlantılar) şablonla değiştirilir. Kaydetmeden önce vazgeçerseniz sayfayı yenilemeniz yeterli."
          footer={
            <Button variant="primary" onClick={() => (onTemplate(confirm), setConfirm(null))}>
              Uygula
            </Button>
          }
        >
          <p className="font-mono text-xs">{confirm.flow}</p>
        </Dialog>
      )}
    </div>
  );
}

function PublishDialog({ agentId, revision, onClose, onIssues }: { agentId: string; revision: number; onClose: () => void; onIssues: (i: Issue[]) => void }) {
  const [note, setNote] = useState("");
  const pub = useAbMutation(() => api.post<{ version: number }>(`/agents/${agentId}/publish`, { revision, note: note || null }), "Yayımlanamadı", {
    silent: true,
    onSuccess: onClose,
  });
  const err = pub.error as ApiError | null;
  useEffect(() => {
    if (err?.code === "agent_invalid" && Array.isArray(err.details)) onIssues(err.details as Issue[]);
  }, [err]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      title="Yayımla"
      description="Taslak tam doğrulamadan geçerse değişmez yeni sürüm oluşur ve sonraki çalıştırmalarda kullanılır; başlamış çalıştırmalar kendi sürümüyle tamamlanır."
      footer={
        <Button variant="primary" loading={pub.isPending} onClick={() => pub.mutate(undefined)}>
          Yayımla
        </Button>
      }
    >
      <Label htmlFor="pub-note">Sürüm notu (isteğe bağlı)</Label>
      <Input id="pub-note" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
      {err && <Notice tone="critical" className="mt-3">{err.message}{err.code === "agent_invalid" && " — sorunlar node'larda ve Sorunlar sekmesinde gösterildi."}</Notice>}
    </Dialog>
  );
}

