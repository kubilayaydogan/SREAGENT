"""Context Windows API (`/api/context-windows/...`): LLM çağrısı başına context window kullanımı (salt okunur).

Okuma `observability:read` (SRE, admin). Sohbet turu kayıtları yalnız sahibine ve admin'e görünür; görünmeyen kayıt
404 döner. Ham prompt / yanıt / MCP payload'ı tutulmaz ve döndürülmez.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ...context_window import queries as q
from ...db.models import Role, User
from ...security.permissions import P
from ..deps import db
from ..deps import require_perm as perm

router = APIRouter(prefix="/context-windows", tags=["context-windows"])
Range = Literal["1h", "24h", "7d", "30d"]
Status = Literal["ok", "error", "context_limit", "retried", "cancelled"]


def _viewer(u: User) -> q.Viewer:
    return (str(u.id), getattr(u, "role", None) == Role.ADMIN)


@router.get("/overview")
async def overview(range: Range = "24h", u: User = Depends(perm(P.OBSERVABILITY_READ)),
                   s: AsyncSession = Depends(db)) -> dict:
    return await q.overview(s, _viewer(u), range)


@router.get("/facets")
async def facets(range: Range = "24h", u: User = Depends(perm(P.OBSERVABILITY_READ)),
                 s: AsyncSession = Depends(db)) -> dict:
    return await q.facets(s, _viewer(u), range)


@router.get("/calls")
async def calls(range: Range = "24h", run_id: str | None = Query(None, max_length=160),
                node: str | None = Query(None, max_length=64), model: str | None = Query(None, max_length=128),
                status: Status | None = None, sort: Literal["time", "fill", "sequence"] = "time",
                limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                u: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)) -> dict:
    return await q.calls(s, _viewer(u), rng=range, run_id=run_id, node=node, model=model, status=status, sort=sort,
                         limit=limit, offset=offset)


@router.get("/calls/{call_id}")
async def call(call_id: str, u: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)) -> dict:
    try:
        return await q.call(s, call_id, _viewer(u))
    except q.NotFound:
        raise HTTPException(404, "çağrı bulunamadı") from None
