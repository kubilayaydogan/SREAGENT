"""Agent Middleware API (`/api/middleware/...`).

Üç kavram ayrı uçlardadır:
* tanım (kodda kayıtlı, salt okunur): `catalog`, `hooks`
* yapılandırma (versiyonlu, audit'li): `PATCH catalog/{name}`, `POST reorder` — `middleware:manage` (admin)
* execution kaydı (salt okunur): `executions`, `runs`, `runs/{run_id}/timeline`

Okuma `observability:read` (SRE, admin). Sohbet turu kayıtları yalnız sahibine ve admin'e görünür; görünmeyen kayıt
404 döner. Dry-run sentetik girdiyle çalışır: gerçek LLM / MCP çağrısı ve yan etki yoktur.
"""
from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from airlab_agent import middleware as mw

from ...db.models import User
from ...middleware import queries as q
from ...middleware import service
from ...security.permissions import P, has
from ...settings import service as settings_service
from ..deps import db
from ..deps import require_perm as perm

router = APIRouter(prefix="/middleware", tags=["agent-middleware"])
Range = Literal["1h", "24h", "7d", "30d"]


def _viewer(u: User) -> q.Viewer:
    from ...db.models import Role
    return (str(u.id), getattr(u, "role", None) == Role.ADMIN)


class ConfigPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=0)
    enabled: bool | None = None
    order: int | None = Field(None, ge=1, le=1000)
    params: dict[str, Any] | None = None
    scope: dict[str, Any] | None = None
    note: str | None = Field(None, max_length=1000)


class ReorderItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=64)
    order: int = Field(ge=1, le=1000)
    version: int = Field(ge=0)


class ReorderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ReorderItem] = Field(min_length=1, max_length=50)
    note: str | None = Field(None, max_length=1000)


class DryRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["model", "tool", "agent"] | None = None
    target: str | None = Field(None, max_length=128)
    task: str | None = Field(None, max_length=128)
    connector: str | None = Field(None, max_length=64)
    dql: str | None = Field(None, max_length=20000)
    messages: list[tuple[str, str]] | None = Field(None, max_length=20)


class DryRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=64)
    hook: str = Field(max_length=32)
    input: DryRunInput = Field(default_factory=DryRunInput)
    params: dict[str, Any] | None = None
    state: dict[str, int] | None = None


@router.get("/overview")
async def overview(range: Range = "24h", _: User = Depends(perm(P.OBSERVABILITY_READ)),
                   s: AsyncSession = Depends(db)) -> dict:
    return await q.overview(s, range)


@router.get("/hooks")
async def hooks(_: User = Depends(perm(P.OBSERVABILITY_READ))) -> dict:
    return {"hooks": [{"hook": h, **mw.HOOK_INFO[h]} for h in mw.HOOKS],
            "unsupported": [{"name": k, "reason": v} for k, v in mw.UNSUPPORTED_HOOKS.items()],
            "kinds": list(mw.KINDS), "decisions": list(mw.DECISIONS),
            "ordering": {"before": "artan order (küçük önce)", "wrap": "iç içe: küçük order en dışta (ilk girer, son "
                         "çıkar)", "after": "azalan order (ters)"},
            "framework": "özel LangGraph StateGraph (create_agent kullanılmıyor); hook'lar adapter ile bağlı"}


@router.get("/settings")
async def mw_settings(request: Request, _: User = Depends(perm(P.OBSERVABILITY_READ)),
                      s: AsyncSession = Depends(db)) -> dict:
    eff = (await settings_service.effective(request.app.state.rt, s)).get("agent_middleware", {})
    return {**mw.DEFAULT_SETTINGS, **eff}


@router.get("/catalog")
async def catalog(range: Range = "24h", u: User = Depends(perm(P.OBSERVABILITY_READ)),
                  s: AsyncSession = Depends(db)) -> dict:
    return {"items": await q.catalog(s, range), "can_manage": has(u.role, P.MIDDLEWARE_MANAGE)}


@router.get("/catalog/{name}")
async def detail(name: str, range: Range = "24h", u: User = Depends(perm(P.OBSERVABILITY_READ)),
                 s: AsyncSession = Depends(db)) -> dict:
    return {**await q.detail(s, name, _viewer(u), range), "can_manage": has(u.role, P.MIDDLEWARE_MANAGE)}


@router.patch("/catalog/{name}")
async def update(name: str, body: ConfigPatch, u: User = Depends(perm(P.MIDDLEWARE_MANAGE)),
                 s: AsyncSession = Depends(db)) -> dict:
    values = body.model_dump(exclude_none=True, exclude={"version", "note"})
    return await service.update(s, name, version=body.version, values=values, actor=u.username, note=body.note)


@router.post("/reorder")
async def reorder(body: ReorderIn, u: User = Depends(perm(P.MIDDLEWARE_MANAGE)),
                  s: AsyncSession = Depends(db)) -> dict:
    out = await service.reorder(s, [i.model_dump() for i in body.items], actor=u.username, note=body.note)
    return {"items": [{"name": n, "order": c["order"], "version": c["version"]} for n, c in out.items()]}


@router.post("/dry-run")
async def dry_run(body: DryRunIn, _: User = Depends(perm(P.OBSERVABILITY_READ))) -> dict:
    if body.name not in mw.REGISTRY:
        raise service.MiddlewareError(404, "kayıtlı olmayan middleware")
    if body.hook not in mw.HOOKS:
        raise service.MiddlewareError(422, f"bilinmeyen hook: {body.hook}")
    inp: dict[str, Any] = body.input.model_dump(exclude_none=True)
    msgs = inp.pop("messages", None)
    if msgs is not None:
        inp["request"] = {"messages": [tuple(m) for m in msgs]}
    out = mw.dry_run(body.name, body.hook, inp, body.params, body.state)
    from airlab_agent.redact import redact                   # sentetik girdi de yanıtta maskeli döner
    return redact(out, "trace")


@router.get("/executions")
async def executions(range: Range = "24h", middleware: str | None = Query(None, max_length=64),
                     hook: str | None = Query(None, max_length=32), decision: str | None = Query(None, max_length=16),
                     result: str | None = Query(None, max_length=16), run_id: str | None = Query(None, max_length=160),
                     target: str | None = Query(None, max_length=128),
                     tool_call_id: str | None = Query(None, max_length=64),
                     entry_type: Literal["middleware", "call", "agent", "all"] = "middleware",
                     limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                     u: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)) -> dict:
    return await q.executions(s, _viewer(u), rng=range, middleware=middleware, hook=hook, decision=decision,
                              result=result, run_id=run_id, target=target, tool_call_id=tool_call_id,
                              entry_type=None if entry_type == "all" else entry_type, limit=limit, offset=offset)


@router.get("/executions/{eid}")
async def execution(eid: uuid.UUID, u: User = Depends(perm(P.OBSERVABILITY_READ)),
                    s: AsyncSession = Depends(db)) -> dict:
    return await q.execution(s, eid, _viewer(u))


@router.get("/runs")
async def runs(range: Range = "24h", kind: str | None = Query(None, max_length=16),
               limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
               u: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)) -> dict:
    return await q.runs(s, _viewer(u), range, limit, offset, kind)


@router.get("/runs/{run_id}/timeline")
async def timeline(run_id: str, u: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)) -> dict:
    return await q.timeline(s, run_id[:160], _viewer(u))
