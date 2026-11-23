import { ChevronDown, ChevronRight } from "lucide-react";
import { Fragment, useState } from "react";
import { EmptyState } from "@/components/ui/states";
import { SIGNAL_LABEL, STEPS } from "@/lib/steps";
import { cn, fmtDuration, fmtTime } from "@/lib/utils";
import type { Evidence, ToolExecution } from "@/types/api";
import { AppTag } from "./badges";

const EXEC_CLS: Record<ToolExecution["execution_status"], string> = {
  STARTED: "text-accent",
  SUCCEEDED: "text-st-good",
  FAILED: "text-st-critical",
  SKIPPED: "text-muted",
};
const EXEC_TR: Record<ToolExecution["execution_status"], string> = { STARTED: "çalışıyor", SUCCEEDED: "başarılı", FAILED: "başarısız", SKIPPED: "atlandı" };
const stepLabel = (id: string | null) => STEPS.find((s) => s.id === id)?.label ?? id ?? "—";

/** Araç çalıştırma geçmişi: araç adı, durum, süre, hata, kanıt referansı; satır açılınca DQL argümanı. */
export function ToolTable({ rows }: { rows: ToolExecution[] }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!rows.length) return <EmptyState title="Araç çalıştırması kaydı yok" />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted">
          <tr className="border-b border-line">
            <th className="w-6" />
            <th className="px-3 py-2 font-medium">Adım</th>
            <th className="px-3 py-2 font-medium">Araç / sinyal</th>
            <th className="px-3 py-2 font-medium">Durum</th>
            <th className="px-3 py-2 text-right font-medium">Kayıt</th>
            <th className="px-3 py-2 text-right font-medium">Süre</th>
            <th className="px-3 py-2 font-medium">Evidence / queryId</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((t) => {
            const isOpen = open === t.id;
            const dql = (t.tool_arguments as { dqlQueryString?: string }).dqlQueryString;
            return (
              <Fragment key={t.id}>
                <tr className={cn("border-b border-line align-top hover:bg-surface-2", t.execution_status === "FAILED" && "bg-st-critical/5")}>
                  <td className="pl-2">
                    {(dql || t.error_message) && (
                      <button onClick={() => setOpen(isOpen ? null : t.id)} aria-label="ayrıntı" className="p-1 text-muted hover:text-ink">
                        {isOpen ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
                      </button>
                    )}
                  </td>
                  <td className="px-3 py-2 text-xs text-ink-2">{stepLabel(t.step)}</td>
                  <td className="px-3 py-2">
                    <div className="text-ink">{t.signal ? SIGNAL_LABEL[t.signal] ?? t.signal : "—"}</div>
                    <div className="font-mono text-[11px] text-muted">{t.tool_name}</div>
                  </td>
                  <td className="px-3 py-2 text-xs">
                    <span className={EXEC_CLS[t.execution_status]}>{EXEC_TR[t.execution_status]}</span>
                    {t.result_status && <span className="ml-1 font-mono text-[11px] text-muted">{t.result_status}</span>}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-ink-2">{t.record_count ?? "—"}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-ink-2">{fmtDuration(t.duration_ms)}</td>
                  <td className="px-3 py-2 font-mono text-[11px] text-muted">
                    {t.evidence_key && <div>{t.evidence_key}</div>}
                    {t.dynatrace_query_id && <div title="Dynatrace queryId">q:{t.dynatrace_query_id}</div>}
                    {!t.evidence_key && !t.dynatrace_query_id && "—"}
                  </td>
                </tr>
                {isOpen && (
                  <tr className="border-b border-line bg-page/50">
                    <td />
                    <td colSpan={6} className="px-3 py-2">
                      {t.error_message && <div className="mb-2 font-mono text-xs break-all text-[#f08a8a]">{t.error_message}</div>}
                      {dql && <pre className="overflow-x-auto rounded bg-page p-2 font-mono text-[11px] whitespace-pre-wrap text-ink-2">{dql}</pre>}
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function EvidenceTable({ rows }: { rows: Evidence[] }) {
  if (!rows.length) return <EmptyState title="Evidence kaydı yok" description="Bu araştırmada Dynatrace'ten evidence toplanamadı." />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted">
          <tr className="border-b border-line">
            <th className="px-3 py-2 font-medium">Anahtar</th>
            <th className="px-3 py-2 font-medium">Tür</th>
            <th className="px-3 py-2 font-medium">Sinyal</th>
            <th className="px-3 py-2 font-medium">Uygulama</th>
            <th className="px-3 py-2 font-medium">Özet</th>
            <th className="px-3 py-2 font-medium">Referans</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.id} className="border-b border-line align-top hover:bg-surface-2">
              <td className="px-3 py-2 font-mono text-xs text-ink">{e.evidence_key}</td>
              <td className="px-3 py-2 text-xs text-ink-2">{e.evidence_type}</td>
              <td className="px-3 py-2 text-xs text-ink-2">
                {e.signal ? SIGNAL_LABEL[e.signal] ?? e.signal : "—"}
                {e.data_status && <span className="ml-1 font-mono text-[10px] text-muted">{e.data_status}</span>}
              </td>
              <td className="px-3 py-2">{e.application_id ? <AppTag id={e.application_id} /> : <span className="text-xs text-muted">—</span>}</td>
              <td className="max-w-md px-3 py-2 text-xs text-ink-2">
                {e.content ?? "—"}
                {e.timestamp && <div className="text-[11px] text-muted">{fmtTime(e.timestamp)}</div>}
              </td>
              <td className="px-3 py-2 font-mono text-[11px] text-muted">
                {e.trace_id && <div title="trace.id">trace:{e.trace_id}</div>}
                {e.span_id && <div title="span.id">span:{e.span_id}</div>}
                {e.dynatrace_query_id && <div title="Dynatrace queryId">q:{e.dynatrace_query_id}</div>}
                {e.metric_name && <div>{e.metric_name}</div>}
                {!e.trace_id && !e.dynatrace_query_id && !e.metric_name && "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
