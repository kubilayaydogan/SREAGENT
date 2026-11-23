import { AlertCircle, CheckCircle2, ChevronDown, ChevronRight, Circle, CircleSlash, Loader2, MinusCircle } from "lucide-react";
import { useState, type ReactElement } from "react";
import type { StepState, ToolItem } from "@/hooks/runReducer";
import { SIGNAL_LABEL, STEPS } from "@/lib/steps";
import { cn, fmtDuration } from "@/lib/utils";
import type { ToolExecution } from "@/types/api";

const STEP_ICON: Record<StepState, ReactElement> = {
  pending: <Circle className="size-4 text-line-strong" />,
  running: <Loader2 className="size-4 animate-spin text-accent" />,
  done: <CheckCircle2 className="size-4 text-st-good" />,
  failed: <AlertCircle className="size-4 text-st-critical" />,
  skipped: <MinusCircle className="size-4 text-muted" />,
};
const STEP_TEXT: Record<StepState, string> = { pending: "bekliyor", running: "çalışıyor", done: "tamam", failed: "hata", skipped: "atlandı" };

const TOOL_TEXT: Record<ToolItem["state"], [string, string]> = {
  running: ["çalışıyor", "text-accent"],
  ok: ["veri alındı", "text-st-good"],
  empty: ["veri yok", "text-muted"],
  failed: ["başarısız", "text-st-critical"],
  skipped: ["bütçe doldu", "text-muted"],
};

/** Kaydedilmiş araç çalıştırmalarını (DB) canlı akıştakiyle aynı biçime çevirir. */
export function toolsFromRecords(rows: ToolExecution[]): { tools: ToolItem[]; steps: Record<string, StepState> } {
  const tools: ToolItem[] = rows.map((r) => ({
    id: r.id,
    tool: r.tool_name,
    signal: r.signal ?? r.tool_name,
    step: r.step ?? "extra",
    state:
      r.execution_status === "STARTED"
        ? "running"
        : r.execution_status === "FAILED"
          ? "failed"
          : r.execution_status === "SKIPPED"
            ? "skipped"
            : r.result_status === "EMPTY"
              ? "empty"
              : "ok",
    status: r.result_status ?? undefined,
    records: r.record_count,
    durationMs: r.duration_ms,
    queryId: r.dynatrace_query_id,
    error: r.error_message,
  }));
  const steps: Record<string, StepState> = { start: "done" };
  for (const s of STEPS) {
    const ts = tools.filter((t) => t.step === s.id);
    if (!ts.length) continue;
    steps[s.id] = ts.some((t) => t.state === "running")
      ? "running"
      : ts.every((t) => t.state === "failed")
        ? "failed"
        : ts.every((t) => t.state === "skipped")
          ? "skipped"
          : "done";
  }
  return { tools, steps };
}

export function ActivityTimeline({ steps, tools, compact, hideEmpty }: { steps: Record<string, StepState>; tools: ToolItem[]; compact?: boolean; hideEmpty?: boolean }) {
  const [openStep, setOpenStep] = useState<string | null>(null);
  const shown = STEPS.filter((s) => !hideEmpty || steps[s.id] || tools.some((t) => t.step === s.id));
  return (
    <ol className="relative" aria-label="ajan adımları">
      {shown.map((s, i) => {
        const st: StepState = steps[s.id] ?? "pending";
        const ts = tools.filter((t) => t.step === s.id);
        const failedN = ts.filter((t) => t.state === "failed").length;
        const dur = ts.reduce((a, t) => a + (t.durationMs ?? 0), 0);
        const isOpen = openStep === s.id || (!compact && openStep === null && st === "running");
        return (
          <li key={s.id} className="relative pb-3 pl-7 last:pb-0">
            {i < shown.length - 1 && <span aria-hidden className="absolute top-5 bottom-0 left-[7px] w-px bg-line-strong" />}
            <span className="absolute top-0.5 left-0" aria-label={STEP_TEXT[st]}>
              {STEP_ICON[st]}
            </span>
            <button
              type="button"
              disabled={!ts.length}
              onClick={() => setOpenStep(isOpen ? "" : s.id)}
              className="flex w-full items-center gap-2 text-left disabled:cursor-default"
            >
              <span className={cn("text-sm", st === "pending" ? "text-muted" : "text-ink")}>{s.label}</span>
              {ts.length > 0 && (
                <span className="text-xs text-muted">
                  {ts.length} sorgu{failedN ? ` · ${failedN} hata` : ""}
                  {dur ? ` · ${fmtDuration(dur)}` : ""}
                </span>
              )}
              {ts.length > 0 && (isOpen ? <ChevronDown className="ml-auto size-3.5 text-muted" /> : <ChevronRight className="ml-auto size-3.5 text-muted" />)}
            </button>
            {!compact && st === "pending" && <div className="text-xs text-muted">{s.hint}</div>}
            {isOpen && ts.length > 0 && (
              <ul className="mt-1.5 space-y-1 rounded-md border border-line bg-page/60 p-2">
                {ts.map((t) => (
                  <ToolRow key={t.id} t={t} />
                ))}
              </ul>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function ToolRow({ t }: { t: ToolItem }) {
  const [label, cls] = TOOL_TEXT[t.state];
  return (
    <li className="text-xs">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
        {t.state === "running" ? <Loader2 className="size-3 animate-spin text-accent" /> : t.state === "failed" ? <CircleSlash className="size-3 text-st-critical" /> : <span className="size-3" />}
        <span className="text-ink-2">{SIGNAL_LABEL[t.signal] ?? t.signal}</span>
        <span className="font-mono text-[10px] text-muted">{t.tool}</span>
        <span className={cls}>{label}</span>
        {t.records != null && t.state !== "failed" && <span className="text-muted">{t.records} kayıt</span>}
        {t.durationMs != null && <span className="text-muted">{fmtDuration(t.durationMs)}</span>}
        {t.queryId && (
          <span className="font-mono text-[10px] text-muted" title="Dynatrace queryId (evidence referansı)">
            q:{t.queryId.slice(0, 12)}
          </span>
        )}
      </div>
      {t.error && <div className="mt-0.5 pl-5 font-mono text-[11px] break-all text-[#f08a8a]">{t.error}</div>}
    </li>
  );
}
