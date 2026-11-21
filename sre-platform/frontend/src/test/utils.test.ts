import { describe, expect, it } from "vitest";
import { fmtDuration, fmtMs, fmtNum, fmtPct, qs } from "@/lib/utils";

describe("biçimlendirme — eksik veri asla 0 olarak gösterilmez", () => {
  it.each([null, undefined])("%s → —", (v) => {
    expect(fmtNum(v)).toBe("—");
    expect(fmtMs(v)).toBe("—");
    expect(fmtPct(v)).toBe("—");
    expect(fmtDuration(v)).toBe("—");
  });
  it("gerçek sıfır gösterilir", () => {
    expect(fmtPct(0)).toBe("%0");
    expect(fmtNum(0)).toBe("0");
  });
  it("birimler", () => {
    expect(fmtMs(4.25)).toBe("4,3 ms");
    expect(fmtMs(250)).toBe("250 ms");
    expect(fmtMs(12_500)).toBe("12,5 sn");
    expect(fmtPct(0.0476, 2)).toBe("%4,76");
    expect(fmtDuration(95_000)).toBe("1 dk 35 sn");
  });
  it("qs boş değerleri atar, dizileri virgülle birleştirir", () => {
    expect(qs({ a: "", b: null, c: undefined, d: [], e: ["OPEN", "MITIGATED"], f: 2 })).toBe("?e=OPEN%2CMITIGATED&f=2");
    expect(qs({})).toBe("");
  });
});
