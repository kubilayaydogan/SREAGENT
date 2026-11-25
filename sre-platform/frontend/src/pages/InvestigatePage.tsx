import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, Loader2, PanelLeft, RotateCcw, Search, WifiOff } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { ActivityTimeline } from "@/components/ActivityTimeline";
import { Markdown } from "@/components/Markdown";
import { Button, Skeleton } from "@/components/ui/primitives";
import { ErrorState } from "@/components/ui/states";
import { Composer, type TimeChoice } from "@/features/chat/Composer";
import { ConversationSidebar } from "@/features/chat/ConversationSidebar";
import { FocusBar } from "@/features/chat/FocusBar";
import { InvestigationCard } from "@/features/chat/InvestigationCard";
import { errorToast, useConversation, useMessages } from "@/hooks/queries";
import type { RunState } from "@/hooks/runReducer";
import { useAgentRun } from "@/hooks/useAgentRun";
import { api } from "@/services/api";
import { useAuth } from "@/services/auth";
import { cn, fmtTime } from "@/lib/utils";
import type { Message, RunOut } from "@/types/api";

const EXAMPLES = [
  "Reservation uygulaması neden yavaşladı?",
  "Son 30 dakikada PostgreSQL bağlantı sorunları var mı?",
  "Bu incident daha önce yaşandı mı?",
  "Son 24 saatin error rate'lerini analiz et.",
];

