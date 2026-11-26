/** Builder sağ paneli: seçili node'un ayarları ya da agent ayarları. Değerler tanıma yazılır; doğrulama backend'dedir
 *  (kaydet / test / yayımla), buradaki ipuçları yalnız yardımcıdır. Bağlantılar kimlikle seçilir; secret alanı yok. */
import { AlertTriangle, ArrowRight, Lightbulb, Lock, Plus, Trash2, XCircle } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { TabPanel, Tabs } from "@/components/ui/overlay";
import { Button, Input, Label, Select, Textarea } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";
import { NO_VALUE_OPS, NODE_TR, OPERATOR_TR, type AgentMode, type Catalog, type Definition, type Issue, type ResolvedModel, type WfNode } from "./api";
import { availableVars, NODE_GUIDE } from "./guide";

/** Kullanılabilir değişkenler: tıklayınca alana eklenir. Yalnız bu node'a BAĞLI önceki adımların çıktıları listelenir. */
export function VarChips({ def, nodeId, onPick, raw, disabled }: { def: Definition; nodeId: string; onPick: (v: string) => void; raw?: boolean; disabled?: boolean }) {
  const vars = availableVars(def, nodeId);
  if (disabled) return null;
  return (
    <div className="mt-1">
      <span className="text-2xs text-muted">Eklemek için tıklayın: </span>
      <span className="inline-flex flex-wrap gap-1">
        {vars.map((v) => (
          <button
            key={v.name}
            type="button"
            title={`${v.label} · kaynak: ${v.from}`}
            onClick={() => onPick(raw ? v.name : `{{${v.name}}}`)}
            className="rounded-sm border border-line bg-surface-2 px-1 font-mono text-2xs text-accent-text hover:border-accent"
          >
            {raw ? v.name : `{{${v.name}}}`}
          </button>
        ))}
      </span>
      {vars.length <= 2 && <p className="text-2xs text-muted">Önceki adımların çıktıları, bu node'un girişi bağlandıkça burada görünür.</p>}
    </div>
  );
}

/** Node rehberi: ne yapar, ne zaman kullanılır, öncesine / sonrasına ne bağlanır + tek tıkla sonraki adım. */
function GuideCard({ n, readOnly, onAddAfter, hasNext }: { n: WfNode; readOnly: boolean; onAddAfter?: (type: string, branch: "true" | "false" | null) => void; hasNext: Record<string, boolean> }) {
  const g = NODE_GUIDE[n.type];
  if (!g) return null;
  const add = (b: "true" | "false" | null, label?: string) =>
    !readOnly &&
    onAddAfter &&
    g.after.length > 0 && (
      <div className="mt-1.5">
        <div className="text-2xs font-medium text-ink-2">{label ?? "Sonraki adımı ekle ve bağla:"}</div>
        <div className="mt-0.5 flex flex-wrap gap-1">
          {g.after.map((t) => (
            <Button key={t} size="sm" variant="outline" className="h-6 px-1.5 text-2xs" onClick={() => onAddAfter(t, b)} title={NODE_GUIDE[t]?.what}>
              <ArrowRight className="size-3" /> {NODE_TR[t]}
            </Button>
          ))}
        </div>
      </div>
    );
  return (
    <div className="mb-3 rounded-md border border-accent/30 bg-accent/5 p-2 text-xs" role="note" aria-label="node rehberi">
      <p className="text-ink">{g.what}</p>
      <p className="mt-1 text-2xs text-ink-2">
        <b>Ne zaman:</b> {g.when}
      </p>
      <p className="mt-0.5 text-2xs text-ink-2">
        <b>Öncesi:</b> {g.before}
      </p>
      {g.after.length > 0 && (
        <p className="mt-0.5 text-2xs text-ink-2">
          <b>Sonrası:</b> genelde {g.after.map((t) => NODE_TR[t]).join(", ")}
        </p>
      )}
      <p className="mt-1 flex items-start gap-1 text-2xs text-muted">
        <Lightbulb className="mt-px size-3 shrink-0 text-st-warning-ink" /> {g.tip}
      </p>
      {n.type === "condition" ? (
        <>
          {add("true", hasNext.true ? "Yeşil (true) yola araya adım ekle:" : "Yeşil (true) yol, koşul doğruysa:")}
          {add("false", hasNext.false ? "Kırmızı (false) yola araya adım ekle:" : "Kırmızı (false) yol, koşul yanlışsa:")}
        </>
      ) : (
        add(null, hasNext.out ? "Bu adımla sonraki arasına ekle:" : undefined)
      )}
    </div>
  );
}

// ---------------------------------------------------------------- küçük yardımcılar
function Row({ label, hint, issue, children, htmlFor }: { label: ReactNode; hint?: ReactNode; issue?: Issue[]; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="mb-3">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {hint && <p className="mt-0.5 text-2xs text-muted">{hint}</p>}
      {issue?.map((i, k) => (
        <p key={k} className={cn("mt-0.5 flex items-start gap-1 text-2xs", i.severity === "error" ? "text-st-critical-ink" : "text-st-warning-ink")}>
          {i.severity === "error" ? <XCircle className="mt-px size-3 shrink-0" /> : <AlertTriangle className="mt-px size-3 shrink-0" />}
          {i.message}
        </p>
      ))}
    </div>
  );
}

const num = (v: string): number | null => (v.trim() === "" ? null : Number(v));

/** JSON metin alanı: geçerli JSON olunca değeri yazar; geçersizse yerinde uyarır (kaydetmez). */
export function JsonField({ value, onChange, id, disabled, rows = 6 }: { value: unknown; onChange: (v: Record<string, unknown> | null) => void; id: string; disabled?: boolean; rows?: number }) {
  const [text, setText] = useState(value == null ? "" : JSON.stringify(value, null, 2));
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setText(value == null ? "" : JSON.stringify(value, null, 2));
    setErr(null);
  }, [value]);
  return (
    <>
      <Textarea
        id={id}
        rows={rows}
        disabled={disabled}
        value={text}
        spellCheck={false}
        className="w-full font-mono text-xs"
        onChange={(e) => setText(e.target.value)}
        onBlur={() => {
          if (!text.trim()) return (setErr(null), onChange(null));
          try {
            const v = JSON.parse(text);
            if (typeof v !== "object" || Array.isArray(v) || v === null) throw new Error("JSON nesnesi olmalı");
            setErr(null);
            onChange(v);
          } catch (e) {
            setErr(`Geçersiz JSON: ${(e as Error).message}`);
          }
        }}
      />
      {err && <p className="mt-0.5 text-2xs text-st-critical-ink">{err}</p>}
    </>
  );
}

