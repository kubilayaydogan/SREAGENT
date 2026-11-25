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
