/** Agent içe aktarma: dosya → backend önizlemesi (hiçbir şey çalıştırılmaz) → bağımlılık eşleştirme → kayıt.
 *  Varsayılan hedef yeni agent'tır; mevcut özel agent'a aktarım yeni taslak revizyonu oluşturur. Kayıt taslaktır. */
import { AlertTriangle, FileUp, XCircle } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";
import { Dialog } from "@/components/ui/overlay";
import { Button, Input, Label, Select } from "@/components/ui/primitives";
import { Notice } from "@/components/ui/states";
import { api, ApiError } from "@/services/api";
import { cn } from "@/lib/utils";
import { MODE_TR, useAbMutation, type AgentSummary, type ImportPreview, type Issue } from "./api";

const MAX_BYTES = 512 * 1024;

function readText(f: File): Promise<string> {
  if (typeof f.text === "function") return f.text();
  return new Promise((ok, fail) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result ?? ""));
    r.onerror = () => fail(r.error);
    r.readAsText(f);
  });
}

function IssueList({ items }: { items: Issue[] }) {
  if (!items.length) return null;
  return (
    <ul className="space-y-0.5">
      {items.map((i, k) => (
        <li key={k} className={cn("flex items-start gap-1 text-xs", i.severity === "error" ? "text-st-critical-ink" : "text-st-warning-ink")}>
          {i.severity === "error" ? <XCircle className="mt-0.5 size-3.5 shrink-0" /> : <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />}
          <span>
            {i.node_id && <code className="mr-1">{i.node_id}</code>}
            {i.field && <code className="mr-1 text-muted">{i.field}</code>}
            {i.message}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function ImportDialog({ open, onOpenChange, agents }: { open: boolean; onOpenChange: (v: boolean) => void; agents: AgentSummary[] }) {
  const nav = useNavigate();
  const [pkg, setPkg] = useState<Record<string, unknown> | null>(null);
  const [fileErr, setFileErr] = useState<string | null>(null);
  const [pv, setPv] = useState<ImportPreview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const [target, setTarget] = useState<"new" | "existing">("new");
  const [agentId, setAgentId] = useState("");
  const [name, setName] = useState("");
  const [serverIssues, setServerIssues] = useState<Issue[]>([]);
  const [loading, setLoading] = useState(false);
  const custom = agents.filter((a) => a.kind === "custom" && a.status !== "archived");

  function reset() {
    setPkg(null);
    setPv(null);
    setFileErr(null);
    setMapping({});
    setTarget("new");
    setAgentId("");
    setName("");
    setServerIssues([]);
  }

  async function preview(p: Record<string, unknown>, m: Record<string, string | null>) {
    setLoading(true);
    try {
      const r = await api.post<ImportPreview>("/agents/import/preview", { package: p, mapping: m });
      setPv(r);
      setMapping(r.mapping);
      setFileErr(null);
      setName((n) => n || (r.name_conflict ? `${r.agent.name} (içe aktarılan)` : r.agent.name));
    } catch (e) {
      setPv(null);
      const err = e as ApiError;
      setFileErr(err.message);
      setServerIssues(Array.isArray(err.details) ? (err.details as Issue[]) : []);
    } finally {
      setLoading(false);
    }
  }

  async function onFile(f: File | undefined) {
    reset();
    if (!f) return;
    if (f.size > MAX_BYTES) return setFileErr(`Dosya çok büyük (${Math.round(f.size / 1024)} KB > 512 KB)`);
    try {
      const p = JSON.parse(await readText(f));
      if (typeof p !== "object" || p === null || Array.isArray(p)) throw new Error("JSON nesnesi değil");
      setPkg(p);
      await preview(p, {});
    } catch (e) {
      setFileErr(`Dosya okunamadı: ${(e as Error).message}`);
    }
  }

  const imp = useAbMutation(
    () => api.post<{ agent: AgentSummary; issues: Issue[] }>("/agents/import", { package: pkg, mapping, target, agent_id: target === "existing" ? agentId : null, name: target === "new" ? name : null }),
    "İçe aktarılamadı",
    {
      silent: true,
      onSuccess: (r) => {
        onOpenChange(false);
        reset();
        nav(`/agent-builder/${r.agent.id}`);
      },
    },
  );

  const mappingErrors = [...(pv?.structural_issues ?? []), ...(pv?.mapping_issues ?? [])];
  const blocking = mappingErrors.some((i) => i.severity === "error");
  const err = imp.error as ApiError | null;
  const errIssues = err && Array.isArray(err.details) ? (err.details as Issue[]) : [];

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => (onOpenChange(v), v || reset())}
      title="Agent içe aktar"
      description="JSON paketi doğrulanır (workflow ya da tool çalıştırılmaz); bağımlılıklar bu ortamdaki kayıtlarla eşleştirilir. Sonuç taslaktır."
      wide
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Vazgeç
          </Button>
          <Button
            variant="primary"
            loading={imp.isPending}
            disabled={!pv || blocking || (target === "existing" ? !agentId : !name.trim())}
            onClick={() => imp.mutate(undefined)}
          >
            {target === "existing" ? "Yeni taslak revizyonu olarak aktar" : "Yeni agent olarak aktar"}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div>
          <Label htmlFor="imp-file">Paket dosyası (.json, en fazla 512 KB)</Label>
          <label className="flex cursor-pointer items-center gap-2 rounded-md border border-dashed border-line-strong px-3 py-2 text-xs text-ink-2 hover:bg-surface-2">
            <FileUp className="size-4" />
            <span>Dosya seçin</span>
            <input id="imp-file" type="file" accept="application/json,.json" className="sr-only" onChange={(e) => void onFile(e.target.files?.[0])} />
          </label>
        </div>
        {loading && <p className="text-xs text-muted">Doğrulanıyor…</p>}
        {fileErr && (
          <Notice tone="critical">
            {fileErr}
            <IssueList items={serverIssues} />
          </Notice>
        )}
        {pv && (
          <>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 rounded-md border border-line p-2 text-xs sm:grid-cols-4">
              <div>
                <dt className="text-muted">Ad</dt>
                <dd className="font-medium">{pv.agent.name}</dd>
              </div>
              <div>
                <dt className="text-muted">Sürüm</dt>
                <dd>{pv.agent_version ? `v${pv.agent_version}` : "taslak"}</dd>
              </div>
              <div>
                <dt className="text-muted">Modlar</dt>
                <dd>{pv.agent.modes.map((m) => MODE_TR[m]).join(", ")}</dd>
              </div>
              <div>
                <dt className="text-muted">Node / bağlantı</dt>
                <dd className="tabular-nums">
                  {pv.node_count} / {pv.edge_count}
                </dd>
              </div>
            </dl>
            <div>
              <Label>Bağımlılık eşleştirme</Label>
              {!pv.dependencies.llm_profiles.length && !pv.dependencies.connectors.length && <p className="text-xs text-muted">Eşleştirilecek bağımlılık yok.</p>}
              <div className="space-y-2">
                {pv.dependencies.llm_profiles.map((l) => (
                  <div key={l.ref} className="grid grid-cols-[1fr_1fr] items-center gap-2 text-xs">
                    <div>
                      <span className="font-mono">{l.ref}</span> · LLM {l.name ?? "?"} <span className="font-mono text-muted">({l.provider} · {l.model ?? "?"})</span>
                      <div className="text-2xs text-muted">kullanan: {l.used_by.join(", ")}</div>
                    </div>
                    <Select
                      aria-label={`${l.ref} eşleştirmesi`}
                      value={mapping[l.ref] ?? ""}
                      onChange={(e) => {
                        const m = { ...mapping, [l.ref]: e.target.value || null };
                        setMapping(m);
                        void preview(pkg!, m);
                      }}
                    >
                      <option value="">eşleştirme yok (taslak, sonra seçilir)</option>
                      {pv.candidates.llm_profiles.map((p) => (
                        <option key={p.id} value={p.id} disabled={!p.enabled}>
                          {p.name} · {p.provider} · {p.model}
                        </option>
                      ))}
                    </Select>
                  </div>
                ))}
                {pv.dependencies.connectors.map((c) => (
                  <div key={c.ref} className="grid grid-cols-[1fr_1fr] items-center gap-2 text-xs">
                    <div>
                      <span className="font-mono">{c.ref}</span> · MCP {c.name ?? "?"} <span className="text-muted">({c.provider})</span>
                      <div className="text-2xs text-muted">tool: {c.tools.map((t) => t.name).join(", ") || "—"}</div>
                    </div>
                    <Select
                      aria-label={`${c.ref} eşleştirmesi`}
                      value={mapping[c.ref] ?? ""}
                      onChange={(e) => {
                        const m = { ...mapping, [c.ref]: e.target.value || null };
                        setMapping(m);
                        void preview(pkg!, m);
                      }}
                    >
                      <option value="">eşleştirme yok</option>
                      {pv.candidates.connectors.map((x) => (
                        <option key={x.id} value={x.id}>
                          {x.name} · {x.provider}
                        </option>
                      ))}
                    </Select>
                  </div>
                ))}
              </div>
              {pv.dependencies.memory_source === "platform" && <p className="mt-1 text-2xs text-muted">Memory kaynağı: platform (RAG içeriği pakete dahil değildir).</p>}
            </div>
            <IssueList items={mappingErrors} />
            <div className="grid grid-cols-2 gap-2">
              <div>
                <Label htmlFor="imp-target">Hedef</Label>
                <Select id="imp-target" value={target} onChange={(e) => setTarget(e.target.value as "new" | "existing")} className="w-full">
                  <option value="new">Yeni agent</option>
                  <option value="existing" disabled={!custom.length}>
                    Mevcut özel agent (yeni taslak revizyonu)
                  </option>
                </Select>
              </div>
              {target === "new" ? (
                <div>
                  <Label htmlFor="imp-name">Ad</Label>
                  <Input id="imp-name" maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
                  {pv.name_conflict && name.trim().toLowerCase() === pv.agent.name.toLowerCase() && (
                    <p className="mt-0.5 text-2xs text-st-warning-ink">Bu adda bir agent var; farklı bir ad verin.</p>
                  )}
                </div>
              ) : (
                <div>
                  <Label htmlFor="imp-agent">Agent</Label>
                  <Select id="imp-agent" value={agentId} onChange={(e) => setAgentId(e.target.value)} className="w-full">
                    <option value="">seçin…</option>
                    {custom.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                  </Select>
                  <p className="mt-0.5 text-2xs text-muted">Yayımlanmış sürüm değişmez; yeni revizyon taslak olarak gelir.</p>
                </div>
              )}
            </div>
          </>
        )}
        {err && (
          <Notice tone="critical">
            {err.message}
            <IssueList items={errIssues} />
          </Notice>
        )}
      </div>
    </Dialog>
  );
}
