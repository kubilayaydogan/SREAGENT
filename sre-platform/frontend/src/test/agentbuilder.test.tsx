import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import AgentBuilderPage from "@/pages/AgentBuilderPage";
import InvestigatePage from "@/pages/InvestigatePage";
import { AuthProvider } from "@/services/auth";
import { tokenStore } from "@/services/api";
import { availableVars, checklist, connectionProblem, structureHints, TEMPLATES } from "@/features/agentbuilder/guide";
import { SYSTEM_AGENT_ID, type AgentDetail, type AgentSummary, type Catalog, type Definition } from "@/features/agentbuilder/api";

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
}

type Handler = (url: string, init?: RequestInit) => unknown;
function mockFetch(routes: [string, unknown | Handler][]) {
  const f = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const hit = routes.find(([p]) => {
      const [m, path] = p.includes(" ") ? p.split(" ") : ["GET", p];
      return m === method && url.startsWith(path);
    });
    if (!hit) return json({ error: "not_found", message: url, correlation_id: "c" }, 404);
    const v = typeof hit[1] === "function" ? (hit[1] as Handler)(url, init) : hit[1];
    return v instanceof Response ? v : json(v);
  });
  vi.stubGlobal("fetch", f);
  return f;
}

function wrap(ui: ReactNode, path: string, route: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path={route} element={ui} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  tokenStore.clear();
});

const login = () => tokenStore.set("tok", new Date(Date.now() + 3_600_000).toISOString());
const user = (role: string) => ({ id: "u1", username: "u", display_name: "U", role, is_active: true, last_login_at: null, created_at: "2026-11-21T00:00:00Z" });

// Test düzeneği verisi (yalnız bu testte)
const base: Omit<AgentSummary, "id" | "key" | "name" | "kind"> = {
  description: null, status: "published", modes: ["chat"], active_version: 1, published_version_label: "v1",
  model: { model: "claude-sonnet-5-5", profile: "sonnet", provider: "anthropic" }, draft_revision: 3, has_unpublished_changes: false,
  last_run: null, is_default_chat: false, can_copy: true, can_export: true, copy_unavailable_reason: null, created_by: "u", updated_by: "u",
  created_at: null, updated_at: null,
};
const SYSTEM: AgentSummary = {
  ...base, id: SYSTEM_AGENT_ID, key: "sre-agent", name: "SRE Agent", kind: "system", modes: ["chat", "scheduled"], published_version_label: "builtin",
  model: { model: null, profile: "Settings › LLM (platform)", provider: "anthropic" }, is_default_chat: true, can_copy: false, can_export: false,
  copy_unavailable_reason: "Gömülü agent'ın workflow'u platform kodundadır",
};
const CUSTOM: AgentSummary = {
  ...base, id: "a-1", key: "rapor", name: "Rapor Agent", kind: "custom", has_unpublished_changes: true,
  last_run: { run_id: "agent-1", status: "completed", mode: "chat", started_at: "2026-11-26T10:00:00Z", version_label: "v1", error: null },
};

const DEF: Definition = {
  format: 1,
  settings: {
    model: { llm_profile_id: "p-son", model: null, max_tokens: null, timeout_s: null, effort: null, temperature: null },
    instructions: { system_prompt: "Kısa yanıt ver.", task_instructions: "", variables: [] },
    tools: { connector_id: null, allowed_tools: [], call_timeout_s: 60 },
    memory: { source: "none", scope: [], top_k: 5, history_turns: 6, context_budget_tokens: 60000 },
    policies: { optional: {} },
    io: { input_schema: null, output_schema: null, streaming: true, scheduled_input: "", scheduled_output_format: "markdown" },
    limits: { timeout_s: 180, max_steps: 30, max_tool_calls: 10, max_llm_calls: 8, token_budget: 300000, retries: 2 },
  },
  workflow: {
    nodes: [
      { id: "start", type: "start", label: "Başlangıç", config: {}, position: { x: 0, y: 0 } },
      { id: "llm_1", type: "llm", label: "Yanıt", config: { prompt: "{{input}}", output_var: "answer", llm_profile_id: "p-opus" }, position: { x: 260, y: 0 } },
      { id: "result", type: "result", label: "Sonuç", config: { template: "{{answer}}" }, position: { x: 520, y: 0 } },
    ],
    edges: [
      { id: "e1", source: "start", target: "llm_1", branch: null },
      { id: "e2", source: "llm_1", target: "result", branch: null },
    ],
  },
  layout: { viewport: { x: 0, y: 0, zoom: 1 } },
};
const DETAIL: AgentDetail = {
  ...CUSTOM,
  versions: [{ version: 1, checksum: "abc123", note: "ilk", published_by: "admin", published_at: "2026-11-26T09:00:00Z", draft_revision: 2, active: true }],
  draft: DEF,
  source: {},
  issues: [{ code: "temperature_unsupported", message: "claude-opus-5-5 temperature parametresini desteklemiyor", node_id: "llm_1", field: "temperature", severity: "error" }],
  dependencies: [],
};
const CAT: Catalog = {
  nodes: ["start", "llm", "mcp_tool", "condition", "memory_retrieval", "structured_output", "result"].map((t) => ({ type: t, label: t, status: "available" as const })),
  unavailable_nodes: [{ type: "human_approval", label: "İnsan onayı", status: "unavailable", reason: "onaylı yürütme yolu yok" }],
  operators: ["eq", "contains"],
  efforts: ["low", "medium", "high"],
  llm_profiles: [
    { id: "p-son", name: "sonnet", provider: "anthropic", model: "claude-sonnet-5-5", enabled: true, max_tokens: 4096, timeout_s: 60, effort: null, model_max_tokens: 64000, capabilities: null, supports_temperature: false },
    { id: "p-opus", name: "opus", provider: "anthropic", model: "claude-opus-5-5", enabled: true, max_tokens: 4096, timeout_s: 60, effort: null, model_max_tokens: 64000, capabilities: null, supports_temperature: false },
  ],
  allowed_models: [],
  connectors: [],
  middleware: [{ name: "redaction", title: "Redaction", kind: "x", mandatory: true, description: "", platform_enabled: true }],
  memory_scopes: ["investigations"],
  temperature_note: "Claude 5 ailesi temperature kabul etmez",
};

