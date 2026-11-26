import { describe, expect, it } from "vitest";
import { uaLabel } from "@/features/account/api";
import { PERM_LABEL } from "@/features/account/permissions";
import { keyFormProblem } from "@/features/settings/ApiKeys";

describe("account", () => {
  it("user-agent etiketleri", () => {
    expect(uaLabel(null)).toBe("bilinmeyen istemci");
    expect(uaLabel("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36")).toBe("Chrome · Windows");
    expect(uaLabel("Mozilla/5.0 (Windows NT 10.0) Chrome/131.0 Safari/537.36 Edg/131.0")).toBe("Edge · Windows");
    expect(uaLabel("python-httpx/0.28")).toBe("API istemcisi");
  });
  it("backend'deki 18 permission'ın hepsinin etiketi var", () => {
    expect(Object.keys(PERM_LABEL)).toHaveLength(18);
  });
  it("API anahtarı form doğrulaması", () => {
    expect(keyFormProblem("github-actions", ["record:read"], 90, 365)).toBeNull();
    expect(keyFormProblem("ab", ["record:read"], 90, 365)).toMatch(/Ad/);
    expect(keyFormProblem("ci<script>", ["record:read"], 90, 365)).toMatch(/Ad/);
    expect(keyFormProblem("ci-bot", [], 90, 365)).toMatch(/scope/);
    expect(keyFormProblem("ci-bot", ["record:read"], 0, 365)).toMatch(/Geçerlilik/);
    expect(keyFormProblem("ci-bot", ["record:read"], 366, 365)).toMatch(/Geçerlilik/);
  });
});

describe("SIEM durumu", () => {
  const base = { enabled: true, signed: false, cursor: 10, lag: 0, started_at: null, last_ok: null, failing: false as const };
  it("kapalı / açık / hata / birikme", async () => {
    const { siemState } = await import("@/features/settings/Security");
    expect(siemState({ ...base, enabled: false }).tone).toBe("off");
    expect(siemState(base)).toEqual({ tone: "ok", text: "Açık · bekleyen 0 olay" });
    expect(siemState({ ...base, cursor: null }).text).toMatch(/ilk tur/);
    expect(siemState({ ...base, lag: 5000 }).tone).toBe("warn");
    expect(siemState({ ...base, lag: 3, failing: { since: "x", error: "HTTP 503" } })).toEqual({ tone: "warn", text: "Teslim başarısız (HTTP 503); 3 olay bekliyor" });
  });
});

describe("secret süre sonu", () => {
  it("seçim → API alanı", async () => {
    const { expiryPayload, LIFECYCLE_LABEL } = await import("@/features/settings/Security");
    expect(expiryPayload("keep", "")).toEqual({});
    expect(expiryPayload("none", "")).toEqual({ expires_at: null });
    expect(expiryPayload("date", "2027-03-01")).toEqual({ expires_at: "2027-03-01T23:59:59Z" });
    expect(Object.keys(LIFECYCLE_LABEL).sort()).toEqual(["expired", "expiring", "ok", "rotation_due"]);
  });
});

describe("retention önizleme", () => {
  it("backend kategorilerinin hepsinin etiketi var", async () => {
    const { RETENTION_LABEL } = await import("@/features/settings/sections");
    for (const k of ["investigations", "watch_events", "job_runs", "llm_runs", "llm_usage", "auth_login_failures", "checkpoint_threads_orphan", "checkpoint_threads_ttl", "store_items", "trace_links"])
      expect(RETENTION_LABEL[k]).toBeTruthy();
  });
});

describe("MCP çağrısı → LangSmith span linki", () => {
  it("yalnız span yazılmış çağrıda şablondan link üretir", async () => {
    const { callTraceUrl } = await import("@/components/RecordTables");
    const base = { id: "c1", step: "collect", tool_name: "execute-dql", signal: "http", execution_status: "SUCCEEDED" as const, result_status: "OK", record_count: 1, dynatrace_query_id: null, evidence_key: null, started_at: null, duration_ms: 1, error_message: null, created_at: "" };
    expect(callTraceUrl({ ...base, tool_arguments: { langsmith_span: true } }, "https://ls/r/{call_id}")).toBe("https://ls/r/c1");
    expect(callTraceUrl({ ...base, tool_arguments: {} }, "https://ls/r/{call_id}")).toBeNull();
    expect(callTraceUrl({ ...base, tool_arguments: { langsmith_span: true } }, null)).toBeNull();
  });
});
