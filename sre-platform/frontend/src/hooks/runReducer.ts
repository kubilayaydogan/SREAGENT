/** Ajan çalıştırma akışının (SSE) durum makinesi — saf fonksiyon, React'ten bağımsız test edilir. */
import type { AgentEvent, InvestigationSummary } from "@/types/api";

export type ToolState = "running" | "ok" | "empty" | "failed" | "skipped";

export interface ToolItem {
  id: string;
  tool: string;
  signal: string;
  step: string;
  state: ToolState;
  status?: string;
  records?: number | null;
  durationMs?: number | null;
  queryId?: string | null;
  error?: string | null;
}

export type StepState = "pending" | "running" | "done" | "failed" | "skipped";

export interface RunState {
  phase: "idle" | "streaming" | "done" | "error";
  connection: "connecting" | "open" | "reconnecting" | "closed";
  text: string;
  thinking: { step: string; message: string; ts: string }[];
  tools: ToolItem[];
  steps: Record<string, StepState>;
  evidence: { key: string; signal: string; records: number; queryId?: string | null }[];
  investigationId: string | null;
  investigation: InvestigationSummary | null;
  suggestions: string[];
  memoryCounts: Record<string, number> | null;
  messageId: string | null;
  error: { code: string; message: string } | null;
  cancelRequested: boolean;
}

export const initialRun: RunState = {
  phase: "idle",
  connection: "closed",
  text: "",
  thinking: [],
  tools: [],
  steps: {},
  evidence: [],
  investigationId: null,
  investigation: null,
  suggestions: [],
  memoryCounts: null,
  messageId: null,
  error: null,
  cancelRequested: false,
};

type D = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

export type RunAction =
  | { type: "start" }
  | { type: "event"; event: AgentEvent }
  | { type: "connection"; value: RunState["connection"] }
  | { type: "fail"; message: string }
  | { type: "cancel_requested" }
  | { type: "reset" };

function setStep(steps: Record<string, StepState>, id: string, st: StepState): Record<string, StepState> {
  const cur = steps[id];
  if (cur === "failed" && st !== "failed") return steps; // başarısızlık işareti korunur
  if (cur === "done" && st === "running") return steps;
  return { ...steps, [id]: st };
}

/** Bir adımdaki tüm araçlar bittiyse adımı kapat. */
function settle(steps: Record<string, StepState>, tools: ToolItem[], step: string): Record<string, StepState> {
  const ts = tools.filter((t) => t.step === step);
  if (!ts.length || ts.some((t) => t.state === "running")) return steps;
  if (ts.every((t) => t.state === "failed")) return setStep(steps, step, "failed");
  if (ts.every((t) => t.state === "skipped")) return setStep(steps, step, "skipped");
  return setStep(steps, step, "done");
}

function toolState(status: string | undefined, failed: boolean): ToolState {
  if (failed) return "failed";
  if (status === "SKIPPED_BUDGET") return "skipped";
  if (status === "EMPTY") return "empty";
  return "ok";
}

export function runReducer(s: RunState, a: RunAction): RunState {
  switch (a.type) {
    case "reset":
      return initialRun;
    case "start":
      return { ...initialRun, phase: "streaming", connection: "connecting", steps: { start: "running" } };
    case "connection":
      return { ...s, connection: a.value };
    case "cancel_requested":
      return { ...s, cancelRequested: true };
    case "fail":
      return { ...s, phase: "error", connection: "closed", error: { code: "client", message: a.message } };
    case "event":
      break;
  }
  const e = a.event;
  const d = e.data as D;
  switch (e.type) {
    case "investigation_started":
      return {
        ...s,
        investigationId: d.investigation_id ?? s.investigationId,
        steps: setStep(setStep(s.steps, "start", "done"), "app_metrics", "running"),
      };
    case "agent_thinking": {
      const step = String(d.step ?? "start");
      let steps = s.steps;
      // önceki adımlar tamamlandı say (araç olmayan adımlar: correlate, memory, rca)
      if (step === "correlate" || step === "memory" || step === "rca" || step === "extra") {
        steps = setStep(steps, step, "running");
        if (step === "rca") {
          for (const prev of ["correlate", "memory", "extra"]) if (steps[prev] === "running") steps = setStep(steps, prev, "done");
        }
      }
      if (d.node === "analyze") steps = setStep(steps, "correlate", "done");
      if (d.node === "finalize") steps = setStep(steps, "rca", "done");
      return { ...s, steps, thinking: [...s.thinking, { step, message: String(d.message ?? ""), ts: e.ts }].slice(-40) };
    }
    case "tool_started": {
      const t: ToolItem = { id: d.id, tool: d.tool, signal: d.signal, step: d.step, state: "running" };
      return { ...s, tools: [...s.tools, t], steps: setStep(s.steps, d.step, "running") };
    }
    case "tool_completed":
    case "tool_failed": {
      const failed = e.type === "tool_failed";
      const patch: Partial<ToolItem> = {
        state: toolState(d.status, failed),
        status: d.status,
        records: d.records ?? null,
        durationMs: d.duration_ms ?? null,
        queryId: d.dynatrace_query_id ?? null,
        error: d.error ?? null,
      };
      const exists = s.tools.some((t) => t.id === d.id);
      const tools = exists
        ? s.tools.map((t) => (t.id === d.id ? { ...t, ...patch } : t))
        : [...s.tools, { id: d.id, tool: d.tool, signal: d.signal, step: d.step, ...patch } as ToolItem];
      return { ...s, tools, steps: settle(s.steps, tools, d.step) };
    }
    case "evidence_found":
      return {
        ...s,
        evidence: [...s.evidence, { key: d.evidence_key, signal: d.signal, records: d.records, queryId: d.dynatrace_query_id }],
      };
    case "memory_recalled":
      return { ...s, memoryCounts: d.counts ?? null, steps: setStep(s.steps, "memory", "done") };
    case "token":
      return { ...s, text: s.text + String(d.text ?? "") };
    case "answer_replaced":
      return { ...s, text: String(d.text ?? s.text) };
    case "suggestions":
      return { ...s, suggestions: Array.isArray(d.items) ? d.items : [] };
    case "message_saved":
      return { ...s, messageId: d.message_id ?? null, steps: setStep(setStep(s.steps, "rca", "done"), "save", "done") };
    case "investigation_completed":
      return {
        ...s,
        investigation: d as InvestigationSummary,
        investigationId: d.investigation_id ?? s.investigationId,
        steps: setStep(setStep(setStep(s.steps, "correlate", "done"), "rca", "done"), "save", "done"),
      };
    case "error":
      return { ...s, error: { code: String(d.code ?? "error"), message: String(d.message ?? "bilinmeyen hata") } };
    case "done": {
      const steps = Object.fromEntries(
        Object.entries(s.steps).map(([k, v]) => [k, v === "running" ? (s.error ? "failed" : "done") : v]),
      ) as Record<string, StepState>;
      return { ...s, steps, phase: s.error ? "error" : "done", connection: "closed" };
    }
    default:
      return s;
  }
}
