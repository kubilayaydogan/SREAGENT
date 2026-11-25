import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ActivityTimeline, toolsFromRecords } from "@/components/ActivityTimeline";
import { AppLayout } from "@/layouts/AppLayout";
import DashboardPage from "@/pages/DashboardPage";
import InvestigationDetailPage from "@/pages/InvestigationDetailPage";
import LlmObservabilityPage, { fillSeries } from "@/pages/LlmObservabilityPage";
import LoginPage from "@/pages/LoginPage";
import { AuthProvider } from "@/services/auth";
import { tokenStore } from "@/services/api";
import type { DashboardSummary, ToolExecution } from "@/types/api";

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

function wrap(ui: ReactNode, path = "/") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/" element={ui} />
            <Route path="/login" element={ui} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

const USER = { id: "u1", username: "nobetci", display_name: "Nöbetçi", role: "sre", is_active: true, last_login_at: null, created_at: "2026-11-21T00:00:00Z" };

const unavailable: DashboardSummary = {
  range: "1h",
  start: "2026-11-21T09:00:00Z",
  end: "2026-11-21T10:00:00Z",
  available: false,
  error: "McpUnavailable: outage",
  sources: null,
  interval_minutes: 5,
  monitored_applications: 4,
  status_counts: { unknown: 4 },
  active_incidents: 1,
  open_investigations: 0,
  overall: {},
  db: null,
  db_series: [],
  applications: {},
  recent_investigations: [],
  recent_incidents: [],
  incident_counts: { OPEN: 1 },
  investigation_stats_7d: { by_status: {}, open: 0, total: 0 },
};

afterEach(() => vi.unstubAllGlobals());

describe("DashboardPage", () => {
  it("Dynatrace verisi yoksa bunu açıkça söyler ve metric'leri '—' gösterir (sahte değer yok)", async () => {
    tokenStore.set("tok", new Date(Date.now() + 3_600_000).toISOString());
    mockFetch({ "/api/auth/me": USER, "/api/dashboard/summary": unavailable, "/api/applications": [] });
    wrap(<DashboardPage />);
    expect(await screen.findByText(/Dynatrace verisi alınamadı\./)).toBeInTheDocument();
    expect(screen.getByText("McpUnavailable: outage")).toBeInTheDocument();
    for (const label of ["P95 (en kötü uygulama)", "P99 (en kötü uygulama)", "Throughput", "Error rate", "Ortalama yanıt", "PostgreSQL connection kullanımı"]) {
      expect(within(screen.getByRole("group", { name: label })).getByText("—")).toBeInTheDocument();
    }
    expect(screen.queryByText("0 ms")).not.toBeInTheDocument();
    expect(screen.getAllByText(/Dynatrace verisi alınamadı/).length).toBeGreaterThan(1); // grafik boş durumu da
  });
});

