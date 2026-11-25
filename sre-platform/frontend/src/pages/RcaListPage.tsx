import { Link } from "react-router";
import { AppTag, ConfidenceBadge } from "@/components/badges";
import { AppSelect, SearchInput } from "@/components/filters";
import { FilterBar, TableWrap, useUrlFilters } from "@/components/table";
import { Card } from "@/components/ui/primitives";
import { EmptyState, ErrorState, Page, PageHeader, Pagination, TableSkeleton } from "@/components/ui/states";
import { useRcas } from "@/hooks/queries";
import { fmtTime } from "@/lib/utils";

export default function RcaListPage() {
  const { get: g, set, clear } = useUrlFilters();
  const { data, isLoading, error, refetch, isFetching } = useRcas({ q: g("q"), application_id: g("app"), page: Number(g("page") || 1), size: 50 });
  const active = ["q", "app"].filter((k) => g(k)).length;
  return (
    <Page>
      <PageHeader title="RCA raporları" description="Kaydedilmiş Root Cause Analysis raporları · Markdown olarak dışa aktarılabilir" />
      <Card>
        <FilterBar active={active} onClear={() => clear(["q", "app"])} right={data && <span className="text-xs text-muted tabular-nums">{data.total} rapor</span>}>
          <SearchInput value={g("q")} onChange={(v) => set("q", v)} placeholder="Başlık, özet, Root Cause…" />
          <AppSelect value={g("app")} onChange={(v) => set("app", v)} />
        </FilterBar>
        {isLoading ? (
          <TableSkeleton />
        ) : error ? (
          <ErrorState error={error} onRetry={() => refetch()} />
        ) : !data?.items.length ? (
          <EmptyState title="RCA raporu yok" description="Tamamlanmış bir investigation'ın detayından veya incident ekranından 'RCA üret' ile oluşturulur." />
        ) : (
          <>
            <TableWrap fetching={isFetching}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Rapor</th>
                    <th>Servis</th>
                    <th>Güven</th>
                    <th>Üreten</th>
                    <th className="num">Zaman</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((r) => (
                    <tr key={r.id}>
                      <td className="primary max-w-xl">
                        <Link to={`/rca/${r.id}`} className="block truncate font-medium hover:underline">
                          {r.title}
                        </Link>
                        <p className="truncate text-xs text-muted">{r.summary}</p>
                      </td>
                      <td>{r.application_id ? <AppTag id={r.application_id} /> : <span className="text-xs text-muted">—</span>}</td>
                      <td>
                        <ConfidenceBadge confidence={r.confidence} />
                      </td>
                      <td className="text-xs">{r.generated_by}</td>
                      <td className="num text-xs text-muted">{fmtTime(r.generated_at)}</td>
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
