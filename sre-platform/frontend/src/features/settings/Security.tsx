import { Check, KeyRound, Minus, ShieldAlert, Trash2 } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Card, CardBody, CardHeader, Disclosure, Input, Label, Select } from "@/components/ui/primitives";
import { Dialog } from "@/components/ui/overlay";
import { ErrorState, Notice, Pagination, TableSkeleton } from "@/components/ui/states";
import { api, type ApiError } from "@/services/api";
import { useAuth } from "@/services/auth";
import { fmtRelative, fmtTime } from "@/lib/utils";
import { PERM_LABEL, ROLE_LABEL } from "@/features/account/permissions";
import { useAudit, useAuditSummary, useSecrets, useSecurity, useSettingsAction, type SecretsList, type SecurityInfo, type SecurityOps } from "./api";
import { ConfirmDialog, show } from "./common";

const NAME_RE = /^[a-z0-9][a-z0-9_.-]{0,63}$/;

type SecretRow = SecretsList["stored"][number];
export type ExpiryMode = "keep" | "none" | "date";

/** Süre sonu seçimi → API alanı: keep = gönderme (değişmez), none = null (kaldır), date = gün sonu UTC. */
export function expiryPayload(mode: ExpiryMode, date: string): { expires_at?: string | null } {
  if (mode === "keep") return {};
  if (mode === "none") return { expires_at: null };
  return { expires_at: `${date}T23:59:59Z` };
}

export const LIFECYCLE_LABEL: Record<SecretRow["status"], string> = {
  ok: "geçerli",
  rotation_due: "rotation süresi geçmiş",
  expiring: "süresi yaklaşıyor",
  expired: "süresi dolmuş",
};

function MetaFields({
  row,
  mode,
  setMode,
  date,
  setDate,
  rotation,
  setRotation,
  desc,
  setDesc,
}: {
  row: SecretRow | null;
  mode: ExpiryMode;
  setMode: (m: ExpiryMode) => void;
  date: string;
  setDate: (d: string) => void;
  rotation: string;
  setRotation: (v: string) => void;
  desc: string;
  setDesc: (v: string) => void;
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <div>
        <Label htmlFor="s-exp">Son kullanma</Label>
        <Select id="s-exp" value={mode} onChange={(e) => setMode(e.target.value as ExpiryMode)}>
          {row && <option value="keep">Değiştirme{row.expires_at ? ` (${fmtTime(row.expires_at)})` : " (yok)"}</option>}
          <option value="none">Süre sonu yok</option>
          <option value="date">Tarih seç</option>
        </Select>
        {mode === "date" && <Input className="mt-1.5" type="date" aria-label="son kullanma tarihi" value={date} onChange={(e) => setDate(e.target.value)} />}
      </div>
      <div>
        <Label htmlFor="s-rot">Rotation aralığı (gün; boş = politika yok)</Label>
        <Input id="s-rot" type="number" min={1} max={3650} value={rotation} onChange={(e) => setRotation(e.target.value)} />
      </div>
      <div className="sm:col-span-2">
        <Label htmlFor="s-desc">Açıklama (isteğe bağlı)</Label>
        <Input id="s-desc" maxLength={256} value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="ör. Dynatrace platform token, sahibi: SRE ekibi" />
      </div>
    </div>
  );
}

function useMetaState(row: SecretRow | null, rotating: boolean) {
  const [mode, setMode] = useState<ExpiryMode>(row ? (row.status === "expired" ? "date" : "keep") : "none");
  const [date, setDate] = useState("");
  const [rotation, setRotation] = useState(String(row ? (row.rotation_days ?? "") : 90));
  const [desc, setDesc] = useState(row?.description ?? "");
  const rot = rotation.trim() === "" ? null : Number(rotation);
  const problem =
    mode === "date" && (!date || new Date(`${date}T23:59:59Z`) <= new Date())
      ? "Son kullanma tarihi gelecekte olmalı"
      : rot !== null && (!Number.isInteger(rot) || rot < 1 || rot > 3650)
        ? "Rotation aralığı 1–3650 gün"
        : rotating && row?.status === "expired" && mode === "keep"
          ? "Süresi dolmuş secret için yeni bir tarih seçin ya da süre sonunu kaldırın"
          : null;
  const body = { ...expiryPayload(mode, date), rotation_days: rot, description: desc.trim() || null };
  return { fields: { row, mode, setMode, date, setDate, rotation, setRotation, desc, setDesc }, problem, body };
}

