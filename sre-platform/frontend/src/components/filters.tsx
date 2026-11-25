import { RefreshCw, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { useApplications } from "@/hooks/queries";
import { appLabel } from "@/lib/colors";
import { cn, fmtRelative } from "@/lib/utils";
import type { RangeKey } from "@/types/api";
import { Button, Input, Segmented, Select } from "./ui/primitives";

export function AppSelect({ value, onChange, allLabel = "Tüm servisler", className }: { value: string; onChange: (v: string) => void; allLabel?: string; className?: string }) {
  const { data } = useApplications();
  return (
    <Select aria-label="uygulama" value={value} onChange={(e) => onChange(e.target.value)} className={cn("w-40", className)}>
      <option value="">{allLabel}</option>
      {(data ?? []).map((a) => (
        <option key={a.id} value={a.id}>
          {appLabel(a.id)}
        </option>
      ))}
    </Select>
  );
}

export const RANGES: { value: RangeKey; label: string; title: string }[] = [
  { value: "15m", label: "15m", title: "Son 15 dakika" },
  { value: "1h", label: "1h", title: "Son 1 saat" },
  { value: "6h", label: "6h", title: "Son 6 saat" },
  { value: "24h", label: "24h", title: "Son 24 saat" },
];

/** Zaman aralığı seçici — tüm ekranlarda aynı kontrol (URL parametresinde tutulur). */
export function RangeTabs<T extends string = RangeKey>({ value, onChange, options }: {
  value: T;
  onChange: (v: T) => void;
  options?: { value: T; label: string; title?: string }[];
}) {
  return <Segmented label="zaman aralığı" value={value} onChange={onChange} options={(options ?? RANGES) as { value: T; label: string; title?: string }[]} />;
}

/** Yenile + son güncelleme zamanı. Otomatik yenileme sorgu tarafında (refetchInterval). */
export function RefreshControl({ onRefresh, fetching, updatedAt, auto }: { onRefresh: () => void; fetching?: boolean; updatedAt?: number; auto?: string }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 15_000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="flex items-center gap-1.5">
      {updatedAt ? (
        <span className="hidden text-xs text-muted sm:inline" title={auto ? `Otomatik yenileme: ${auto}` : undefined}>
          {fmtRelative(new Date(updatedAt).toISOString())}
        </span>
      ) : null}
      <Button size="icon" variant="outline" aria-label="yenile" title="Yenile" onClick={onRefresh} disabled={fetching}>
        <RefreshCw className={cn(fetching && "animate-spin")} />
      </Button>
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
    <div className={cn("relative w-56", className)}>
      <Search className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted" />
      <Input aria-label="ara" value={v} onChange={(e) => setV(e.target.value)} placeholder={placeholder} className="pl-7" />
    </div>
  );
}
