import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Skeleton } from "./ui/primitives";

/** KPI kutusu. Değer yoksa "—" ve açıklama gösterilir; sayı uydurulmaz. */
export function StatTile({ label, value, sub, icon, loading, missing, className }: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  icon?: ReactNode;
  loading?: boolean;
  missing?: boolean;
  className?: string;
}) {
  return (
    <div role="group" aria-label={label} className={cn("rounded-lg border border-line bg-surface px-4 py-3", className)}>
      <div className="flex items-center justify-between text-xs text-muted">
        <span>{label}</span>
        {icon && <span className="[&_svg]:size-4">{icon}</span>}
      </div>
      {loading ? (
        <Skeleton className="mt-2 h-7 w-20" />
      ) : (
        <div className={cn("mt-1 text-2xl font-semibold tabular-nums", missing ? "text-muted" : "text-ink")} title={missing ? "Dynatrace'ten veri yok" : undefined}>
          {value}
        </div>
      )}
      {sub && <div className="mt-0.5 truncate text-xs text-muted">{sub}</div>}
    </div>
  );
}
