/** Builder rehberi: node'ların sade açıklaması, bağlantı kuralları (backend doğrulamasıyla aynı), kullanılabilir
 *  değişkenler, kurulum kontrol listesi ve hazır şablonlar. Kurallar yalnız yol göstericidir; son karar backend'dedir. */
import type { Catalog, Definition, WfEdge, WfNode } from "./api";

export interface NodeGuide {
  title: string;
  what: string;
  when: string;
  before: string;
  after: string[];
  tip: string;
}

export const NODE_GUIDE: Record<string, NodeGuide> = {
  start: {
    title: "Başlangıç",
    what: "Agent'ın giriş kapısı. Kullanıcının sorusu (ya da schedule girdisi) buradan {{input}} olarak akar.",
    when: "Her workflow'da tam bir tane bulunur ve silinemez.",
    before: "Önüne hiçbir şey bağlanmaz.",
    after: ["llm", "mcp_tool", "memory_retrieval", "condition"],
    tip: "Genelde önce veri toplanır (MCP Tool / Memory Retrieval), sonra LLM ile yorumlanır.",
  },
  llm: {
    title: "LLM",
    what: "Modele bir prompt gönderir ve yanıtı bir değişkene yazar (ör. answer).",
    when: "Metin üretmek, özetlemek, açıklamak ya da toplanan veriyi yorumlamak için.",
    before: "Başlangıç'tan ya da veri toplayan bir node'dan (MCP Tool, Memory Retrieval) sonra gelir.",
    after: ["result", "condition", "llm"],
    tip: "Prompt'ta önceki node'ların çıktısını {{değişken}} ile kullanın. Son LLM genelde Sonuç'a bağlanır.",
  },
  mcp_tool: {
    title: "MCP Tool",
    what: "Dynatrace'ten veri çeker (DQL). Sonuç bir değişkene yazılır: durum (status), kayıtlar (records), hata (error).",
    when: "Agent'ın gerçek telemetriye (log, metric, trace) bakması gerektiğinde.",
    before: "Başlangıç'tan ya da başka bir node'dan sonra gelir. Agent ayarları › MCP'den connector ve tool seçilmiş olmalı.",
    after: ["llm", "condition", "structured_output"],
    tip: "Çıktıyı ham göstermek yerine bir LLM node'una verip yorumlatın; veri yoksa Koşul ile ayrı dal açın.",
  },
  memory_retrieval: {
    title: "Memory Retrieval",
    what: "Platformun geçmiş kayıtlarından (investigation, incident, RCA, runbook) soruyla ilgili olanları getirir.",
    when: "Benzer sorun daha önce yaşandı mı, runbook ne diyor gibi sorular için.",
    before: "Başlangıç'tan sonra gelir. Agent ayarları › Memory & Context'te kaynak 'platform' olmalı.",
    after: ["llm", "condition"],
    tip: "Getirilen kayıtları bir LLM node'unun prompt'una {{değişken}} olarak verin.",
  },
  condition: {
    title: "Koşul",
    what: "Bir değeri kontrol eder ve akışı iki yola ayırır: koşul doğruysa yeşil (true), değilse kırmızı (false) çıkış.",
    when: "Veri geldi mi, risk yüksek mi, yanıt boş mu gibi kararlar için.",
    before: "Kontrol edilecek değeri üreten node'dan sonra gelir (ör. MCP Tool → p95.status).",
    after: ["llm", "result", "structured_output"],
    tip: "İki çıkışın da bağlı olması gerekir; her dal kendi Sonuç'una gidebilir.",
  },
  structured_output: {
    title: "Structured Output",
    what: "Modelden belirlediğiniz JSON şemasına uyan yapılandırılmış yanıt alır (ör. {severity, summary}).",
    when: "Çıktının bir rapor, entegrasyon ya da Koşul tarafından okunacağı durumlarda; özellikle zamanlanmış JSON çıktısında.",
    before: "Veri toplayan node'lardan ya da Başlangıç'tan sonra gelir.",
    after: ["result", "condition"],
    tip: "Ardından Koşul ile {{değişken.alan}} kontrol edebilirsiniz (ör. risk.severity eq high).",
  },
  result: {
    title: "Sonuç",
    what: "Agent'ın kullanıcıya (ya da schedule'a) döndüreceği son çıktı. Şablonda önceki değişkenleri kullanır.",
    when: "Her yolun sonunda bir Sonuç olmalı.",
    before: "Son LLM / Structured Output node'undan ya da Koşul dalından sonra gelir.",
    after: [],
    tip: "En basit şablon {{answer}}: son LLM'in yanıtını olduğu gibi döndürür.",
  },
};

