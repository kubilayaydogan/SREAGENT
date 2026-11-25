"""MCP Çağrıları: Agent'ın MCP sunucularına gönderdiği `tools/call` isteklerinin listesi ve detayı.

Kaynak mevcut çağrı kaydı tablosudur (`sre.agent_tool_executions`, mcp_method dolu satırlar). Her satır bir mantıksal
çağrıdır; retry denemeleri `attempts`'ta. Yanıtlar yalnız güvenli alanları döner: ham DQL, ham MCP yanıtı, token ve
header hiçbir uçta yoktur. Eski (0012 öncesi) satırlarda ham DQL `tool_arguments`'ta olabilir — API onu yalnız güvenli
önizlemeye çevirerek kullanır, kendisini döndürmez.

Yetki: `observability:read` (SRE/admin; viewer ve bu scope'u olmayan API anahtarı 403).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from airlab_agent.mcp_gateway import request_preview, safe_error_summary

from ...db.models import Investigation, ToolExecution, User
from ...repositories.base import paginate
from ...security.permissions import P
from ...services import langsmith_trace
from ..deps import db
from ..deps import require_perm as perm

router = APIRouter(tags=["mcp-calls"])

OUTCOMES = ("success", "empty", "timeout", "permission_denied", "tool_error", "unavailable", "cancelled")
FAILED = {"timeout", "permission_denied", "tool_error", "unavailable", "cancelled"}
COUNTED = {"success", "empty"}                    # kayıt sayısı yalnız başarılı yanıtta anlamlı


class McpCallRow(BaseModel):
    id: uuid.UUID
    started_at: datetime | None
    connector: str | None
    tool: str
    step: str | None
    query_key: str | None
    outcome: str | None
    failed: bool
    duration_ms: int | None
    record_count: int | None
    attempts: int
    investigation_id: uuid.UUID
    run_id: str


class McpCallPage(BaseModel):
    items: list[McpCallRow]
    total: int
    page: int
    size: int
    pages: int


def _base() -> Select:
    return (select(ToolExecution, Investigation.run_id)
            .join(Investigation, Investigation.id == ToolExecution.investigation_id)
            .where(ToolExecution.mcp_method.is_not(None)))


def _preview(t: ToolExecution) -> dict[str, Any]:
    """Kayıtlı güvenli önizleme; eski satırda ham DQL'den sunucu tarafında üretilir (ham DQL dönmez)."""
    if t.request_preview:
        return dict(t.request_preview)
    dql = (t.tool_arguments or {}).get("dqlQueryString")
    args = {"dqlQueryString": dql, "includeTypes": False} if isinstance(dql, str) else {}
    p = request_preview(t.connector or "dynatrace", t.tool_name, args, t.signal)
    p["legacy"] = True
    return p


def _row(t: ToolExecution, run_id: str) -> dict:
    return {"id": t.id, "started_at": t.started_at or t.created_at, "connector": t.connector, "tool": t.tool_name,
            "step": t.step, "query_key": t.signal, "outcome": t.outcome, "failed": t.outcome in FAILED,
            "duration_ms": t.duration_ms, "record_count": t.record_count if t.outcome in COUNTED else None,
            "attempts": max(1, len(t.attempts or [])), "investigation_id": t.investigation_id, "run_id": run_id}


def _window(prev: dict, inv: Investigation) -> str | None:
    """Sorgu penceresi: incident (analiz aralığıyla kesişir), baseline (baseline aralığında) ya da custom."""
    try:
        qs, qe = datetime.fromisoformat(prev["query_start"]), datetime.fromisoformat(prev["query_end"])
    except (KeyError, TypeError, ValueError):
        return None
    if inv.baseline_start and inv.baseline_end and qs >= inv.baseline_start and qe <= inv.baseline_end:
        return "baseline"
    if qs < inv.window_end and qe > inv.window_start:
        return "incident"
    return "custom"


