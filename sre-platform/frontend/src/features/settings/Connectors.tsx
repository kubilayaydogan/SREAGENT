import { FlaskConical, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Card, CardBody, CardHeader, Input, Label, Select } from "@/components/ui/primitives";
import { Dialog } from "@/components/ui/overlay";
import { EmptyState, ErrorState, Notice, TableSkeleton } from "@/components/ui/states";
import { api, type ApiError } from "@/services/api";
import { useAuth } from "@/services/auth";
import { fmtRelative } from "@/lib/utils";
import { useConnectors, useSecrets, useSettingsAction, type Connector, type ConnectorList, type TestResult } from "./api";
import { CheckStatus, ConfirmDialog, SecretRefBadge, Toggle } from "./common";

interface Form {
  name: string;
  provider: string;
  endpoint: string;
  auth_type: "bearer" | "none";
  secret_ref: string;
  timeout_s: number;
  retries: number;
  enabled: boolean;
  read_only: boolean;
  analysis_source: boolean;
}

const EMPTY: Form = { name: "", provider: "custom", endpoint: "https://", auth_type: "bearer", secret_ref: "", timeout_s: 60, retries: 2, enabled: true, read_only: true, analysis_source: false };

function SecretRefSelect({ value, onChange, id }: { value: string; onChange: (v: string) => void; id: string }) {
  const s = useSecrets();
  const refs = [...(s.data?.stored ?? []).map((x) => x.ref), ...(s.data?.env ?? []).map((x) => x.ref ?? "")].filter(Boolean);
  return (
    <Select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">— seçin —</option>
      {Array.from(new Set([...(value ? [value] : []), ...refs])).map((r) => (
        <option key={r} value={r}>
          {r}
        </option>
      ))}
    </Select>
  );
}

