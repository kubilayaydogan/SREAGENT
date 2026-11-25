"""Araştırmalar, kanıtlar ve araç (tool) çalıştırma geçmişi.

Rapor → kayıt eşlemesi deterministiktir: airlab_agent raporundaki (zaten redakte) alanlar sütunlara yazılır;
ham telemetri kaydı (records) saklanmaz, yalnız kanıt özeti + DQL + Dynatrace queryId.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from airlab_agent.redact import redact, redact_text

from ..db.models import (Application, Confidence, EvidenceType, Investigation, InvestigationEvidence, InvestigationStatus, Severity,
                         ToolExecution, ToolStatus, Trigger)
from .base import NotFound, Page, order_by, paginate, ts_match, ts_rank

BAND_TO_SEVERITY = {"düşük": Severity.LOW, "orta": Severity.MEDIUM, "yüksek": Severity.HIGH, "kritik": Severity.CRITICAL,
                    None: Severity.UNKNOWN}
SIGNAL_TYPE = {"spans": EvidenceType.TRACE, "edges": EvidenceType.LOG, "pg_lock_logs": EvidenceType.LOG,
               "edges_detail": EvidenceType.LOG, "top_statements": EvidenceType.LOG, "problems": EvidenceType.PROBLEM,
               "slow_traces": EvidenceType.TRACE, "events": EvidenceType.EVENT, "topology": EvidenceType.TOPOLOGY}
SIGNAL_METRIC = {"http": "airlab.app.http.server.requests", "hikari": "airlab.app.hikaricp.connections.*",
                 "jvm_gc": "airlab.app.jvm.gc.pause", "jvm_heap": "airlab.app.jvm.memory.used",
                 "hosts": "dt.host.cpu.usage", "containers": "dt.containers.cpu.*",
                 "db_conn": "airlab.pg.connections.*", "db_sessions": "airlab.pg.sessions",
                 "lock_waiters": "airlab.pg.lock.waiters", "collector": "airlab.pg.collector.up",
                 "db_stats": "airlab.pg.db.*", "statements": "airlab.pg.statements.*"}
STATUS_MAP = {"complete": InvestigationStatus.COMPLETED, "partial": InvestigationStatus.PARTIAL}


def _ts(v: str | datetime | None) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def evidence_type(signal: str | None) -> EvidenceType:
    return SIGNAL_TYPE.get(signal or "", EvidenceType.METRIC)


async def create(s: AsyncSession, *, run_id: str, trigger: Trigger, title: str, window_start: datetime,
                 window_end: datetime, user_id: uuid.UUID | None = None, conversation_id: uuid.UUID | None = None,
                 application_id: str | None = None, description: str | None = None,
                 baseline: tuple[datetime, datetime] | None = None) -> Investigation:
    inv = Investigation(run_id=run_id, trigger=trigger, title=redact_text(title)[:300],
                        description=redact_text(description) if description else None,
                        window_start=window_start, window_end=window_end, user_id=user_id,
                        conversation_id=conversation_id, application_id=application_id,
                        baseline_start=baseline[0] if baseline else None, baseline_end=baseline[1] if baseline else None,
                        status=InvestigationStatus.PENDING)
    s.add(inv)
    await s.flush()
    return inv


async def get(s: AsyncSession, inv_id: uuid.UUID, *, with_children: bool = False) -> Investigation:
    stmt = select(Investigation).where(Investigation.id == inv_id)
    if with_children:
        stmt = stmt.options(selectinload(Investigation.evidence), selectinload(Investigation.tools))
    inv = (await s.execute(stmt)).scalar_one_or_none()
    if inv is None:
        raise NotFound("araştırma bulunamadı")
    return inv


async def by_run_id(s: AsyncSession, run_id: str) -> Investigation | None:
    return (await s.execute(select(Investigation).where(Investigation.run_id == run_id))).scalar_one_or_none()


async def mark_running(s: AsyncSession, inv: Investigation) -> None:
    inv.status = InvestigationStatus.RUNNING
    inv.started_at = datetime.now(timezone.utc)
    await s.flush()


async def tool_started(s: AsyncSession, inv_id: uuid.UUID, tool_name: str, *, step: str | None = None,
                       signal: str | None = None, arguments: dict[str, Any] | None = None,
                       id: uuid.UUID | None = None) -> ToolExecution:
    """`id` verilirse kaydın kimliği odur (MCP çağrısında gateway'in mcp_call_id'si = LangSmith span kimliği)."""
    t = ToolExecution(**({"id": id} if id else {}), investigation_id=inv_id, tool_name=tool_name, step=step,
                      signal=signal,
                      tool_arguments=redact(arguments or {}), execution_status=ToolStatus.STARTED,
                      started_at=datetime.now(timezone.utc))
    s.add(t)
    await s.flush()
    return t


async def tool_finished(s: AsyncSession, t: ToolExecution, *, ok: bool, duration_ms: int | None = None,
                        result_status: str | None = None, record_count: int | None = None,
                        query_id: str | None = None, evidence_key: str | None = None, error: str | None = None,
                        skipped: bool = False) -> ToolExecution:
    t.execution_status = ToolStatus.SKIPPED if skipped else (ToolStatus.SUCCEEDED if ok else ToolStatus.FAILED)
    if duration_ms is None and t.started_at is not None:
        duration_ms = int((datetime.now(timezone.utc) - t.started_at).total_seconds() * 1000)
    t.duration_ms = max(0, duration_ms) if duration_ms is not None else None
    t.result_status, t.record_count, t.dynatrace_query_id, t.evidence_key = result_status, record_count, query_id, evidence_key
    t.error_message = redact_text(error)[:2000] if error else None
    await s.flush()
    return t


async def record_tool(s: AsyncSession, inv_id: uuid.UUID, tool_name: str, *, ok: bool, step: str | None = None,
                      signal: str | None = None, arguments: dict | None = None, duration_ms: int | None = None,
                      result_status: str | None = None, record_count: int | None = None, query_id: str | None = None,
                      evidence_key: str | None = None, error: str | None = None, skipped: bool = False,
                      id: uuid.UUID | None = None) -> ToolExecution:
    t = await tool_started(s, inv_id, tool_name, step=step, signal=signal, arguments=arguments, id=id)
    return await tool_finished(s, t, ok=ok, duration_ms=duration_ms, result_status=result_status,
                               record_count=record_count, query_id=query_id, evidence_key=evidence_key, error=error,
                               skipped=skipped)


async def upsert_evidence(s: AsyncSession, inv_id: uuid.UUID, items: list[dict[str, Any]]) -> int:
    """items: {key, type, source, signal, data_status, content, query_reference, dynatrace_query_id, application_id,
    entity_id, trace_id, span_id, metric_name, timestamp, meta}. Aynı (araştırma, key) tekrar yazılırsa güncellenir."""
    if not items:
        return 0
    rows = []
    for e in items:
        rows.append({
            "investigation_id": inv_id, "evidence_key": e["key"],
            "evidence_type": e.get("type") or evidence_type(e.get("signal")),
            "source": e.get("source", "dynatrace-mcp:execute-dql"), "signal": e.get("signal"),
            "data_status": e.get("data_status"), "application_id": e.get("application_id"),
            "entity_id": e.get("entity_id"), "trace_id": e.get("trace_id"), "span_id": e.get("span_id"),
            "metric_name": e.get("metric_name") or SIGNAL_METRIC.get(e.get("signal") or ""),
            "query_reference": e.get("query_reference"), "dynatrace_query_id": e.get("dynatrace_query_id"),
            "timestamp": _ts(e.get("timestamp")),
            "content": redact_text(e["content"])[:4000] if e.get("content") else None,
            "metadata": redact(e.get("meta") or {}),
        })
    stmt = insert(InvestigationEvidence).values(rows)
    cols = [c for c in rows[0] if c not in ("investigation_id", "evidence_key")]
    stmt = stmt.on_conflict_do_update(constraint="uq_evidence_key", set_={c: stmt.excluded[c] for c in cols})
    await s.execute(stmt)
    return len(rows)


def evidence_from_report(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Rapordaki kanıt listesi (+ ek kanıt kayıtlarından trace/edge özetleri) → kanıt satırları."""
    windows = {w["name"]: w for w in report.get("windows") or []}
    target = windows.get("window", {})
    used_by: dict[str, list[str]] = {}
    for m in report.get("mechanisms", []):
        for ev in m.get("evidence_ids", []):
            used_by.setdefault(ev, []).append(m["id"])
    out = []
    for e in report.get("evidence", []):
        status = e.get("status")
        desc = {"OK": f"{e.get('records', 0)} kayıt", "EMPTY": "kayıt yok", "PERMISSION": "yetki hatası",
                "UNAVAILABLE": "MCP erişilemedi", "SKIPPED_BUDGET": "bütçe doldu"}.get(status, status or "?")
        mechs = used_by.get(e["id"], [])
        content = f"{e['signal']}: {desc}" + (f" — destekler: {', '.join(mechs)}" if mechs else "")
        if e.get("error"):
            content += f" — hata: {e['error'][:300]}"
        out.append({"key": e["id"], "signal": e["signal"], "data_status": status, "content": content,
                    "query_reference": e.get("dql"), "dynatrace_query_id": e.get("dynatrace_query_id"),
                    "timestamp": target.get("end"), "meta": {"records": e.get("records"), "mechanisms": mechs}})
    for k, x in (report.get("extra_evidence") or {}).items():
        recs = x.get("records") or []
        if k == "slow_traces":
            for r in recs[:10]:
                if r.get("trace.id"):
                    out.append({"key": f"tr-{str(r['trace.id'])[:24]}", "type": EvidenceType.TRACE, "signal": k,
                                "trace_id": str(r["trace.id"])[:64], "span_id": str(r.get("span.id") or "")[:32] or None,
                                "application_id": r.get("app"), "data_status": "OK",
                                "timestamp": r.get("start_time") or target.get("end"),
                                "content": f"Yavaş istek {r.get('app')} {r.get('endpoint') or ''} "
                                           f"{r.get('duration_ms')} ms", "meta": {"parent": x.get("evidence_id")}})
    return out


