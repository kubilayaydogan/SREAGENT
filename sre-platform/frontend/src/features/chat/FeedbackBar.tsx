import { Check, MessageSquare, ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";
import { Button, Select, Textarea } from "@/components/ui/primitives";
import { Dialog } from "@/components/ui/overlay";
import { useFeedback, useSubmitFeedback } from "@/hooks/queries";
import { useAuth } from "@/services/auth";
import { cn } from "@/lib/utils";
import type { Verdict } from "@/types/api";

const VERDICT_LABEL: Record<Verdict, string> = { CORRECT: "Doğru", PARTIALLY_CORRECT: "Kısmen doğru", INCORRECT: "Yanlış" };

/** Analizi değerlendirme: doğru / kısmen / yanlış + 1–5 puan + yorum. Kullanıcı başına tek kayıt (güncellenir). */
export function FeedbackBar({ investigationId, rcaId, className }: { investigationId: string; rcaId?: string; className?: string }) {
  const { user } = useAuth();
  const { data } = useFeedback(investigationId);
  const submit = useSubmitFeedback(investigationId);
  const mine = data?.find((f) => f.user_id === user?.id);
  const [open, setOpen] = useState(false);
  const [verdict, setVerdict] = useState<Verdict>("PARTIALLY_CORRECT");
  const [rating, setRating] = useState<string>("");
  const [comment, setComment] = useState("");

  const quick = (v: Verdict) => submit.mutate({ verdict: v, rating: mine?.rating ?? null, comment: mine?.comment ?? null, rca_id: rcaId ?? null });

  return (
    <div className={cn("flex items-center gap-1", className)}>
      {mine && (
        <span className="mr-1 inline-flex items-center gap-1 text-2xs text-muted">
          <Check className="size-3" /> {VERDICT_LABEL[mine.verdict]}
          {mine.rating ? ` · ${mine.rating}/5` : ""}
        </span>
      )}
      <Button size="icon" variant="ghost" aria-label="analiz doğru" title="Doğru" className={cn(mine?.verdict === "CORRECT" && "text-st-good")} onClick={() => quick("CORRECT")} disabled={submit.isPending}>
        <ThumbsUp />
      </Button>
      <Button size="icon" variant="ghost" aria-label="analiz yanlış" title="Yanlış" className={cn(mine?.verdict === "INCORRECT" && "text-st-critical")} onClick={() => quick("INCORRECT")} disabled={submit.isPending}>
        <ThumbsDown />
      </Button>
      <Button
        size="icon"
        variant="ghost"
        aria-label="ayrıntılı değerlendirme"
        title="Ayrıntılı değerlendir"
        onClick={() => {
          setVerdict(mine?.verdict ?? "PARTIALLY_CORRECT");
          setRating(mine?.rating ? String(mine.rating) : "");
          setComment(mine?.comment ?? "");
          setOpen(true);
        }}
      >
        <MessageSquare />
      </Button>
      <Dialog
        open={open}
        onOpenChange={setOpen}
        title="Analizi değerlendir"
        description="Geri bildirim, gelecekteki analizlerde ve benzer vaka karşılaştırmalarında kullanılır."
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Vazgeç
            </Button>
            <Button
              variant="primary"
              loading={submit.isPending}
              onClick={() =>
                submit.mutate(
                  { verdict, rating: rating ? Number(rating) : null, comment: comment.trim() || null, rca_id: rcaId ?? null },
                  { onSuccess: () => setOpen(false) },
                )
              }
            >
              Kaydet
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <label className="text-xs text-ink-2">
              Sonuç
              <Select className="mt-1" value={verdict} onChange={(e) => setVerdict(e.target.value as Verdict)}>
                {(Object.keys(VERDICT_LABEL) as Verdict[]).map((v) => (
                  <option key={v} value={v}>
                    {VERDICT_LABEL[v]}
                  </option>
                ))}
              </Select>
            </label>
            <label className="text-xs text-ink-2">
              Puan (1–5)
              <Select className="mt-1" value={rating} onChange={(e) => setRating(e.target.value)}>
                <option value="">—</option>
                {[1, 2, 3, 4, 5].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </Select>
            </label>
          </div>
          <label className="block text-xs text-ink-2">
            Yorum (gerçek Root Cause, eksik evidence…)
            <Textarea className="mt-1" rows={4} maxLength={4000} value={comment} onChange={(e) => setComment(e.target.value)} />
          </label>
        </div>
      </Dialog>
    </div>
  );
}
