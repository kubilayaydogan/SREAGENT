import { UserPlus } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Card, CardHeader, Input, Label, Select } from "@/components/ui/primitives";
import { Dialog } from "@/components/ui/overlay";
import { ErrorState, Notice, Pagination, TableSkeleton } from "@/components/ui/states";
import { uaLabel, useAccountAction, useUserSessions, useUsers } from "@/features/account/api";
import { ROLE_LABEL } from "@/features/account/permissions";
import { fmtRelative, fmtTime } from "@/lib/utils";
import { api, type ApiError } from "@/services/api";
import { useAuth } from "@/services/auth";
import type { Role, User } from "@/types/api";
import { ApiKeysCard } from "./ApiKeys";
import { ConfirmDialog } from "./common";

const ROLES: Role[] = ["viewer", "sre", "admin"];
const USERNAME_RE = /^[A-Za-z0-9._-]{3,64}$/;

function CreateUserDialog({ onClose }: { onClose: () => void }) {
  const [f, setF] = useState({ username: "", display_name: "", role: "viewer" as Role, password: "" });
  const [err, setErr] = useState<string | null>(null);
  const create = useAccountAction<void, User>(() => api.post("/users", f), "Kullanıcı oluşturulamadı", (u) => `${u.username} oluşturuldu`);
  const ok = USERNAME_RE.test(f.username) && f.display_name.trim() && f.password.length >= 10;
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Kullanıcı ekle"
      description="Parola en az 10 karakter. Kullanıcı ilk girişten sonra parolasını Hesabım sayfasından değiştirebilir."
      footer={
        <>
          <Button onClick={onClose}>Vazgeç</Button>
          <Button variant="primary" disabled={!ok} loading={create.isPending} onClick={() => create.mutate(undefined, { onSuccess: onClose, onError: (e) => setErr((e as ApiError).message) })}>
            Oluştur
          </Button>
        </>
      }
    >
      <div className="grid gap-3">
        <div>
          <Label htmlFor="u-name">Kullanıcı adı</Label>
          <Input id="u-name" className="font-mono" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value.trim() })} />
        </div>
        <div>
          <Label htmlFor="u-disp">Görünen ad</Label>
          <Input id="u-disp" value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} />
        </div>
        <div>
          <Label htmlFor="u-role">Rol</Label>
          <Select id="u-role" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value as Role })}>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="u-pw">Geçici parola</Label>
          <Input id="u-pw" type="password" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
        </div>
        {err && <Notice tone="critical">{err}</Notice>}
      </div>
    </Dialog>
  );
}

