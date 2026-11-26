import { ConceptHelp } from "@/components/ConceptHelp";
import { ExternalLink } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { DqlBlock } from "@/components/RecordTables";
import { FilterBar, TableWrap, useUrlFilters } from "@/components/table";
import { Dialog } from "@/components/ui/overlay";
import { Card, Input, Label, Mono, Select, Skeleton } from "@/components/ui/primitives";
import { EmptyState, ErrorState, Page, PageHeader, Pagination, TableSkeleton } from "@/components/ui/states";
import { useMcpCall, useMcpCalls, useMcpFacets } from "@/hooks/queries";
import { SIGNAL_LABEL, STEPS } from "@/lib/steps";
import { cn, fmtDuration, fmtTime } from "@/lib/utils";
import type { McpCallDetail, McpOutcome } from "@/types/api";

/** Sonuç sınıfları: başarılı ve boş sonuç başarılıdır; HTTP 200 içindeki tool hatası da başarısızdır. */
export const OUTCOME_TR: Record<McpOutcome, string> = {
  success: "başarılı",
  empty: "boş sonuç",
  timeout: "timeout",
  permission_denied: "yetki hatası",
  tool_error: "MCP tool hatası",
  unavailable: "erişilemedi",
  cancelled: "iptal edildi",
};
const OUTCOME_DOT: Record<McpOutcome, string> = {
  success: "bg-st-good",
  empty: "bg-st-unknown",
  timeout: "bg-st-critical",
  permission_denied: "bg-st-critical",
  tool_error: "bg-st-critical",
  unavailable: "bg-st-critical",
  cancelled: "bg-st-warning",
};
const WINDOW_TR = { incident: "incident penceresi", baseline: "baseline penceresi", custom: "özel aralık" } as const;
const FILTER_KEYS = ["from", "to", "connector", "tool", "status", "run_id"];

const stepLabel = (id: string | null) => STEPS.find((s) => s.id === id)?.label ?? id ?? "—";
const dayIso = (d: string, end = false) => (d ? new Date(`${d}T${end ? "23:59:59" : "00:00:00"}`).toISOString() : undefined);

export function OutcomeLabel({ outcome }: { outcome: McpOutcome | null }) {
  if (!outcome) return <span className="text-xs text-muted">—</span>;
  const failed = !["success", "empty"].includes(outcome);
  return (
    <span className="inline-flex items-center gap-1.5 text-xs whitespace-nowrap">
      <span aria-hidden className={cn("size-1.5 rounded-full", OUTCOME_DOT[outcome])} />
      <span className={failed ? "text-st-critical-ink" : "text-ink-2"}>{OUTCOME_TR[outcome] ?? outcome}</span>
    </span>
  );
}

