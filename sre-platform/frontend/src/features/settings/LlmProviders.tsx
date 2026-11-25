import { FlaskConical, ListTree, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Card, CardHeader, Input, Label, Select } from "@/components/ui/primitives";
import { Dialog } from "@/components/ui/overlay";
import { EmptyState, ErrorState, Notice, TableSkeleton } from "@/components/ui/states";
import { useLlmModel } from "@/hooks/queries";
import { api, type ApiError } from "@/services/api";
import { useAuth } from "@/services/auth";
import { fmtUsd } from "@/pages/LlmObservabilityPage";
import { useLlmProfiles, useSecrets, useSettingsAction, type LlmProfile, type LlmProfileList, type TestResult } from "./api";
import { CheckStatus, ConfirmDialog, SecretRefBadge, Toggle } from "./common";
import { SectionForm, type FieldRenderer } from "./SectionForm";

interface Form {
  name: string;
  provider: string;
  model: string;
  base_url: string;
  secret_ref: string;
  timeout_s: number;
  max_tokens: number;
  effort: string;
  retries: number;
  monthly_budget_usd: string;
  enabled: boolean;
}

const EMPTY: Form = { name: "", provider: "anthropic", model: "claude-opus-5-5", base_url: "", secret_ref: "env:ANTHROPIC_API_KEY", timeout_s: 120, max_tokens: 16000, effort: "medium", retries: 2, monthly_budget_usd: "", enabled: true };

