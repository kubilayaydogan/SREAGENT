import { CheckCircle2, CircleAlert, CircleDashed, Lock } from "lucide-react";
import { useEffect, type ReactNode } from "react";
import { useBlocker } from "react-router";
import { Badge, Button } from "@/components/ui/primitives";
import { Dialog } from "@/components/ui/overlay";
import { cn, fmtRelative, fmtTime } from "@/lib/utils";
import type { ApplyMode, FieldSource, LastCheck, SecretInfo } from "./api";

export const SOURCE_LABEL: Record<FieldSource, { label: string; title: string; cls: string }> = {
  locked: { label: "Locked", title: "Deployment lock (SRE_SETTINGS_LOCKS): arayüzden değiştirilemez", cls: "border-st-warning/60 text-ink" },
  admin: { label: "Admin", title: "Yönetici tarafından ayarlandı (ortam/varsayılanı ezer)", cls: "border-accent/60 text-ink" },
  env: { label: "Ortam", title: "Deployment ortam değişkeni / yapılandırma dosyasından", cls: "" },
  default: { label: "Varsayılan", title: "Uygulama varsayılanı", cls: "text-muted" },
};

export const APPLY_LABEL: Record<ApplyMode, { label: string; title: string }> = {
  next_run: { label: "Sonraki koşu", title: "Kaydedildikten sonra başlayan investigation'larda geçerli; süren koşuyu etkilemez" },
  next_tick: { label: "Sonraki tur", title: "Worker'ın bir sonraki zamanlayıcı turunda (≤5 sn) geçerli" },
  restart: { label: "Restart gerekli", title: "Süreç yeniden başlatılınca geçerli olur" },
};

export function SourceBadge({ source }: { source: FieldSource }) {
  const s = SOURCE_LABEL[source];
  return (
    <Badge title={s.title} className={cn("text-2xs", s.cls)}>
      {source === "locked" && <Lock className="size-3" aria-hidden />}
      {s.label}
    </Badge>
  );
}

export function ApplyBadge({ apply, pending }: { apply: ApplyMode; pending?: boolean }) {
  if (pending)
    return (
      <Badge title="Kaydedilen değer süreç yeniden başlatılınca uygulanacak" className="border-st-warning/60 text-2xs text-ink">
        Restart bekliyor
      </Badge>
    );
  if (apply === "next_run") return null;
  const a = APPLY_LABEL[apply];
  return (
    <Badge title={a.title} className="text-2xs text-muted">
      {a.label}
    </Badge>
  );
}

/** Bağlantı testinin son sonucu: hiç test edilmedi / başarılı / başarısız — sahte "bağlı" yok. */
export function CheckStatus({ check, compact }: { check: LastCheck | null | undefined; compact?: boolean }) {
  if (!check || (!check.at && check.ok === undefined))
    return (
      <span className="inline-flex items-center gap-1 text-xs text-muted">
        <CircleDashed className="size-3.5" aria-hidden /> Test edilmedi
      </span>
    );
  const ok = !!check.ok && !check.stale;
  return (
    <span className="inline-flex min-w-0 items-center gap-1 text-xs" title={check.message ?? undefined}>
      {ok ? (
        <CheckCircle2 className="size-3.5 shrink-0 text-st-good" aria-hidden />
      ) : (
        <CircleAlert className={cn("size-3.5 shrink-0", check.stale ? "text-st-warning" : "text-st-critical")} aria-hidden />
      )}
      <span className="truncate text-ink-2">
        {check.stale ? "Yapılandırma değişti, yeniden test edin" : ok ? "Doğrulandı" : `Başarısız (${check.kind ?? "hata"})`}
        {!compact && check.at && <span className="text-muted"> · {fmtRelative(check.at)}</span>}
      </span>
    </span>
  );
}

export function SecretRefBadge({ secret }: { secret: SecretInfo }) {
  if (!secret.ref) return <span className="text-xs text-muted">—</span>;
  const bad = secret.available === false;
  return (
    <span className="inline-flex items-center gap-1">
      <code className="font-mono text-xs text-ink-2">{secret.ref}</code>
      {bad && (
        <Badge title={secret.problem ?? ""} className="border-st-critical/60 text-2xs text-ink">
          {secret.problem ?? "çözülemiyor"}
        </Badge>
      )}
    </span>
  );
}

export function Toggle({ checked, onChange, disabled, label, id }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label: string; id?: string }) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors duration-100 disabled:opacity-50",
        checked ? "border-accent bg-accent" : "border-line-strong bg-surface-3",
      )}
    >
      <span className={cn("inline-block size-3.5 rounded-full bg-white shadow transition-transform duration-100", checked ? "translate-x-4.5" : "translate-x-0.5")} />
    </button>
  );
}

/** Kaydedilmemiş değişiklik varken sayfadan ayrılma uyarısı (route değişimi + sekme kapatma). */
export function useDirtyGuard(dirty: boolean) {
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname);
  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);
  return blocker;
}

export function LeaveDialog({ blocker }: { blocker: ReturnType<typeof useBlocker> }) {
  return (
    <Dialog
      open={blocker.state === "blocked"}
      onOpenChange={(o) => !o && blocker.reset?.()}
      title="Kaydedilmemiş değişiklikler"
      description="Bu bölümde kaydedilmemiş değişiklikler var. Ayrılırsanız kaybolur."
      footer={
        <>
          <Button onClick={() => blocker.reset?.()}>Kal</Button>
          <Button variant="danger" onClick={() => blocker.proceed?.()}>
            Değişiklikleri at ve ayrıl
          </Button>
        </>
      }
    >
      <p className="text-xs text-ink-2">Kaydetmek için “Kaydet” düğmesini kullanın.</p>
    </Dialog>
  );
}

export function ConfirmDialog({ open, onOpenChange, title, children, confirmLabel, onConfirm, danger, busy, disabled }: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  danger?: boolean;
  busy?: boolean;
  disabled?: boolean;
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>Vazgeç</Button>
          <Button variant={danger ? "danger" : "primary"} loading={busy} disabled={disabled} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="space-y-2 text-xs text-ink-2">{children}</div>
    </Dialog>
  );
}

export function SectionMeta({ revision, by, at }: { revision: number; by: string | null; at: string | null }) {
  if (!revision) return <span className="text-xs text-muted">Henüz admin değişikliği yok (ortam/varsayılan)</span>;
  return (
    <span className="text-xs text-muted" title={at ? fmtTime(at) : undefined}>
      Revizyon {revision} · {by ?? "?"} · {fmtRelative(at)}
    </span>
  );
}

/** Değeri okunabilir metne çevirir (devralınan değer, audit farkı). */
export function show(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "açık" : "kapalı";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}