/** Takip soruları: sade metin bağlantıları (sohbet "pill"leri değil). */
function Suggestions({ items, onPick, disabled, title = "Takip soruları" }: { items: string[]; onPick: (s: string) => void; disabled?: boolean; title?: string }) {
  if (!items.length) return null;
  return (
    <div className="mt-3 border-t border-line pt-2">
      <div className="mb-1 text-xs font-medium text-muted">{title}</div>
      <ul className="space-y-px">
        {items.map((s) => (
          <li key={s}>
            <button
              disabled={disabled}
              onClick={() => onPick(s)}
              className="inline-flex items-center gap-1.5 rounded-sm px-1 py-0.5 text-left text-[13px] text-ink-2 hover:bg-surface-2 hover:text-ink disabled:opacity-50"
            >
              <ArrowRight className="size-3 shrink-0 text-muted" />
              {s}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Investigation log düzeni: soru başlık satırı, yanıt altında kenarlıklı blok. Avatar / baloncuk yok. */
function Turn({ role, children, meta }: { role: "user" | "assistant"; children: React.ReactNode; meta?: React.ReactNode }) {
  if (role === "user")
    return (
      <div className="flex items-baseline gap-3 pt-2">
        <span className="w-14 shrink-0 text-xs font-medium text-muted">Soru</span>
        <div className="min-w-0 flex-1 text-[14px] font-medium whitespace-pre-wrap text-ink">{children}</div>
        {meta && <span className="shrink-0 text-2xs text-muted tabular-nums">{meta}</span>}
      </div>
    );
  return (
    <div className="flex gap-3">
      <span className="w-14 shrink-0 pt-2.5 text-xs font-medium text-muted">Yanıt</span>
      <div className="min-w-0 flex-1">
        <div className="rounded-lg border border-line bg-surface px-4 py-3">{children}</div>
        {meta && <div className="mt-1 text-2xs text-muted">{meta}</div>}
      </div>
    </div>
  );
}

const SOURCE_LABEL: Record<string, string> = { llm: "Claude", deterministic: "deterministik (LLM yok)", memory: "kayıtlar" };

function SavedMessage({ m, onPick, busy, last }: { m: Message; onPick: (s: string) => void; busy: boolean; last: boolean }) {
  if (m.role === "user")
    return (
      <Turn role="user" meta={fmtTime(m.created_at)}>
        {m.content}
      </Turn>
    );
  const md = m.metadata ?? {};
  return (
    <Turn
      role="assistant"
      meta={
        <>
          {fmtTime(m.created_at)}
          {md.answer_source && ` · yanıt: ${SOURCE_LABEL[md.answer_source] ?? md.answer_source}`}
          {md.memory_counts && Object.values(md.memory_counts).some(Boolean) && ` · geçmiş kayıt: ${Object.entries(md.memory_counts).filter(([, v]) => v).map(([k, v]) => `${k} ${v}`).join(", ")}`}
        </>
      }
    >
      <Markdown>{m.content || "_(boş yanıt)_"}</Markdown>
      {md.investigation && <InvestigationCard inv={md.investigation} />}
      {last && <Suggestions items={md.suggestions ?? []} onPick={onPick} disabled={busy} />}
    </Turn>
  );
}

function LiveAnswer({ s }: { s: RunState }) {
  const lastThought = s.thinking[s.thinking.length - 1];
  const hasTools = s.tools.length > 0 || Object.keys(s.steps).length > 1;
  return (
    <Turn role="assistant">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted" aria-live="polite">
        {s.phase === "streaming" && <Loader2 className="size-3.5 animate-spin text-accent" />}
        <span>{lastThought?.message ?? "Başlatılıyor…"}</span>
        {s.connection === "reconnecting" && (
          <span className="inline-flex items-center gap-1 text-st-warning-ink">
            <WifiOff className="size-3.5" /> bağlantı koptu, yeniden bağlanılıyor (çalıştırma sürüyor)
          </span>
        )}
        {s.cancelRequested && s.phase === "streaming" && <span className="text-st-warning-ink">iptal istendi…</span>}
      </div>
      {hasTools && (
        <details open={!s.text} className="mb-2 rounded-md border border-line bg-surface-2 px-3 py-2">
          <summary className="cursor-pointer text-xs text-ink-2">
            Agent adımları · {s.tools.filter((t) => t.state !== "running").length}/{s.tools.length} query
            {s.evidence.length > 0 && ` · ${s.evidence.length} evidence`}
          </summary>
          <div className="mt-2">
            <ActivityTimeline steps={s.steps} tools={s.tools} compact hideEmpty />
          </div>
        </details>
      )}
      {s.text ? <Markdown streaming={s.phase === "streaming"}>{s.text}</Markdown> : s.phase === "streaming" ? <Skeleton className="h-4 w-2/3" /> : null}
      {s.error && (
        <div role="alert" className="mt-2 flex items-start gap-2 rounded-md border border-l-2 border-st-critical/50 px-3 py-2 text-xs text-ink">
          <AlertTriangle className="mt-px size-4 shrink-0 text-st-critical" />
          <span>
            {s.error.code === "cancelled" ? "Çalıştırma iptal edildi." : s.error.code === "timeout" ? "Timeout: analiz süre sınırını aştı." : s.error.message}
          </span>
        </div>
      )}
    </Turn>
  );
}

export default function InvestigatePage() {
  const { conversationId } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { can } = useAuth();
  const conv = useConversation(conversationId);
  const msgs = useMessages(conversationId);
  const [pendingUser, setPendingUser] = useState<string | null>(null);
  const [app, setApp] = useState("");
  const [showSide, setShowSide] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const attachedFor = useRef<string | null>(null);
  const runFailed = useRef(false);

  const run = useAgentRun((e) => {
    if (e.type === "error") runFailed.current = true;
    if (e.type === "done") {
      Promise.all([
        qc.invalidateQueries({ queryKey: ["messages"] }),
        qc.invalidateQueries({ queryKey: ["conversations"] }),
        qc.invalidateQueries({ queryKey: ["conversation"] }),
        qc.invalidateQueries({ queryKey: ["dashboard"] }),
        qc.invalidateQueries({ queryKey: ["wm"] }),
      ]).then(() => {
        setPendingUser(null);
        if (!runFailed.current) run.detach(); // hata/iptal mesajı ekranda kalsın
        runFailed.current = false;
      });
    }
  });
  const busy = run.state.phase === "streaming";

  // sayfa yenilendiyse / başka sekmede başlatıldıysa süren çalıştırmaya yeniden bağlan
  useEffect(() => {
    const id = conv.data?.active_run_id;
    if (id && !busy && attachedFor.current !== id) {
      attachedFor.current = id;
      void run.attach(id);
    }
  }, [conv.data?.active_run_id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (conv.data?.application_id) setApp(conv.data.application_id);
  }, [conv.data?.application_id]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [msgs.data?.items.length, run.state.text.length, pendingUser, run.state.tools.length]);

  async function send(text: string, time: TimeChoice | null) {
    setPendingUser(text);
    try {
      const r = await api.post<RunOut>("/agent/chat", {
        message: text,
        conversation_id: conversationId ?? null,
        application_id: app || null,
        time_range: time,
      });
      attachedFor.current = r.run_id;
      if (!conversationId && r.conversation_id) {
        nav(`/investigate/${r.conversation_id}`, { replace: true });
        qc.invalidateQueries({ queryKey: ["conversations"] });
      }
      void run.attach(r.run_id, r.stream_url);
    } catch (e) {
      setPendingUser(null);
      errorToast(e, "Soru gönderilemedi");
    }
  }

  async function resume() {
    if (!conversationId) return;
    try {
      const r = await api.post<RunOut>(`/conversations/${conversationId}/resume`);
      attachedFor.current = r.run_id;
      void run.attach(r.run_id, r.stream_url);
    } catch (e) {
      errorToast(e, "Sürdürülemedi");
    }
  }

  function newConversation() {
    run.detach();
    setPendingUser(null);
    setShowSide(false);
    nav("/investigate");
  }

  const items = msgs.data?.items ?? [];
  const lastAssistant = [...items].reverse().find((m) => m.role === "assistant");
  const canRun = can("sre");

  return (
    <div className="flex h-full">
      <aside className={cn("w-72 shrink-0 border-r border-line bg-surface max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:z-30", showSide ? "max-md:block" : "max-md:hidden")}>
        <ConversationSidebar onNew={newConversation} activeId={conversationId} />
      </aside>
      {showSide && <div className="fixed inset-0 z-20 bg-black/40 md:hidden" onClick={() => setShowSide(false)} />}
      <section className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-10 items-center gap-2 border-b border-line px-4">
          <button className="text-ink-2 md:hidden" onClick={() => setShowSide(true)} aria-label="konuşmalar">
            <PanelLeft className="size-5" />
          </button>
          <h1 className="truncate text-[13px] font-semibold text-ink">{conv.data?.title ?? (conversationId ? "…" : "Yeni investigation")}</h1>
          {busy && (
            <span className="ml-2 inline-flex items-center gap-1 text-xs text-accent-text">
              <Loader2 className="size-3.5 animate-spin" /> çalışıyor
            </span>
          )}
          <FocusBar conversationId={conversationId} busy={busy} canEdit={canRun} />
        </div>
        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-4xl space-y-4 px-4 py-5">
            {conversationId && (msgs.isLoading || conv.isLoading) && (
              <div className="space-y-4">
                <Skeleton className="ml-auto h-10 w-1/2" />
                <Skeleton className="h-32" />
              </div>
            )}
            {(conv.error || msgs.error) && <ErrorState error={conv.error ?? msgs.error} onRetry={() => (conv.refetch(), msgs.refetch())} />}
            {!conversationId && !pendingUser && (
              <div className="rounded-lg border border-line bg-surface p-4">
                <div className="flex items-start gap-3">
                  <Search className="mt-0.5 size-4 shrink-0 text-muted" />
                  <div>
                    <p className="text-[13px] font-medium text-ink">Yeni investigation</p>
                    <p className="mt-0.5 max-w-2xl text-xs text-muted">
                      Agent Dynatrace MCP üzerinden servis, PostgreSQL, log ve trace verilerini toplar; evidence'ı ilişkilendirir, deterministik risk score hesaplar ve
                      geçmiş investigation'larla karşılaştırır. Sonuç, investigation kaydı olarak saklanır.
                    </p>
                  </div>
                </div>
                {canRun && <Suggestions title="Örnek sorular" items={EXAMPLES} onPick={(s) => send(s, null)} />}
              </div>
            )}
            {items.map((m) => (
              <SavedMessage key={m.id} m={m} onPick={(s) => send(s, null)} busy={busy || !canRun} last={m.id === lastAssistant?.id && !busy && !pendingUser} />
            ))}
            {pendingUser && items[items.length - 1]?.content !== pendingUser && <Turn role="user">{pendingUser}</Turn>}
            {run.state.phase !== "idle" && <LiveAnswer s={run.state} />}
            {conv.data?.resumable && !busy && (
              <div className="flex items-center gap-3 rounded-md border border-l-2 border-st-warning/50 bg-surface px-3 py-2 text-[13px]">
                <AlertTriangle className="size-4 shrink-0 text-st-warning" />
                <span className="flex-1">Son tur yarıda kaldı (ör. servis yeniden başlatıldı). Kaldığı adımdan sürdürülebilir: {conv.data.pending_steps.join(", ")}</span>
                {canRun && (
                  <Button size="sm" variant="outline" onClick={resume}>
                    <RotateCcw /> Sürdür
                  </Button>
                )}
              </div>
            )}
            <div ref={bottom} />
          </div>
        </div>
        <Composer
          onSend={send}
          onCancel={run.cancel}
          busy={busy}
          disabled={!canRun}
          disabledReason="İzleyici rolü agent çalıştıramaz"
          app={app}
          onApp={setApp}
        />
      </section>
    </div>
  );
}
