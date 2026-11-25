"""Konuşmalar ve ajan çalıştırmaları (SSE)."""
from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from airlab_agent.graph import new_run_id
from airlab_agent.llm import AnalysisRequest, resolve_window

from ...agent.chat import ChatOrchestrator, TurnContext
from ...agent.investigation import InvestigationRunner
from ...db.models import Role, Trigger, User
from ...repositories import applications, conversations, incidents, investigations
from ...security import audit, sessions as sec_sessions, throttle
from ...services.runs import Run
from ...settings import runtime as settings_runtime
from ...settings import service as settings_service
from ...security.permissions import P
from ..deps import current_user, db, keyed
from ..deps import require_perm as perm
from ..schemas import (ChatIn, ConversationIn, ConversationOut, ConversationPatch, InvestigateIn, MessageOut, PageOut,
                       RunOut, page_of)

router = APIRouter(tags=["agent"])
MAX_RUNS_PER_USER = 2


# ---------------------------------------------------------------- konuşmalar
@router.post("/conversations", response_model=ConversationOut, status_code=201)
async def create_conversation(body: ConversationIn, u: User = Depends(current_user), s: AsyncSession = Depends(db)):
    if body.application_id:
        await applications.get(s, body.application_id)
    return await conversations.create(s, u.id, body.title, body.application_id)


@router.get("/conversations", response_model=PageOut[ConversationOut])
async def list_conversations(q: str | None = Query(None, max_length=200), archived: bool = False, page: int = 1,
                             size: int = 30, sort: str | None = None, u: User = Depends(current_user),
                             s: AsyncSession = Depends(db)):
    return page_of(await conversations.list_(s, u.id, q=q, archived=archived, page=page, size=size, sort=sort),
                   ConversationOut)


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: uuid.UUID, request: Request, u: User = Depends(current_user),
                           s: AsyncSession = Depends(db)):
    c = await conversations.get(s, conv_id, u.id)
    active = request.app.state.runs.active_for_conversation(c.id)
    pending = await ChatOrchestrator(request.app.state.rt).pending(c.thread_id) if not active else []
    return {**ConversationOut.model_validate(c).model_dump(mode="json"),
            "active_run_id": active.id if active else None, "resumable": bool(pending), "pending_steps": pending}


@router.patch("/conversations/{conv_id}", response_model=ConversationOut)
async def patch_conversation(conv_id: uuid.UUID, body: ConversationPatch, u: User = Depends(current_user),
                             s: AsyncSession = Depends(db)):
    return await conversations.rename(s, conv_id, u.id, body.title, body.archived)


@router.get("/conversations/{conv_id}/messages")
async def get_messages(conv_id: uuid.UUID, page: int = 1, size: int = 200, u: User = Depends(current_user),
                       s: AsyncSession = Depends(db)):
    await conversations.get(s, conv_id, u.id)
    p = await conversations.messages(s, conv_id, page=page, size=size)
    return {"items": [MessageOut.model_validate(m).model_dump(mode="json", by_alias=True) for m in p.items],
            "total": p.total, "page": p.page, "size": p.size, "pages": p.pages}


@router.get("/conversations/{conv_id}/memory")
async def get_working_memory(conv_id: uuid.UUID, request: Request, u: User = Depends(current_user),
                             s: AsyncSession = Depends(db)):
    """Working memory görünümü: konuşma odağı, tur özetleri, state'teki mesaj sayısı (yalnız konuşma sahibi)."""
    c = await conversations.get(s, conv_id, u.id)
    return await ChatOrchestrator(request.app.state.rt).working_memory(c.thread_id)


@router.delete("/conversations/{conv_id}/memory/focus", status_code=204)
async def clear_focus(conv_id: uuid.UUID, request: Request, u: User = Depends(current_user),
                      s: AsyncSession = Depends(db)):
    """Konuşma odağını siler; sonraki soru önceki uygulama/pencereyi devralmaz. Mesajlar ve özetler kalır."""
    c = await conversations.get(s, conv_id, u.id)
    busy = request.app.state.runs.active_for_conversation(c.id)
    if busy or not await ChatOrchestrator(request.app.state.rt).clear_focus(c.thread_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "konuşmada süren ya da yarım kalan bir tur var")
    await audit.record(s, actor=u.username, action="memory.focus.clear", section="memory", target=str(c.id))


# ---------------------------------------------------------------- çalıştırma başlatma
def _check_limits(request: Request, u: User, conv_id: uuid.UUID | None = None):
    runs = request.app.state.runs
    if conv_id and runs.active_for_conversation(conv_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "bu konuşmada süren bir çalıştırma var")
    if runs.active_count(u.id) >= MAX_RUNS_PER_USER:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"aynı anda en fazla {MAX_RUNS_PER_USER} çalıştırma başlatabilirsiniz")


