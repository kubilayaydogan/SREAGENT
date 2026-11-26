"""Agent Memory API (`/api/memory/...`).

Yetki (least privilege):
* okuma (istatistik, kayıtlar, thread, checkpoint, retrieval, olay, playground): `observability:read` (SRE, admin)
* oluşturma / düzenleme / arşiv / verify / procedural öneri: `memory:write` (SRE, admin)
* silme: `memory:delete` + onay metni (admin) · procedural onay ve export: `memory:approve` (admin, four-eyes)
* Ayarlar: mevcut Settings API (`/api/settings/agent_memory`, `settings:write`, versiyonlu + audit)
İzolasyon: her istek bir agent'a (`agent` parametresi, kayıtlı olmalı) ve kullanıcının tenant'ına bağlıdır. Sohbet
thread'leri / olayları yalnız sahibine ve admin'e görünür; yetkisiz kayıt 404 döner (varlığı sızdırılmaz).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from ...db.models import MemoryEvent, User
from ...db.session import transaction
from ...memory import events as mem_events
from ...memory import manager as mgr
from ...memory import observability as obs
from ...memory import registry
from ...security.permissions import P
from ..deps import db
from ..deps import require_perm as perm

router = APIRouter(prefix="/memory", tags=["agent-memory"])

MemType = Literal["semantic", "episodic", "procedural"]


class RecordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memory_type: MemType
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=8000)
    category: str | None = Field(None, max_length=48)
    namespace: str | None = Field(None, max_length=200)
    structured_data: dict[str, Any] | None = None
    source: Literal["manual", "import", "agent_extraction", "mcp", "log", "external"] = "manual"
    evidence_references: list[str] = Field(default_factory=list, max_length=50)
    confidence_score: float | None = Field(None, ge=0, le=1)
    verification_status: Literal["unverified", "verified"] | None = None
    tags: list[str] = Field(default_factory=list, max_length=30)
    expires_at: datetime | None = None
    reason: str | None = Field(None, max_length=500)


class RecordPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(None, min_length=1, max_length=300)
    content: str | None = Field(None, min_length=1, max_length=8000)
    category: str | None = Field(None, max_length=48)
    structured_data: dict[str, Any] | None = None
    evidence_references: list[str] | None = Field(None, max_length=50)
    confidence_score: float | None = Field(None, ge=0, le=1)
    tags: list[str] | None = Field(None, max_length=30)
    expires_at: datetime | None = None
    version: int | None = None
    reason: str | None = Field(None, max_length=500)


class StatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(None, max_length=500)


class DecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: str | None = None
    reason: str | None = Field(None, max_length=500)


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: str


class PlaygroundIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: str = registry.EMBEDDED_AGENT
    query: str = Field(min_length=1, max_length=500)
    strategy: Literal["exact", "keyword", "fingerprint", "semantic", "hybrid"] = "keyword"
    memory_types: list[MemType] = Field(default_factory=lambda: ["semantic", "episodic", "procedural"])
    namespace: str | None = None
    top_k: int = Field(5, ge=1, le=50)
    similarity_threshold: float | None = Field(None, ge=0, le=1)
    since_days: int | None = Field(None, ge=1, le=3650)
    apps: list[str] = Field(default_factory=list, max_length=20)


def _agent(agent: str, u: User) -> str:
    if registry.get(agent, registry.tenant_of(u)) is None:
        raise HTTPException(404, "agent bulunamadı")
    return agent


def _admin(u: User) -> bool:
    return obs.is_admin(u)


async def _cfg(request: Request, s: AsyncSession) -> dict:
    from ...settings import service
    return (await service.effective(request.app.state.rt, s)).get("agent_memory", {})


def _err(e: mgr.MemoryError_):
    raise e                                     # app.py: MemoryError_ → {"error", "message", "details"} yanıtı


# ------------------------------------------------------------------------------------------------ meta
@router.get("/agents")
async def agents(u: User = Depends(perm(P.OBSERVABILITY_READ))):
    return {"tenant_id": registry.tenant_of(u),
            "items": [{"agent_id": a.agent_id, "name": a.name, "kind": a.kind, "namespace_prefix": a.namespace_prefix,
                       "settings_section": a.settings_section, "description": a.description}
                      for a in registry.agents(registry.tenant_of(u))],
            "agent_builder": "mevcut değil — kullanıcı tanımlı agent'lar için kayıt arayüzü hazır (registry.register)"}


@router.get("/overview")
async def overview(request: Request, agent: str = registry.EMBEDDED_AGENT, range: str = Query("24h"),
                   u: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)):
    from ...settings import service
    eff = await service.effective(request.app.state.rt, s)
    with mem_events.suppressed():
        out = await obs.overview(request.app.state.rt, s, agent_id=_agent(agent, u), rng=range)
    out["environment"] = eff.get("observability", {}).get("environment_tag")
    out["settings"] = eff.get("agent_memory", {})
    return out


@router.get("/health")
async def health(request: Request, u: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)):
    return await obs.health(request.app.state.rt, s)


# ------------------------------------------------------------------------------------------------ long-term
@router.get("/records")
async def records(request: Request, agent: str = registry.EMBEDDED_AGENT, memory_type: MemType | None = None,
                  q: str | None = Query(None, max_length=200), status: str | None = None,
                  verification: str | None = None, backing: str | None = None, page: int = Query(1, ge=1),
                  size: int = Query(50, ge=1, le=200), u: User = Depends(perm(P.OBSERVABILITY_READ)),
                  s: AsyncSession = Depends(db)):
    return await mgr.list_records(s, agent_id=_agent(agent, u), memory_type=memory_type, q=q, status=status,
                                  verification=verification, backing=backing, page=page, size=size, user=u,
                                  admin=_admin(u))


@router.get("/records/{mid:path}/versions")
async def record_versions(mid: str, agent: str = registry.EMBEDDED_AGENT,
                          u: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)):
    try:
        return {"items": await mgr.versions(s, mid, agent_id=_agent(agent, u))}
    except mgr.MemoryError_ as e:
        _err(e)


@router.get("/records/{mid:path}")
async def record(mid: str, agent: str = registry.EMBEDDED_AGENT, u: User = Depends(perm(P.OBSERVABILITY_READ)),
                 s: AsyncSession = Depends(db)):
    try:
        return await mgr.get_record(s, mid, agent_id=_agent(agent, u), user=u, admin=_admin(u))
    except mgr.MemoryError_ as e:
        _err(e)


@router.post("/records", status_code=201)
async def create_record(body: RecordIn, request: Request, agent: str = registry.EMBEDDED_AGENT,
                        u: User = Depends(perm(P.MEMORY_WRITE)), s: AsyncSession = Depends(db)):
    try:
        return await mgr.create(s, agent_id=_agent(agent, u), actor=u.username, cfg=await _cfg(request, s),
                                data=body.model_dump(exclude_none=True))
    except mgr.MemoryError_ as e:
        _err(e)


@router.patch("/records/{mid:path}")
async def patch_record(mid: str, body: RecordPatch, request: Request, agent: str = registry.EMBEDDED_AGENT,
                       u: User = Depends(perm(P.MEMORY_WRITE)), s: AsyncSession = Depends(db)):
    data = body.model_dump(exclude_unset=True)
    ver = data.pop("version", None)
    try:
        return await mgr.update_record(s, mid, agent_id=_agent(agent, u), actor=u.username,
                                       cfg=await _cfg(request, s), data=data, expected_version=ver)
    except mgr.MemoryError_ as e:
        _err(e)


@router.post("/records/{mid:path}/{op}")
async def record_op(mid: str, op: Literal["archive", "restore", "verify", "reject_verification", "approve", "reject"],
                    request: Request, body: DecideIn | None = None, agent: str = registry.EMBEDDED_AGENT,
                    s: AsyncSession = Depends(db), u: User = Depends(perm(P.MEMORY_WRITE))):
    from ...security.permissions import has
    body = body or DecideIn()
    try:
        if op in ("approve", "reject"):
            if not has(u.role, P.MEMORY_APPROVE):
                raise HTTPException(403, "memory:approve yetkisi gerekli")
            _agent(agent, u)
            return await mgr.decide(s, mid, actor=u.username, approve=op == "approve", version_id=body.version_id,
                                    reason=body.reason)
        return await mgr.set_status(s, mid, agent_id=_agent(agent, u), actor=u.username, op=op, reason=body.reason)
    except mgr.MemoryError_ as e:
        _err(e)


@router.post("/records-delete/{mid:path}")
async def delete_record(mid: str, body: ConfirmIn, agent: str = registry.EMBEDDED_AGENT,
                        u: User = Depends(perm(P.MEMORY_DELETE)), s: AsyncSession = Depends(db)):
    if body.confirm != mid:
        raise HTTPException(422, "onay için kayıt kimliğini aynen yazın")
    try:
        return await mgr.delete_record(s, mid, agent_id=_agent(agent, u), actor=u.username)
    except mgr.MemoryError_ as e:
        _err(e)


@router.get("/approvals")
async def approvals(u: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)):
    return {"items": await mgr.approvals(s)}


@router.get("/export")
async def export(agent: str = registry.EMBEDDED_AGENT, memory_type: MemType | None = None,
                 u: User = Depends(perm(P.MEMORY_APPROVE)), s: AsyncSession = Depends(db)):
    return await mgr.export(s, agent_id=_agent(agent, u), actor=u.username, memory_type=memory_type, user=u)


# ------------------------------------------------------------------------------------------------ short-term / checkpoints
@router.get("/threads")
async def threads(agent: str = registry.EMBEDDED_AGENT, q: str | None = Query(None, max_length=128),
                  kind: Literal["chat", "analysis"] | None = None, page: int = Query(1, ge=1),
                  size: int = Query(50, ge=1, le=200), u: User = Depends(perm(P.OBSERVABILITY_READ)),
                  s: AsyncSession = Depends(db)):
    if _agent(agent, u) != registry.EMBEDDED_AGENT:
        return {"items": [], "total": 0, "page": 1, "size": size, "pages": 1}
    return await obs.threads(s, user=u, admin=_admin(u), q=q, kind=kind, page=page, size=size)


@router.get("/threads/{thread_id}")
async def thread(thread_id: str, request: Request, u: User = Depends(perm(P.OBSERVABILITY_READ)),
                 s: AsyncSession = Depends(db)):
    out = await obs.thread_detail(request.app.state.rt, s, thread_id, user=u, admin=_admin(u),
                                  runs=getattr(request.app.state, "runs", None))
    if out is None:
        raise HTTPException(404, "thread bulunamadı")
    return out


@router.get("/threads/{thread_id}/checkpoints")
async def thread_checkpoints(thread_id: str, request: Request, limit: int = Query(100, ge=1, le=500),
                             u: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)):
    out = await obs.checkpoints(request.app.state.rt, s, thread_id, user=u, admin=_admin(u), limit=limit)
    if out is None:
        raise HTTPException(404, "thread bulunamadı")
    return out


@router.get("/threads/{thread_id}/checkpoints/{checkpoint_id}")
async def checkpoint(thread_id: str, checkpoint_id: str, request: Request, ns: str = "",
                     u: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)):
    out = await obs.checkpoint_detail(request.app.state.rt, s, thread_id, checkpoint_id, user=u, admin=_admin(u),
                                      ns=ns)
    if out is None:
        raise HTTPException(404, "checkpoint bulunamadı")
    return out


@router.get("/threads/{thread_id}/compare")
async def compare(thread_id: str, a: str, b: str, request: Request, u: User = Depends(perm(P.OBSERVABILITY_READ)),
                  s: AsyncSession = Depends(db)):
    out = await obs.checkpoint_compare(request.app.state.rt, s, thread_id, a, b, user=u, admin=_admin(u))
    if out is None:
        raise HTTPException(404, "checkpoint bulunamadı")
    return out


# ------------------------------------------------------------------------------------------------ working memory
@router.get("/working")
async def working_runs(u: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)):
    return {"items": await obs.working_runs(s, user=u, admin=_admin(u))}


@router.get("/working/{run_id}")
async def working(run_id: str, u: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)):
    out = await obs.working_memory(s, run_id, user=u, admin=_admin(u))
    if out is None:
        raise HTTPException(404, "run bulunamadı")
    return out


# ------------------------------------------------------------------------------------------------ retrieval / events
@router.get("/retrievals")
async def retrievals(agent: str = registry.EMBEDDED_AGENT, run_id: str | None = None, origin: str | None = None,
                     range: str | None = None, page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200),
                     u: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)):
    return await obs.list_retrievals(s, agent_id=_agent(agent, u), user=u, admin=_admin(u), run_id=run_id,
                                     origin=origin, since=obs.since_of(range) if range else None, page=page,
                                     size=size)


@router.get("/retrievals/{rid}")
async def retrieval(rid: str, u: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)):
    out = await obs.get_retrieval(s, rid, user=u, admin=_admin(u))
    if out is None:
        raise HTTPException(404, "retrieval bulunamadı")
    return out


@router.post("/playground")
async def playground(body: PlaygroundIn, u: User = Depends(perm(P.OBSERVABILITY_READ)),
                     s: AsyncSession = Depends(db)):
    try:
        with mem_events.suppressed():
            return await mgr.playground(s, agent_id=_agent(body.agent, u), query=body.query, strategy=body.strategy,
                                        memory_types=list(body.memory_types), top_k=body.top_k,
                                        threshold=body.similarity_threshold, since_days=body.since_days,
                                        namespace=body.namespace, apps=body.apps, user=u)
    except mgr.MemoryError_ as e:
        _err(e)


@router.get("/strategies")
async def strategies(u: User = Depends(perm(P.OBSERVABILITY_READ))):
    return {"implemented": [{"id": k, "label": v} for k, v in mgr.STRATEGIES.items()],
            "not_implemented": [{"id": k, "label": v} for k, v in mgr.NOT_IMPLEMENTED.items()],
            "agent_strategy": "fts_fingerprint"}


@router.get("/events")
async def list_events(agent: str = registry.EMBEDDED_AGENT, type: list[str] | None = Query(None),
                      run_id: str | None = None, thread_id: str | None = None, memory_id: str | None = None,
                      status: str | None = None, range: str | None = None, page: int = Query(1, ge=1),
                      size: int = Query(100, ge=1, le=500), u: User = Depends(perm(P.OBSERVABILITY_READ)),
                      s: AsyncSession = Depends(db)):
    return await obs.list_events(s, agent_id=_agent(agent, u), user=u, admin=_admin(u), types=type, run_id=run_id,
                                 thread_id=thread_id, memory_id=memory_id, status=status,
                                 since=obs.since_of(range) if range else None, after_ts=None, page=page, size=size)


@router.get("/events/stream")
async def stream_events(request: Request, agent: str = registry.EMBEDDED_AGENT,
                        last_event_id: str | None = Header(None, alias="Last-Event-ID"),
                        u: User = Depends(perm(P.OBSERVABILITY_READ))):
    """Canlı takip (mevcut SSE altyapısı). SSE id = olay zamanı (µs); Last-Event-ID ile kaldığı yerden devam eder.
    İstemci aynı olayı event_id ile eler."""
    agent_id, admin = _agent(agent, u), _admin(u)
    sm = request.app.state.rt.sm
    try:
        start = datetime.fromtimestamp(int(last_event_id) / 1e6, timezone.utc) if last_event_id else \
            datetime.now(timezone.utc)
    except ValueError:
        start = datetime.now(timezone.utc)

    async def gen():
        last, idle, seq = start, 0, 0
        while True:
            if await request.is_disconnected():
                return
            async with transaction(sm) as s2:
                page = await obs.list_events(s2, agent_id=agent_id, user=u, admin=admin, types=None, run_id=None,
                                             thread_id=None, memory_id=None, status=None, since=None, after_ts=last,
                                             page=1, size=200)
            rows = sorted(page["items"], key=lambda x: x["ts"])
            for e in rows:
                ts = datetime.fromisoformat(e["ts"])
                last = max(last, ts)
                seq += 1
                e.update(type="memory_event", seq=int(ts.timestamp() * 1e6))
                yield {"id": str(int(ts.timestamp() * 1e6)), "event": "memory_event",
                       "data": json.dumps(e, ensure_ascii=False, default=str)}
            if not rows:
                idle += 1
                if idle % 15 == 0:
                    yield {"comment": "keepalive"}
                await asyncio.sleep(1.5)
            else:
                idle = 0

    return EventSourceResponse(gen(), ping=20, headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/events/{eid}")
async def get_event(eid: str, u: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)):
    out = await obs.get_event(s, eid, user=u, admin=_admin(u))
    if out is None:
        raise HTTPException(404, "olay bulunamadı")
    return out


@router.get("/governance")
async def governance(request: Request, u: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)):
    """Uygulanan kontrollerin salt okunur özeti + son policy ihlalleri ve memory audit kayıtları."""
    from sqlalchemy import select
    from ...db.models import AuditEvent
    from ...security.permissions import ROLE_PERMISSIONS
    cfg = await _cfg(request, s)
    viol = (await s.execute(select(MemoryEvent).where(MemoryEvent.event_type.in_(
        ["MEMORY_POLICY_VIOLATION", "MEMORY_REJECTED"])).order_by(MemoryEvent.ts.desc()).limit(50))).scalars()
    aud = (await s.execute(select(AuditEvent).where(AuditEvent.section == "memory")
                           .order_by(AuditEvent.ts.desc()).limit(50))).scalars()
    perms = [p for p in P if p.value.startswith("memory:") or p == P.OBSERVABILITY_READ]
    return {
        "controls": [
            {"id": "tenant_isolation", "status": "enforced", "detail": "tek tenant (default); tüm query'ler tenant_id"},
            {"id": "agent_isolation", "status": "enforced", "detail": "tüm query'ler agent_id; başka agent → 404"},
            {"id": "namespace_authorization", "status": "enforced",
             "detail": "namespace agent önekine ait olmalı; tek başına güvenlik sınırı değildir"},
            {"id": "rbac", "status": "enforced", "detail": "observability:read / memory:write / memory:delete / "
                                                           "memory:approve"},
            {"id": "secret_detection", "status": "enforced", "detail": "secret içeren kayıt reddedilir (kapatılamaz)"},
            {"id": "pii_redaction", "status": "enabled" if cfg.get("pii_redaction_enabled", True) else "disabled"},
            {"id": "prompt_injection_detection", "status": "enabled" if cfg.get("require_validation", True)
             else "disabled", "detail": "kural tabanlı; her saldırıyı yakalamaz"},
            {"id": "memory_poisoning_protection", "status": "enabled" if cfg.get("poisoning_protection_enabled", True)
             else "disabled", "detail": "güvenilmeyen kaynak procedural yazamaz, kendini doğrulayamaz"},
            {"id": "evidence_validation", "status": "enabled" if cfg.get("require_evidence_verification", True)
             else "disabled", "detail": "güvenilmeyen kayıt doğrulanmadan retrieval'a girmez"},
            {"id": "duplicate_detection", "status": "enforced", "detail": "content_hash"},
            {"id": "retention_ttl", "status": "enabled", "detail": f"event {cfg.get('memory_event_retention_days')} "
                                                                   f"gün; kayıt TTL {cfg.get('default_ttl_days')} gün"},
            {"id": "audit_logging", "status": "enforced", "detail": "create/update/delete/archive/approve/export"},
            {"id": "approval_workflow", "status": "enforced", "detail": "procedural memory four-eyes"},
            {"id": "versioning", "status": "enforced", "detail": "sre.memory_versions (değişmez geçmiş)"},
            {"id": "untrusted_content", "status": "enforced",
             "detail": "MCP / log / agent extraction içeriği untrusted; LLM'e yalnız veri bölümünde gider"},
        ],
        "permissions": [{"permission": p.value, "roles": [r.value for r, ps in ROLE_PERMISSIONS.items() if p in ps]}
                        for p in perms],
        "violations": [obs.event_out(e, full=True) for e in viol],
        "audit": [{"ts": a.ts.isoformat(), "actor": a.actor, "action": a.action, "target": a.target,
                   "result": a.result, "detail": a.detail} for a in aud],
        "pending_approvals": len(await mgr.approvals(s)),
    }
