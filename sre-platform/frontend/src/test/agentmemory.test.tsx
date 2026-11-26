import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import AgentMemoryPage from "@/pages/AgentMemoryPage";
import { AuthProvider } from "@/services/auth";
import { tokenStore } from "@/services/api";
import type { Overview, RecordList, RetrievalDetail } from "@/features/memory/api";

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
}

function mockFetch(routes: [string, unknown, number?][]) {
  const f = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const hit = routes.find(([k]) => (k.startsWith("POST ") ? method === "POST" && url.startsWith(k.slice(5)) : method === "GET" && url.startsWith(k)));
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

const USER = { id: "u1", username: "nobetci", display_name: "Nöbetçi", role: "sre", is_active: true, last_login_at: null, created_at: "2026-11-21T00:00:00Z" };
const AGENTS = { tenant_id: "default", items: [{ agent_id: "sre-agent", name: "SRE Agent (embedded)", kind: "embedded", namespace_prefix: "sre-agent", settings_section: "agent_memory", description: "" }], agent_builder: "" };
const lat = { p50: null, p95: null, n: 0 };
const OVERVIEW: Overview = {
  agent_id: "sre-agent", range: "24h", since: "2026-11-25T00:00:00Z", environment: "lab", definitions: { total_memory_records: "ayrık kaynaklar" }, settings: {},
  metrics: {
    total_memory_records: 42, by_backing: { episodes: 30, known_issues: 12 }, active_threads: 3, total_threads: 9, total_checkpoints: 120,
    semantic: 12, episodic: 30, procedural: 0, reads: 7, writes: 2, checkpoint_writes: 50, checkpoint_loads: 20, retrievals: 4,
    retrieval_success_rate: 1, empty_retrieval_rate: 0.25, read_latency: lat, write_latency: lat, checkpoint_write_latency: lat,
    retrieval_latency: { p50: 12, p95: 30, p99: 31 }, context_tokens: 900, storage_bytes: { "sre.memory_records": 8192 }, storage_total: 8192,
    expired: 0, rejected: 1, events_by_type: {},
  },
  health: { checkpointer: { status: "healthy", latency_ms: 1.2 }, store: { status: "healthy" }, memory_tables: { status: "healthy" }, event_publisher: { status: "healthy", published: 5, lost: 0 }, vector_index: { status: "not_configured", note: "pgvector yok" } },
  charts: { bucket: "hour", growth: [], reads_writes: [], distribution: [{ memory_type: "episodic", count: 30 }], retrieval_latency: [], context_tokens: [], validation: [], by_agent: [], by_namespace: [] },
};
const RECORDS: RecordList = {
  items: [
    {
      memory_id: "mem:11111111-1111-1111-1111-111111111111", backing: "sre.memory_records", tenant_id: "default", agent_id: "sre-agent", namespace: "sre-agent/semantic/topology",
      memory_type: "semantic", category: "topology", title: "checkin → reservation", content: "bağımlılık", structured_data: {}, source: "mcp", source_trust: "untrusted",
      source_run_id: null, source_thread_id: null, source_trace_id: null, evidence_references: [], confidence_score: null, verification_status: "unverified",
      approval_status: "not_required", status: "active", created_at: "2026-11-25T10:00:00Z", updated_at: "2026-11-25T10:00:00Z", last_accessed_at: null,
      access_count: 0, expires_at: null, version: 1, tags: [], embedding_model: null, embedding_status: "not_indexed", metadata: {}, actions: ["update", "archive", "verify"],
    },
  ],
  total: 1, page: 1, size: 50, pages: 1, by_backing: { "sre.memory_records": 1 }, note: "Kaynaklar ayrık tablolardır",
};

describe("AgentMemoryPage", () => {
  it("Overview: gerçek metric değerleri, ölçülmeyen değer '—', 9 sekme ve vector index durumu", async () => {
    tokenStore.set("tok", new Date(Date.now() + 3_600_000).toISOString());
    mockFetch([
      ["/api/auth/me", USER],
      ["/api/memory/agents", AGENTS],
      ["/api/memory/overview", OVERVIEW],
    ]);
    wrap(<AgentMemoryPage />);
    await waitFor(() => expect(screen.getByRole("group", { name: "Total Memory Records" })).toHaveTextContent("42"));   // skeleton → değer
    expect(screen.getByRole("group", { name: "Empty Retrieval Rate" })).toHaveTextContent("25");
    expect(screen.getByRole("group", { name: "Read Latency p50 / p95" })).toHaveTextContent("—");      // ölçüm yok → uydurma yok
    for (const t of ["Overview", "Working Memory", "Short-Term Memory", "Long-Term Memory", "Checkpoints", "Memory Retrieval", "Memory Activity", "Memory Governance", "Settings"])
      expect(screen.getByRole("tab", { name: t })).toBeInTheDocument();
    expect(screen.getByText("not_configured")).toBeInTheDocument();
  });

  it("Long-Term: untrusted kayıt işaretlenir; policy reddi ihlal kodlarıyla gösterilir", async () => {
    tokenStore.set("tok", new Date(Date.now() + 3_600_000).toISOString());
    const f = mockFetch([
      ["/api/auth/me", USER],
      ["/api/memory/agents", AGENTS],
      ["/api/memory/overview", OVERVIEW],
      ["/api/memory/approvals", { items: [] }],
      ["POST /api/memory/records", { error: "memory_policy", message: "memory policy reddetti", correlation_id: "c", details: [{ code: "secret_detected", detail: "secret kalıbı" }] }, 422],
      ["/api/memory/records", RECORDS],
    ]);
    wrap(<AgentMemoryPage />, "/?tab=long");
    const row = await screen.findByText("checkin → reservation");
    expect(within(row.closest("tr")!).getByText("untrusted")).toBeInTheDocument();
    const u = userEvent.setup();
    await u.click(screen.getByRole("button", { name: /New memory/ }));
    await u.type(screen.getByLabelText("Title"), "db");
    await u.type(screen.getByLabelText("Content"), "password=x");
    await u.click(screen.getByRole("button", { name: "Kaydet" }));
    expect(await screen.findByText(/secret_detected/)).toBeInTheDocument();
    const post = f.mock.calls.find(([, i]) => (i as RequestInit | undefined)?.method === "POST");
    expect(JSON.parse(String((post![1] as RequestInit).body))).toMatchObject({ memory_type: "semantic", title: "db", source: "manual" });
  });

  it("Retrieval: pipeline aşamaları ve embedding kullanılmadığı açıkça gösterilir; Semantic strateji seçilemez", async () => {
    tokenStore.set("tok", new Date(Date.now() + 3_600_000).toISOString());
    const det: RetrievalDetail = {
      retrieval_id: "r1", ts: "2026-11-25T10:00:00Z", agent_id: "sre-agent", origin: "agent", run_id: "chat-1", thread_id: "t1", trace_id: null, node: "recall",
      strategy: "fts_fingerprint", namespaces: ["sre-agent/episodic/investigation"], memory_types: ["episodic"], top_k: 5, returned_count: 1, empty: false,
      duration_ms: 12, status: "ok", error: null, injection_status: "injected", injected_tokens: 120, query: "checkin", filters: {}, selected: [{ ref: "inv:1" }],
      excluded: [{ ref: "mem:x", reason: "untrusted ve doğrulanmamış" }], stages: [{ stage: "query" }, { stage: "namespace_filter" }, { stage: "validation" }],
      injection: { status: "injected" }, query_embedding: { status: "not_applicable", note: "embedding kullanılmıyor" }, events: [],
    };
    mockFetch([
      ["/api/auth/me", USER],
      ["/api/memory/agents", AGENTS],
      ["/api/memory/overview", OVERVIEW],
      ["/api/memory/strategies", { implemented: [{ id: "keyword", label: "Keyword Search" }], not_implemented: [{ id: "semantic", label: "Semantic Search (vector)" }], agent_strategy: "fts_fingerprint" }],
      ["/api/memory/retrievals/r1", det],
      ["/api/memory/retrievals", { items: [det], total: 1, page: 1, size: 50, pages: 1 }],
    ]);
    wrap(<AgentMemoryPage />, "/?tab=retrieval");
    expect(await screen.findByRole("option", { name: "Semantic Search (vector)" })).toBeDisabled();
    const u = userEvent.setup();
    await u.click(await screen.findByText("fts_fingerprint"));
    expect(await screen.findByText(/not_applicable — embedding kullanılmıyor/)).toBeInTheDocument();
    expect(screen.getByText("2. namespace_filter")).toBeInTheDocument();
    expect(screen.getByText(/untrusted ve doğrulanmamış/)).toBeInTheDocument();
  });
});
