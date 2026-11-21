"""Human-in-the-loop onay kayıtları. Agent salt-okunurdur: onaylanan eylem bu sürümde OTOMATİK ÇALIŞTIRILMAZ;
onay, bir insanın runbook'u uygulamasına yetki kaydıdır. Dört göz ilkesi: isteyen kendi isteğini onaylayamaz."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from airlab_agent.redact import redact, redact_text

from ..db.models import ActionType, ApprovalRequest, ApprovalStatus
from .base import Conflict, InvalidTransition, NotFound, Page, order_by, paginate


async def request(s: AsyncSession, *, action_type: ActionType, target: str, justification: str,
                  requested_by: uuid.UUID, investigation_id: uuid.UUID | None = None,
                  incident_id: uuid.UUID | None = None, runbook_id: uuid.UUID | None = None,
                  payload: dict | None = None, ttl_hours: int = 24) -> ApprovalRequest:
    a = ApprovalRequest(action_type=action_type, target=redact_text(target)[:200],
                        justification=redact_text(justification), requested_by=requested_by,
                        investigation_id=investigation_id, incident_id=incident_id, runbook_id=runbook_id,
                        payload=redact(payload or {}), expires_at=datetime.now(timezone.utc) + timedelta(hours=ttl_hours))
    s.add(a)
    await s.flush()
    return a


async def get(s: AsyncSession, aid: uuid.UUID) -> ApprovalRequest:
    a = await s.get(ApprovalRequest, aid)
    if a is None:
        raise NotFound("onay isteği bulunamadı")
    return a


async def expire_due(s: AsyncSession) -> int:
    res = await s.execute(update(ApprovalRequest).where(ApprovalRequest.status == ApprovalStatus.PENDING,
                                                        ApprovalRequest.expires_at < datetime.now(timezone.utc))
                          .values(status=ApprovalStatus.EXPIRED))
    return res.rowcount or 0


async def decide(s: AsyncSession, aid: uuid.UUID, *, approve: bool, decided_by: uuid.UUID,
                 comment: str | None = None) -> ApprovalRequest:
    await expire_due(s)
    a = (await s.execute(select(ApprovalRequest).where(ApprovalRequest.id == aid).with_for_update())).scalar_one_or_none()
    if a is None:
        raise NotFound("onay isteği bulunamadı")
    if a.status != ApprovalStatus.PENDING:
        raise InvalidTransition(f"istek zaten {a.status.value}")
    if a.requested_by == decided_by:
        raise Conflict("dört göz ilkesi: isteği açan kişi kendi isteğine karar veremez")
    a.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
    a.decided_by, a.decided_at = decided_by, datetime.now(timezone.utc)
    a.decision_comment = redact_text(comment) if comment else None
    await s.flush()
    return a


async def cancel(s: AsyncSession, aid: uuid.UUID, user_id: uuid.UUID) -> ApprovalRequest:
    a = await get(s, aid)
    if a.requested_by != user_id:
        raise Conflict("yalnız isteği açan kişi iptal edebilir")
    if a.status != ApprovalStatus.PENDING:
        raise InvalidTransition(f"istek zaten {a.status.value}")
    a.status = ApprovalStatus.CANCELLED
    await s.flush()
    return a


async def list_(s: AsyncSession, *, status: list[ApprovalStatus] | None = None, page: int = 1, size: int = 25,
                sort: str | None = None) -> Page[ApprovalRequest]:
    stmt = select(ApprovalRequest)
    if status:
        stmt = stmt.where(ApprovalRequest.status.in_(status))
    stmt = order_by(stmt, sort, {"created_at": ApprovalRequest.created_at, "status": ApprovalRequest.status},
                    "-created_at")
    return await paginate(s, stmt, page, size)