function EditUserDialog({ u, onClose }: { u: User; onClose: () => void }) {
  const { user: me } = useAuth();
  const self = me?.id === u.id;
  const [role, setRole] = useState<Role>(u.role);
  const [active, setActive] = useState(u.is_active);
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const sessions = useUserSessions(u.id);
  const save = useAccountAction<void, User>(
    () => {
      const body: Record<string, unknown> = {};
      if (role !== u.role) body.role = role;
      if (active !== u.is_active) body.is_active = active;
      if (pw) body.password = pw;
      return api.patch(`/users/${u.id}`, body);
    },
    "Kullanıcı güncellenemedi",
    () => `${u.username} güncellendi`,
  );
  const revokeAll = useAccountAction<void, { revoked: number }>(() => api.post(`/users/${u.id}/sessions/revoke`), "Oturumlar kapatılamadı", (r) => `${r.revoked} oturum kapatıldı`);
  const changed = role !== u.role || active !== u.is_active || !!pw;
  const revokes = role !== u.role || (u.is_active && !active) || !!pw;
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`${u.display_name} (${u.username})`}
      wide
      footer={
        <>
          <Button onClick={onClose}>Kapat</Button>
          <Button
            variant="primary"
            disabled={!changed || (!!pw && pw.length < 10)}
            loading={save.isPending}
            onClick={() => save.mutate(undefined, { onSuccess: onClose, onError: (e) => setErr((e as ApiError).message) })}
          >
            Kaydet
          </Button>
        </>
      }
    >
      <div className="grid gap-4">
        <div className="grid gap-3 sm:grid-cols-3">
          <div>
            <Label htmlFor="e-role">Rol</Label>
            <Select id="e-role" value={role} disabled={self} onChange={(e) => setRole(e.target.value as Role)}>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {ROLE_LABEL[r]}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label htmlFor="e-active">Durum</Label>
            <Select id="e-active" value={active ? "1" : "0"} disabled={self} onChange={(e) => setActive(e.target.value === "1")}>
              <option value="1">Etkin</option>
              <option value="0">Devre dışı</option>
            </Select>
          </div>
          <div>
            <Label htmlFor="e-pw">Parola sıfırla (isteğe bağlı)</Label>
            <Input id="e-pw" type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} placeholder="en az 10 karakter" />
          </div>
        </div>
        {self && <Notice tone="info">Kendi rolünüzü düşüremez ya da hesabınızı kapatamazsınız.</Notice>}
        {revokes && <Notice tone="warning">Rol değişikliği, devre dışı bırakma ve parola sıfırlama kullanıcının tüm açık oturumlarını kapatır.</Notice>}
        {err && <Notice tone="critical">{err}</Notice>}
        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <h3 className="text-xs font-medium text-ink">Açık oturumlar</h3>
            <Button size="sm" disabled={!sessions.data?.length} loading={revokeAll.isPending} onClick={() => revokeAll.mutate()}>
              Tümünü kapat
            </Button>
          </div>
          {sessions.isLoading ? (
            <TableSkeleton rows={2} cols={4} />
          ) : sessions.isError ? (
            <ErrorState error={sessions.error} onRetry={() => sessions.refetch()} />
          ) : sessions.data?.length ? (
            <table className="data-table w-full">
              <thead>
                <tr>
                  <th>İstemci</th>
                  <th>IP</th>
                  <th>Son etkinlik</th>
                  <th>Bitiş</th>
                </tr>
              </thead>
              <tbody>
                {sessions.data.map((s) => (
                  <tr key={s.id}>
                    <td className="text-xs" title={s.user_agent ?? undefined}>{uaLabel(s.user_agent)}</td>
                    <td className="font-mono text-xs">{s.ip ?? "—"}</td>
                    <td className="text-xs">{fmtRelative(s.last_seen_at)}</td>
                    <td className="text-xs">{fmtTime(s.expires_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-xs text-muted">Açık oturum yok.</p>
          )}
        </div>
      </div>
    </Dialog>
  );
}

function UsersCard() {
  const [page, setPage] = useState(1);
  const q = useUsers(page);
  const [create, setCreate] = useState(false);
  const [edit, setEdit] = useState<User | null>(null);
  const [disable, setDisable] = useState<User | null>(null);
  const toggle = useAccountAction<User>((u) => api.patch(`/users/${u.id}`, { is_active: !u.is_active }), "Durum değiştirilemedi", () => "Kullanıcı durumu güncellendi");
  const { user: me } = useAuth();
  return (
    <Card>
      <CardHeader
        title="Kullanıcılar"
        description="Rol, durum ve parola değişiklikleri audit'e yazılır ve kullanıcının açık oturumlarını kapatır."
        actions={
          <Button size="sm" variant="primary" onClick={() => setCreate(true)}>
            <UserPlus /> Kullanıcı ekle
          </Button>
        }
      />
      {q.isLoading ? (
        <TableSkeleton rows={4} cols={5} />
      ) : q.isError || !q.data ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="data-table w-full">
              <thead>
                <tr>
                  <th>Kullanıcı</th>
                  <th>Rol</th>
                  <th>Durum</th>
                  <th>Son giriş</th>
                  <th>Oluşturma</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {q.data.items.map((u) => (
                  <tr key={u.id}>
                    <td className="text-xs">
                      <div className="text-ink">{u.display_name}</div>
                      <div className="font-mono text-2xs text-muted">{u.username}</div>
                    </td>
                    <td className="text-xs">{ROLE_LABEL[u.role] ?? u.role}</td>
                    <td className="text-xs">{u.is_active ? <Badge className="text-2xs">etkin</Badge> : <Badge className="border-st-critical/60 text-2xs">devre dışı</Badge>}</td>
                    <td className="text-xs" title={u.last_login_at ? fmtTime(u.last_login_at) : undefined}>{u.last_login_at ? fmtRelative(u.last_login_at) : "—"}</td>
                    <td className="text-xs">{fmtTime(u.created_at)}</td>
                    <td className="text-right whitespace-nowrap">
                      <Button size="sm" variant="ghost" onClick={() => setEdit(u)}>
                        Düzenle
                      </Button>
                      {u.id !== me?.id && (
                        <Button size="sm" variant="ghost" onClick={() => (u.is_active ? setDisable(u) : toggle.mutate(u))}>
                          {u.is_active ? "Devre dışı bırak" : "Etkinleştir"}
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {q.data.pages > 1 && <Pagination page={page} pages={q.data.pages} total={q.data.total} onPage={setPage} />}
        </>
      )}
      {create && <CreateUserDialog onClose={() => setCreate(false)} />}
      {edit && <EditUserDialog u={edit} onClose={() => setEdit(null)} />}
      <ConfirmDialog
        open={!!disable}
        onOpenChange={(o) => !o && setDisable(null)}
        title="Kullanıcı devre dışı bırakılsın mı?"
        danger
        confirmLabel="Devre dışı bırak"
        busy={toggle.isPending}
        onConfirm={() => disable && toggle.mutate(disable, { onSuccess: () => setDisable(null) })}
      >
        <p>
          <b>{disable?.display_name}</b> giriş yapamaz ve açık oturumları hemen kapanır. Kayıtları silinmez; daha sonra yeniden etkinleştirilebilir.
        </p>
      </ConfirmDialog>
    </Card>
  );
}

export function UsersSection() {
  return (
    <div className="space-y-3">
      <UsersCard />
      <ApiKeysCard />
    </div>
  );
}
