import { ChevronDown, ChevronRight } from "lucide-react";
import { Fragment, useMemo, useState } from "react";
import { Link } from "react-router";
import { EmptyState } from "@/components/ui/states";
import { Segmented } from "@/components/ui/primitives";
import { SIGNAL_LABEL, STEPS } from "@/lib/steps";
import { cn, fmtDuration, fmtTime } from "@/lib/utils";
import type { Evidence, ToolExecution } from "@/types/api";
import { AppTag } from "./badges";

const EXEC_CLS: Record<ToolExecution["execution_status"], string> = {
  STARTED: "text-accent-text",
  SUCCEEDED: "text-ink-2",
  FAILED: "text-st-critical-ink",
  SKIPPED: "text-muted",
};
const EXEC_DOT: Record<ToolExecution["execution_status"], string> = {
  STARTED: "bg-accent",
  SUCCEEDED: "bg-st-good",
  FAILED: "bg-st-critical",
  SKIPPED: "bg-st-unknown",
};
const EXEC_TR: Record<ToolExecution["execution_status"], string> = { STARTED: "çalışıyor", SUCCEEDED: "başarılı", FAILED: "başarısız", SKIPPED: "atlandı" };
const stepLabel = (id: string | null) => STEPS.find((s) => s.id === id)?.label ?? id ?? "—";

function ExpandButton({ open, onClick }: { open: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} aria-label="ayrıntı" aria-expanded={open} className="rounded-sm p-0.5 text-muted hover:text-ink">
      {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
    </button>
  );
}

/** MCP çağrısının LangSmith span linki: kaydın kimliği (mcp_call_id) span kimliğidir. Span yazılmadıysa null. */
export function callTraceUrl(t: ToolExecution, template?: string | null): string | null {
  if (!template || !(t.tool_arguments as { langsmith_span?: boolean }).langsmith_span) return null;
  return template.replace("{call_id}", t.id);
}

