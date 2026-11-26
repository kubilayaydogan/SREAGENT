/** Taslak testi: girdi → SSE ile çalıştırma; sonuç, süre, çalışan node'lar, token kullanımı ve hatalar.
 *  Test platform politikalarını, zorunlu middleware'leri ve tool yetkilerini aynen uygular. */
import { ExternalLink, Play, Square } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { JsonView } from "@/components/JsonView";
import { Markdown } from "@/components/Markdown";
import { Button, Label, Segmented, Textarea } from "@/components/ui/primitives";
import { Notice } from "@/components/ui/states";
import { useAgentRun } from "@/hooks/useAgentRun";
import { api, ApiError } from "@/services/api";
import { errorToast } from "@/hooks/queries";
import { cn, fmtDuration, fmtNum } from "@/lib/utils";
import type { RunOut } from "@/types/api";
import type { AgentResult, Issue, NodeRun } from "./api";

export function TestPanel({ agentId, canTest, blocked, onNodes, onIssues }: {
  agentId: string;
  canTest: boolean;
  blocked: string | null;
  onNodes: (m: Record<string, { status: string; duration_ms?: number | null }>) => void;
  onIssues: (i: Issue[]) => void;
}) {
  const [input, setInput] = useState("");
  const [sem, setSem] = useState<"chat" | "scheduled">("chat");
  const [nodes, setNodes] = useState<NodeRun[]>([]);
  const [result, setResult] = useState<AgentResult | null>(null);
  const [starting, setStarting] = useState(false);
  const run = useAgentRun((e) => {
    if (e.type === "agent_node") {
      const n = e.data as unknown as NodeRun;
      setNodes((prev) => {
        const next = [...prev.filter((x) => x.node_id !== n.node_id), n];
        onNodes(Object.fromEntries(next.map((x) => [x.node_id, { status: x.status, duration_ms: x.duration_ms }])));
        return next;
      });
    }
    if (e.type === "agent_result") setResult(e.data as unknown as AgentResult);
  });
  const busy = run.state.phase === "streaming";

  async function start() {
    setNodes([]);
    setResult(null);
    onNodes({});
    setStarting(true);
    try {
      const r = await api.post<RunOut>(`/agents/${agentId}/test`, { input, semantics: sem });
      onIssues([]);
      void run.attach(r.run_id, r.stream_url);
    } catch (e) {
      if (e instanceof ApiError && Array.isArray(e.details)) onIssues(e.details as Issue[]);
      errorToast(e, "Test başlatılamadı");
    } finally {
      setStarting(false);
    }
  }

  const err = result?.error;
  return (
    <div className="space-y-3">
      {blocked && <Notice tone="warning">{blocked}</Notice>}
      <div>
        <Label htmlFor="test-input">Girdi</Label>
        <Textarea id="test-input" rows={3} maxLength={8000} value={input} onChange={(e) => setInput(e.target.value)} className="w-full text-xs" placeholder="Soru ya da zamanlanmış girdi" />
      </div>
      <div className="flex items-center gap-2">
        <Segmented
          label="test modu"
          value={sem}
          onChange={setSem}
          options={[
            { value: "chat", label: "Soru-Cevap gibi", title: "streaming ve konuşma semantiği" },
            { value: "scheduled", label: "Zamanlanmış gibi", title: "çıktı formatı ve şeması uygulanır" },
          ]}
        />
        {busy ? (
          <Button size="sm" variant="outline" className="ml-auto" onClick={run.cancel}>
            <Square /> Durdur
          </Button>
        ) : (
          <Button size="sm" variant="primary" className="ml-auto" loading={starting} disabled={!canTest || !!blocked} onClick={start}>
            <Play /> Taslağı test et
          </Button>
        )}
      </div>
      {(busy || nodes.length > 0) && (
        <div>
          <Label>Çalışan node'lar</Label>
          <ol className="space-y-0.5 text-xs">
            {nodes.map((n) => (
              <li key={n.node_id} className="flex items-center gap-2">
                <span className={cn("w-4 text-center", n.status === "ok" ? "text-st-good-ink" : n.status === "failed" ? "text-st-critical-ink" : "text-st-warning-ink")}>
                  {n.status === "ok" ? "✓" : n.status === "failed" ? "✗" : "…"}
                </span>
                <span className="font-mono">{n.node_id}</span>
                {n.model && <span className="font-mono text-2xs text-muted">{n.model}</span>}
                {n.branch && <span className="text-2xs text-muted">dal: {n.branch}</span>}
                <span className="ml-auto tabular-nums text-muted">{n.duration_ms != null ? `${n.duration_ms} ms` : ""}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
      {busy && run.state.text && (
        <div className="rounded-md border border-line p-2 text-[13px]">
          <Markdown>{run.state.text}</Markdown>
        </div>
      )}
      {result && (
        <div className="space-y-2 rounded-md border border-line p-2">
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
            <span>
              Durum: <b className={result.status === "completed" ? "text-st-good-ink" : "text-st-critical-ink"}>{result.status}</b>
            </span>
            <span>Süre: {fmtDuration(result.duration_ms)}</span>
            <span>
              Token: {fmtNum(result.usage?.input_tokens ?? 0)} girdi / {fmtNum(result.usage?.output_tokens ?? 0)} çıktı
            </span>
            <span>LLM çağrısı: {result.usage?.calls ?? 0}</span>
            <span>Model: {result.models.map((m) => m.model).filter(Boolean).join(", ") || "—"}</span>
          </div>
          {err && (
            <Notice tone="critical">
              {err.message}
              {err.node_id && <> (node: <code>{err.node_id}</code>)</>} · <code>{err.code}</code>
            </Notice>
          )}
          {result.output != null &&
            (typeof result.output === "string" ? (
              <div className="text-[13px]">
                <Markdown>{result.output}</Markdown>
              </div>
            ) : (
              <JsonView value={result.output} />
            ))}
          <div className="flex gap-3 text-xs">
            <Link className="inline-flex items-center gap-1 text-accent-text hover:underline" to={`/agent-trace/${encodeURIComponent(result.run_id)}`}>
              Agent Trace <ExternalLink className="size-3" />
            </Link>
            <Link className="inline-flex items-center gap-1 text-accent-text hover:underline" to={`/context-windows?run_id=${encodeURIComponent(result.run_id)}`}>
              Context Windows <ExternalLink className="size-3" />
            </Link>
          </div>
        </div>
      )}
      {run.state.error && !result && <Notice tone="critical">{run.state.error.message}</Notice>}
    </div>
  );
}
