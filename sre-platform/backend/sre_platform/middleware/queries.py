"""Middleware gözlem query'leri: overview, katalog, detay, execution explorer, run timeline.

Yalnız gerçekten kaydedilen değerler döner; ölçülmeyen alan `None` (arayüz "yakalanmadı" / "desteklenmiyor" yazar).
İçerik kayıttan önce maskelenmiştir; istemciye gönderimden önce yeniden maskelenir (savunma derinliği).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import Float, and_, cast, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from airlab_agent import middleware as mw
from airlab_agent.agent_trace import state_diff
from airlab_agent.redact import redact

from ..db.models import MiddlewareExecution as E
from . import service

RANGES = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}


Viewer = tuple[str | None, bool]            # (user_id, admin mi)


def visible(viewer: Viewer):
    """Sohbet turu (chat_turn) kayıtları yalnız sahibine görünür (admin hepsini görür) — Agent Trace ile aynı kural."""
    uid, admin = viewer
    if admin:
        return true()
    return or_(E.run_kind.is_(None), E.run_kind != "chat_turn", E.user_id == uid)


def since_of(rng: str | None) -> datetime:
    return datetime.now(timezone.utc) - RANGES.get(rng or "24h", RANGES["24h"])


def _pct(col, p: float):
    return func.percentile_cont(p).within_group(cast(col, Float))


def _f(v):
    return round(float(v), 3) if v is not None else None


def view(r: E, *, full: bool = False) -> dict:
    out = {"execution_id": str(r.id), "run_id": r.run_id, "run_kind": r.run_kind, "thread_id": r.thread_id,
           "trace_id": r.trace_id, "span_id": None,
           "parent_execution_id": str(r.parent_execution_id) if r.parent_execution_id else None,
           "sequence": r.sequence, "depth": r.depth, "entry_type": r.entry_type, "middleware": r.middleware,
           "middleware_version": r.middleware_version, "hook": r.hook, "target_kind": r.target_kind,
           "target": r.target, "task": r.task, "connector": r.connector, "tool_call_id": r.tool_call_id,
           "attempt_no": r.attempt_no, "started_at": r.started_at.isoformat(),
           "ended_at": r.ended_at.isoformat() if r.ended_at else None, "overhead_ms": r.overhead_ms,
           "wrapped_ms": r.wrapped_ms, "result": r.result, "decision": r.decision, "outcome": r.outcome,
           "reason": r.reason, "error_code": r.error_code, "policy": r.policy,
           "changed_fields": list(r.changed_fields or []), "truncated": r.truncated,
           "payload_captured": r.before is not None or r.after is not None}
    if full:
        before, after = redact(r.before, "trace"), redact(r.after, "trace")
        out.update(before=before, after=after, extra=redact(r.extra or {}, "trace"),
                   diff=state_diff(before, after) if (before is not None and after is not None) else None,
                   cache="not_supported")
    return out


async def overview(s: AsyncSession, rng: str | None) -> dict:
    since = since_of(rng)
    cfg = await service.load(s)
    win = E.started_at >= since
    mid = and_(win, E.entry_type == "middleware")
    row = (await s.execute(select(
        func.count().filter(mid),
        func.count().filter(and_(mid, E.decision == "block")),
        func.count().filter(and_(mid, E.result == "error")),
        func.count().filter(and_(mid, E.decision == "modify")),
        func.count().filter(and_(win, E.entry_type == "call")),
        func.count(func.distinct(E.run_id)).filter(and_(win, E.entry_type == "agent")),
    ))).one()
    lat = (await s.execute(select(
        _pct(E.overhead_ms, 0.5), _pct(E.overhead_ms, 0.95), _pct(E.wrapped_ms, 0.5), _pct(E.wrapped_ms, 0.95),
        func.count(E.wrapped_ms)).where(win, E.entry_type == "call"))).one()
    bucket = "hour" if (rng or "24h") in ("1h", "24h") else "day"
    b = func.date_trunc(bucket, E.started_at)
    trend = [{"t": t.isoformat(), "executions": n, "blocked": bl, "errors": er} for t, n, bl, er in (await s.execute(
        select(b, func.count(), func.count().filter(E.decision == "block"), func.count().filter(E.result == "error"))
        .where(mid).group_by(b).order_by(b))).all()]
    by_hook = [{"hook": h, "executions": n, "overhead_p95_ms": _f(p)} for h, n, p in (await s.execute(
        select(E.hook, func.count(), _pct(E.overhead_ms, 0.95)).where(mid).group_by(E.hook))).all()]
    return {"range": rng or "24h", "since": since.isoformat(),
            "registered": len(mw.REGISTRY), "enabled": sum(1 for c in cfg.values() if c["enabled"]),
            "mandatory": sum(1 for m in mw.REGISTRY.values() if m.mandatory),
            "executions": row[0], "blocked": row[1], "errors": row[2], "modified": row[3], "wrapped_calls": row[4],
            "runs": row[5], "pending_policy_decisions": None,
            "pending_policy_note": "desteklenmiyor: middleware'de bekleyen karar yok (onaylar Approvals sayfasında)",
            "overhead_ms": {"p50": _f(lat[0]), "p95": _f(lat[1])},
            "wrapped_ms": {"p50": _f(lat[2]), "p95": _f(lat[3]), "measured": lat[4]},
            "trend": trend, "trend_bucket": bucket, "by_hook": by_hook}


async def _stats(s: AsyncSession, since: datetime) -> dict[str, dict]:
    q = select(E.middleware, func.count(), func.count().filter(E.decision == "block"),
               func.count().filter(E.result == "error"), func.count().filter(E.decision == "modify"),
               _pct(E.overhead_ms, 0.5), _pct(E.overhead_ms, 0.95)) \
        .where(E.started_at >= since, E.entry_type == "middleware").group_by(E.middleware)
    return {n: {"executions": c, "blocked": bl, "errors": er, "modified": mo, "overhead_p50_ms": _f(p50),
                "overhead_p95_ms": _f(p95)} for n, c, bl, er, mo, p50, p95 in (await s.execute(q)).all()}


async def _last(s: AsyncSession) -> dict[str, dict]:
    sub = select(E.middleware, func.max(E.started_at).label("m")).where(E.entry_type == "middleware") \
        .group_by(E.middleware).subquery()
    rows = (await s.execute(select(E).join(sub, and_(E.middleware == sub.c.middleware, E.started_at == sub.c.m))
                            )).scalars()
    return {r.middleware: {"at": r.started_at.isoformat(), "decision": r.decision, "result": r.result,
                           "reason": r.reason, "execution_id": str(r.id)} for r in rows}


async def catalog(s: AsyncSession, rng: str | None = "24h") -> list[dict]:
    cfg = await service.load(s)
    stats, last = await _stats(s, since_of(rng)), await _last(s)
    out = []
    for n, m in mw.REGISTRY.items():
        c = cfg[n]
        out.append({**mw.describe(n), "enabled": c["enabled"], "order": c["order"], "scope": c["scope"],
                    "scope_label": "global (zorunlu)" if m.mandatory else _scope_label(c["scope"]),
                    "version": c["version"], "updated_at": c.get("updated_at"), "updated_by": c.get("updated_by"),
                    "last": last.get(n), "stats": stats.get(n) or {"executions": 0, "blocked": 0, "errors": 0,
                                                                   "modified": 0, "overhead_p50_ms": None,
                                                                   "overhead_p95_ms": None}})
    out.sort(key=lambda x: x["order"])
    return out


def _scope_label(scope: dict) -> str:
    parts = [f"{k}: {', '.join(v)}" for k, v in (scope or {}).items() if v]
    return "; ".join(parts) if parts else "global"


async def detail(s: AsyncSession, name: str, viewer: Viewer, rng: str | None = "24h") -> dict:
    cat = {c["name"]: c for c in await catalog(s, rng)}
    if name not in cat:
        raise service.MiddlewareError(404, "kayıtlı olmayan middleware")
    d = cat[name]
    cfg = (await service.load(s))[name]
    hooks = [{"hook": h, "executions": n, "blocked": bl, "errors": er, "overhead_p50_ms": _f(p50),
              "overhead_p95_ms": _f(p95)} for h, n, bl, er, p50, p95 in (await s.execute(
                  select(E.hook, func.count(), func.count().filter(E.decision == "block"),
                         func.count().filter(E.result == "error"), _pct(E.overhead_ms, 0.5),
                         _pct(E.overhead_ms, 0.95))
                  .where(E.middleware == name, E.started_at >= since_of(rng)).group_by(E.hook))).all()]
    recent = [view(r) for r in (await s.execute(select(E).where(E.middleware == name, visible(viewer))
                                                .order_by(E.started_at.desc()).limit(25))).scalars()]
    return {**d, "config_json": service.masked({k: cfg[k] for k in ("enabled", "order", "params", "scope",
                                                                     "version")}),
            "hook_info": {h: mw.HOOK_INFO[h] for h in d["hooks"]}, "versions": await service.versions(s, name),
            "hook_stats": hooks, "recent": recent}


async def executions(s: AsyncSession, viewer: Viewer, *, rng: str | None = "24h", middleware: str | None = None,
                     hook: str | None = None, decision: str | None = None, result: str | None = None,
                     run_id: str | None = None, target: str | None = None, tool_call_id: str | None = None,
                     entry_type: str | None = "middleware", limit: int = 50, offset: int = 0) -> dict:
    conds = [visible(viewer)] + ([E.started_at >= since_of(rng)] if not run_id and not tool_call_id else [])
    for col, v in ((E.middleware, middleware), (E.hook, hook), (E.decision, decision), (E.result, result),
                   (E.run_id, run_id), (E.target, target), (E.tool_call_id, tool_call_id),
                   (E.entry_type, entry_type)):
        if v:
            conds.append(col == v)
    total = (await s.execute(select(func.count()).select_from(E).where(*conds))).scalar_one()
    rows = (await s.execute(select(E).where(*conds).order_by(E.started_at.desc(), E.sequence.desc())
                            .limit(min(max(limit, 1), 200)).offset(max(offset, 0)))).scalars()
    return {"total": total, "items": [view(r) for r in rows], "limit": limit, "offset": offset}


async def execution(s: AsyncSession, eid, viewer: Viewer) -> dict:
    r = (await s.execute(select(E).where(E.id == eid, visible(viewer)))).scalar_one_or_none()
    if r is None:
        raise service.MiddlewareError(404, "execution bulunamadı")
    out = view(r, full=True)
    parent = await s.get(E, r.parent_execution_id) if r.parent_execution_id else None
    out["parent"] = view(parent) if parent is not None else None
    out["links"] = _links(r)
    return out


def _links(r: E) -> dict:
    return {"agent_trace": f"/agent-trace/{r.run_id}" if r.run_id else None,
            "timeline": f"/agent-middleware?tab=timeline&run={r.run_id}" if r.run_id else None,
            "mcp_call": f"/mcp-calls?call={r.tool_call_id}" if r.tool_call_id else None,
            "llm_monitoring": "/llm" if r.target_kind == "model" else None}


async def runs(s: AsyncSession, viewer: Viewer, rng: str | None = "24h", limit: int = 50, offset: int = 0,
               kind: str | None = None) -> dict:
    conds = [E.entry_type == "agent", E.started_at >= since_of(rng), visible(viewer)]
    if kind:
        conds.append(E.run_kind == kind)
    total = (await s.execute(select(func.count()).select_from(E).where(*conds))).scalar_one()
    rows = (await s.execute(select(E).where(*conds).order_by(E.started_at.desc()).limit(min(limit, 200))
                            .offset(offset))).scalars()
    items = []
    for r in rows:
        v = view(r)
        v["lost_records"] = (r.extra or {}).get("lost_records")
        items.append(v)
    if items:
        ids = [i["run_id"] for i in items]
        agg = {rid: (n, bl, er) for rid, n, bl, er in (await s.execute(
            select(E.run_id, func.count().filter(E.entry_type == "middleware"),
                   func.count().filter(E.decision == "block"),
                   func.count().filter(and_(E.result == "error", E.entry_type == "middleware")))
            .where(E.run_id.in_(ids)).group_by(E.run_id))).all()}
        for i in items:
            n, bl, er = agg.get(i["run_id"], (0, 0, 0))
            i.update(executions=n, blocked=bl, errors=er)
    return {"total": total, "items": items}


async def timeline(s: AsyncSession, run_id: str, viewer: Viewer) -> dict:
    """Gerçek yürütme sırası: `sequence` (run içi monoton sayaç) + parent/child ağacı. Zaman damgasına göre sıralanmaz.
    Paralel tool çağrıları aynı parent (run kökü) altında kardeş zarflardır; iç içe wrapper'lar depth ile görünür."""
    rows = list((await s.execute(select(E).where(E.run_id == run_id, visible(viewer)).order_by(E.sequence))).scalars())
    if not rows:
        raise service.MiddlewareError(404, "bu run için middleware kaydı yok")
    nodes = {str(r.id): {**view(r), "children": []} for r in rows}
    roots = []
    for r in rows:
        n = nodes[str(r.id)]
        p = str(r.parent_execution_id) if r.parent_execution_id else None
        if p and p in nodes:
            nodes[p]["children"].append(n)
        else:
            roots.append(n)

    def sort(ns):
        ns.sort(key=lambda x: x["sequence"])
        for x in ns:
            sort(x["children"])
    sort(roots)
    root = next((r for r in rows if r.entry_type == "agent"), None)
    nested = [view(r) for r in (await s.execute(select(E).where(E.entry_type == "agent", visible(viewer),
                                                                E.parent_execution_id == root.id))).scalars()] \
        if root is not None else []
    parallel = _parallel_groups(rows)
    return {"run_id": run_id, "root": view(root) if root else None,
            "status": root.outcome if root else "running_or_unknown", "tree": roots, "count": len(rows),
            "nested_runs": nested, "parallel_groups": parallel,
            "config_versions": (root.extra or {}).get("config_versions") if root else None,
            "lost_records": (root.extra or {}).get("lost_records") if root else None,
            "ordering": "sequence + parent_execution_id (zaman damgası değil)"}


def _parallel_groups(rows: list[E]) -> list[list[str]]:
    """Zaman aralığı örtüşen tool zarfları (gerçekten eşzamanlı çalışanlar). Yalnız bilgi amaçlıdır."""
    calls = sorted([r for r in rows if r.entry_type == "call" and r.target_kind == "tool" and r.ended_at],
                   key=lambda r: r.started_at)
    groups, cur, cur_end = [], [], None
    for r in calls:
        if cur and r.started_at < cur_end:
            cur.append(str(r.id))
            cur_end = max(cur_end, r.ended_at)
        else:
            if len(cur) > 1:
                groups.append(cur)
            cur, cur_end = [str(r.id)], r.ended_at
    if len(cur) > 1:
        groups.append(cur)
    return groups


async def retention(s: AsyncSession, days: int) -> int:
    from sqlalchemy import delete
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(int(days), 1))
    res = await s.execute(delete(E).where(E.started_at < cutoff))
    return res.rowcount or 0
