"""Sohbet orkestrasyonu (LangGraph + AsyncPostgresSaver).

Konuşma grafiği: route → [investigate] → recall → respond. Durum konuşmanın `thread_id`'si ile PostgreSQL'e
checkpoint'lenir: yeniden başlatmadan sonra konuşma kaldığı yerden devam eder, yarıda kalan bir tur `resume()` ile
sürdürülebilir. Telemetri analizi mevcut airlab_agent grafiğiyle (InvestigationRunner) yapılır.
"""
from __future__ import annotations

from airlab_agent.errors import UserError

import logging
import operator
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from airlab_agent.graph import new_run_id
from airlab_agent.graph import FAILED_SIGNAL
from airlab_agent.llm import AnalysisRequest, LlmUnavailable, resolve_window
from airlab_agent.report import deterministic_explanation

from ..db.models import MessageRole, Trigger
from ..db.session import transaction
from ..repositories import conversations, investigations
from . import prompts
from . import working_memory as wm
from .investigation import InvestigationRunner, summary_of
from .llm_port import ChatRoute, heuristic_route

log = logging.getLogger("sre.chat")
MAX_STATE_MESSAGES = 20


def _keep_last(a: list, b: list) -> list:
    return (operator.add(a or [], b or []))[-MAX_STATE_MESSAGES:]


class ChatState(TypedDict, total=False):
    messages: Annotated[list[dict], _keep_last]
    focus: dict | None                                     # working memory: konuşma odağı (bkz. working_memory)
    digests: Annotated[list[dict], wm.append_digests]       # working memory: tur özetleri (eski turlar)
    question: str
    route: dict
    investigation: dict | None
    memory: dict | None
    answer: str
    answer_source: str
    suggestions: list[str]
    last_investigation_id: str | None


@dataclass
class TurnContext:
    user_id: uuid.UUID
    conversation_id: uuid.UUID
    thread_id: str
    emit: Any
    application_id: str | None = None
    minutes: int | None = None
    start: datetime | None = None
    end: datetime | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    result: dict = field(default_factory=dict)
    rc: Any = None                                 # tur başında kurulan değişmez Settings yapılandırması (RunConfig)


def suggestions_for(route: dict, inv: dict | None, memory: dict | None) -> list[str]:
    out: list[str] = []
    mech = (inv or {}).get("mechanism") or route.get("mechanism")
    apps = (inv or {}).get("affected_apps") or route.get("focus_apps") or []
    app = apps[0].split("-")[0].capitalize() if apps else None
    if route.get("intent") in ("analyze", "compare"):
        out.append(f"{app} uygulamasında daha önce benzer bir sorun yaşandı mı?" if app else
                   "Bu sorun daha önce yaşandı mı?")
        if mech:
            out.append("Bu sorun için kayıtlı bir runbook var mı?")
        out.append("Önceki araştırmalarla karşılaştır.")
        if inv and inv.get("status") in ("COMPLETED", "PARTIAL"):
            out.append("Bu araştırma için RCA raporu oluştur.")
    else:
        out.append("Son 30 dakikayı analiz et.")
        out.append("Son 24 saatin error rate'lerini analiz et.")
        if memory and memory.get("incidents"):
            out.append("Önceki incident nasıl çözülmüştü?")
    return out[:4]


def _hist_block(memory: dict | None) -> dict | None:
    if not memory:
        return None
    keep = {k: memory[k] for k in ("episodes", "current_symptoms", "investigations", "incidents", "rca", "runbooks",
                                   "known_issues", "semantic") if memory.get(k)}
    return keep or {"note": "benzer kayıt bulunamadı"}


def _mem_cfg(rc) -> dict:
    return ((getattr(rc, "effective", None) or {}).get("agent_memory") or {}) if rc is not None else {}


def _mem_flag(rc, name: str) -> bool:
    c = _mem_cfg(rc)
    return bool(c.get("memory_enabled", True)) and bool(c.get(name, True))