async def complete(s: AsyncSession, inv: Investigation, report: dict[str, Any], *,
                   failed: bool = False, error: str | None = None) -> Investigation:
    """Agent raporunu araştırma kaydına yazar (deterministik eşleme)."""
    caus = report.get("causality") or {}
    overall = (report.get("risk") or {}).get("overall") or {}
    cov = (report.get("data_coverage") or {}).get("ratio")
    primary = next((m for m in report.get("mechanisms", []) if m["id"] == caus.get("primary_mechanism")), None)
    inv.status = (InvestigationStatus.FAILED if failed else
                  STATUS_MAP.get(report.get("status", "complete"), InvestigationStatus.COMPLETED))
    inv.severity = BAND_TO_SEVERITY.get(overall.get("band"), Severity.UNKNOWN)
    inv.risk_score = Decimal(str(overall["score"])) if overall.get("score") is not None else None
    inv.data_coverage = Decimal(str(cov)) if cov is not None else None
    inv.confidence = Confidence(caus["confidence"]) if caus.get("confidence") else None
    inv.primary_mechanism = caus.get("primary_mechanism")
    inv.source_apps = list(caus.get("source_apps") or [])
    inv.affected_apps = list(caus.get("affected_apps") or [])
    if primary:
        inv.root_cause_hypothesis = redact_text(f"{primary['title']}: {primary.get('mechanism', '')}")
    elif report.get("impacted"):
        inv.root_cause_hypothesis = "Impact var ancak tanımlı mechanism'lerle açıklanamadı (human review gerekli)."
    else:
        inv.root_cause_hypothesis = None
    inv.explanation = report.get("explanation")
    inv.explanation_source = report.get("explanation_source")
    slim = {k: v for k, v in report.items() if k not in ("explanation",)}
    inv.report = redact(slim)
    inv.error = redact_text(error) if error else ("; ".join(report.get("errors") or []) or None)
    inv.completed_at = datetime.now(timezone.utc)
    if inv.started_at:
        inv.duration_ms = int((inv.completed_at - inv.started_at).total_seconds() * 1000)
    if not inv.application_id:
        apps = inv.source_apps or inv.affected_apps
        if len(apps) == 1 and await s.get(Application, apps[0]) is not None:
            inv.application_id = apps[0]
    await upsert_evidence(s, inv.id, evidence_from_report(report))
    await s.flush()
    if not failed:
        from . import episodes                        # episodic memory (kayda değer koşular)
        await episodes.safe_refresh(s, inv_id=inv.id)
    return inv