function ProfileDialog({ open, onOpenChange, editing, meta }: { open: boolean; onOpenChange: (v: boolean) => void; editing: LlmProfile | null; meta: LlmProfileList }) {
  const [f, setF] = useState<Form>(EMPTY);
  const [key, setKey] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [models, setModels] = useState<{ id: string; display_name: string }[] | null>(null);
  const sec = useSecrets();
  const cur = editing?.id ?? "new";
  if (open && key !== cur) {
    setKey(cur);
    setErr(null);
    setModels(null);
    setF(
      editing
        ? { name: editing.name, provider: editing.provider, model: editing.model, base_url: editing.base_url ?? "", secret_ref: editing.secret.ref ?? "", timeout_s: editing.timeout_s, max_tokens: editing.max_tokens, effort: editing.effort ?? "", retries: editing.retries, monthly_budget_usd: editing.monthly_budget_usd?.toString() ?? "", enabled: editing.enabled }
        : EMPTY,
    );
  }
  if (!open && key !== null) setKey(null);
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((x) => ({ ...x, [k]: v }));
  const save = useSettingsAction<Form, LlmProfile>(
    (v) => {
      const body = {
        model: v.model,
        base_url: v.base_url || null,
        secret_ref: v.secret_ref,
        timeout_s: v.timeout_s,
        max_tokens: v.max_tokens,
        effort: v.effort || null,
        retries: v.retries,
        monthly_budget_usd: v.monthly_budget_usd ? Number(v.monthly_budget_usd) : editing ? 0 : null,
        enabled: v.enabled,
      };
      return editing
        ? api.patch<LlmProfile>(`/settings/llm-providers/${editing.id}`, { ...body, revision: editing.revision })
        : api.post<LlmProfile>("/settings/llm-providers", { ...body, name: v.name, provider: v.provider });
    },
    "Profil kaydedilemedi",
    (p) => `Profil kaydedildi: ${p.name}`,
  );
  const listModels = useSettingsAction<string, { items: { id: string; display_name: string }[] }>((id) => api.get(`/settings/llm-providers/${id}/models`), "Model listesi alınamadı");
  const refs = [...(sec.data?.stored ?? []).map((x) => x.ref), ...(sec.data?.env ?? []).map((x) => x.ref ?? "")].filter(Boolean);
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={editing ? `Profil düzenle: ${editing.name}` : "LLM profili ekle"}
      description="Yalnız doğrulanmış adapter'ı olan provider'lar kullanılabilir. API key secret olarak tutulur; burada yalnız referansı seçilir."
      wide
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>Vazgeç</Button>
          <Button variant="primary" loading={save.isPending} onClick={() => save.mutate(f, { onSuccess: () => onOpenChange(false), onError: (e) => setErr((e as ApiError).message) })}>
            Kaydet
          </Button>
        </>
      }
    >
      <div className="grid gap-3 md:grid-cols-2">
        <div>
          <Label htmlFor="p-name">Ad</Label>
          <Input id="p-name" value={f.name} disabled={!!editing} onChange={(e) => set("name", e.target.value)} placeholder="ör. claude-rca" />
        </div>
        <div>
          <Label htmlFor="p-prov">Provider</Label>
          <Select id="p-prov" value={f.provider} disabled={!!editing} onChange={(e) => set("provider", e.target.value)}>
            {meta.providers.map((p) => (
              <option key={p.id} value={p.id} disabled={!p.supported}>
                {p.label}
                {p.supported ? "" : " — adapter yok"}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <Label htmlFor="p-model">Model</Label>
          <div className="flex gap-1.5">
            <Input id="p-model" className="font-mono" list="p-models" value={f.model} onChange={(e) => set("model", e.target.value)} />
            {editing && (
              <Button size="sm" title="Provider'dan model listesini al" loading={listModels.isPending} onClick={() => listModels.mutate(editing.id, { onSuccess: (r) => setModels(r.items) })}>
                <ListTree />
              </Button>
            )}
          </div>
          <datalist id="p-models">
            {(models ?? []).map((m) => (
              <option key={m.id} value={m.id}>
                {m.display_name}
              </option>
            ))}
          </datalist>
        </div>
        <div>
          <Label htmlFor="p-secret">API key secret referansı</Label>
          <Select id="p-secret" value={f.secret_ref} onChange={(e) => set("secret_ref", e.target.value)}>
            {Array.from(new Set([f.secret_ref, ...refs].filter(Boolean))).map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </Select>
        </div>
        <div className="md:col-span-2">
          <Label htmlFor="p-base">Base URL (opsiyonel, proxy/gateway)</Label>
          <Input id="p-base" className="font-mono" value={f.base_url} onChange={(e) => set("base_url", e.target.value)} placeholder="boş = provider varsayılanı" />
        </div>
        <div>
          <Label htmlFor="p-max">Max output token</Label>
          <Input id="p-max" type="number" min={256} max={128000} value={f.max_tokens} onChange={(e) => set("max_tokens", Number(e.target.value))} />
        </div>
        <div>
          <Label htmlFor="p-effort">Effort</Label>
          <Select id="p-effort" value={f.effort} onChange={(e) => set("effort", e.target.value)}>
            <option value="">(gönderilmez)</option>
            <option value="low">low</option>
            <option value="medium">medium</option>
            <option value="high">high</option>
          </Select>
        </div>
        <div>
          <Label htmlFor="p-to">Timeout (sn)</Label>
          <Input id="p-to" type="number" min={5} max={600} value={f.timeout_s} onChange={(e) => set("timeout_s", Number(e.target.value))} />
        </div>
        <div>
          <Label htmlFor="p-rt">Retry</Label>
          <Input id="p-rt" type="number" min={0} max={5} value={f.retries} onChange={(e) => set("retries", Number(e.target.value))} />
        </div>
        <div>
          <Label htmlFor="p-budget">Aylık bütçe (USD)</Label>
          <Input id="p-budget" type="number" min={0} step="any" value={f.monthly_budget_usd} onChange={(e) => set("monthly_budget_usd", e.target.value)} placeholder="boş = sınırsız" />
          <p className="mt-0.5 text-2xs text-muted">Aşılınca o görevde LLM kullanılmaz (deterministik açıklama). Maliyet LangSmith senkronundan gelir.</p>
        </div>
        <label className="flex items-center gap-2 self-end text-xs text-ink-2">
          <Toggle checked={f.enabled} onChange={(v) => set("enabled", v)} label="Etkin" /> Etkin
        </label>
      </div>
      {err && (
        <Notice tone="critical" className="mt-3">
          {err}
        </Notice>
      )}
    </Dialog>
  );
}

function ProfilesTable() {
  const q = useLlmProfiles();
  const { can } = useAuth();
  const admin = can("admin");
  const [edit, setEdit] = useState<{ open: boolean; p: LlmProfile | null }>({ open: false, p: null });
  const [del, setDel] = useState<LlmProfile | null>(null);
  const [res, setRes] = useState<Record<string, TestResult>>({});
  const test = useSettingsAction<string, TestResult>((id) => api.post(`/settings/llm-providers/${id}/test`), "Test başarısız");
  const remove = useSettingsAction<string>((id) => api.del(`/settings/llm-providers/${id}`), "Silinemedi", () => "Profil silindi");
  if (q.isLoading) return <TableSkeleton rows={3} cols={6} />;
  if (q.isError || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const d = q.data;
  const routed = (n: string) => (["chat", "rca"] as const).filter((t) => d.routing[t] === n);
  return (
    <Card>
      <CardHeader
        title="Profiller"
        description="Test: provider'a prompt göndermeden model kimliğini ve anahtarı doğrular (models.retrieve); bildirilen yetenekleri gösterir."
        actions={
          admin && (
            <Button size="sm" variant="primary" onClick={() => setEdit({ open: true, p: null })}>
              <Plus /> Profil ekle
            </Button>
          )
        }
      />
      {d.items.length === 0 ? (
        <EmptyState title="Profil yok" description="Ortamda ANTHROPIC_API_KEY varsa açılışta iki profil otomatik oluşturulur." />
      ) : (
        <div className="overflow-x-auto">
          <table className="data-table w-full">
            <thead>
              <tr>
                <th>Ad</th>
                <th>Model</th>
                <th>Secret</th>
                <th>Bu ay</th>
                <th>Doğrulama</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {d.items.map((p) => {
                const r = res[p.id];
                return (
                  <tr key={p.id}>
                    <td>
                      <div className="flex flex-wrap items-center gap-1">
                        <span className="font-medium text-ink">{p.name}</span>
                        {routed(p.name).map((t) => (
                          <Badge key={t} className="text-2xs">
                            {t}
                          </Badge>
                        ))}
                        {!p.enabled && <Badge className="text-2xs text-muted">kapalı</Badge>}
                      </div>
                      <div className="text-2xs text-muted">
                        {p.provider_label} · max {p.max_tokens} · effort {p.effort ?? "—"} · {p.timeout_s} sn
                      </div>
                    </td>
                    <td className="font-mono text-xs">{p.model}</td>
                    <td>
                      <SecretRefBadge secret={p.secret} />
                    </td>
                    <td className="text-xs">
                      {fmtUsd(p.month_cost_usd)}
                      {p.monthly_budget_usd != null && <span className="text-muted"> / {fmtUsd(p.monthly_budget_usd)}</span>}
                    </td>
                    <td>
                      <CheckStatus check={p.last_check} />
                      {r?.ok && Array.isArray(r.warnings) && r.warnings.map((w) => <div key={w} className="text-2xs text-st-warning-ink">{w}</div>)}
                      {r && !r.ok && <div className="max-w-64 truncate text-2xs text-st-critical-ink">{r.message}</div>}
                    </td>
                    <td className="text-right whitespace-nowrap">
                      {admin && (
                        <>
                          <Button size="sm" variant="ghost" loading={test.isPending && test.variables === p.id} onClick={() => test.mutate(p.id, { onSuccess: (x) => setRes((s) => ({ ...s, [p.id]: x })) })}>
                            <FlaskConical /> Test
                          </Button>
                          <Button size="icon" variant="ghost" aria-label={`${p.name} düzenle`} onClick={() => setEdit({ open: true, p })}>
                            <Pencil />
                          </Button>
                          <Button size="icon" variant="ghost" aria-label={`${p.name} sil`} disabled={routed(p.name).length > 0} onClick={() => setDel(p)}>
                            <Trash2 />
                          </Button>
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <ProfileDialog open={edit.open} onOpenChange={(o) => setEdit((x) => ({ ...x, open: o }))} editing={edit.p} meta={d} />
      <ConfirmDialog open={!!del} onOpenChange={(o) => !o && setDel(null)} title="Profil silinsin mi?" danger confirmLabel="Sil" busy={remove.isPending} onConfirm={() => del && remove.mutate(del.id, { onSuccess: () => setDel(null) })}>
        <p>
          <b>{del?.name}</b> profili silinir. Görev yönlendirmesinde kullanılan profil silinemez.
        </p>
      </ConfirmDialog>
    </Card>
  );
}

export function LlmProvidersSection() {
  const prof = useLlmProfiles();
  const model = useLlmModel();
  const names = (prof.data?.items ?? []).filter((p) => p.enabled).map((p) => p.name);
  const render: FieldRenderer = ({ name, value, onChange, disabled }) => {
    if (name === "chat_profile" || name === "rca_profile")
      return (
        <Select className="w-64" value={String(value ?? "")} disabled={disabled} onChange={(e) => onChange(e.target.value || null)} aria-label={name}>
          <option value="">Ortam (CLAUDE_MODEL / CLAUDE_RCA_MODEL)</option>
          {Array.from(new Set([...(value ? [String(value)] : []), ...names])).map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </Select>
      );
    if (name === "model_override")
      return (
        <Select className="w-64" value={String(value ?? "")} disabled={disabled} onChange={(e) => onChange(e.target.value || null)} aria-label={name}>
          <option value="">Seçim yok (profil / ortam)</option>
          {(model.data?.options ?? []).map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </Select>
      );
    return undefined;
  };
  return (
    <SectionForm
      section="llm"
      title="LLM Providers — görev yönlendirmesi"
      description="Yalnız gerçekten LLM kullanan adımlar için model seçilir: chat (soru yorumlama, yönlendirme, sohbet cevabı) ve RCA (açıklama). Üst bardaki model seçimi profil modelini ezer."
      render={render}
      aside={(v) => <Notice tone="info">{String((v.status as { fallback: string }).fallback)}</Notice>}
    >
      {() => <ProfilesTable />}
    </SectionForm>
  );
}
