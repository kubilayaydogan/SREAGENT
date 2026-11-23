import { describe, expect, it } from "vitest";
import { initialRun, runReducer, type RunState } from "@/hooks/runReducer";
import type { AgentEvent, AgentEventType } from "@/types/api";

let seq = 0;
const ev = (type: AgentEventType, data: Record<string, unknown> = {}): AgentEvent => ({ type, data, seq: ++seq, ts: "2026-11-21T10:00:00Z" });
const play = (events: AgentEvent[], s: RunState = runReducer(initialRun, { type: "start" })) =>
  events.reduce((acc, e) => runReducer(acc, { type: "event", event: e }), s);

describe("runReducer", () => {
  it("tam bir sohbet turunu adımlara, araçlara ve metne dönüştürür", () => {
    const s = play([
      ev("agent_thinking", { step: "start", message: "Güncel telemetri analizi yapılacak." }),
      ev("investigation_started", { investigation_id: "inv-1" }),
      ev("tool_started", { id: "t1", tool: "execute-dql", signal: "http", step: "app_metrics" }),
      ev("tool_started", { id: "t2", tool: "execute-dql", signal: "db_conn", step: "db_metrics" }),
      ev("tool_completed", { id: "t1", tool: "execute-dql", signal: "http", step: "app_metrics", status: "OK", records: 12, duration_ms: 340, dynatrace_query_id: "q-1" }),
      ev("evidence_found", { evidence_key: "E1", signal: "http", records: 12 }),
      ev("tool_failed", { id: "t2", tool: "execute-dql", signal: "db_conn", step: "db_metrics", status: "ERROR", error: "timeout" }),
      ev("agent_thinking", { step: "correlate", node: "analyze", message: "Evidence ilişkilendirildi." }),
      ev("token", { text: "Merhaba " }),
      ev("token", { text: "dünya" }),
      ev("suggestions", { items: ["Önceki araştırmalarla karşılaştır"] }),
      ev("message_saved", { message_id: "m1" }),
      ev("investigation_completed", { investigation_id: "inv-1", status: "PARTIAL", severity: "medium" }),
      ev("done"),
    ]);
    expect(s.phase).toBe("done");
    expect(s.text).toBe("Merhaba dünya");
    expect(s.investigationId).toBe("inv-1");
    expect(s.tools.map((t) => [t.id, t.state])).toEqual([
      ["t1", "ok"],
      ["t2", "failed"],
    ]);
    expect(s.tools[0]).toMatchObject({ records: 12, durationMs: 340, queryId: "q-1" });
    expect(s.steps).toMatchObject({ start: "done", app_metrics: "done", db_metrics: "failed", correlate: "done", save: "done" });
    expect(s.evidence).toHaveLength(1);
    expect(s.suggestions).toEqual(["Önceki araştırmalarla karşılaştır"]);
    expect(s.investigation?.status).toBe("PARTIAL");
  });

  it("budget nedeniyle atlanan ve boş dönen query'leri ayırt eder", () => {
    const s = play([
      ev("tool_completed", { id: "a", signal: "spans", step: "traces", status: "EMPTY", records: 0 }),
      ev("tool_completed", { id: "b", signal: "hosts", step: "infra", status: "SKIPPED_BUDGET" }),
    ]);
    expect(s.tools.map((t) => t.state)).toEqual(["empty", "skipped"]);
    expect(s.steps.traces).toBe("done");
    expect(s.steps.infra).toBe("skipped");
  });

  it("hata + done: faz error olur, süren adımlar başarısız işaretlenir", () => {
    const s = play([
      ev("tool_started", { id: "x", signal: "http", step: "app_metrics" }),
      ev("error", { code: "timeout", message: "süre aşıldı" }),
      ev("done"),
    ]);
    expect(s.phase).toBe("error");
    expect(s.error).toEqual({ code: "timeout", message: "süre aşıldı" });
    expect(s.steps.app_metrics).toBe("failed");
  });

  it("start durumu sıfırlar", () => {
    const s = play([ev("token", { text: "eski" })]);
    expect(runReducer(s, { type: "start" }).text).toBe("");
  });
});