@router.get("/mcp-calls", response_model=McpCallPage)
async def list_calls(since: datetime | None = None, until: datetime | None = None,
                     connector: str | None = Query(None, max_length=64), tool: str | None = Query(None, max_length=64),
                     status: str | None = Query(None, description="outcome; virgülle birden çok"),
                     run_id: str | None = Query(None, max_length=128), page: int = 1, size: int = 50,
                     _: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)):
    stmt = _base()
    if since:
        stmt = stmt.where(ToolExecution.created_at >= since)
    if until:
        stmt = stmt.where(ToolExecution.created_at < until)
    if connector:
        stmt = stmt.where(ToolExecution.connector == connector)
    if tool:
        stmt = stmt.where(ToolExecution.tool_name == tool)
    if status:
        wanted = [x for x in status.split(",") if x]
        bad = [x for x in wanted if x not in OUTCOMES]
        if bad:
            raise HTTPException(422, f"bilinmeyen durum: {', '.join(bad)}")
        stmt = stmt.where(ToolExecution.outcome.in_(wanted))
    if run_id:
        stmt = stmt.where(Investigation.run_id == run_id)
    stmt = stmt.order_by(ToolExecution.created_at.desc(), ToolExecution.id.desc())   # en yeni üstte
    p = await paginate(s, stmt, page, size, scalars=False)
    return {"items": [_row(t, rid) for t, rid in p.items], "total": p.total, "page": p.page, "size": p.size,
            "pages": p.pages}


@router.get("/mcp-calls/facets")
async def facets(_: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)) -> dict:
    """Filtre seçenekleri: kayıtlı connector ve tool adları."""
    base = select(ToolExecution).where(ToolExecution.mcp_method.is_not(None)).subquery()
    conns = (await s.execute(select(base.c.connector).where(base.c.connector.is_not(None)).distinct())).scalars()
    tools = (await s.execute(select(base.c.tool_name).distinct())).scalars()
    return {"connectors": sorted(conns), "tools": sorted(tools), "statuses": list(OUTCOMES)}


@router.get("/mcp-calls/{call_id}")
async def call_detail(call_id: uuid.UUID, request: Request, _: User = Depends(perm(P.OBSERVABILITY_READ)),
                      s: AsyncSession = Depends(db)) -> dict:
    res = (await s.execute(_base().where(ToolExecution.id == call_id))).first()
    if res is None:
        raise HTTPException(404, "MCP çağrısı bulunamadı")
    t, run_id = res
    inv = await s.get(Investigation, t.investigation_id)
    prev = _preview(t)
    span = None
    if (t.tool_arguments or {}).get("langsmith_span"):          # span yalnız tracing açıkken yazıldı
        try:
            tmpl = (await langsmith_trace.trace_info(request.app.state.rt, s, t.investigation_id))["call_url_template"]
        except Exception:  # noqa: BLE001 — LangSmith erişilemezse detay yine döner
            tmpl = None
        span = tmpl.replace("{call_id}", str(t.id)) if tmpl else None
    row = _row(t, run_id)
    return {
        **row,
        "request": {k: prev.get(k) for k in ("method", "connector", "tool", "query_key", "params", "query_start",
                                             "query_end", "limit", "omitted_params", "legacy")},
        "query": {"query_key": prev.get("query_key"), "dql_template": prev.get("dql_template"),
                  "dql_chars": prev.get("dql_chars"), "dql_sha256": prev.get("dql_sha256"),
                  "window": _window(prev, inv) if inv else None} if prev.get("dql_template") else None,
        "response": {"outcome": t.outcome, "failed": t.outcome in FAILED, "result_status": t.result_status,
                     "duration_ms": t.duration_ms, "http_status": t.http_status,
                     "record_count": row["record_count"],
                     "error_summary": safe_error_summary(t.error_message) if t.error_message else None,
                     "evidence_key": t.evidence_key, "dynatrace_query_id": t.dynatrace_query_id},
        "attempts_log": [{"attempt": a.get("attempt"), "elapsed_ms": a.get("elapsed_ms"), "outcome": a.get("outcome"),
                          "http_status": a.get("http_status"), "error_type": a.get("error_type")}
                         for a in (t.attempts or [])],
        "related": {"mcp_call_id": str(t.id), "run_id": run_id, "investigation_id": str(t.investigation_id),
                    "step": t.step, "trigger": inv.trigger.value if inv else None,
                    "langsmith_span_url": span},
    }
