import { AlertTriangle, ChevronLeft, ChevronRight, Inbox, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import type { ApiError } from "@/services/api";
import { cn } from "@/lib/utils";
import { Button, Skeleton } from "./primitives";

export function EmptyState({ title, description, action, icon, className }: { title: string; description?: ReactNode; action?: ReactNode; icon?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-2 px-6 py-10 text-center", className)}>
      <div className="text-muted">{icon ?? <Inbox className="size-8" />}</div>
      <p className="text-sm font-medium text-ink">{title}</p>
      {description && <p className="max-w-md text-xs text-muted">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  const e = error as ApiError;
  return (
    <div role="alert" className={cn("flex flex-col items-center gap-2 px-6 py-8 text-center", className)}>
      <AlertTriangle className="size-7 text-st-critical" />
      <p className="text-sm font-medium text-ink">Veri yüklenemedi</p>
      <p className="max-w-md text-xs text-muted">{e?.message ?? String(error)}</p>
      {e?.correlationId && <p className="font-mono text-[10px] text-muted">ref {e.correlationId}</p>}
      {onRetry && (
        <Button size="sm" variant="outline" onClick={onRetry}>
          <RefreshCw /> Tekrar dene
        </Button>
      )}
    </div>
  );
}

export function TableSkeleton({ rows = 6, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div className="space-y-2 p-4" aria-busy="true" aria-label="yükleniyor">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex gap-3">
          {Array.from({ length: cols }).map((__, j) => (
            <Skeleton key={j} className={cn("h-5", j === 0 ? "w-2/5" : "flex-1")} />
          ))}
        </div>
      ))}
    </div>
  );
}

export function Pagination({ page, pages, total, onPage }: { page: number; pages: number; total: number; onPage: (p: number) => void }) {
  if (total === 0) return null;
  return (
    <div className="flex items-center justify-between border-t border-line px-4 py-2 text-xs text-muted">
      <span>{total} kayıt</span>
      <div className="flex items-center gap-1">
        <Button size="icon" variant="ghost" aria-label="önceki sayfa" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          <ChevronLeft />
        </Button>
        <span className="tabular-nums">
          {page} / {Math.max(pages, 1)}
        </span>
        <Button size="icon" variant="ghost" aria-label="sonraki sayfa" disabled={page >= pages} onClick={() => onPage(page + 1)}>
          <ChevronRight />
        </Button>
      </div>
    </div>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