/** Tool execution geçmişi: adım, sinyal, durum, kayıt, süre, evidence; satır açılınca DQL ve hata. */
export function ToolTable({ rows, callUrlTemplate, runId, onEvidence }: {
  rows: ToolExecution[];
  callUrlTemplate?: string | null;
  /** verilirse MCP satırları MCP Çağrıları sayfasındaki kayda bağlanır */
  runId?: string;
  /** verilirse evidence anahtarı Evidence sekmesindeki satırı açar */
  onEvidence?: (key: string) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "failed">("all");
  const failed = rows.filter((r) => r.execution_status === "FAILED").length;
  const shown = filter === "failed" ? rows.filter((r) => r.execution_status === "FAILED") : rows;
  if (!rows.length) return <EmptyState title="Tool execution kaydı yok" />;
  return (
    <div>
      <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
        <span className="text-xs text-muted tabular-nums">
          {rows.length} çalıştırma · {failed} başarısız
        </span>
        <Segmented label="filtre" value={filter} onChange={setFilter} options={[{ value: "all", label: "Tümü" }, { value: "failed", label: `Başarısız (${failed})` }]} />
      </div>
      <div className="max-h-[70vh] overflow-auto">
        <table className="data-table">
          <thead>
            <tr>
              <th className="w-6" />
              <th>Adım</th>
              <th>Ölçüm / tool</th>
              <th>Durum</th>
              <th className="num">Kayıt</th>
              <th className="num">Süre</th>
              <th>Evidence / queryId</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((t) => {
              const isOpen = open === t.id;
              const dql = (t.request_preview?.dql_template ?? (t.tool_arguments as { dql_template?: string }).dql_template) as string | undefined;
              const spanUrl = callTraceUrl(t, callUrlTemplate);
              return (
                <Fragment key={t.id}>
                  <tr>
                    <td className="!pr-0">{(dql || t.error_message) && <ExpandButton open={isOpen} onClick={() => setOpen(isOpen ? null : t.id)} />}</td>
                    <td className="text-xs">{stepLabel(t.step)}</td>
                    <td>
                      <div className="text-ink">{t.signal ? (SIGNAL_LABEL[t.signal] ?? t.signal) : "—"}</div>
                      <div className="font-mono text-2xs text-muted">{t.tool_name}</div>
                    </td>
                    <td className="text-xs whitespace-nowrap">
                      <span className="inline-flex items-center gap-1.5">
                        <span aria-hidden className={cn("size-1.5 rounded-full", EXEC_DOT[t.execution_status])} />
                        <span className={EXEC_CLS[t.execution_status]}>{EXEC_TR[t.execution_status]}</span>
                      </span>
                      {t.result_status && <span className="ml-1.5 font-mono text-2xs text-muted">{t.result_status}</span>}
                    </td>
                    <td className="num">{t.record_count ?? "—"}</td>
                    <td className="num">{fmtDuration(t.duration_ms)}</td>
                    <td className="font-mono text-2xs text-muted">
                      {t.evidence_key &&
                        (onEvidence ? (
                          <button type="button" className="block text-accent-text hover:underline" onClick={() => onEvidence(t.evidence_key!)} title="Bu çağrının ürettiği evidence'ı aç">
                            {t.evidence_key}
                          </button>
                        ) : (
                          <div>{t.evidence_key}</div>
                        ))}
                      {runId && t.tool_name === "execute-dql" && (
                        <Link to={`/mcp-calls?run_id=${encodeURIComponent(runId)}&call=${t.id}`} className="block text-accent-text hover:underline">
                          MCP çağrısını aç
                        </Link>
                      )}
                      {t.dynatrace_query_id && <div title="Dynatrace queryId">q:{t.dynatrace_query_id}</div>}
                      {!t.evidence_key && !t.dynatrace_query_id && !spanUrl && "—"}
                      {spanUrl && (
                        <a href={spanUrl} target="_blank" rel="noreferrer" className="text-accent-text hover:underline" title={`mcp_call_id ${t.id}`}>
                          LangSmith span
                        </a>
                      )}
                    </td>
                  </tr>
                  {isOpen && (
                    <tr>
                      <td />
                      <td colSpan={6} className="!bg-surface-2">
                        {t.error_message && <div className="mb-2 font-mono text-xs break-all text-st-critical-ink">{t.error_message}</div>}
                        {dql && <DqlBlock dql={dql} />}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function DqlBlock({ dql }: { dql: string }) {
  return (
    <div>
      <div className="mb-1 text-xs font-medium text-muted">DQL şablonu (değerler maskeli)</div>
      <pre className="overflow-x-auto rounded-md border border-line bg-surface p-2 font-mono text-2xs whitespace-pre-wrap text-ink-2">{dql}</pre>
    </div>
  );
}

const DATA_OK = new Set(["OK", "EMPTY", null, undefined, ""]);

/** Evidence listesi. Satır açılınca ham içerik ve referanslar; `highlight` ile analizden gelen evidence vurgulanır. */
export function EvidenceTable({ rows, highlight, callHref }: {
  rows: Evidence[];
  highlight?: string | null;
  /** evidence anahtarı → onu üreten MCP çağrısının adresi (kayıt yoksa null) */
  callHref?: (evidenceKey: string) => string | null;
}) {
  const [open, setOpen] = useState<string | null>(highlight ?? null);
  const [filter, setFilter] = useState<"all" | "issues">("all");
  const issues = useMemo(() => rows.filter((r) => !DATA_OK.has(r.data_status)), [rows]);
  const shown = filter === "issues" ? issues : rows;
  if (!rows.length) return <EmptyState title="Evidence kaydı yok" description="Bu investigation için Dynatrace'ten evidence toplanamadı." />;
  return (
    <div>
      <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
        <span className="text-xs text-muted tabular-nums">
          {rows.length} evidence · {issues.length} sorunlu
        </span>
        <Segmented label="filtre" value={filter} onChange={setFilter} options={[{ value: "all", label: "Tümü" }, { value: "issues", label: `Sorunlu (${issues.length})` }]} />
      </div>
      <div className="max-h-[70vh] overflow-auto">
        <table className="data-table">
          <thead>
            <tr>
              <th className="w-6" />
              <th>Anahtar</th>
              <th>Ölçüm</th>
              <th>Uygulama</th>
              <th>Özet</th>
              <th>Zaman</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((e) => {
              const isOpen = open === e.evidence_key;
              const hl = highlight === e.evidence_key;
              return (
                <Fragment key={e.id}>
                  <tr id={`evidence-${e.evidence_key}`} className={cn(hl && "[&>td]:!bg-accent/8")}>
                    <td className="!pr-0">
                      <ExpandButton open={isOpen} onClick={() => setOpen(isOpen ? null : e.evidence_key)} />
                    </td>
                    <td className="font-mono text-xs text-ink">{e.evidence_key}</td>
                    <td className="text-xs whitespace-nowrap">
                      {e.signal ? (SIGNAL_LABEL[e.signal] ?? e.signal) : "—"}
                      {e.data_status && (
                        <span className={cn("ml-1.5 font-mono text-2xs", DATA_OK.has(e.data_status) ? "text-muted" : "text-st-warning-ink")}>{e.data_status}</span>
                      )}
                    </td>
                    <td>{e.application_id ? <AppTag id={e.application_id} /> : <span className="text-xs text-muted">—</span>}</td>
                    <td className="max-w-md text-xs">
                      <span className="line-clamp-2">{e.content ?? "—"}</span>
                    </td>
                    <td className="text-xs whitespace-nowrap text-muted">{fmtTime(e.timestamp)}</td>
                  </tr>
                  {isOpen && (
                    <tr>
                      <td />
                      <td colSpan={5} className="!bg-surface-2">
                        <EvidenceDetail e={e} callHref={callHref?.(e.evidence_key)} hasCalls={!!callHref} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function EvidenceDetail({ e, callHref, hasCalls }: { e: Evidence; callHref?: string | null; hasCalls?: boolean }) {
  const refs: [string, string | null][] = [
    ["Tür", e.evidence_type],
    ["Kaynak", e.source],
    ["Dynatrace queryId", e.dynatrace_query_id],
    ["trace.id", e.trace_id],
    ["span.id", e.span_id],
    ["Metric", e.metric_name],
    ["Entity", e.entity_id],
  ];
  return (
    <div className="space-y-2">
      {e.content && <p className="text-xs whitespace-pre-wrap text-ink-2">{e.content}</p>}
      <dl className="grid gap-x-6 gap-y-0.5 text-2xs sm:grid-cols-2">
        {refs
          .filter(([, v]) => v)
          .map(([k, v]) => (
            <div key={k} className="flex gap-2">
              <dt className="w-28 shrink-0 text-muted">{k}</dt>
              <dd className="min-w-0 font-mono break-all text-ink-2">{v}</dd>
            </div>
          ))}
      </dl>
      {hasCalls &&
        (callHref ? (
          <Link to={callHref} className="inline-block text-xs text-accent-text hover:underline">
            Bu veriyi getiren MCP çağrısını aç
          </Link>
        ) : (
          <p className="text-2xs text-muted">Bu evidence için MCP çağrı kaydı bulunamadı (türetilmiş evidence ya da eski kayıt).</p>
        ))}
      {e.query_reference && <DqlBlock dql={e.query_reference} />}
      {Object.keys(e.metadata ?? {}).length > 0 && (
        <details className="text-2xs">
          <summary className="cursor-pointer text-muted hover:text-ink">Ham metadata</summary>
          <pre className="mt-1 max-h-64 overflow-auto rounded-md border border-line bg-surface p-2 font-mono text-ink-2">{JSON.stringify(e.metadata, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}
