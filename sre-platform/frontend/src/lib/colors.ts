/** Varlık ve grafik renkleri — değerler index.css token'larındadır (light/dark ayrı doğrulanmış adımlar);
 *  burada yalnız CSS değişkeni referansı döner, böylece tema değişince grafikler de değişir.
 *  Uygulama rengi VARLIĞA bağlıdır (sıraya değil): filtre seri sayısını değiştirse de renk değişmez. */
export const APP_COLORS: Record<string, string> = {
  "reservation-service": "var(--color-app-reservation)",
  "checkin-service": "var(--color-app-checkin)",
  "flightops-service": "var(--color-app-flightops)",
  "baggage-service": "var(--color-app-baggage)",
};
export const APP_ORDER = ["reservation-service", "checkin-service", "flightops-service", "baggage-service"];
export const FALLBACK_COLOR = "var(--color-app-other)";

export function appColor(id: string): string {
  return APP_COLORS[id] ?? FALLBACK_COLOR;
}

export function appLabel(id: string): string {
  return id.replace(/-service$/, "");
}

export const CHART = {
  grid: "var(--color-chart-grid)",
  axis: "var(--color-chart-axis)",
  muted: "var(--color-muted)",
  ink: "var(--color-ink)",
  ink2: "var(--color-ink-2)",
  surface: "var(--color-surface)",
  baseline: "var(--color-chart-baseline)",
  /** tek seri (ör. risk score, PostgreSQL bağlantı oranı) — sıralı mavi */
  single: "var(--color-chart-single)",
};
