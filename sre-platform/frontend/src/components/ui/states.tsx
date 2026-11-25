import { AlertTriangle, ChevronLeft, ChevronRight, CloudOff, Inbox, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import type { ApiError } from "@/services/api";
import { cn } from "@/lib/utils";
import { Button, Skeleton } from "./primitives";

export function EmptyState({ title, description, action, icon, className }: { title: string; description?: ReactNode; action?: ReactNode; icon?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-1.5 px-6 py-8 text-center", className)}>
      <div className="text-muted [&_svg]:size-5">{icon ?? <Inbox />}</div>
      <p className="text-[13px] font-medium text-ink">{title}</p>
      {description && <p className="max-w-md text-xs text-muted">{description}</p>}
      {action && <div className="mt-1.5">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  const e = error as ApiError;
  return (
    <div role="alert" className={cn("flex flex-col items-center gap-1.5 px-6 py-8 text-center", className)}>
      <AlertTriangle className="size-5 text-st-critical" />
      <p className="text-[13px] font-medium text-ink">Veri yüklenemedi</p>
      <p className="max-w-md text-xs text-muted">{e?.message ?? String(error)}</p>
      {e?.correlationId && <p className="font-mono text-[10px] text-muted">ref {e.correlationId}</p>}
      {onRetry && (
        <Button size="sm" variant="outline" onClick={onRetry} className="mt-1">
          <RefreshCw /> Tekrar dene
        </Button>
      )}
    </div>
  );
}

/** Kaynak erişilemez (Dynatrace/MCP, LangSmith): veri "yok" değil "alınamadı" — değer tahmin edilmez. */
export function UnavailableState({ title, detail, className }: { title: string; detail?: ReactNode; className?: string }) {
  return (
    <div role="status" className={cn("flex flex-col items-center gap-1.5 px-6 py-8 text-center", className)}>
      <CloudOff className="size-5 text-muted" />
      <p className="text-[13px] font-medium text-ink">{title}</p>
      {detail && <p className="max-w-md font-mono text-2xs text-muted">{detail}</p>}
    </div>
  );
}

/** Sayfa üstü bildirim şeridi (kısmi veri, kaynak hatası). Kart değil, tek satırlık durum. */
export function Notice({ tone, children, className }: { tone: "warning" | "critical" | "info"; children: ReactNode; className?: string }) {
  const c = {
    warning: "border-st-warning/50 [&>svg]:text-st-warning",
    critical: "border-st-critical/50 [&>svg]:text-st-critical",
    info: "border-accent/40 [&>svg]:text-accent",
  }[tone];
  return (
    <div role={tone === "critical" ? "alert" : "status"} className={cn("flex items-start gap-2 rounded-md border border-l-2 bg-surface px-3 py-2 text-xs text-ink-2", c, className)}>
      <AlertTriangle className="mt-px size-3.5 shrink-0" />
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export function TableSkeleton({ rows = 6, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div className="space-y-2.5 p-3" aria-busy="true" aria-label="yükleniyor">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex gap-3">
          {Array.from({ length: cols }).map((__, j) => (
            <Skeleton key={j} className={cn("h-4", j === 0 ? "w-2/5" : "flex-1")} />
          ))}
        </div>
      ))}
    </div>
  );
}

export function Pagination({ page, pages, total, onPage }: { page: number; pages: number; total: number; onPage: (p: number) => void }) {
  if (total === 0) return null;
  return (
    <div className="flex items-center justify-between border-t border-line px-3 py-1.5 text-xs text-muted">
      <span className="tabular-nums">{total} kayıt</span>
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

export interface Crumb {
  label: string;
  to?: string;
}

/** Sayfa başlığı: breadcrumb + kompakt başlık + meta satırı + sağda eylemler. Tüm ekranlarda aynı yapı. */
export function PageHeader({ title, description, actions, crumbs, meta }: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  crumbs?: Crumb[];
  meta?: ReactNode;
}) {
  return (
    <header className="mb-4">
      {crumbs && crumbs.length > 0 && (
        <nav aria-label="breadcrumb" className="mb-1 flex items-center gap-1 text-xs text-muted">
          {crumbs.map((c, i) => (
            <span key={i} className="inline-flex items-center gap-1">
              {i > 0 && <ChevronRight className="size-3" aria-hidden />}
              {c.to ? (
                <Link to={c.to} className="hover:text-ink">
                  {c.label}
                </Link>
              ) : (
                <span>{c.label}</span>
              )}
            </span>
          ))}
        </nav>
      )}
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <h1 className="text-base leading-6 font-semibold tracking-tight text-ink">{title}</h1>
          {description && <p className="text-xs text-muted">{description}</p>}
          {meta && <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">{meta}</div>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </header>
  );
}

/** Sayfa gövdesi: tutarlı dış boşluk ve maksimum genişlik. */
export function Page({ children, className, wide }: { children: ReactNode; className?: string; wide?: boolean }) {
  return <div className={cn("mx-auto w-full px-4 py-4 lg:px-6", wide ? "max-w-none" : "max-w-[1600px]", className)}>{children}</div>;
}
