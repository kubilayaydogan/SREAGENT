/** Uygulama renkleri VARLIĞA bağlıdır (sıraya değil): filtre seri sayısını değiştirse de renk değişmez.
 *  Değerler dataviz referans paletinin koyu-mod 1–4. slotlarıdır (validate_palette.js ile doğrulandı). */
export const APP_COLORS: Record<string, string> = {
  "reservation-service": "#3987e5",
  "checkin-service": "#d95926",
  "flightops-service": "#199e70",
  "baggage-service": "#c98500",
};
export const APP_ORDER = ["reservation-service", "checkin-service", "flightops-service", "baggage-service"];
export const FALLBACK_COLOR = "#9085e9";

export function appColor(id: string): string {
  return APP_COLORS[id] ?? FALLBACK_COLOR;
}

export function appLabel(id: string): string {
  return id.replace(/-service$/, "");
}

export const CHART = {
  grid: "#2c2c2a",
  axis: "#383835",
  muted: "#898781",
  ink: "#f0efec",
  ink2: "#c3c2b7",
  surface: "#1a1a19",
  /** tek seri (ör. PostgreSQL bağlantı oranı) — sıralı/tek-ton mavisi */
  single: "#3987e5",
};
