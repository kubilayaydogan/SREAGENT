import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import GuardrailsPage from "@/pages/GuardrailsPage";
import { AuthProvider } from "@/services/auth";
import { tokenStore } from "@/services/api";
import { jsonDiff, type Analytics, type Approval, type Overview, type Policy, type PolicyDetail } from "@/features/guardrails/api";

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

const META = { evaluators: [], stages: [{ key: "input", label: "Before agent" }], categories: ["input", "tool"], actions: ["block", "warn"], modes: ["monitor", "enforce", "disabled"], severities: ["low", "medium", "high", "critical"], scopes: ["global"] };

const OVERVIEW: Overview = {
  range: "24h", total_evaluations: 1234, violations: 7, blocked: 3, redacted: 2, warned: 2, approval_required: 0, errors: 0, timeouts: 0,
  by_decision: { passed: 1227, blocked: 3 }, avg_ms: null, max_ms: null, p95_ms_approx: null, histogram: { labels: ["≤0.1ms"], counts: [5] },
  policies: { active: 30, mandatory: 22, monitor: 3, disabled: 1 }, pending_approvals: 1, open_violations: { critical: 1 }, series: [],
  top_policies: [{ policy_key: "input_prompt_injection", category: "input", n: 3 }], top_agents: [], recent_critical: [],
  health: { writer_lost: 0, writer_written: 10, snapshot_revision: 4 },
};

const POLICY: Policy = {
  id: "p1", key: "tool_rbac", name: "Tool RBAC", description: null, category: "tool", type: "tool_rbac", type_title: "Tool RBAC", severity: "critical",
  mode: "enforce", status: "active", mandatory: true, builtin: true, stages: ["tool_request"], action: "block", priority: 10, params: {}, bound_to: null,
  enforced_by: "guardrail_engine", assignments: [{ scope: "global", value: null }], scope: "global", version: 2, latest_version: 2, created_by: null,
  updated_by: "admin", created_at: null, updated_at: null, last_triggered_at: null,
};
const DETAIL: PolicyDetail = {
  ...POLICY,
  versions: [
    { version: 2, change: "update", note: null, checksum: "x", created_by: "admin", created_at: "2026-11-26T10:00:00Z", active: true, snapshot: {} },
    { version: 1, change: "create", note: null, checksum: "y", created_by: null, created_at: "2026-11-25T10:00:00Z", active: false, snapshot: {} },
  ],
  evaluator: null,
};

const ANALYTICS: Analytics = {
  range: "24h", by_category: [], by_stage: [], policies: [], monitor_vs_enforce: [], latency_note: "yaklaşık",
  labels: { triage: { false_positive: 0, true_positive: 0, false_positive_rate: null }, tests: { labeled: 0, false_positive: 0, false_negative: 0, false_positive_rate: null, false_negative_rate: null }, note: "etiket yoksa değer gösterilmez" },
};

const APPROVAL = (by: string): Approval => ({
  id: `a-${by}`, agent_id: null, agent_key: null, run_id: "r1", thread_id: null, requested_action: "dynatrace:write-tool", target_resource: "dynatrace",
  risk_level: "critical", requested_by: by, required_role: "sre", requested_at: "2026-11-26T10:00:00Z", expires_at: "2099-01-01T00:00:00Z", status: "pending",
  reason: null, decision_reason: null, decided_by: null, decided_at: null, action_params: {}, action_hash: "abcdef1234567890", policy_key: "tool_write_approval",
  policy_version: 1, consumed_at: null, result: null, evaluation_id: null,
});

describe("jsonDiff", () => {
  it("eklenen / silinen / değişen alanları yol ile verir", () => {
    const d = jsonDiff({ a: 1, b: { c: "x" }, r: [1] }, { a: 1, b: { c: "[REDACTED]" }, n: true, r: [1, 2] });
    expect(d).toEqual(
      expect.arrayContaining([
        { path: "$.b.c", change: "changed", old: "x", new: "[REDACTED]" },
        { path: "$.n", change: "added", new: true },
        { path: "$.r[1]", change: "added", new: 2 },
      ]),
    );
    expect(d.find((x) => x.path === "$.a")).toBeUndefined();
  });
});