def _inject(rc, section: str, block: dict | None, task: str, *, llm_=None, observe_only: bool = False):
    """Memory Context Builder: bütçe + Context Injection anahtarı. Varsayılan ayarlarda blok DEĞİŞMEZ.
    Ne eklendiği Context Injection Inspector için kaydedilir (gözlem hatası yanıtı etkilemez)."""
    from ..memory import context as mctx
    from ..memory.observed import record_injection
    c = _mem_cfg(rc)
    on = _mem_flag(rc, "context_injection_enabled")
    out, dropped = mctx.apply_budget(block, max_records=int(c.get("max_retrieved_records") or 0),
                                     max_tokens=int(c.get("max_context_tokens") or 0)) if on else (None, [])
    if observe_only:
        out = block if on else None
    try:
        record_injection(section=section, before=block, after=out, formatted=prompts.dump(out) if out else None,
                         dropped=dropped, injected=bool(out) and llm_ is not None,
                         budget={"max_retrieved_records": int(c.get("max_retrieved_records") or 0),
                                 "max_context_tokens": int(c.get("max_context_tokens") or 0)},
                         reason=None if (out and llm_ is not None) else
                         ("Settings: Context Injection kapalı" if not on else
                          "LLM kullanılmadı (deterministik yanıt)" if llm_ is None else "boş blok"),
                         llm_task=task, model=getattr(llm_, "model_name", None))
    except Exception:  # noqa: BLE001
        log.debug("context injection kaydı yazılamadı", exc_info=True)
    try:                                     # Context Windows: bu bölümün dahil / çıkarılan referansları
        from airlab_agent.context_window import note_context
        sent = (block if observe_only else out) if llm_ is not None else None
        note_context(section.strip("<>"), status="injected" if sent else "not_injected",
                     included=mctx.refs(sent) if isinstance(sent, dict) else [],
                     dropped=[{"ref": d.get("ref"), "reason": d.get("reason")} for d in (dropped or [])
                              if isinstance(d, dict)],
                     reason=None if sent else ("Settings: Context Injection kapalı" if not on else
                                               "LLM kullanılmadı" if llm_ is None else "boş blok"),
                     token_estimate=mctx.estimate_tokens(prompts.dump(sent)) if sent else 0)
    except Exception:  # noqa: BLE001
        log.debug("context window kararı yazılamadı", exc_info=True)
    return out if not observe_only else block


OUTCOME_TR = {"confirmed": "feedback ile doğrulandı", "partially_confirmed": "kısmen doğrulandı",
              "rejected": "hipotez yanlış çıktı (feedback)", "unverified": "sonuç doğrulanmadı"}


def _when(v) -> str:
    """Deterministik metinde zaman: TSİ; ayrıştırılamayan değer olduğu gibi kalır."""
    from airlab_agent.timefmt import tsi
    return tsi(v, missing=str(v or ""))


def _det_episodes(items: list[dict]) -> list[str]:
    out = ["### Benzer geçmiş episode'lar"]
    for e in items:
        sim = f" · benzerlik {e['similarity']:.2f}" if e.get("similarity") is not None else ""
        out.append(f"- `{e['ref']}` {_when(e.get('at'))} {e.get('mechanism') or 'mechanism yok'}{sim} — "
                   f"{OUTCOME_TR.get(e.get('outcome'), e.get('outcome'))}")
        if e.get("shared_symptoms"):
            out.append(f"  ortak belirtiler: {', '.join(e['shared_symptoms'][:6])}")
        if e.get("resolution"):
            out.append(f"  çözüm kaydı: {e['resolution']}")
    return out


def _det_history(memory: dict) -> str:
    lines, any_ = [], False
    if memory.get("episodes"):
        lines += _det_episodes(memory["episodes"])
        any_ = True
    titles = {"investigations": "Araştırmalar", "incidents": "Incident'lar", "rca": "RCA raporları",
              "runbooks": "Runbook'lar", "known_issues": "Bilinen sorunlar", "conversations": "Önceki konuşmalar"}
    for k, t in titles.items():
        items = memory.get(k) or []
        if not items:
            continue
        any_ = True
        lines.append(f"### {t}")
        for i in items:
            when = _when(i.get("created_at") or i.get("detected_at") or i.get("generated_at") or i.get("at"))
            extra = i.get("mechanism") or i.get("status") or i.get("risk_level") or ""
            lines.append(f"- `{i.get('ref', '')}` {i.get('title') or i.get('excerpt', '')} {when} {extra}".rstrip())
    if memory.get("last_investigation"):
        li = memory["last_investigation"]
        lines.append(f"\nSon araştırma: `{li['ref']}` {li['title']} — {li.get('mechanism') or 'mechanism yok'}")
        any_ = True
    return "\n".join(lines) if any_ else ("Bu soruya uyan kayıt bulunamadı. Güncel durum için örneğin "
                                          "\"Son 30 dakikayı analiz et\" diye sorabilirsiniz.")


def data_banner(report: dict | None) -> str:
    """Kısmi/eksik veri uyarısı — deterministik; LLM'e bırakılmaz (sahte kesinlik olmasın)."""
    if not report:
        return ""
    errs = report.get("errors") or []
    cov = (report.get("data_coverage") or {}).get("ratio")
    parts = []
    if report.get("status") == "partial" or errs:
        # Yanıt vermeyen query'ler adıyla yazılır: kullanıcı hangi verinin eksik olduğunu ve bunun 0 sayılmadığını görür.
        failed = sorted({e.get("signal") for e in report.get("evidence") or []
                         if e.get("status") in FAILED_SIGNAL and e.get("signal")})
        miss = ""
        if failed:
            miss = (f" Okunamayan signal ({len(failed)}): {', '.join(failed[:4])}"
                    + (f" (+{len(failed) - 4})" if len(failed) > 4 else "") + ". Okunamayan veri 0 sayılmaz.")
        parts.append("Dynatrace verisi eksik; sonuç KISMİ." + miss
                     + (" " + " ".join(e[:200] for e in errs[:2]) if errs else ""))
    if cov is not None and cov < 0.6:
        parts.append(f"Data coverage düşük (%{cov * 100:.0f}); risk score güvenilir değil veya hesaplanmadı.")
    return ("> **Uyarı:** " + " ".join(parts) + "\n\n") if parts else ""


