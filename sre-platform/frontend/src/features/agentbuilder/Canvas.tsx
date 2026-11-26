/** Workflow canvas (React Flow): sürüklenebilir / yakınlaştırılabilir; node'lar ve bağlantılar tanımın kendisidir.
 *  Konumlar ve viewport kaydedilir (layout), node'daki doğrulama hataları ve çözülmüş model node üzerinde görünür. */
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type NodeProps,
  type Viewport,
  applyEdgeChanges,
  applyNodeChanges,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { AlertTriangle, Brain, Braces, Flag, GitBranch, Play, Plug, Sparkles, XCircle } from "lucide-react";
import { useCallback, useMemo, type DragEvent } from "react";
import { cn } from "@/lib/utils";
import { NODE_TR, type Issue, type ResolvedModel, type WfEdge, type WfNode } from "./api";

export const NODE_ICON: Record<string, typeof Play> = {
  start: Play,
  llm: Sparkles,
  mcp_tool: Plug,
  condition: GitBranch,
  memory_retrieval: Brain,
  structured_output: Braces,
  result: Flag,
};

export interface NodeData extends Record<string, unknown> {
  node: WfNode;
  issues: Issue[];
  model: ResolvedModel | null;
  run?: { status: string; duration_ms?: number | null } | null;
  readOnly: boolean;
}

type FlowNode = Node<NodeData, "agent">;

function summary(n: WfNode): string {
  const c = n.config ?? {};
  switch (n.type) {
    case "llm":
    case "structured_output":
      return c.output_var ? `→ ${String(c.output_var)}` : "";
    case "mcp_tool":
      return [c.tool, c.output_var && `→ ${String(c.output_var)}`].filter(Boolean).join(" ");
    case "memory_retrieval":
      return c.output_var ? `→ ${String(c.output_var)}` : "";
    case "condition":
      return c.field ? `${String(c.field)} ${String(c.operator ?? "")} ${c.value != null ? String(c.value) : ""}`.trim() : "";
    case "result":
      return typeof c.template === "string" ? c.template.slice(0, 40) : "";
    default:
      return "";
  }
}

function AgentNode({ data, selected }: NodeProps<FlowNode>) {
  const { node: n, issues, model, run } = data;
  const Icon = NODE_ICON[n.type] ?? Sparkles;
  const errors = issues.filter((i) => i.severity === "error");
  const warns = issues.filter((i) => i.severity === "warning");
  const isCond = n.type === "condition";
  return (
    <div
      data-testid={`node-${n.id}`}
      className={cn(
        "w-56 rounded-md border bg-surface text-left shadow-sm",
        selected ? "border-accent ring-2 ring-accent/30" : "border-line-strong",
        errors.length ? "border-st-critical" : warns.length ? "border-st-warning" : "",
      )}
      title={issues.map((i) => `${i.severity === "error" ? "Hata" : "Uyarı"}: ${i.message}`).join("\n") || undefined}
    >
      {n.type !== "start" && <Handle type="target" position={Position.Left} className="!size-2.5 !bg-ink-2" />}
      <div className="flex items-center gap-1.5 border-b border-line px-2 py-1.5">
        <Icon className="size-3.5 shrink-0 text-accent-text" aria-hidden />
        <span className="truncate text-xs font-semibold text-ink">{n.label || NODE_TR[n.type] || n.type}</span>
        <span className="ml-auto text-2xs text-muted">{NODE_TR[n.type] ?? n.type}</span>
      </div>
      <div className="space-y-0.5 px-2 py-1.5">
        <div className="font-mono text-2xs text-muted">{n.id}</div>
        {summary(n) && <div className="truncate font-mono text-2xs text-ink-2">{summary(n)}</div>}
        {model && (
          <div className="truncate text-2xs" title={`Çözülmüş model (${model.source === "node" ? "node ayarı" : "agent varsayılanı"})`}>
            <span className="text-muted">model: </span>
            <span className="font-mono text-ink">{model.model ?? "seçilmedi"}</span>
            <span className="text-muted"> · {model.source === "node" ? "node" : "agent"}</span>
          </div>
        )}
        {run && (
          <div className={cn("text-2xs", run.status === "failed" ? "text-st-critical-ink" : run.status === "ok" ? "text-st-good-ink" : "text-st-warning-ink")}>
            {run.status === "ok" ? "✓ çalıştı" : run.status === "failed" ? "✗ hata" : "… çalışıyor"}
            {run.duration_ms != null && ` · ${run.duration_ms} ms`}
          </div>
        )}
        {errors.length > 0 && (
          <div className="flex items-start gap-1 text-2xs text-st-critical-ink">
            <XCircle className="mt-px size-3 shrink-0" aria-hidden />
            <span className="line-clamp-2">{errors[0].message}{errors.length > 1 && ` (+${errors.length - 1})`}</span>
          </div>
        )}
        {!errors.length && warns.length > 0 && (
          <div className="flex items-start gap-1 text-2xs text-st-warning-ink">
            <AlertTriangle className="mt-px size-3 shrink-0" aria-hidden />
            <span className="line-clamp-2">{warns[0].message}{warns.length > 1 && ` (+${warns.length - 1})`}</span>
          </div>
        )}
      </div>
      {isCond ? (
        <>
          <Handle id="true" type="source" position={Position.Right} style={{ top: "35%" }} className="!size-2.5 !bg-st-good" />
          <Handle id="false" type="source" position={Position.Right} style={{ top: "70%" }} className="!size-2.5 !bg-st-critical" />
          <span className="pointer-events-none absolute -right-9 top-[28%] text-2xs text-st-good-ink">true</span>
          <span className="pointer-events-none absolute -right-9 top-[63%] text-2xs text-st-critical-ink">false</span>
        </>
      ) : (
        n.type !== "result" && <Handle type="source" position={Position.Right} className="!size-2.5 !bg-ink-2" />
      )}
    </div>
  );
}