// ---------------------------------------------------------------- bağlantı kuralları
export function connectionProblem(def: Definition, source: string, target: string, branch: string | null): string | null {
  const s = def.workflow.nodes.find((n) => n.id === source);
  const t = def.workflow.nodes.find((n) => n.id === target);
  if (!s || !t) return "Node bulunamadı.";
  if (source === target) return "Bir node kendisine bağlanamaz.";
  if (t.type === "start") return "Başlangıç'a giriş bağlanamaz; akış her zaman Başlangıç'tan başlar.";
  if (s.type === "result") return "Sonuç son adımdır; ondan sonra bir şey bağlanamaz.";
  if (s.type === "condition" && !branch) return "Koşul'dan çıkarken yeşil (true) ya da kırmızı (false) noktadan sürükleyin.";
  if (reaches(def.workflow.edges, target, source))
    return "Bu bağlantı döngü oluşturur (akış geri dönerdi). Workflow ileri doğru akmalı; tekrar gerekiyorsa ayrı bir node ekleyin.";
  return null;
}

function reaches(edges: WfEdge[], from: string, to: string): boolean {
  const seen = new Set<string>();
  const stack = [from];
  while (stack.length) {
    const x = stack.pop()!;
    if (x === to) return true;
    if (seen.has(x)) continue;
    seen.add(x);
    for (const e of edges) if (e.source === x) stack.push(e.target);
  }
  return false;
}

export function ancestors(def: Definition, id: string): WfNode[] {
  const seen = new Set<string>();
  const stack = [id];
  while (stack.length) {
    const x = stack.pop()!;
    for (const e of def.workflow.edges)
      if (e.target === x && !seen.has(e.source)) {
        seen.add(e.source);
        stack.push(e.source);
      }
  }
  return def.workflow.nodes.filter((n) => seen.has(n.id));
}

export interface VarHint {
  name: string;
  label: string;
  from: string;
}

/** Bu node'da kullanılabilecek değişkenler: Başlangıç değerleri + önceki (bağlı) node'ların çıktıları. */
export function availableVars(def: Definition, id: string): VarHint[] {
  const out: VarHint[] = [
    { name: "input", label: "kullanıcı sorusu / schedule girdisi", from: "Başlangıç" },
    { name: "now", label: "şu anki zaman", from: "Başlangıç" },
    ...def.settings.instructions.variables.map((v) => ({ name: v.name, label: v.description || "başlangıç değişkeni", from: "Agent ayarları" })),
  ];
  for (const n of ancestors(def, id)) {
    const ov = n.config.output_var as string | undefined;
    if (!ov) continue;
    const kind =
      n.type === "mcp_tool" ? "tool sonucu" : n.type === "memory_retrieval" ? "geçmiş kayıtlar" : n.type === "structured_output" ? "JSON yanıt" : "model yanıtı";
    out.push({ name: ov, label: kind, from: n.label || n.id });
    if (n.type === "mcp_tool") {
      out.push({ name: `${ov}.status`, label: "OK / EMPTY / ERROR", from: n.label || n.id });
      out.push({ name: `${ov}.records`, label: "dönen kayıtlar", from: n.label || n.id });
    }
    if (n.type === "structured_output") {
      const props = ((n.config.schema as { properties?: Record<string, unknown> })?.properties ?? {}) as Record<string, unknown>;
      for (const k of Object.keys(props)) out.push({ name: `${ov}.${k}`, label: "şema alanı", from: n.label || n.id });
    }
  }
  return out;
}

