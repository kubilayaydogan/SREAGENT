import { Search } from "lucide-react";
import { useEffect, useState } from "react";
import { useApplications } from "@/hooks/queries";
import { appLabel } from "@/lib/colors";
import { cn } from "@/lib/utils";
import type { RangeKey } from "@/types/api";
import { Input, Select } from "./ui/primitives";

export function AppSelect({ value, onChange, allLabel = "Tüm uygulamalar", className }: { value: string; onChange: (v: string) => void; allLabel?: string; className?: string }) {
  const { data } = useApplications();
  return (
    <Select aria-label="uygulama" value={value} onChange={(e) => onChange(e.target.value)} className={cn("w-48", className)}>
      <option value="">{allLabel}</option>
      {(data ?? []).map((a) => (
        <option key={a.id} value={a.id}>
          {appLabel(a.id)}
        </option>
      ))}
    </Select>
  );
}

export const RANGES: { value: RangeKey; label: string }[] = [
  { value: "15m", label: "Son 15 dk" },
  { value: "1h", label: "Son 1 saat" },
  { value: "6h", label: "Son 6 saat" },
  { value: "24h", label: "Son 24 saat" },
];

/** Segmentli zaman aralığı seçici (grafiklerin üstünde, tek satır). */
export function RangeTabs({ value, onChange }: { value: RangeKey; onChange: (v: RangeKey) => void }) {
  return (
    <div role="radiogroup" aria-label="zaman aralığı" className="inline-flex rounded-md border border-line-strong bg-surface-2 p-0.5">
      {RANGES.map((r) => (
        <button
          key={r.value}
          role="radio"
          aria-checked={value === r.value}
          onClick={() => onChange(r.value)}
          className={cn(
            "rounded px-2.5 py-1 text-xs font-medium transition-colors",
            value === r.value ? "bg-surface-3 text-ink" : "text-muted hover:text-ink",
          )}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}

/** Gecikmeli (debounce) arama kutusu. */
export function SearchInput({ value, onChange, placeholder = "Ara…", className }: { value: string; onChange: (v: string) => void; placeholder?: string; className?: string }) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  useEffect(() => {
    if (v === value) return;
    const t = setTimeout(() => onChange(v), 350);
    return () => clearTimeout(t);
  }, [v]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className={cn("relative w-64", className)}>
      <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted" />
      <Input aria-label="ara" value={v} onChange={(e) => setV(e.target.value)} placeholder={placeholder} className="pl-8" />
    </div>
  );
}
