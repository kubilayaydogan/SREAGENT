import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FocusBar } from "@/features/chat/FocusBar";
import { tokenStore } from "@/services/api";

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
}

const WM = {
  focus: {
    apps: ["checkin-service"],
    mechanism: "row_lock",
    window: { start: "2026-11-25T10:00:00Z", end: "2026-11-25T10:30:00Z" },
    investigation: "inv:0b1c2d3e-0000-4000-8000-000000000001",
  },
  digests: [{ turn: 1, at: "2026-11-25T10:31:00Z", question: "Checkin neden yavaşladı?", intent: "analyze", apps: [], investigation: null, mechanism: "row_lock", answer_head: "…" }],
  messages_in_state: 2,
  max_messages: 20,
  max_digests: 30,
  pending: [],
};

function mount(props: { busy?: boolean; canEdit?: boolean }, focus: unknown = WM.focus) {
  tokenStore.set("tok", new Date(Date.now() + 3_600_000).toISOString());
  const calls: { url: string; method: string }[] = [];
  let current = focus;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ url, method });
      if (url === "/api/conversations/c1/memory/focus" && method === "DELETE") {
        current = null;
        return new Response(null, { status: 204 });
      }
      if (url === "/api/conversations/c1/memory") return json({ ...WM, focus: current });
      return json({ error: "not_found" }, 404);
    }),
  );
  const router = createMemoryRouter(
    [{ path: "/", element: <FocusBar conversationId="c1" busy={props.busy ?? false} canEdit={props.canEdit ?? true} /> }],
    { initialEntries: ["/"] },
  );
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return calls;
}

describe("FocusBar (working memory)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    tokenStore.clear();
  });

  it("odağı gösterir ve temizler", async () => {
    const calls = mount({});
    expect(await screen.findByText(/checkin-service/)).toBeInTheDocument();
    expect(screen.getByText(/row_lock/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /inv:0b1c2d3e/ })).toHaveAttribute("href", "/investigations/0b1c2d3e-0000-4000-8000-000000000001");
    await userEvent.setup().click(screen.getByRole("button", { name: "Odağı temizle" }));
    await waitFor(() => expect(screen.queryByText(/checkin-service/)).not.toBeInTheDocument());
    expect(calls.some((c) => c.method === "DELETE")).toBe(true);
  });

  it("salt okunur rolde temizleme düğmesi yok", async () => {
    mount({ canEdit: false });
    await screen.findByText(/checkin-service/);
    expect(screen.queryByRole("button", { name: "Odağı temizle" })).not.toBeInTheDocument();
  });

  it("tur sürerken temizleme devre dışı", async () => {
    mount({ busy: true });
    expect(await screen.findByRole("button", { name: "Odağı temizle" })).toBeDisabled();
  });

  it("odak boşsa görünmez", async () => {
    const calls = mount({}, null);
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(screen.queryByText(/Konuşma odağı/)).not.toBeInTheDocument();
  });
});
