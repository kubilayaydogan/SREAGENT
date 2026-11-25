/* Durum göstergeleri. Renk ASLA tek başına anlam taşımaz: her göstergede ikon + metin vardır.
   Varsayılan dil sade: renkli ikon + nötr metin. Tonlu zemin yalnız dikkat isteyen durumlarda (kritik, açık incident). */
import { AlertOctagon, AlertTriangle, CheckCircle2, CircleDashed, CircleHelp, CircleDot, Clock, Loader2, ShieldAlert, XCircle } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { appColor, appLabel } from "@/lib/colors";
import type { Confidence, HealthStatus, IncidentStatus, InvestigationStatus, Severity } from "@/types/api";

type Tone = "good" | "warning" | "serious" | "critical" | "unknown" | "info";

const ICON_TONE: Record<Tone, string> = {
  good: "text-st-good",
  warning: "text-st-warning",
  serious: "text-st-serious",
  critical: "text-st-critical",
  unknown: "text-st-unknown",
  info: "text-accent",
};

const TEXT_TONE: Record<Tone, string> = {
  good: "text-ink-2",
  warning: "text-ink-2",
  serious: "text-st-serious-ink",
  critical: "text-st-critical-ink",
  unknown: "text-muted",
  info: "text-ink-2",
};

export function StatusLabel({ tone, icon, children, title, emphasis, className }: {
  tone: Tone;
  icon: ReactNode;
  children: ReactNode;
  title?: string;
  /** dikkat isteyen durum: tonlu zemin */
  emphasis?: boolean;
  className?: string;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1 text-xs whitespace-nowrap [&_svg]:size-3.5 [&_svg]:shrink-0",
        emphasis ? "rounded-sm px-1.5 py-px font-medium" : "",
        emphasis && tone === "critical" && "bg-st-critical/12",
        emphasis && tone === "serious" && "bg-st-serious/12",
        TEXT_TONE[tone],
        className,
      )}
    >
      <span className={ICON_TONE[tone]}>{icon}</span>
      {children}
    </span>
  );
}

export const HEALTH_LABEL: Record<HealthStatus, string> = { healthy: "Sağlıklı", degraded: "Bozulmuş", down: "Erişilemiyor", unknown: "Veri yok" };

export function HealthBadge({ status, title }: { status: HealthStatus; title?: string }) {
  const m: Record<HealthStatus, [Tone, ReactNode]> = {
    healthy: ["good", <CheckCircle2 key="i" />],
    degraded: ["warning", <AlertTriangle key="i" />],
    down: ["critical", <XCircle key="i" />],
    unknown: ["unknown", <CircleHelp key="i" />],
  };
  return (
    <StatusLabel tone={m[status][0]} icon={m[status][1]} title={title} emphasis={status === "down"}>
      {HEALTH_LABEL[status]}
    </StatusLabel>
  );
}

export const SEVERITY_LABEL: Record<Severity, string> = {
  critical: "Kritik",
  high: "Yüksek",
  medium: "Orta",
  low: "Düşük",
  unknown: "Belirsiz",
};

export const SEVERITY_RANK: Record<Severity, number> = { critical: 4, high: 3, medium: 2, low: 1, unknown: 0 };

export function SeverityBadge({ severity }: { severity: Severity }) {
  const m: Record<Severity, [Tone, ReactNode]> = {
    critical: ["critical", <AlertOctagon key="i" />],
    high: ["serious", <ShieldAlert key="i" />],
    medium: ["warning", <AlertTriangle key="i" />],
    low: ["good", <CircleDot key="i" />],
    unknown: ["unknown", <CircleHelp key="i" />],
  };
  return (
    <StatusLabel
      tone={m[severity][0]}
      icon={m[severity][1]}
      emphasis={severity === "critical"}
      title={severity === "unknown" ? "Data coverage yetersiz; risk score hesaplanamadı" : undefined}
    >
      {SEVERITY_LABEL[severity]}
    </StatusLabel>
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
  const m: Record<InvestigationStatus, [Tone, ReactNode]> = {
    PENDING: ["unknown", <Clock key="i" />],
    RUNNING: ["info", <Loader2 key="i" className="animate-spin" />],
    COMPLETED: ["good", <CheckCircle2 key="i" />],
    PARTIAL: ["warning", <AlertTriangle key="i" />],
    FAILED: ["critical", <XCircle key="i" />],
    CANCELLED: ["unknown", <CircleDashed key="i" />],
  };
  return (
    <StatusLabel tone={m[status][0]} icon={m[status][1]}>
      {INV_STATUS_LABEL[status]}
    </StatusLabel>
  );
}

export const INC_STATUS_LABEL: Record<IncidentStatus, string> = {
  OPEN: "Açık",
  INVESTIGATING: "İnceleniyor",
  MITIGATED: "Hafifletildi",
  RESOLVED: "Çözüldü",
};

export function IncidentStatusBadge({ status }: { status: IncidentStatus }) {
  const m: Record<IncidentStatus, [Tone, ReactNode]> = {
    OPEN: ["critical", <AlertOctagon key="i" />],
    INVESTIGATING: ["info", <CircleDot key="i" />],
    MITIGATED: ["warning", <ShieldAlert key="i" />],
    RESOLVED: ["good", <CheckCircle2 key="i" />],
  };
  return (
    <StatusLabel tone={m[status][0]} icon={m[status][1]} emphasis={status === "OPEN"}>
      {INC_STATUS_LABEL[status]}
    </StatusLabel>
  );
}

export const CONF_LABEL: Record<Confidence, string> = { high: "Yüksek güven", medium: "Orta güven", low: "Düşük güven" };

/** Güven seviyesi: 3 basamaklı ölçek + etiket (renk değil, doluluk). */
export function ConfidenceBadge({ confidence }: { confidence: Confidence | null | undefined }) {
  if (!confidence) return <span className="text-xs text-muted">güven —</span>;
  const n = { low: 1, medium: 2, high: 3 }[confidence];
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-ink-2" aria-label={`${CONF_LABEL[confidence]} (${n}/3)`}>
      <span className="flex items-end gap-0.5" aria-hidden>
        {[0, 1, 2].map((i) => (
          <span key={i} className={cn("w-1 rounded-[1px]", ["h-1.5", "h-2", "h-2.5"][i], i < n ? "bg-ink-2" : "bg-line-strong")} />
        ))}
      </span>
      {CONF_LABEL[confidence]}
    </span>
  );
}

/** Uygulama etiketi: metin her zaman nötr; renk yalnız yanındaki işarette (varlık rengi, tüm ekranlarda aynı). */
export function AppTag({ id, className }: { id: string; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-xs whitespace-nowrap text-ink-2", className)}>
      <span aria-hidden className="size-2 shrink-0 rounded-[2px]" style={{ background: appColor(id) }} />
      {appLabel(id)}
    </span>
  );
}
