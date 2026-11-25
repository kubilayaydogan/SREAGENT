import { History, RotateCcw, Save, Undo2 } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Button, Card, CardBody, CardHeader, Input, Select } from "@/components/ui/primitives";
import { ErrorState, Notice, TableSkeleton } from "@/components/ui/states";
import { ApiError } from "@/services/api";
import { useAuth } from "@/services/auth";
import { fmtTime } from "@/lib/utils";
import { useRevisions, useRollback, useSaveSection, useSection, type FieldInfo, type SectionId, type SectionView } from "./api";
import { ApplyBadge, ConfirmDialog, LeaveDialog, SectionMeta, SourceBadge, Toggle, show, useDirtyGuard } from "./common";

export type Draft = Record<string, unknown>;
export type FieldRenderer = (p: { name: string; field: FieldInfo; value: unknown; onChange: (v: unknown) => void; disabled: boolean }) => ReactNode | undefined;

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** Tek bir alan: etiket + açıklama + kaynak/uygulama rozetleri + denetim. */
function FieldRow({ name, field, value, onChange, disabled, onReset, resetPending, render }: {
  name: string;
  field: FieldInfo;
  value: unknown;
  onChange: (v: unknown) => void;
  disabled: boolean;
  onReset: () => void;
  resetPending: boolean;
  render?: FieldRenderer;
}) {
  const id = `f-${name}`;
  const custom = render?.({ name, field, value, onChange, disabled });
  let control: ReactNode = custom;
  if (control === undefined) {
    if (field.kind === "bool") control = <Toggle id={id} checked={!!value} onChange={onChange} disabled={disabled} label={field.label} />;
    else if (field.kind === "enum")
      control = (
        <Select id={id} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} disabled={disabled} className="w-56">
          {(field.options ?? []).map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </Select>
      );
    else if (field.kind === "int" || field.kind === "float")
      control = (
        <div className="flex items-center gap-1.5">
          <Input
            id={id}
            type="number"
            inputMode="decimal"
            className="w-28"
            value={value === null || value === undefined ? "" : String(value)}
            min={field.min ?? undefined}
            max={field.max ?? undefined}
            step={field.kind === "float" ? "any" : 1}
            disabled={disabled}
            onChange={(e) => onChange(e.target.value === "" ? null : field.kind === "int" ? Math.trunc(Number(e.target.value)) : Number(e.target.value))}
          />
          {field.unit && <span className="text-xs text-muted">{field.unit}</span>}
          {(field.min !== null || field.max !== null) && (
            <span className="text-2xs text-muted">
              {field.min ?? "…"}–{field.max ?? "…"}
            </span>
          )}
        </div>
      );
    else
      control = (
        <Input
          id={id}
          className="w-72"
          value={value === null || value === undefined ? "" : String(value)}
          disabled={disabled}
          onChange={(e) => onChange(field.nullable && e.target.value === "" ? null : e.target.value)}
        />
      );
  }
  return (
    <div className="grid gap-x-6 gap-y-1.5 border-b border-line py-3 last:border-b-0 md:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-1.5">
          <label htmlFor={id} className="text-[13px] font-medium text-ink">
            {field.label}
          </label>
          <SourceBadge source={field.source} />
          <ApplyBadge apply={field.apply} pending={field.restart_pending} />
        </div>
        {field.help && <p className="mt-0.5 text-xs text-muted">{field.help}</p>}
        {field.env && <p className="mt-0.5 font-mono text-2xs text-muted">env: {field.env}</p>}
      </div>
      <div className="min-w-0">
        {control}
        {field.source === "admin" && (
          <div className="mt-1 flex items-center gap-2 text-2xs text-muted">
            <span>Devralınan: {show(field.inherited)}</span>
            {!disabled && (
              <button type="button" className="inline-flex items-center gap-0.5 text-accent hover:underline" onClick={onReset}>
                <RotateCcw className="size-3" aria-hidden /> {resetPending ? "sıfırlanacak" : "devralınana dön"}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function RevisionsDialog({ section, view, open, onOpenChange }: { section: SectionId; view: SectionView; open: boolean; onOpenChange: (v: boolean) => void }) {
  const { data } = useRevisions(section, open);
  const rb = useRollback(section);
  const [target, setTarget] = useState<number | null>(null);
  const { can } = useAuth();
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Revizyon geçmişi"
      confirmLabel={target ? `Revizyon ${target}'e geri al` : "Revizyon seçin"}
      disabled={!target || !can("admin")}
      busy={rb.isPending}
      onConfirm={() => target && rb.mutate({ revision: view.revision, to_revision: target }, { onSuccess: () => onOpenChange(false) })}
    >
      <p>Rollback eski değerleri YENİ bir revizyon olarak uygular; geçmiş silinmez. Secret değerleri geri getirilmez.</p>
      <div className="max-h-80 overflow-y-auto rounded-md border border-line">
        {(data?.items ?? []).map((r) => (
          <label key={r.revision} className="flex cursor-pointer items-start gap-2 border-b border-line px-2 py-1.5 last:border-b-0 hover:bg-surface-2">
            <input type="radio" name="rev" className="mt-0.5" disabled={r.revision === view.revision} checked={target === r.revision} onChange={() => setTarget(r.revision)} />
            <div className="min-w-0">
              <div className="text-xs text-ink">
                #{r.revision} · {r.actor} · {fmtTime(r.created_at)} {r.revision === view.revision && <span className="text-muted">(güncel)</span>}
              </div>
              {r.note && <div className="text-2xs text-muted">{r.note}</div>}
              <code className="block truncate font-mono text-2xs text-muted">{JSON.stringify(r.data)}</code>
            </div>
          </label>
        ))}
        {data && data.items.length === 0 && <p className="p-2 text-muted">Henüz revizyon yok.</p>}
      </div>
    </ConfirmDialog>
  );
}

/**
 * Şemadan üretilen bölüm formu. Kaydet/vazgeç bölüm başınadır; yalnız değişen alanlar gönderilir. Başkası araya
 * girip kaydettiyse (409) taslak korunur ve kullanıcıya yenileme önerilir. Yetkisiz kullanıcı formu salt okunur görür.
 */
export function SectionForm({ section, title, description, fields, render, children, aside }: {
  section: SectionId;
  title: string;
  description?: ReactNode;
  /** Gösterilecek alanlar ve sırası (yoksa tümü). */
  fields?: string[];
  render?: FieldRenderer;
  /** Form altı: taslağa erişen bölüm özel içerik (önizleme, iş listesi…). */
  children?: (draft: Draft, view: SectionView) => ReactNode;
  aside?: (view: SectionView) => ReactNode;
}) {
  const q = useSection(section);
  const save = useSaveSection(section);
  const { can } = useAuth();
  const admin = can("admin");
  const [draft, setDraft] = useState<Draft>({});
  const [reset, setReset] = useState<string[]>([]);
  const [base, setBase] = useState<number | null>(null);
  const [conflict, setConflict] = useState<number | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [revOpen, setRevOpen] = useState(false);
  const view = q.data;

  // Sunucu revizyonu değiştiğinde (ilk yükleme, kendi kaydımız, rollback) taslağı sunucu değerleriyle başlat.
  useEffect(() => {
    if (view && view.revision !== base) {
      setDraft(structuredClone(view.values));
      setReset([]);
      setBase(view.revision);
    }
  }, [view, base]);

  const changed = useMemo(() => (view ? Object.keys(draft).filter((k) => !same(draft[k], view.values[k])) : []), [draft, view]);
  const dirty = changed.length > 0 || reset.length > 0;
  const blocker = useDirtyGuard(dirty);

  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  // Taslak sunucu değerleriyle eşlenmeden (ilk render, revizyon değişimi) form çizilmez: özel editörler boş değer görmez.
  if (q.isLoading || !view || base !== view.revision) return <TableSkeleton rows={6} cols={2} />;

  const names = (fields ?? Object.keys(view.fields)).filter((n) => view.fields[n]);
  const discard = () => {
    setDraft(structuredClone(view.values));
    setReset([]);
    setErr(null);
    setConflict(null);
  };
  const submit = () => {
    setErr(null);
    const values = Object.fromEntries(changed.filter((k) => !reset.includes(k)).map((k) => [k, draft[k]]));
    save.mutate(
      { revision: view.revision, values, reset },
      {
        onError: (e) => {
          const ae = e as ApiError;
          if (ae.status === 409 && ae.code === "revision_conflict") setConflict((ae.details as { current_revision?: number })?.current_revision ?? null);
          else setErr(ae);
        },
        onSuccess: () => setConflict(null),
      },
    );
  };

  return (
    <div className="space-y-3">
      <Card>
        <CardHeader
          title={title}
          description={<SectionMeta revision={view.revision} by={view.updated_by} at={view.updated_at} />}
          actions={
            <Button size="sm" variant="ghost" onClick={() => setRevOpen(true)} disabled={!view.revision}>
              <History /> Revizyonlar
            </Button>
          }
        />
        <CardBody className="py-0">
          {description && <div className="border-b border-line py-2 text-xs text-muted">{description}</div>}
          {!admin && (
            <Notice tone="info" className="my-2">
              Salt okunur: ayarları yalnız yönetici (admin) değiştirebilir.
            </Notice>
          )}
          {names.map((n) => (
            <FieldRow
              key={n}
              name={n}
              field={view.fields[n]}
              value={draft[n]}
              disabled={!admin || view.fields[n].locked}
              render={render}
              resetPending={reset.includes(n)}
              onChange={(v) => {
                setDraft((d) => ({ ...d, [n]: v }));
                setReset((r) => r.filter((x) => x !== n));
              }}
              onReset={() => {
                setReset((r) => (r.includes(n) ? r.filter((x) => x !== n) : [...r, n]));
                setDraft((d) => ({ ...d, [n]: view.values[n] }));
              }}
            />
          ))}
        </CardBody>
        {admin && (
          <div className="sticky bottom-0 flex flex-wrap items-center justify-between gap-2 rounded-b-lg border-t border-line bg-surface px-3 py-2">
            <span className="text-xs text-muted" aria-live="polite">
              {dirty ? `${changed.length + reset.filter((r) => !changed.includes(r)).length} alan değişti (kaydedilmedi)` : "Değişiklik yok"}
            </span>
            <div className="flex gap-2">
              <Button size="sm" onClick={discard} disabled={!dirty || save.isPending}>
                <Undo2 /> Vazgeç
              </Button>
              <Button size="sm" variant="primary" onClick={submit} disabled={!dirty} loading={save.isPending}>
                <Save /> Kaydet
              </Button>
            </div>
          </div>
        )}
      </Card>
      {conflict !== null && (
        <Notice tone="warning">
          Bu bölüm siz düzenlerken başka biri tarafından kaydedildi (güncel revizyon {conflict}). Değişiklikleriniz korunuyor; güncel değerleri görmek
          için{" "}
          <button
            type="button"
            className="text-accent underline"
            onClick={() => {
              setConflict(null);
              setBase(null);
              q.refetch();
            }}
          >
            yenileyin
          </button>{" "}
          (taslak atılır) ve tekrar uygulayın.
        </Notice>
      )}
      {err && (
        <Notice tone="critical">
          <div className="font-medium text-ink">{err.message}</div>
          {Array.isArray(err.details) &&
            (err.details as { loc?: (string | number)[]; msg?: string }[]).map((d, i) => (
              <div key={i}>
                {(d.loc ?? []).filter((x) => x !== "body" && x !== "values").join(".")}: {d.msg}
              </div>
            ))}
        </Notice>
      )}
      {aside?.(view)}
      {children?.(draft, view)}
      <RevisionsDialog section={section} view={view} open={revOpen} onOpenChange={setRevOpen} />
      <LeaveDialog blocker={blocker} />
    </div>
  );
}