// ---------------------------------------------------------------- yapı ipuçları (canvas)
export interface Hint {
  kind: "no_input" | "no_output" | "cond_branch";
  message: string;
}

export function structureHints(def: Definition): Record<string, Hint[]> {
  const out: Record<string, Hint[]> = {};
  const add = (id: string, h: Hint) => (out[id] ??= []).push(h);
  for (const n of def.workflow.nodes) {
    const ins = def.workflow.edges.filter((e) => e.target === n.id);
    const outs = def.workflow.edges.filter((e) => e.source === n.id);
    if (n.type !== "start" && !ins.length) add(n.id, { kind: "no_input", message: "Girişi bağlı değil: önceki bir adımın çıkışını (sağdaki nokta) buraya sürükleyin." });
    if (n.type === "condition") {
      const missing = ["true", "false"].filter((b) => !outs.some((e) => e.branch === b));
      if (missing.length) add(n.id, { kind: "cond_branch", message: `${missing.map((b) => (b === "true" ? "yeşil (true)" : "kırmızı (false)")).join(" ve ")} çıkış bağlı değil.` });
    } else if (n.type !== "result" && !outs.length) add(n.id, { kind: "no_output", message: "Çıkışı bağlı değil: sağdaki noktadan bir sonraki adıma sürükleyin ya da 'Sonraki adım' ile ekleyin." });
  }
  return out;
}

// ---------------------------------------------------------------- kurulum kontrol listesi
export interface Step {
  key: string;
  label: string;
  done: boolean;
  hint: string;
  action?: { kind: "tab"; tab: string } | { kind: "node"; id: string };
}

export function checklist(def: Definition, opts: { dirty: boolean; versions: number; tested: boolean; errors: number; modes: string[] }): Step[] {
  const hints = structureHints(def);
  const loose = Object.keys(hints)[0];
  const st = def.settings;
  const usesMcp = def.workflow.nodes.some((n) => n.type === "mcp_tool");
  const usesMem = def.workflow.nodes.some((n) => n.type === "memory_retrieval");
  const results = def.workflow.nodes.filter((n) => n.type === "result");
  return [
    { key: "mode", label: "Çalışma modunu seçin", done: opts.modes.length > 0, hint: "Soru-Cevap: kullanıcı soru sorar. Zamanlanmış: schedule belirli aralıkla çalıştırır.", action: { kind: "tab", tab: "agent" } },
    { key: "model", label: "Model (LLM profili) seçin", done: !!st.model.llm_profile_id, hint: "Agent ayarları › Model. Node'lar ayrıca kendi modelini seçebilir.", action: { kind: "tab", tab: "agent" } },
    { key: "prompt", label: "Agent'a ne yapacağını anlatın", done: st.instructions.system_prompt.trim().length > 20, hint: "Agent ayarları › Talimatlar › System prompt: rolü ve kuralları yazın.", action: { kind: "tab", tab: "agent" } },
    ...(usesMcp
      ? [{ key: "mcp", label: "MCP connector ve tool'u seçin", done: !!st.tools.connector_id && st.tools.allowed_tools.length > 0, hint: "Agent ayarları › MCP: connector seçip execute-dql'i işaretleyin.", action: { kind: "tab" as const, tab: "agent" } }]
      : []),
    ...(usesMem ? [{ key: "mem", label: "Memory kaynağını açın", done: st.memory.source === "platform", hint: "Agent ayarları › Memory & Context › kaynak: platform.", action: { kind: "tab" as const, tab: "agent" } }] : []),
    { key: "result", label: "Akışı bir Sonuç ile bitirin", done: results.length > 0, hint: "Paletten Sonuç ekleyip son adımın çıkışını ona bağlayın." },
    {
      key: "wired",
      label: "Tüm node'ları bağlayın",
      done: !loose,
      hint: loose ? `${loose}: ${hints[loose][0].message}` : "Her node'un girişi ve çıkışı bağlı.",
      action: loose ? { kind: "node", id: loose } : undefined,
    },
    { key: "save", label: "Kaydedin", done: !opts.dirty, hint: "Üstteki Kaydet. Kayıtta yapısal hatalar kontrol edilir." },
    { key: "valid", label: "Hataları giderin", done: opts.errors === 0, hint: "Kırmızı çerçeveli node'lara tıklayın; sorun açıklaması ayar panelinde.", action: { kind: "tab", tab: "issues" } },
    { key: "test", label: "Taslağı test edin", done: opts.tested, hint: "Test sekmesinde örnek bir soru yazıp çalıştırın; hangi node'un çalıştığını canvas'ta görürsünüz.", action: { kind: "tab", tab: "test" } },
    { key: "publish", label: "Yayımlayın (admin)", done: opts.versions > 0, hint: "Yayımlanınca Soru-Cevap seçicisinde ve schedule'larda kullanılabilir.", action: { kind: "tab", tab: "versions" } },
  ];
}

