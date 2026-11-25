import { Crosshair, X } from "lucide-react";
import { Link } from "react-router";
import { useClearFocus, useWorkingMemory } from "@/hooks/queries";
import { fmtTime } from "@/lib/utils";

/** Working memory göstergesi: konuşmanın odağı (sonraki eksik soru bunu devralır) ve tur özeti sayısı. */
export function FocusBar({ conversationId, busy, canEdit }: { conversationId?: string; busy: boolean; canEdit: boolean }) {
  const wm = useWorkingMemory(conversationId);
  const clear = useClearFocus(conversationId);
  const f = wm.data?.focus;
  if (!conversationId || !f || !(f.apps?.length || f.window || f.mechanism)) return null;
  const inv = f.investigation?.startsWith("inv:") ? f.investigation.slice(4) : null;
  const parts: string[] = [];
  if (f.apps?.length) parts.push(f.apps.join(", "));
  if (f.window) parts.push(`${fmtTime(f.window.start)} – ${fmtTime(f.window.end, false)}`);
  if (f.mechanism) parts.push(f.mechanism);
  const title =
    "Konuşma odağı (working memory): uygulama veya zaman belirtmeyen takip soruları bu odağı devralır. " +
    `Hafızada ${wm.data?.digests.length ?? 0} tur özeti var.`;
  return (
    <div className="ml-auto flex min-w-0 items-center gap-1.5 text-xs text-ink-2" title={title}>
      <Crosshair className="size-3.5 shrink-0 text-muted" aria-hidden />
      <span className="sr-only">Konuşma odağı:</span>
      <span className="truncate">{parts.join(" · ")}</span>
      {inv && (
        <Link to={`/investigations/${inv}`} className="shrink-0 font-mono text-accent-text hover:underline">
          inv:{inv.slice(0, 8)}
        </Link>
      )}
      {canEdit && (
        <button
          type="button"
          className="shrink-0 rounded p-0.5 text-muted hover:bg-surface-2 hover:text-ink disabled:opacity-40"
          onClick={() => clear.mutate()}
          disabled={busy || clear.isPending}
          aria-label="Odağı temizle"
          title="Odağı temizle: sonraki soru önceki uygulama/pencereyi devralmaz"
        >
          <X className="size-3.5" />
        </button>
      )}
    </div>
  );
}
