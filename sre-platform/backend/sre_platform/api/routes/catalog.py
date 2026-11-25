"""Uygulamalar, dashboard ve sistem uçları."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import IncidentStatus, InvestigationStatus, User, WatchCondition
from ...repositories import applications, incidents, investigations
from ...security.permissions import P
from ..deps import current_user, db
from ..deps import require_perm as perm
from ..schemas import ApplicationOut, IncidentOut, InvestigationOut, RangeKey

router = APIRouter(tags=["applications"])


async def _open_keys(s: AsyncSession) -> set[str]:
    rows = (await s.execute(select(WatchCondition.key, WatchCondition.state))).all()
    return {k for k, st in rows if (st or {}).get("state") == "open"}


@router.get("/applications", response_model=list[ApplicationOut])
async def list_apps(kind: str | None = Query(None, pattern="^(business|tooling)$"), _: User = Depends(current_user),
                    s: AsyncSession = Depends(db)):
    return await applications.list_(s, kind)


def _app_view(snap: dict, app_id: str, with_series: bool) -> dict:
    a = (snap.get("apps") or {}).get(app_id)
    base = {"application_id": app_id, "available": snap["available"], "range": snap["range"], "start": snap["start"],
            "end": snap["end"], "sources": snap.get("sources"), "error": snap.get("error")}
    if a is None:
        return {**base, "status": "unknown", "reasons": ["Dynatrace verisi alınamadı" if not snap["available"]
                                                         else "telemetri yok"], "metrics": None}
    metrics = {k: v for k, v in a.items() if k not in ("status", "reasons", "series")}
    out = {**base, "status": a["status"], "reasons": a["reasons"], "metrics": metrics}
    if with_series:
        out["series"] = a["series"]
        out["interval_minutes"] = snap.get("interval_minutes")
    return out


@router.get("/applications/{app_id}/health")
async def app_health(app_id: str, request: Request, range: RangeKey = "15m", _: User = Depends(current_user),
                     s: AsyncSession = Depends(db)):
    await applications.get(s, app_id)
    snap = await request.app.state.telemetry.snapshot(range, open_keys=await _open_keys(s))
    return _app_view(snap, app_id, False)


@router.get("/applications/{app_id}/metrics")
async def app_metrics(app_id: str, request: Request, range: RangeKey = "1h", _: User = Depends(current_user),
                      s: AsyncSession = Depends(db)):
    await applications.get(s, app_id)
    snap = await request.app.state.telemetry.snapshot(range, open_keys=await _open_keys(s))
    return _app_view(snap, app_id, True)


def _single_overall(a: dict | None, snap: dict) -> dict:
    """Tek uygulama filtresinde genel görünümle AYNI anahtarlar (p95_ms_worst = o uygulamanın p95'i)."""
    if not snap.get("available"):
        return {}
    a = a or {}
    db = snap.get("db") or {}
    return {"throughput_rpm": a.get("throughput_rpm"), "error_rate": a.get("error_rate"), "mean_ms": a.get("mean_ms"),
            "p95_ms_worst": a.get("p95_ms"), "p99_ms_worst": a.get("p99_ms"),
            "db_connection_ratio": db.get("last_ratio"), "db_connection_ratio_max": db.get("max_ratio")}


@router.get("/dashboard/summary")
async def dashboard(request: Request, range: RangeKey = "1h", application_id: str | None = None,
                    _: User = Depends(current_user), s: AsyncSession = Depends(db)):
    if application_id:
        await applications.get(s, application_id)
    snap = await request.app.state.telemetry.snapshot(range, open_keys=await _open_keys(s))
    apps = snap.get("apps") or {}
    if application_id:
        apps = {k: v for k, v in apps.items() if k == application_id}
    business = [a.id for a in await applications.list_(s, "business")]
    since = datetime.now(timezone.utc) - timedelta(days=7)
    inc_open = await incidents.list_(s, status=[IncidentStatus.OPEN, IncidentStatus.INVESTIGATING,
                                                IncidentStatus.MITIGATED], application_id=application_id, size=5)
    inv_recent = await investigations.list_(s, application_id=application_id, size=6)
    inv_open = await investigations.list_(s, status=[InvestigationStatus.PENDING, InvestigationStatus.RUNNING], size=1)
    counts: dict[str, int] = {}
    for a in business:
        st = (apps.get(a) or {}).get("status", "unknown") if (not application_id or a == application_id) else None
        if st:
            counts[st] = counts.get(st, 0) + 1
    return {
        "range": snap["range"], "start": snap["start"], "end": snap["end"], "available": snap["available"],
        "error": snap.get("error"), "sources": snap.get("sources"), "interval_minutes": snap.get("interval_minutes"),
        "monitored_applications": len(business) if not application_id else 1,
        "status_counts": counts,
        "active_incidents": inc_open.total,
        "open_investigations": inv_open.total,
        "overall": snap.get("overall") if not application_id else _single_overall(apps.get(application_id), snap),
        "db": snap.get("db"), "db_series": snap.get("db_series", []),
        "applications": {k: {"status": v["status"], "reasons": v["reasons"],
                             **{m: v.get(m) for m in ("throughput_rpm", "error_rate", "mean_ms", "p95_ms", "p99_ms",
                                                      "span_samples", "pool")},
                             "series": v["series"]} for k, v in apps.items()},
        "recent_investigations": [InvestigationOut.model_validate(i).model_dump(mode="json") for i in inv_recent.items],
        "recent_incidents": [IncidentOut.model_validate(i).model_dump(mode="json") for i in inc_open.items],
        "incident_counts": await incidents.counts(s),
        "investigation_stats_7d": await investigations.stats(s, since),
    }


system = APIRouter(tags=["system"])


@system.get("/health")
async def health():
    return {"status": "ok"}


@system.get("/ready")
async def ready(request: Request):
    from ...db.migrate import check_ready
    import asyncio
    ok, detail = await asyncio.to_thread(check_ready, request.app.state.rt.ps.database_url)
    if not ok:
        from fastapi.responses import JSONResponse
        return JSONResponse({"status": "not_ready", "detail": detail}, status_code=503)
    return {"status": "ready", "detail": detail}


@system.get("/system/info")
async def info(request: Request, _: User = Depends(current_user)):
    rt = request.app.state.rt
    from ...services import model_switch
    await model_switch.refresh(rt)                     # başka süreçte (ör. worker) yapılan seçim de görünsün
    return {"environment": rt.ps.environment, "llm_enabled": rt.llm_enabled,
            "llm_model": rt.llm.model_name if rt.llm else None, "mcp_configured": bool(rt.agent.mcp_url and rt.agent.dt_token),
            "dynatrace_environment": rt.agent.dt_env or None,
            "langsmith_tracing": rt.agent.langsmith_tracing, "limits": {
                "max_concurrent_investigations": rt.ps.max_concurrent_investigations,
                "investigation_timeout_s": rt.ps.investigation_timeout_s,
                "max_queries_per_run": rt.agent.cfg["budgets"]["max_queries_per_run"],
                "max_iterations": rt.agent.cfg["budgets"]["max_iterations"],
                "max_window_minutes": rt.agent.cfg["windows"]["max_minutes"]},
            "active_runs": request.app.state.runs.active_count()}


@system.get("/system/llm-model")
async def get_llm_model(request: Request, _: User = Depends(current_user)):
    from ...services import model_switch
    return await model_switch.state(request.app.state.rt)


@system.put("/system/llm-model")
async def put_llm_model(request: Request, body: dict, u: User = Depends(perm(P.LLM_MANAGE))):
    from fastapi import HTTPException
    from ...services import model_switch
    rt = request.app.state.rt
    if rt.llm is None:
        raise HTTPException(409, "LLM kapalı (ANTHROPIC_API_KEY yok); model seçilemez")
    try:
        return await model_switch.select(rt, str(body.get("model", "")), username=u.username)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


@router.get("/observability/llm", tags=["observability"])
async def llm_observability(request: Request, range: str = Query("24h", pattern="^(24h|7d|30d)$"),
                            _: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)):
    """LLM izleme: LangSmith'ten worker'ın senkronladığı özetler (sre.llm_runs). İçerik (prompt/cevap) yok."""
    from ...services import langsmith_trace, llm_observability
    out = await llm_observability.summary(s, range)
    rt = request.app.state.rt
    base = await langsmith_trace.run_url(rt, "{run_id}") if langsmith_trace.tracer(rt) else None
    for key in ("top_cost", "slowest", "errors", "llm_errors"):
        for item in out[key]:
            tid = item.get("trace_id") or item.get("id")
            item["url"] = base.replace("{run_id}", tid) if base and tid else None
    return out