function ConnectorDialog({ open, onOpenChange, editing, meta }: { open: boolean; onOpenChange: (v: boolean) => void; editing: Connector | null; meta: ConnectorList }) {
  const [f, setF] = useState<Form>(EMPTY);
  const [err, setErr] = useState<string | null>(null);
  const [key, setKey] = useState<string | null>(null);
  const curKey = editing?.id ?? "new";
  if (open && key !== curKey) {
    setKey(curKey);
    setErr(null);
    setF(
      editing
        ? { name: editing.name, provider: editing.provider, endpoint: editing.endpoint, auth_type: editing.auth_type, secret_ref: editing.secret.ref ?? "", timeout_s: editing.timeout_s, retries: editing.retries, enabled: editing.enabled, read_only: editing.read_only, analysis_source: editing.analysis_source }
        : EMPTY,
    );
  }
  if (!open && key !== null) setKey(null);
  const save = useSettingsAction<Form, Connector>(
    (v) => {
      const body = { ...v, secret_ref: v.auth_type === "bearer" ? v.secret_ref || null : null };
      if (!editing) return api.post<Connector>("/settings/connectors", body);
      const { name: _n, provider: _p, ...rest } = body;      // ad ve provider oluşturulduktan sonra değişmez
      void _n;
      void _p;
      return api.patch<Connector>(`/settings/connectors/${editing.id}`, { ...rest, revision: editing.revision });
    },
    "Kaydedilemedi",
    (c) => `Connector kaydedildi: ${c.name}`,
  );
  const prov = meta.providers.find((p) => p.id === f.provider);
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((x) => ({ ...x, [k]: v }));
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={editing ? `Connector düzenle: ${editing.name}` : "MCP connector ekle"}
      description="Yalnız Streamable HTTP. Endpoint SSRF korumasından geçer (https, public adres veya deployment allowlist'i)."
      wide
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>Vazgeç</Button>
          <Button
            variant="primary"
            loading={save.isPending}
            onClick={() =>
              save.mutate(f, {
                onSuccess: () => onOpenChange(false),
                onError: (e) => setErr((e as ApiError).message),
              })
            }
          >
            Kaydet
          </Button>
        </>
      }
    >
      <div className="grid gap-3 md:grid-cols-2">
        <div>
          <Label htmlFor="c-name">Ad (namespace)</Label>
          <Input id="c-name" value={f.name} disabled={!!editing} onChange={(e) => set("name", e.target.value)} placeholder="ör. dynatrace" />
          <p className="mt-0.5 text-2xs text-muted">Tool kimlikleri bu adla namespace'lenir: ad/tool</p>
        </div>
        <div>
          <Label htmlFor="c-prov">Provider</Label>
          <Select id="c-prov" value={f.provider} disabled={!!editing} onChange={(e) => set("provider", e.target.value)}>
            {meta.providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </Select>
          <p className="mt-0.5 text-2xs text-muted">{prov?.analysis ? `Analiz adapter'ı: ${prov.adapter}` : "Analiz kaynağı olamaz (adapter yok); test ve keşif yapılabilir"}</p>
        </div>
        <div className="md:col-span-2">
          <Label htmlFor="c-url">Endpoint</Label>
          <Input id="c-url" className="font-mono" value={f.endpoint} onChange={(e) => set("endpoint", e.target.value)} />
        </div>
        <div>
          <Label htmlFor="c-auth">Kimlik doğrulama</Label>
          <Select id="c-auth" value={f.auth_type} onChange={(e) => set("auth_type", e.target.value as Form["auth_type"])}>
            <option value="bearer">Bearer token (secret)</option>
            <option value="none">Yok</option>
          </Select>
        </div>
        {f.auth_type === "bearer" && (
          <div>
            <Label htmlFor="c-secret">Token secret referansı</Label>
            <SecretRefSelect id="c-secret" value={f.secret_ref} onChange={(v) => set("secret_ref", v)} />
          </div>
        )}
        <div>
          <Label htmlFor="c-to">Timeout (sn)</Label>
          <Input id="c-to" type="number" min={1} max={300} value={f.timeout_s} onChange={(e) => set("timeout_s", Number(e.target.value))} />
        </div>
        <div>
          <Label htmlFor="c-rt">Retry</Label>
          <Input id="c-rt" type="number" min={0} max={5} value={f.retries} onChange={(e) => set("retries", Number(e.target.value))} />
        </div>
        <div className="flex flex-wrap gap-4 md:col-span-2">
          {(
            [
              ["enabled", "Etkin"],
              ["read_only", "Read-only politika"],
              ["analysis_source", "Analiz veri kaynağı"],
            ] as const
          ).map(([k, l]) => (
            <label key={k} className="flex items-center gap-2 text-xs text-ink-2">
              <Toggle checked={f[k]} onChange={(v) => set(k, v)} label={l} disabled={k === "analysis_source" && !prov?.analysis} /> {l}
            </label>
          ))}
        </div>
      </div>
      {err && (
        <Notice tone="critical" className="mt-3">
          {err}
        </Notice>
      )}
    </Dialog>
  );
}

