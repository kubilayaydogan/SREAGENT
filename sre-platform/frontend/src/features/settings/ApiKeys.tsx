import { Copy, KeyRound } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Badge, Button, Card, CardHeader, Input, Label } from "@/components/ui/primitives";
import { Dialog } from "@/components/ui/overlay";
import { EmptyState, ErrorState, Notice, TableSkeleton } from "@/components/ui/states";
import { type ApiKeyInfo, useAccountAction, useApiKeys } from "@/features/account/api";
import { PERM_LABEL } from "@/features/account/permissions";
import { fmtRelative, fmtTime } from "@/lib/utils";
import { api, type ApiError } from "@/services/api";
import { ConfirmDialog } from "./common";

const NAME_RE = /^[A-Za-z0-9._ -]{3,64}$/;

/** Oluşturma formu doğrulaması (sunucu ayrıca doğrular). */
export function keyFormProblem(name: string, scopes: string[], days: number, maxDays: number): string | null {
  if (!NAME_RE.test(name.trim())) return "Ad 3–64 karakter; harf, rakam, boşluk, . _ -";
  if (!scopes.length) return "En az bir scope seçin";
  if (!Number.isInteger(days) || days < 1 || days > maxDays) return `Geçerlilik 1–${maxDays} gün`;
  return null;
}

function CreateKeyDialog({ allowed, maxDays, onClose }: { allowed: string[]; maxDays: number; onClose: () => void }) {
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["record:read"]);
  const [days, setDays] = useState(90);
  const [err, setErr] = useState<string | null>(null);
  const [created, setCreated] = useState<ApiKeyInfo | null>(null);
  const create = useAccountAction<void, ApiKeyInfo>(() => api.post("/api-keys", { name: name.trim(), scopes, days }), "Anahtar oluşturulamadı");
  const problem = keyFormProblem(name, scopes, days, maxDays);
  if (created?.token)
    return (
      <Dialog
        open
        onOpenChange={(o) => !o && onClose()}
        title="Anahtar oluşturuldu"
        footer={
          <Button variant="primary" onClick={onClose}>
            Kopyaladım, kapat
          </Button>
        }
      >
        <div className="grid gap-3">
          <Notice tone="warning">Bu anahtar yalnız şimdi gösterilir; sunucuda düz hali tutulmaz. Kaybolursa iptal edip yenisini oluşturun.</Notice>
          <div className="flex items-center gap-2">
            <Input readOnly className="font-mono text-xs" value={created.token} onFocus={(e) => e.target.select()} aria-label="API anahtarı" />
            <Button
              size="sm"
              onClick={() => navigator.clipboard.writeText(created.token ?? "").then(() => toast.success("Kopyalandı"), () => toast.error("Kopyalanamadı; elle seçin"))}
            >
              <Copy /> Kopyala
            </Button>
          </div>
          <p className="text-xs text-muted">
            Kullanım: <code className="font-mono">Authorization: Bearer srek_…</code> · Bitiş: {fmtTime(created.expires_at)}
          </p>
        </div>
      </Dialog>
    );
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="API anahtarı oluştur"
      description="Otomasyon (CI, betik) için. Anahtar size bağlanır; etkin yetkisi seçilen scope'lar ile sizin o anki yetkilerinizin kesişimidir."
      wide
      footer={
        <>
          <Button onClick={onClose}>Vazgeç</Button>
          <Button
            variant="primary"
            disabled={!!problem}
            loading={create.isPending}
            onClick={() => create.mutate(undefined, { onSuccess: (r) => setCreated(r), onError: (e) => setErr((e as ApiError).message) })}
          >
            Oluştur
          </Button>
        </>
      }
    >
      <div className="grid gap-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <Label htmlFor="k-name">Ad</Label>
            <Input id="k-name" value={name} placeholder="ör. github-actions" onChange={(e) => setName(e.target.value)} />
          </div>
          <div>
            <Label htmlFor="k-days">Geçerlilik (gün, en çok {maxDays})</Label>
            <Input id="k-days" type="number" min={1} max={maxDays} value={days} onChange={(e) => setDays(Number(e.target.value))} />
          </div>
        </div>
        <fieldset>
          <legend className="mb-1 text-xs font-medium text-ink">Scope'lar</legend>
          <div className="grid gap-1.5 sm:grid-cols-2">
            {allowed.map((s) => (
              <label key={s} className="flex items-start gap-2 text-xs">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={scopes.includes(s)}
                  onChange={(e) => setScopes(e.target.checked ? [...scopes, s] : scopes.filter((x) => x !== s))}
                />
                <span>
                  {PERM_LABEL[s] ?? s} <code className="font-mono text-2xs text-muted">{s}</code>
                </span>
              </label>
            ))}
          </div>
          <p className="mt-1.5 text-2xs text-muted">Kullanıcı/secret/connector/LLM yönetimi, settings değişikliği ve onay kararı API anahtarına verilemez.</p>
        </fieldset>
        {(problem || err) && <Notice tone={err ? "critical" : "warning"}>{err ?? problem}</Notice>}
      </div>
    </Dialog>
  );
}

