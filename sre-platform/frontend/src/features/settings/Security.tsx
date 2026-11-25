import { KeyRound, Trash2 } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Card, CardBody, CardHeader, Disclosure, Input, Label, Select } from "@/components/ui/primitives";
import { Dialog } from "@/components/ui/overlay";
import { ErrorState, Notice, Pagination, TableSkeleton } from "@/components/ui/states";
import { api, type ApiError } from "@/services/api";
import { useAuth } from "@/services/auth";
import { fmtRelative, fmtTime } from "@/lib/utils";
import { useAudit, useSecrets, useSecurity, useSettingsAction } from "./api";
import { ConfirmDialog, show } from "./common";

const NAME_RE = /^[a-z0-9][a-z0-9_.-]{0,63}$/;

function SecretDialog({ name, onClose }: { name: string | null; onClose: () => void }) {
  const [n, setN] = useState(name ?? "");
  const [v, setV] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const put = useSettingsAction<void, { ref: string; created: boolean }>(
    () => api.put(`/settings/secrets/${n}`, { value: v }),
    "Secret kaydedilemedi",
    (r) => `${r.ref} ${r.created ? "oluşturuldu" : "döndürüldü (rotate)"}`,
  );
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={name ? `Secret döndür: ${name}` : "Secret ekle"}
      description="Değer şifrelenerek saklanır ve bir daha gösterilmez. Referans: secret:ad"
      footer={
        <>
          <Button onClick={onClose}>Vazgeç</Button>
          <Button
            variant="primary"
            disabled={!NAME_RE.test(n) || !v}
            loading={put.isPending}
            onClick={() =>
              put.mutate(undefined, {
                onSuccess: () => {
                  setV("");
                  onClose();
                },
                onError: (e) => setErr((e as ApiError).message),
              })
            }
          >
            Kaydet
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div>
          <Label htmlFor="s-name">Ad</Label>
          <Input id="s-name" className="font-mono" value={n} disabled={!!name} onChange={(e) => setN(e.target.value.trim())} placeholder="ör. dt-platform-token" />
        </div>
        <div>
          <Label htmlFor="s-val">Değer</Label>
          <Input id="s-val" type="password" autoComplete="new-password" value={v} onChange={(e) => setV(e.target.value)} />
        </div>
        {err && <Notice tone="critical">{err}</Notice>}
      </div>
    </Dialog>
  );
}

function SecretsCard() {
  const q = useSecrets();
  const { can } = useAuth();
  const admin = can("admin");
  const [dlg, setDlg] = useState<{ name: string | null } | null>(null);
  const [del, setDel] = useState<string | null>(null);
  const remove = useSettingsAction<string>((n) => api.del(`/settings/secrets/${n}`), "Secret silinemedi", () => "Secret silindi");
  if (q.isLoading) return <TableSkeleton rows={3} cols={4} />;
  if (q.isError || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const d = q.data;
  return (
    <Card>
      <CardHeader
        title="Secrets"
        description="Değerler hiçbir API cevabında, logda ya da audit kaydında yer almaz. Ayarlar yalnız referans (secret:ad / env:AD) tutar."
        actions={
          admin && (
            <Button size="sm" variant="primary" disabled={!d.store_available} onClick={() => setDlg({ name: null })}>
              <KeyRound /> Secret ekle
            </Button>
          )
        }
      />
      {!d.store_available && (
        <Notice tone="warning" className="m-3">
          Şifreli secret deposu kapalı: SRE_SETTINGS_KEY (≥32 karakter) deployment ortamında tanımlı değil. Yalnız ortam değişkeni referansları kullanılabilir.
        </Notice>
      )}
      <div className="overflow-x-auto">
        <table className="data-table w-full">
          <thead>
            <tr>
              <th>Referans</th>
              <th>Durum</th>
              <th>Kullanan</th>
              <th>Son değişiklik</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {d.stored.map((s) => (
              <tr key={s.ref}>
                <td className="font-mono text-xs">{s.ref}</td>
                <td className="text-xs">saklı {s.hint && <span className="text-muted">({s.hint})</span>}</td>
                <td className="text-xs">{s.used_by.join(", ") || "—"}</td>
                <td className="text-xs" title={fmtTime(s.rotated_at ?? s.created_at)}>
                  {s.created_by} · {fmtRelative(s.rotated_at ?? s.created_at)}
                </td>
                <td className="text-right whitespace-nowrap">
                  {admin && (
                    <>
                      <Button size="sm" variant="ghost" onClick={() => setDlg({ name: s.name })}>
                        Döndür
                      </Button>
                      <Button size="icon" variant="ghost" aria-label={`${s.name} sil`} disabled={s.used_by.length > 0} onClick={() => setDel(s.name)}>
                        <Trash2 />
                      </Button>
                    </>
                  )}
                </td>
              </tr>
            ))}
            {d.env.map((e) => (
              <tr key={e.name}>
                <td className="font-mono text-xs">{e.ref}</td>
                <td className="text-xs">{e.available ? "ortamda tanımlı" : <span className="text-muted">{e.problem}</span>}</td>
                <td className="text-xs">{e.used_by.join(", ") || "—"}</td>
                <td className="text-xs text-muted">deployment</td>
                <td />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {dlg && <SecretDialog name={dlg.name} onClose={() => setDlg(null)} />}
      <ConfirmDialog open={!!del} onOpenChange={(o) => !o && setDel(null)} title="Secret silinsin mi?" danger confirmLabel="Kalıcı olarak sil" busy={remove.isPending} onConfirm={() => del && remove.mutate(del, { onSuccess: () => setDel(null) })}>
        <p>
          <code className="font-mono">secret:{del}</code> kalıcı olarak silinir ve geri getirilemez. Kullanımdaki secret silinemez.
        </p>
      </ConfirmDialog>
    </Card>
  );
}

const ACTION_LABEL: Record<string, string> = {
  update: "Güncelleme",
  rollback: "Rollback",
  model_select: "Model seçimi",
  migrate: "Taşıma",
  secret_create: "Secret oluşturma",
  secret_rotate: "Secret rotate",
  secret_delete: "Secret silme",
  connector_create: "Connector ekleme",
  connector_update: "Connector güncelleme",
  connector_delete: "Connector silme",
  connector_test: "Connector testi",
  connector_discover: "Tool keşfi",
  llm_profile_create: "Profil ekleme",
  llm_profile_update: "Profil güncelleme",
  llm_profile_delete: "Profil silme",
  llm_profile_test: "Profil testi",
  observability_test: "LangSmith testi",
  notification_test: "Bildirim testi",
  job_run_now: "Run now",
  checkpoint_delete: "Checkpoint silme",
};

function Diff({ before, after }: { before: Record<string, unknown> | null; after: Record<string, unknown> | null }) {
  const keys = Array.from(new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})])).filter((k) => show(before?.[k]) !== show(after?.[k]));
  if (!keys.length) return <span className="text-muted">—</span>;
  return (
    <ul className="space-y-0.5">
      {keys.map((k) => (
        <li key={k} className="font-mono text-2xs">
          {k}: <span className="text-muted line-through">{show(before?.[k])}</span> → <span className="text-ink">{show(after?.[k])}</span>
        </li>
      ))}
    </ul>
  );
}