describe("Agent Builder", () => {
  it("liste: gömülü agent sistem agent'ı; model, mod, sürüm ve son çalıştırma", async () => {
    login();
    mockFetch([
      ["/api/auth/me", user("sre")],
      ["/api/agents?", { items: [SYSTEM, CUSTOM], default_chat_agent_id: SYSTEM_AGENT_ID }],
    ]);
    wrap(<AgentBuilderPage />, "/agent-builder", "/agent-builder/:id?");
    const row = (await screen.findByRole("link", { name: "Rapor Agent" })).closest("tr")!;
    expect(within(row).getByText("claude-sonnet-5-5")).toBeInTheDocument();
    expect(within(row).getByText("yayımlanmamış değişiklik")).toBeInTheDocument();
    expect(within(row).getByText("Tamamlandı")).toBeInTheDocument();
    const sys = screen.getByRole("link", { name: "SRE Agent" }).closest("tr")!;
    expect(within(sys).getByText("Sistem")).toBeInTheDocument();
    expect(within(sys).getByRole("button", { name: "SRE Agent kopyala" })).toBeDisabled();
    expect(within(sys).getByRole("button", { name: "SRE Agent dışa aktar" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Agent İçe Aktar/ })).toBeInTheDocument();
    // SRE varsayılan agent'ı değiştiremez, arşivleyemez (admin)
    expect(screen.getByLabelText("Soru-Cevap varsayılan agent'ı")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Rapor Agent arşivle" })).not.toBeInTheDocument();
  });

  it("editör: palet (kullanılamayan node nedeniyle), node'da hata ve çözülmüş model; kaydet tanımı gönderir", async () => {
    login();
    const f = mockFetch([
      ["/api/auth/me", user("sre")],
      ["/api/agents/catalog", CAT],
      ["PUT /api/agents/a-1/draft", { draft_revision: 4, issues: [] }],
      ["/api/agents/a-1", DETAIL],
    ]);
    wrap(<AgentBuilderPage />, "/agent-builder/a-1", "/agent-builder/:id?");
    const node = await screen.findByTestId("node-llm_1");
    expect(within(node).getByText(/temperature parametresini desteklemiyor/)).toBeInTheDocument();
    expect(within(node).getByText("claude-opus-5-5")).toBeInTheDocument(); // node ayarı agent varsayılanını geçersiz kılar
    expect(within(node).getByText(/· node/)).toBeInTheDocument();
    expect(screen.getByText("onaylı yürütme yolu yok")).toBeInTheDocument();
    // palet: tıklayınca Koşul node'u eklenir → kaydet
    fireEvent.click(within(screen.getByLabelText("node paleti")).getByText("condition"));
    expect(await screen.findByTestId("node-condition_1")).toBeInTheDocument();
    const save = screen.getByRole("button", { name: /Kaydet/ });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await waitFor(() => expect(f.mock.calls.some(([, i]) => (i as RequestInit | undefined)?.method === "PUT")).toBe(true));
    const put = f.mock.calls.find(([, i]) => (i as RequestInit | undefined)?.method === "PUT")!;
    const body = JSON.parse(String((put[1] as RequestInit).body));
    expect(body.revision).toBe(3);
    expect(body.definition.workflow.nodes.map((n: { id: string }) => n.id)).toEqual(["start", "llm_1", "result", "condition_1"]);
    expect(JSON.stringify(body)).not.toMatch(/api_key|secret/i);
    // SRE yayımlayamaz (admin)
    expect(screen.queryByRole("button", { name: /Yayımla/ })).not.toBeInTheDocument();
  });

  it("içe aktarma: önizleme, eşleştirme ve yeni agent olarak kayıt", async () => {
    login();
    const preview = {
      agent: { name: "Rapor Agent", description: null, modes: ["chat"] }, agent_version: 2, schema_version: 1, node_count: 3, edge_count: 2,
      dependencies: { llm_profiles: [{ ref: "llm:1", name: "opus", provider: "anthropic", model: "claude-opus-5-5", used_by: ["settings.model"] }], connectors: [], middleware: [], memory_source: "none" },
      mapping: { "llm:1": null }, name_conflict: true, structural_issues: [],
      mapping_issues: [{ code: "mapping_missing", message: "opus eşleştirilmedi", node_id: null, field: "mapping.llm:1", severity: "warning" }],
      candidates: { llm_profiles: [{ id: "p-opus", name: "opus", model: "claude-opus-5-5", provider: "anthropic", enabled: true }], connectors: [] },
    };
    const f = mockFetch([
      ["/api/auth/me", user("sre")],
      ["/api/agents?", { items: [SYSTEM, CUSTOM], default_chat_agent_id: SYSTEM_AGENT_ID }],
      // backend gibi: gönderilen eşleştirme öneriyle birleştirilir
      ["POST /api/agents/import/preview", (_u: string, i?: RequestInit) => ({ ...preview, mapping: { ...preview.mapping, ...JSON.parse(String(i?.body)).mapping } })],
      ["POST /api/agents/import", { agent: { ...CUSTOM, id: "a-2", status: "draft" }, issues: [] }],
      ["/api/agents/a-2", { ...DETAIL, id: "a-2" }],
      ["/api/agents/catalog", CAT],
    ]);
    wrap(<AgentBuilderPage />, "/agent-builder", "/agent-builder/:id?");
    await userEvent.click(await screen.findByRole("button", { name: /Agent İçe Aktar/ }));
    const file = new File([JSON.stringify({ schema_version: 1, kind: "airlab.agent", agent: { name: "Rapor Agent", modes: ["chat"] }, definition: {} })], "a.agent.json", { type: "application/json" });
    await userEvent.upload(screen.getByLabelText(/Paket dosyası/), file);
    expect(await screen.findByText("v2")).toBeInTheDocument();
    expect(screen.getByText("opus eşleştirilmedi")).toBeInTheDocument();
    expect(screen.getByLabelText("Ad")).toHaveValue("Rapor Agent (içe aktarılan)"); // ad çakışması → yeniden adlandırma
    await userEvent.selectOptions(screen.getByLabelText("llm:1 eşleştirmesi"), "p-opus");
    await userEvent.click(screen.getByRole("button", { name: "Yeni agent olarak aktar" }));
    await waitFor(() => expect(f.mock.calls.some(([u, i]) => String(u) === "/api/agents/import" && (i as RequestInit)?.method === "POST")).toBe(true));
    const call = f.mock.calls.find(([u]) => String(u) === "/api/agents/import")!;
    const body = JSON.parse(String((call[1] as RequestInit).body));
    expect(body).toMatchObject({ target: "new", name: "Rapor Agent (içe aktarılan)", mapping: { "llm:1": "p-opus" } });
  });
});

describe("Agent Builder rehberi", () => {
  it("bağlantı kuralları neden bağlanamadığını açıklar", () => {
    expect(connectionProblem(DEF, "llm_1", "start", null)).toMatch(/Başlangıç'a giriş/);
    expect(connectionProblem(DEF, "result", "llm_1", null)).toMatch(/Sonuç son adımdır/);
    expect(connectionProblem(DEF, "result", "start", null)).toMatch(/Başlangıç'a giriş/);
    expect(connectionProblem(DEF, "llm_1", "llm_1", null)).toMatch(/kendisine/);
    // llm_1 → start yerine döngü: result zaten llm_1'den sonra; llm_1'e geri bağlamak döngü
    const loop = { ...DEF, workflow: { ...DEF.workflow, nodes: [...DEF.workflow.nodes, { id: "llm_2", type: "llm", label: "x", config: {}, position: { x: 0, y: 0 } }], edges: [...DEF.workflow.edges, { id: "e3", source: "llm_1", target: "llm_2", branch: null }] } };
    expect(connectionProblem(loop, "llm_2", "llm_1", null)).toMatch(/döngü/);
    expect(connectionProblem(DEF, "start", "result", null)).toBeNull();
  });

  it("değişkenler yalnız bağlı önceki adımlardan; eksik bağlantılar ipucu üretir", () => {
    const names = availableVars(DEF, "result").map((v) => v.name);
    expect(names).toEqual(expect.arrayContaining(["input", "now", "answer"]));
    expect(availableVars(DEF, "llm_1").map((v) => v.name)).not.toContain("answer");
    expect(structureHints(DEF)).toEqual({});
    const loose = { ...DEF, workflow: { ...DEF.workflow, edges: [DEF.workflow.edges[0]] } };
    const h = structureHints(loose);
    expect(h.llm_1[0].kind).toBe("no_output");
    expect(h.result[0].kind).toBe("no_input");
  });

  it("kontrol listesi ve şablonlar", () => {
    const steps = checklist(DEF, { dirty: false, versions: 0, tested: false, errors: 0, modes: ["chat"] });
    expect(steps.find((x) => x.key === "model")!.done).toBe(true);
    expect(steps.find((x) => x.key === "test")!.done).toBe(false);
    expect(steps.find((x) => x.key === "publish")!.done).toBe(false);
    const cat = { ...CAT, connectors: [{ id: "c-1", name: "dt", enabled: true, tools: [{ name: "execute-dql", executable: true }] }] } as unknown as typeof CAT;
    for (const t of TEMPLATES) {
      const d = t.build(DEF, cat);
      expect(structureHints(d)).toEqual({}); // her şablon tam bağlı
      expect(d.workflow.nodes.filter((n) => n.type === "start")).toHaveLength(1);
    }
    const tel = TEMPLATES.find((t) => t.key === "telemetry")!.build(DEF, cat);
    expect(tel.settings.tools.connector_id).toBe("c-1");
    expect(tel.settings.tools.allowed_tools).toEqual(["execute-dql"]);
  });

  it("node rehberi: sonraki adım ekle araya girer ve bağlar; Rehber sekmesi adımları gösterir", async () => {
    login();
    mockFetch([
      ["/api/auth/me", user("sre")],
      ["/api/agents/catalog", CAT],
      ["/api/agents/a-1", DETAIL],
    ]);
    wrap(<AgentBuilderPage />, "/agent-builder/a-1", "/agent-builder/:id?");
    fireEvent.click(await screen.findByTestId("node-llm_1"));
    const card = await screen.findByRole("note", { name: "node rehberi" });
    expect(within(card).getByText(/Modele bir prompt gönderir/)).toBeInTheDocument();
    // değişken çipi prompt'a ekler
    fireEvent.click(within(screen.getByLabelText("Prompt").parentElement!).getByRole("button", { name: "{{now}}" }));
    expect((screen.getByLabelText("Prompt") as HTMLTextAreaElement).value).toBe("{{input}} {{now}}");
    fireEvent.click(within(card).getByRole("button", { name: /Koşul/ }));
    expect(await screen.findByTestId("node-condition_1")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /Rehber/ }));
    const steps = await screen.findByRole("region", { name: "kurulum adımları" });
    expect(within(steps).getByText("Tüm node'ları bağlayın")).toBeInTheDocument();
    expect(within(steps).getByText(/condition_1: .*çıkış bağlı değil/)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "agent nasıl çalışır" })).toBeInTheDocument();
  });
});

