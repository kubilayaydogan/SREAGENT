/** fetch tabanlı SSE istemcisi. EventSource Authorization başlığı gönderemediği için fetch + ReadableStream kullanılır.
 *  Bağlantı koparsa `Last-Event-ID` ile kaldığı yerden yeniden bağlanır (backend olayları sıra numarasıyla saklar). */
import type { AgentEvent } from "@/types/api";
import { ApiError, authHeaders } from "./api";

export interface SseFrame {
  id?: string;
  event?: string;
  data: string;
}

/** Artımlı SSE ayrıştırıcı: parça parça gelen metni çerçevelere böler (yorum satırları — ': ping' — atlanır). */
export class SseParser {
  private buf = "";

  push(chunk: string): SseFrame[] {
    this.buf += chunk.replace(/\r\n?/g, "\n");
    const out: SseFrame[] = [];
    let idx: number;
    while ((idx = this.buf.indexOf("\n\n")) >= 0) {
      const block = this.buf.slice(0, idx);
      this.buf = this.buf.slice(idx + 2);
      const f: SseFrame = { data: "" };
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (!line || line.startsWith(":")) continue;
        const c = line.indexOf(":");
        const field = c < 0 ? line : line.slice(0, c);
        let value = c < 0 ? "" : line.slice(c + 1);
        if (value.startsWith(" ")) value = value.slice(1);
        if (field === "data") data.push(value);
        else if (field === "id") f.id = value;
        else if (field === "event") f.event = value;
      }
      if (data.length) {
        f.data = data.join("\n");
        out.push(f);
      }
    }
    return out;
  }
}

export interface StreamOptions {
  onEvent: (e: AgentEvent) => void;
  onStatus?: (s: "connecting" | "open" | "reconnecting" | "closed") => void;
  signal?: AbortSignal;
  maxRetries?: number;
  fetchImpl?: typeof fetch;
  retryDelayMs?: (attempt: number) => number;
}

/** Akışı `done` olayına (veya iptal / kalıcı hata) kadar okur. Son görülen sıra numarasını döner. */
export async function streamRun(url: string, opts: StreamOptions): Promise<number> {
  const f = opts.fetchImpl ?? fetch;
  const maxRetries = opts.maxRetries ?? 6;
  const delay = opts.retryDelayMs ?? ((n: number) => Math.min(1000 * 2 ** n, 10_000));
  let last = 0;
  let attempt = 0;
  for (;;) {
    opts.onStatus?.(attempt === 0 ? "connecting" : "reconnecting");
    let finished = false;
    try {
      const res = await f(url, {
        headers: { Accept: "text/event-stream", ...authHeaders(), ...(last ? { "Last-Event-ID": String(last) } : {}) },
        signal: opts.signal,
        cache: "no-store",
      });
      if (!res.ok || !res.body) {
        let msg = `Akış açılamadı (HTTP ${res.status})`;
        try {
          msg = (await res.json()).message ?? msg;
        } catch {
          /* yok */
        }
        // 4xx kalıcıdır (yetki / bulunamadı): tekrar deneme
        if (res.status >= 400 && res.status < 500) throw new ApiError(res.status, "stream_error", msg);
        throw new Error(msg);
      }
      opts.onStatus?.("open");
      const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
      const parser = new SseParser();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        for (const fr of parser.push(value)) {
          let ev: AgentEvent;
          try {
            ev = JSON.parse(fr.data);
          } catch {
            continue;
          }
          // Sunucu oturumu iptal etti (logout / parola değişikliği / admin): kalıcı hata, yeniden bağlanılmaz
          if ((ev.type as string) === "session_revoked") throw new ApiError(401, "session_revoked", "Oturum sona erdi; yeniden giriş yapın");
          if (typeof ev.seq === "number") {
            if (ev.seq <= last) continue; // yeniden bağlanmada tekrar gelen olay
            last = ev.seq;
          }
          attempt = 0; // yalnız gerçek ilerleme (yeni olay) deneme sayacını sıfırlar
          opts.onEvent(ev);
          if (ev.type === "done") finished = true;
        }
      }
      if (finished) {
        opts.onStatus?.("closed");
        return last;
      }
      throw new Error("akış beklenmedik biçimde kapandı");
    } catch (e) {
      if (opts.signal?.aborted || (e as Error).name === "AbortError") {
        opts.onStatus?.("closed");
        return last;
      }
      if (e instanceof ApiError || attempt >= maxRetries) {
        opts.onStatus?.("closed");
        throw e;
      }
      attempt += 1;
      opts.onStatus?.("reconnecting");
      await new Promise((r) => setTimeout(r, delay(attempt)));
    }
  }
}