describe("GuardrailsPage", () => {
  it("Overview: gerçek sayılar; ölçülmeyen latency '—'", async () => {
    login();
    mockFetch([["/api/auth/me", SRE], ["/api/guardrails/overview", OVERVIEW]]);
    wrap(<GuardrailsPage />);
    await waitFor(() => expect(screen.getByRole("group", { name: "Değerlendirme" })).toHaveTextContent("1.234"));
    expect(screen.getByRole("group", { name: "Engellenen" })).toHaveTextContent("3");
    expect(screen.getByRole("group", { name: "Ort. / p95 latency" })).toHaveTextContent("—");
    expect(screen.getByText("input_prompt_injection")).toBeInTheDocument();
  });

  it("Policies: mandatory policy'de mode ve durum değiştirilemez; eski sürüme rollback sunulur", async () => {
    login();
    mockFetch([["/api/auth/me", ADMIN], ["/api/guardrails/evaluators", META], ["/api/guardrails/policies/p1", DETAIL], ["/api/guardrails/policies", { items: [POLICY] }]]);
    wrap(<GuardrailsPage />, "/?tab=policies");
    await userEvent.click(await screen.findByText("Tool RBAC"));
    expect(await screen.findByText(/Mandatory policy: her kapsamda enforce/)).toBeInTheDocument();
    const selects = screen.getAllByRole("combobox");
    const mode = selects.find((s) => (s as HTMLSelectElement).value === "enforce") as HTMLSelectElement;
    expect(mode).toBeDisabled();
    await userEvent.click(screen.getByRole("tab", { name: /Sürümler/ }));
    expect(screen.getAllByRole("button", { name: /Rollback/ })).toHaveLength(1);
  });

  it("Policies: admin olmayan kullanıcı salt okunur uyarısı görür", async () => {
    login();
    mockFetch([["/api/auth/me", SRE], ["/api/guardrails/evaluators", META], ["/api/guardrails/policies", { items: [POLICY] }]]);
    wrap(<GuardrailsPage />, "/?tab=policies");
    expect(await screen.findByText(/yalnız admin rolüyle/)).toBeInTheDocument();
  });

  it("Human Approvals: kendi isteğini onaylayamaz (dört göz), yalnız iptal eder", async () => {
    login();
    mockFetch([["/api/auth/me", ADMIN], ["/api/guardrails/approvals", { total: 2, items: [APPROVAL("u0"), APPROVAL("u9")] }]]);
    wrap(<GuardrailsPage />, "/?tab=approvals");
    await waitFor(() => expect(screen.getAllByText("dynatrace:write-tool")).toHaveLength(2));
    expect(screen.getAllByRole("button", { name: "Onayla" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "İptal" })).toHaveLength(1);
  });

  it("Human Approvals: approval:decide yetkisi olmayan (sre) karar düğmesi görmez", async () => {
    login();
    mockFetch([["/api/auth/me", SRE], ["/api/guardrails/approvals", { total: 1, items: [APPROVAL("u9")] }]]);
    wrap(<GuardrailsPage />, "/?tab=approvals");
    await screen.findByText("dynatrace:write-tool");
    expect(screen.queryByRole("button", { name: "Onayla" })).toBeNull();
  });

  it("Analytics: etiket yoksa FP / FN oranı uydurulmaz", async () => {
    login();
    mockFetch([["/api/auth/me", SRE], ["/api/guardrails/analytics", ANALYTICS]]);
    wrap(<GuardrailsPage />, "/?tab=analytics");
    await waitFor(() => expect(screen.getByRole("group", { name: "FP oranı (triage)" })).toHaveTextContent("—"));
    expect(screen.getByRole("group", { name: "FN oranı (etiketli test)" })).toHaveTextContent("—");
  });
});
