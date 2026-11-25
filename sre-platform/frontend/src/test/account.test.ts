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
  it("backend'deki 15 permission'ın hepsinin etiketi var", () => {
    expect(Object.keys(PERM_LABEL)).toHaveLength(15);
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
