/* Risk score GÖSTERİMİ. Hesaplama backend'dedir (airlab_agent/scoring.py, deterministik) ve burada tekrarlanmaz;
   ağırlık ve bant eşikleri yalnız bileşen katkısını açıklamak için sre-agent/src/airlab_agent/defaults.yaml
   `score:` bölümünün aynısıdır. Orada değişirse burada da güncellenmelidir. */
import type { Severity } from "@/types/api";

export const RISK_WEIGHTS = { latency: 0.3, errors: 0.25, saturation: 0.2, lock: 0.15, resource: 0.1 } as const;
export type RiskComponent = keyof typeof RISK_WEIGHTS;
export const RISK_COMPONENTS = Object.keys(RISK_WEIGHTS) as RiskComponent[];
export const RISK_BANDS = { low: 25, medium: 50, high: 75 } as const;

export const COMPONENT_LABEL: Record<RiskComponent, string> = {
  latency: "Latency",
  errors: "Error rate",
  saturation: "Saturation",
  lock: "Lock wait",
  resource: "Resource (CPU)",
};

export const COMPONENT_HINT: Record<RiskComponent, string> = {
  latency: "p95 (span) ya da ortalama sürenin önceki döneme oranı; ×1.2 → 0, ×3 → 1",
  errors: "5xx oranı; %0.5 → 0, %5 → 1",
  saturation: "max(connection pool, DB connection oranı); %75 → 0, %100 → 1",
  lock: "bekleyen oturum ortalaması; 0 → 0, 3 → 1",
  resource: "APP_VM CPU; %70 → 0, %95 → 1",
};

export type RiskBand = "low" | "medium" | "high" | "critical";

export function bandOf(score: number | null | undefined): RiskBand | null {
  if (score == null || Number.isNaN(score)) return null;
  if (score < RISK_BANDS.low) return "low";
  if (score < RISK_BANDS.medium) return "medium";
  if (score < RISK_BANDS.high) return "high";
  return "critical";
}

export const BAND_LABEL: Record<RiskBand, string> = { low: "Düşük", medium: "Orta", high: "Yüksek", critical: "Kritik" };
export const BAND_SEVERITY: Record<RiskBand, Severity> = { low: "low", medium: "medium", high: "high", critical: "critical" };

/** Metin rengi: düşük bantta nötr; renk yalnız dikkat gereken bantlarda. */
export const BAND_TEXT: Record<RiskBand, string> = {
  low: "text-ink",
  medium: "text-st-warning-ink",
  high: "text-st-serious-ink",
  critical: "text-st-critical-ink",
};
export const BAND_FILL: Record<RiskBand, string> = {
  low: "bg-ink-2/50",
  medium: "bg-st-warning",
  high: "bg-st-serious",
  critical: "bg-st-critical",
};

export function toNum(v: string | number | null | undefined): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

export interface AppRisk {
  score: number | null;
  band: string | null;
  coverage: number;
  missing: string[];
  components: Partial<Record<RiskComponent, number | null>> & { _latency_ratio?: number | null; _latency_source?: string | null; _latency_delta_ms?: number | null };
}

/** Bileşenin skora katkısı (puan). combine(): 100 · Σ(w·v) / Σ(w, mevcut bileşenler). */
export function contributions(a: AppRisk): Partial<Record<RiskComponent, number>> {
  const avail = RISK_COMPONENTS.filter((k) => typeof a.components[k] === "number");
  const wsum = avail.reduce((s, k) => s + RISK_WEIGHTS[k], 0);
  const out: Partial<Record<RiskComponent, number>> = {};
  if (!wsum) return out;
  for (const k of avail) out[k] = (100 * RISK_WEIGHTS[k] * (a.components[k] as number)) / wsum;
  return out;
}
