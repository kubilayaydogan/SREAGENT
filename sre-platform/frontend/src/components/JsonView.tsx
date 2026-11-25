/* Katlanabilir JSON ağacı + ham JSON + arama + maskeli JSON kopyalama. Veri sunucuda maskelenmiştir; burada yeniden
   biçimlendirilmez (kopyalanan metin ekrandakiyle aynıdır). */
import { ChevronRight, Copy } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Button, Input, Segmented } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";

type Mode = "tree" | "raw";

function matches(v: unknown, q: string): boolean {
  if (!q) return false;
  if (v === null || typeof v !== "object") return String(v).toLowerCase().includes(q);
  return Object.entries(v as Record<string, unknown>).some(([k, x]) => k.toLowerCase().includes(q) || matches(x, q));
}

function Scalar({ v, q }: { v: unknown; q: string }) {
  const s = typeof v === "string" ? JSON.stringify(v) : String(v);
  const cls = v === null ? "text-muted" : typeof v === "string" ? "text-st-good-ink" : typeof v === "number" ? "text-accent-text" : "text-st-warning-ink";
  const hit = q && s.toLowerCase().includes(q);
  return <span className={cn("break-all", cls, hit && "rounded-sm bg-st-warning-bg")}>{s}</span>;
}

function Node({ k, v, depth, q, open0 }: { k: string | null; v: unknown; depth: number; q: string; open0: number }) {
  const isObj = v !== null && typeof v === "object";
  const auto = depth < open0 || (q !== "" && matches(v, q));
  const [open, setOpen] = useState<boolean | null>(null);
  const isOpen = open ?? auto;
  const keyHit = q && k?.toLowerCase().includes(q);
  const label = k !== null && <span className={cn("text-ink-2", keyHit && "rounded-sm bg-st-warning-bg")}>{k}: </span>;
  if (!isObj) {
    return (
      <div className="pl-4 leading-5">
        {label}
        <Scalar v={v} q={q} />
      </div>
    );
  }
  const entries = Array.isArray(v) ? v.map((x, i) => [String(i), x] as const) : Object.entries(v as Record<string, unknown>);
  const brace = Array.isArray(v) ? ["[", "]"] : ["{", "}"];
  return (
    <div className="leading-5">
      <button type="button" aria-expanded={isOpen} onClick={() => setOpen(!isOpen)} className="inline-flex items-center text-left hover:text-ink">
        <ChevronRight className={cn("size-3 shrink-0 text-muted transition-transform", isOpen && "rotate-90")} />
        {label}
        <span className="text-muted">
          {brace[0]}
          {!isOpen && `${entries.length} öğe${brace[1]}`}
        </span>
      </button>
      {isOpen && (
        <div className="ml-1.5 border-l border-line pl-1">
          {entries.map(([kk, x]) => (
            <Node key={kk} k={kk} v={x} depth={depth + 1} q={q} open0={open0} />
          ))}
          <span className="pl-1 text-muted">{brace[1]}</span>
        </div>
      )}
    </div>
  );
}

export function JsonView({ value, title, open = 2, className, maxH = "max-h-[60vh]" }: {
  value: unknown;
  title?: string;
  open?: number;
  className?: string;
  maxH?: string;
}) {
  const [mode, setMode] = useState<Mode>("tree");
  const [q, setQ] = useState("");
  const raw = useMemo(() => JSON.stringify(value, null, 2) ?? "null", [value]);
  const ql = q.trim().toLowerCase();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(raw);
      toast.success("Maskeli JSON kopyalandı");
    } catch {
      toast.error("Kopyalanamadı");
    }
  };
  return (
    <div className={cn("min-w-0 rounded-md border border-line", className)}>
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-2 py-1.5">
        {title && <span className="text-xs font-medium text-ink">{title}</span>}
        <span className="flex-1" />
        <Input aria-label={`${title ?? "JSON"} içinde ara`} placeholder="Ara" value={q} onChange={(e) => setQ(e.target.value)} className="h-7 w-32 text-xs" />
        <Segmented<Mode> label="görünüm" value={mode} onChange={setMode} options={[{ value: "tree", label: "Ağaç" }, { value: "raw", label: "Ham JSON" }]} />
        <Button size="sm" variant="ghost" onClick={copy} title="Maskeli JSON'u kopyala" aria-label={`${title ?? "JSON"} kopyala`}>
          <Copy className="size-3.5" />
        </Button>
      </div>
      <div className={cn("overflow-auto p-2 font-mono text-[11.5px]", maxH)}>
        {mode === "raw" ? (
          <pre className="whitespace-pre-wrap break-all text-ink-2">{raw}</pre>
        ) : (
          <Node k={null} v={value} depth={0} q={ql} open0={open} />
        )}
      </div>
    </div>
  );
}
