import { Link, useSearchParams } from "react-router";
import { AppTag, InvestigationStatusBadge, SeverityBadge, INV_STATUS_LABEL, SEVERITY_LABEL } from "@/components/badges";
import { AppSelect, SearchInput } from "@/components/filters";
import { Card, Input, Select } from "@/components/ui/primitives";
import { EmptyState, ErrorState, PageHeader, Pagination, TableSkeleton } from "@/components/ui/states";
import { useInvestigations } from "@/hooks/queries";
import { MECHANISM_LABEL, mechanismLabel } from "@/lib/steps";
import { fmtDuration, fmtNum, fmtTime } from "@/lib/utils";
import type { InvestigationStatus, Severity } from "@/types/api";

const TRIGGER_TR: Record<string, string> = { chat: "sohbet", manual: "manuel", scheduled: "zamanlanmış", watch: "watch", import: "içe aktarım" };

/** Tarih alanı (yerel gün) → UTC ISO */
const dayIso = (d: string, end = false) => (d ? new Date(`${d}T${end ? "23:59:59" : "00:00:00"}`).toISOString() : undefined);

export default function HistoryPage() {
  const [sp, setSp] = useSearchParams();
  const g = (k: string) => sp.get(k) ?? "";
  const set = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v);
    else n.delete(k);
    if (k !== "page") n.delete("page");
    setSp(n, { replace: true });
  };
  const page = Number(g("page") || 1);
  const params = {
    application_id: g("app"),
    status: g("status"),
    severity: g("severity"),
    mechanism: g("mechanism"),
    trigger: g("trigger"),
    q: g("q"),
    since: dayIso(g("from")),
    until: dayIso(g("to"), true),
    page,
    size: 25,
    sort: g("sort") || "-created_at",
  };
  const { data, isLoading, error, refetch, isFetching } = useInvestigations(params);

  return (
    <div className="mx-auto max-w-[1500px] p-4 lg:p-6">
      <PageHeader title="Araştırma Geçmişi" description="PostgreSQL'de saklanan tüm araştırmalar (sohbet, manuel, zamanlanmış, watch)" />
      <Card className="mb-4 flex flex-wrap items-end gap-2 p-3">
        <SearchInput value={g("q")} onChange={(v) => set("q", v)} placeholder="Başlık, kök neden, açıklama…" />
        <AppSelect value={g("app")} onChange={(v) => set("app", v)} />
        <Select aria-label="durum" value={g("status")} onChange={(e) => set("status", e.target.value)} className="w-36">
          <option value="">Tüm durumlar</option>
          {(Object.keys(INV_STATUS_LABEL) as InvestigationStatus[]).map((s) => (
            <option key={s} value={s}>
              {INV_STATUS_LABEL[s]}
            </option>
          ))}
        </Select>
        <Select aria-label="önem" value={g("severity")} onChange={(e) => set("severity", e.target.value)} className="w-32">
          <option value="">Tüm önemler</option>
          {(Object.keys(SEVERITY_LABEL) as Severity[]).map((s) => (
            <option key={s} value={s}>
              {SEVERITY_LABEL[s]}
            </option>
          ))}
        </Select>
        <Select aria-label="mekanizma" value={g("mechanism")} onChange={(e) => set("mechanism", e.target.value)} className="w-48">
          <option value="">Tüm mekanizmalar</option>
          {Object.entries(MECHANISM_LABEL).map(([k, l]) => (
            <option key={k} value={k}>
              {l}
            </option>
          ))}
        </Select>
        <Select aria-label="tetikleyici" value={g("trigger")} onChange={(e) => set("trigger", e.target.value)} className="w-36">
          <option value="">Tüm kaynaklar</option>
          {Object.entries(TRIGGER_TR).map(([k, l]) => (
            <option key={k} value={k}>
              {l}
            </option>
          ))}
        </Select>
        <label className="text-xs text-muted">
          Başlangıç
          <Input type="date" value={g("from")} onChange={(e) => set("from", e.target.value)} className="mt-0.5 w-36" />
        </label>
        <label className="text-xs text-muted">
          Bitiş
          <Input type="date" value={g("to")} onChange={(e) => set("to", e.target.value)} className="mt-0.5 w-36" />
        </label>
        <Select aria-label="sıralama" value={params.sort} onChange={(e) => set("sort", e.target.value)} className="w-40">
          <option value="-created_at">En yeni</option>
          <option value="created_at">En eski</option>
          <option value="-risk_score">Risk (yüksek)</option>
          <option value="-duration_ms">Süre (uzun)</option>
        </Select>
      </Card>
      <Card>
        {isLoading ? (
          <TableSkeleton />
        ) : error ? (
          <ErrorState error={error} onRetry={() => refetch()} />
        ) : !data?.items.length ? (
          <EmptyState title="Kayıt bulunamadı" description="Filtreleri değiştirmeyi deneyin." />
        ) : (
          <div className={isFetching ? "opacity-70 transition-opacity" : ""}>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-muted">
                  <tr className="border-b border-line">
                    <th className="px-4 py-2 font-medium">Araştırma</th>
                    <th className="px-4 py-2 font-medium">Uygulamalar</th>
                    <th className="px-4 py-2 font-medium">Mekanizma</th>
                    <th className="px-4 py-2 font-medium">Önem</th>
                    <th className="px-4 py-2 text-right font-medium">Risk</th>
                    <th className="px-4 py-2 text-right font-medium">Kapsam</th>
                    <th className="px-4 py-2 font-medium">Durum</th>
                    <th className="px-4 py-2 text-right font-medium">Süre</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((i) => (
                    <tr key={i.id} className="border-b border-line last:border-0 hover:bg-surface-2">
                      <td className="max-w-sm px-4 py-2">
                        <Link to={`/investigations/${i.id}`} className="block truncate text-ink hover:underline">
                          {i.title}
                        </Link>
                        <div className="text-[11px] text-muted">
                          {fmtTime(i.created_at)} · {TRIGGER_TR[i.trigger] ?? i.trigger}
                        </div>
                      </td>
                      <td className="px-4 py-2">
                        <div className="flex flex-wrap gap-x-2">
                          {[...new Set([...i.source_apps, ...i.affected_apps])].map((a) => (
                            <AppTag key={a} id={a} />
                          ))}
                          {!i.source_apps.length && !i.affected_apps.length && <span className="text-xs text-muted">—</span>}
                        </div>
                      </td>
                      <td className="px-4 py-2 text-xs text-ink-2">{mechanismLabel(i.primary_mechanism)}</td>
                      <td className="px-4 py-2">
                        <SeverityBadge severity={i.severity} />
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums">{i.risk_score == null ? "—" : fmtNum(Number(i.risk_score), 1)}</td>
                      <td className="px-4 py-2 text-right tabular-nums">{i.data_coverage == null ? "—" : `%${fmtNum(Number(i.data_coverage) * 100)}`}</td>
                      <td className="px-4 py-2">
                        <InvestigationStatusBadge status={i.status} />
                      </td>
                      <td className="px-4 py-2 text-right text-xs text-ink-2 tabular-nums">{fmtDuration(i.duration_ms)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination page={data.page} pages={data.pages} total={data.total} onPage={(p) => set("page", String(p))} />
          </div>
        )}
      </Card>
    </div>
  );
}
