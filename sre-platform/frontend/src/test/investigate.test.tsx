import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import InvestigatePage from "@/pages/InvestigatePage";
import { AuthProvider } from "@/services/auth";
import { tokenStore } from "@/services/api";

// SSE akışı: abort edilene kadar açık kalır, hiç olay göndermez (run sürüyor)
const streamRun = vi.fn((_url: string, o: { signal: AbortSignal }) => new Promise<void>((res) => o.signal.addEventListener("abort", () => res())));
vi.mock("@/services/sse", () => ({ streamRun: (url: string, o: { signal: AbortSignal }) => streamRun(url, o) }));

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
}

const SRE = { id: "u1", username: "nobetci", display_name: "Nöbetçi", role: "sre", is_active: true, last_login_at: null, created_at: "2026-11-21T00:00:00Z" };
const conv = (id: string, title: string, active: string | null = null) => ({
  id, title, thread_id: id, application_id: null, archived: false, created_at: "2026-11-26T19:00:00Z", updated_at: "2026-11-26T19:00:00Z",
  active_run_id: active, resumable: false, pending_steps: [],
});
const QUESTION = "Reservation uygulaması neden yavaşladı?";
Element.prototype.scrollIntoView = vi.fn();                                                      // jsdom'da yok

afterEach(() => {
  vi.unstubAllGlobals();
  tokenStore.clear();
  streamRun.mockClear();
});

describe("InvestigatePage", () => {
  it("yeni konuşmada sorulan sorunun bekleyen hali ve canlı yanıtı listedeki diğer konuşmaya taşınmaz", async () => {
    tokenStore.set("tok", new Date(Date.now() + 3_600_000).toISOString());
    const OLD = conv("c-old", "Son 5 dk nasıl");
    const NEW = conv("c-new", QUESTION, "run-new");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/auth/me")) return json(SRE);
      if (url.startsWith("/api/applications")) return json([]);
      if (init?.method === "POST" && url.startsWith("/api/agent/chat"))
        return json({ run_id: "run-new", kind: "chat", stream_url: "/api/agent/stream/run-new", conversation_id: "c-new" }, 202);
      if (url.startsWith("/api/conversations/c-old/messages") || url.startsWith("/api/conversations/c-new/messages")) return json({ items: [], total: 0, page: 1, size: 200, pages: 1 });
      if (url.startsWith("/api/conversations/c-old/memory") || url.startsWith("/api/conversations/c-new/memory")) return json({ focus: null, summaries: [], message_count: 0 });
      if (url.startsWith("/api/conversations/c-old")) return json(OLD);
      if (url.startsWith("/api/conversations/c-new")) return json(NEW);
      if (url.startsWith("/api/conversations")) return json({ items: [NEW, OLD], total: 2, page: 1, size: 30, pages: 1 });
      return json({ items: [], total: 0, page: 1, size: 30, pages: 1 });
    }));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <AuthProvider>
          <MemoryRouter initialEntries={["/investigate"]}>
            <Routes>
              <Route path="/investigate/:conversationId?" element={<InvestigatePage />} />
            </Routes>
          </MemoryRouter>
        </AuthProvider>
      </QueryClientProvider>,
    );
    const u = userEvent.setup();
    await u.click(await screen.findByRole("button", { name: QUESTION }));
    await waitFor(() => expect(streamRun).toHaveBeenCalledWith("/api/agent/stream/run-new", expect.anything()));
    expect(screen.getAllByText(QUESTION).length).toBeGreaterThan(0);

    // listedeki alttaki (eski) konuşmaya geç: yeni sorunun izi orada görünmemeli
    await u.click(await screen.findByRole("link", { name: /Son 5 dk nasıl/ }));
    await waitFor(() => expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Son 5 dk nasıl"));
    expect(screen.queryByText("çalışıyor")).toBeNull();
    expect(screen.getAllByText(QUESTION)).toHaveLength(1);                                     // yalnız sidebar başlığı
    expect(streamRun.mock.calls[0][1].signal.aborted).toBe(true);                              // bağlantı kapandı, run backend'de sürer

    // yeni konuşmaya dönünce süren run'a active_run_id üzerinden yeniden bağlanılır
    await u.click(screen.getByRole("link", { name: new RegExp(QUESTION.slice(0, 20)) }));
    await waitFor(() => expect(streamRun).toHaveBeenCalledTimes(2));
    expect(streamRun.mock.calls[1][0]).toBe("/api/agent/stream/run-new");
  });
});
