import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppLayout } from "@/layouts/AppLayout";
import InvestigationDetailPage from "@/pages/InvestigationDetailPage";
import { AuthProvider } from "@/services/auth";
import { tokenStore } from "@/services/api";
import { llmRunText, sourceLabel } from "@/lib/runconfig";
import { ConceptHelp, CONCEPTS } from "@/components/ConceptHelp";

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
}

function mockFetch(routes: Record<string, unknown | ((init?: RequestInit) => Response)>) {
  const f = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const key = Object.keys(routes).find((k) => url.startsWith(k));
    if (!key) return json({ error: "not_found", message: `mock yok: ${url}`, correlation_id: "c" }, 404);
    const v = routes[key];
    return typeof v === "function" ? (v as (i?: RequestInit) => Response)(init) : json(v);
  });
  vi.stubGlobal("fetch", f);
  return f;
}

afterEach(() => {
  vi.unstubAllGlobals();
  tokenStore.clear();
});

const SRE = { id: "u1", username: "nobetci", display_name: "Nöbetçi", role: "sre", is_active: true, last_login_at: null, created_at: "2026-11-21T00:00:00Z" };
const OPUS = "claude-opus-5-5";
const SONNET = "claude-sonnet-5-5";

const snapshot = (llm: Record<string, unknown>) => ({
  hash: "abc123", trigger: "manual", revisions: { agent: 2, llm: 3 }, agent: {}, tracing: { langsmith: false, sampling_rate: 1 },
  connector: { connector: "dynatrace", available: true, query_timeout_s: 30, query_retries: 2 },
  llm: { enabled: true, chat: null, rca: null, model_override: null, ...llm },
});

const inv = {
  id: "i1", run_id: "man-1", trigger: "manual", title: "Manuel analiz", description: null, status: "COMPLETED",
  severity: "high", risk_score: "72.5", data_coverage: "0.8", confidence: "medium", primary_mechanism: "row_lock",
  source_apps: [], affected_apps: [], application_id: null, conversation_id: null, user_id: null,
  window_start: "2026-11-25T10:00:00Z", window_end: "2026-11-25T10:15:00Z", baseline_start: null, baseline_end: null,
  started_at: null, completed_at: null, duration_ms: 1000, root_cause_hypothesis: null, error: null,
  created_at: "2026-11-25T10:16:00Z", explanation: null, explanation_source: null, report: null,
};

