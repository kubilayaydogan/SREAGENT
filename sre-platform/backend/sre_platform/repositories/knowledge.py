"""Bilgi bankası: bilinen sorunlar ve runbook'lar (tam metin araması + mekanizma eşleşmesi)."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import case, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from airlab_agent.redact import redact_text

from ..db.models import KnownIssue, RiskLevel, Runbook
from .base import NotFound, Page, order_by, paginate, ts_match, ts_rank


def _clean(d: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {k: (redact_text(v) if isinstance(v, str) else v) for k, v in d.items() if k in keys}


KI_FIELDS = ("application_id", "mechanism", "title", "description", "symptoms", "root_cause", "resolution", "tags",
             "verified", "origin", "created_by")
RB_FIELDS = ("application_id", "mechanism", "title", "description", "procedure", "risk_level", "requires_approval",
             "tags", "origin")


async def create_known_issue(s: AsyncSession, data: dict[str, Any]) -> KnownIssue:
    k = KnownIssue(**_clean(data, KI_FIELDS))
    s.add(k)
    await s.flush()
    return k


async def create_runbook(s: AsyncSession, data: dict[str, Any]) -> Runbook:
    d = _clean(data, RB_FIELDS)
    if isinstance(d.get("risk_level"), str):
        d["risk_level"] = RiskLevel(d["risk_level"])
    if d.get("risk_level") == RiskLevel.HIGH:
        d["requires_approval"] = True             # yüksek riskli prosedür her zaman onay ister
    r = Runbook(**d)
    s.add(r)
    await s.flush()
    return r


async def seed(s: AsyncSession, items: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    """Tohum kayıtlar: `origin` + başlık ile idempotent (var olan kayda dokunulmaz)."""
    out = {"known_issues": 0, "runbooks": 0}
    for model, key, fields in ((KnownIssue, "known_issues", KI_FIELDS), (Runbook, "runbooks", RB_FIELDS)):
        for it in items.get(key, []):
            exists = (await s.execute(select(model.id).where(model.title == it["title"],
                                                             model.origin == it.get("origin", "seed")))).first()
            if exists:
                continue
            if key == "runbooks":
                await create_runbook(s, it)
            else:
                await create_known_issue(s, it)
            out[key] += 1
    return out


async def get_known_issue(s: AsyncSession, kid: uuid.UUID) -> KnownIssue:
    k = await s.get(KnownIssue, kid)
    if k is None:
        raise NotFound("bilinen sorun bulunamadı")
    return k


async def get_runbook(s: AsyncSession, rid: uuid.UUID) -> Runbook:
    r = await s.get(Runbook, rid)
    if r is None:
        raise NotFound("runbook bulunamadı")
    return r


async def _list(s, model, *, application_id, mechanism, q, page, size, sort, sorts) -> Page:
    stmt = select(model)
    if application_id:
        stmt = stmt.where(or_(model.application_id == application_id, model.application_id.is_(None)))
    if mechanism:
        stmt = stmt.where(model.mechanism == mechanism)
    if q:
        stmt = stmt.where(ts_match(model.tsv, q))
        if not sort:
            stmt = stmt.order_by(ts_rank(model.tsv, q).desc())
    if not (q and not sort):
        stmt = order_by(stmt, sort, sorts, "title")
    return await paginate(s, stmt, page, size)


async def list_known_issues(s: AsyncSession, *, application_id: str | None = None, mechanism: str | None = None,
                            q: str | None = None, page: int = 1, size: int = 25, sort: str | None = None) -> Page:
    return await _list(s, KnownIssue, application_id=application_id, mechanism=mechanism, q=q, page=page, size=size,
                       sort=sort, sorts={"title": KnownIssue.title, "updated_at": KnownIssue.updated_at,
                                         "created_at": KnownIssue.created_at})


async def list_runbooks(s: AsyncSession, *, application_id: str | None = None, mechanism: str | None = None,
                        q: str | None = None, page: int = 1, size: int = 25, sort: str | None = None) -> Page:
    return await _list(s, Runbook, application_id=application_id, mechanism=mechanism, q=q, page=page, size=size,
                       sort=sort, sorts={"title": Runbook.title, "updated_at": Runbook.updated_at,
                                         "risk_level": Runbook.risk_level})


async def recommend(s: AsyncSession, *, mechanisms: list[str], apps: list[str], q: str | None = None,
                    limit: int = 3) -> dict[str, list]:
    """Araştırma sonucuna göre ilgili bilinen sorun + runbook (mekanizma eşleşmesi önce, sonra metin)."""
    out = {}
    for key, model in (("known_issues", KnownIssue), ("runbooks", Runbook)):
        conds, parts = [], []
        if mechanisms:
            c = model.mechanism.in_(mechanisms)
            conds.append(c)
            parts.append(case((c, 3), else_=0))
        if q:
            c = ts_match(model.tsv, q)
            conds.append(c)
            parts.append(case((c, 1), else_=0))
        if not conds:
            out[key] = []
            continue
        stmt = select(model).where(or_(*conds), model.archived_at.is_(None))
        if model is Runbook:                     # procedural memory: agent yalnız onaylı runbook'u görür (0015)
            stmt = stmt.where(Runbook.approval_status == "approved")
        if apps:
            stmt = stmt.where(or_(model.application_id.in_(apps), model.application_id.is_(None)))
        stmt = stmt.order_by(sum(parts[1:], parts[0]).desc(), model.title).limit(limit)
        out[key] = list((await s.execute(stmt)).scalars())
    return out