async def _admit(request: Request, u: User, s: AsyncSession, kind: str) -> None:
    """Kalıcı hız sınırı: kullanıcının son 60 sn'deki run.start audit olayları (restart'tan etkilenmez)."""
    limit = request.app.state.rt.ps.run_rate_per_minute
    if limit > 0 and await throttle.runs_last_minute(s, u.username) >= limit:
        await audit.record_separate(request.app.state.rt.sm, actor=u.username, action="run.denied", section="agent",
                                    target=kind, result="denied", reason_code="rate_limited")
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"dakikada en fazla {limit} çalıştırma başlatılabilir",
                            headers={"Retry-After": "60"})


async def _started(request: Request, u: User, run: Run, kind: str, target: str | None, decision=None) -> None:
    await audit.record_separate(request.app.state.rt.sm, actor=u.username, action="run.start", section="agent",
                                target=f"{kind}:{target}" if target else kind, run_id=run.id,
                                after={"policy_version": getattr(decision, "policy_version", None)})


def _run_out(run: Run, **kw) -> RunOut:
    return RunOut(run_id=run.id, kind=run.kind, stream_url=f"/api/agent/stream/{run.id}", **kw)


@router.post("/agent/chat", response_model=RunOut, status_code=202)
async def chat(body: ChatIn, request: Request, u: User = Depends(perm(P.AGENT_RUN)), s: AsyncSession = Depends(db)):
    decision = settings_runtime.check_allowed(await settings_service.effective(request.app.state.rt, s), "manual")
    await _admit(request, u, s, "chat")
    if body.application_id:
        await applications.get(s, body.application_id)
    if body.conversation_id:
        conv = await conversations.get(s, body.conversation_id, u.id)
    else:
        conv = await conversations.create(s, u.id, body.message[:80], body.application_id)
    _check_limits(request, u, conv.id)
    await s.commit()                                           # konuşma, çalıştırma başlamadan görünür olsun
    rt = request.app.state.rt
    tr = body.time_range
    conv_id, thread_id, uid = conv.id, conv.thread_id, u.id

    async def go(run: Run):
        ctx = TurnContext(user_id=uid, conversation_id=conv_id, thread_id=thread_id, emit=run.emit,
                          application_id=body.application_id, cancel=run.cancel,
                          minutes=tr.minutes if tr else None, start=tr.start if tr else None,
                          end=tr.end if tr else None)
        await ChatOrchestrator(rt).turn(ctx, body.message)

    run = request.app.state.runs.start("chat", u.id, go, conversation_id=conv_id)
    await _started(request, u, run, "chat", str(conv_id), decision)
    return _run_out(run, conversation_id=conv_id)


@router.post("/conversations/{conv_id}/resume", response_model=RunOut, status_code=202)
async def resume(conv_id: uuid.UUID, request: Request, u: User = Depends(perm(P.AGENT_RUN)), s: AsyncSession = Depends(db)):
    conv = await conversations.get(s, conv_id, u.id)
    # P0.3: resume de yeni bir LLM/MCP çalıştırmasıdır → chat ile aynı policy kapısı (agent devre dışı / mod)
    decision = settings_runtime.check_allowed(await settings_service.effective(request.app.state.rt, s), "manual")
    await _admit(request, u, s, "resume")
    _check_limits(request, u, conv.id)
    rt = request.app.state.rt
    if not await ChatOrchestrator(rt).pending(conv.thread_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "sürdürülecek yarım kalmış bir tur yok")
    thread_id, uid = conv.thread_id, u.id

    async def go(run: Run):
        ctx = TurnContext(user_id=uid, conversation_id=conv_id, thread_id=thread_id, emit=run.emit, cancel=run.cancel)
        await ChatOrchestrator(rt).resume(ctx)

    run = request.app.state.runs.start("chat", u.id, go, conversation_id=conv_id)
    await _started(request, u, run, "resume", str(conv_id), decision)
    return _run_out(run, conversation_id=conv_id)