def _mw_status(e: BaseException) -> str:
    import asyncio
    return "cancelled" if isinstance(e, asyncio.CancelledError) else "failed"


class ChatOrchestrator:
    def __init__(self, rt):
        self.rt = rt
        self.runner = InvestigationRunner(rt)

    # ------------------------------------------------------------ grafik
    def _graph(self, ctx: TurnContext):
        rt = self.rt
        rc = ctx.rc
        cfg = rc.agent.cfg if rc else rt.agent.cfg
        apps = cfg["apps"]
        chat_llm = rt.llm if (rc is None or rc.llm_chat) else None      # Settings: LLM kapalı / profil devre dışı
        rca_llm = rt.llm if (rc is None or rc.llm_rca) else None

        async def route(state: ChatState) -> dict:
            q = state["question"]
            short_term = _mem_flag(rc, "short_term_enabled")
            history = [m for m in state.get("messages", [])[:-1]] if short_term else []
            focus = state.get("focus") if short_term else None
            r: ChatRoute | None = None
            if chat_llm is not None:
                try:
                    r = await chat_llm.route(q, history, apps=apps, app=ctx.application_id,
                                             focus=wm.router_hint(focus))
                except LlmUnavailable as e:
                    from airlab_agent.agent_trace import mark
                    mark("fallback", {"from": "route_question", "to": "heuristic_route", "reason": str(e)[:500]})
                    await ctx.emit("agent_thinking", {"step": "start", "message":
                                                      f"Soru Claude ile yorumlanamadı; kural tabanlı yorum kullanıldı ({e})."})
            r = r or heuristic_route(q, apps, ctx.application_id)
            d = r.model_dump()
            d["focus_apps"] = [a for a in d.get("focus_apps", []) if a in apps]
            if ctx.application_id and ctx.application_id not in d["focus_apps"]:
                d["focus_apps"].insert(0, ctx.application_id)
            if ctx.minutes:                       # arayüzde seçilen zaman aralığı sorudakini ezer
                d.update(minutes=ctx.minutes, start_utc=None, end_utc=None)
            if ctx.start and ctx.end:
                d.update(minutes=None, start_utc=ctx.start.isoformat(), end_utc=ctx.end.isoformat())
            d, inherited = wm.resolve(d, focus, q, explicit_window=bool(ctx.minutes or (ctx.start and ctx.end)))
            if inherited:
                await ctx.emit("agent_thinking", {"step": "start", "message":
                                                  "Önceki turun odağı kullanıldı — " + "; ".join(inherited) + "."})
            if d["intent"] == "followup" and not state.get("last_investigation_id") and \
                    len(state.get("messages", [])) <= 1:
                d["intent"] = "analyze"           # takip edilecek önceki yanıt yok
            await ctx.emit("agent_thinking", {"step": "start", "message": {
                "analyze": "Güncel telemetri analizi yapılacak.", "compare": "Güncel analiz yapılıp geçmişle karşılaştırılacak.",
                "history": "Geçmiş kayıtlar aranacak (yeni telemetry query yok).",
                "followup": "Önceki yanıt üzerinden devam ediliyor.", "smalltalk": "Genel soru."}[d["intent"]]})
            turn = wm.next_turn(state)
            return {"route": d, "investigation": None, "memory": None,
                    "focus": wm.focus_from_route(focus, d, turn)}

        def after_route(state: ChatState) -> str:
            i = state["route"]["intent"]
            return "investigate" if i in ("analyze", "compare") else "recall" if i in ("history", "followup") \
                else "respond"

        async def investigate(state: ChatState) -> dict:
            r = state["route"]
            req = AnalysisRequest(intent="analyze", minutes=r.get("minutes"), start_utc=r.get("start_utc"),
                                  end_utc=r.get("end_utc"), focus_apps=r.get("focus_apps") or [])
            start, end, notes = resolve_window(req, cfg)
            if r.get("focus_apps"):
                notes.append(f"Odak: {', '.join(r['focus_apps'])} (tüm uygulamalar yine analiz edildi; "
                             f"kaynak/etkilenen ayrımı için gerekli).")
            app = r["focus_apps"][0] if len(r.get("focus_apps") or []) == 1 else None
            async with rt.sem:
                async with transaction(rt.sm) as s:
                    inv = await investigations.create(
                        s, run_id=new_run_id("chat"), trigger=Trigger.CHAT, title=state["question"][:300],
                        description=state["question"], window_start=start, window_end=end, user_id=ctx.user_id,
                        conversation_id=ctx.conversation_id, application_id=app)
                    inv_id = inv.id
                ctx.result["investigation_id"] = str(inv_id)
                explicit = bool(r.get("minutes") or r.get("start_utc"))
                inv, report = await self.runner.run(inv_id, emit=ctx.emit, cancel=ctx.cancel,
                                                    question=state["question"], mode="chat", notes=notes, rc=rc,
                                                    window_source="user_question" if explicit else "default")
            summ = summary_of(inv, report)
            if report:
                await rt.memory.remember_investigation(inv, user_id=ctx.user_id, conversation_id=ctx.conversation_id)
            return {"investigation": summ, "last_investigation_id": str(inv.id),
                    "focus": wm.focus_from_investigation(state.get("focus"), summ)}

        async def recall(state: ChatState) -> dict:
            r, inv = state["route"], state.get("investigation") or {}
            await ctx.emit("agent_thinking", {"step": "memory", "message": "Geçmiş araştırma, incident, RCA ve runbook "
                                                                            "kayıtları aranıyor."})
            apps_ = list(dict.fromkeys((inv.get("source_apps") or []) + (inv.get("affected_apps") or [])
                                       + (r.get("focus_apps") or [])))
            mech = inv.get("mechanism") or r.get("mechanism")
            scope = r.get("history_scope") or None
            if r["intent"] in ("analyze", "compare"):
                scope = ["investigations", "incidents", "rca", "runbooks", "known_issues"]
            exclude = uuid.UUID(inv["investigation_id"]) if inv.get("investigation_id") else None
            from ..memory import events as mem_events
            with mem_events.bind(node="recall"):
                mem = await rt.memory.recall(user_id=ctx.user_id, question=state["question"], scope=scope, apps=apps_,
                                             mechanism=mech, terms=r.get("search_terms") if not mech else None,
                                             since_days=r.get("since_days"), exclude_investigation=exclude,
                                             conversation_id=ctx.conversation_id)
            if exclude:
                async with transaction(rt.sm) as s:
                    await investigations.record_tool(s, exclude, "sre-db:recall", ok=True, step="memory",
                                                     arguments={"mechanism": mech, "apps": apps_},
                                                     record_count=sum(mem["counts"].values()))
            await ctx.emit("memory_recalled", {"counts": mem["counts"]})
            return {"memory": mem}

        async def respond(state: ChatState) -> dict:
            r, inv, mem = state["route"], state.get("investigation"), state.get("memory")
            q = state["question"]
            intent = r["intent"]
            system = human = None
            fallback = banner = ""
            report = None
            if intent in ("analyze", "compare"):
                if inv and inv.get("investigation_id"):
                    async with transaction(rt.sm) as s:
                        row = await investigations.get(s, uuid.UUID(inv["investigation_id"]))
                        report = row.report
                banner = data_banner(report)
                if report:
                    system = prompts.ANALYSIS_SYSTEM
                    hist = _inject(rc, "<history_data>", _hist_block(mem), "rca", llm_=rca_llm)
                    from airlab_agent.context_window import note_analysis_payload
                    note_analysis_payload(prompts.analysis_payload(report))
                    human = prompts.analysis_human(q, prompts.analysis_payload(report), hist)
                    fallback = deterministic_explanation(report)
                    if mem:
                        fallback += "\n\n**Geçmişle karşılaştırma.**\n" + _det_history(_hist_block(mem) or {})
                else:
                    fallback = ("Analiz tamamlanamadı: " + ((inv or {}).get("error") or "bilinmeyen hata") +
                                ". Dynatrace verisi alınamadığı için sonuç üretilmedi; sahte veri gösterilmez.")
            elif intent == "history":
                system, human = prompts.HISTORY_SYSTEM, prompts.history_human(
                    q, _inject(rc, "<memory_data>", mem or {}, "chat", llm_=chat_llm) or {})
                fallback = _det_history(mem or {})
            elif intent == "followup":
                prev = next((m["content"] for m in reversed(state.get("messages", [])[:-1])
                             if m["role"] == "assistant"), None)
                short_term = _mem_flag(rc, "short_term_enabled")
                ctx_data = {"previous_answer": (prev or "")[:6000],
                            "last_investigation": (mem or {}).get("last_investigation"),
                            "focus": state.get("focus") if short_term else None,
                            "earlier_turns": (state.get("digests") or [])[-8:] if short_term else []}
                _inject(rc, "<context_data>", {"last_investigation": ctx_data["last_investigation"]}
                        if ctx_data["last_investigation"] else None, "chat", llm_=chat_llm, observe_only=True)
                try:
                    from airlab_agent.context_window import note_context
                    turns = state.get("digests") or []
                    note_context("context_data", status="windowed",
                                 included=[k for k, v in ctx_data.items() if v],
                                 dropped=([{"ref": "previous_answer kısmen", "reason": "6000 karakter sonrası kırpıldı",
                                            "chars": len(prev) - 6000}] if prev and len(prev) > 6000 else []) +
                                 ([{"ref": f"en eski {len(turns) - 8} tur özeti", "reason": "yalnız son 8 tur özeti",
                                    "count": len(turns) - 8}] if short_term and len(turns) > 8 else []),
                                 summarized=[{"ref": f"{min(len(turns), 8)} önceki tur", "reason":
                                              "tur metni yerine özet (digest)"}] if short_term and turns else [],
                                 reason="follow-up: önceki yanıt + odak + tur özetleri")
                except Exception:  # noqa: BLE001
                    log.debug("context window kararı yazılamadı", exc_info=True)
                system, human = prompts.FOLLOWUP_SYSTEM, prompts.followup_human(q, ctx_data)
                fallback = prev or "Önceki bir yanıt bulunamadı."
            else:
                fallback = prompts.SMALLTALK
            answer, source = "", "deterministik"
            llm_failed = False
            if banner:
                await ctx.emit("token", {"text": banner})
            rca = intent in ("analyze", "compare")           # RCA cevabı → RCA modeli/profili
            llm_ = rca_llm if rca else chat_llm
            if system and llm_ is not None:
                await ctx.emit("agent_thinking", {"step": "rca", "message": "Yanıt hazırlanıyor."})
                from airlab_agent.guardrails.stream import StreamGuard
                sg = StreamGuard()          # hassas veri doğrulanmadan token iletilmez (son 256 karakter tutulur)
                try:
                    async for tok in llm_.astream(system, human, rca=rca):
                        if ctx.cancel.is_set():
                            break
                        safe = sg.feed(tok)
                        if safe:
                            answer += safe
                            await ctx.emit("token", {"text": safe})
                    rest = sg.flush()
                    if rest:
                        answer += rest
                        await ctx.emit("token", {"text": rest})
                    source = f"Claude ({getattr(rt.llm, 'rca_model_name', rt.llm.model_name) if rca else rt.llm.model_name})"
                except LlmUnavailable as e:
                    await ctx.emit("agent_thinking", {"step": "rca", "message":
                                                      f"Claude erişilemedi; deterministik özet kullanıldı ({e})."})
                    llm_failed = True
                    if answer:      # yarım kalan LLM metni ekranda kalmaz; deterministik yanıt onun yerine geçer
                        await ctx.emit("answer_replaced", {"text": banner, "reason": "llm_unavailable"})
                    answer = ""
            if answer.strip():
                answer, source = await _guard_answer(ctx, answer, source, report, fallback, banner)
            if not answer.strip():
                why = " (Claude devre dışı)" if llm_ is None else " (Claude erişilemedi)" if llm_failed else ""
                answer, source = fallback, "deterministik" + why
                await ctx.emit("token", {"text": answer})
            answer = banner + answer
            if inv and inv.get("investigation_id"):
                async with transaction(rt.sm) as s:
                    row = await investigations.get(s, uuid.UUID(inv["investigation_id"]))
                    row.explanation, row.explanation_source = answer, source
                    await investigations.record_tool(s, row.id, source.split(" ")[0].lower() + ":answer", ok=True,
                                                     step="rca")
                if intent in ("analyze", "compare"):     # RCA metni kesinleşti → yüksek riskte Slack bildirimi
                    from ..services.slack_notify import notify_rca
                    await notify_rca(rt, uuid.UUID(inv["investigation_id"]))
            sugg = suggestions_for(r, inv, mem)
            turn = wm.next_turn(state)
            return {"answer": answer, "answer_source": source, "suggestions": sugg,
                    "messages": [{"role": "assistant", "content": answer[:8000]}],
                    "digests": [wm.digest(turn, q, r, inv, answer)]}

        g = StateGraph(ChatState)
        g.add_node("route", route)
        g.add_node("investigate", investigate)
        g.add_node("recall", recall)
        g.add_node("respond", respond)
        g.add_edge(START, "route")
        g.add_conditional_edges("route", after_route, ["investigate", "recall", "respond"])
        g.add_edge("investigate", "recall")
        g.add_edge("recall", "respond")
        g.add_edge("respond", END)
        return g.compile(checkpointer=rt.asaver, name="airlab-sre-chat")

    def _config(self, ctx: TurnContext, run_id: uuid.UUID | None = None, *, attempt: int = 1,
                resumed: bool = False) -> dict:
        # Tur = tek LangSmith trace (sınıflandırma, analiz grafı, DQL'ler, hatırlama, cevap, Slack altında).
        # conversation_id metadata'sı LangSmith Threads görünümünde aynı konuşmanın turlarını birleştirir.
        # platform_run_id = turun kullanıcı mesajı kimliği; checkpoint'ten sürdürülen tur aynı kimliği taşır.
        llm = self.rt.llm
        traced = ctx.rc is None or ctx.rc.trace
        env = ((ctx.rc.effective.get("observability") or {}).get("environment_tag") if ctx.rc else None)
        meta = {"conversation_id": str(ctx.conversation_id), "user_id": str(ctx.user_id),
                "thread_id": ctx.thread_id, "platform_run_id": ctx.result.get("user_message_id"),
                "mode": "chat", "trigger": "chat", "environment": env, "attempt": attempt, "resumed": resumed,
                "model": getattr(llm, "model_name", None),
                **({"config_hash": ctx.rc.snapshot["hash"]} if ctx.rc else {})}
        cfg = {"configurable": {"thread_id": ctx.thread_id}, "recursion_limit": 12,
               "callbacks": self.rt.callbacks if traced else [], "run_name": "chat-turn", "tags": ["airlab", "chat"],
               "metadata": {k: v for k, v in meta.items() if v is not None}}
        if run_id is not None:
            cfg["run_id"] = run_id
        return cfg

    async def _link(self, ctx: TurnContext, run_id: uuid.UUID, *, resumed: bool = False) -> None:
        """Turun LangSmith eşlemesi (platform run_id = kullanıcı mesajı kimliği). Trace gönderilmediyse yazılmaz."""
        if not ctx.result.get("user_message_id") or not (ctx.rc is None or ctx.rc.trace):
            return
        from ..services.langsmith_trace import record_link
        await record_link(self.rt, platform_run_id=ctx.result["user_message_id"], kind="chat_turn", run_id=run_id,
                          conversation_id=ctx.conversation_id, thread_id=ctx.thread_id, trigger="chat", mode="chat",
                          resumed=resumed)

    # ------------------------------------------------------------ dış arayüz
    def _mem_bind(self, ctx: TurnContext, tsess, rid):
        from ..memory import events as mem_events
        return mem_events.bind(run_id=tsess.run_id if tsess is not None else f"chat-{ctx.result.get('user_message_id')}",
                               thread_id=ctx.thread_id, trace_id=str(rid) if rid else None,
                               correlation_id=ctx.result.get("user_message_id"), user_id=str(ctx.user_id),
                               cfg=_mem_cfg(ctx.rc))

    async def turn(self, ctx: TurnContext, question: str) -> dict:
        question = question.strip()
        if not question:
            raise UserError("soru boş")
        async with transaction(self.rt.sm) as s:
            m = await conversations.add_message(s, ctx.conversation_id, MessageRole.USER, question)
            ctx.result["user_message_id"] = str(m.id)
        await self._prepare(ctx)
        graph = self._graph(ctx)
        rid = uuid.UUID(ctx.result["user_message_id"])                 # trace id = kullanıcı mesajının id'si
        tsess = await self._trace_start(ctx, attempt=1)
        msess = await self._mw_start(ctx, tsess, rid)
        err, status = None, "completed"
        gsess, blocked = await self._gr_start(ctx, tsess, rid, question)
        if blocked is not None:                 # before_agent guardrail: tur LLM / MCP çalışmadan yanıtlanır
            await ctx.emit("token", {"text": blocked})
            for sess in (tsess, msess):
                if sess is not None:
                    await sess.finish("failed", blocked)
            return await self._finish(ctx, {"route": {"intent": "guardrail_blocked"}, "answer": blocked,
                                            "answer_source": "guardrail", "suggestions": []})
        try:
            with self._mem_bind(ctx, tsess, rid):
                state = await self._invoke(ctx, graph, {"question": question,
                                                        "messages": [{"role": "user", "content": question}]},
                                           self._config(ctx, run_id=rid), tsess)
        except BaseException as e:
            err = f"{type(e).__name__}: {e}"
            status = _mw_status(e)
            raise
        finally:
            if gsess is not None:
                try:
                    gsess.finish(status)
                except Exception:  # noqa: BLE001
                    log.exception("guardrail run kapanışı başarısız")
            await self._link(ctx, rid)
            if tsess is not None:
                await tsess.finish("failed" if err else "completed", err)
            if msess is not None:
                await msess.finish(status, err)
        return await self._finish(ctx, state)

    async def _mw_start(self, ctx: TurnContext, tsess, rid):
        """Agent Middleware run bağlamı (snapshot + before_agent). Başlatılamazsa tur kod varsayılanıyla sürer."""
        from ..middleware import runtime as mw_rt
        try:
            return await mw_rt.start(self.rt, ctx.rc, kind="chat_turn", graph="chat-turn", thread_id=ctx.thread_id,
                                     run_id=tsess.run_id if tsess is not None else
                                     f"chat-{ctx.result.get('user_message_id') or rid}",
                                     trace_id=str(rid), user_id=str(ctx.user_id))
        except Exception:  # noqa: BLE001
            log.exception("middleware run bağlamı başlatılamadı")
            return None

    async def _gr_start(self, ctx: TurnContext, tsess, rid, question: str):
        """Guardrail run bağlamı + before_agent (girdi). Engellenirse (None, kullanıcıya gösterilecek mesaj)."""
        from airlab_agent.guardrails.model import GuardrailBlocked
        from ..guardrails import runtime as g_rt
        try:
            env = ((getattr(ctx.rc, "effective", None) or {}).get("observability") or {}).get("environment_tag")
            return await g_rt.start(self.rt, kind="chat_turn", run_id=tsess.run_id if tsess is not None else
                                    f"chat-{ctx.result.get('user_message_id') or rid}", thread_id=ctx.thread_id,
                                    trace_id=str(rid), user_id=ctx.user_id, input_text=question,
                                    environment=env), None
        except GuardrailBlocked as e:
            return None, ("Bu istek bir güvenlik policy'si nedeniyle işlenmedi: " + g_rt.blocked_message(e) +
                          "\n\nAyrıntı: Guardrails › Evaluation Logs.")
        except Exception:  # noqa: BLE001 — bağlam kurulamadı: hook'lar süreç snapshot'ıyla (kayıtsız) çalışır
            log.exception("guardrail run bağlamı başlatılamadı")
            return None, None

    async def _trace_start(self, ctx: TurnContext, *, attempt: int):
        """Agent Trace run'ı: platform run_id = turun kullanıcı mesajı; sürdürülen tur yeni girişim."""
        from ..services import agent_trace as atrace
        mid = ctx.result.get("user_message_id")
        if not mid:
            return None
        base = f"chat-{mid}"
        if attempt > 1:
            attempt = max(attempt, await atrace.attempt_for(self.rt.sm, base))
        return await atrace.start(self.rt, run_id=base if attempt == 1 else f"{base}-a{attempt}", kind="chat_turn",
                                  graph="chat-turn", trigger="chat", conversation_id=ctx.conversation_id,
                                  user_id=ctx.user_id, thread_id=ctx.thread_id, platform_run_id=mid, attempt=attempt)

    async def _prepare(self, ctx: TurnContext) -> None:
        """Tur başı: etkili ayarlardan değişmez yapılandırma; LLM profilleri ve arayüzde seçilen model uygulanır."""
        from ..settings import runtime as srt
        ctx.rc = await srt.run_config(self.rt, trigger="chat")
        srt.apply_llm(self.rt, ctx.rc)

    @staticmethod
    async def _invoke(ctx: TurnContext, graph, inp, config, tsess=None):
        if ctx.rc is None or ctx.rc.trace:
            return await ChatOrchestrator._run_graph(graph, inp, config, tsess)
        from airlab_agent import tracing                # sampling dışı tur: alt LLM çağrıları da trace göndermez
        token = tracing._TRACING_OFF.set(True)
        try:
            return await ChatOrchestrator._run_graph(graph, inp, config, tsess)
        finally:
            tracing._TRACING_OFF.reset(token)

    @staticmethod
    async def _run_graph(graph, inp, config, tsess):
        """Agent Trace yoksa mevcut ainvoke; varsa updates + debug stream'i kaydedilir, son state checkpoint'ten."""
        if tsess is None:
            return await graph.ainvoke(inp, config)
        from airlab_agent.agent_trace import ACTIVE, STREAM_MODES, with_callback
        rec = tsess.recorder
        config = with_callback(dict(config), rec)
        token = ACTIVE.set(rec)
        try:
            async for mode, chunk in graph.astream(inp, config, stream_mode=STREAM_MODES):
                rec.on_stream(mode, chunk)
        finally:
            ACTIVE.reset(token)
        return (await graph.aget_state(config)).values

    async def resume(self, ctx: TurnContext) -> dict | None:
        """Yarıda kalmış (ör. süreç yeniden başlatıldı) turu son checkpoint'ten sürdürür."""
        await self._prepare(ctx)
        graph = self._graph(ctx)
        snap = await graph.aget_state(self._config(ctx))
        if not snap.next:
            return None
        # Sürdürülen tur yeni bir trace'tir; aynı platform run_id'ye (yarıda kalan turun kullanıcı mesajı) yeni
        # girişim olarak bağlanır, önceki girişimin eşlemesi korunur.
        from ..services.langsmith_trace import previous_attempts
        async with transaction(self.rt.sm) as s:
            last_user = next((m for m in reversed(await conversations.recent_messages(s, ctx.conversation_id, 20))
                              if m.role == MessageRole.USER), None)
            if last_user is not None:
                ctx.result.setdefault("user_message_id", str(last_user.id))
            attempt = await previous_attempts(s, ctx.result["user_message_id"]) + 1 \
                if ctx.result.get("user_message_id") else 1
        rid = uuid.uuid4()
        tsess = await self._trace_start(ctx, attempt=max(2, attempt))
        msess = await self._mw_start(ctx, tsess, rid)
        err, status = None, "completed"
        try:
            with self._mem_bind(ctx, tsess, rid):
                state = await self._invoke(ctx, graph, None,
                                           self._config(ctx, run_id=rid, attempt=attempt, resumed=True), tsess)
        except BaseException as e:
            err = f"{type(e).__name__}: {e}"
            status = _mw_status(e)
            raise
        finally:
            await self._link(ctx, rid, resumed=True)
            if tsess is not None:
                await tsess.finish("failed" if err else "completed", err)
            if msess is not None:
                await msess.finish(status, err)
        return await self._finish(ctx, state)

    async def pending(self, thread_id: str) -> list[str]:
        ctx = TurnContext(user_id=uuid.uuid4(), conversation_id=uuid.uuid4(), thread_id=thread_id, emit=_noop)
        snap = await self._graph(ctx).aget_state(self._config(ctx))
        return list(snap.next or [])

    async def working_memory(self, thread_id: str) -> dict:
        ctx = TurnContext(user_id=uuid.uuid4(), conversation_id=uuid.uuid4(), thread_id=thread_id, emit=_noop)
        snap = await self._graph(ctx).aget_state(self._config(ctx))
        return {**wm.public_view(snap.values or {}, MAX_STATE_MESSAGES), "pending": list(snap.next or [])}

    async def clear_focus(self, thread_id: str) -> bool:
        """Konuşma odağını siler (sonraki tur odak devralmaz). Yarım kalan tur varsa dokunulmaz → False."""
        ctx = TurnContext(user_id=uuid.uuid4(), conversation_id=uuid.uuid4(), thread_id=thread_id, emit=_noop)
        graph = self._graph(ctx)
        snap = await graph.aget_state(self._config(ctx))
        if snap.next:
            return False
        if (snap.values or {}).get("focus"):
            await graph.aupdate_state(self._config(ctx), {"focus": None}, as_node="respond")
        return True

    async def _finish(self, ctx: TurnContext, state: dict) -> dict:
        inv = state.get("investigation")
        meta = {"intent": state["route"]["intent"], "route": state["route"], "answer_source": state.get("answer_source"),
                "suggestions": state.get("suggestions", []), "investigation": inv,
                "memory_counts": (state.get("memory") or {}).get("counts")}
        # Bu turun kimligi ve tur basinda dondurulan model secimi (UI: "hangi model, neden" + run baglantilari)
        mid_user = ctx.result.get("user_message_id")
        if mid_user:
            meta["run_id"] = f"chat-{mid_user}"
        if ctx.rc is not None:
            meta["llm"] = {k: v for k, v in ctx.rc.llm_effective.items() if k == "chat" or inv}
        async with transaction(self.rt.sm) as s:
            m = await conversations.add_message(
                s, ctx.conversation_id, MessageRole.ASSISTANT, state.get("answer") or "", meta,
                investigation_id=uuid.UUID(inv["investigation_id"]) if inv and inv.get("investigation_id") else None)
            mid = str(m.id)
        from ..security import llm_usage
        await llm_usage.persist(self.rt.sm, run_id=ctx.result.get("user_message_id") or ctx.thread_id, trigger="chat",
                                conversation_id=ctx.conversation_id,
                                investigation_id=uuid.UUID(inv["investigation_id"]) if inv and inv.get("investigation_id")
                                else None)
        await ctx.emit("suggestions", {"items": state.get("suggestions", [])})
        await ctx.emit("message_saved", {"message_id": mid, "conversation_id": str(ctx.conversation_id)})
        if inv:
            await ctx.emit("investigation_completed", inv)
        ctx.result.update(message_id=mid, answer=state.get("answer"), intent=state["route"]["intent"],
                          investigation=inv, suggestions=state.get("suggestions", []),
                          answer_source=state.get("answer_source"))
        return ctx.result