export default function McpCallsPage() {
  const { get: g, set, clear } = useUrlFilters();
  const page = Number(g("page") || 1);
  const params = {
    since: dayIso(g("from")),
    until: dayIso(g("to"), true),
    connector: g("connector") || undefined,
    tool: g("tool") || undefined,
    status: g("status") || undefined,
    run_id: g("run_id") || undefined,
    page,
    size: 50,
  };
  const { data, isLoading, error, refetch, isFetching } = useMcpCalls(params);
  const facets = useMcpFacets();
  const active = FILTER_KEYS.filter((k) => g(k)).length;
  const selected = g("call");

  return (
    <Page>
      <PageHeader
        title="MCP Çağrıları"
        description="Agent'ın veri okumak için gönderdiği her istek, aldığı yanıtın özeti ve ait olduğu çalıştırma"
        meta={<ConceptHelp k="mcp" />}
      />
      <Card>
        <FilterBar
          active={active}
          onClear={() => clear(FILTER_KEYS)}
          right={data && <span className="text-xs text-muted tabular-nums">{data.total} çağrı</span>}
          more={
            <>
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
          <Input aria-label="run_id" placeholder="run_id" value={g("run_id")} onChange={(e) => set("run_id", e.target.value.trim())} className="w-56 font-mono" />
          <Select aria-label="connector" value={g("connector")} onChange={(e) => set("connector", e.target.value)} className="w-36">
            <option value="">Tüm connector'lar</option>
            {facets.data?.connectors.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </Select>
          <Select aria-label="tool" value={g("tool")} onChange={(e) => set("tool", e.target.value)} className="w-36">
            <option value="">Tüm tool'lar</option>
            {facets.data?.tools.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </Select>
          <Select aria-label="durum" value={g("status")} onChange={(e) => set("status", e.target.value)} className="w-40">
            <option value="">Tüm durumlar</option>
            {(Object.keys(OUTCOME_TR) as McpOutcome[]).map((o) => (
              <option key={o} value={o}>
                {OUTCOME_TR[o]}
              </option>
            ))}
          </Select>
        </FilterBar>
        {isLoading ? (
          <TableSkeleton cols={7} />
        ) : error ? (
          <ErrorState error={error} onRetry={() => refetch()} />
        ) : !data?.items.length ? (
          <EmptyState title="MCP çağrısı bulunamadı" description={active ? "Filtreleri değiştirmeyi ya da temizlemeyi deneyin." : "Henüz kayıtlı MCP çağrısı yok."} />
        ) : (
          <>
            <TableWrap fetching={isFetching}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th className="num">Başlangıç</th>
                    <th>Connector</th>
                    <th>Tool</th>
                    <th>Agent adımı</th>
                    <th>Durum</th>
                    <th className="num">Süre</th>
                    <th className="num">Kayıt</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((c) => (
                    <tr
                      key={c.id}
                      tabIndex={0}
                      aria-label={`çağrı ${c.id}`}
                      className={cn("cursor-pointer", selected === c.id && "!bg-surface-2")}
                      onClick={() => set("call", c.id)}
                      onKeyDown={(e) => e.key === "Enter" && set("call", c.id)}
                    >
                      <td className="num text-xs text-muted">{fmtTime(c.started_at)}</td>
                      <td className="font-mono text-xs">{c.connector ?? "—"}</td>
                      <td>
                        <div className="font-mono text-xs text-ink">{c.tool}</div>
                        {c.query_key && <div className="text-2xs text-muted">{SIGNAL_LABEL[c.query_key] ?? c.query_key}</div>}
                      </td>
                      <td className="text-xs">{stepLabel(c.step)}</td>
                      <td>
                        <OutcomeLabel outcome={c.outcome} />
                        {c.attempts > 1 && <span className="ml-1.5 text-2xs text-muted">{c.attempts} deneme</span>}
                      </td>
                      <td className="num text-xs">{fmtDuration(c.duration_ms)}</td>
                      <td className="num text-xs">{c.record_count ?? ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
            <Pagination page={data.page} pages={data.pages} total={data.total} onPage={(p) => set("page", String(p))} />
          </>
        )}
      </Card>
      <CallDialog id={selected || null} onClose={() => set("call", "")} />
    </Page>
  );
}

function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[10rem_1fr] gap-2 py-1 text-xs">
      <dt className="text-muted">{k}</dt>
      <dd className="min-w-0 break-words text-ink-2">{children ?? "—"}</dd>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-t border-line pt-2 first:border-t-0 first:pt-0">
      <h3 className="mb-1 text-xs font-semibold text-ink">{title}</h3>
      <dl>{children}</dl>
    </section>
  );
}

function CallDialog({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { data, isLoading, error } = useMcpCall(id);
  return (
    <Dialog open={!!id} onOpenChange={(o) => !o && onClose()} title="MCP çağrısı" description={id ?? undefined} wide>
      {isLoading ? (
        <Skeleton className="h-40" />
      ) : error ? (
        <ErrorState error={error} />
      ) : data ? (
        <CallDetail d={data} />
      ) : null}
    </Dialog>
  );
}

export function CallDetail({ d }: { d: McpCallDetail }) {
  const r = d.request;
  const res = d.response;
  return (
    <div className="space-y-3">
      <Section title="İstek">
        <Row k="MCP metodu">
          <Mono>{r.method}</Mono>
        </Row>
        <Row k="Connector / tool">
          <Mono>
            {r.connector ?? "—"} / {r.tool}
          </Mono>
        </Row>
        <Row k="Query anahtarı">{r.query_key && <Mono>{r.query_key}</Mono>}</Row>
        <Row k="Query başlangıcı">{r.query_start && fmtTime(r.query_start)}</Row>
        <Row k="Query bitişi">{r.query_end && fmtTime(r.query_end)}</Row>
        <Row k="Limit">{r.limit ?? null}</Row>
        <Row k="Diğer parametreler">
          {Object.keys(r.params ?? {}).length ? <Mono>{JSON.stringify(r.params)}</Mono> : null}
          {r.omitted_params?.length ? <div className="text-2xs text-muted">kaydedilmedi: {r.omitted_params.join(", ")}</div> : null}
        </Row>
        {r.legacy && <div className="text-2xs text-muted">Önizleme eski kayıttan sunucu tarafında üretildi.</div>}
      </Section>
      {d.query && (
        <Section title="Dynatrace query">
          <Row k="Query penceresi">{d.query.window ? WINDOW_TR[d.query.window] : null}</Row>
          <Row k="Parmak izi">
            <Mono>
              sha256 {d.query.dql_sha256} · {d.query.dql_chars} karakter
            </Mono>
          </Row>
          <div className="pt-1">
            <DqlBlock dql={d.query.dql_template} />
          </div>
        </Section>
      )}
      <Section title="Yanıt özeti">
        <Row k="Durum">
          <OutcomeLabel outcome={res.outcome} />
          {res.result_status && <span className="ml-1.5 font-mono text-2xs text-muted">{res.result_status}</span>}
        </Row>
        <Row k="Süre">{fmtDuration(res.duration_ms)}</Row>
        <Row k="HTTP durum kodu">{res.http_status ?? null}</Row>
        <Row k="Kayıt sayısı">{res.record_count ?? ""}</Row>
        <Row k="Hata özeti">{res.error_summary && <span className="font-mono text-st-critical-ink">{res.error_summary}</span>}</Row>
        <Row k="Evidence">
          {res.evidence_key ? (
            <Link className="text-accent-text hover:underline" to={`/investigations/${d.investigation_id}?tab=evidence&ev=${encodeURIComponent(res.evidence_key)}`}>
              {res.evidence_key}
            </Link>
          ) : null}
          {res.dynatrace_query_id && <span className="ml-2 font-mono text-2xs text-muted">q:{res.dynatrace_query_id}</span>}
        </Row>
      </Section>
      <Section title={`Denemeler (${d.attempts_log.length || 1})`}>
        {d.attempts_log.length ? (
          <table className="data-table">
            <thead>
              <tr>
                <th className="num">#</th>
                <th>Sonuç</th>
                <th className="num">Süre</th>
                <th className="num">HTTP</th>
              </tr>
            </thead>
            <tbody>
              {d.attempts_log.map((a) => (
                <tr key={a.attempt}>
                  <td className="num">{a.attempt}</td>
                  <td>
                    <OutcomeLabel outcome={a.outcome as McpOutcome} />
                    {a.error_type && <span className="ml-1.5 font-mono text-2xs text-muted">{a.error_type}</span>}
                  </td>
                  <td className="num text-xs">{fmtDuration(a.elapsed_ms)}</td>
                  <td className="num text-xs">{a.http_status ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="text-xs text-muted">Deneme ayrıntısı yok (eski kayıt).</div>
        )}
      </Section>
      <Section title="İlişkili çalışma">
        <Row k="mcp_call_id">
          <Mono>{d.related.mcp_call_id}</Mono>
        </Row>
        <Row k="run_id">
          <Mono>{d.related.run_id}</Mono>
        </Row>
        <Row k="Agent adımı">{stepLabel(d.related.step)}</Row>
        <Row k="Run detayı">
          <Link className="text-accent-text hover:underline" to={`/investigations/${d.related.investigation_id}?tab=tools`}>
            Investigation'ı aç →
          </Link>
          {d.related.langsmith_span_url && (
            <a href={d.related.langsmith_span_url} target="_blank" rel="noreferrer" className="ml-3 inline-flex items-center gap-1 text-accent-text hover:underline">
              <ExternalLink className="size-3" /> LangSmith span
            </a>
          )}
        </Row>
      </Section>
    </div>
  );
}
