"""LangSmith entegrasyonu: investigation trace linki ve sonuç değerlerinin feedback olarak yazılması.

- Her investigation'ın LangSmith run id'si investigation id'sinin kendisidir (graph.run_analysis trace_run_id).
  Sohbetten açıldıysa sohbet turu trace'inin altındadır; worker/watch'ta kök trace'tir.
- Risk, severity, mechanism ve kapsam koşu bitince belli olur. Run metadata'sı başta yazıldığı için bunlar
  feedback olarak eklenir; LangSmith'te "risk_score > 50" ya da "mechanism = row_lock" diye filtrelenebilir.
- Platform run_id ↔ LangSmith eşlemesi `sre.trace_links`'tedir (çalıştırma girişimi başına satır; platform run_id,
  LangSmith run_id ve trace_id ayrı alanlar). Checkpoint'ten sürdürülen sohbet turu aynı platform run_id ile yeni
  girişim (attempt) satırı yazar; önceki eşleşme korunur.
- MCP çağrı span'lerinin LangSmith kimliği platformdaki çağrı kaydının kimliğidir (mcp_call_id); çağrı detayından
  ilgili span'e geçilebilir.
- Eski investigation'larda yalnız `langsmith:trace` tool kaydı vardır; link o durumda da gösterilir.
Bu modüldeki hiçbir hata araştırmayı bozmaz.
"""
from __future__ import annotations

import asyncio
import logging
import time
import types
import uuid

from sqlalchemy import select

from ..db.models import ToolExecution, TraceLink
from ..db.session import transaction
from ..repositories import investigations

log = logging.getLogger("sre_platform.langsmith")
TOOL = "langsmith:trace"
_PLACEHOLDER = "00000000-0000-0000-0000-00000000beef"
_templates: dict[str, str] = {}


def tracer(rt):
    """Runtime'daki LangSmith tracer'ı (yoksa None)."""
    for cb in getattr(rt, "callbacks", None) or []:
        if getattr(cb, "client", None) is not None and getattr(cb, "project_name", None):
            return cb
    return None


def _template(t) -> str:
    pid = t.client.read_project(project_name=t.project_name).id
    url = t.client.get_run_url(run=types.SimpleNamespace(id=_PLACEHOLDER, session_id=pid))
    return url.replace(_PLACEHOLDER, "{run_id}")


async def run_url(rt, run_id: uuid.UUID) -> str | None:
    t = tracer(rt)
    if t is None:
        return None
    tmpl = _templates.get(t.project_name)
    if tmpl is None:
        try:
            tmpl = await asyncio.to_thread(_template, t)
        except Exception as e:  # noqa: BLE001
            log.warning("LangSmith proje adresi alınamadı: %s", type(e).__name__)
            return None
        _templates[t.project_name] = tmpl
    return tmpl.format(run_id=run_id)


async def has_trace(s, inv_id: uuid.UUID) -> bool:
    if await s.scalar(select(TraceLink.id).where(TraceLink.investigation_id == inv_id).limit(1)):
        return True
    return bool(await s.scalar(select(ToolExecution.id).where(ToolExecution.investigation_id == inv_id,
                                                               ToolExecution.tool_name == TOOL).limit(1)))


def _uuid(v) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v)) if v else None
    except ValueError:
        return None


async def record_link(rt, *, platform_run_id: str, kind: str, run_id, investigation_id: uuid.UUID | None = None,
                      conversation_id: uuid.UUID | None = None, thread_id: str | None = None,
                      trigger: str | None = None, mode: str | None = None, resumed: bool = False) -> dict | None:
    """Çalıştırma girişiminin LangSmith eşlemesini yazar (tracer yoksa yazılmaz). Asla exception fırlatmaz.
    trace_id tracer'ın gördüğü kök trace'tir (sohbetteki analiz için sohbet turunun trace'i)."""
    t = tracer(rt)
    if t is None:
        return None
    try:
        from airlab_agent.tracing import trace_id_for
        trace_id = _uuid(trace_id_for([t], run_id))
        async with transaction(rt.sm) as s:
            n = await s.scalar(select(TraceLink.attempt).where(TraceLink.platform_run_id == platform_run_id)
                               .order_by(TraceLink.attempt.desc()).limit(1))
            row = TraceLink(platform_run_id=platform_run_id, kind=kind, attempt=(n or 0) + 1,
                            investigation_id=investigation_id, conversation_id=conversation_id, thread_id=thread_id,
                            langsmith_run_id=_uuid(run_id), langsmith_trace_id=trace_id, project=t.project_name,
                            trigger=trigger, mode=mode, resumed=resumed)
            s.add(row)
            await s.flush()
            return {"attempt": row.attempt, "trace_id": str(trace_id) if trace_id else None}
    except Exception:  # noqa: BLE001
        log.exception("LangSmith trace eşlemesi yazılamadı (%s)", platform_run_id)
        return None