function AuditCard() {
  const [page, setPage] = useState(1);
  const [section, setSection] = useState("");
  const q = useAudit({ page, section: section || undefined });
  return (
    <Card>
      <CardHeader
        title="Audit log"
        description="Her ayar değişikliği, test, keşif, run-now ve silme kaydedilir; secret değerleri redakte edilir. Reddedilen değişiklikler de yazılır."
        actions={
          <Select
            aria-label="bölüm filtresi"
            className="w-44"
            value={section}
            onChange={(e) => {
              setSection(e.target.value);
              setPage(1);
            }}
          >
            <option value="">Tüm bölümler</option>
            {["general", "agent", "scheduling", "risk", "connectors", "llm", "observability", "notifications", "retention", "memory", "security"].map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
        }
      />
      {q.isLoading ? (
        <TableSkeleton rows={6} cols={5} />
      ) : q.isError || !q.data ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="data-table w-full">
              <thead>
                <tr>
                  <th>Zaman</th>
                  <th>Kim</th>
                  <th>Eylem</th>
                  <th>Hedef</th>
                  <th>Sonuç</th>
                  <th>Değişiklik</th>
                </tr>
              </thead>
              <tbody>
                {q.data.items.map((a) => (
                  <tr key={a.id}>
                    <td className="text-xs whitespace-nowrap">{fmtTime(a.ts)}</td>
                    <td className="text-xs">{a.actor}</td>
                    <td className="text-xs">{ACTION_LABEL[a.action] ?? a.action}</td>
                    <td className="text-xs">
                      {a.section}
                      {a.target ? ` · ${a.target}` : ""}
                      {a.revision ? ` · r${a.revision}` : ""}
                    </td>
                    <td className="text-xs">
                      <Badge className={a.result === "ok" ? "text-2xs" : "border-st-critical/60 text-2xs"}>{a.result}</Badge>
                      {a.detail && <div className="max-w-56 truncate text-2xs text-muted" title={a.detail}>{a.detail}</div>}
                    </td>
                    <td>
                      <Diff before={a.before} after={a.after} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {q.data.items.length === 0 && <p className="p-3 text-xs text-muted">Kayıt yok.</p>}
          </div>
          {q.data.pages > 1 && <Pagination page={page} pages={q.data.pages} total={q.data.total} onPage={setPage} />}
        </>
      )}
    </Card>
  );
}

export function SecuritySection() {
  const q = useSecurity();
  return (
    <div className="space-y-3">
      <SecretsCard />
      {q.data && (
        <Card>
          <CardHeader title="Erişim ve deployment politikaları" />
          <CardBody className="space-y-2">
            <table className="data-table w-full">
              <thead>
                <tr>
                  <th>Rol</th>
                  <th>Yetkiler</th>
                </tr>
              </thead>
              <tbody>
                {q.data.roles.map((r) => (
                  <tr key={r.role}>
                    <td className="font-mono text-xs">{r.role}</td>
                    <td className="text-xs">{r.can.join(" · ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Notice tone="info">{q.data.remediation}</Notice>
            <Disclosure title="Deployment lock'ları ve allowlist'ler" meta={`${Object.keys(q.data.locks).length} bölümde lock`}>
              <ul className="space-y-1 text-xs text-ink-2">
                <li>Lock'lar (SRE_SETTINGS_LOCKS): {Object.keys(q.data.locks).length ? <code className="font-mono">{JSON.stringify(q.data.locks)}</code> : "yok"}</li>
                <li>Outbound allowlist (SRE_OUTBOUND_ALLOWLIST): {q.data.outbound_allowlist.join(", ") || "boş — yalnız public https"}</li>
                <li>Env secret allowlist: {q.data.secret_store.env_allowlist.join(", ")}</li>
                <li>{q.data.audit_retention}</li>
              </ul>
            </Disclosure>
          </CardBody>
        </Card>
      )}
      <AuditCard />
    </div>
  );
}