function ToolsDialog({ c, onClose }: { c: Connector; onClose: () => void }) {
  const [allowed, setAllowed] = useState<string[]>(c.allowed_tools);
  const { can } = useAuth();
  const save = useSettingsAction<void, Connector>(
    () => api.patch(`/settings/connectors/${c.id}`, { revision: c.revision, allowed_tools: allowed }),
    "Tool onayı kaydedilemedi",
    () => "Onaylı tool listesi kaydedildi",
  );
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`${c.name} — keşfedilen tool'lar`}
      description="Agent yalnız onaylanan tool'ları kullanabilir. Read-only politikada yazma/yıkıcı annotation'lı tool onaylanamaz. Açıklamalar sunucudan gelir (güvenilmeyen metin)."
      wide
      footer={
        can("admin") && (
          <Button variant="primary" loading={save.isPending} onClick={() => save.mutate(undefined, { onSuccess: onClose })}>
            Onayı kaydet
          </Button>
        )
      }
    >
      {c.tools.length === 0 ? (
        <EmptyState title="Henüz keşif yapılmadı" description="“Tool'ları keşfet” ile sunucunun tool listesini alın." />
      ) : (
        <table className="data-table w-full">
          <thead>
            <tr>
              <th>Onay</th>
              <th>Tool</th>
              <th>Annotation</th>
              <th>Açıklama</th>
            </tr>
          </thead>
          <tbody>
            {c.tools.map((t) => {
              const unsafe = c.read_only && (t.destructive || t.read_only === false);
              return (
                <tr key={t.name}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`${t.name} onayı`}
                      disabled={!can("admin") || (unsafe && !allowed.includes(t.name))}
                      checked={allowed.includes(t.name)}
                      onChange={(e) => setAllowed((a) => (e.target.checked ? [...a, t.name] : a.filter((x) => x !== t.name)))}
                    />
                  </td>
                  <td className="font-mono text-xs">
                    {c.name}/{t.name}
                    {c.needs_review?.includes(t.name) && (
                      <div className="font-sans text-2xs text-st-critical-ink">tanım onaydan sonra değişti — inceleyip yeniden onaylayın</div>
                    )}
                  </td>
                  <td className="text-xs">{t.destructive ? "destructive" : t.read_only ? "read-only" : t.read_only === false ? "yazma" : "belirtilmemiş"}</td>
                  <td className="max-w-md text-xs text-ink-2">{t.description}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Dialog>
  );
}

export function ConnectorsSection() {
  const q = useConnectors();
  const { can } = useAuth();
  const admin = can("admin");
  const [edit, setEdit] = useState<{ open: boolean; c: Connector | null }>({ open: false, c: null });
  const [tools, setTools] = useState<Connector | null>(null);
  const [del, setDel] = useState<Connector | null>(null);
  const [result, setResult] = useState<Record<string, TestResult>>({});
  const test = useSettingsAction<string, TestResult>((id) => api.post(`/settings/connectors/${id}/test`), "Test başarısız");
  const disc = useSettingsAction<string, TestResult>((id) => api.post(`/settings/connectors/${id}/discover`), "Keşif başarısız");
  const remove = useSettingsAction<string>((id) => api.del(`/settings/connectors/${id}`), "Silinemedi", () => "Connector silindi");
  const toggle = useSettingsAction<Connector>((c) => api.patch(`/settings/connectors/${c.id}`, { revision: c.revision, enabled: !c.enabled }), "Güncellenemedi");

  if (q.isLoading) return <TableSkeleton rows={3} cols={6} />;
  if (q.isError || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const d = q.data;
  return (
    <div className="space-y-3">
      <Card>
        <CardHeader
          title="MCP Servers"
          description="Bağlantı testi (initialize) ile tool keşfi (tools/list) ayrıdır. Analiz grafiği yalnız 'analiz veri kaynağı' connector'ının onaylı execute-dql tool'unu kullanır."
          actions={
            admin && (
              <Button size="sm" variant="primary" onClick={() => setEdit({ open: true, c: null })}>
                <Plus /> Connector ekle
              </Button>
            )
          }
        />
        {d.items.length === 0 ? (
          <EmptyState title="Connector yok" description="Analiz için Dynatrace Remote MCP connector'ı ekleyin." />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table w-full">
              <thead>
                <tr>
                  <th>Ad</th>
                  <th>Endpoint</th>
                  <th>Auth</th>
                  <th>Durum</th>
                  <th>Tool'lar</th>
                  <th>Son test</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {d.items.map((c) => (
                  <tr key={c.id}>
                    <td>
                      <div className="flex flex-wrap items-center gap-1">
                        <span className="font-medium text-ink">{c.name}</span>
                        {c.analysis_source && <Badge className="text-2xs">analiz kaynağı</Badge>}
                        {c.read_only && <Badge className="text-2xs text-muted">read-only</Badge>}
                        {c.managed === "bootstrap" && <Badge className="text-2xs text-muted" title="Ortamdan bir kez oluşturuldu">ortam</Badge>}
                      </div>
                      <div className="text-2xs text-muted">{d.providers.find((p) => p.id === c.provider)?.label ?? c.provider}</div>
                    </td>
                    <td className="max-w-64 truncate font-mono text-xs" title={c.endpoint}>
                      {c.endpoint}
                    </td>
                    <td>{c.auth_type === "bearer" ? <SecretRefBadge secret={c.secret} /> : "yok"}</td>
                    <td>
                      <Toggle checked={c.enabled} disabled={!admin || toggle.isPending} label={`${c.name} etkin`} onChange={() => toggle.mutate(c)} />
                    </td>
                    <td className="text-xs">
                      <button type="button" className="text-accent hover:underline" onClick={() => setTools(c)}>
                        {c.allowed_tools.length} onaylı / {c.tools.length} keşfedilen
                      </button>
                      {!!c.needs_review?.length && (
                        <div className="text-2xs text-st-critical-ink">{c.needs_review.length} tool yeniden inceleme bekliyor (kullanılmıyor)</div>
                      )}
                    </td>
                    <td>
                      <CheckStatus check={c.last_check} />
                      {result[c.id] && !result[c.id].ok && <div className="max-w-64 truncate text-2xs text-st-critical-ink">{result[c.id].message}</div>}
                      {c.last_check?.last_success_at && <div className="text-2xs text-muted">son başarı {fmtRelative(c.last_check.last_success_at)}</div>}
                    </td>
                    <td className="text-right whitespace-nowrap">
                      {admin && (
                        <>
                          <Button size="sm" variant="ghost" title="Bağlantı testi" loading={test.isPending && test.variables === c.id} onClick={() => test.mutate(c.id, { onSuccess: (r) => setResult((x) => ({ ...x, [c.id]: r })) })}>
                            <FlaskConical /> Test
                          </Button>
                          <Button size="sm" variant="ghost" title="Tool keşfi" loading={disc.isPending && disc.variables === c.id} onClick={() => disc.mutate(c.id, { onSuccess: (r) => setResult((x) => ({ ...x, [c.id]: r })) })}>
                            <Search /> Keşfet
                          </Button>
                          <Button size="icon" variant="ghost" aria-label={`${c.name} düzenle`} onClick={() => setEdit({ open: true, c })}>
                            <Pencil />
                          </Button>
                          <Button size="icon" variant="ghost" aria-label={`${c.name} sil`} disabled={c.analysis_source} onClick={() => setDel(c)}>
                            <Trash2 />
                          </Button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <Card>
        <CardHeader title="Desteklenen transport'lar" />
        <CardBody>
          <ul className="space-y-0.5 text-xs text-ink-2">
            {d.transports.map((t) => (
              <li key={t.id}>
                {t.label}: {t.supported ? "destekleniyor" : `desteklenmiyor — ${t.reason}`}
              </li>
            ))}
            <li>Outbound allowlist: {d.outbound_allowlist.length ? d.outbound_allowlist.join(", ") : "boş (yalnız public https)"}</li>
          </ul>
        </CardBody>
      </Card>
      <ConnectorDialog open={edit.open} onOpenChange={(o) => setEdit((x) => ({ ...x, open: o }))} editing={edit.c} meta={d} />
      {tools && <ToolsDialog c={d.items.find((x) => x.id === tools.id) ?? tools} onClose={() => setTools(null)} />}
      <ConfirmDialog
        open={!!del}
        onOpenChange={(o) => !o && setDel(null)}
        title="Connector silinsin mi?"
        danger
        confirmLabel="Sil"
        busy={remove.isPending}
        onConfirm={() => del && remove.mutate(del.id, { onSuccess: () => setDel(null) })}
      >
        <p>
          <b>{del?.name}</b> connector'ı ve keşfedilen tool listesi silinir. Kullandığı secret silinmez.
        </p>
      </ConfirmDialog>
    </div>
  );
}