async def fail(s: AsyncSession, inv: Investigation, error: str, *, cancelled: bool = False) -> Investigation:
    inv.status = InvestigationStatus.CANCELLED if cancelled else InvestigationStatus.FAILED
    inv.error = redact_text(error)[:4000]
    inv.completed_at = datetime.now(timezone.utc)
    if inv.started_at:
        inv.duration_ms = int((inv.completed_at - inv.started_at).total_seconds() * 1000)
    await s.flush()
    return inv


SORTS = {"created_at": Investigation.created_at, "severity": Investigation.severity,
         "risk_score": Investigation.risk_score, "duration_ms": Investigation.duration_ms,
         "status": Investigation.status, "title": Investigation.title}


async def list_(s: AsyncSession, *, application_id: str | None = None, status: list[InvestigationStatus] | None = None,
                severity: list[Severity] | None = None, mechanism: str | None = None, q: str | None = None,
                since: datetime | None = None, until: datetime | None = None, user_id: uuid.UUID | None = None,
                trigger: Trigger | None = None, page: int = 1, size: int = 25, sort: str | None = None
                ) -> Page[Investigation]:
    stmt = select(Investigation)
    if application_id:
        stmt = stmt.where((Investigation.application_id == application_id)
                          | Investigation.source_apps.contains([application_id])
                          | Investigation.affected_apps.contains([application_id]))
    if status:
        stmt = stmt.where(Investigation.status.in_(status))
    if severity:
        stmt = stmt.where(Investigation.severity.in_(severity))
    if mechanism:
        stmt = stmt.where(Investigation.primary_mechanism == mechanism)
    if since:
        stmt = stmt.where(Investigation.created_at >= since)
    if until:
        stmt = stmt.where(Investigation.created_at < until)
    if user_id:
        stmt = stmt.where(Investigation.user_id == user_id)
    if trigger:
        stmt = stmt.where(Investigation.trigger == trigger)
    if q:
        stmt = stmt.where(ts_match(Investigation.tsv, q))
        if not sort:
            stmt = stmt.order_by(ts_rank(Investigation.tsv, q).desc())
    if not (q and not sort):
        stmt = order_by(stmt, sort, SORTS, "-created_at")
    return await paginate(s, stmt, page, size)