// ---------------------------------------------------------------- hazır şablonlar
export interface Template {
  key: string;
  title: string;
  description: string;
  flow: string;
  build: (def: Definition, cat: Catalog) => Definition;
}

const N = (id: string, type: string, label: string, x: number, y: number, config: Record<string, unknown> = {}): WfNode => ({ id, type, label, config, position: { x, y } });
const E = (source: string, target: string, branch: "true" | "false" | null = null): WfEdge => ({ id: `e_${source}_${branch ? `${branch}_` : ""}${target}`, source, target, branch });

function withDefaults(def: Definition, cat: Catalog, mcp: boolean, mem: boolean): Definition {
  const prof = cat.llm_profiles.find((p) => p.enabled);
  const con = cat.connectors.find((c) => c.enabled && c.tools.some((t) => t.executable));
  const st = def.settings;
  return {
    ...def,
    settings: {
      ...st,
      model: { ...st.model, llm_profile_id: st.model.llm_profile_id ?? prof?.id ?? null },
      tools: mcp && con ? { ...st.tools, connector_id: con.id, allowed_tools: con.tools.filter((t) => t.executable).map((t) => t.name) } : st.tools,
      memory: mem ? { ...st.memory, source: "platform" } : st.memory,
    },
  };
}

export const TEMPLATES: Template[] = [
  {
    key: "basic",
    title: "Basit soru-cevap",
    description: "Soruyu doğrudan modele sorar ve yanıtı döndürür. İlk agent için en kolay başlangıç.",
    flow: "Başlangıç → LLM → Sonuç",
    build: (def, cat) => withDefaults(def, cat, false, false),
  },
  {
    key: "telemetry",
    title: "Dynatrace verisiyle yanıt",
    description: "Önce Dynatrace'ten veri çeker, veri geldiyse modele yorumlatır; gelmediyse bunu açıkça söyler.",
    flow: "Başlangıç → MCP Tool → Koşul → LLM → Sonuç (veri yoksa ayrı Sonuç)",
    build: (def, cat) => ({
      ...withDefaults(def, cat, true, false),
      workflow: {
        nodes: [
          N("start", "start", "Başlangıç", 0, 140),
          N("veri", "mcp_tool", "Dynatrace verisi", 260, 140, {
            tool: "execute-dql",
            output_var: "veri",
            args: { dqlQueryString: "fetch logs, from:now()-30m\n| filter loglevel == \"ERROR\"\n| summarize count = count(), by:{dt.entity.service}\n| sort count desc\n| limit 20" },
          }),
          N("veri_var_mi", "condition", "Veri geldi mi?", 520, 140, { field: "veri.status", operator: "eq", value: "OK" }),
          N("yorum", "llm", "Veriyi yorumla", 780, 60, {
            prompt: "Kullanıcının sorusu: {{input}}\n\nDynatrace'ten gelen veri aşağıda. Bu veriye dayanarak kısa ve net yanıt ver; veride olmayan bir şeyi iddia etme.\n\n{{veri.records}}",
            output_var: "answer",
            stream: true,
          }),
          N("sonuc", "result", "Yanıt", 1040, 60, { template: "{{answer}}" }),
          N("veri_yok", "result", "Veri yok", 780, 260, { template: "Dynatrace'ten bu soru için veri alınamadı (durum: {{veri.status}}). Zaman aralığını ya da DQL query'yi kontrol edin." }),
        ],
        edges: [E("start", "veri"), E("veri", "veri_var_mi"), E("veri_var_mi", "yorum", "true"), E("veri_var_mi", "veri_yok", "false"), E("yorum", "sonuc")],
      },
    }),
  },
  {
    key: "memory",
    title: "Geçmiş kayıtlarla yanıt",
    description: "Benzer geçmiş investigation, incident ve runbook kayıtlarını bulur, modele bunlara dayanarak yanıt verdirir.",
    flow: "Başlangıç → Memory Retrieval → LLM → Sonuç",
    build: (def, cat) => ({
      ...withDefaults(def, cat, false, true),
      workflow: {
        nodes: [
          N("start", "start", "Başlangıç", 0, 120),
          N("gecmis", "memory_retrieval", "Geçmiş kayıtlar", 260, 120, { query: "{{input}}", output_var: "gecmis" }),
          N("yanit", "llm", "Yanıtla", 520, 120, {
            prompt: "Soru: {{input}}\n\nİlgili geçmiş kayıtlar:\n{{gecmis}}\n\nGeçmiş kayıtlara dayanarak yanıt ver; hangi kayda dayandığını belirt. Kayıt yoksa bunu söyle.",
            output_var: "answer",
            stream: true,
          }),
          N("sonuc", "result", "Yanıt", 780, 120, { template: "{{answer}}" }),
        ],
        edges: [E("start", "gecmis"), E("gecmis", "yanit"), E("yanit", "sonuc")],
      },
    }),
  },
  {
    key: "report",
    title: "Zamanlanmış risk raporu (JSON)",
    description: "Schedule ile çalışır: veriyi çeker, modelden şemaya uygun JSON risk değerlendirmesi üretir.",
    flow: "Başlangıç → MCP Tool → Structured Output → Sonuç",
    build: (def, cat) => {
      const schema = {
        type: "object",
        properties: { severity: { type: "string", enum: ["low", "medium", "high", "critical"] }, summary: { type: "string" } },
        required: ["severity", "summary"],
        additionalProperties: false,
      };
      const d = withDefaults(def, cat, true, false);
      return {
        ...d,
        settings: { ...d.settings, io: { ...d.settings.io, scheduled_output_format: "json", output_schema: schema, scheduled_input: "Son 30 dakikanın hata durumu" } },
        workflow: {
          nodes: [
            N("start", "start", "Başlangıç", 0, 120),
            N("veri", "mcp_tool", "Hata sayıları", 260, 120, {
              tool: "execute-dql",
              output_var: "veri",
              args: { dqlQueryString: "fetch logs, from:now()-30m\n| filter loglevel == \"ERROR\"\n| summarize count = count(), by:{dt.entity.service}\n| sort count desc\n| limit 20" },
            }),
            N("risk", "structured_output", "Risk değerlendirmesi", 520, 120, {
              prompt: "Aşağıdaki hata sayılarına göre risk seviyesini ve kısa özeti üret.\n\n{{veri.records}}",
              output_var: "risk",
              schema,
            }),
            N("sonuc", "result", "Rapor", 780, 120, { template: "{{risk}}" }),
          ],
          edges: [E("start", "veri"), E("veri", "risk"), E("risk", "sonuc")],
        },
      };
    },
  },
];
