import { useCallback, useEffect, useReducer, useRef } from "react";
import { api } from "@/services/api";
import { streamRun } from "@/services/sse";
import type { AgentEvent } from "@/types/api";
import { initialRun, runReducer } from "./runReducer";

/** Bir ajan çalıştırmasına (run_id) bağlanır; olayları reducer'a aktarır. Bileşen kaldırılınca yalnız BAĞLANTI
 *  kapanır — çalıştırma backend'de sürer ve sonuç kayıtlara yazılır. */
export function useAgentRun(onEvent?: (e: AgentEvent) => void) {
  const [state, dispatch] = useReducer(runReducer, initialRun);
  const ctl = useRef<AbortController | null>(null);
  const runId = useRef<string | null>(null);
  const cb = useRef(onEvent);
  cb.current = onEvent;

  const attach = useCallback(async (id: string, streamUrl?: string) => {
    ctl.current?.abort();
    const c = new AbortController();
    ctl.current = c;
    runId.current = id;
    dispatch({ type: "start" });
    try {
      await streamRun(streamUrl ?? `/api/agent/stream/${id}`, {
        signal: c.signal,
        onEvent: (e) => {
          dispatch({ type: "event", event: e });
          cb.current?.(e);
        },
        onStatus: (v) => dispatch({ type: "connection", value: v }),
      });
    } catch (e) {
      if (!c.signal.aborted) dispatch({ type: "fail", message: (e as Error).message });
    }
  }, []);

  const cancel = useCallback(async () => {
    if (!runId.current) return;
    dispatch({ type: "cancel_requested" });
    try {
      await api.post(`/agent/runs/${runId.current}/cancel`);
    } catch {
      /* zaten bitmiş olabilir */
    }
  }, []);

  const detach = useCallback(() => {
    ctl.current?.abort();
    ctl.current = null;
    runId.current = null;
    dispatch({ type: "reset" });
  }, []);

  useEffect(() => () => ctl.current?.abort(), []);

  return { state, attach, cancel, detach, runId: runId.current };
}
