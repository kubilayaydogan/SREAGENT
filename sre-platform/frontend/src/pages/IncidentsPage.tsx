import { Plus } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { AppTag, IncidentStatusBadge, INC_STATUS_LABEL, SeverityBadge, SEVERITY_LABEL } from "@/components/badges";
import { FilterBar, SortTh, TableWrap, useUrlFilters } from "@/components/table";
import { AppSelect, SearchInput } from "@/components/filters";
import { Button, Card, Input, Label, Select, Textarea } from "@/components/ui/primitives";
import { Dialog } from "@/components/ui/overlay";
import { EmptyState, ErrorState, Page, PageHeader, Pagination, TableSkeleton } from "@/components/ui/states";
import { useDashboard, useIncidentMutations, useIncidents } from "@/hooks/queries";
import { useAuth } from "@/services/auth";
import { cn, fmtRelative, fmtTime } from "@/lib/utils";
import type { IncidentStatus, Severity } from "@/types/api";

const TABS: { v: string; l: string; s: IncidentStatus[] }[] = [
  { v: "active", l: "Aktif", s: ["OPEN", "INVESTIGATING", "MITIGATED"] },
  { v: "OPEN", l: INC_STATUS_LABEL.OPEN, s: ["OPEN"] },
  { v: "INVESTIGATING", l: INC_STATUS_LABEL.INVESTIGATING, s: ["INVESTIGATING"] },
  { v: "MITIGATED", l: INC_STATUS_LABEL.MITIGATED, s: ["MITIGATED"] },
  { v: "RESOLVED", l: INC_STATUS_LABEL.RESOLVED, s: ["RESOLVED"] },
  { v: "all", l: "Tümü", s: [] },
];

