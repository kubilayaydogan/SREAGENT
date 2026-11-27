import { Download, Printer } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router";
import { ConfidenceBadge } from "@/components/badges";
import { Markdown } from "@/components/Markdown";
import { Button, Skeleton } from "@/components/ui/primitives";
import { ErrorState, Page, PageHeader } from "@/components/ui/states";
import { errorToast, useRca } from "@/hooks/queries";
import { FeedbackBar } from "@/features/chat/FeedbackBar";
import { downloadText } from "@/services/api";
import { mechanismLabel } from "@/lib/steps";
import { fmtTime, shortId } from "@/lib/utils";

const SECTIONS = [
  ["summary", "Executive Summary"],
  ["problem", "Problem tanımı"],
  ["timeline", "Incident Timeline"],
  ["impact", "Etki analizi"],
  ["evidence", "Supporting Evidence"],
  ["root-cause", "Root Cause"],
  ["factors", "Katkıda bulunan faktörler"],
  ["actions", "Recommended Actions"],
  ["confidence", "Confidence ve belirsizlik"],
] as const;

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section id={`rca-${id}`} className="scroll-mt-4 border-t border-line px-5 py-4 first:border-t-0 print:break-inside-avoid print:px-0">
      <h2 className="mb-2 text-[13px] font-semibold text-ink">{title}</h2>
      <div className="text-[13px] text-ink-2">{children}</div>
    </section>
  );
}

const List = ({ items, ordered }: { items: string[]; ordered?: boolean }) =>
  items.length ? (
    ordered ? (
      <ol className="space-y-1 text-ink">
        {items.map((x, i) => (
          <li key={x} className="flex gap-2">
            <span className="w-4 shrink-0 text-right text-xs text-muted tabular-nums">{i + 1}.</span>
            {x}
          </li>
        ))}
      </ol>
    ) : (
      <ul className="list-disc space-y-1 pl-5">
        {items.map((x) => (
          <li key={x}>{x}</li>
        ))}
      </ul>
    )
  ) : (
    <p className="text-muted">—</p>
  );

