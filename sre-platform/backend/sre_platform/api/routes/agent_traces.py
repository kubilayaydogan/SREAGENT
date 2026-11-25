"""Agent Trace: graph çalıştırmalarının node / state / stream / LLM / Pydantic olayları.

Yetki: `observability:read`. Sohbet turu (chat_turn) trace'leri konuşma gibi yalnız sahibine ve admin'e açıktır;
analiz (investigation) trace'leri investigation'lar gibi ekip geneli görünür. Yetkisiz kayıt 404 döner (varlığı
sızdırılmaz). Tüm payload'lar kayıt anında maskelenmiştir; API ham veri üretmez (diff de maskeli kopyalardan).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import String, case, cast, false, func, literal, null, or_, select, true, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from airlab_agent.agent_trace import state_diff

from ...db.models import AgentTraceEvent, AgentTraceRun, Investigation, Role, User
from ...db.session import transaction
from ...security import sessions as sec_sessions
from ...security.permissions import P
from ...services import agent_trace as svc
from ..deps import db
from ..deps import require_perm as perm

router = APIRouter(tags=["agent-trace"])

CATEGORY = {"node": ("node_start", "node_end", "state_out", "transition", "routing"),
            "llm": ("llm_request", "llm_provider_request", "llm_response"),
            "pydantic": ("parser_input", "parser_output", "validation_error"),
            "stream": ("stream",), "run": ("run_start", "run_end", "fallback")}


def _visible(u: User):
    """chat_turn: yalnız sahibi (admin hepsini görür)."""
    if u.role == Role.ADMIN:
        return true()
    return or_(AgentTraceRun.kind != "chat_turn", AgentTraceRun.user_id == u.id)


async def _run(s: AsyncSession, u: User, key: str) -> AgentTraceRun:
    q = select(AgentTraceRun).where(_visible(u))
    try:
        q = q.where(AgentTraceRun.id == uuid.UUID(key))
    except ValueError:
        q = q.where(AgentTraceRun.run_id == key)
    row = (await s.execute(q)).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "trace bulunamadı")
    return row


def _run_out(r: AgentTraceRun) -> dict:
    st = svc.effective_status(r)
    return {"trace_id": str(r.id), "run_id": r.run_id, "kind": r.kind, "graph": r.graph, "trigger": r.trigger,
            "status": st, "recorded": True, "investigation_id": str(r.investigation_id) if r.investigation_id else None,
            "conversation_id": str(r.conversation_id) if r.conversation_id else None, "thread_id": r.thread_id,
            "parent_run_id": r.parent_run_id, "platform_run_id": r.platform_run_id, "attempt": r.attempt,
            "started_at": r.started_at, "finished_at": r.finished_at, "duration_ms": r.duration_ms,
            "event_count": r.event_count, "truncated_count": r.truncated_count, "capture_errors": r.capture_errors,
            "lost_events": r.lost_events, "error": r.error,
            "capture": ("failed" if r.lost_events and r.lost_events >= max(1, r.event_count) else
                        "partial" if (r.lost_events or r.capture_errors or st == "interrupted") else
                        "truncated" if r.truncated_count else "complete")}


# ---------------------------------------------------------------- liste
@router.get("/agent-traces")
async def list_traces(kind: str | None = Query(None, pattern="^(analysis|chat_turn)$"),
                      status: str | None = Query(None, max_length=16), trigger: str | None = Query(None, max_length=16),
                      q: str | None = Query(None, max_length=160), recorded: bool | None = None,
                      page: int = 1, size: int = 50,
                      u: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)) -> dict:
    """Kayıtlı trace run'ları + trace'i olmayan (eski) investigation'lar (`recorded: false`)."""
    t = select(cast(AgentTraceRun.id, String).label("trace_id"), AgentTraceRun.run_id, AgentTraceRun.kind,
               AgentTraceRun.trigger, AgentTraceRun.status, AgentTraceRun.started_at, AgentTraceRun.duration_ms,
               AgentTraceRun.event_count, cast(AgentTraceRun.investigation_id, String).label("investigation_id"),
               AgentTraceRun.thread_id, AgentTraceRun.parent_run_id, true().label("recorded"),
               (AgentTraceRun.lost_events + AgentTraceRun.capture_errors).label("problems"),
               AgentTraceRun.truncated_count).where(_visible(u))
    legacy = select(null().cast(String).label("trace_id"), Investigation.run_id, literal("analysis").label("kind"),
                    cast(Investigation.trigger, String).label("trigger"),
                    func.lower(cast(Investigation.status, String)).label("status"),
                    Investigation.created_at.label("started_at"), Investigation.duration_ms,
                    literal(0).label("event_count"), cast(Investigation.id, String).label("investigation_id"),
                    Investigation.run_id.label("thread_id"), null().cast(String).label("parent_run_id"),
                    false().label("recorded"), literal(0).label("problems"), literal(0).label("truncated_count")
                    ).where(~select(AgentTraceRun.id).where(AgentTraceRun.investigation_id == Investigation.id)
                            .exists())
    u_all = union_all(t, legacy).subquery()
    stmt = select(u_all)
    if kind:
        stmt = stmt.where(u_all.c.kind == kind)
    if status:
        stmt = stmt.where(u_all.c.status == status.lower())
    if trigger:
        stmt = stmt.where(func.lower(u_all.c.trigger) == trigger.lower())
    if q:
        stmt = stmt.where(u_all.c.run_id.ilike(f"%{q}%"))
    if recorded is not None:
        stmt = stmt.where(u_all.c.recorded == recorded)
    page, size = max(1, page), max(1, min(size, 200))
    total = (await s.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await s.execute(stmt.order_by(u_all.c.started_at.desc(), u_all.c.run_id.desc())
                            .limit(size).offset((page - 1) * size))).mappings().all()
    now = datetime.now(timezone.utc)
    items = []
    for r in rows:
        d = dict(r)
        if d["recorded"] and d["status"] == "running" and \
                (now - d["started_at"]).total_seconds() > svc.STALE_AFTER_S:
            d["status"] = "interrupted"
        items.append(d)
    return {"items": items, "total": total, "page": page, "size": size, "pages": (total + size - 1) // size}


# ---------------------------------------------------------------- run detayı
def _summary_col():
    p = AgentTraceEvent.payload
    errs = case((func.jsonb_typeof(p["errors"]) == "array", func.jsonb_array_length(p["errors"])), else_=null())
    return func.jsonb_strip_nulls(func.jsonb_build_object(
        "task", p["task"], "model", p["model"], "from", p["from"], "to", p["to"], "function", p["function"],
        "result", p["result"], "routing", p["routing"], "error_type", p["error_type"], "errors", errs,
        "mode", p["mode"], "type", p["type"], "parser", p["parser"], "finish_reason", p["finish_reason"],
        "source", p["source"], "combined", p["combined"], "node_error", p["error"]["error_type"],
        "nodes", p["nodes"], "capture_failed", p["_capture_failed"], "streamed", p["streamed"],
        "provider_response_status", p["provider_response_status"]))


def _meta_cols():
    e = AgentTraceEvent
    return (e.id, e.sequence, e.ts, e.event_type, e.node, e.node_exec_id, e.step, e.attempt, e.call_id,
            e.related_call_id, e.status, e.duration_ms, e.truncated, e.size_bytes, _summary_col().label("summary"))


def _meta(r) -> dict:
    d = dict(r._mapping)
    d["event_id"] = str(d.pop("id"))
    return d


@router.get("/agent-traces/{key}")
async def trace_detail(key: str, request: Request, u: User = Depends(perm(P.OBSERVABILITY_READ)),
                       s: AsyncSession = Depends(db)) -> dict:
    """Run + olay listesi (sıra numarasına göre, payload'sız). Trace'i olmayan investigation run_id → recorded=false."""
    try:
        run = await _run(s, u, key)
    except HTTPException:
        inv = (await s.execute(select(Investigation).where(Investigation.run_id == key))).scalar_one_or_none()
        if inv is None:
            raise
        return {"run": {"trace_id": None, "run_id": inv.run_id, "kind": "analysis", "recorded": False,
                        "trigger": getattr(inv.trigger, "value", str(inv.trigger)),
                        "status": getattr(inv.status, "value", str(inv.status)).lower(),
                        "investigation_id": str(inv.id), "thread_id": inv.run_id, "started_at": inv.created_at,
                        "duration_ms": inv.duration_ms, "capture": "none"},
                "events": [], "children": [], "parent": None, "langsmith_url": None}
    evs = (await s.execute(select(*_meta_cols()).where(AgentTraceEvent.trace_run_id == run.id)
                           .order_by(AgentTraceEvent.sequence))).all()
    kids = (await s.execute(select(AgentTraceRun).where(AgentTraceRun.parent_run_id == run.run_id, _visible(u))
                            .order_by(AgentTraceRun.started_at))).scalars().all()
    parent = None
    if run.parent_run_id:
        parent = (await s.execute(select(AgentTraceRun).where(AgentTraceRun.run_id == run.parent_run_id,
                                                               _visible(u)))).scalar_one_or_none()
    url = None
    if run.investigation_id:
        try:
            from ...services import langsmith_trace
            url = (await asyncio.wait_for(langsmith_trace.trace_info(request.app.state.rt, s, run.investigation_id),
                                          5))["url"]
        except Exception:  # noqa: BLE001
            url = None
    return {"run": _run_out(run), "events": [_meta(r) for r in evs],
            "children": [_run_out(k) for k in kids], "parent": _run_out(parent) if parent else None,
            "langsmith_url": url}


@router.get("/agent-traces/{key}/events/{event_id}")
async def event_detail(key: str, event_id: uuid.UUID, u: User = Depends(perm(P.OBSERVABILITY_READ)),
                       s: AsyncSession = Depends(db)) -> dict:
    run = await _run(s, u, key)
    e = (await s.execute(select(AgentTraceEvent).where(AgentTraceEvent.trace_run_id == run.id,
                                                       AgentTraceEvent.id == event_id))).scalar_one_or_none()
    if e is None:
        raise HTTPException(404, "olay bulunamadı")
    return _full(e)


def _full(e: AgentTraceEvent) -> dict:
    return {"event_id": str(e.id), "sequence": e.sequence, "ts": e.ts, "event_type": e.event_type, "node": e.node,
            "node_exec_id": e.node_exec_id, "step": e.step, "attempt": e.attempt, "call_id": e.call_id,
            "related_call_id": e.related_call_id, "status": e.status, "duration_ms": e.duration_ms,
            "truncated": e.truncated, "size_bytes": e.size_bytes, "payload": e.payload}


async def _events(s, run_id, *conds) -> list[AgentTraceEvent]:
    return list((await s.execute(select(AgentTraceEvent).where(AgentTraceEvent.trace_run_id == run_id, *conds)
                                 .order_by(AgentTraceEvent.sequence))).scalars())


@router.get("/agent-traces/{key}/node-execs/{exec_id}")
async def node_exec(key: str, exec_id: str, u: User = Depends(perm(P.OBSERVABILITY_READ)),
                    s: AsyncSession = Depends(db)) -> dict:
    """Node çalıştırması: Before (State In), Node Update, After (adım sonu State Out), Diff, stream olayları, geçişler,
    LLM / Pydantic olayları (metadata). After paralel adımda birleşik state'tir (`after_combined`)."""
    run = await _run(s, u, key)
    own = await _events(s, run.id, AgentTraceEvent.node_exec_id == exec_id)
    start = next((e for e in own if e.event_type == "node_start"), None)
    if start is None:
        raise HTTPException(404, "node çalıştırması bulunamadı")
    end = next((e for e in own if e.event_type == "node_end"), None)
    step = start.step
    st_out = next(iter(await _events(s, run.id, AgentTraceEvent.event_type == "state_out",
                                     AgentTraceEvent.step == step)), None)
    trans = await _events(s, run.id, AgentTraceEvent.event_type == "transition",
                          AgentTraceEvent.step.in_([step - 1, step]))
    t_in = next((t for t in trans if t.step == step - 1 and start.node in (t.payload.get("to") or [])), None)
    t_out = next((t for t in trans if t.step == step), None)
    nxt = await _events(s, run.id, AgentTraceEvent.event_type == "node_start", AgentTraceEvent.step == step + 1)
    before = (start.payload or {}).get("state_in")
    after = (st_out.payload or {}).get("values") if st_out else None
    diff_ok = before is not None and after is not None and not start.truncated and not (st_out and st_out.truncated)
    return {
        "node": start.node, "node_exec_id": exec_id, "step": step, "attempt": start.attempt,
        "status": end.status if end else "running", "duration_ms": end.duration_ms if end else None,
        "before": {"captured": before is not None, "truncated": start.truncated, "value": before,
                   "event_id": str(start.id)},
        "update": {"captured": end is not None, "truncated": bool(end and end.truncated),
                   "value": (end.payload or {}).get("node_update") if end else None,
                   "error": (end.payload or {}).get("error") if end else None,
                   "event_id": str(end.id) if end else None},
        "after": {"captured": after is not None, "truncated": bool(st_out and st_out.truncated), "value": after,
                  "combined": bool(st_out and st_out.payload.get("combined")),
                  "step_node_exec_ids": (st_out.payload.get("node_exec_ids") if st_out else []),
                  "event_id": str(st_out.id) if st_out else None},
        "diff": {"available": diff_ok, "changes": state_diff(before, after) if diff_ok else [],
                 "reason": None if diff_ok else ("kırpılmış snapshot" if before is not None and after is not None
                                                 else "snapshot kaydedilemedi")},
        "transition_in": ({"from": t_in.payload.get("from"), "from_exec_ids": t_in.payload.get("from_exec_ids"),
                           "routing": t_in.payload.get("routing"), "event_id": str(t_in.id)} if t_in else None),
        "transition_out": ({"to": t_out.payload.get("to"), "routing": t_out.payload.get("routing"),
                            "to_exec_ids": [n.node_exec_id for n in nxt if n.node in (t_out.payload.get("to") or [])],
                            "event_id": str(t_out.id)} if t_out else None),
        "events": [{k: v for k, v in _full(e).items() if k != "payload"} for e in own],
    }


@router.get("/agent-traces/{key}/calls/{call_id}")
async def call_detail(key: str, call_id: str, u: User = Depends(perm(P.OBSERVABILITY_READ)),
                      s: AsyncSession = Depends(db)) -> dict:
    """LLM çağrısı (request / provider request / response) ve ona bağlı parser olayları; validation error'da takip
    eden retry ya da fallback."""
    run = await _run(s, u, key)
    evs = await _events(s, run.id, or_(AgentTraceEvent.call_id == call_id,
                                       AgentTraceEvent.related_call_id == call_id))
    if not evs:
        raise HTTPException(404, "çağrı bulunamadı")
    llm_id = next((e.call_id for e in evs if e.event_type == "llm_request"), None) or \
        next((e.related_call_id for e in evs if e.related_call_id), None)
    if llm_id and llm_id != call_id:
        evs = await _events(s, run.id, or_(AgentTraceEvent.call_id == llm_id,
                                           AgentTraceEvent.related_call_id == llm_id))
    out = [_full(e) for e in evs]
    for e, d in zip(evs, out):
        if e.event_type == "validation_error":
            d["followup"] = await _followup(s, run.id, e)
    return {"call_id": llm_id or call_id, "events": out}


async def _followup(s, run_id, err: AgentTraceEvent) -> dict:
    """Hatadan sonra aynı node çalıştırmasındaki ilk LLM isteği (retry) ya da açık fallback işareti."""
    nxt = await _events(s, run_id, AgentTraceEvent.node_exec_id == err.node_exec_id,
                        AgentTraceEvent.sequence > err.sequence,
                        AgentTraceEvent.event_type.in_(["llm_request", "fallback"]))
    if not nxt:
        return {"kind": "none", "label": "retry yok"}
    n = nxt[0]
    if n.event_type == "llm_request":
        return {"kind": "retry", "call_id": n.call_id, "attempt": n.attempt, "event_id": str(n.id)}
    return {"kind": "fallback", "to": (n.payload or {}).get("to"), "event_id": str(n.id)}


# ---------------------------------------------------------------- canlı takip (SSE)
@router.get("/agent-traces/{key}/stream")
async def stream(key: str, request: Request, after: int = Query(0, ge=0),
                 last_event_id: str | None = Header(None, alias="Last-Event-ID"),
                 u: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)):
    """Olay metadata'sını sıra numarasıyla yayınlar (SSE id = sequence). Yeniden bağlanan istemci Last-Event-ID ile
    kaldığı yerden devam eder → aynı olay iki kez gelmez. Run bitip olaylar tükenince akış kapanır."""
    run = await _run(s, u, key)
    rid, start = run.id, int(last_event_id) if last_event_id and last_event_id.isdigit() else after
    sm, sid = request.app.state.rt.sm, getattr(request.state, "session_id", None)

    async def gen():
        last, idle = start, 0
        while True:
            if await request.is_disconnected():
                return
            async with transaction(sm) as s2:
                rows = (await s2.execute(select(*_meta_cols()).where(AgentTraceEvent.trace_run_id == rid,
                                                                     AgentTraceEvent.sequence > last)
                                         .order_by(AgentTraceEvent.sequence).limit(500))).all()
                r = await s2.get(AgentTraceRun, rid)
                st = svc.effective_status(r) if r else "failed"
            for row in rows:
                m = _meta(row)
                last = m["sequence"]
                m.update(type="trace_event", seq=last)             # mevcut SSE istemcisi: seq ile tekrar elenir
                yield {"id": str(last), "event": "trace_event", "data": json.dumps(m, ensure_ascii=False, default=str)}
            if not rows and st != "running":
                yield {"event": "trace_done", "data": json.dumps({"type": "done", "status": st, "last_sequence": last})}
                return
            if not rows:
                idle += 1
                if idle % 15 == 0:
                    if sid and not await sec_sessions.is_active(sm, sid):
                        yield {"event": "session_revoked", "data": json.dumps({"type": "session_revoked"})}
                        return
                    yield {"comment": "keepalive"}
                await asyncio.sleep(1)
            else:
                idle = 0

    return EventSourceResponse(gen(), ping=20, headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
