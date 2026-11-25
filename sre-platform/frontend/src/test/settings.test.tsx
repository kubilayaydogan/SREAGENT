import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import SettingsPage from "@/pages/SettingsPage";
import { AuthProvider } from "@/services/auth";
import { tokenStore } from "@/services/api";
import { fmtTime, setDisplayPrefs } from "@/lib/utils";

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
}

const USER = { id: "u1", username: "yonetici", display_name: "Yönetici", role: "admin", is_active: true, last_login_at: null, created_at: "2026-11-21T00:00:00Z" };

const meta = { env: null, apply: "next_run", help: "", unit: null, min: null, max: null, nullable: false, options: null, restart_pending: false, locked: false, inherited: null };
const AGENT = {
  section: "agent",
  revision: 3,
  updated_by: "admin",
  updated_at: "2026-11-25T10:00:00Z",
  values: { recursion_limit: 25, max_concurrent_runs: 2, execution_mode: "hybrid", llm_enabled: true },
  fields: {
    recursion_limit: { ...meta, label: "LangGraph recursion_limit", kind: "int", min: 10, max: 100, value: 25, source: "default", inherited: 25 },
    max_concurrent_runs: { ...meta, label: "Eşzamanlı investigation", kind: "int", value: 2, source: "locked", locked: true, apply: "restart" },
    execution_mode: { ...meta, label: "Execution mode", kind: "enum", options: ["manual", "scheduled", "hybrid"], value: "hybrid", source: "admin", inherited: "hybrid" },
    llm_enabled: { ...meta, label: "LLM kullan", kind: "bool", value: true, source: "env", env: "ANTHROPIC_API_KEY" },
  },
  status: { llm_available: true, llm_reason: null, graph: ["plan", "collect"], backoff: "1, 2, 4, 8 sn", concurrency_in_process: 2 },
};
const OVERVIEW = { sections: [{ section: "agent", revision: 3, updated_by: "admin", updated_at: null, restart_pending: ["max_concurrent_runs"], locked: ["max_concurrent_runs"], overridden: ["execution_mode"] }], connectors: 1, llm_profiles: 2, secret_store: true, environment: "lab" };

function mount(routes: Record<string, (init?: RequestInit) => Response>, user = USER, path = "/settings/agent") {
  tokenStore.set("tok", new Date(Date.now() + 3_600_000).toISOString());
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      if (url.startsWith("/api/auth/me")) return json(user);
      const key = Object.keys(routes).find((k) => url === k || url.startsWith(k + "?"));
      return key ? routes[key](init) : json({ error: "not_found", message: `mock yok: ${url}` }, 404);
    }),
  );
  const router = createMemoryRouter([{ path: "/settings/:section?", element: <SettingsPage /> }], { initialEntries: [path] });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  );
  return calls;
}

describe("Settings", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    tokenStore.clear();
  });

  it("kaynak ve lock rozetlerini gösterir; lock olan alan değiştirilemez", async () => {
    mount({ "/api/settings/agent": () => json(AGENT), "/api/settings": () => json(OVERVIEW) });
    const field = await screen.findByLabelText("LangGraph recursion_limit");
    expect(field).not.toBeDisabled();
    expect(screen.getByLabelText("Eşzamanlı investigation")).toBeDisabled();
    expect(screen.getByText("Locked")).toBeInTheDocument();
    expect(screen.getByText("Restart gerekli")).toBeInTheDocument();
    expect(screen.getByText("env: ANTHROPIC_API_KEY")).toBeInTheDocument();
    expect(screen.getByText(/Revizyon 3/)).toBeInTheDocument();
    expect(screen.getByLabelText("restart bekliyor")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Kaydet/ })).toBeDisabled();
  });

  it("yalnız değişen alanı revizyonla gönderir; 409'da taslağı korur ve uyarır", async () => {
    const bodies: unknown[] = [];
    mount({
      "/api/settings/agent": (init) => {
        if (init?.method === "PATCH") {
          bodies.push(JSON.parse(String(init.body)));
          return json({ error: "revision_conflict", message: "başkası kaydetti", details: { current_revision: 4 } }, 409);
        }
        return json(AGENT);
      },
      "/api/settings": () => json(OVERVIEW),
    });
    const u = userEvent.setup();
    const field = await screen.findByLabelText("LangGraph recursion_limit");
    await u.clear(field);
    await u.type(field, "40");
    expect(screen.getByText(/1 alan değişti/)).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: /Kaydet/ }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ revision: 3, values: { recursion_limit: 40 }, reset: [] });
    expect(await screen.findByText(/güncel revizyon 4/)).toBeInTheDocument();
    expect(screen.getByLabelText("LangGraph recursion_limit")).toHaveValue(40);
    await u.click(screen.getByRole("button", { name: /Vazgeç/ }));
    expect(screen.getByLabelText("LangGraph recursion_limit")).toHaveValue(25);
  });

  it("sre rolü salt okunur görür", async () => {
    mount({ "/api/settings/agent": () => json(AGENT), "/api/settings": () => json(OVERVIEW) }, { ...USER, role: "sre" });
    expect(await screen.findByText(/Salt okunur: ayarları yalnız yönetici/)).toBeInTheDocument();
    expect(screen.getByLabelText("LangGraph recursion_limit")).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Kaydet/ })).toBeNull();
    const nav = screen.getByRole("navigation", { name: "Settings bölümleri" });
    expect(within(nav).getByText("MCP Servers")).toBeInTheDocument();
  });
});

const RISK = {
  section: "risk",
  revision: 0,
  updated_by: null,
  updated_at: null,
  values: {
    weights: { latency: 0.3, errors: 0.2, saturation: 0.2, lock: 0.2, resource: 0.1 },
    bands: { low: 25, medium: 50, high: 75 },
  },
  fields: {
    weights: { ...meta, label: "Bileşen ağırlıkları", kind: "object", value: null, source: "default" },
    bands: { ...meta, label: "Risk bantları", kind: "object", value: null, source: "default" },
  },
  status: {},
};

describe("Risk Scoring", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    tokenStore.clear();
  });
  it("nesne alanlarını düzenler ve ağırlık toplamını doğrular", async () => {
    mount({ "/api/settings/risk": () => json(RISK), "/api/settings": () => json(OVERVIEW) }, USER, "/settings/risk");
    const u = userEvent.setup();
    const lat = await screen.findByLabelText("ağırlık latency");
    expect(screen.getByText(/Toplam 1.000/)).toBeInTheDocument();
    await u.clear(lat);
    await u.type(lat, "0.5");
    expect(screen.getByText(/Toplam 1.200/)).toBeInTheDocument();
    expect(screen.getByLabelText("bant high")).toHaveValue(75);
  });
});

describe("görüntü tercihleri", () => {
  afterEach(() => setDisplayPrefs({ timezone: "Europe/Istanbul", date_format: "dd.MM.yyyy HH:mm" }));
  it("timezone ve tarih biçimini uygular", () => {
    const t = "2026-11-25T10:05:00Z";
    expect(fmtTime(t)).toBe("25.11.2026 13:05");
    setDisplayPrefs({ timezone: "UTC", date_format: "yyyy-MM-dd HH:mm" });
    expect(fmtTime(t)).toBe("2026-11-25 10:05");
  });
});