async def previous_attempts(s, platform_run_id: str) -> int:
    return int(await s.scalar(select(TraceLink.attempt).where(TraceLink.platform_run_id == platform_run_id)
                              .order_by(TraceLink.attempt.desc()).limit(1)) or 0)


async def trace_info(rt, s, inv_id: uuid.UUID) -> dict:
    """Investigation'ın trace linki + platform run_id ↔ trace_id eşlemeleri + MCP çağrısı link şablonu."""
    empty = {"url": None, "platform_run_id": None, "links": [], "call_url_template": None}
    if not await has_trace(s, inv_id):
        return empty
    rows = (await s.execute(select(TraceLink).where(TraceLink.investigation_id == inv_id)
                            .order_by(TraceLink.attempt))).scalars().all()
    url = await run_url(rt, rows[-1].langsmith_run_id if rows else inv_id)
    if url is None:
        return empty
    return {"url": url, "platform_run_id": rows[-1].platform_run_id if rows else None,
            "links": [{"attempt": r.attempt, "kind": r.kind, "langsmith_run_id": str(r.langsmith_run_id),
                       "langsmith_trace_id": str(r.langsmith_trace_id) if r.langsmith_trace_id else None,
                       "project": r.project, "trigger": r.trigger, "resumed": r.resumed,
                       "created_at": r.created_at.isoformat()} for r in rows],
            "call_url_template": url.replace(str(rows[-1].langsmith_run_id if rows else inv_id), "{call_id}")}


def outcome_feedback(inv) -> list[tuple[str, dict]]:
    """(key, kwargs) listesi. Değeri olmayan alan yazılmaz."""
    out: list[tuple[str, dict]] = []
    if inv.risk_score is not None:
        out.append(("risk_score", {"score": float(inv.risk_score)}))
    sev = getattr(inv.severity, "value", inv.severity)
    if sev:
        out.append(("severity", {"value": str(sev)}))
    if inv.primary_mechanism:
        out.append(("mechanism", {"value": inv.primary_mechanism}))
    if inv.data_coverage is not None:
        out.append(("data_coverage", {"score": float(inv.data_coverage)}))
    status = getattr(inv.status, "value", inv.status)
    out.append(("partial_data", {"score": 1 if str(status) == "PARTIAL" else 0}))
    return out


def _send(t, inv_id: uuid.UUID, items: list[tuple[str, dict]]) -> None:
    t.wait_for_futures()            # run kaydı LangSmith'e gitmeden feedback yazılmasın
    t.client.flush()
    for key, kw in items:
        t.client.create_feedback(run_id=inv_id, key=key, **kw)


async def record_outcome(rt, inv_id: uuid.UUID) -> bool:
    """Bitmiş investigation'ın sonuç değerlerini trace'ine feedback olarak yazar. Asla exception fırlatmaz."""
    t = tracer(rt)
    if t is None:
        return False
    try:
        async with transaction(rt.sm) as s:
            inv = await investigations.get(s, inv_id)
            items = outcome_feedback(inv)
        t0, error = time.monotonic(), None
        try:
            await asyncio.wait_for(asyncio.to_thread(_send, t, inv_id, items), 30)
        except Exception as e:  # noqa: BLE001
            error = type(e).__name__
        async with transaction(rt.sm) as s:
            await investigations.record_tool(s, inv_id, TOOL, ok=error is None, step="trace",
                                             arguments={"project": t.project_name, "feedback": [k for k, _ in items]},
                                             duration_ms=int((time.monotonic() - t0) * 1000), error=error)
        if error:
            log.warning("LangSmith feedback yazılamadı (%s): %s", inv_id, error)
        return error is None
    except Exception:  # noqa: BLE001
        log.exception("LangSmith sonucu kaydedilemedi (%s)", inv_id)
        return False