async def _guard_answer(ctx, answer: str, source: str, report: dict | None, fallback: str,
                        banner: str) -> tuple[str, str]:
    """Before final output: LLM yanıtı guardrail'lerden geçer (evidence / risk tutarlılığı, hassas veri, schema).
    Düzeltilirse istemciye son metin `answer_replaced` olayıyla gönderilir; engellenirse deterministik yanıt."""
    from airlab_agent.graph import guard_facts
    from airlab_agent.guardrails import runtime as gr
    from airlab_agent.guardrails.model import GuardrailBlocked
    try:
        out = gr.final_output(answer, guard_facts(report) if report else {}, node="respond")
    except GuardrailBlocked as e:
        text = fallback or ("Yanıt bir güvenlik policy'si nedeniyle gösterilmedi: " + str(e)[:300])
        await ctx.emit("answer_replaced", {"text": banner + text, "reason": "guardrail"})
        return text, "deterministik (guardrail)"
    except Exception:  # noqa: BLE001 — değerlendirme hatası engine içinde fail-open/closed'a göre işlenir
        log.exception("final output guardrail değerlendirmesi başarısız")
        return answer, source
    text = out.payload.get("text", answer) if isinstance(out.payload, dict) else answer
    if text != answer:
        await ctx.emit("answer_replaced", {"text": banner + text, "reason": "guardrail"})
    return text, source


async def _noop(*_a, **_k):
    return None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def minutes_window(minutes: int) -> tuple[datetime, datetime]:
    end = utcnow()
    return end - timedelta(minutes=minutes), end
