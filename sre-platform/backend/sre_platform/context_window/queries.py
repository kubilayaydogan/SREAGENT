"""Context Windows okuma query'leri: özet, çağrı listesi (filtre / sıralama / sayfalama), çağrı detayı.

Değer kaydedilmediyse `None` döner (sıfırla doldurulmaz); arayüz "Bilinmiyor" / "Ölçülmedi" yazar. Sohbet turu
(chat_turn) kayıtları yalnız sahibine görünür (admin hepsini) — Agent Trace ve Agent Middleware ile aynı kural.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from airlab_agent import context_window as cw

from ..db.models import LlmContextCall as C

RANGES = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
STATUSES = ("ok", "error", "context_limit", "retried", "cancelled")
Viewer = tuple[str | None, bool]


class NotFound(Exception):
    pass


def visible(viewer: Viewer):
    uid, admin = viewer
    if admin:
        return true()
    return or_(C.run_kind.is_(None), C.run_kind != "chat_turn", C.user_id == uid)


def since_of(rng: str | None) -> datetime:
    return datetime.now(timezone.utc) - RANGES.get(rng or "24h", RANGES["24h"])


def _iso(d):
    return d.isoformat() if d else None


def view(r: C) -> dict:
    return {"call_id": str(r.call_id), "logical_call_id": str(r.logical_call_id), "attempt_no": r.attempt_no,
            "attempt_count": r.attempt_count, "run_id": r.run_id, "run_kind": r.run_kind, "node": r.node,
            "task": r.task, "call_seq": r.call_seq, "provider": r.provider, "model": r.model,
            "served_model": r.served_model, "streaming": r.streaming, "started_at": _iso(r.started_at),
            "duration_ms": r.duration_ms, "status": r.status, "error_code": r.error_code,
            "input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "capacity": r.capacity,
            "planned_fill": r.planned_fill, "actual_fill": r.actual_fill, "usage_source": r.usage_source}


def _links(r: C) -> dict:
    trace = None
    if r.run_id:
        trace = f"/agent-trace/{r.run_id}" + (f"?call={r.trace_call_id}" if r.trace_call_id else "")
    return {"agent_trace": trace, "agent_trace_level": ("call" if r.trace_call_id else "run") if r.run_id else None,
            "middleware_timeline": f"/agent-middleware?tab=timeline&run={r.run_id}" if r.run_id else None,
            "llm_monitoring": "/llm"}


def detail_view(r: C, siblings: list[C]) -> dict:
    out = view(r)
    out.update({
        "thread_id": r.thread_id, "ended_at": _iso(r.ended_at), "error_message": r.error_message,
        "http_status": r.http_status, "stop_reason": r.stop_reason, "request_id": r.request_id,
        "capacity_source": r.capacity_source, "capacity_note": r.capacity_note, "model_max_output": r.model_max_output,
        "reserved_output": r.reserved_output, "estimated_input": r.estimated_input,
        "estimate_method": r.estimate_method, "estimate_over": r.estimate_over,
        "cache_read_tokens": r.cache_read_tokens, "cache_creation_tokens": r.cache_creation_tokens,
        "reasoning_tokens": r.reasoning_tokens, "components": r.components, "context_decisions": r.context_decisions,
        "stream": r.stream, "trace_call_id": r.trace_call_id, "middleware_execution_id": r.middleware_execution_id,
        "attempts": [view(x) for x in sorted(siblings, key=lambda x: x.attempt_no)],
        "links": _links(r),
        "component_labels": cw.COMPONENTS,
    })
    return out


async def overview(s: AsyncSession, viewer: Viewer, rng: str | None = "24h") -> dict:
    conds = [C.started_at >= since_of(rng), visible(viewer)]
    row = (await s.execute(select(
        func.count(),
        func.count(func.distinct(C.logical_call_id)),
        func.max(C.actual_fill),
        func.count().filter(C.status == "context_limit"),
        func.count().filter(C.status == "retried"),
        func.count().filter(C.status == "error"),
        func.count().filter(C.usage_source == "missing", C.status != "retried"),
        func.count().filter(C.capacity.is_(None)),
    ).where(*conds))).one()
    top = None
    if row[2] is not None:
        t = (await s.execute(select(C).where(*conds, C.actual_fill.is_not(None))
                             .order_by(C.actual_fill.desc()).limit(1))).scalar_one_or_none()
        top = view(t) if t is not None else None
    return {"range": rng or "24h", "since": since_of(rng).isoformat(), "attempts": row[0], "calls": row[1],
            "max_actual_fill": row[2], "max_call": top, "context_limit_errors": row[3], "retried_attempts": row[4],
            "errors": row[5], "usage_missing": row[6], "capacity_unknown": row[7]}


async def facets(s: AsyncSession, viewer: Viewer, rng: str | None = "24h") -> dict:
    conds = [C.started_at >= since_of(rng), visible(viewer)]
    nodes = [n for (n,) in await s.execute(select(C.node).where(*conds).distinct().order_by(C.node)) if n]
    models = [m for (m,) in await s.execute(select(C.model).where(*conds).distinct().order_by(C.model)) if m]
    return {"nodes": nodes, "models": models, "statuses": list(STATUSES)}


async def calls(s: AsyncSession, viewer: Viewer, *, rng: str | None = "24h", run_id: str | None = None,
                node: str | None = None, model: str | None = None, status: str | None = None,
                sort: str = "time", limit: int = 50, offset: int = 0) -> dict:
    conds = [visible(viewer)] + ([] if run_id else [C.started_at >= since_of(rng)])
    for col, v in ((C.run_id, run_id), (C.node, node), (C.model, model), (C.status, status)):
        if v:
            conds.append(col == v)
    total = (await s.execute(select(func.count()).select_from(C).where(*conds))).scalar_one()
    order = ([C.actual_fill.desc().nulls_last(), C.started_at.desc()] if sort == "fill" else
             [C.run_id, C.call_seq, C.attempt_no] if (sort == "sequence" and run_id) else
             [C.started_at.desc(), C.attempt_no.desc()])
    rows = (await s.execute(select(C).where(*conds).order_by(*order)
                            .limit(min(max(limit, 1), 200)).offset(max(offset, 0)))).scalars()
    return {"total": total, "items": [view(r) for r in rows], "limit": limit, "offset": offset}


async def call(s: AsyncSession, call_id: str, viewer: Viewer) -> dict:
    try:
        cid = uuid.UUID(call_id)
    except ValueError:
        raise NotFound() from None
    r = (await s.execute(select(C).where(C.call_id == cid, visible(viewer)))).scalar_one_or_none()
    if r is None:
        raise NotFound()
    sib = list((await s.execute(select(C).where(C.logical_call_id == r.logical_call_id))).scalars())
    return detail_view(r, sib)