async def similar(s: AsyncSession, *, mechanism: str | None, apps: list[str], exclude_id: uuid.UUID | None = None,
                  q: str | None = None, limit: int = 5) -> list[Investigation]:
    """Geçmişte benzer araştırmalar. Sıralama: aynı mekanizma (3) + uygulama örtüşmesi (2) + metin eşleşmesi (1),
    eşitlikte en yeni. Benzerlik bir ipucudur, aynı kök neden olduğunun kanıtı değildir."""
    done = [InvestigationStatus.COMPLETED, InvestigationStatus.PARTIAL]
    parts, conds = [], []
    if mechanism:
        c = Investigation.primary_mechanism == mechanism
        conds.append(c)
        parts.append(case((c, 3), else_=0))
    if apps:
        c = Investigation.source_apps.overlap(apps) | Investigation.affected_apps.overlap(apps)
        conds.append(c)
        parts.append(case((c, 2), else_=0))
    if q:
        c = ts_match(Investigation.tsv, q)
        conds.append(c)
        parts.append(case((c, 1), else_=0))
    if not conds:
        return []
    score = sum(parts[1:], parts[0])
    stmt = select(Investigation).where(Investigation.status.in_(done), or_(*conds))
    if exclude_id:
        stmt = stmt.where(Investigation.id != exclude_id)
    stmt = stmt.order_by(score.desc(), Investigation.created_at.desc()).limit(limit)
    return list((await s.execute(stmt)).scalars())


async def stats(s: AsyncSession, since: datetime) -> dict[str, Any]:
    rows = (await s.execute(select(Investigation.status, func.count()).where(Investigation.created_at >= since)
                            .group_by(Investigation.status))).all()
    by = {st.value: n for st, n in rows}
    return {"by_status": by, "open": by.get("PENDING", 0) + by.get("RUNNING", 0), "total": sum(by.values())}