function SecretDialog({ row, onClose }: { row: SecretRow | null; onClose: () => void }) {
  const [n, setN] = useState(row?.name ?? "");
  const [v, setV] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const meta = useMetaState(row, true);
  const put = useSettingsAction<void, { ref: string; created: boolean }>(
    () => api.put(`/settings/secrets/${n}`, { value: v, ...meta.body }),
    "Secret kaydedilemedi",
    (r) => `${r.ref} ${r.created ? "oluşturuldu" : "döndürüldü (rotate)"}`,
  );
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={row ? `Secret döndür: ${row.name}` : "Secret ekle"}
      description="Değer şifrelenerek saklanır ve bir daha gösterilmez. Referans: secret:ad. Süresi dolan secret kullanılmaz; rotation süresinin geçmesi yalnız uyarı üretir."
      wide
      footer={
        <>
          <Button onClick={onClose}>Vazgeç</Button>
          <Button
            variant="primary"
            disabled={!NAME_RE.test(n) || !v || !!meta.problem}
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
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <Label htmlFor="s-name">Ad</Label>
            <Input id="s-name" className="font-mono" value={n} disabled={!!row} onChange={(e) => setN(e.target.value.trim())} placeholder="ör. dt-platform-token" />
          </div>
          <div>
            <Label htmlFor="s-val">Değer</Label>
            <Input id="s-val" type="password" autoComplete="new-password" value={v} onChange={(e) => setV(e.target.value)} />
          </div>
        </div>
        <MetaFields {...meta.fields} />
        {(meta.problem || err) && <Notice tone={err ? "critical" : "warning"}>{err ?? meta.problem}</Notice>}
      </div>
    </Dialog>
  );
}

