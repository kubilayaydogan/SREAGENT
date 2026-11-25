import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import AgentTracePage from "@/pages/AgentTracePage";
import { AuthProvider } from "@/services/auth";
import { tokenStore } from "@/services/api";
import type { NodeExec, TraceCall, TraceDetail, TraceEventMeta } from "@/types/api";

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

function wrap(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/agent-trace/:key?" element={<AgentTracePage />} />
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
let seq = 0;
const ev = (p: Partial<TraceEventMeta>): TraceEventMeta => ({
  event_id: `e${++seq}`, sequence: seq, ts: "2026-11-26T10:00:00Z", event_type: "stream", node: null, node_exec_id: null, step: null,
  attempt: null, call_id: null, related_call_id: null, status: null, duration_ms: null, truncated: false, size_bytes: 10, summary: {}, ...p,
});
const events = [
  ev({ event_type: "run_start" }),
  ev({ event_type: "node_start", node: "collect", node_exec_id: "t-col", step: 2, attempt: 1 }),
  ev({ event_type: "node_end", node: "collect", node_exec_id: "t-col", step: 2, attempt: 1, status: "ok", duration_ms: 1200 }),
  ev({ event_type: "state_out", step: 2, summary: { nodes: ["collect"] } }),
  ev({ event_type: "transition", step: 2, summary: { from: ["collect"], to: ["analyze"] } }),
  ev({ event_type: "node_start", node: "analyze", node_exec_id: "t-ana", step: 3, attempt: 1 }),
  ev({ event_type: "node_end", node: "analyze", node_exec_id: "t-ana", step: 3, attempt: 1, status: "ok" }),
  ev({ event_type: "routing", node: "analyze", node_exec_id: "t-ana", step: 3, summary: { function: "needs_extra", result: "explain" } }),
  ev({ event_type: "llm_request", node: "route", node_exec_id: "t-r", step: 1, attempt: 1, call_id: "llm-1", summary: { task: "route_question" } }),
  ev({ event_type: "validation_error", node: "route", node_exec_id: "t-r", step: 1, attempt: 1, call_id: "p-1", related_call_id: "llm-1", status: "error", summary: { errors: 1 } }),
  ev({ event_type: "llm_request", node: "route", node_exec_id: "t-r", step: 1, attempt: 2, call_id: "llm-2", summary: { task: "route_question" } }),
];
const detail: TraceDetail = {
  run: { trace_id: "tr1", run_id: "man-1", kind: "analysis", trigger: "manual", status: "completed", recorded: true, investigation_id: "i1", thread_id: "man-1",
    started_at: "2026-11-26T10:00:00Z", duration_ms: 5000, attempt: 1, capture: "complete", event_count: events.length },
  events, children: [], parent: null, langsmith_url: "https://ls.test/r/1",
};
const results = { http: { status: "OK", records: { _records_omitted: true, count: 12, fields: ["p95"] } } };
const col: NodeExec = {
  node: "collect", node_exec_id: "t-col", step: 2, attempt: 1, status: "ok", duration_ms: 1200,
  before: { captured: true, truncated: false, value: { plan: { apps: ["reservation-service"] } }, event_id: "e2" },
  update: { captured: true, truncated: false, value: { results }, error: null, event_id: "e3" },
  after: { captured: true, truncated: false, value: { plan: { apps: ["reservation-service"] }, results }, combined: false, step_node_exec_ids: ["t-col"], event_id: "e4" },
  diff: { available: true, reason: null, changes: [{ path: "results.http.status", change: "added", old: null, new: "OK" }] },
  transition_in: { from: ["plan"], from_exec_ids: [], routing: null, event_id: "x" },
  transition_out: { to: ["analyze"], routing: null, to_exec_ids: ["t-ana"], event_id: "e5" },
  events: [],
};
const ana: NodeExec = {
  ...col, node: "analyze", node_exec_id: "t-ana", step: 3,
  before: { ...col.after, event_id: "e6" },
  update: { captured: true, truncated: false, value: { attribution: { mechanism: "row_lock" } }, error: null, event_id: "e7" },
  after: { captured: true, truncated: false, value: { results, attribution: { mechanism: "row_lock" } }, combined: false, step_node_exec_ids: ["t-ana"], event_id: "e8" },
  diff: { available: true, reason: null, changes: [{ path: "attribution.mechanism", change: "added", old: null, new: "row_lock" }] },
  transition_in: { from: ["collect"], from_exec_ids: ["t-col"], routing: null, event_id: "e5" },
  transition_out: { to: ["explain"], routing: { analyze: [{ function: "needs_extra", result: "explain" }] }, to_exec_ids: [], event_id: "e9" },
};
const call: TraceCall = {
  call_id: "llm-1",
  events: [
    { ...events[8], payload: { source: "framework", task: "route_question", model: "claude-test", messages: [{ role: "user", content: "soru" }] } },
    { ...events[9], payload: { error_type: "ValidationError", model: "ChatRoute", errors: [{ path: "window.minutes", type: "greater_than_equal", message: "Input should be ≥ 1" }] },
      followup: { kind: "retry", call_id: "llm-2", attempt: 2, event_id: "e11" } },
  ],
};

describe("AgentTracePage", () => {
  it("collect → analyze: Before / Node Update / After / Diff ve gerçek state alanı", async () => {
    tokenStore.set("tok", new Date(Date.now() + 3_600_000).toISOString());
    mockFetch([
      ["/api/auth/me", USER],
      ["/api/agent-traces/man-1/node-execs/t-col", col],
      ["/api/agent-traces/man-1/node-execs/t-ana", ana],
      ["/api/agent-traces/man-1/calls/llm-1", call],
      ["/api/agent-traces/man-1", detail],
    ]);
    wrap("/agent-trace/man-1");
    const tl = await screen.findByRole("list", { name: "olay zaman çizelgesi" });
    expect(within(tl).getByText(/Graph adımı 2/)).toBeInTheDocument();

    // collect
    await userEvent.click(within(tl).getAllByRole("button", { name: /Node başladı · State In\s*collect/ })[0]);
    expect(await screen.findByText("Before · State In")).toBeInTheDocument();
    expect(screen.getByText("After · State Out")).toBeInTheDocument();
    expect(screen.getAllByText(/^results:/)).toHaveLength(1); // yalnız After'da (collect'in ürettiği alan)
    await userEvent.click(screen.getByRole("tab", { name: "Node Update" }));
    expect(await screen.findByText(/^results:/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /Diff/ }));
    expect(await screen.findByText("results.http.status")).toBeInTheDocument();
    // geçiş: collect → analyze (bağlantıyla analyze çalıştırmasına atlanır)
    await userEvent.click(screen.getByRole("button", { name: "analyze" }));

    // analyze
    expect(await screen.findByText(/graph adımı 3/)).toBeInTheDocument();
    expect(screen.getAllByText(/^results:/)).toHaveLength(2); // State In'de collect'in sonucu var
    expect(screen.getByText(/routing: needs_extra\(\) → explain/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Node Update" }));
    expect(await screen.findByText(/^attribution:/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /Diff/ }));
    const row = (await screen.findByText("attribution.mechanism")).closest("tr")!;
    expect(within(row).getByText("eklendi")).toBeInTheDocument();
    expect(within(row).getByText("row_lock")).toBeInTheDocument();
  });

  it("validation error: alan yolu ve retry bağlantısı; framework/provider ayrımı", async () => {
    tokenStore.set("tok", new Date(Date.now() + 3_600_000).toISOString());
    mockFetch([
      ["/api/auth/me", USER],
      ["/api/agent-traces/man-1/calls/llm-1", call],
      ["/api/agent-traces/man-1", detail],
    ]);
    wrap("/agent-trace/man-1?f=errors");
    const tl = await screen.findByRole("list", { name: "olay zaman çizelgesi" });
    await userEvent.click(within(tl).getByRole("button", { name: /Validation Error/ }));
    expect(await screen.findByText("window.minutes")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Retry: deneme #2/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Provider Request" }));
    expect(await screen.findByText(/Kaydedilemedi/)).toBeInTheDocument();
  });

  it("eski çalıştırma: trace verisi kaydedilmemiş", async () => {
    tokenStore.set("tok", new Date(Date.now() + 3_600_000).toISOString());
    mockFetch([
      ["/api/auth/me", USER],
      ["/api/agent-traces/legacy-1", { ...detail, run: { ...detail.run, run_id: "legacy-1", recorded: false, capture: "none" }, events: [] }],
    ]);
    wrap("/agent-trace/legacy-1");
    expect(await screen.findByText("Bu çalıştırma için trace verisi kaydedilmemiş")).toBeInTheDocument();
  });
});
