import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const NA = "—";

/** Sayı yoksa "—" döner; ASLA 0 uydurmaz. */
export function fmtNum(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return NA;
  return v.toLocaleString("tr-TR", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function fmtMs(v: number | null | undefined): string {
  if (v === null || v === undefined) return NA;
  if (v >= 10_000) return `${fmtNum(v / 1000, 1)} sn`;
  return `${fmtNum(v, v < 10 ? 1 : 0)} ms`;
}

export function fmtPct(ratio: number | null | undefined, digits = 1): string {
  if (ratio === null || ratio === undefined) return NA;
  return `%${fmtNum(ratio * 100, digits)}`;
}

export function fmtDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return NA;
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  if (s < 60) return `${fmtNum(s, 1)} sn`;
  return `${Math.floor(s / 60)} dk ${Math.round(s % 60)} sn`;
}

const TZ = "Europe/Istanbul";

export function fmtTime(iso: string | null | undefined, withDate = true): string {
  if (!iso) return NA;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return NA;
  return d.toLocaleString("tr-TR", {
    timeZone: TZ,
    ...(withDate ? { day: "2-digit", month: "2-digit", year: "numeric" } : {}),
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtRelative(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return NA;
  const diff = (now - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "az önce";
  if (diff < 3600) return `${Math.floor(diff / 60)} dk önce`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} sa önce`;
  return `${Math.floor(diff / 86400)} gün önce`;
}

export function shortId(id: string | null | undefined): string {
  return id ? id.slice(0, 8) : NA;
}

export function qs(params: Record<string, unknown>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "" || (Array.isArray(v) && v.length === 0)) continue;
    p.set(k, Array.isArray(v) ? v.join(",") : String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}
