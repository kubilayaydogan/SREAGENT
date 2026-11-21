import { Link, useSearchParams } from "react-router";
import { ConfidenceBadge } from "@/components/badges";
import { AppSelect, SearchInput } from "@/components/filters";
import { Card } from "@/components/ui/primitives";
import { EmptyState, ErrorState, PageHeader, Pagination, TableSkeleton } from "@/components/ui/states";
import { useRcas } from "@/hooks/queries";
import { fmtTime } from "@/lib/utils";

export default function RcaListPage() {
  const [sp, setSp] = useSearchParams();
  const g = (k: string) => sp.get(k) ?? "";
  const set = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v);
    else n.delete(k);
    if (k !== "page") n.delete("page");
    setSp(n, { replace: true });
  };
  const { data, isLoading, error, refetch } = useRcas({ q: g("q"), application_id: g("app"), page: Number(g("page") || 1), size: 25 });
  return (
    <div className="mx-auto max-w-[1500px] p-4 lg:p-6">
      <PageHeader title="RCA Raporları" description="Kaydedilmiş kök neden analizleri; tekrar açılabilir ve Markdown olarak dışa aktarılabilir" />
      <div className="mb-3 flex flex-wrap gap-2">
        <SearchInput value={g("q")} onChange={(v) => set("q", v)} placeholder="Başlık, özet, kök neden…" />
        <AppSelect value={g("app")} onChange={(v) => set("app", v)} />
      </div>
      <Card>
        {isLoading ? (
          <TableSkeleton />
        ) : error ? (
          <ErrorState error={error} onRetry={() => refetch()} />
        ) : !data?.items.length ? (
          <EmptyState title="RCA raporu yok" description="Tamamlanmış bir araştırmanın detayından veya incident ekranından 'RCA üret' ile oluşturulur." />
        ) : (
          <>
            <ul className="divide-y divide-line">
              {data.items.map((r) => (
                <li key={r.id}>
                  <Link to={`/rca/${r.id}`} className="block px-4 py-3 hover:bg-surface-2">
                    <div className="flex flex-wrap items-center gap-3">
                      <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink">{r.title}</span>
                      <ConfidenceBadge confidence={r.confidence} />
                      <span className="text-xs text-muted">{fmtTime(r.generated_at)}</span>
                    </div>
                    <p className="mt-1 line-clamp-2 text-xs text-ink-2">{r.summary}</p>
                    <div className="mt-1 text-[11px] text-muted">
                      {r.application_id ?? "—"} · üretim: {r.generated_by}
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
            <Pagination page={data.page} pages={data.pages} total={data.total} onPage={(p) => set("page", String(p))} />
          </>
        )}
      </Card>
    </div>
  );
}