function SecretMetaDialog({ row, onClose }: { row: SecretRow; onClose: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  const meta = useMetaState(row, false);
  const save = useSettingsAction<void>(() => api.patch(`/settings/secrets/${row.name}`, meta.body), "Politika kaydedilemedi", () => "Secret politikası güncellendi");
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Secret politikası: ${row.name}`}
      description="Değer değişmez. Süre sonunu uzatmak ya da kaldırmak audit'e yazılır."
      wide
      footer={
        <>
          <Button onClick={onClose}>Vazgeç</Button>
          <Button
            variant="primary"
            disabled={!!meta.problem}
            loading={save.isPending}
            onClick={() => save.mutate(undefined, { onSuccess: onClose, onError: (e) => setErr((e as ApiError).message) })}
          >
            Kaydet
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <MetaFields {...meta.fields} />
        {(meta.problem || err) && <Notice tone={err ? "critical" : "warning"}>{err ?? meta.problem}</Notice>}
      </div>
    </Dialog>
  );
}

function SecretsCard() {
  const q = useSecrets();
  const { can } = useAuth();
  const admin = can("admin");
  const [dlg, setDlg] = useState<{ row: SecretRow | null } | null>(null);
  const [metaRow, setMetaRow] = useState<SecretRow | null>(null);
  const [del, setDel] = useState<string | null>(null);
  const remove = useSettingsAction<string>((n) => api.del(`/settings/secrets/${n}`), "Secret silinemedi", () => "Secret silindi");
  if (q.isLoading) return <TableSkeleton rows={3} cols={4} />;
  if (q.isError || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const d = q.data;
  return (
    <Card>
      <CardHeader
        title="Secrets"
        description="Değerler hiçbir API cevabında, logda ya da audit kaydında yer almaz. Ayarlar yalnız referans (secret:ad / env:AD) tutar. Süresi dolan secret kullanılmaz; rotation süresinin geçmesi uyarı üretir (varsayılan 90 gün)."
        actions={
          admin && (
            <Button size="sm" variant="primary" disabled={!d.store_available} onClick={() => setDlg({ row: null })}>
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
              <th>Süre sonu / rotation</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {d.stored.map((s) => (
              <tr key={s.ref}>
                <td className="text-xs">
                  <span className="font-mono">{s.ref}</span>
                  {s.description && <div className="text-2xs text-muted">{s.description}</div>}
                </td>
                <td className="text-xs">
                  <Badge className={s.status === "ok" ? "text-2xs" : s.status === "expired" ? "border-st-critical/60 text-2xs" : "border-st-warning/60 text-2xs"}>
                    {LIFECYCLE_LABEL[s.status]}
                  </Badge>
                  {s.hint && <div className="text-2xs text-muted">{s.hint}</div>}
                </td>
                <td className="text-xs">{s.used_by.join(", ") || "—"}</td>
                <td className="text-xs" title={fmtTime(s.rotated_at ?? s.created_at)}>
                  {s.created_by} · {fmtRelative(s.rotated_at ?? s.created_at)}
                </td>
                <td className="text-xs">
                  <div>{s.expires_at ? fmtTime(s.expires_at) : "süre sonu yok"}</div>
                  <div className="text-2xs text-muted">
                    {s.rotation_days ? `rotation ${s.rotation_days} gün · ${s.rotation_due_at ? fmtTime(s.rotation_due_at) : "—"}` : "rotation politikası yok"}
                  </div>
                </td>
                <td className="text-right whitespace-nowrap">
                  {admin && (
                    <>
                      <Button size="sm" variant="ghost" onClick={() => setDlg({ row: s })}>
                        Döndür
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setMetaRow(s)}>
                        Politika
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
                <td className="text-xs text-muted">deployment yönetir</td>
                <td />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {dlg && <SecretDialog row={dlg.row} onClose={() => setDlg(null)} />}
      {metaRow && <SecretMetaDialog row={metaRow} onClose={() => setMetaRow(null)} />}
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
  secret_metadata: "Secret politikası",
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
  "auth.login": "Giriş",
  "auth.logout": "Çıkış",
  "auth.password.change": "Parola değişikliği",
  "auth.session.rejected": "Oturum reddi",
  "auth.session.revoke": "Oturum kapatma",
  "auth.session.revoke_others": "Diğer oturumları kapatma",
  "auth.session.revoke_all": "Kullanıcı oturumlarını kapatma",
  "authz.denied": "Yetki reddi",
  "auth.apikey.rejected": "API anahtarı reddi",
  "apikey.create": "API anahtarı oluşturma",
  "apikey.revoke": "API anahtarı iptali",
  "user.create": "Kullanıcı oluşturma",
  "user.update": "Kullanıcı güncelleme",
  "user.role_change": "Rol değişikliği",
  "user.disable": "Kullanıcı devre dışı",
  "run.start": "Koşu başlatma",
  "run.denied": "Koşu reddi (policy)",
  "run.cancel": "Koşu iptali",
  "tool.denied": "Tool reddi (policy)",
  "egress.denied": "Dış bağlantı engeli (SSRF)",
  "llm.budget_exceeded": "LLM koşu bütçesi aşıldı",
  "llm.model_denied": "Model allowlist reddi",
  "connector.tool_changed": "Tool tanımı değişti",
  "approval.request": "Onay isteği",
  "approval.decide": "Onay kararı",
  "approval.cancel": "Onay iptali",
  "export.rca_markdown": "RCA dışa aktarma",
  "memory.focus.clear": "Odak temizleme",
  "security.alert": "Güvenlik alarmı",
  "siem.delivery_failed": "SIEM teslim hatası",
  "siem.delivery_recovered": "SIEM teslimi düzeldi",
};

const label = (a: string) => ACTION_LABEL[a] ?? a;

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

type AuditFilter = { action: string; result: string };

function AuditCard({ filter, setFilter }: { filter: AuditFilter; setFilter: (f: AuditFilter) => void }) {
  const [page, setPage] = useState(1);
  const [section, setSection] = useState("");
  const q = useAudit({ page, section: section || undefined, action: filter.action || undefined, result: filter.result || undefined });
  return (
    <Card>
      <CardHeader
        title="Audit log"
        description="Her ayar değişikliği, test, keşif, run-now ve silme kaydedilir; secret değerleri redakte edilir. Reddedilen değişiklikler de yazılır."
        actions={
          <div className="flex flex-wrap items-center gap-2">
          {filter.action && (
            <Button size="sm" variant="ghost" onClick={() => setFilter({ ...filter, action: "" })} title="eylem filtresini kaldır">
              {label(filter.action)} ✕
            </Button>
          )}
          <Select
            aria-label="sonuç filtresi"
            className="w-40"
            value={filter.result}
            onChange={(e) => {
              setFilter({ ...filter, result: e.target.value });
              setPage(1);
            }}
          >
            <option value="">Tüm sonuçlar</option>
            <option value="denied">Yalnız reddedilen</option>
            <option value="rejected">Yalnız geçersiz</option>
            <option value="ok">Yalnız başarılı</option>
          </Select>
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
            {["auth", "users", "agent", "connectors", "llm", "approvals", "export", "general", "scheduling", "risk", "observability", "notifications", "retention", "memory", "security"].map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
          </div>
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
                    <td className="text-xs">
                      {a.actor}
                      {a.source_ip && <div className="font-mono text-2xs text-muted">{a.source_ip}</div>}
                    </td>
                    <td className="text-xs">
                      <button type="button" className="text-left hover:underline" onClick={() => setFilter({ ...filter, action: a.action })} title="bu eyleme göre filtrele">
                        {label(a.action)}
                      </button>
                    </td>
                    <td className="text-xs">
                      {a.section}
                      {a.target ? ` · ${a.target}` : ""}
                      {a.revision ? ` · r${a.revision}` : ""}
                    </td>
                    <td className="text-xs">
                      <Badge className={a.result === "ok" ? "text-2xs" : "border-st-critical/60 text-2xs"}>{a.result}</Badge>
                      {a.reason_code && <div className="font-mono text-2xs text-st-critical-ink">{a.reason_code}</div>}
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

function PermissionMatrix({ d }: { d: SecurityInfo }) {
  const roles = ["viewer", "sre", "admin"];
  return (
    <Card>
      <CardHeader title="Permission matrisi" description="Rotalar rol yerine permission ister; reddedilen her istek audit'e 'Yetki reddi' olarak yazılır. Salt okunur (kodda tanımlı)." />
      <div className="overflow-x-auto">
        <table className="data-table w-full">
          <thead>
            <tr>
              <th>Permission</th>
              {roles.map((r) => (
                <th key={r} className="text-center">
                  {ROLE_LABEL[r]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {d.permissions.map((p) => (
              <tr key={p.permission}>
                <td className="text-xs">
                  {PERM_LABEL[p.permission] ?? p.permission}
                  <div className="font-mono text-2xs text-muted">{p.permission}</div>
                </td>
                {roles.map((r) => (
                  <td key={r} className="text-center">
                    {p.roles.includes(r) ? <Check aria-label="var" className="inline size-4 text-st-good" /> : <Minus aria-label="yok" className="inline size-4 text-muted" />}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

const SINK_LABEL: Record<string, string> = {
  llm: "LLM'e giden prompt",
  trace: "LangSmith trace",
  log: "Uygulama logu",
  notify: "Slack bildirimi",
  export: "RCA dışa aktarma",
  storage: "Veritabanı kayıtları",
  audit: "Audit kaydı",
};

function DataProtectionCard({ d }: { d: SecurityInfo }) {
  return (
    <Card>
      <CardHeader
        title="Veri koruma (DLP) ve LLM politikası"
        description="Secret kalıpları (token, JWT, bearer, webhook, private key) her yerde maskelenir. PII (e-posta, IBAN, kart, TC kimlik no) checksum ile doğrulanarak maskelenir. Değişiklik deployment ortamından yapılır (SRE_DLP_PII_KEEP_SINKS, SRE_LLM_MODEL_ALLOWLIST)."
      />
      <CardBody className="grid gap-4 lg:grid-cols-2">
        <table className="data-table w-full">
          <thead>
            <tr>
              <th>Hedef</th>
              <th className="text-center">Secret</th>
              <th className="text-center">PII</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(d.dlp).map(([sink, p]) => (
              <tr key={sink}>
                <td className="text-xs">
                  {SINK_LABEL[sink] ?? sink} <code className="font-mono text-2xs text-muted">{sink}</code>
                </td>
                <td className="text-center text-xs">{p.secrets ? "maskeli" : "açık"}</td>
                <td className="text-center text-xs">{p.pii ? "maskeli" : <span className="text-st-warning-ink">korunur (muaf)</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="space-y-2 text-xs text-ink-2">
          <div>
            <div className="mb-1 font-medium text-ink">İzinli LLM modelleri</div>
            <div className="flex flex-wrap gap-1">
              {d.llm_model_allowlist.map((m) => (
                <Badge key={m} className="font-mono text-2xs">
                  {m}
                </Badge>
              ))}
            </div>
          </div>
          <p>Listede olmayan model LLM profilinde kaydedilemez; çalışma anında da kullanılmaz ("Model allowlist reddi").</p>
          <p>Koşu başına LLM çağrı ve token sınırları Settings → Agent bölümündedir; sınır dolunca cevap deterministik üretilir ve "LLM koşu bütçesi aşıldı" olayı yazılır.</p>
          <p>MCP tool tanımı onaydan sonra değişirse tool kullanılmaz ("Tool tanımı değişti"); MCP Servers bölümünden yeniden onaylanır.</p>
        </div>
      </CardBody>
    </Card>
  );
}

function SecurityEventsCard({ onPick }: { onPick: (action: string) => void }) {
  const q = useAuditSummary(24);
  return (
    <Card>
      <CardHeader title="Güvenlik olayları (son 24 saat)" description="Reddedilen ve başarısız işlemler. Bir olaya tıklayınca aşağıdaki audit log o olaya göre filtrelenir." />
      {q.isLoading ? (
        <TableSkeleton rows={3} cols={3} />
      ) : q.isError || !q.data ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : q.data.items.length === 0 ? (
        <p className="flex items-center gap-2 p-3 text-xs text-muted">
          <Check className="size-4 text-st-good" aria-hidden /> Son 24 saatte reddedilen ya da başarısız işlem yok.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead>
              <tr>
                <th>Olay</th>
                <th>Neden</th>
                <th className="text-right">Adet</th>
                <th>Son</th>
              </tr>
            </thead>
            <tbody>
              {q.data.items.map((i) => (
                <tr key={`${i.action}:${i.reason_code}`}>
                  <td className="text-xs">
                    <button type="button" className="inline-flex items-center gap-1.5 text-left hover:underline" onClick={() => onPick(i.action)}>
                      <ShieldAlert className="size-3.5 text-st-critical" aria-hidden /> {label(i.action)}
                    </button>
                  </td>
                  <td className="font-mono text-2xs">{i.reason_code ?? "—"}</td>
                  <td className="text-right text-xs tabular-nums">{i.count}</td>
                  <td className="text-xs" title={fmtTime(i.last_ts)}>
                    {fmtRelative(i.last_ts)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

/** SIEM bekleyen olay sayısı + teslim durumundan tek satır özet (test edilir). */
export function siemState(s: SecurityOps["siem"]): { tone: "ok" | "warn" | "off"; text: string } {
  if (!s.enabled) return { tone: "off", text: "Kapalı" };
  if (s.failing) return { tone: "warn", text: `Teslim başarısız (${s.failing.error}); ${s.lag ?? "?"} olay bekliyor` };
  if (s.cursor === null) return { tone: "ok", text: "Açık; ilk tur bekleniyor" };
  return { tone: s.lag && s.lag > 1000 ? "warn" : "ok", text: `Açık · bekleyen ${s.lag ?? 0} olay` };
}

function SecurityOpsCard({ ops }: { ops: SecurityOps }) {
  const st = siemState(ops.siem);
  return (
    <Card>
      <CardHeader
        title="SIEM iletimi ve güvenlik alarmları"
        description="Worker her dakika audit olaylarını SIEM'e iletir (at-least-once, olay id'si ile tekrar ayıklanabilir) ve alarm kurallarını değerlendirir. İkisi de varsayılan kapalı; Settings › Notifications'tan açılır."
      />
      <CardBody className="grid gap-4 lg:grid-cols-2">
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
          <dt className="text-muted">SIEM</dt>
          <dd className={st.tone === "warn" ? "text-st-warning-ink" : "text-ink"}>{st.text}</dd>
          <dt className="text-muted">İmza</dt>
          <dd>{ops.siem.signed ? "HMAC-SHA256 (X-SRE-Signature)" : "yok"}</dd>
          <dt className="text-muted">Son başarılı</dt>
          <dd>{ops.siem.last_ok ? `${fmtRelative(ops.siem.last_ok.ts)} · ${ops.siem.last_ok.count} olay` : "—"}</dd>
          <dt className="text-muted">Başlangıç</dt>
          <dd>{ops.siem.started_at ? fmtTime(ops.siem.started_at) : "—"}</dd>
          <dt className="text-muted">Secret hijyeni</dt>
          <dd>
            {ops.secrets && ops.secrets.expired.length + ops.secrets.expiring.length + ops.secrets.rotation_due.length > 0 ? (
              <span className="text-st-warning-ink">
                {ops.secrets.expired.length} süresi dolmuş · {ops.secrets.expiring.length} yaklaşan · {ops.secrets.rotation_due.length} rotation süresi geçmiş
              </span>
            ) : (
              "sorun yok"
            )}
          </dd>
          <dt className="text-muted">Alarmlar</dt>
          <dd>
            {ops.alerts.enabled ? `Açık · pencere ${ops.alerts.window_minutes} dk` : "Kapalı"}
            {ops.alerts.enabled && !ops.alerts.slack && " · Slack webhook yok (yalnız audit)"}
          </dd>
        </dl>
        <table className="data-table w-full">
          <thead>
            <tr>
              <th>Kural</th>
              <th className="text-right">Threshold</th>
              <th>Son alarm</th>
            </tr>
          </thead>
          <tbody>
            {ops.alerts.rules.map((r) => (
              <tr key={r.rule}>
                <td className="text-xs" title={r.actions.join(", ")}>
                  {r.label}
                  <div className="font-mono text-2xs text-muted">{r.rule}</div>
                </td>
                <td className="text-right text-xs tabular-nums">{r.threshold}</td>
                <td className="text-xs">{r.last_fired ? fmtRelative(r.last_fired) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardBody>
    </Card>
  );
}

export function SecuritySection() {
  const q = useSecurity();
  const [filter, setFilter] = useState<AuditFilter>({ action: "", result: "" });
  return (
    <div className="space-y-3">
      <SecurityEventsCard onPick={(action) => setFilter({ action, result: "" })} />
      {q.data?.security_ops && <SecurityOpsCard ops={q.data.security_ops} />}
      {q.data && <PermissionMatrix d={q.data} />}
      {q.data && <DataProtectionCard d={q.data} />}
      <SecretsCard />
      {q.data && (
        <Card>
          <CardHeader title="Deployment politikaları" />
          <CardBody className="space-y-2">
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
      <AuditCard filter={filter} setFilter={setFilter} />
    </div>
  );
}