function ModelOverride({ n, cat, set, issues, resolved, readOnly }: { n: WfNode; cat: Catalog; set: (k: string, v: unknown) => void; issues: Issue[]; resolved: ResolvedModel | null; readOnly: boolean }) {
  const c = n.config;
  const pid = (c.llm_profile_id as string) ?? "";
  const prof = cat.llm_profiles.find((p) => p.id === (pid || resolved?.profile_id));
  const f = (k: string) => issues.filter((i) => i.field === k);
  return (
    <div className="mb-3 rounded-md border border-line p-2">
      <p className="mb-2 text-2xs text-muted">
        Çözülmüş model: <span className="font-mono text-ink">{resolved?.model ?? "seçilmedi"}</span>{" "}
        ({resolved?.source === "node" ? "bu node'un ayarı" : "agent varsayılanı"}{resolved?.profile ? ` · ${resolved.profile}` : ""})
      </p>
      <Row label="LLM (bu node için)" htmlFor={`${n.id}-prof`} issue={f("llm_profile_id")}>
        <Select id={`${n.id}-prof`} disabled={readOnly} value={pid} onChange={(e) => set("llm_profile_id", e.target.value || undefined)} className="w-full">
          <option value="">Agent varsayılanı</option>
          {cat.llm_profiles.map((p) => (
            <option key={p.id} value={p.id} disabled={!p.enabled}>
              {p.name} · {p.model}{p.enabled ? "" : " (devre dışı)"}
            </option>
          ))}
        </Select>
      </Row>
      <div className="grid grid-cols-2 gap-2">
        <Row label="Maks. çıktı token" htmlFor={`${n.id}-mt`} issue={f("max_tokens")} hint={prof?.model_max_tokens ? `model sınırı ${prof.model_max_tokens}` : undefined}>
          <Input id={`${n.id}-mt`} type="number" disabled={readOnly} value={(c.max_tokens as number) ?? ""} onChange={(e) => set("max_tokens", num(e.target.value) ?? undefined)} placeholder="varsayılan" />
        </Row>
        <Row label="Effort" htmlFor={`${n.id}-ef`} issue={f("effort")}>
          <Select id={`${n.id}-ef`} disabled={readOnly} value={(c.effort as string) ?? ""} onChange={(e) => set("effort", e.target.value || undefined)} className="w-full">
            <option value="">varsayılan</option>
            {cat.efforts.map((x) => (
              <option key={x}>{x}</option>
            ))}
          </Select>
        </Row>
      </div>
      <Row label="Temperature" htmlFor={`${n.id}-tmp`} issue={f("temperature")} hint={prof && !prof.supports_temperature ? cat.temperature_note : undefined}>
        <Input
          id={`${n.id}-tmp`}
          type="number"
          step="0.1"
          min={0}
          max={1}
          disabled={readOnly || (!!prof && !prof.supports_temperature && c.temperature == null)}
          value={(c.temperature as number) ?? ""}
          onChange={(e) => set("temperature", num(e.target.value) ?? undefined)}
          placeholder={prof && !prof.supports_temperature ? "bu model desteklemiyor" : "varsayılan"}
        />
      </Row>
    </div>
  );
}

