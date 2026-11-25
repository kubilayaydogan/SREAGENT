/* Liste ekranları için ortak parçalar: tablo üstü filtre şeridi, sunucu sıralamalı başlık, URL durumlu filtreler. */
import { ArrowDown, ArrowUp, SlidersHorizontal, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { cn } from "@/lib/utils";
import { Button } from "./ui/primitives";

/** URL query string'inde tutulan filtre durumu (paylaşılabilir, geri tuşu ile korunur). Filtre değişince sayfa 1'e döner. */
export function useUrlFilters() {
  const [sp, setSp] = useSearchParams();
  const get = (k: string) => sp.get(k) ?? "";
  const set = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v);
    else n.delete(k);
    if (k !== "page") n.delete("page");
    setSp(n, { replace: true });
  };
  const clear = (keys: string[]) => {
    const n = new URLSearchParams(sp);
    for (const k of [...keys, "page"]) n.delete(k);
    setSp(n, { replace: true });
  };
  return { sp, get, set, clear };
}

/** Tablo kartının üst şeridi: birincil filtreler tek satır, ikincil filtreler "Daha fazla" altında. */
export function FilterBar({ children, more, active, onClear, right }: {
  children: ReactNode;
  more?: ReactNode;
  /** aktif filtre sayısı (temizle düğmesi için) */
  active?: number;
  onClear?: () => void;
  right?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-b border-line">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        {children}
        {more && (
          <Button size="sm" variant={open ? "secondary" : "ghost"} onClick={() => setOpen((o) => !o)} aria-expanded={open}>
            <SlidersHorizontal /> Daha fazla
          </Button>
        )}
        {!!active && onClear && (
          <Button size="sm" variant="ghost" onClick={onClear}>
            <X /> Filtreleri temizle ({active})
          </Button>
        )}
        {right && <div className="ml-auto flex items-center gap-2">{right}</div>}
      </div>
      {more && open && <div className="flex flex-wrap items-end gap-2 border-t border-line bg-surface-2/50 px-3 py-2">{more}</div>}
    </div>
  );
}

/** Sunucu sıralamalı başlık. sort = "field" (artan) | "-field" (azalan). */
export function SortTh({ field, sort, onSort, children, num, className }: {
  field: string;
  sort: string;
  onSort: (s: string) => void;
  children: ReactNode;
  num?: boolean;
  className?: string;
}) {
  const active = sort === field || sort === `-${field}`;
  const desc = sort === `-${field}`;
  return (
    <th className={cn(num && "num", className)} aria-sort={active ? (desc ? "descending" : "ascending") : "none"}>
      <button
        type="button"
        className={cn("inline-flex items-center gap-0.5 hover:text-ink", active && "text-ink")}
        onClick={() => onSort(active && desc ? field : `-${field}`)}
      >
        {children}
        {active && (desc ? <ArrowDown className="size-3" /> : <ArrowUp className="size-3" />)}
      </button>
    </th>
  );
}

/** Tablo sarmalayıcı: yatay kaydırma + sticky header için dikey sınır. */
export function TableWrap({ children, fetching, maxH = "max-h-[calc(100vh-15rem)]" }: { children: ReactNode; fetching?: boolean; maxH?: string }) {
  return <div className={cn("overflow-auto transition-opacity duration-100", maxH, fetching && "opacity-60")}>{children}</div>;
}
