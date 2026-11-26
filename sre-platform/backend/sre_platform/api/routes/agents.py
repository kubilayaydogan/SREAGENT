"""Agent Builder API (`/api/agents/...`).

Yetkiler (backend'de doğrulanır):
* okuma: `agent:run` (agent seçicisi) / `observability:read` (run kayıtları)
* oluşturma, taslak, test, kopya, import / export: `agent:build` (SRE, admin)
* yayımlama, sürüm aktifleştirme, arşivleme, Soru-Cevap varsayılanı: `agent:publish` (admin)
* schedule agent seçimi: `settings:write` (mevcut iş zamanlama yetkisi)
Yayımlama, varsayılan agent değişikliği, import ve export audit'e yazılır.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...builder import definition as D
from ...builder import package, runs, service
from ...db.models import User
from ...security.permissions import P
from ..deps import db
from ..deps import require_perm as perm
from ..schemas import RunOut

router = APIRouter(prefix="/agents", tags=["agent-builder"])


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateIn(_In):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(None, max_length=2000)
    modes: list[Literal["chat", "scheduled"]] = Field(default_factory=lambda: ["chat"])


class DraftIn(_In):
    revision: int
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(None, max_length=2000)
    modes: list[Literal["chat", "scheduled"]]
    definition: dict[str, Any]


class PublishIn(_In):
    revision: int
    note: str | None = Field(None, max_length=500)


class CopyIn(_In):
    name: str | None = Field(None, max_length=100)


class TestIn(_In):
    input: str = Field("", max_length=8000)
    variables: dict[str, str] = Field(default_factory=dict)
    semantics: Literal["chat", "scheduled"] = "chat"


class DefaultIn(_In):
    agent_id: uuid.UUID


class ScheduleIn(_In):
    name: str | None = Field(None, max_length=60)
    agent_id: uuid.UUID
    interval_minutes: int = Field(60, ge=1, le=10080)
    enabled: bool = True
    input: str = Field("", max_length=4000)
    variables: dict[str, str] = Field(default_factory=dict)


class ImportIn(_In):
    package: dict[str, Any]
    mapping: dict[str, str | None] = Field(default_factory=dict)
    target: Literal["new", "existing"] = "new"
    agent_id: str | None = None
    name: str | None = Field(None, max_length=100)


class PreviewIn(_In):
    package: dict[str, Any]
    mapping: dict[str, str | None] = Field(default_factory=dict)


def _size_guard(request: Request) -> None:
    n = int(request.headers.get("content-length") or 0)
    if n > package.MAX_BYTES:
        raise package.PackageInvalid(f"paket çok büyük ({n // 1024} KB > {package.MAX_BYTES // 1024} KB)")


# ------------------------------------------------------------------------------------------------ liste / katalog
@router.get("")
async def list_agents(mode: Literal["chat", "scheduled"] | None = None, runnable: bool = False,
                      archived: bool = False, u: User = Depends(perm(P.AGENT_RUN)), s: AsyncSession = Depends(db)):
    return {"items": await service.list_(s, include_archived=archived, mode=mode, runnable=runnable),
            "default_chat_agent_id": str(await service.default_chat(s))}


@router.get("/catalog")
async def catalog(request: Request, u: User = Depends(perm(P.AGENT_BUILD)), s: AsyncSession = Depends(db)):
    """Builder için node paleti, politikalar ve seçilebilir bağlantılar (secret yok)."""
    from airlab_agent import middleware as mw

    from ...builder import validate as V
    env = await V.load_env(request.app.state.rt, s)
    return {"nodes": D.CATALOG, "unavailable_nodes": D.UNAVAILABLE, "operators": list(D.OPERATORS),
            "efforts": list(D.EFFORTS),
            "llm_profiles": [{**{k: p[k] for k in ("id", "name", "provider", "model", "enabled", "max_tokens",
                                                   "timeout_s", "effort")},
                              "model_max_tokens": (p["check"] or {}).get("model_max_tokens")
                              if (p["check"] or {}).get("ok") else None,
                              "capabilities": (p["check"] or {}).get("capabilities") if (p["check"] or {}).get("ok")
                              else None,
                              "supports_temperature": D.supports_temperature(p["model"])}
                             for p in env.profiles.values()],
            "allowed_models": sorted(env.allowed_models),
            "connectors": [{"id": c["id"], "name": c["name"], "provider": c.get("provider"), "enabled": c.get("enabled"),
                            "allowed_tools": c.get("allowed_tools") or [],
                            "tools": [{"name": n, "read_only": t.get("read_only"), "destructive": t.get("destructive"),
                                       "executable": c.get("provider") == "dynatrace" and n == "execute-dql",
                                       "description": t.get("description")}
                                      for n, t in (c.get("tools") or {}).items()] or
                                     ([{"name": "execute-dql", "executable": True, "read_only": True}]
                                      if c.get("provider") == "dynatrace" else [])}
                           for c in env.connectors.values()],
            "middleware": [{"name": n, "title": x.title, "kind": x.kind, "mandatory": x.mandatory,
                            "description": x.description,
                            "platform_enabled": (env.middleware.get(n) or {}).get("enabled", True)}
                           for n, x in mw.REGISTRY.items()],
            "memory_scopes": ["investigations", "incidents", "rca", "runbooks", "known_issues"],
            "temperature_note": "Claude 5 ailesi (Opus / Sonnet / Haiku 5.x, Fable) temperature, top_p ve top_k "
                                "kabul etmez; davranış prompt ve effort ile yönetilir."}


# ------------------------------------------------------------------------------------------------ varsayılan / schedule
@router.get("/default")
async def get_default(u: User = Depends(perm(P.AGENT_RUN)), s: AsyncSession = Depends(db)):
    aid = await service.default_chat(s)
    return {"agent_id": str(aid)}


@router.put("/default")
async def put_default(body: DefaultIn, u: User = Depends(perm(P.AGENT_PUBLISH)), s: AsyncSession = Depends(db)):
    return await service.set_default(s, body.agent_id, actor=u.username)


@router.get("/schedules")
async def list_schedules(u: User = Depends(perm(P.SETTINGS_READ)), s: AsyncSession = Depends(db)):
    return {"items": await service.schedules(s)}


@router.post("/schedules", status_code=201)
async def create_schedule(body: ScheduleIn, u: User = Depends(perm(P.SETTINGS_WRITE)), s: AsyncSession = Depends(db)):
    return await service.upsert_schedule(s, name=body.name, agent_id=str(body.agent_id),
                                         interval_minutes=body.interval_minutes, enabled=body.enabled,
                                         input_=body.input, variables=body.variables, actor=u.username, create=True)


@router.put("/schedules/{name}")
async def update_schedule(name: str, body: ScheduleIn, u: User = Depends(perm(P.SETTINGS_WRITE)),
                          s: AsyncSession = Depends(db)):
    return await service.upsert_schedule(s, name=name, agent_id=str(body.agent_id),
                                         interval_minutes=body.interval_minutes, enabled=body.enabled,
                                         input_=body.input, variables=body.variables, actor=u.username, create=False)


@router.delete("/schedules/{name}", status_code=204)
async def delete_schedule(name: str, u: User = Depends(perm(P.SETTINGS_WRITE)), s: AsyncSession = Depends(db)):
    await service.delete_schedule(s, name, actor=u.username)


# ------------------------------------------------------------------------------------------------ run kayıtları
@router.get("/runs")
async def list_runs(agent_id: uuid.UUID | None = None, mode: Literal["chat", "scheduled", "test"] | None = None,
                    version: str | None = Query(None, max_length=32),
                    status: Literal["running", "completed", "failed", "rejected", "cancelled"] | None = None,
                    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                    u: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)):
    return await service.runs(s, u, agent_id=str(agent_id) if agent_id else None, mode=mode, version=version,
                              status=status, limit=limit, offset=offset)


@router.get("/runs/{run_id}")
async def get_run(run_id: str, u: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)):
    return service.run_out(await service.run(s, u, run_id))


@router.post("/runs/{run_id}/retry")
async def retry_run(run_id: str, request: Request, u: User = Depends(perm(P.AGENT_RUN)), s: AsyncSession = Depends(db)):
    row = await service.run(s, u, run_id)
    await s.commit()
    res = await runs.retry(request.app.state.rt, row, user=u)
    return runs._public(res)


# ------------------------------------------------------------------------------------------------ import
@router.post("/import/preview")
async def import_preview(body: PreviewIn, request: Request, u: User = Depends(perm(P.AGENT_BUILD)),
                         s: AsyncSession = Depends(db)):
    _size_guard(request)
    return await package.preview(request.app.state.rt, s, body.package, mapping=body.mapping)


@router.post("/import", status_code=201)
async def import_agent(body: ImportIn, request: Request, u: User = Depends(perm(P.AGENT_BUILD)),
                       s: AsyncSession = Depends(db)):
    _size_guard(request)
    a, issues = await package.import_(request.app.state.rt, s, body.package, mapping=body.mapping, target=body.target,
                                      agent_id=body.agent_id, name=body.name, actor=u.username)
    return {"agent": await service.summary(s, a), "issues": issues}


# ------------------------------------------------------------------------------------------------ agent
@router.post("", status_code=201)
async def create_agent(body: CreateIn, u: User = Depends(perm(P.AGENT_BUILD)), s: AsyncSession = Depends(db)):
    a = await service.create(s, name=body.name, description=body.description, modes=list(body.modes), actor=u.username)
    return await service.summary(s, a)


@router.get("/{agent_id}")
async def get_agent(agent_id: str, request: Request, u: User = Depends(perm(P.AGENT_RUN)),
                    s: AsyncSession = Depends(db)):
    return await service.detail(request.app.state.rt, s, await service.get(s, agent_id))


@router.put("/{agent_id}/draft")
async def save_draft(agent_id: str, body: DraftIn, request: Request, u: User = Depends(perm(P.AGENT_BUILD)),
                     s: AsyncSession = Depends(db)):
    a = await service.get(s, agent_id)
    a, issues = await service.save_draft(request.app.state.rt, s, a, revision=body.revision, name=body.name,
                                         description=body.description, modes=list(body.modes),
                                         definition=body.definition, actor=u.username)
    return {"draft_revision": a.draft_revision, "issues": issues}


@router.post("/{agent_id}/validate")
async def validate_agent(agent_id: str, request: Request, stage: Literal["save", "test", "publish"] = "publish",
                         u: User = Depends(perm(P.AGENT_BUILD)), s: AsyncSession = Depends(db)):
    from ...builder import validate as V
    a = await service.get(s, agent_id)
    if a.kind != "custom":
        return {"issues": []}
    env = await V.load_env(request.app.state.rt, s)
    _, issues = V.run_all(a.draft, env, name=a.name, modes=list(a.modes or []), stage=stage)
    return {"issues": issues}


@router.post("/{agent_id}/copy", status_code=201)
async def copy_agent(agent_id: str, body: CopyIn, u: User = Depends(perm(P.AGENT_BUILD)), s: AsyncSession = Depends(db)):
    a = await service.copy(s, await service.get(s, agent_id), name=body.name, actor=u.username)
    return await service.summary(s, a)


@router.post("/{agent_id}/publish")
async def publish(agent_id: str, body: PublishIn, request: Request, u: User = Depends(perm(P.AGENT_PUBLISH)),
                  s: AsyncSession = Depends(db)):
    v = await service.publish(request.app.state.rt, s, await service.get(s, agent_id), revision=body.revision,
                              note=body.note, actor=u.username)
    return {"version": v.version, "checksum": v.checksum[:12]}


@router.get("/{agent_id}/versions/{n}")
async def get_version(agent_id: str, n: int, u: User = Depends(perm(P.AGENT_RUN)), s: AsyncSession = Depends(db)):
    a = await service.get(s, agent_id)
    v = await service.version(s, a, n)
    return {"version": v.version, "definition": v.definition, "checksum": v.checksum, "note": v.note,
            "published_by": v.published_by, "published_at": v.published_at.isoformat(),
            "active": a.active_version == v.version}


@router.post("/{agent_id}/versions/{n}/activate")
async def activate(agent_id: str, n: int, u: User = Depends(perm(P.AGENT_PUBLISH)), s: AsyncSession = Depends(db)):
    a = await service.activate(s, await service.get(s, agent_id), n, actor=u.username)
    return {"active_version": a.active_version}


@router.get("/{agent_id}/dependencies")
async def deps(agent_id: str, u: User = Depends(perm(P.AGENT_RUN)), s: AsyncSession = Depends(db)):
    a = await service.get(s, agent_id)
    return {"items": await service.dependencies(s, a.id)}


@router.post("/{agent_id}/archive")
async def archive(agent_id: str, u: User = Depends(perm(P.AGENT_PUBLISH)), s: AsyncSession = Depends(db)):
    a = await service.archive(s, await service.get(s, agent_id), actor=u.username)
    return {"status": a.status}


@router.get("/{agent_id}/export")
async def export(agent_id: str, request: Request, version: int | None = Query(None, ge=1),
                 u: User = Depends(perm(P.AGENT_BUILD)), s: AsyncSession = Depends(db)):
    from fastapi.responses import Response
    a = await service.get(s, agent_id)
    pkg = await package.export(request.app.state.rt, s, a, version=version, actor=u.username)
    await s.commit()
    fname = f"{a.key}-{'v' + str(version) if version else 'draft'}.agent.json"
    return Response(json.dumps(pkg, ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.post("/{agent_id}/test", response_model=RunOut, status_code=202)
async def test(agent_id: str, body: TestIn, request: Request, u: User = Depends(perm(P.AGENT_BUILD)),
               s: AsyncSession = Depends(db)):
    """Taslağı çalıştırır (SSE). Platform politikaları, zorunlu middleware'ler ve tool yetkileri aynen uygulanır."""
    from ...builder import validate as V
    from ...security.permissions import has
    from ...settings import runtime as srt
    from ...settings import service as settings_service
    from .agent import _admit, _check_limits, _run_out, _started
    if not has(u.role, P.AGENT_RUN):
        from fastapi import HTTPException
        raise HTTPException(403, "agent çalıştırma yetkiniz yok")
    a = await service.get(s, agent_id)
    if a.kind != "custom":
        from airlab_agent.errors import UserError
        raise UserError("gömülü SRE Agent Soru-Cevap ekranından çalıştırılır")
    rt = request.app.state.rt
    decision = srt.check_allowed(await settings_service.effective(rt, s),
                                 "scheduled" if body.semantics == "scheduled" else "manual")
    env = await V.load_env(rt, s)
    _, issues = V.run_all(a.draft, env, name=a.name, modes=list(a.modes or []), stage="test")
    if V.blocking(issues):
        raise service.Invalid(V.blocking(issues), "taslak test edilemez: doğrulama hataları var")
    await _admit(request, u, s, "agent_test")
    _check_limits(request, u)
    await s.commit()
    uid, inp, var, sem = u.id, body.input, dict(body.variables), body.semantics

    async def go(run):
        await runs.test_run(rt, agent=a, input_=inp, variables=var, semantics=sem, user_id=uid, emit=run.emit,
                            cancel=run.cancel)

    run = request.app.state.runs.start("agent_test", u.id, go)
    await _started(request, u, run, "agent_test", str(a.id), decision)
    return _run_out(run)