@router.post("/agent/investigate", response_model=RunOut, status_code=202)
async def investigate(body: InvestigateIn, request: Request, u: User = Depends(perm(P.AGENT_RUN)), s: AsyncSession = Depends(db)):
    _check_limits(request, u)
    rt = request.app.state.rt
    decision = settings_runtime.check_allowed(await settings_service.effective(rt, s), "manual")
    await _admit(request, u, s, "investigate")
    if body.application_id:
        await applications.get(s, body.application_id)
    if body.incident_id:
        await incidents.get(s, body.incident_id)
    tr = body.time_range
    req = AnalysisRequest(intent="analyze", minutes=tr.minutes, start_utc=tr.start.isoformat() if tr.start else None,
                          end_utc=tr.end.isoformat() if tr.end else None)
    start, end, notes = resolve_window(req, rt.agent.cfg)
    baseline = None
    if body.baseline:
        b = body.baseline
        if b.minutes:
            baseline = (start - timedelta(minutes=b.minutes), start)
        elif b.start and b.end:
            baseline = (b.start, b.end)
    title = body.title or f"Manuel araştırma {start:%Y-%m-%d %H:%M}–{end:%H:%M} UTC" + \
        (f" ({body.application_id})" if body.application_id else "")
    inv = await investigations.create(s, run_id=new_run_id("man"), trigger=Trigger.MANUAL, title=title,
                                      window_start=start, window_end=end, user_id=u.id,
                                      application_id=body.application_id, baseline=baseline)
    if body.incident_id:
        await incidents.link_investigation(s, body.incident_id, inv.id, user_id=u.id)
    await s.commit()
    inv_id, uid = inv.id, u.id
    core_llm = rt.llm.llm if (body.use_llm and rt.llm is not None and hasattr(rt.llm, "llm")) else None

    async def go(run: Run):
        async with rt.sem:
            done, report = await InvestigationRunner(rt).run(inv_id, emit=run.emit, cancel=run.cancel, mode="manual",
                                                             core_llm=core_llm, notes=notes)
        if report:
            await rt.memory.remember_investigation(done, user_id=uid, conversation_id=None)
        from ...agent.investigation import summary_of
        await run.emit("investigation_completed", summary_of(done, report))

    run = request.app.state.runs.start("investigate", u.id, go)
    run.investigation_id = str(inv_id)
    await _started(request, u, run, "investigate", str(inv_id), decision)
    return _run_out(run, investigation_id=inv_id)


# ---------------------------------------------------------------- akış / iptal
def _own_run(request: Request, run_id: str, u: User) -> Run:
    run = request.app.state.runs.get(run_id)
    if run is None or (run.user_id != u.id and u.role != Role.ADMIN):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "çalıştırma bulunamadı (bitmiş çalıştırmalar 15 dk sonra "
                                                       "bellekten atılır; sonuçlar kayıtlarda)")
    return run


@router.get("/agent/stream/{run_id}")
async def stream(run_id: str, request: Request, after: int = Query(0, ge=0),
                 last_event_id: str | None = Header(None, alias="Last-Event-ID"), u: User = Depends(keyed(P.AGENT_RUN))):
    run = _own_run(request, run_id, u)
    start = int(last_event_id) if last_event_id and last_event_id.isdigit() else after
    registry = request.app.state.runs
    import json

    sm, sid = request.app.state.rt.sm, getattr(request.state, "session_id", None)

    async def gen():
        async for e in registry.stream(run, start):
            if await request.is_disconnected():
                return                                         # istemci gitti; çalıştırma sürer
            if e is None:
                # Zero-trust: uzun süren akışta oturum her keepalive'da yeniden doğrulanır (logout / iptal → akış kapanır)
                if not await sec_sessions.is_active(sm, sid):
                    yield {"event": "session_revoked", "data": json.dumps({"type": "session_revoked"})}
                    return
                yield {"comment": "keepalive"}
                continue
            yield {"id": str(e["seq"]), "event": e["type"], "data": json.dumps(e, ensure_ascii=False, default=str)}

    return EventSourceResponse(gen(), ping=20, headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/agent/runs/{run_id}")
async def run_status(run_id: str, request: Request, u: User = Depends(keyed(P.AGENT_RUN))):
    return _own_run(request, run_id, u).status()


@router.get("/agent/runs")
async def my_runs(request: Request, u: User = Depends(keyed(P.AGENT_RUN))):
    return [r.status() for r in request.app.state.runs.runs.values() if r.user_id == u.id and not r.done]


@router.post("/agent/runs/{run_id}/cancel")
async def cancel(run_id: str, request: Request, u: User = Depends(keyed(P.AGENT_RUN))):
    run = _own_run(request, run_id, u)
    if run.done:
        raise HTTPException(status.HTTP_409_CONFLICT, "çalıştırma zaten bitti")
    run.cancel.set()
    await audit.record_separate(request.app.state.rt.sm, actor=u.username, action="run.cancel", section="agent",
                                target=run.kind, run_id=run.id)
    await run.emit("agent_thinking", {"step": "start", "message": "İptal istendi; mevcut adım bitince durdurulacak."})
    return run.status()

