import { Download, Printer } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router";
import { ConfidenceBadge } from "@/components/badges";
import { Markdown } from "@/components/Markdown";
import { Button, Card, CardBody, CardHeader, Skeleton } from "@/components/ui/primitives";
import { ErrorState } from "@/components/ui/states";
import { errorToast, useRca } from "@/hooks/queries";
import { FeedbackBar } from "@/features/chat/FeedbackBar";
import { downloadText } from "@/services/api";
import { mechanismLabel } from "@/lib/steps";
import { fmtTime } from "@/lib/utils";

function Section({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader title={`${n}. ${title}`} />
      <CardBody className="text-sm text-ink-2">{children}</CardBody>
    </Card>
  );
}

const List = ({ items, empty = "—" }: { items: string[]; empty?: string }) =>
  items.length ? (
    <ul className="list-disc space-y-1 pl-5">
      {items.map((x) => (
        <li key={x}>{x}</li>
      ))}
    </ul>
  ) : (
    <p className="text-muted">{empty}</p>
  );

export default function RcaDetailPage() {
  const { id } = useParams();
  const { data: r, isLoading, error, refetch } = useRca(id);
  if (error) return <ErrorState className="mt-10" error={error} onRetry={() => refetch()} />;
  if (isLoading || !r)
    return (
      <div className="space-y-4 p-6">
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="h-40" />
      </div>
    );
  return (
    <div className="mx-auto max-w-5xl p-4 lg:p-6 print:max-w-none print:p-0">
      <Link to="/rca" className="text-xs text-muted hover:text-ink print:hidden">
        ← RCA raporları
      </Link>
      <div className="mt-1 mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-ink">{r.title}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
            <ConfidenceBadge confidence={r.confidence} />
            <span>· {fmtTime(r.generated_at)}</span>
            <span>· üretim: {r.generated_by}</span>
            <Link to={`/investigations/${r.investigation_id}`} className="hover:text-ink">
              · araştırma
            </Link>
            {r.incident_id && (
              <Link to={`/incidents/${r.incident_id}`} className="hover:text-ink">
                · incident
              </Link>
            )}
          </div>
        </div>
        <div className="flex gap-2 print:hidden">
          <FeedbackBar investigationId={r.investigation_id} rcaId={r.id} />
          <Button size="sm" variant="outline" onClick={() => downloadText(`/rca/${r.id}/markdown`, `rca-${r.id}.md`).catch((e) => errorToast(e, "Dışa aktarılamadı"))}>
            <Download /> Markdown
          </Button>
          <Button size="sm" variant="ghost" onClick={() => window.print()}>
            <Printer /> Yazdır
          </Button>
        </div>
      </div>
      <div className="space-y-4">
        <Section n={1} title="Yönetici özeti">
          <Markdown>{r.summary}</Markdown>
        </Section>
        <Section n={2} title="Problem tanımı">
          <p>{r.problem_description ?? "—"}</p>
        </Section>
        <Section n={3} title="Zaman çizelgesi">
          {r.timeline.length ? (
            <ol className="space-y-1.5">
              {r.timeline.map((t, i) => (
                <li key={i} className="grid grid-cols-[150px_1fr] gap-3">
                  <span className="font-mono text-xs text-muted">{fmtTime(t.at)}</span>
                  <span>{t.event}</span>
                </li>
              ))}
            </ol>
          ) : (
            <p className="text-muted">—</p>
          )}
        </Section>
        <Section n={4} title="Etki analizi">
          <Markdown>{r.impact_analysis ?? "—"}</Markdown>
        </Section>
        <Section n={5} title="Kanıtlar">
          {r.supporting_evidence.length ? (
            <table className="w-full text-xs">
              <thead className="text-left text-muted">
                <tr className="border-b border-line">
                  <th className="py-1.5 pr-3 font-medium">Anahtar</th>
                  <th className="py-1.5 pr-3 font-medium">Tür</th>
                  <th className="py-1.5 pr-3 font-medium">Mekanizma</th>
                  <th className="py-1.5 pr-3 font-medium">Özet</th>
                  <th className="py-1.5 font-medium">Referans</th>
                </tr>
              </thead>
              <tbody>
                {r.supporting_evidence.map((e, i) => (
                  <tr key={`${e.key}-${i}`} className="border-b border-line align-top last:border-0">
                    <td className="py-1.5 pr-3 font-mono text-ink">{e.key}</td>
                    <td className="py-1.5 pr-3">{e.type ?? "—"}</td>
                    <td className="py-1.5 pr-3">{e.mechanism ? mechanismLabel(e.mechanism) : "—"}</td>
                    <td className="py-1.5 pr-3">{e.summary ?? "—"}</td>
                    <td className="py-1.5 font-mono text-[11px] text-muted">
                      {e.trace_id && <div>trace:{e.trace_id}</div>}
                      {e.dynatrace_query_id && <div>q:{e.dynatrace_query_id}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-muted">Mekanizmayı destekleyen kanıt yok.</p>
          )}
        </Section>
        <Section n={6} title="Kök neden hipotezi">
          <p className="text-ink">{r.suspected_root_cause ?? "Tanımlı mekanizmalarla açıklanamadı."}</p>
        </Section>
        <Section n={7} title="Katkıda bulunan faktörler">
          <List items={r.contributing_factors} />
        </Section>
        <Section n={8} title="Önerilen aksiyonlar">
          <List items={r.recommended_actions} />
          <p className="mt-2 text-[11px] text-muted">Ajan hiçbir aksiyonu kendisi uygulamaz; kritik aksiyonlar Onaylar ekranından onaya sunulur.</p>
        </Section>
        <Section n={9} title="Güven / belirsizlik">
          <div className="mb-2">
            <ConfidenceBadge confidence={r.confidence} />
          </div>
          <p className="whitespace-pre-wrap">{r.uncertainty ?? "—"}</p>
          {r.alternatives.length > 0 && (
            <div className="mt-2">
              <div className="text-xs font-medium text-ink">Alternatif açıklamalar</div>
              <List items={r.alternatives} />
            </div>
          )}
        </Section>
      </div>
    </div>
  );
}