export default function RcaDetailPage() {
  const { id } = useParams();
  const { data: r, isLoading, error, refetch } = useRca(id);
  if (error) return <ErrorState className="mt-10" error={error} onRetry={() => refetch()} />;
  if (isLoading || !r)
    return (
      <Page>
        <Skeleton className="mb-4 h-8 w-1/2" />
        <Skeleton className="h-64" />
      </Page>
    );
  return (
    <Page className="print:max-w-none print:p-0">
      <div className="print:hidden">
        <PageHeader
          crumbs={[{ label: "RCA raporları", to: "/rca" }, { label: shortId(r.id) }]}
          title={r.title}
          meta={
            <>
              <ConfidenceBadge confidence={r.confidence} />
              <span className="tabular-nums">{fmtTime(r.generated_at)}</span>
              <span>üreten: {r.generated_by}</span>
              <Link to={`/investigations/${r.investigation_id}`} className="hover:text-ink">
                investigation →
              </Link>
              {r.incident_id && (
                <Link to={`/incidents/${r.incident_id}`} className="hover:text-ink">
                  incident →
                </Link>
              )}
            </>
          }
          actions={
            <>
              <FeedbackBar investigationId={r.investigation_id} rcaId={r.id} />
              <Button size="sm" variant="outline" onClick={() => downloadText(`/rca/${r.id}/markdown`, `rca-${r.id}.md`).catch((e) => errorToast(e, "Dışa aktarılamadı"))}>
                <Download /> Markdown
              </Button>
              <Button size="sm" variant="ghost" onClick={() => window.print()}>
                <Printer /> Yazdır
              </Button>
            </>
          }
        />
      </div>
      <h1 className="mb-3 hidden text-lg font-semibold print:block">{r.title}</h1>
      <div className="grid items-start gap-3 lg:grid-cols-[minmax(0,1fr)_13rem]">
        <article className="rounded-lg border border-line bg-surface print:border-0">
          <Section id="summary" title="Executive Summary">
            <Markdown>{r.summary}</Markdown>
          </Section>
          <Section id="problem" title="Problem tanımı">
            <p>{r.problem_description ?? "—"}</p>
          </Section>
          <Section id="timeline" title="Incident Timeline">
            {r.timeline.length ? (
              <ol className="space-y-1">
                {r.timeline.map((t, i) => (
                  <li key={i} className="grid grid-cols-[9rem_1fr] gap-3">
                    <span className="text-xs text-muted tabular-nums">{fmtTime(t.at)}</span>
                    <span className="text-ink">{t.event}</span>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-muted">—</p>
            )}
          </Section>
          <Section id="impact" title="Etki analizi">
            <Markdown>{r.impact_analysis ?? "—"}</Markdown>
          </Section>
          <Section id="evidence" title="Supporting Evidence">
            {r.supporting_evidence.length ? (
              <div className="-mx-5 overflow-x-auto print:mx-0">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Anahtar</th>
                      <th>Tür</th>
                      <th>Problem</th>
                      <th>Özet</th>
                      <th>Referans</th>
                    </tr>
                  </thead>
                  <tbody>
                    {r.supporting_evidence.map((e, i) => (
                      <tr key={`${e.key}-${i}`}>
                        <td className="font-mono text-xs text-ink">{e.key}</td>
                        <td className="text-xs">{e.type ?? "—"}</td>
                        <td className="text-xs">{e.mechanism ? mechanismLabel(e.mechanism) : "—"}</td>
                        <td className="text-xs">{e.summary ?? "—"}</td>
                        <td className="font-mono text-2xs text-muted">
                          {e.trace_id && <div>trace:{e.trace_id}</div>}
                          {e.dynatrace_query_id && <div>q:{e.dynatrace_query_id}</div>}
                          {!e.trace_id && !e.dynatrace_query_id && "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-muted">Problemi destekleyen evidence yok.</p>
            )}
          </Section>
          <Section id="root-cause" title="Root Cause">
            <p className="text-ink">{r.suspected_root_cause ?? "Bilinen problem türleriyle açıklanamadı."}</p>
            <p className="mt-1 text-2xs text-muted">Korelasyon temelli hipotezdir; değişiklik öncesi doğrulanmalıdır.</p>
          </Section>
          <Section id="factors" title="Katkıda bulunan faktörler">
            <List items={r.contributing_factors} />
          </Section>
          <Section id="actions" title="Recommended Actions">
            <List items={r.recommended_actions} ordered />
            <p className="mt-2 text-2xs text-muted">Ajan hiçbir aksiyonu kendisi uygulamaz; kritik aksiyonlar Onaylar ekranından onaya sunulur.</p>
          </Section>
          <Section id="confidence" title="Confidence ve belirsizlik">
            <div className="mb-2">
              <ConfidenceBadge confidence={r.confidence} />
            </div>
            <p className="whitespace-pre-wrap">{r.uncertainty ?? "—"}</p>
            {r.alternatives.length > 0 && (
              <div className="mt-2">
                <div className="mb-1 text-xs font-medium text-ink">Alternative Hypotheses</div>
                <List items={r.alternatives} />
              </div>
            )}
          </Section>
        </article>
        <nav aria-label="bölümler" className="hidden lg:sticky lg:top-3 lg:block print:hidden">
          <div className="mb-1 px-1.5 text-xs font-medium text-muted">Bölümler</div>
          <ul className="space-y-px text-xs">
            {SECTIONS.map(([sid, label]) => (
              <li key={sid}>
                <a href={`#rca-${sid}`} className="block rounded-sm px-1.5 py-0.5 text-ink-2 hover:bg-surface-2 hover:text-ink">
                  {label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </Page>
  );
}
