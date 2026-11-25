import { KeyRound, LogOut, MonitorSmartphone, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Card, CardBody, CardHeader, Input, Label } from "@/components/ui/primitives";
import { ErrorState, Notice, Page, PageHeader, TableSkeleton } from "@/components/ui/states";
import { uaLabel, useAccountAction, useMyPermissions, useMySessions } from "@/features/account/api";
import { PERM_LABEL, ROLE_LABEL } from "@/features/account/permissions";
import { ConfirmDialog } from "@/features/settings/common";
import { fmtRelative, fmtTime } from "@/lib/utils";
import { api, type ApiError } from "@/services/api";
import { useAuth } from "@/services/auth";

function SessionsCard() {
  const q = useMySessions();
  const { logout } = useAuth();
  const [confirmAll, setConfirmAll] = useState(false);
  const revoke = useAccountAction<string>((sid) => api.del(`/auth/sessions/${sid}`), "Oturum kapatılamadı", () => "Oturum kapatıldı");
  const others = useAccountAction<void, { revoked: number }>(
    () => api.post("/auth/sessions/revoke-others"),
    "Oturumlar kapatılamadı",
    (r) => `${r.revoked} oturum kapatıldı`,
  );
  const otherCount = q.data?.filter((s) => !s.current).length ?? 0;
  return (
    <Card>
      <CardHeader
        title="Oturumlarım"
        description="Açık oturumlar sunucuda tutulur. 30 dk hareketsizlikte ya da süresi dolunca kendiliğinden kapanır; parola değişikliği diğer oturumları kapatır."
        actions={
          <Button size="sm" disabled={!otherCount} onClick={() => setConfirmAll(true)}>
            Diğer oturumları kapat
          </Button>
        }
      />
      {q.isLoading ? (
        <TableSkeleton rows={2} cols={4} />
      ) : q.isError || !q.data ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : (
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead>
              <tr>
                <th>İstemci</th>
                <th>IP</th>
                <th>Açılış</th>
                <th>Son etkinlik</th>
                <th>Bitiş</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {q.data.map((s) => (
                <tr key={s.id}>
                  <td className="text-xs">
                    <span className="inline-flex items-center gap-1.5">
                      <MonitorSmartphone className="size-3.5 text-muted" aria-hidden />
                      <span title={s.user_agent ?? undefined}>{uaLabel(s.user_agent)}</span>
                      {s.current && <Badge className="text-2xs">bu oturum</Badge>}
                    </span>
                  </td>
                  <td className="font-mono text-xs">{s.ip ?? "—"}</td>
                  <td className="text-xs" title={fmtTime(s.created_at)}>{fmtRelative(s.created_at)}</td>
                  <td className="text-xs" title={fmtTime(s.last_seen_at)}>{fmtRelative(s.last_seen_at)}</td>
                  <td className="text-xs" title={fmtTime(s.expires_at)}>{fmtTime(s.expires_at)}</td>
                  <td className="text-right">
                    {s.current ? (
                      <Button size="sm" variant="ghost" onClick={logout}>
                        <LogOut /> Çıkış
                      </Button>
                    ) : (
                      <Button size="sm" variant="ghost" loading={revoke.isPending && revoke.variables === s.id} onClick={() => revoke.mutate(s.id)}>
                        Kapat
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <ConfirmDialog
        open={confirmAll}
        onOpenChange={setConfirmAll}
        title="Diğer oturumlar kapatılsın mı?"
        confirmLabel={`${otherCount} oturumu kapat`}
        busy={others.isPending}
        onConfirm={() => others.mutate(undefined, { onSuccess: () => setConfirmAll(false) })}
      >
        <p>Bu tarayıcı dışındaki tüm oturumlarınız hemen geçersiz olur; o cihazlarda yeniden giriş gerekir.</p>
      </ConfirmDialog>
    </Card>
  );
}

function PasswordCard() {
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const change = useAccountAction<void>(
    () => api.post("/auth/password", { current_password: cur, new_password: next }),
    "Parola değiştirilemedi",
    () => "Parola değiştirildi; diğer oturumlar kapatıldı",
  );
  const problem = next && next.length < 10 ? "En az 10 karakter" : again && next !== again ? "Parolalar eşleşmiyor" : next && next === cur ? "Yeni parola eskisiyle aynı olamaz" : null;
  return (
    <Card>
      <CardHeader title="Parola değiştir" description="Mevcut parola zorunludur. Değişiklikten sonra bu oturum açık kalır, diğerleri kapatılır." />
      <CardBody>
        <form
          className="grid max-w-md gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            setErr(null);
            change.mutate(undefined, {
              onSuccess: () => {
                setCur("");
                setNext("");
                setAgain("");
              },
              onError: (e2) => setErr((e2 as ApiError).message),
            });
          }}
        >
          <div>
            <Label htmlFor="pw-cur">Mevcut parola</Label>
            <Input id="pw-cur" type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} />
          </div>
          <div>
            <Label htmlFor="pw-new">Yeni parola</Label>
            <Input id="pw-new" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
          </div>
          <div>
            <Label htmlFor="pw-again">Yeni parola (tekrar)</Label>
            <Input id="pw-again" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} />
          </div>
          {(problem || err) && <Notice tone={err ? "critical" : "warning"}>{err ?? problem}</Notice>}
          <div>
            <Button type="submit" variant="primary" loading={change.isPending} disabled={!cur || !next || next !== again || !!problem}>
              <KeyRound /> Parolayı değiştir
            </Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}

function PermissionsCard() {
  const q = useMyPermissions();
  return (
    <Card>
      <CardHeader title="Yetkilerim" description="Rolünüzün verdiği yetkiler. Yetki kontrolü her istekte sunucuda yapılır; arayüz yalnız gösterir." />
      <CardBody>
        {q.isLoading ? (
          <TableSkeleton rows={3} cols={1} />
        ) : q.isError || !q.data ? (
          <ErrorState error={q.error} onRetry={() => q.refetch()} />
        ) : (
          <ul className="grid gap-1.5 sm:grid-cols-2">
            {q.data.permissions.map((p) => (
              <li key={p} className="flex items-start gap-2 text-xs">
                <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-st-good" aria-hidden />
                <span>
                  {PERM_LABEL[p] ?? p} <code className="font-mono text-2xs text-muted">{p}</code>
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}

export default function AccountPage() {
  const { user } = useAuth();
  return (
    <Page>
      <PageHeader
        title="Hesabım"
        description={user ? `${user.display_name} · ${user.username} · ${ROLE_LABEL[user.role] ?? user.role}` : undefined}
        meta={user?.last_login_at && <span>Son giriş: {fmtTime(user.last_login_at)}</span>}
      />
      <div className="grid gap-3 xl:grid-cols-2">
        <div className="space-y-3 xl:col-span-2">
          <SessionsCard />
        </div>
        <PasswordCard />
        <PermissionsCard />
      </div>
    </Page>
  );
}
