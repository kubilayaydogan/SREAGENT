"""RCA raporları ve kullanıcı geri bildirimi."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from airlab_agent.redact import redact, redact_text

from ..db.models import AgentFeedback, Confidence, RcaReport, Verdict
from .base import NotFound, Page, order_by, paginate, ts_match, ts_rank


async def create(s: AsyncSession, inv_id: uuid.UUID, data: dict[str, Any]) -> RcaReport:
    txt = lambda k: redact_text(data[k]) if data.get(k) else None  # noqa: E731
    r = RcaReport(investigation_id=inv_id, incident_id=data.get("incident_id"), application_id=data.get("application_id"),
                  title=redact_text(data["title"])[:300], summary=redact_text(data["summary"]),
                  problem_description=txt("problem_description"), timeline=redact(data.get("timeline") or []),
                  impact_analysis=txt("impact_analysis"), suspected_root_cause=txt("suspected_root_cause"),
                  supporting_evidence=redact(data.get("supporting_evidence") or []),
                  contributing_factors=[redact_text(x) for x in data.get("contributing_factors") or []],
                  recommended_actions=[redact_text(x) for x in data.get("recommended_actions") or []],
                  alternatives=[redact_text(x) for x in data.get("alternatives") or []],
                  data_gaps=[redact_text(x) for x in data.get("data_gaps") or []],
                  confidence=Confidence(data["confidence"]) if data.get("confidence") else None,
                  uncertainty=txt("uncertainty"), markdown=redact_text(data["markdown"]),
                  generated_by=data.get("generated_by", "deterministik"))
    s.add(r)
    await s.flush()
    return r


async def get(s: AsyncSession, rca_id: uuid.UUID) -> RcaReport:
    r = await s.get(RcaReport, rca_id)
    if r is None:
        raise NotFound("RCA raporu bulunamadı")
    return r


async def latest_for(s: AsyncSession, inv_id: uuid.UUID) -> RcaReport | None:
    return (await s.execute(select(RcaReport).where(RcaReport.investigation_id == inv_id)
                            .order_by(RcaReport.generated_at.desc()).limit(1))).scalar_one_or_none()


SORTS = {"generated_at": RcaReport.generated_at, "confidence": RcaReport.confidence, "title": RcaReport.title}


async def list_(s: AsyncSession, *, application_id: str | None = None, incident_id: uuid.UUID | None = None,
                investigation_id: uuid.UUID | None = None, q: str | None = None, since: datetime | None = None,
                page: int = 1, size: int = 25, sort: str | None = None) -> Page[RcaReport]:
    stmt = select(RcaReport)
    if application_id:
        stmt = stmt.where(RcaReport.application_id == application_id)
    if incident_id:
        stmt = stmt.where(RcaReport.incident_id == incident_id)
    if investigation_id:
        stmt = stmt.where(RcaReport.investigation_id == investigation_id)
    if since:
        stmt = stmt.where(RcaReport.generated_at >= since)
    if q:
        stmt = stmt.where(ts_match(RcaReport.tsv, q))
        if not sort:
            stmt = stmt.order_by(ts_rank(RcaReport.tsv, q).desc())
    if not (q and not sort):
        stmt = order_by(stmt, sort, SORTS, "-generated_at")
    return await paginate(s, stmt, page, size)


# ---------------------------------------------------------------- geri bildirim
async def upsert_feedback(s: AsyncSession, *, inv_id: uuid.UUID, user_id: uuid.UUID, verdict: Verdict,
                          rating: int | None = None, comment: str | None = None,
                          rca_id: uuid.UUID | None = None) -> AgentFeedback:
    """Kullanıcı başına araştırma başına tek değerlendirme; tekrar gönderilirse güncellenir."""
    stmt = insert(AgentFeedback).values(investigation_id=inv_id, user_id=user_id, verdict=verdict, rating=rating,
                                        comment=redact_text(comment)[:4000] if comment else None, rca_id=rca_id)
    stmt = stmt.on_conflict_do_update(constraint="uq_feedback_user",
                                      set_={"verdict": stmt.excluded.verdict, "rating": stmt.excluded.rating,
                                            "comment": stmt.excluded.comment, "rca_id": stmt.excluded.rca_id,
                                            "updated_at": func.now()}).returning(AgentFeedback.id)
    fid = (await s.execute(stmt)).scalar_one()
    from . import episodes                            # feedback → episode outcome (confirmed / rejected …)
    await episodes.safe_refresh(s, inv_id=inv_id)
    return await s.get(AgentFeedback, fid, populate_existing=True)


async def feedback_for(s: AsyncSession, inv_id: uuid.UUID) -> list[AgentFeedback]:
    return list((await s.execute(select(AgentFeedback).where(AgentFeedback.investigation_id == inv_id)
                                 .order_by(AgentFeedback.created_at))).scalars())


async def feedback_summary(s: AsyncSession) -> dict[str, int]:
    rows = (await s.execute(select(AgentFeedback.verdict, func.count()).group_by(AgentFeedback.verdict))).all()
    return {v.value: n for v, n in rows}