describe("Soru-Cevap agent seçimi", () => {
  Element.prototype.scrollIntoView ??= vi.fn();
  it("gönder düğmesinin yanında yalnız çalıştırılabilir agent'lar; seçilen agent isteğe eklenir", async () => {
    login();
    const f = mockFetch([
      ["/api/auth/me", user("sre")],
      ["/api/agents?", { items: [SYSTEM, CUSTOM], default_chat_agent_id: SYSTEM_AGENT_ID }],
      ["/api/conversations", { items: [], total: 0 }],
      ["/api/applications", []],
      ["POST /api/agent/chat", { run_id: "r1", stream_url: "/api/agent/stream/r1", conversation_id: "c1" }],
    ]);
    wrap(<InvestigatePage />, "/investigate", "/investigate/:conversationId?");
    const sel = (await screen.findByLabelText("agent seçimi")) as HTMLSelectElement;
    expect([...sel.options].map((o) => o.text)).toEqual(["SRE Agent (varsayılan)", "Rapor Agent"]);
    expect(f.mock.calls.some(([u]) => String(u).includes("mode=chat") && String(u).includes("runnable=true"))).toBe(true);
    await userEvent.selectOptions(sel, "a-1");
    await userEvent.type(screen.getByLabelText("soru"), "durum nedir{Enter}");
    await waitFor(() => expect(f.mock.calls.some(([u]) => String(u) === "/api/agent/chat")).toBe(true));
    const call = f.mock.calls.find(([u]) => String(u) === "/api/agent/chat")!;
    expect(JSON.parse(String((call[1] as RequestInit).body))).toMatchObject({ message: "durum nedir", agent_id: "a-1", conversation_id: null });
  });
});