describe("LoginPage", () => {
  it("hatalı girişte sunucu mesajını gösterir, başarılı girişte token saklar", async () => {
    let n = 0;
    mockFetch({
      "/api/auth/login": () =>
        ++n === 1
          ? json({ error: "unauthorized", message: "kullanıcı adı veya parola hatalı", correlation_id: "c1" }, 401)
          : json({ access_token: "jwt", token_type: "bearer", expires_at: new Date(Date.now() + 3_600_000).toISOString(), user: USER }),
    });
    wrap(<LoginPage />, "/login");
    const u = userEvent.setup();
    await u.type(screen.getByLabelText("Kullanıcı adı"), "nobetci");
    await u.type(screen.getByLabelText("Parola"), "yanlis");
    await u.click(screen.getByRole("button", { name: "Giriş yap" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("kullanıcı adı veya parola hatalı");
    expect(tokenStore.get()).toBeNull();
    await u.type(screen.getByLabelText("Parola"), "dogru-parola-123");
    await u.click(screen.getByRole("button", { name: "Giriş yap" }));
    await vi.waitFor(() => expect(tokenStore.get()).toBe("jwt"));
  });
});

describe("ActivityTimeline", () => {
  it("kayıtlı araç çalıştırmalarından adım durumlarını türetir ve hatayı gösterir", async () => {
    const rows: ToolExecution[] = [
      { id: "1", step: "app_metrics", tool_name: "execute-dql", signal: "http", tool_arguments: {}, execution_status: "SUCCEEDED", result_status: "OK", record_count: 5, dynatrace_query_id: "q1", evidence_key: "E1", started_at: null, duration_ms: 200, error_message: null, created_at: "" },
      { id: "2", step: "db_metrics", tool_name: "execute-dql", signal: "db_conn", tool_arguments: {}, execution_status: "FAILED", result_status: "ERROR", record_count: null, dynatrace_query_id: null, evidence_key: null, started_at: null, duration_ms: 50, error_message: "MCP timeout", created_at: "" },
    ];
    const { tools, steps } = toolsFromRecords(rows);
    expect(steps).toMatchObject({ app_metrics: "done", db_metrics: "failed" });
    render(<ActivityTimeline steps={steps} tools={tools} />);
    expect(screen.getByLabelText("hata")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: /PostgreSQL metric'leri/ }));
    expect(screen.getByText("MCP timeout")).toBeInTheDocument();
  });
});

describe("LLM model seçici", () => {
  afterEach(() => { vi.unstubAllGlobals(); tokenStore.clear(); });
  const info = { environment: "lab", llm_enabled: true, llm_model: "claude-opus-5-5", mcp_configured: true,
    dynatrace_environment: null, langsmith_tracing: false, limits: {}, active_runs: 0 };
  const state = (model: string) => ({ enabled: true, model, rca_model: model, changed_by: null, changed_at: null,
    options: [{ id: "claude-opus-5-5", label: "Claude Opus 5.5" }, { id: "claude-sonnet-5-5", label: "Claude Sonnet 5.5" }] });

  it("yönetici Opus ↔ Sonnet seçer ve seçim API'ye PUT edilir", async () => {
    tokenStore.set("tok", new Date(Date.now() + 3_600_000).toISOString());
    const puts: string[] = [];
    mockFetch({
      "/api/auth/me": { ...USER, role: "admin" },
      "/api/system/info": info,
      "/api/system/llm-model": (init?: RequestInit) => {
        if (init?.method === "PUT") {
          const m = JSON.parse(String(init.body)).model as string;
          puts.push(m);
          return json(state(m));
        }
        return json(state("claude-opus-5-5"));
      },
    });
    wrap(<AppLayout />);
    const sel = (await screen.findAllByLabelText("Claude modeli"))[0] as HTMLSelectElement;
    expect(sel.value).toBe("claude-opus-5-5");
    await userEvent.setup().selectOptions(sel, "claude-sonnet-5-5");
    await vi.waitFor(() => expect(puts).toEqual(["claude-sonnet-5-5"]));
  });

  it("yönetici olmayan kullanıcı yalnız modeli görür", async () => {
    tokenStore.set("tok", new Date(Date.now() + 3_600_000).toISOString());
    mockFetch({ "/api/auth/me": USER, "/api/system/info": info, "/api/system/llm-model": state("claude-sonnet-5-5") });
    wrap(<AppLayout />);
    expect((await screen.findAllByText("LLM Claude Sonnet 5.5")).length).toBeGreaterThan(0);
    expect(screen.queryByLabelText("Claude modeli")).toBeNull();
  });
});

describe("InvestigationDetailPage", () => {
  const inv = {
    id: "i1", run_id: "rep-1", trigger: "SCHEDULED", title: "Periyodik analiz", description: null, status: "COMPLETED",
    severity: "high", risk_score: "72.5", data_coverage: "0.8", confidence: "medium", primary_mechanism: "row_lock",
    source_apps: [], affected_apps: [], application_id: null, conversation_id: null, user_id: null,
    window_start: "2026-11-25T10:00:00Z", window_end: "2026-11-25T10:15:00Z", baseline_start: null, baseline_end: null,
    started_at: null, completed_at: null, duration_ms: 1000, root_cause_hypothesis: null, error: null,
    created_at: "2026-11-25T10:16:00Z", explanation: null, explanation_source: null, report: null,
  };
  function renderAt(trace: { url: string | null }) {
    tokenStore.set("tok", new Date(Date.now() + 3_600_000).toISOString());
    mockFetch({
      "/api/auth/me": USER,
      "/api/investigations/i1/trace": trace,
      "/api/investigations/i1/similar": [],
      "/api/investigations/i1/feedback": [],
      "/api/investigations/i1/recommendations": { known_issues: [], runbooks: [] },
      "/api/rca": { items: [], total: 0, page: 1, size: 20, pages: 0 },
      "/api/investigations/i1": inv,
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <AuthProvider>
          <MemoryRouter initialEntries={["/investigations/i1"]}>
            <Routes>
              <Route path="/investigations/:id" element={<InvestigationDetailPage />} />
            </Routes>
          </MemoryRouter>
        </AuthProvider>
      </QueryClientProvider>,
    );
  }

  it("trace varsa LangSmith bağlantısını yeni sekmede açar", async () => {
    renderAt({ url: "https://smith.test/o/t/projects/p/p1/r/i1?poll=true" });
    const a = await screen.findByRole("link", { name: /LangSmith'te aç/ });
    expect(a).toHaveAttribute("href", "https://smith.test/o/t/projects/p/p1/r/i1?poll=true");
    expect(a).toHaveAttribute("target", "_blank");
  });

  it("trace yoksa bağlantı gösterilmez", async () => {
    renderAt({ url: null });
    expect(await screen.findByText("Periyodik analiz")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /LangSmith'te aç/ })).not.toBeInTheDocument();
  });
});

describe("LlmObservabilityPage", () => {
  const agg = { calls: 3, errors: 1, input_tokens: 11000, output_tokens: 510, cost_usd: 0.13, p50_ms: 8000, p95_ms: 23300 };
  const obs = {
    range: "24h", start: "2026-11-24T12:00:00Z", end: "2026-11-25T12:00:00Z", bucket: "1 hour",
    sync: { last_ok_at: new Date().toISOString(), last_error: null, last_error_at: null, project: "airlab-sre-agent" },
    totals: { ...agg, p95_first_token_ms: 1500, traces: 3, trace_errors: 1, error_rate: 1 / 3 },
    by_model: [{ ...agg, model: "claude-opus-5-5" }],
    by_call: [{ ...agg, name: "explain_findings" }],
    by_trigger: [{ trigger: "scheduled", traces: 1, errors: 1, cost_usd: 0.01, p95_ms: 30000 }],
    series: [{ ...agg, t: "2026-11-25T10:00:00Z" }],
    top_cost: [
      { id: "t1", name: "chat-turn", trigger: "chat", model: "claude-opus-5-5", status: "success", error: null,
        start_time: "2026-11-25T11:30:00Z", latency_ms: 12000, investigation_id: null, conversation_id: "c1",
        cost_usd: 0.12, tokens: 4510, llm_calls: 2, url: "https://smith.test/r/t1" },
    ],
    slowest: [], errors: [], llm_errors: [],
  };

  it("özet kutuları, kırılımlar ve LangSmith bağlantısı gösterilir", async () => {
    tokenStore.set("tok", new Date(Date.now() + 3_600_000).toISOString());
    mockFetch({ "/api/auth/me": USER, "/api/observability/llm": obs });
    wrap(<LlmObservabilityPage />);
    expect(await within(await screen.findByRole("group", { name: "Error rate" })).findByText("%33,3")).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Maliyet" })).getByText("$0.1300")).toBeInTheDocument();
    expect(screen.getByText("RCA açıklaması")).toBeInTheDocument();
    expect(screen.getByText("Periyodik kontrol")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "LangSmith'te aç" })).toHaveAttribute("href", "https://smith.test/r/t1");
    expect(screen.getByRole("link", { name: /konuşma/ })).toHaveAttribute("href", "/investigate/c1");
  });

  it("senkron hatası açıkça gösterilir", async () => {
    tokenStore.set("tok", new Date(Date.now() + 3_600_000).toISOString());
    const old = new Date(Date.now() - 3_600_000).toISOString();
    mockFetch({
      "/api/auth/me": USER,
      "/api/observability/llm": { ...obs, sync: { last_ok_at: old, last_error: "ConnectError: down", last_error_at: new Date().toISOString(), project: null } },
    });
    wrap(<LlmObservabilityPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("ConnectError: down");
  });

  it("boş kovalar: çağrı/maliyet 0, latency boşluk", () => {
    const pts = fillSeries({ start: "2026-11-25T08:00:00Z", end: "2026-11-25T11:00:00Z", bucket: "1 hour", series: [{ ...agg, t: "2026-11-25T10:00:00+00:00" }] });
    expect(pts.map((p) => p.calls)).toEqual([0, 0, 3, 0]);
    expect(pts.map((p) => p.p95)).toEqual([null, null, 23300, null]);
  });
});
