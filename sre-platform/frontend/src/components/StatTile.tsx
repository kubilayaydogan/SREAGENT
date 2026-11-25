import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Skeleton } from "./ui/primitives";

/** KPI hücresi. Ayrı kart değildir: MetricStrip içinde kenarlıklarla ayrılır. Değer yoksa "—" ve açıklama;
 *  sayı uydurulmaz. tone yalnız dikkat gerektiren durumda verilir (renk + ikon/metin). */
export function StatTile({ label, value, sub, icon, loading, missing, className, tone, unit }: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  icon?: ReactNode;
  loading?: boolean;
  missing?: boolean;
  className?: string;
  tone?: string;
  unit?: ReactNode;
}) {
  return (
    <div role="group" aria-label={label} className={cn("min-w-0 px-3 py-2.5", className)}>
      <div className="flex items-center gap-1.5 text-xs text-muted">
        {icon && <span className="[&_svg]:size-3.5">{icon}</span>}
        <span className="truncate">{label}</span>
      </div>
      {loading ? (
        <Skeleton className="mt-1.5 h-6 w-16" />
      ) : (
        <div
          className={cn("mt-0.5 flex items-baseline gap-1 text-xl leading-7 font-semibold tracking-tight tabular-nums", missing ? "text-muted" : (tone ?? "text-ink"))}
          title={missing ? "Kaynaktan veri yok" : undefined}
        >
          {value}
          {unit && !missing && <span className="text-xs font-normal text-muted">{unit}</span>}
        </div>
      )}
      {sub && <div className="truncate text-xs text-muted">{sub}</div>}
    </div>
  );
}

/** KPI şeridi: tek kenarlıklı yüzey, hücreler arası ince çizgi. Her metric için ayrı kart açılmaz. */
export function MetricStrip({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "grid gap-px overflow-hidden rounded-lg border border-line bg-line [&>*]:bg-surface",
        className,
      )}
    >
      {children}
    </div>
  );
}
