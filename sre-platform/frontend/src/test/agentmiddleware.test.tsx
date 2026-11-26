import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import AgentMiddlewarePage from "@/pages/AgentMiddlewarePage";
import { AuthProvider } from "@/services/auth";
import { tokenStore } from "@/services/api";
import type { CatalogItem, Execution, ExecutionDetail, HooksInfo, MwDetail, Overview, Timeline } from "@/features/middleware/api";

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
}

function mockFetch(routes: [string, unknown, number?][]) {
  const f = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const hit = routes.find(([k]) => {
      const [m, p] = k.includes(" ") ? k.split(" ") : ["GET", k];
      return method === m && url.startsWith(p);
    });
    return hit ? json(hit[1], hit[2] ?? 200) : json({ error: "not_found", message: url, correlation_id: "c" }, 404);
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
const ADMIN = { ...SRE, id: "u0", username: "admin", role: "admin" };
const login = () => tokenStore.set("tok", new Date(Date.now() + 3_600_000).toISOString());

const OVERVIEW: Overview = {
  range: "24h", since: "2026-11-25T00:00:00Z", registered: 7, enabled: 6, mandatory: 4, executions: 120, blocked: 3, errors: 0, modified: 2, wrapped_calls: 30, runs: 4,
  pending_policy_decisions: null, pending_policy_note: "desteklenmiyor: middleware'de bekleyen karar yok",
  overhead_ms: { p50: 0.05, p95: 0.4 }, wrapped_ms: { p50: null, p95: null, measured: 0 }, trend: [], trend_bucket: "hour", by_hook: [],
};
const HOOKS: HooksInfo = {
  hooks: [{ hook: "wrap_tool_call", supported: true, attach: "McpGateway._one_inner", adapter: "MCP client wrapper", langchain_builtin: false, order: "iç içe" }],
  unsupported: [{ name: "wrap_node (graph node)", reason: "Agent Trace'te" }],
  kinds: ["validation"], decisions: ["allow", "block"], ordering: { before: "artan", wrap: "iç içe", after: "ters" }, framework: "özel LangGraph StateGraph",
};
const schema = { type: "object", properties: { max_chars: { type: "integer", title: "Max Chars", minimum: 100, maximum: 8000 } } };
const ITEM: CatalogItem = {
  name: "tool_param_validation", title: "Tool parameter validation (DQL)", kind: "validation", description: "DQL denetimi", hooks: ["wrap_tool_call"],
  mandatory: true, security: true, default_order: 50, dry_run: "supported", policy_ref: "validate_dql", params_schema: schema, scope_schema: {},
  enabled: true, order: 50, scope: {}, scope_label: "global (zorunlu)", version: 1, updated_at: "2026-11-26T10:00:00Z", updated_by: "admin",
  last: { at: "2026-11-26T10:00:00Z", decision: "block", result: "success", reason: "izin verilmeyen komut", execution_id: "e1" },
  stats: { executions: 10, blocked: 1, errors: 0, modified: 0, overhead_p50_ms: 0.01, overhead_p95_ms: 0.02 },
};
const EXEC: Execution = {
  execution_id: "e1", run_id: "run-a", run_kind: "analysis", thread_id: "run-a", trace_id: null, span_id: null, parent_execution_id: "c1", sequence: 3, depth: 2,
  entry_type: "middleware", middleware: "tool_param_validation", middleware_version: 1, hook: "wrap_tool_call", target_kind: "tool", target: "execute-dql", task: "logs",
  connector: "dynatrace", tool_call_id: "tc-1", attempt_no: null, started_at: "2026-11-26T10:00:00Z", ended_at: "2026-11-26T10:00:00Z", overhead_ms: 0.02, wrapped_ms: null,
  result: "success", decision: "block", outcome: "blocked", reason: "izin verilmeyen komut", error_code: "dql_rejected", policy: "validate_dql", changed_fields: [],
  truncated: false, payload_captured: false,
};
const DETAIL: MwDetail = {
  ...ITEM, can_manage: false, config_json: { enabled: true, order: 50, params: { max_chars: 8000 }, scope: {}, version: 1 },
  hook_info: { wrap_tool_call: HOOKS.hooks[0] }, versions: [{ version: 1, change: "update", snapshot: {}, actor: "admin", note: null, created_at: "2026-11-26T10:00:00Z" }],
  hook_stats: [], recent: [EXEC],
};

describe("AgentMiddlewarePage", () => {
  it("Overview: gerçek sayılar, ölçülmeyen değer 'yakalanmadı', bekleyen karar 'desteklenmiyor', hook'lar adapter olarak etiketli", async () => {
    login();
    mockFetch([["/api/auth/me", SRE], ["/api/middleware/overview", OVERVIEW], ["/api/middleware/hooks", HOOKS]]);
    wrap(<AgentMiddlewarePage />);
    await waitFor(() => expect(screen.getByRole("group", { name: "Executions" })).toHaveTextContent("120"));
    expect(screen.getByRole("group", { name: "Pending Policy Decisions" })).toHaveTextContent("desteklenmiyor");
    expect(screen.getAllByText("yakalanmadı").length).toBeGreaterThan(0);                // wrapped p50/p95 ölçülmedi
    expect(await screen.findByText("hayır (adapter)")).toBeInTheDocument();
    expect(screen.getByText(/desteklenmiyor — Agent Trace'te/)).toBeInTheDocument();
    for (const t of ["Overview", "Catalog", "Execution Explorer", "Run Timeline", "Dry-run", "Settings"]) expect(screen.getByRole("tab", { name: t })).toBeInTheDocument();
  });

  it("Catalog (SRE): zorunlu middleware mandatory (lock), karar metin + işaretle; yönetim yetkisi yoksa düzenleme / sıralama yok", async () => {
    login();
    mockFetch([
      ["/api/auth/me", SRE],
      ["/api/middleware/overview", OVERVIEW],
      ["/api/middleware/catalog/tool_param_validation", DETAIL],
      ["/api/middleware/catalog", { items: [ITEM], can_manage: false }],
    ]);
    wrap(<AgentMiddlewarePage />, "/?tab=catalog");
    const row = (await screen.findByText("Tool parameter validation (DQL)")).closest("tr")!;
    expect(within(row).getByText("mandatory")).toBeInTheDocument();
    expect(within(row).getByText("block")).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: /taşı/ })).toBeNull();
    const u = userEvent.setup();
    await u.click(row);
    expect(await screen.findByText(/LangChain hook değil/)).toBeInTheDocument();
    expect(screen.getByText(/fail-closed/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Yapılandırmayı düzenle" })).toBeNull();
  });

  it("Admin: parametre formu backend şemasından üretilir; 409 çakışma mesajı formda görünür", async () => {
    login();
    const f = mockFetch([
      ["/api/auth/me", ADMIN],
      ["/api/middleware/overview", OVERVIEW],
      ["PATCH /api/middleware/catalog/tool_param_validation", { error: "conflict", message: "yapılandırma başka biri tarafından değiştirildi", correlation_id: "c" }, 409],
      ["/api/middleware/catalog/tool_param_validation", { ...DETAIL, can_manage: true }],
      ["/api/middleware/catalog", { items: [ITEM], can_manage: true }],
    ]);
    wrap(<AgentMiddlewarePage />, "/?tab=catalog&mw=tool_param_validation");
    const u = userEvent.setup();
    await u.click(await screen.findByRole("button", { name: "Yapılandırmayı düzenle" }));
    const input = screen.getByLabelText(/Max Chars/);
    expect(input).toHaveValue(8000);
    expect(screen.getByLabelText(/Enabled/)).toBeDisabled();                               // zorunlu: kapatılamaz
    await u.clear(input);
    await u.type(input, "2000");
    await u.click(screen.getByRole("button", { name: /Kaydet/ }));
    expect(await screen.findByText(/başka biri tarafından değiştirildi/)).toBeInTheDocument();
    const patch = f.mock.calls.find(([, i]) => (i as RequestInit | undefined)?.method === "PATCH");
    expect(JSON.parse(String((patch![1] as RequestInit).body))).toMatchObject({ version: 1, params: { max_chars: 2000 } });
  });

  it("Run Timeline: sıra numarası ve iç içe yapı; kayıt detayı payload'ın yakalanmadığını söyler", async () => {
    login();
    const tl: Timeline = {
      run_id: "run-a", status: "completed", count: 3, nested_runs: [], parallel_groups: [], config_versions: {}, lost_records: 0, ordering: "sequence + parent_execution_id",
      root: { ...EXEC, execution_id: "r", entry_type: "agent", sequence: 0, depth: 0, middleware: null, hook: null, decision: null, outcome: "completed" },
      tree: [
        {
          ...EXEC, execution_id: "r", entry_type: "agent", sequence: 0, depth: 0, middleware: null, hook: null, decision: null, outcome: "completed",
          children: [{ ...EXEC, execution_id: "c1", entry_type: "call", sequence: 1, depth: 1, middleware: null, hook: null, children: [{ ...EXEC, children: [] }] }],
        },
      ],
    };
    const det: ExecutionDetail = { ...EXEC, before: null, after: null, extra: {}, diff: null, cache: "not_supported", parent: null, links: { agent_trace: "/agent-trace/run-a", timeline: null, mcp_call: "/mcp-calls?call=tc-1", llm_monitoring: null } };
    mockFetch([
      ["/api/auth/me", SRE],
      ["/api/middleware/overview", OVERVIEW],
      ["/api/middleware/runs/run-a/timeline", tl],
      ["/api/middleware/runs", { total: 1, items: [{ ...tl.root, executions: 3, blocked: 1, errors: 0, lost_records: 0 }] }],
      ["/api/middleware/executions/e1", det],
    ]);
    wrap(<AgentMiddlewarePage />, "/?tab=timeline&run=run-a");
    const list = await screen.findByRole("list", { name: "Yürütme sırası" });
    const items = within(list).getAllByRole("button");
    expect(items.map((b) => b.textContent?.match(/#\d+/)?.[0])).toEqual(["#0", "#1", "#3"]);
    const u = userEvent.setup();
    await u.click(items[2]);
    expect(await screen.findByText(/Payload yakalanmadı/)).toBeInTheDocument();
    expect(screen.getByText("desteklenmiyor (cache yok)")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /MCP Çağrısı/ })).toHaveAttribute("href", "/mcp-calls?call=tc-1");
  });
});