// ---------------------------------------------------------------- node ayarları
export function NodeSettings({ node: n, def, cat, issues, resolved, readOnly, onChange, onDelete, onAddAfter }: {
  node: WfNode;
  def: Definition;
  cat: Catalog;
  issues: Issue[];
  resolved: ResolvedModel | null;
  readOnly: boolean;
  onChange: (n: WfNode) => void;
  onDelete: () => void;
  onAddAfter?: (type: string, branch: "true" | "false" | null) => void;
}) {
  const c = n.config ?? {};
  const outs = def.workflow.edges.filter((e) => e.source === n.id);
  const hasNext = { out: outs.some((e) => !e.branch), true: outs.some((e) => e.branch === "true"), false: outs.some((e) => e.branch === "false") };
  const set = (k: string, v: unknown) => {
    const next = { ...c };
    if (v === undefined) delete next[k];
    else next[k] = v;
    onChange({ ...n, config: next });
  };
  const f = (k: string) => issues.filter((i) => i.field === k);
  const append = (k: string, v: string) => {
    const cur = ((c[k] as string) ?? "").replace(/\s+$/, "");
    set(k, cur ? `${cur} ${v}` : v);
  };
  const general = issues.filter((i) => !i.field || !["prompt", "instructions", "output_var", "tool", "args", "args.dqlQueryString", "query", "top_k", "field", "operator", "value", "template", "schema", "max_tokens", "effort", "temperature", "llm_profile_id", "timeout_s"].includes(i.field));
  const con = cat.connectors.find((x) => x.id === def.settings.tools.connector_id);
  const ta = (k: string, label: string, hint?: string, rows = 4, chips = true) => (
    <Row label={label} htmlFor={`${n.id}-${k}`} hint={hint} issue={f(k)}>
      <Textarea id={`${n.id}-${k}`} rows={rows} disabled={readOnly} value={(c[k] as string) ?? ""} onChange={(e) => set(k, e.target.value)} className="w-full font-mono text-xs" />
      {chips && <VarChips def={def} nodeId={n.id} disabled={readOnly} onPick={(v) => append(k, v)} />}
    </Row>
  );
  const ovar = (
    <Row label="Çıktı değişkeni" htmlFor={`${n.id}-ov`} issue={f("output_var")} hint="Bu adımın sonucu bu adla saklanır; sonraki adımlarda {{ad}} ile kullanılır. Küçük harf, rakam ve _ kullanın (ör. answer, loglar).">
      <Input id={`${n.id}-ov`} disabled={readOnly} value={(c.output_var as string) ?? ""} onChange={(e) => set("output_var", e.target.value.trim())} className="font-mono" />
    </Row>
  );
  return (
    <div>
      <div className="mb-3 flex items-center gap-2">
        <span className="text-[13px] font-semibold text-ink">{NODE_TR[n.type] ?? n.type}</span>
        <span className="font-mono text-2xs text-muted">{n.id}</span>
        {!readOnly && n.type !== "start" && (
          <Button size="sm" variant="ghost" className="ml-auto text-st-critical-ink" onClick={onDelete} aria-label="node'u sil">
            <Trash2 /> Sil
          </Button>
        )}
      </div>
      {general.map((i, k) => (
        <p key={k} className={cn("mb-2 flex items-start gap-1 text-xs", i.severity === "error" ? "text-st-critical-ink" : "text-st-warning-ink")}>
          {i.severity === "error" ? <XCircle className="mt-0.5 size-3.5 shrink-0" /> : <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />}
          {i.message}
        </p>
      ))}
      <GuideCard n={n} readOnly={readOnly} onAddAfter={onAddAfter} hasNext={hasNext} />
      <Row label="Etiket" htmlFor={`${n.id}-label`} hint="Canvas'ta görünen ad; ne yaptığını anlatan kısa bir ad verin (ör. 'Hata loglarını çek').">
        <Input id={`${n.id}-label`} disabled={readOnly} maxLength={80} value={n.label} onChange={(e) => onChange({ ...n, label: e.target.value })} />
      </Row>
      {n.type === "start" && (
        <p className="text-xs text-muted">
          Girdi <code>{"{{input}}"}</code> (soru-cevapta kullanıcı sorusu, zamanlanmış modda schedule girdisi), <code>{"{{now}}"}</code>, <code>{"{{agent}}"}</code>,{" "}
          <code>{"{{mode}}"}</code> ve agent ayarlarındaki başlangıç değişkenleri buradan akar.
        </p>
      )}
      {n.type === "llm" && (
        <>
          {ta("prompt", "Prompt", "Modele bu adımda ne yapmasını istediğinizi yazın. Kullanıcının sorusu {{input}}; önceki adımların sonuçları kendi değişken adlarıyla.", 6)}
          {ta("instructions", "Ek talimat (system)", "İsteğe bağlı: yalnız bu adım için kural (ör. 'En fazla 5 madde yaz').", 3, false)}
          {ovar}
          <label className="mb-3 flex items-center gap-2 text-xs text-ink-2">
            <input type="checkbox" disabled={readOnly} checked={c.stream !== false} onChange={(e) => set("stream", e.target.checked)} />
            Soru-cevapta yanıtı akış (streaming) olarak gönder
          </label>
          <ModelOverride n={n} cat={cat} set={set} issues={issues} resolved={resolved} readOnly={readOnly} />
        </>
      )}
      {n.type === "structured_output" && (
        <>
          {ta("prompt", "Prompt", undefined, 5)}
          {ovar}
          <Row label="JSON şeması" htmlFor={`${n.id}-schema`} issue={f("schema")} hint="Model çıktısı bu şemaya göre üretilir ve doğrulanır">
            <JsonField id={`${n.id}-schema`} disabled={readOnly} value={c.schema ?? null} onChange={(v) => set("schema", v ?? undefined)} rows={8} />
          </Row>
          <ModelOverride n={n} cat={cat} set={set} issues={issues} resolved={resolved} readOnly={readOnly} />
        </>
      )}
      {n.type === "mcp_tool" && (
        <>
          {!con && <p className="mb-2 text-xs text-st-warning-ink">Agent ayarları › MCP bölümünden connector seçin.</p>}
          <Row label="Tool" htmlFor={`${n.id}-tool`} issue={f("tool")} hint="Yalnız agent tool allowlist'indeki ve platformda yürütülebilen tool'lar çalışır">
            <Select id={`${n.id}-tool`} disabled={readOnly} value={(c.tool as string) ?? ""} onChange={(e) => set("tool", e.target.value)} className="w-full">
              <option value="">seçin…</option>
              {(con?.tools ?? []).map((t) => (
                <option key={t.name} value={t.name} disabled={!t.executable || !def.settings.tools.allowed_tools.includes(t.name)}>
                  {t.name}
                  {!t.executable ? " (yürütme yolu yok)" : !def.settings.tools.allowed_tools.includes(t.name) ? " (allowlist'te değil)" : ""}
                </option>
              ))}
            </Select>
          </Row>
          {c.tool === "execute-dql" ? (
            <Row label="DQL" htmlFor={`${n.id}-dql`} issue={f("args.dqlQueryString")} hint="Şablon değişkenleri metin olarak yerleştirilir">
              <Textarea
                id={`${n.id}-dql`}
                rows={6}
                disabled={readOnly}
                className="w-full font-mono text-xs"
                value={((c.args as Record<string, unknown>)?.dqlQueryString as string) ?? ""}
                onChange={(e) => set("args", { ...((c.args as object) ?? {}), dqlQueryString: e.target.value })}
              />
              <VarChips
                def={def}
                nodeId={n.id}
                disabled={readOnly}
                onPick={(v) => set("args", { ...((c.args as object) ?? {}), dqlQueryString: `${((c.args as Record<string, unknown>)?.dqlQueryString as string) ?? ""}${v}` })}
              />
              <p className="mt-1 text-2xs text-muted">
                Örnek: <code>fetch logs, from:now()-30m | filter loglevel == "ERROR" | limit 20</code>. Sonuç: <code>.status</code> (OK, EMPTY, ERROR) ve <code>.records</code>.
              </p>
            </Row>
          ) : (
            <Row label="Parametreler (JSON)" htmlFor={`${n.id}-args`} issue={f("args")}>
              <JsonField id={`${n.id}-args`} disabled={readOnly} value={c.args ?? {}} onChange={(v) => set("args", v ?? {})} />
            </Row>
          )}
          {ovar}
          <Row label="Çağrı timeout (sn)" htmlFor={`${n.id}-to`} issue={f("timeout_s")} hint={`boş: agent ayarı (${def.settings.tools.call_timeout_s} sn)`}>
            <Input id={`${n.id}-to`} type="number" disabled={readOnly} value={(c.timeout_s as number) ?? ""} onChange={(e) => set("timeout_s", num(e.target.value) ?? undefined)} />
          </Row>
        </>
      )}
      {n.type === "memory_retrieval" && (
        <>
          {def.settings.memory.source !== "platform" && <p className="mb-2 text-xs text-st-warning-ink">Agent ayarları › Memory & Context'te kaynak "platform" olmalı.</p>}
          {ta("query", "Arama metni", "ör. {{input}}", 3)}
          <Row label="Kayıt sayısı (top_k)" htmlFor={`${n.id}-k`} issue={f("top_k")}>
            <Input id={`${n.id}-k`} type="number" disabled={readOnly} value={(c.top_k as number) ?? ""} onChange={(e) => set("top_k", num(e.target.value) ?? undefined)} placeholder={String(def.settings.memory.top_k)} />
          </Row>
          {ovar}
        </>
      )}
      {n.type === "condition" && (
        <>
          <Row label="Alan" htmlFor={`${n.id}-field`} issue={f("field")} hint="Değişken yolu: answer, p95.status, risk.severity…">
            <Input id={`${n.id}-field`} disabled={readOnly} value={(c.field as string) ?? ""} onChange={(e) => set("field", e.target.value.trim())} className="font-mono" />
            <VarChips def={def} nodeId={n.id} raw disabled={readOnly} onPick={(v) => set("field", v)} />
          </Row>
          <Row label="Operatör" htmlFor={`${n.id}-op`} issue={f("operator")}>
            <Select id={`${n.id}-op`} disabled={readOnly} value={(c.operator as string) ?? ""} onChange={(e) => set("operator", e.target.value)} className="w-full">
              <option value="">seçin…</option>
              {cat.operators.map((o) => (
                <option key={o} value={o}>
                  {o} ({OPERATOR_TR[o] ?? o})
                </option>
              ))}
            </Select>
          </Row>
          {!NO_VALUE_OPS.has(String(c.operator ?? "")) && (
            <Row label="Değer" htmlFor={`${n.id}-val`} issue={f("value")}>
              <Input id={`${n.id}-val`} disabled={readOnly} value={c.value == null ? "" : String(c.value)} onChange={(e) => set("value", e.target.value)} />
            </Row>
          )}
          {typeof c.field === "string" && c.field && c.operator ? (
            <p className="rounded-sm bg-surface-2 px-2 py-1 text-2xs text-ink-2">
              Okunuşu: <b>{c.field}</b> {OPERATOR_TR[String(c.operator)] ?? String(c.operator)}
              {!NO_VALUE_OPS.has(String(c.operator)) && <> <b>&quot;{String(c.value ?? "")}&quot;</b></>} ise <span className="text-st-good-ink">yeşil (true)</span>, değilse{" "}
              <span className="text-st-critical-ink">kırmızı (false)</span> yoldan devam eder.
            </p>
          ) : (
            <p className="text-2xs text-muted">Yeşil çıkış: koşul doğru · kırmızı çıkış: yanlış. İki dal da bağlı olmalı.</p>
          )}
        </>
      )}
      {n.type === "result" && ta("template", "Sonuç şablonu", "Kullanıcının göreceği metin. Çoğu zaman yalnız {{answer}} yeterlidir. Zamanlanmış JSON çıktısında bir JSON değişkeni verin (ör. {{risk}}).", 5)}
    </div>
  );
}

// ---------------------------------------------------------------- agent ayarları
export interface Meta {
  name: string;
  description: string;
  modes: AgentMode[];
}

const TABS = [
  { value: "identity", label: "Kimlik" },
  { value: "model", label: "Model" },
  { value: "instructions", label: "Talimatlar" },
  { value: "mcp", label: "MCP" },
  { value: "memory", label: "Memory & Context" },
  { value: "policies", label: "Politikalar" },
  { value: "io", label: "Girdi / Çıktı" },
  { value: "limits", label: "Limitler" },
];

export function AgentSettings({ def, meta, cat, issues, readOnly, onDef, onMeta }: {
  def: Definition;
  meta: Meta;
  cat: Catalog;
  issues: Issue[];
  readOnly: boolean;
  onDef: (d: Definition) => void;
  onMeta: (m: Meta) => void;
}) {
  const [tab, setTab] = useState("identity");
  const st = def.settings;
  const up = <K extends keyof Definition["settings"]>(k: K, v: Partial<Definition["settings"][K]>) =>
    onDef({ ...def, settings: { ...st, [k]: { ...st[k], ...v } } });
  const f = (prefix: string) => issues.filter((i) => !i.node_id && (i.field ?? "").startsWith(prefix));
  const prof = cat.llm_profiles.find((p) => p.id === st.model.llm_profile_id);
  const con = cat.connectors.find((c) => c.id === st.tools.connector_id);
  const tabLabel = (t: (typeof TABS)[number]) => {
    const n = issues.filter((i) => !i.node_id && i.severity === "error" && (i.field ?? "").startsWith(t.value === "identity" ? "name" : `settings.${t.value === "mcp" ? "tools" : t.value}`)).length;
    return n ? `${t.label} (${n})` : t.label;
  };

  return (
    <Tabs value={tab} onValueChange={setTab} tabs={TABS.map((t) => ({ value: t.value, label: tabLabel(t) }))}>
      <TabPanel value="identity">
        <p className="mb-3 rounded-sm bg-surface-2 px-2 py-1.5 text-2xs text-ink-2">Agent'ın adı ve nerede çalışacağı. Soru-Cevap: kullanıcı soru sorduğunda; Zamanlanmış: schedule belirli aralıkla çalıştırdığında.</p>
        <Row label="Ad" htmlFor="ag-name" issue={issues.filter((i) => i.field === "name")}>
          <Input id="ag-name" maxLength={100} disabled={readOnly} value={meta.name} onChange={(e) => onMeta({ ...meta, name: e.target.value })} />
        </Row>
        <Row label="Açıklama" htmlFor="ag-desc">
          <Textarea id="ag-desc" rows={3} maxLength={2000} disabled={readOnly} value={meta.description} onChange={(e) => onMeta({ ...meta, description: e.target.value })} className="w-full" />
        </Row>
        <Row label="Çalışma modları" issue={issues.filter((i) => i.field === "modes")}>
          {(["chat", "scheduled"] as AgentMode[]).map((m) => (
            <label key={m} className="mr-4 inline-flex items-center gap-1.5 text-xs text-ink-2">
              <input
                type="checkbox"
                disabled={readOnly}
                checked={meta.modes.includes(m)}
                onChange={(e) => onMeta({ ...meta, modes: e.target.checked ? [...meta.modes, m] : meta.modes.filter((x) => x !== m) })}
              />
              {m === "chat" ? "Soru-Cevap" : "Zamanlanmış"}
            </label>
          ))}
        </Row>
      </TabPanel>
      <TabPanel value="model">
        <p className="mb-3 rounded-sm bg-surface-2 px-2 py-1.5 text-2xs text-ink-2">Agent'ın varsayılan 'beyni'. LLM node'ları ayrıca kendi modelini seçmezse bu kullanılır. Profiller Settings › LLM'de tanımlıdır; API anahtarı orada saklanır.</p>
        <Row label="LLM profili (provider + model)" htmlFor="ag-prof" issue={f("settings.model.llm_profile_id")} hint="Settings › LLM'de tanımlı profiller; API anahtarı profilde saklanır, tanıma yazılmaz">
          <Select id="ag-prof" disabled={readOnly} value={st.model.llm_profile_id ?? ""} onChange={(e) => up("model", { llm_profile_id: e.target.value || null })} className="w-full">
            <option value="">seçin…</option>
            {cat.llm_profiles.map((p) => (
              <option key={p.id} value={p.id} disabled={!p.enabled}>
                {p.name} · {p.provider} · {p.model}{p.enabled ? "" : " (devre dışı)"}
              </option>
            ))}
          </Select>
        </Row>
        {prof && (
          <p className="-mt-2 mb-3 text-2xs text-muted">
            Model: <span className="font-mono">{prof.model}</span>
            {prof.model_max_tokens ? ` · maks. çıktı ${prof.model_max_tokens}` : " · model kapasitesi doğrulanmadı (Settings › LLM › Test)"}
            {prof.capabilities ? ` · structured outputs ${prof.capabilities.structured_outputs === false ? "yok" : "var"}` : ""}
          </p>
        )}
        <div className="grid grid-cols-2 gap-2">
          <Row label="Maks. çıktı token" htmlFor="ag-mt" issue={f("settings.model.max_tokens")}>
            <Input id="ag-mt" type="number" disabled={readOnly} value={st.model.max_tokens ?? ""} onChange={(e) => up("model", { max_tokens: num(e.target.value) })} placeholder={String(prof?.max_tokens ?? "profil")} />
          </Row>
          <Row label="Timeout (sn)" htmlFor="ag-to" issue={f("settings.model.timeout_s")}>
            <Input id="ag-to" type="number" disabled={readOnly} value={st.model.timeout_s ?? ""} onChange={(e) => up("model", { timeout_s: num(e.target.value) })} placeholder={String(prof?.timeout_s ?? "profil")} />
          </Row>
          <Row label="Effort" htmlFor="ag-ef" issue={f("settings.model.effort")}>
            <Select id="ag-ef" disabled={readOnly} value={st.model.effort ?? ""} onChange={(e) => up("model", { effort: (e.target.value || null) as Definition["settings"]["model"]["effort"] })} className="w-full">
              <option value="">profil</option>
              {cat.efforts.map((x) => (
                <option key={x}>{x}</option>
              ))}
            </Select>
          </Row>
          <Row label="Temperature" htmlFor="ag-tmp" issue={f("settings.model.temperature")}>
            <Input
              id="ag-tmp"
              type="number"
              step="0.1"
              min={0}
              max={1}
              disabled={readOnly || (!!prof && !prof.supports_temperature && st.model.temperature == null)}
              value={st.model.temperature ?? ""}
              onChange={(e) => up("model", { temperature: num(e.target.value) })}
              placeholder={prof && !prof.supports_temperature ? "desteklenmiyor" : "varsayılan"}
            />
          </Row>
        </div>
        {prof && !prof.supports_temperature && <p className="text-2xs text-muted">{cat.temperature_note}</p>}
      </TabPanel>
      <TabPanel value="instructions">
        <p className="mb-3 rounded-sm bg-surface-2 px-2 py-1.5 text-2xs text-ink-2">System prompt agent'ın kim olduğunu ve uyacağı kuralları anlatır; her LLM adımına eklenir. Örnek: 'Sen bir SRE asistanısın. Kısa, Türkçe yanıt ver; veride olmayanı iddia etme.'</p>
        <Row label="System prompt" htmlFor="ag-sys" issue={f("settings.instructions.system_prompt")}>
          <Textarea id="ag-sys" rows={7} disabled={readOnly} value={st.instructions.system_prompt} onChange={(e) => up("instructions", { system_prompt: e.target.value })} className="w-full text-xs" />
        </Row>
        <Row label="Görev talimatları" htmlFor="ag-task">
          <Textarea id="ag-task" rows={4} disabled={readOnly} value={st.instructions.task_instructions} onChange={(e) => up("instructions", { task_instructions: e.target.value })} className="w-full text-xs" />
        </Row>
        <Label>Başlangıç değişkenleri</Label>
        {st.instructions.variables.map((v, i) => (
          <div key={i} className="mb-1.5 grid grid-cols-[1fr_1fr_auto] gap-1.5">
            <Input aria-label={`değişken ${i + 1} adı`} disabled={readOnly} value={v.name} className="font-mono text-xs" onChange={(e) => up("instructions", { variables: st.instructions.variables.map((x, k) => (k === i ? { ...x, name: e.target.value.trim() } : x)) })} />
            <Input aria-label={`değişken ${i + 1} varsayılanı`} disabled={readOnly} value={v.default} className="text-xs" placeholder="varsayılan" onChange={(e) => up("instructions", { variables: st.instructions.variables.map((x, k) => (k === i ? { ...x, default: e.target.value } : x)) })} />
            {!readOnly && (
              <Button size="sm" variant="ghost" aria-label="değişkeni sil" onClick={() => up("instructions", { variables: st.instructions.variables.filter((_, k) => k !== i) })}>
                <Trash2 />
              </Button>
            )}
          </div>
        ))}
        {f("settings.instructions.variables").map((i, k) => (
          <p key={k} className="text-2xs text-st-critical-ink">{i.message}</p>
        ))}
        {!readOnly && (
          <Button size="sm" variant="outline" onClick={() => up("instructions", { variables: [...st.instructions.variables, { name: `var_${st.instructions.variables.length + 1}`, default: "", description: "" }] })}>
            <Plus /> Değişken
          </Button>
        )}
      </TabPanel>
      <TabPanel value="mcp">
        <p className="mb-3 rounded-sm bg-surface-2 px-2 py-1.5 text-2xs text-ink-2">Agent'ın veri çekebileceği kaynak. MCP Tool node'u kullanacaksanız connector seçip izin verilen tool'ları işaretleyin.</p>
        <Row label="Connector" htmlFor="ag-con" issue={f("settings.tools.connector_id")}>
          <Select id="ag-con" disabled={readOnly} value={st.tools.connector_id ?? ""} onChange={(e) => up("tools", { connector_id: e.target.value || null, allowed_tools: [] })} className="w-full">
            <option value="">yok</option>
            {cat.connectors.map((c) => (
              <option key={c.id} value={c.id} disabled={!c.enabled}>
                {c.name} · {c.provider}{c.enabled ? "" : " (devre dışı)"}
              </option>
            ))}
          </Select>
        </Row>
        {con && (
          <Row label="Tool allowlist" issue={f("settings.tools.allowed_tools")} hint="Platformun zorunlu Tool allowlist middleware'i ve connector onayı ayrıca uygulanır">
            {con.tools.map((t) => (
              <label key={t.name} className="flex items-center gap-1.5 py-0.5 text-xs text-ink-2">
                <input
                  type="checkbox"
                  disabled={readOnly}
                  checked={st.tools.allowed_tools.includes(t.name)}
                  onChange={(e) => up("tools", { allowed_tools: e.target.checked ? [...st.tools.allowed_tools, t.name] : st.tools.allowed_tools.filter((x) => x !== t.name) })}
                />
                <span className="font-mono">{t.name}</span>
                {!t.executable && <span className="text-2xs text-muted">· platformda yürütme yolu yok</span>}
                {t.destructive && <span className="text-2xs text-st-warning-ink">· yazma yetenekli (policy reddeder)</span>}
                {!con.allowed_tools.includes(t.name) && <span className="text-2xs text-muted">· connector'da onaylı değil</span>}
              </label>
            ))}
          </Row>
        )}
        <Row label="Çağrı timeout (sn)" htmlFor="ag-cto">
          <Input id="ag-cto" type="number" disabled={readOnly} value={st.tools.call_timeout_s} onChange={(e) => up("tools", { call_timeout_s: num(e.target.value) ?? 60 })} />
        </Row>
      </TabPanel>
      <TabPanel value="memory">
        <p className="mb-3 rounded-sm bg-surface-2 px-2 py-1.5 text-2xs text-ink-2">Geçmiş kayıtları kullanmak için kaynağı 'platform' yapın. Geçmiş tur: Soru-Cevap'ta önceki kaç mesajın modele gönderileceği.</p>
        <Row label="RAG kaynağı" htmlFor="ag-mem" issue={f("settings.memory")}>
          <Select id="ag-mem" disabled={readOnly} value={st.memory.source} onChange={(e) => up("memory", { source: e.target.value as "none" | "platform" })} className="w-full">
            <option value="none">yok</option>
            <option value="platform">platform memory (Agent Memory)</option>
          </Select>
        </Row>
        {st.memory.source === "platform" && (
          <Row label="Kapsam">
            {cat.memory_scopes.map((sc) => (
              <label key={sc} className="mr-3 inline-flex items-center gap-1 text-xs text-ink-2">
                <input type="checkbox" disabled={readOnly} checked={st.memory.scope.includes(sc)} onChange={(e) => up("memory", { scope: e.target.checked ? [...st.memory.scope, sc] : st.memory.scope.filter((x) => x !== sc) })} />
                {sc}
              </label>
            ))}
          </Row>
        )}
        <div className="grid grid-cols-3 gap-2">
          <Row label="top_k" htmlFor="ag-k">
            <Input id="ag-k" type="number" disabled={readOnly} value={st.memory.top_k} onChange={(e) => up("memory", { top_k: num(e.target.value) ?? 5 })} />
          </Row>
          <Row label="Geçmiş tur" htmlFor="ag-h" hint="soru-cevap">
            <Input id="ag-h" type="number" disabled={readOnly} value={st.memory.history_turns} onChange={(e) => up("memory", { history_turns: num(e.target.value) ?? 0 })} />
          </Row>
          <Row label="Context bütçesi" htmlFor="ag-cb" hint="token">
            <Input id="ag-cb" type="number" disabled={readOnly} value={st.memory.context_budget_tokens} onChange={(e) => up("memory", { context_budget_tokens: num(e.target.value) ?? 60000 })} />
          </Row>
        </div>
      </TabPanel>
      <TabPanel value="policies">
        <p className="mb-3 rounded-sm bg-surface-2 px-2 py-1.5 text-2xs text-ink-2">Güvenlik kuralları. Mandatory (lock simgeli) olanlar her agent'ta zorunlu çalışır; diğerlerini bu agent için kapatabilirsiniz.</p>
        <p className="mb-2 text-xs text-muted">
          Zorunlu platform politikaları (redaction, call limit, tool allowlist, tool parametre doğrulama) özel agent'larda da her koşulda çalışır. Yalnız
          zorunlu olmayan middleware'ler bu agent için kapatılabilir. Guardrail ve onay kuralları platform policy'sinden gelir.
        </p>
        {f("settings.policies").map((i, k) => (
          <p key={k} className="mb-1 text-2xs text-st-critical-ink">{i.message}</p>
        ))}
        {cat.middleware.map((m) => (
          <label key={m.name} className="flex items-start gap-2 border-b border-line py-1.5 text-xs">
            {m.mandatory ? (
              <Lock className="mt-0.5 size-3.5 shrink-0 text-muted" aria-label="zorunlu" />
            ) : (
              <input
                type="checkbox"
                className="mt-0.5"
                disabled={readOnly || !m.platform_enabled}
                checked={m.platform_enabled && st.policies.optional[m.name] !== false}
                onChange={(e) => up("policies", { optional: { ...st.policies.optional, [m.name]: e.target.checked } })}
              />
            )}
            <span>
              <span className="font-medium text-ink">{m.title}</span> <span className="font-mono text-2xs text-muted">{m.name}</span>
              {m.mandatory && <span className="ml-1 text-2xs text-muted">· zorunlu</span>}
              {!m.platform_enabled && <span className="ml-1 text-2xs text-muted">· platformda kapalı</span>}
              <span className="block text-2xs text-muted">{m.description}</span>
            </span>
          </label>
        ))}
      </TabPanel>
      <TabPanel value="io">
        <p className="mb-3 rounded-sm bg-surface-2 px-2 py-1.5 text-2xs text-ink-2">Girdi ve çıktının biçimi. Çoğu agent için varsayılanlar yeterlidir; zamanlanmış rapor JSON olacaksa çıktı şeması verin.</p>
        <label className="mb-3 flex items-center gap-2 text-xs text-ink-2">
          <input type="checkbox" disabled={readOnly} checked={st.io.streaming} onChange={(e) => up("io", { streaming: e.target.checked })} />
          Soru-cevapta streaming yanıt
        </label>
        <Row label="Girdi şeması (JSON Schema, isteğe bağlı)" htmlFor="ag-is" issue={f("settings.io.input_schema")} hint="Tanımlıysa girdi JSON olarak bu şemaya göre doğrulanır">
          <JsonField id="ag-is" disabled={readOnly} value={st.io.input_schema} onChange={(v) => up("io", { input_schema: v })} rows={4} />
        </Row>
        <Row label="Zamanlanmış çıktı formatı" htmlFor="ag-of" issue={f("settings.io.scheduled_output_format")}>
          <Select id="ag-of" disabled={readOnly} value={st.io.scheduled_output_format} onChange={(e) => up("io", { scheduled_output_format: e.target.value as "markdown" | "json" })} className="w-full">
            <option value="markdown">markdown</option>
            <option value="json">json (çıktı şeması ile)</option>
          </Select>
        </Row>
        <Row label="Çıktı şeması (JSON Schema)" htmlFor="ag-os" issue={f("settings.io.output_schema")}>
          <JsonField id="ag-os" disabled={readOnly} value={st.io.output_schema} onChange={(v) => up("io", { output_schema: v })} rows={5} />
        </Row>
        <Row label="Varsayılan zamanlanmış girdi" htmlFor="ag-si">
          <Textarea id="ag-si" rows={2} disabled={readOnly} value={st.io.scheduled_input} onChange={(e) => up("io", { scheduled_input: e.target.value })} className="w-full text-xs" />
        </Row>
      </TabPanel>
      <TabPanel value="limits">
        <p className="mb-3 rounded-sm bg-surface-2 px-2 py-1.5 text-2xs text-ink-2">Kaçak çalışmayı önleyen sınırlar. Emin değilseniz varsayılanları bırakın.</p>
        <div className="grid grid-cols-2 gap-2">
          {(
            [
              ["timeout_s", "Toplam timeout (sn)"],
              ["max_steps", "Maks. adım"],
              ["max_tool_calls", "Maks. tool çağrısı"],
              ["max_llm_calls", "Maks. LLM çağrısı"],
              ["token_budget", "Token bütçesi"],
              ["retries", "Retry (LLM / MCP)"],
            ] as [keyof Definition["settings"]["limits"], string][]
          ).map(([k, l]) => (
            <Row key={k} label={l} htmlFor={`ag-l-${k}`} issue={f(`settings.limits.${k}`)}>
              <Input id={`ag-l-${k}`} type="number" disabled={readOnly} value={st.limits[k]} onChange={(e) => up("limits", { [k]: num(e.target.value) ?? 0 })} />
            </Row>
          ))}
        </div>
      </TabPanel>
    </Tabs>
  );
}