const nodeTypes = { agent: AgentNode };

export interface CanvasProps {
  nodes: WfNode[];
  edges: WfEdge[];
  viewport: Viewport;
  issues: Issue[];
  models: Record<string, ResolvedModel>;
  runState?: Record<string, { status: string; duration_ms?: number | null }>;
  selected: string | null;
  readOnly: boolean;
  onChange: (nodes: WfNode[], edges: WfEdge[]) => void;
  onViewport: (v: Viewport) => void;
  onSelect: (id: string | null) => void;
  onDropNode: (type: string, pos: { x: number; y: number }) => void;
}

function toFlowEdge(e: WfEdge): Edge {
  return {
    id: e.id,
    source: e.source,
    target: e.target,
    sourceHandle: e.branch ?? undefined,
    label: e.branch ?? undefined,
    markerEnd: { type: MarkerType.ArrowClosed },
    className: e.branch === "false" ? "stroke-st-critical" : undefined,
  };
}

function Inner(p: CanvasProps) {
  const rf = useReactFlow();
  const byNode = useMemo(() => {
    const m: Record<string, Issue[]> = {};
    for (const i of p.issues) if (i.node_id) (m[i.node_id] ??= []).push(i);
    return m;
  }, [p.issues]);

  const flowNodes: FlowNode[] = useMemo(
    () =>
      p.nodes.map((n) => ({
        id: n.id,
        type: "agent",
        position: n.position ?? { x: 0, y: 0 },
        selected: p.selected === n.id,
        deletable: !p.readOnly,
        data: { node: n, issues: byNode[n.id] ?? [], model: p.models[n.id] ?? null, run: p.runState?.[n.id] ?? null, readOnly: p.readOnly },
      })),
    [p.nodes, p.selected, p.readOnly, byNode, p.models, p.runState],
  );
  const flowEdges = useMemo(() => p.edges.map(toFlowEdge), [p.edges]);

  const onNodesChange = useCallback(
    (ch: NodeChange<FlowNode>[]) => {
      if (p.readOnly) {
        const sel = ch.find((c) => c.type === "select" && c.selected);
        if (sel && sel.type === "select") p.onSelect(sel.id);
        return;
      }
      const next = applyNodeChanges(ch, flowNodes);
      const removed = new Set(ch.filter((c) => c.type === "remove").map((c) => c.id));
      const nodes = next.map((fn) => ({ ...fn.data.node, position: { x: Math.round(fn.position.x), y: Math.round(fn.position.y) } }));
      const moved = ch.some((c) => c.type === "position" || c.type === "remove");
      if (moved) p.onChange(nodes, p.edges.filter((e) => !removed.has(e.source) && !removed.has(e.target)));
      const sel = ch.find((c) => c.type === "select");
      if (sel && sel.type === "select") p.onSelect(sel.selected ? sel.id : null);
    },
    [flowNodes, p],
  );

  const onEdgesChange = useCallback(
    (ch: EdgeChange[]) => {
      if (p.readOnly) return;
      if (!ch.some((c) => c.type === "remove")) return;
      const keep = new Set(applyEdgeChanges(ch, flowEdges).map((e) => e.id));
      p.onChange(p.nodes, p.edges.filter((e) => keep.has(e.id)));
    },
    [flowEdges, p],
  );

  const onConnect = useCallback(
    (c: Connection) => {
      if (p.readOnly || !c.source || !c.target) return;
      const branch = c.sourceHandle === "true" || c.sourceHandle === "false" ? c.sourceHandle : null;
      const id = `e_${c.source}_${branch ? `${branch}_` : ""}${c.target}`.slice(0, 80);
      if (p.edges.some((e) => e.id === id)) return;
      // aynı çıkıştan ikinci bağlantı: eskisi yenisiyle değişir (her node tek çıkış; koşul dal başına tek)
      const rest = p.edges.filter((e) => !(e.source === c.source && (e.branch ?? null) === branch));
      p.onChange(p.nodes, [...rest, { id, source: c.source, target: c.target, branch }]);
    },
    [p],
  );

  function onDrop(e: DragEvent) {
    e.preventDefault();
    const type = e.dataTransfer.getData("application/x-agent-node");
    if (!type || p.readOnly) return;
    const pos = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY });
    p.onDropNode(type, { x: Math.round(pos.x), y: Math.round(pos.y) });
  }

  return (
    <div className="h-full w-full" onDragOver={(e) => (e.preventDefault(), (e.dataTransfer.dropEffect = "move"))} onDrop={onDrop} aria-label="workflow canvas">
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={nodeTypes}
        defaultViewport={p.viewport}
        onMoveEnd={(_, v) => p.onViewport({ x: Math.round(v.x), y: Math.round(v.y), zoom: Math.round(v.zoom * 100) / 100 })}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onPaneClick={() => p.onSelect(null)}
        nodesDraggable={!p.readOnly}
        nodesConnectable={!p.readOnly}
        edgesFocusable={!p.readOnly}
        deleteKeyCode={p.readOnly ? null : ["Backspace", "Delete"]}
        minZoom={0.2}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable className="!bg-surface" />
      </ReactFlow>
    </div>
  );
}

export function Canvas(p: CanvasProps) {
  return (
    <ReactFlowProvider>
      <Inner {...p} />
    </ReactFlowProvider>
  );
}
