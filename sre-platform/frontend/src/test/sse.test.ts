import { describe, expect, it, vi } from "vitest";
import { SseParser, streamRun } from "@/services/sse";
import type { AgentEvent } from "@/types/api";

const frame = (seq: number, type: string, data: object = {}) =>
  `id: ${seq}\nevent: ${type}\ndata: ${JSON.stringify({ type, seq, ts: "t", data })}\n\n`;

function body(chunks: string[], { fail = false } = {}) {
  // pull tabanlı: önce parçalar okunur, SONRA hata/kapanış (start içinde error() kuyruğu siler)
  const enc = new TextEncoder();
  let i = 0;
  return new ReadableStream<Uint8Array>({
    pull(c) {
      if (i < chunks.length) c.enqueue(enc.encode(chunks[i++]));
      else if (fail) c.error(new TypeError("network"));
      else c.close();
    },
  });
}

describe("SseParser", () => {
  it("parça parça gelen çerçeveleri, yorumları ve CRLF'yi doğru ayrıştırır", () => {
    const p = new SseParser();
    expect(p.push(": ping\r\n\r\nid: 1\r\nevent: tok")).toEqual([]);
    const out = p.push('en\r\ndata: {"a":1}\r\n\r\ndata: x\ndata: y\n\n');
    expect(out).toEqual([
      { id: "1", event: "token", data: '{"a":1}' },
      { data: "x\ny" },
    ]);
  });
});

describe("streamRun", () => {
  it("koparsa Last-Event-ID ile yeniden bağlanır, tekrar gelen olayları atlar, done'da biter", async () => {
    const calls: (string | undefined)[] = [];
    const fetchImpl = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      const h = init?.headers as Record<string, string>;
      calls.push(h["Last-Event-ID"]);
      if (calls.length === 1) return new Response(body([frame(1, "investigation_started"), frame(2, "token", { text: "a" })], { fail: true }), { status: 200 });
      // sunucu 2'den sonrasını gönderir ama biri tekrar (2) gelir — istemci atlamalı
      return new Response(body([frame(2, "token", { text: "a" }), frame(3, "token", { text: "b" }), frame(4, "done")]), { status: 200 });
    }) as unknown as typeof fetch;
    const got: AgentEvent[] = [];
    const statuses: string[] = [];
    const last = await streamRun("/api/agent/stream/x", { fetchImpl, onEvent: (e) => got.push(e), onStatus: (s) => statuses.push(s), retryDelayMs: () => 0 });
    expect(calls).toEqual([undefined, "2"]);
    expect(got.map((e) => e.seq)).toEqual([1, 2, 3, 4]);
    expect(last).toBe(4);
    expect(statuses).toContain("reconnecting");
    expect(statuses.at(-1)).toBe("closed");
  });

  it("4xx kalıcı hatadır: tekrar denemez ve sunucu mesajını iletir", async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ message: "çalıştırma bulunamadı" }), { status: 404 })) as unknown as typeof fetch;
    await expect(streamRun("/x", { fetchImpl, onEvent: () => {}, retryDelayMs: () => 0 })).rejects.toThrow("çalıştırma bulunamadı");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("done gelmeden kapanan akış sınırlı sayıda yeniden denenir", async () => {
    const fetchImpl = vi.fn(async () => new Response(body([]), { status: 200 })) as unknown as typeof fetch;
    await expect(streamRun("/x", { fetchImpl, onEvent: () => {}, maxRetries: 2, retryDelayMs: () => 0 })).rejects.toThrow();
    expect(fetchImpl).toHaveBeenCalledTimes(3);
  });

  it("iptal (AbortSignal) sessizce kapanır", async () => {
    const ac = new AbortController();
    const fetchImpl = vi.fn(async () => {
      ac.abort();
      throw new DOMException("aborted", "AbortError");
    }) as unknown as typeof fetch;
    await expect(streamRun("/x", { fetchImpl, onEvent: () => {}, signal: ac.signal })).resolves.toBe(0);
  });
});
