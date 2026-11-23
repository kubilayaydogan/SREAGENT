import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ActivityTimeline, toolsFromRecords } from "@/components/ActivityTimeline";
import DashboardPage from "@/pages/DashboardPage";
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
