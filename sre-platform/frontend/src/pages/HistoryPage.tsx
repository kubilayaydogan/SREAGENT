import { Link } from "react-router";
import { AppTag, InvestigationStatusBadge, SeverityBadge, INV_STATUS_LABEL, SEVERITY_LABEL } from "@/components/badges";
import { AppSelect, SearchInput } from "@/components/filters";
import { RiskValue } from "@/components/risk";
import { FilterBar, SortTh, TableWrap, useUrlFilters } from "@/components/table";
import { Card, Input, Label, Select } from "@/components/ui/primitives";
import { EmptyState, ErrorState, Page, PageHeader, Pagination, TableSkeleton } from "@/components/ui/states";
import { useInvestigations } from "@/hooks/queries";
import { toNum } from "@/lib/risk";
import { MECHANISM_LABEL, mechanismLabel } from "@/lib/steps";
import { fmtDuration, fmtPct, fmtTime } from "@/lib/utils";
import type { InvestigationStatus, Severity } from "@/types/api";

const TRIGGER_TR: Record<string, string> = { chat: "AI Investigation", manual: "manuel", scheduled: "health check", watch: "watch", import: "içe aktarım" };
const FILTER_KEYS = ["q", "app", "status", "severity", "mechanism", "trigger", "from", "to"];

/** Tarih alanı (yerel gün) → UTC ISO */
const dayIso = (d: string, end = false) => (d ? new Date(`${d}T${end ? "23:59:59" : "00:00:00"}`).toISOString() : undefined);

export default function HistoryPage() {
  const { get: g, set, clear } = useUrlFilters();
  const page = Number(g("page") || 1);
  const sort = g("sort") || "-created_at";
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
    size: 50,
    sort,
  };
  const { data, isLoading, error, refetch, isFetching } = useInvestigations(params);
  const active = FILTER_KEYS.filter((k) => g(k)).length;

  return (
    <Page>
      <PageHeader title="Investigation geçmişi" description="AI Investigation, health check, watch ve manuel investigation'ların tümü" />
      <Card>
        <FilterBar
          active={active}
          onClear={() => clear(FILTER_KEYS)}
          right={data && <span className="text-xs text-muted tabular-nums">{data.total} kayıt</span>}
          more={
            <>
              <div>
                <Label htmlFor="f-mech">Mechanism</Label>
                <Select id="f-mech" value={g("mechanism")} onChange={(e) => set("mechanism", e.target.value)} className="w-56">
                  <option value="">Tümü</option>
                  {Object.entries(MECHANISM_LABEL).map(([k, l]) => (
                    <option key={k} value={k}>
                      {l}
                    </option>
                  ))}
                </Select>
              </div>
              <div>
                <Label htmlFor="f-trig">Kaynak</Label>
                <Select id="f-trig" value={g("trigger")} onChange={(e) => set("trigger", e.target.value)} className="w-40">
                  <option value="">Tümü</option>
                  {Object.entries(TRIGGER_TR).map(([k, l]) => (
                    <option key={k} value={k}>
                      {l}
                    </option>
                  ))}
                </Select>
              </div>
              <div>
                <Label htmlFor="f-from">Başlangıç</Label>
                <Input id="f-from" type="date" value={g("from")} onChange={(e) => set("from", e.target.value)} className="w-36" />
              </div>
              <div>
                <Label htmlFor="f-to">Bitiş</Label>
                <Input id="f-to" type="date" value={g("to")} onChange={(e) => set("to", e.target.value)} className="w-36" />
              </div>
            </>
          }
        >
          <SearchInput value={g("q")} onChange={(v) => set("q", v)} placeholder="Başlık, Root Cause, açıklama…" />
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
        </FilterBar>
        {isLoading ? (
          <TableSkeleton />
        ) : error ? (
          <ErrorState error={error} onRetry={() => refetch()} />
        ) : !data?.items.length ? (
          <EmptyState title="Kayıt bulunamadı" description={active ? "Filtreleri değiştirmeyi ya da temizlemeyi deneyin." : "Henüz investigation yok."} />
        ) : (
          <>
            <TableWrap fetching={isFetching}>
              <table className="data-table">
                <thead>
                  <tr>
                    <SortTh field="title" sort={sort} onSort={(s) => set("sort", s)}>
                      Investigation
                    </SortTh>
                    <th>Servisler</th>
                    <th>Mechanism</th>
                    <SortTh field="severity" sort={sort} onSort={(s) => set("sort", s)}>
                      Önem
                    </SortTh>
                    <SortTh field="risk_score" sort={sort} onSort={(s) => set("sort", s)} num>
                      Risk
                    </SortTh>
                    <th className="num">Coverage</th>
                    <SortTh field="status" sort={sort} onSort={(s) => set("sort", s)}>
                      Durum
                    </SortTh>
                    <SortTh field="duration_ms" sort={sort} onSort={(s) => set("sort", s)} num>
                      Süre
                    </SortTh>
                    <SortTh field="created_at" sort={sort} onSort={(s) => set("sort", s)} num>
                      Zaman
                    </SortTh>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((i) => (
                    <tr key={i.id}>
                      <td className="primary max-w-sm">
                        <Link to={`/investigations/${i.id}`} className="block truncate hover:underline">
                          {i.title}
                        </Link>
                        <div className="text-2xs text-muted">{TRIGGER_TR[i.trigger] ?? i.trigger}</div>
                      </td>
                      <td>
                        <div className="flex flex-wrap gap-x-2">
                          {[...new Set([...i.source_apps, ...i.affected_apps])].map((a) => (
                            <AppTag key={a} id={a} />
                          ))}
                          {!i.source_apps.length && !i.affected_apps.length && <span className="text-xs text-muted">—</span>}
                        </div>
                      </td>
                      <td className="text-xs">{mechanismLabel(i.primary_mechanism)}</td>
                      <td>
                        <SeverityBadge severity={i.severity} />
                      </td>
                      <td className="num">
                        <RiskValue score={toNum(i.risk_score)} />
                      </td>
                      <td className="num">{fmtPct(toNum(i.data_coverage), 0)}</td>
                      <td>
                        <InvestigationStatusBadge status={i.status} />
                      </td>
                      <td className="num text-xs">{fmtDuration(i.duration_ms)}</td>
                      <td className="num text-xs text-muted">{fmtTime(i.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
            <Pagination page={data.page} pages={data.pages} total={data.total} onPage={(p) => set("page", String(p))} />
          </>
        )}
      </Card>
    </Page>
  );
}
