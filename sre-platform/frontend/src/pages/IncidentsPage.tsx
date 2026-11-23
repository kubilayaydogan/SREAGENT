import { Plus } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { IncidentStatusBadge, INC_STATUS_LABEL, SeverityBadge, SEVERITY_LABEL } from "@/components/badges";
import { AppSelect, SearchInput } from "@/components/filters";
import { Button, Card, Input, Label, Select, Textarea } from "@/components/ui/primitives";
import { Dialog } from "@/components/ui/overlay";
import { EmptyState, ErrorState, PageHeader, Pagination, TableSkeleton } from "@/components/ui/states";
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
            <Label>Önem</Label>
            <Select value={f.severity} onChange={(e) => setF({ ...f, severity: e.target.value as Severity })}>
              {(["critical", "high", "medium", "low", "unknown"] as Severity[]).map((s) => (
                <option key={s} value={s}>
                  {SEVERITY_LABEL[s]}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>Uygulama</Label>
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
  const [sp, setSp] = useSearchParams();
  const { can } = useAuth();
  const g = (k: string) => sp.get(k) ?? "";
  const set = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v);
    else n.delete(k);
    if (k !== "page") n.delete("page");
    setSp(n, { replace: true });
  };
  const tab = g("tab") || "active";
  const statuses = TABS.find((t) => t.v === tab)?.s ?? [];
  const [creating, setCreating] = useState(false);
  const { data, isLoading, error, refetch } = useIncidents({
    status: statuses,
    severity: g("severity"),
    application_id: g("app"),
    q: g("q"),
    page: Number(g("page") || 1),
    size: 25,
  });
  const counts = useDashboard("15m").data?.incident_counts ?? {};

  return (
    <div className="mx-auto max-w-[1500px] p-4 lg:p-6">
      <PageHeader
        title="Incident Yönetimi"
        description="Yaşam döngüsü: Açık → İnceleniyor → Hafifletildi → Çözüldü (her geçiş zaman çizelgesine yazılır)"
        actions={
          can("sre") && (
            <Button variant="primary" onClick={() => setCreating(true)}>
              <Plus /> Yeni incident
            </Button>
          )
        }
      />
      <div className="mb-3 flex flex-wrap gap-1 border-b border-line">
        {TABS.map((t) => {
          const n = t.s.length ? t.s.reduce((a, s) => a + (counts[s] ?? 0), 0) : undefined;
          return (
            <button
              key={t.v}
              onClick={() => set("tab", t.v === "active" ? "" : t.v)}
              className={cn("-mb-px border-b-2 px-3 py-2 text-sm", tab === t.v ? "border-accent text-ink" : "border-transparent text-muted hover:text-ink")}
            >
              {t.l}
              {n !== undefined && <span className="ml-1.5 rounded-full bg-surface-3 px-1.5 text-[11px] tabular-nums">{n}</span>}
            </button>
          );
        })}
      </div>
      <div className="mb-3 flex flex-wrap gap-2">
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
      </div>
      <Card>
        {isLoading ? (
          <TableSkeleton />
        ) : error ? (
          <ErrorState error={error} onRetry={() => refetch()} />
        ) : !data?.items.length ? (
          <EmptyState title="Incident yok" description={tab === "active" ? "Aktif incident bulunmuyor." : "Bu filtreyle eşleşen kayıt yok."} />
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-muted">
                  <tr className="border-b border-line">
                    <th className="px-4 py-2 font-medium">Incident</th>
                    <th className="px-4 py-2 font-medium">Önem</th>
                    <th className="px-4 py-2 font-medium">Durum</th>
                    <th className="px-4 py-2 font-medium">Uygulama</th>
                    <th className="px-4 py-2 font-medium">Kaynak</th>
                    <th className="px-4 py-2 font-medium">Tespit</th>
                    <th className="px-4 py-2 font-medium">Güncelleme</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((i) => (
                    <tr key={i.id} className="border-b border-line last:border-0 hover:bg-surface-2">
                      <td className="max-w-md px-4 py-2">
                        <Link to={`/incidents/${i.id}`} className="block truncate text-ink hover:underline">
                          {i.title}
                        </Link>
                        {i.root_cause && <div className="truncate text-[11px] text-muted">kök neden: {i.root_cause}</div>}
                      </td>
                      <td className="px-4 py-2">
                        <SeverityBadge severity={i.severity} />
                      </td>
                      <td className="px-4 py-2">
                        <IncidentStatusBadge status={i.status} />
                      </td>
                      <td className="px-4 py-2 text-xs text-ink-2">{i.application_id ?? "—"}</td>
                      <td className="px-4 py-2 text-xs text-ink-2">{i.source}</td>
                      <td className="px-4 py-2 text-xs text-ink-2">{fmtTime(i.detected_at)}</td>
                      <td className="px-4 py-2 text-xs text-muted">{fmtRelative(i.updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination page={data.page} pages={data.pages} total={data.total} onPage={(p) => set("page", String(p))} />
          </>
        )}
      </Card>
      <CreateIncident open={creating} onOpenChange={setCreating} />
    </div>
  );
}
