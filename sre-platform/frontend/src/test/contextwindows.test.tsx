import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import ContextWindowsPage from "@/pages/ContextWindowsPage";
import { AuthProvider } from "@/services/auth";
import { tokenStore } from "@/services/api";
import type { CwCall, CwCallDetail, CwOverview } from "@/features/contextwindows/api";

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
}

function mockFetch(routes: [string, unknown][]) {
  const f = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const hit = routes.find(([p]) => url.startsWith(p));
    return hit ? json(hit[1]) : json({ error: "not_found", message: url, correlation_id: "c" }, 404);
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

const SRE = { id: "u1", username: "nobetci", display_name: "Nöbetçi", role: "sre", is_active: true, last_login_at: null, created_at: "2026-11-21T00:00:00Z" };
const login = () => tokenStore.set("tok", new Date(Date.now() + 3_600_000).toISOString());

// Test düzeneği verisi (yalnız bu testte; gerçek telemetri değildir)
const OV: CwOverview = {
  range: "24h", since: "2026-11-26T00:00:00Z", attempts: 4, calls: 3, max_actual_fill: 0.052, max_call: null,
  context_limit_errors: 1, retried_attempts: 1, errors: 0, usage_missing: 2, capacity_unknown: 2,
};
const ROW: CwCall = {
  call_id: "c-ok", logical_call_id: "l1", attempt_no: 1, attempt_count: 2, run_id: "run-a", run_kind: "analysis", node: "explain",
  task: "explain_findings", call_seq: 2, provider: "anthropic", model: "claude-opus-5-5", served_model: null, streaming: false,
  started_at: "2026-11-26T10:00:00Z", duration_ms: 900, status: "ok", error_code: null, input_tokens: 50_000, output_tokens: 2_000,
  capacity: 1_000_000, planned_fill: 0.06, actual_fill: 0.052, usage_source: "provider",
};
const UNKNOWN_CAP: CwCall = { ...ROW, call_id: "c-unk", logical_call_id: "l2", attempt_no: 0, attempt_count: 1, node: "route", call_seq: 1, capacity: null, planned_fill: null, actual_fill: null, input_tokens: 600, output_tokens: 40 };
const LIMIT: CwCall = { ...UNKNOWN_CAP, call_id: "c-lim", logical_call_id: "l3", node: "respond", status: "context_limit", error_code: "prompt_too_long", input_tokens: null, output_tokens: null, usage_source: "missing" };
const DETAIL: CwCallDetail = {
  ...ROW, thread_id: "run-a", ended_at: "2026-11-26T10:00:01Z", error_message: null, http_status: 200, stop_reason: "end_turn", request_id: "req_1",
  capacity_source: "Models API (max_input_tokens)", capacity_note: null, model_max_output: 128_000, reserved_output: 12_000, estimated_input: 48_000,
  estimate_method: "karakter / 4 (yerel tahmin; sağlayıcı tokenizer'ı çağrılmaz)", estimate_over: null, cache_read_tokens: 5_000, cache_creation_tokens: null,
  reasoning_tokens: null,
  components: [
    { key: "tool_results", label: "MCP / tool sonuçları", tokens: 30_000, method: "estimate", refs: ["<analysis_data>", "alan:risk"] },
    { key: "system", label: "Sistem talimatları", tokens: 18_000, method: "estimate", refs: [] },
    { key: "other", label: "Diğer / sağlayıcı ek yükü", tokens: 2_000, method: "provider_minus_estimate", refs: [] },
  ],
  context_decisions: null, stream: null, trace_call_id: "tc-1", middleware_execution_id: "e1",
  attempts: [{ ...ROW, call_id: "c-retry", attempt_no: 0, status: "retried", error_code: "overloaded", input_tokens: null, output_tokens: null, actual_fill: null }, ROW],
  links: { agent_trace: "/agent-trace/run-a?call=tc-1", agent_trace_level: "call", middleware_timeline: "/agent-middleware?tab=timeline&run=run-a", llm_monitoring: "/llm" },
  component_labels: {},
};

describe("ContextWindowsPage", () => {
  it("üst alan ve tablo: kapasite bilinmiyorsa doluluk 'Bilinmiyor', context limit ayrı durum, sıralama sunucuya gider", async () => {
    login();
    const f = mockFetch([
      ["/api/auth/me", SRE],
      ["/api/context-windows/overview", OV],
      ["/api/context-windows/facets", { nodes: ["explain", "respond", "route"], models: ["claude-opus-5-5"], statuses: [] }],
      ["/api/context-windows/calls", { total: 3, items: [ROW, UNKNOWN_CAP, LIMIT] }],
    ]);
    wrap(<ContextWindowsPage />);
    await waitFor(() => expect(screen.getByRole("group", { name: "Toplam çağrı" })).toHaveTextContent("3"));
    expect(screen.getByRole("group", { name: "En yüksek ölçülen doluluk" })).toHaveTextContent("%5,2");
    expect(screen.getByRole("group", { name: "Context limit hatası" })).toHaveTextContent("1");
    const unk = (await screen.findByRole("row", { name: "çağrı c-unk" })) as HTMLElement;
    expect(within(unk).getByText("Bilinmiyor")).toBeInTheDocument();
    const lim = screen.getByRole("row", { name: "çağrı c-lim" });
    expect(within(lim).getByText("context limit")).toBeInTheDocument();
    expect(within(lim).getAllByText("Bilinmiyor").length).toBeGreaterThanOrEqual(2);         // usage yok: 0 yazılmaz
    expect(within(screen.getByRole("row", { name: "çağrı c-ok" })).getByText(/deneme 2\/2/)).toBeInTheDocument();
    const u = userEvent.setup();
    await u.click(screen.getByRole("radio", { name: "En yüksek doluluk" }));
    await waitFor(() => expect(f.mock.calls.some(([url]) => String(url).includes("sort=fill"))).toBe(true));
  });

  it("detay paneli: planlanan / gerçekleşen ayrı, değer kaynakları, girdi dağılımı, denemeler, Agent Trace çağrı bağlantısı", async () => {
    login();
    mockFetch([
      ["/api/auth/me", SRE],
      ["/api/context-windows/overview", OV],
      ["/api/context-windows/facets", { nodes: [], models: [], statuses: [] }],
      ["/api/context-windows/calls/c-ok", DETAIL],
      ["/api/context-windows/calls", { total: 1, items: [ROW] }],
    ]);
    wrap(<ContextWindowsPage />, "/?call=c-ok");
    const planned = await screen.findByRole("region", { name: "Planlanan bütçe" });
    expect(within(planned).getByText("48.000")).toBeInTheDocument();
    expect(within(planned).getAllByText("tahmini").length).toBeGreaterThan(0);
    expect(within(planned).getByText("platform ayarı")).toBeInTheDocument();
    const actual = screen.getByRole("region", { name: "Gerçekleşen kullanım" });
    expect(within(actual).getByText("50.000")).toBeInTheDocument();
    expect(within(actual).getAllByText("sağlayıcı ölçümü").length).toBeGreaterThan(0);
    expect(within(actual).getAllByText("bilinmiyor").length).toBeGreaterThan(0);              // thinking ayrı bildirilmedi
    const dist = screen.getByRole("list", { name: "bileşenler" });
    expect(within(dist).getAllByRole("listitem").map((li) => li.textContent?.split(/\d/)[0])).toEqual(["MCP / tool sonuçları", "Sistem talimatları", "Diğer / sağlayıcı ek yükü"]);
    expect(within(dist).getByText(/sağlayıcı toplamı − tahminler/)).toBeInTheDocument();
    expect(screen.getByText(/Ölçülmedi: bu çağrı için context hazırlama/)).toBeInTheDocument();
    const attempts = screen.getByRole("region", { name: "Denemeler" });
    expect(within(attempts).getByText("retry edildi")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Agent Trace \(bu çağrı\)/ })).toHaveAttribute("href", "/agent-trace/run-a?call=tc-1");
  });
});
