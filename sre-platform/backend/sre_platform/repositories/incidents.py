"""Incident yaşam döngüsü: OPEN → INVESTIGATING → MITIGATED → RESOLVED (+ zaman çizelgesi).

Kurallar burada (backend) uygulanır; ön yüz yalnız izinli geçişleri gösterir.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from airlab_agent.redact import redact_text

from ..db.models import (Incident, IncidentEvent, IncidentInvestigation, IncidentSource, IncidentStatus, Investigation,
                         Severity)
from . import episodes
from .base import Conflict, InvalidTransition, NotFound, Page, order_by, paginate, ts_match, ts_rank

S = IncidentStatus
TRANSITIONS: dict[IncidentStatus, set[IncidentStatus]] = {
    S.OPEN: {S.INVESTIGATING, S.MITIGATED, S.RESOLVED},
    S.INVESTIGATING: {S.MITIGATED, S.RESOLVED},
    S.MITIGATED: {S.INVESTIGATING, S.RESOLVED},       # azaltma tutmadıysa yeniden araştırma
    S.RESOLVED: {S.INVESTIGATING},                     # yeniden açma (aynı kayıt, zaman çizelgesinde görünür)
}


def allowed_transitions(st: IncidentStatus) -> list[IncidentStatus]:
    return sorted(TRANSITIONS[st], key=list(S).index)


async def create(s: AsyncSession, *, title: str, source: IncidentSource, severity: Severity = Severity.UNKNOWN,
                 description: str | None = None, application_id: str | None = None,
                 investigation_id: uuid.UUID | None = None, dedup_key: str | None = None,
                 conditions: list[str] | None = None, created_by: uuid.UUID | None = None,
                 detected_at: datetime | None = None) -> Incident:
    inc = Incident(title=redact_text(title)[:300], description=redact_text(description) if description else None,
                   source=source, severity=severity, application_id=application_id, investigation_id=investigation_id,
                   dedup_key=dedup_key, conditions=conditions or [], created_by=created_by,
                   detected_at=detected_at or datetime.now(timezone.utc))
    try:
        async with s.begin_nested():
            s.add(inc)
            await s.flush()
    except IntegrityError:
        raise Conflict(f"aynı anahtarla açık incident var: {dedup_key}") from None
    s.add(IncidentEvent(incident_id=inc.id, kind="status", to_status=S.OPEN.value, user_id=created_by,
                        message=f"Incident açıldı ({source.value})",
                        **({"at": detected_at} if detected_at else {})))   # zaman çizelgesi tespit anından başlar
    if investigation_id:
        await link_investigation(s, inc.id, investigation_id, relation="trigger", user_id=created_by, log=False)
    await s.flush()
    return inc


async def open_by_dedup(s: AsyncSession, dedup_key: str) -> Incident | None:
    return (await s.execute(select(Incident).where(Incident.dedup_key == dedup_key, Incident.status != S.RESOLVED))
            ).scalar_one_or_none()


async def get(s: AsyncSession, inc_id: uuid.UUID, *, with_events: bool = False) -> Incident:
    stmt = select(Incident).where(Incident.id == inc_id)
    if with_events:
        stmt = stmt.options(selectinload(Incident.events))
    inc = (await s.execute(stmt)).scalar_one_or_none()
    if inc is None:
        raise NotFound("incident bulunamadı")
    return inc


async def transition(s: AsyncSession, inc: Incident, to: IncidentStatus, *, user_id: uuid.UUID | None = None,
                     note: str | None = None, at: datetime | None = None) -> Incident:
    if to == inc.status:
        return inc
    if to not in TRANSITIONS[inc.status]:
        raise InvalidTransition(f"{inc.status.value} → {to.value} geçişine izin yok "
                                f"(izinli: {', '.join(x.value for x in allowed_transitions(inc.status)) or 'yok'})")
    now = at or datetime.now(timezone.utc)
    frm = inc.status
    try:
        async with s.begin_nested():            # çakışmada savepoint geri alınır, nesne eski haline yüklenir
            if to == S.INVESTIGATING and frm == S.RESOLVED:
                inc.resolved_at = None
                inc.mitigated_at = None
            if to in (S.INVESTIGATING, S.MITIGATED, S.RESOLVED) and inc.acknowledged_at is None:
                inc.acknowledged_at = now
            if to == S.MITIGATED:
                inc.mitigated_at = now
            if to == S.RESOLVED:
                inc.resolved_at = max(now, inc.detected_at)
            inc.status = to
            s.add(IncidentEvent(incident_id=inc.id, kind="status", from_status=frm.value, to_status=to.value,
                                user_id=user_id, message=redact_text(note) if note else None, at=now))
            await s.flush()
    except IntegrityError:
        await s.refresh(inc)
        raise Conflict("aynı anahtarla başka bir açık incident var; yeniden açılamaz") from None
    await episodes.safe_refresh(s, incident=inc)          # çözüm durumu episodic memory'ye
    return inc


async def update_fields(s: AsyncSession, inc: Incident, *, user_id: uuid.UUID | None = None, **fields) -> Incident:
    changed = []
    for k in ("title", "description", "severity", "root_cause", "recommended_actions", "application_id"):
        if k in fields and fields[k] is not None:
            v = fields[k]
            if isinstance(v, str):
                v = redact_text(v)
            if k == "recommended_actions":
                v = [redact_text(x) for x in v]
            if getattr(inc, k) != v:
                setattr(inc, k, v)
                changed.append(k)
    if changed:
        s.add(IncidentEvent(incident_id=inc.id, kind="update", user_id=user_id,
                            message="Güncellendi: " + ", ".join(changed)))
    await s.flush()
    if {"root_cause", "recommended_actions"} & set(changed):
        await episodes.safe_refresh(s, incident=inc)
    return inc


async def add_note(s: AsyncSession, inc_id: uuid.UUID, message: str, user_id: uuid.UUID | None = None,
                   kind: str = "note") -> IncidentEvent:
    ev = IncidentEvent(incident_id=inc_id, kind=kind, user_id=user_id, message=redact_text(message)[:4000])
    s.add(ev)
    await s.flush()
    return ev


async def link_investigation(s: AsyncSession, inc_id: uuid.UUID, inv_id: uuid.UUID, *, relation: str = "related",
                             user_id: uuid.UUID | None = None, log: bool = True) -> None:
    stmt = insert(IncidentInvestigation).values(incident_id=inc_id, investigation_id=inv_id, relation=relation)
    res = await s.execute(stmt.on_conflict_do_nothing())
    if log and res.rowcount:
        s.add(IncidentEvent(incident_id=inc_id, kind="investigation", user_id=user_id,
                            message=f"Araştırma bağlandı ({relation})"))
    await s.flush()
    if res.rowcount:
        await episodes.safe_refresh(s, inv_id=inv_id)


async def investigations_of(s: AsyncSession, inc_id: uuid.UUID) -> list[tuple[Investigation, str]]:
    stmt = (select(Investigation, IncidentInvestigation.relation).join(
        IncidentInvestigation, IncidentInvestigation.investigation_id == Investigation.id)
        .where(IncidentInvestigation.incident_id == inc_id).order_by(Investigation.created_at))
    return [(i, r) for i, r in (await s.execute(stmt)).all()]


SORTS = {"detected_at": Incident.detected_at, "severity": Incident.severity, "status": Incident.status,
         "updated_at": Incident.updated_at, "title": Incident.title}


async def list_(s: AsyncSession, *, status: list[IncidentStatus] | None = None, severity: list[Severity] | None = None,
                application_id: str | None = None, q: str | None = None, since: datetime | None = None,
                until: datetime | None = None, page: int = 1, size: int = 25, sort: str | None = None) -> Page[Incident]:
    stmt = select(Incident)
    if status:
        stmt = stmt.where(Incident.status.in_(status))
    if severity:
        stmt = stmt.where(Incident.severity.in_(severity))
    if application_id:
        stmt = stmt.where(Incident.application_id == application_id)
    if since:
        stmt = stmt.where(Incident.detected_at >= since)
    if until:
        stmt = stmt.where(Incident.detected_at < until)
    if q:
        stmt = stmt.where(ts_match(Incident.tsv, q))
        if not sort:
            stmt = stmt.order_by(ts_rank(Incident.tsv, q).desc())
    if not (q and not sort):
        stmt = order_by(stmt, sort, SORTS, "-detected_at")
    return await paginate(s, stmt, page, size)


async def counts(s: AsyncSession) -> dict[str, int]:
    rows = (await s.execute(select(Incident.status, func.count()).group_by(Incident.status))).all()
    return {st.value: n for st, n in rows}
