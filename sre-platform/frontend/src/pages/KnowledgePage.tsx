import { BadgeCheck, Plus, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { AppTag, IncidentStatusBadge, InvestigationStatusBadge, SeverityBadge } from "@/components/badges";
import { AppSelect, SearchInput } from "@/components/filters";
import { Markdown } from "@/components/Markdown";
import { Button, Card, CardBody, Input, Label, Select, Skeleton, Textarea } from "@/components/ui/primitives";
import { Dialog, TabPanel, Tabs } from "@/components/ui/overlay";
import { EmptyState, ErrorState, Page, PageHeader, Pagination } from "@/components/ui/states";
import { errorToast, useKnownIssue, useKnownIssues, useRunbooks } from "@/hooks/queries";
import { api } from "@/services/api";
import { useAuth } from "@/services/auth";
import { MECHANISM_LABEL, mechanismLabel } from "@/lib/steps";
import { cn, fmtRelative } from "@/lib/utils";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import type { Runbook } from "@/types/api";

const RISK_CLS = { low: "text-ink-2", medium: "text-st-warning-ink", high: "text-st-serious-ink" } as const;
const RISK_TR = { low: "düşük", medium: "orta", high: "yüksek" } as const;

function IssueDetail({ id }: { id: string }) {
  const { data: k, isLoading, error } = useKnownIssue(id);
  if (error) return <ErrorState error={error} />;
  if (isLoading || !k) return <Skeleton className="m-4 h-64" />;
  return (
    <div className="space-y-4 p-4 text-[13px]">
      <div>
        <h2 className="text-[15px] font-semibold text-ink">{k.title}</h2>
        <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
          {k.verified && (
            <span className="inline-flex items-center gap-1 text-st-good-ink">
              <BadgeCheck className="size-3.5" /> doğrulanmış
            </span>
          )}
          {k.application_id && <AppTag id={k.application_id} />}
          {k.mechanism && <span>{mechanismLabel(k.mechanism)}</span>}
          <span>· {k.origin}</span>
          {k.tags.map((t) => (
            <span key={t} className="rounded-sm border border-line-strong px-1 text-2xs">
              {t}
            </span>
          ))}
        </div>
      </div>
      {[
        ["Açıklama", k.description],
        ["Belirtiler", k.symptoms],
        ["Root Cause", k.root_cause],
        ["Çözüm", k.resolution],
      ].map(([t, v]) =>
        v ? (
          <div key={t}>
            <div className="mb-1 text-xs font-medium text-muted">{t}</div>
            <Markdown>{v}</Markdown>
          </div>
        ) : null,
      )}
      <div>
        <div className="mb-1 text-xs font-medium text-muted">İlgili geçmiş incident'lar</div>
        {k.related_incidents.length ? (
          <ul className="divide-y divide-line rounded-md border border-line">
            {k.related_incidents.map((i) => (
              <li key={i.id}>
                <Link to={`/incidents/${i.id}`} className="flex items-center gap-2 px-3 py-2 hover:bg-surface-2">
                  <span className="min-w-0 flex-1 truncate text-ink">{i.title}</span>
                  <SeverityBadge severity={i.severity} />
                  <IncidentStatusBadge status={i.status} />
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted">Eşleşen incident yok.</p>
        )}
      </div>
      <div>
        <div className="mb-1 text-xs font-medium text-muted">Aynı problemin görüldüğü investigation'lar</div>
        {k.related_investigations.length ? (
          <ul className="divide-y divide-line rounded-md border border-line">
            {k.related_investigations.map((i) => (
              <li key={i.id}>
                <Link to={`/investigations/${i.id}`} className="flex items-center gap-2 px-3 py-2 hover:bg-surface-2">
                  <span className="min-w-0 flex-1 truncate text-ink">{i.title}</span>
                  <span className="text-xs text-muted">{fmtRelative(i.created_at)}</span>
                  <InvestigationStatusBadge status={i.status} />
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted">Eşleşen investigation yok.</p>
        )}
      </div>
    </div>
  );
}

function RunbookDetail({ rb }: { rb: Runbook }) {
  return (
    <div className="space-y-3 p-4 text-[13px]">
      <h2 className="text-[15px] font-semibold text-ink">{rb.title}</h2>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <span className={RISK_CLS[rb.risk_level]}>risk: {RISK_TR[rb.risk_level]}</span>
        {rb.requires_approval && (
          <span className="inline-flex items-center gap-1 text-st-serious-ink">
            <ShieldAlert className="size-3.5" /> uygulamadan önce onay gerekli
          </span>
        )}
        {rb.application_id && <AppTag id={rb.application_id} />}
        {rb.mechanism && <span>{mechanismLabel(rb.mechanism)}</span>}
      </div>
      {rb.description && <p className="text-ink-2">{rb.description}</p>}
      <div className="rounded-md border border-line bg-surface-2 p-3">
        <Markdown>{rb.procedure}</Markdown>
      </div>
      {rb.requires_approval && (
        <Link to="/approvals" className="inline-block text-xs text-accent-text hover:underline">
          Onay talebi oluştur →
        </Link>
      )}
    </div>
  );
}

function NewIssue({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [f, setF] = useState({ title: "", application_id: "", mechanism: "", symptoms: "", root_cause: "", resolution: "", verified: false });
  async function save() {
    setBusy(true);
    try {
      await api.post("/known-issues", { ...f, title: f.title.trim(), application_id: f.application_id || null, mechanism: f.mechanism || null });
      qc.invalidateQueries({ queryKey: ["known-issues"] });
      toast.success("Bilinen sorun eklendi");
      onOpenChange(false);
    } catch (e) {
      errorToast(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      wide
      open={open}
      onOpenChange={onOpenChange}
      title="Bilinen sorun ekle"
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Vazgeç
          </Button>
          <Button variant="primary" disabled={f.title.trim().length < 3} loading={busy} onClick={save}>
            Kaydet
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div>
          <Label htmlFor="ki-title">Başlık</Label>
          <Input id="ki-title" value={f.title} maxLength={300} onChange={(e) => setF({ ...f, title: e.target.value })} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>Servis</Label>
            <AppSelect value={f.application_id} onChange={(v) => setF({ ...f, application_id: v })} allLabel="—" className="w-full" />
          </div>
          <div>
            <Label htmlFor="ki-mech">Problem</Label>
            <Select id="ki-mech" value={f.mechanism} onChange={(e) => setF({ ...f, mechanism: e.target.value })}>
              <option value="">—</option>
              {Object.entries(MECHANISM_LABEL).map(([k, l]) => (
                <option key={k} value={k}>
                  {l}
                </option>
              ))}
            </Select>
          </div>
        </div>
        {(["symptoms", "root_cause", "resolution"] as const).map((k) => (
          <div key={k}>
            <Label htmlFor={`ki-${k}`}>{{ symptoms: "Belirtiler", root_cause: "Root Cause", resolution: "Çözüm" }[k]}</Label>
            <Textarea id={`ki-${k}`} rows={3} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} />
          </div>
        ))}
        <label className="flex items-center gap-2 text-xs text-ink-2">
          <input type="checkbox" checked={f.verified} onChange={(e) => setF({ ...f, verified: e.target.checked })} /> Doğrulanmış
        </label>
      </div>
    </Dialog>
  );
}

export default function KnowledgePage() {
  const [sp, setSp] = useSearchParams();
  const { can } = useAuth();
  const g = (k: string) => sp.get(k) ?? "";
  const set = (kv: Record<string, string>) => {
    const n = new URLSearchParams(sp);
    for (const [k, v] of Object.entries(kv)) {
      if (v) n.set(k, v);
      else n.delete(k);
    }
    setSp(n, { replace: true });
  };
  const tab = g("tab") || "issues";
  const page = Number(g("page") || 1);
  const params = { q: g("q"), application_id: g("app"), mechanism: g("mechanism"), page, size: 20 };
  const issues = useKnownIssues(params);
  const runbooks = useRunbooks(params);
  const [adding, setAdding] = useState(false);
  const list = tab === "issues" ? issues : runbooks;
  const selected = g("id");
  const selRunbook = runbooks.data?.items.find((r) => r.id === selected);

  return (
    <Page>
      <PageHeader
        title="Bilgi bankası"
        description="Bilinen sorunlar ve runbook'lar · agent benzer durumlarda bunları Recommended Actions'ta önerir"
        actions={
          can("sre") &&
          tab === "issues" && (
            <Button variant="primary" size="sm" onClick={() => setAdding(true)}>
              <Plus /> Bilinen sorun
            </Button>
          )
        }
      />
      <Tabs
        value={tab}
        onValueChange={(v) => set({ tab: v, id: "", page: "" })}
        tabs={[
          { value: "issues", label: "Bilinen sorunlar" },
          { value: "runbooks", label: "Runbook'lar" },
        ]}
      >
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <SearchInput value={g("q")} onChange={(v) => set({ q: v, page: "" })} placeholder="Ara…" />
          <AppSelect value={g("app")} onChange={(v) => set({ app: v, page: "" })} />
          <Select aria-label="mechanism" value={g("mechanism")} onChange={(e) => set({ mechanism: e.target.value, page: "" })} className="w-52">
            <option value="">Tüm problem türleri</option>
            {Object.entries(MECHANISM_LABEL).map(([k, l]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </Select>
        </div>
        {(["issues", "runbooks"] as const).map((t) => (
          <TabPanel key={t} value={t}>
            <div className="grid items-start gap-3 lg:grid-cols-[22rem_1fr]">
              <Card className="lg:sticky lg:top-3">
                {list.isLoading ? (
                  <Skeleton className="m-4 h-64" />
                ) : list.error ? (
                  <ErrorState error={list.error} onRetry={() => list.refetch()} />
                ) : !list.data?.items.length ? (
                  <EmptyState title="Kayıt yok" description="Bilgi bankası tohum kayıtları kurulumda 'sre-platform migrate --seed' ile yüklenir." />
                ) : (
                  <>
                    <ul className="divide-y divide-line">
                      {(list.data.items as { id: string; title: string; mechanism: string | null; application_id: string | null }[]).map((x) => (
                        <li key={x.id}>
                          <button
                            onClick={() => set({ id: x.id })}
                            aria-current={selected === x.id ? "true" : undefined}
                            className={cn(
                              "block w-full border-l-2 px-3 py-2 text-left hover:bg-surface-2",
                              selected === x.id ? "border-accent bg-surface-2" : "border-transparent",
                            )}
                          >
                            <div className="text-[13px] text-ink">{x.title}</div>
                            <div className="mt-0.5 flex items-center gap-2 text-2xs text-muted">
                              {mechanismLabel(x.mechanism)}
                              {x.application_id && <AppTag id={x.application_id} />}
                            </div>
                          </button>
                        </li>
                      ))}
                    </ul>
                    <Pagination page={list.data.page} pages={list.data.pages} total={list.data.total} onPage={(p) => set({ page: String(p) })} />
                  </>
                )}
              </Card>
              <Card className="min-h-64">
                {!selected ? (
                  <EmptyState title="Bir kayıt seçin" description="Soldaki listeden bir bilinen sorun ya da runbook seçin." />
                ) : t === "issues" ? (
                  <IssueDetail id={selected} />
                ) : selRunbook ? (
                  <RunbookDetail rb={selRunbook} />
                ) : (
                  <CardBody>
                    <RunbookById id={selected} />
                  </CardBody>
                )}
              </Card>
            </div>
          </TabPanel>
        ))}
      </Tabs>
      {adding && <NewIssue open={adding} onOpenChange={setAdding} />}
    </Page>
  );
}

/** Listede o sayfada olmayan runbook (ör. araştırmadan bağlantıyla gelindi). */
function RunbookById({ id }: { id: string }) {
  const { data, error } = useQuery({ queryKey: ["runbook", id], queryFn: () => api.get<Runbook>(`/runbooks/${id}`) });
  if (error) return <ErrorState error={error} />;
  return data ? <RunbookDetail rb={data} /> : <Skeleton className="h-40" />;
}