function CreateIncident({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const nav = useNavigate();
  const { create } = useIncidentMutations();
  const [f, setF] = useState({ title: "", description: "", severity: "medium" as Severity, application_id: "" });
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Yeni incident"
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Vazgeç
          </Button>
          <Button
            variant="primary"
            disabled={f.title.trim().length < 3}
            loading={create.isPending}
            onClick={() =>
              create.mutate(
                { title: f.title.trim(), description: f.description.trim() || null, severity: f.severity, application_id: f.application_id || null },
                { onSuccess: (d) => (onOpenChange(false), nav(`/incidents/${d.id}`)) },
              )
            }
          >
            Oluştur
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div>
          <Label htmlFor="it">Başlık</Label>
          <Input id="it" value={f.title} maxLength={300} onChange={(e) => setF({ ...f, title: e.target.value })} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label htmlFor="isev">Önem</Label>
            <Select id="isev" value={f.severity} onChange={(e) => setF({ ...f, severity: e.target.value as Severity })}>
              {(["critical", "high", "medium", "low", "unknown"] as Severity[]).map((s) => (
                <option key={s} value={s}>
                  {SEVERITY_LABEL[s]}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>Servis</Label>
            <AppSelect value={f.application_id} onChange={(v) => setF({ ...f, application_id: v })} allLabel="—" className="w-full" />
          </div>
        </div>
        <div>
          <Label htmlFor="id">Açıklama</Label>
          <Textarea id="id" rows={4} maxLength={10000} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
        </div>
      </div>
    </Dialog>
  );
}

export default function IncidentsPage() {
  const { can } = useAuth();
  const { get: g, set, clear } = useUrlFilters();
  const tab = g("tab") || "active";
  const sort = g("sort") || "-detected_at";
  const statuses = TABS.find((t) => t.v === tab)?.s ?? [];
  const [creating, setCreating] = useState(false);
  const { data, isLoading, error, refetch, isFetching } = useIncidents({
    status: statuses,
    severity: g("severity"),
    application_id: g("app"),
    q: g("q"),
    page: Number(g("page") || 1),
    size: 50,
    sort,
  });
  const counts = useDashboard("15m").data?.incident_counts ?? {};
  const active = ["q", "app", "severity"].filter((k) => g(k)).length;

  return (
    <Page>
      <PageHeader
        title="Incident'lar"
        description="Açık → İnceleniyor → Hafifletildi → Çözüldü · her geçiş timeline'a yazılır"
        actions={
          can("sre") && (
            <Button variant="primary" size="sm" onClick={() => setCreating(true)}>
              <Plus /> Yeni incident
            </Button>
          )
        }
      />
      <div role="tablist" aria-label="durum" className="mb-3 flex flex-wrap gap-0.5 border-b border-line">
        {TABS.map((t) => {
          const n = t.s.length ? t.s.reduce((a, s) => a + (counts[s] ?? 0), 0) : undefined;
          return (
            <button
              key={t.v}
              role="tab"
              aria-selected={tab === t.v}
              onClick={() => set("tab", t.v === "active" ? "" : t.v)}
              className={cn(
                "-mb-px border-b-2 px-2.5 py-1.5 text-[13px] transition-colors duration-100",
                tab === t.v ? "border-ink font-medium text-ink" : "border-transparent text-muted hover:text-ink",
              )}
            >
              {t.l}
              {n !== undefined && <span className="ml-1.5 text-xs text-muted tabular-nums">{n}</span>}
            </button>
          );
        })}
      </div>
      <Card>
        <FilterBar active={active} onClear={() => clear(["q", "app", "severity"])} right={data && <span className="text-xs text-muted tabular-nums">{data.total} kayıt</span>}>
          <SearchInput value={g("q")} onChange={(v) => set("q", v)} placeholder="Başlık, açıklama, Root Cause…" />
          <AppSelect value={g("app")} onChange={(v) => set("app", v)} />
          <Select aria-label="önem" value={g("severity")} onChange={(e) => set("severity", e.target.value)} className="w-36">
            <option value="">Tüm önemler</option>
            {(Object.keys(SEVERITY_LABEL) as Severity[]).map((s) => (
              <option key={s} value={s}>
                {SEVERITY_LABEL[s]}
              </option>
            ))}
          </Select>
        </FilterBar>
        {isLoading ? (
          <TableSkeleton />
        ) : error ? (
          <ErrorState error={error} onRetry={() => refetch()} />
        ) : !data?.items.length ? (
          <EmptyState title="Incident yok" description={tab === "active" ? "Aktif incident bulunmuyor." : "Bu filtreyle eşleşen kayıt yok."} />
        ) : (
          <>
            <TableWrap fetching={isFetching}>
              <table className="data-table">
                <thead>
                  <tr>
                    <SortTh field="title" sort={sort} onSort={(v) => set("sort", v)}>
                      Incident
                    </SortTh>
                    <SortTh field="severity" sort={sort} onSort={(v) => set("sort", v)}>
                      Önem
                    </SortTh>
                    <SortTh field="status" sort={sort} onSort={(v) => set("sort", v)}>
                      Durum
                    </SortTh>
                    <th>Servis</th>
                    <th>Kaynak</th>
                    <SortTh field="detected_at" sort={sort} onSort={(v) => set("sort", v)} num>
                      Tespit
                    </SortTh>
                    <SortTh field="updated_at" sort={sort} onSort={(v) => set("sort", v)} num>
                      Güncelleme
                    </SortTh>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((i) => (
                    <tr key={i.id} className={cn(i.severity === "critical" && i.status !== "RESOLVED" && "[&>td:first-child]:shadow-[inset_2px_0_0_var(--color-st-critical)]")}>
                      <td className="primary max-w-md">
                        <Link to={`/incidents/${i.id}`} className="block truncate hover:underline">
                          {i.title}
                        </Link>
                        {i.root_cause && <div className="truncate text-2xs text-muted">Root Cause: {i.root_cause}</div>}
                      </td>
                      <td>
                        <SeverityBadge severity={i.severity} />
                      </td>
                      <td>
                        <IncidentStatusBadge status={i.status} />
                      </td>
                      <td>{i.application_id ? <AppTag id={i.application_id} /> : <span className="text-xs text-muted">—</span>}</td>
                      <td className="text-xs">{i.source}</td>
                      <td className="num text-xs">{fmtTime(i.detected_at)}</td>
                      <td className="num text-xs text-muted">{fmtRelative(i.updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
            <Pagination page={data.page} pages={data.pages} total={data.total} onPage={(p) => set("page", String(p))} />
          </>
        )}
      </Card>
      <CreateIncident open={creating} onOpenChange={setCreating} />
    </Page>
  );
}