const STATUS_LABEL = { active: "etkin", expired: "süresi doldu", revoked: "iptal" } as const;

export function ApiKeysCard() {
  const [inactive, setInactive] = useState(false);
  const q = useApiKeys(inactive);
  const [create, setCreate] = useState(false);
  const [revoke, setRevoke] = useState<ApiKeyInfo | null>(null);
  const del = useAccountAction<string>((id) => api.del(`/api-keys/${id}`), "Anahtar iptal edilemedi", () => "Anahtar iptal edildi");
  return (
    <Card>
      <CardHeader
        title="Service account API anahtarları"
        description="Süre sonu zorunludur. Anahtar yalnız permission isteyen API uçlarında geçerlidir; oturum, parola ve sohbet uçlarına giremez. Her kullanım audit'te 'service' olarak görünür."
        actions={
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1.5 text-xs text-muted">
              <input type="checkbox" checked={inactive} onChange={(e) => setInactive(e.target.checked)} /> İptal / süresi dolanlar
            </label>
            <Button size="sm" variant="primary" disabled={!q.data} onClick={() => setCreate(true)}>
              <KeyRound /> Anahtar oluştur
            </Button>
          </div>
        }
      />
      {q.isLoading ? (
        <TableSkeleton rows={2} cols={5} />
      ) : q.isError || !q.data ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : q.data.items.length === 0 ? (
        <EmptyState title="API anahtarı yok" description="Otomasyon için kapsamı dar, süreli bir anahtar oluşturun." />
      ) : (
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead>
              <tr>
                <th>Ad</th>
                <th>Scope'lar</th>
                <th>Sahip</th>
                <th>Son kullanım</th>
                <th>Bitiş</th>
                <th>Durum</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {q.data.items.map((k) => (
                <tr key={k.id}>
                  <td className="text-xs">
                    <div className="text-ink">{k.name}</div>
                    <div className="font-mono text-2xs text-muted">srek_{k.prefix}_…</div>
                  </td>
                  <td className="text-xs">
                    <div className="flex flex-wrap gap-1">
                      {k.scopes.map((s) => (
                        <Badge key={s} className="font-mono text-2xs" title={PERM_LABEL[s]}>
                          {s}
                        </Badge>
                      ))}
                    </div>
                  </td>
                  <td className="text-xs">{k.owner ?? k.created_by}</td>
                  <td className="text-xs" title={k.last_used_ip ?? undefined}>
                    {k.last_used_at ? fmtRelative(k.last_used_at) : "hiç"}
                  </td>
                  <td className="text-xs">{fmtTime(k.expires_at)}</td>
                  <td className="text-xs">
                    <Badge className={k.status === "active" ? "text-2xs" : "border-st-critical/60 text-2xs"} title={k.revoked_reason ?? undefined}>
                      {STATUS_LABEL[k.status]}
                    </Badge>
                  </td>
                  <td className="text-right">
                    {k.status === "active" && (
                      <Button size="sm" variant="ghost" onClick={() => setRevoke(k)}>
                        İptal et
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {create && q.data && <CreateKeyDialog allowed={q.data.allowed_scopes} maxDays={q.data.max_days} onClose={() => setCreate(false)} />}
      <ConfirmDialog
        open={!!revoke}
        onOpenChange={(o) => !o && setRevoke(null)}
        title="Anahtar iptal edilsin mi?"
        danger
        confirmLabel="İptal et"
        busy={del.isPending}
        onConfirm={() => revoke && del.mutate(revoke.id, { onSuccess: () => setRevoke(null) })}
      >
        <p>
          <b>{revoke?.name}</b> anahtarını kullanan otomasyonlar hemen 401 almaya başlar. İptal geri alınamaz; kayıt audit için saklanır.
        </p>
      </ConfirmDialog>
    </Card>
  );
}
