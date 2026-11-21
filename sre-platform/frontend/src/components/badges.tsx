/* Durum rozetleri: renk ASLA tek başına anlam taşımaz — her rozette ikon + metin etiketi vardır. */
import { AlertOctagon, AlertTriangle, CheckCircle2, CircleDashed, CircleHelp, Clock, Loader2, ShieldAlert, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { appColor, appLabel } from "@/lib/colors";
import type { Confidence, HealthStatus, IncidentStatus, InvestigationStatus, Severity } from "@/types/api";

function Pill({ cls, icon, children, title }: { cls: string; icon: ReactNode; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium [&_svg]:size-3.5", cls)}>
      {icon}
      {children}
    </span>
  );
}

const ST = {
  good: "border-st-good/40 bg-st-good/10 text-st-good",
  warning: "border-st-warning/40 bg-st-warning/10 text-st-warning",
  serious: "border-st-serious/40 bg-st-serious/10 text-st-serious",
  critical: "border-st-critical/50 bg-st-critical/15 text-[#f08a8a]",
  unknown: "border-line-strong bg-surface-2 text-muted",
  info: "border-accent/40 bg-accent/10 text-[#8ab8f0]",
};

export function HealthBadge({ status, title }: { status: HealthStatus; title?: string }) {
  const m = {
    healthy: [ST.good, <CheckCircle2 key="i" />, "Sağlıklı"],
    degraded: [ST.warning, <AlertTriangle key="i" />, "Bozulmuş"],
    down: [ST.critical, <XCircle key="i" />, "Erişilemiyor"],
    unknown: [ST.unknown, <CircleHelp key="i" />, "Veri yok"],
  }[status] as [string, ReactNode, string];
  return (
    <Pill cls={m[0]} icon={m[1]} title={title}>
      {m[2]}
    </Pill>
  );
}

export const SEVERITY_LABEL: Record<Severity, string> = {
  critical: "Kritik",
  high: "Yüksek",
  medium: "Orta",
  low: "Düşük",
  unknown: "Belirsiz",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  const m: Record<Severity, [string, ReactNode]> = {
    critical: [ST.critical, <AlertOctagon key="i" />],
    high: [ST.serious, <ShieldAlert key="i" />],
    medium: [ST.warning, <AlertTriangle key="i" />],
    low: [ST.good, <CheckCircle2 key="i" />],
    unknown: [ST.unknown, <CircleHelp key="i" />],
  };
  return (
    <Pill cls={m[severity][0]} icon={m[severity][1]} title={severity === "unknown" ? "Veri kapsamı yetersiz; risk hesaplanamadı" : undefined}>
      {SEVERITY_LABEL[severity]}
    </Pill>
  );
}

export const INV_STATUS_LABEL: Record<InvestigationStatus, string> = {
  PENDING: "Sırada",
  RUNNING: "Çalışıyor",
  COMPLETED: "Tamamlandı",
  PARTIAL: "Kısmi veri",
  FAILED: "Başarısız",
  CANCELLED: "İptal",
};

export function InvestigationStatusBadge({ status }: { status: InvestigationStatus }) {
  const m: Record<InvestigationStatus, [string, ReactNode]> = {
    PENDING: [ST.unknown, <Clock key="i" />],
    RUNNING: [ST.info, <Loader2 key="i" className="animate-spin" />],
    COMPLETED: [ST.good, <CheckCircle2 key="i" />],
    PARTIAL: [ST.warning, <AlertTriangle key="i" />],
    FAILED: [ST.critical, <XCircle key="i" />],
    CANCELLED: [ST.unknown, <CircleDashed key="i" />],
  };
  return (
    <Pill cls={m[status][0]} icon={m[status][1]}>
      {INV_STATUS_LABEL[status]}
    </Pill>
  );
}

export const INC_STATUS_LABEL: Record<IncidentStatus, string> = {
  OPEN: "Açık",
  INVESTIGATING: "İnceleniyor",
  MITIGATED: "Hafifletildi",
  RESOLVED: "Çözüldü",
};

export function IncidentStatusBadge({ status }: { status: IncidentStatus }) {
  const m: Record<IncidentStatus, [string, ReactNode]> = {
    OPEN: [ST.critical, <AlertOctagon key="i" />],
    INVESTIGATING: [ST.info, <Loader2 key="i" />],
    MITIGATED: [ST.warning, <ShieldAlert key="i" />],
    RESOLVED: [ST.good, <CheckCircle2 key="i" />],
  };
  return (
    <Pill cls={m[status][0]} icon={m[status][1]}>
      {INC_STATUS_LABEL[status]}
    </Pill>
  );
}

export const CONF_LABEL: Record<Confidence, string> = { high: "Yüksek güven", medium: "Orta güven", low: "Düşük güven" };

export function ConfidenceBadge({ confidence }: { confidence: Confidence | null | undefined }) {
  if (!confidence) return <span className="text-xs text-muted">güven —</span>;
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-ink-2">
      <span className="flex gap-0.5" aria-hidden>
        {[0, 1, 2].map((i) => (
          <span key={i} className={cn("h-2.5 w-1 rounded-sm", i < { low: 1, medium: 2, high: 3 }[confidence] ? "bg-ink-2" : "bg-line-strong")} />
        ))}
      </span>
      {CONF_LABEL[confidence]}
    </span>
  );
}

/** Uygulama etiketi: metin her zaman nötr mürekkep; renk yalnız yanındaki işarette. */
export function AppTag({ id, className }: { id: string; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-xs text-ink-2", className)}>
      <span aria-hidden className="size-2 rounded-full" style={{ background: appColor(id) }} />
      {appLabel(id)}
    </span>
  );
}
