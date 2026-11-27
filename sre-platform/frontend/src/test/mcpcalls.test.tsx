import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import McpCallsPage from "@/pages/McpCallsPage";
import { AuthProvider } from "@/services/auth";
import { tokenStore } from "@/services/api";
import type { McpCallDetail, McpCallRow } from "@/types/api";

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
}

function mockFetch(routes: [string, unknown][]) {
  const f = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const hit = routes.find(([k]) => url.startsWith(k));
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

const USER = { id: "u1", username: "nobetci", display_name: "Nöbetçi", role: "sre", is_active: true, last_login_at: null, created_at: "2026-11-21T00:00:00Z" };
const base: McpCallRow = {
  id: "c1", started_at: "2026-11-25T10:00:00Z", connector: "dynatrace", tool: "execute-dql", step: "app_metrics",
  query_key: "http", outcome: "success", failed: false, duration_ms: 420, record_count: 12, attempts: 1,
  investigation_id: "i1", run_id: "man-1",
};
const rows: McpCallRow[] = [
  { ...base, id: "c2", query_key: "pg_lock_logs", step: "logs", outcome: "tool_error", failed: true, record_count: null },
  { ...base, id: "c3", query_key: "db_conn", step: "db_metrics", outcome: "success", attempts: 2 },
  { ...base, id: "c4", query_key: "hikari", outcome: "empty", record_count: 0 },
];
const detail: McpCallDetail = {
  ...rows[0],
  request: { method: "tools/call", connector: "dynatrace", tool: "execute-dql", query_key: "pg_lock_logs", params: { includeTypes: false },
    query_start: "2026-11-25T09:45:00Z", query_end: "2026-11-25T10:00:00Z", limit: 50, omitted_params: [], legacy: null },
  query: { query_key: "pg_lock_logs", dql_template: 'fetch logs, from:"…", to:"…" | limit 50', dql_chars: 140, dql_sha256: "abcd1234abcd1234", window: "incident" },
  response: { outcome: "tool_error", failed: true, result_status: "ERROR", duration_ms: 420, http_status: 200, record_count: null,
    error_summary: 'DQL syntax error near "…"', evidence_key: null, dynatrace_query_id: null },
  attempts_log: [{ attempt: 1, elapsed_ms: 410, outcome: "tool_error", http_status: 200, error_type: null }],
  related: { mcp_call_id: "c2", run_id: "man-1", investigation_id: "i1", step: "logs", trigger: "manual", langsmith_span_url: "https://ls.test/r/c2" },
};

describe("McpCallsPage", () => {
  it("liste: durum sınıfları, retry sayısı, boş kayıt sayısı; run_id filtresi sunucuya gider; detay açılır", async () => {
    tokenStore.set("tok", new Date(Date.now() + 3_600_000).toISOString());
    const f = mockFetch([
      ["/api/auth/me", USER],
      ["/api/mcp-calls/facets", { connectors: ["dynatrace"], tools: ["execute-dql"], statuses: [] }],
      ["/api/mcp-calls/c2", detail],
      ["/api/mcp-calls", { items: rows, total: 3, page: 1, size: 50, pages: 1 }],
    ]);
    wrap(<McpCallsPage />, "/?run_id=man-1");
    const failedRow = await screen.findByRole("row", { name: "çağrı c2" });
    expect(within(failedRow).getByText("MCP tool hatası")).toBeInTheDocument();                // HTTP 200 içindeki hata başarısız
    expect(within(failedRow).getAllByRole("cell").at(-1)).toHaveTextContent(/^$/);             // kayıt sayısı uygulanamaz → boş
    expect(within(screen.getByRole("row", { name: "çağrı c3" })).getByText("2 deneme")).toBeInTheDocument();
    expect(within(screen.getByRole("row", { name: "çağrı c4" })).getByText("boş sonuç")).toBeInTheDocument();
    const listUrl = f.mock.calls.map((c) => String(c[0])).find((u) => u.startsWith("/api/mcp-calls?"));
    expect(listUrl).toContain("run_id=man-1");

    await userEvent.click(failedRow);
    const dlg = await screen.findByRole("dialog");
    expect(await within(dlg).findByText("tools/call")).toBeInTheDocument();
    expect(within(dlg).getByText('DQL syntax error near "…"')).toBeInTheDocument();
    expect(within(dlg).getByText("incelenen zaman aralığı")).toBeInTheDocument();
    expect(within(dlg).getByText('fetch logs, from:"…", to:"…" | limit 50')).toBeInTheDocument();
    expect(within(dlg).getByRole("link", { name: /Investigation'ı aç/ })).toHaveAttribute("href", "/investigations/i1?tab=tools");
    expect(within(dlg).getByRole("link", { name: /LangSmith span/ })).toHaveAttribute("href", "https://ls.test/r/c2");
    expect(within(dlg).getByText("Denemeler (1)")).toBeInTheDocument();
  });
});