function renderDetail(detail: Record<string, unknown>, path = "/investigations/i1", extra: Record<string, unknown> = {}) {
  tokenStore.set("tok", new Date(Date.now() + 3_600_000).toISOString());
  mockFetch({
    ...extra,
    "/api/auth/me": SRE,
    "/api/investigations/i1/trace": { url: null },
    "/api/investigations/i1/similar": [],
    "/api/investigations/i1/feedback": [],
    "/api/investigations/i1/recommendations": { known_issues: [], runbooks: [] },
    "/api/rca": { items: [], total: 0, page: 1, size: 20, pages: 0 },
    "/api/investigations/i1": detail,
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/investigations/:id" element={<InvestigationDetailPage />} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

describe("model seçimi metinleri", () => {
  it("kaynak ve neden etiketleri", () => {
    expect(sourceLabel({ source: "profile", profile: "rca-haiku" })).toBe("LLM profili “rca-haiku”");
    expect(sourceLabel(undefined)).toBe("kaydedilmedi");
    expect(llmRunText({ stage: "explain", used: false, model: null, reason: "scheduled_llm_off" }, { model: OPUS, source: "env" })).toEqual({
      model: null,
      detail: "zamanlanmış koşularda LLM kapalı (Settings › Agent)",
    });
    expect(llmRunText({ stage: "explain", used: true, model: SONNET, source: "override" }, { model: SONNET, source: "override" })).toEqual({
      model: SONNET,
      detail: "üst bardaki model seçimi",
    });
    expect(llmRunText(undefined, undefined).model).toBeNull();
  });
});

describe("Bu çalıştırmada kullanılanlar", () => {
  it("model ve seçim kaynağını kayıttan gösterir", async () => {
    renderDetail({
      ...inv,
      explanation_source: `Claude (${SONNET})`,
      config_snapshot: snapshot({ effective: { chat: { model: OPUS, source: "env" }, rca: { model: SONNET, source: "override" } }, run: { stage: "explain", used: true, model: SONNET, source: "override" } }),
    });
    const card = (await screen.findByText("Bu çalıştırmada kullanılanlar")).closest("div")!.parentElement!;
    expect(within(card).getByText(SONNET)).toBeInTheDocument();
    expect(within(card).getByText(/Seçim kaynağı: üst bardaki model seçimi/)).toBeInTheDocument();
    expect(within(card).getByText("Gömülü SRE Agent")).toBeInTheDocument();
    expect(within(card).getByText("dynatrace")).toBeInTheDocument();
  });

  it("LLM kullanılmadıysa nedenini yazar; model adı göstermez", async () => {
    renderDetail({
      ...inv,
      trigger: "scheduled",
      explanation_source: "deterministik",
      config_snapshot: snapshot({ effective: { chat: { model: OPUS, source: "env" }, rca: { model: OPUS, source: "env" } }, run: { stage: "explain", used: false, model: null, reason: "scheduled_llm_off" } }),
    });
    expect(await screen.findByText("LLM kullanılmadı")).toBeInTheDocument();
    expect(screen.getByText(/zamanlanmış koşularda LLM kapalı/)).toBeInTheDocument();
  });

  it("eski kayıtta (seçim kaynağı yok) 'kaydedilmedi' yazar; snapshot hiç yoksa bunu söyler", async () => {
    renderDetail({ ...inv, config_snapshot: snapshot({}) });
    expect(await screen.findByText("kaydedilmedi")).toBeInTheDocument();
  });
});

describe("Evidence → MCP çağrısı", () => {
  it("evidence ayrıntısı, veriyi getiren MCP çağrısına bağlanır", async () => {
    renderDetail({ ...inv }, "/investigations/i1?tab=evidence&ev=ev-1", {
      "/api/investigations/i1/evidence": [
        { id: "e1", evidence_key: "ev-1", evidence_type: "LOG", source: "dynatrace", signal: "edges", data_status: "OK", application_id: null, entity_id: null,
          trace_id: null, span_id: null, metric_name: null, query_reference: null, dynatrace_query_id: "q-1", timestamp: null, content: "edges: 49 kayıt", metadata: {} },
        { id: "e2", evidence_key: "ev-2", evidence_type: "DERIVED", source: "agent", signal: null, data_status: "OK", application_id: null, entity_id: null,
          trace_id: null, span_id: null, metric_name: null, query_reference: null, dynatrace_query_id: null, timestamp: null, content: "türetilmiş", metadata: {} },
      ],
      "/api/investigations/i1/tools": [
        { id: "call-1", step: "logs", tool_name: "execute-dql", signal: "edges", tool_arguments: {}, execution_status: "SUCCEEDED", result_status: "OK", record_count: 49,
          dynatrace_query_id: "q-1", evidence_key: "ev-1", started_at: null, duration_ms: 120, error_message: null, created_at: "2026-11-25T10:01:00Z" },
      ],
    });
    const link = await screen.findByRole("link", { name: "Bu veriyi getiren MCP çağrısını aç" });
    expect(link).toHaveAttribute("href", "/mcp-calls?run_id=man-1&call=call-1");
    const u = userEvent.setup();
    const row2 = screen.getByText("ev-2").closest("tr")!;
    await u.click(within(row2).getByRole("button", { name: "ayrıntı" }));
    expect(await screen.findByText(/MCP çağrı kaydı bulunamadı/)).toBeInTheDocument();
  });
});

describe("Üst bar model seçici: seçim yok durumu", () => {
  const info = { environment: "lab", llm_enabled: true, llm_model: OPUS, mcp_configured: true, dynatrace_environment: null, langsmith_tracing: false, limits: {}, active_runs: 0 };
  const options = [{ id: OPUS, label: "Claude Opus 5.5" }, { id: SONNET, label: "Claude Sonnet 5.5" }];
  const state = (selected: string | null) => ({
    enabled: true, model: selected ?? OPUS, rca_model: selected ?? OPUS, changed_by: null, changed_at: null, options, selected,
    effective: { chat: selected ? { model: selected, source: "override" } : { model: OPUS, source: "profile", profile: "varsayilan" }, rca: { model: OPUS, source: "env" } },
  });

  it("seçim yokken 'Otomatik' seçeneği kaynağıyla görünür; seçim kaldırılabilir (PUT null)", async () => {
    tokenStore.set("tok", new Date(Date.now() + 3_600_000).toISOString());
    const puts: (string | null)[] = [];
    let cur: string | null = null;
    mockFetch({
      "/api/auth/me": { ...SRE, role: "admin" },
      "/api/system/info": info,
      "/api/system/llm-model": (init?: RequestInit) => {
        if (init?.method === "PUT") {
          cur = JSON.parse(String(init.body)).model as string | null;
          puts.push(cur);
        }
        return json(state(cur));
      },
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <AuthProvider>
          <MemoryRouter>
            <AppLayout />
          </MemoryRouter>
        </AuthProvider>
      </QueryClientProvider>,
    );
    const sel = (await screen.findAllByLabelText("Claude modeli"))[0] as HTMLSelectElement;
    expect(sel.value).toBe("");
    expect(within(sel).getByRole("option", { name: "Otomatik: Claude Opus 5.5 (LLM profili)" })).toBeInTheDocument();
    const u = userEvent.setup();
    await u.selectOptions(sel, SONNET);
    await vi.waitFor(() => expect(puts).toEqual([SONNET]));
    await vi.waitFor(() => expect(sel.value).toBe(SONNET));
    await u.selectOptions(sel, "");
    await vi.waitFor(() => expect(puts).toEqual([SONNET, null]));
  });
});

describe("Hata ve eksik veri mesajları", () => {
  const report = (evidence: { id: string; signal: string; status: string; records: number | null }[]) => ({ evidence, data_gaps: [], status: "partial" });

  it("kısmi veride yanıt vermeyen query'leri adıyla yazar ve 0 sayılmadığını belirtir", async () => {
    renderDetail({
      ...inv,
      status: "PARTIAL",
      report: report([
        { id: "ev-1", signal: "http", status: "OK", records: 12 },
        { id: "ev-2", signal: "jvm_gc", status: "TIMEOUT", records: null },
        { id: "ev-3", signal: "edges", status: "EMPTY", records: 0 },
      ]),
    });
    expect(await screen.findByText(/1 Dynatrace query'si yanıt vermedi \(jvm_gc\)/)).toBeInTheDocument();
    expect(screen.getByText(/Okunamayan veri 0 sayılmaz/)).toBeInTheDocument();
  });

  it("tamamlanan çalıştırmadaki hata uyarıdır; teknik metin açılabilir bölümdedir", async () => {
    renderDetail({ ...inv, status: "COMPLETED", error: "Claude erişilemedi: APIConnectionError" });
    expect(await screen.findByText(/Çalıştırma tamamlandı, ancak bir adım beklendiği gibi çalışmadı/)).toBeInTheDocument();
    expect(screen.queryByText(/Çalıştırma tamamlanamadı/)).not.toBeInTheDocument();
    expect(screen.getByText("Claude erişilemedi: APIConnectionError").closest("details")).not.toHaveAttribute("open");
  });

  it("başarısız çalıştırmada sonuç üretilmediğini söyler", async () => {
    renderDetail({ ...inv, status: "FAILED", risk_score: null, error: "RuntimeError: x" });
    expect(await screen.findByText(/Çalıştırma tamamlanamadı; sonuç üretilmedi/)).toBeInTheDocument();
  });
});

describe("Kavram açıklamaları", () => {
  it("her agent sayfası için sade açıklama, terimler ve gerçek bir bağlantı vardır", () => {
    for (const [k, c] of Object.entries(CONCEPTS)) {
      expect(c.what.length, k).toBeGreaterThan(40);
      expect(c.terms.length, k).toBeGreaterThanOrEqual(3);
      expect(c.links.every((l) => l.to.startsWith("/")), k).toBe(true);
    }
  });

  it("varsayılan olarak kapalıdır; klavye ile açılabilen <details> öğesidir", async () => {
    render(
      <MemoryRouter>
        <ConceptHelp k="mcp" />
      </MemoryRouter>,
    );
    const summary = screen.getByText("Bu sayfa neyi gösterir?");
    const details = summary.closest("details")!;
    expect(details).not.toHaveAttribute("open");
    await userEvent.setup().click(summary);
    expect(details).toHaveAttribute("open");
    expect(screen.getByText(/TIMEOUT \/ ERROR: yanıt alınamadı; bu veri 0 sayılmaz/)).toBeInTheDocument();
  });
});
