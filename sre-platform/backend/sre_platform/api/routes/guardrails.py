"""Guardrails Management & Monitoring Center API (`/api/guardrails/...`).

Yetki (backend'de uygulanır):
* okuma (overview, policy, log, ihlal, onay, analytics, yapılandırma): `observability:read` (SRE, admin)
* policy / kapsam / rollback / yapılandırma / kill switch: `guardrail:manage` (admin)
* ihlal triage + Policy Tester (dry-run): `guardrail:triage` (SRE, admin)
* onay kararı: `approval:decide` (admin) + four-eyes (isteyen kendi isteğini onaylayamaz)
* audit: `audit:read`
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from airlab_agent.guardrails.model import EvalContext

from ...db.models import User
from ...guardrails import queries as q
from ...guardrails import runtime as g_rt
from ...guardrails import service as svc
from ...guardrails import tester
from ...security.permissions import P
from ..deps import db
from ..deps import require_perm as perm

router = APIRouter(prefix="/guardrails", tags=["guardrails"])
Range = Literal["1h", "24h", "7d", "30d"]


def _role(u: User) -> str:
    r = getattr(u, "role", None)
    return str(getattr(r, "value", r) or "viewer").lower()


class Assign(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["global", "tenant", "environment", "agent", "mcp_server", "tool", "graph_node"]
    value: str | None = Field(None, max_length=160)


class PolicyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str | None = Field(None, max_length=80)
    name: str | None = Field(None, max_length=160)
    description: str | None = Field(None, max_length=2000)
    category: str | None = Field(None, max_length=32)
    type: str | None = Field(None, max_length=48)
    severity: str | None = Field(None, max_length=16)
    mode: str | None = Field(None, max_length=16)
    status: Literal["active", "inactive", "archived"] | None = None
    stages: list[str] | None = Field(None, max_length=14)
    action: str | None = Field(None, max_length=24)
    priority: int | None = Field(None, ge=1, le=1000)
    params: dict[str, Any] | None = None
    assignments: list[Assign] | None = Field(None, max_length=50)
    note: str | None = Field(None, max_length=1000)


class PolicyPatch(PolicyIn):
    version: int = Field(ge=1)


class CloneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=3, max_length=80)
    name: str | None = Field(None, max_length=160)


class ActivateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(None, max_length=1000)


class AssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    assignments: list[Assign] = Field(min_length=1, max_length=50)


class ViolationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["open", "acknowledged", "investigating", "resolved", "false_positive"] | None = None
    assigned_to: str | None = Field(None, max_length=64)
    note: str | None = Field(None, max_length=1000)


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(None, max_length=1000)


class TestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: str = Field(max_length=24)
    text: str | None = Field(None, max_length=50000)
    data: dict[str, Any] | list | None = None
    context: dict[str, str | None] = Field(default_factory=dict)
    policy_keys: list[str] | None = Field(None, max_length=100)
    draft: dict[str, Any] | None = None
    expected_decision: str | None = Field(None, max_length=24)
    is_attack: bool | None = None
    save: bool = True


class ConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    data: dict[str, Any]


class KillIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    global_: bool = Field(alias="global")
    agents: list[str] = Field(default_factory=list, max_length=200)
    reason: str = Field(min_length=3, max_length=500)


def _actor(u: User) -> str:
    return u.username


# ------------------------------------------------------------------------------------------------ overview / analytics
@router.get("/overview")
async def overview(range: Range = "24h", _: User = Depends(perm(P.OBSERVABILITY_READ)),
                   s: AsyncSession = Depends(db)) -> dict:
    return await q.overview(s, range)


@router.get("/analytics")
async def analytics(range: Range = "7d", _: User = Depends(perm(P.OBSERVABILITY_READ)),
                    s: AsyncSession = Depends(db)) -> dict:
    return await q.analytics(s, range)


# ------------------------------------------------------------------------------------------------ policy
@router.get("/evaluators")
async def evaluator_types(_: User = Depends(perm(P.OBSERVABILITY_READ))) -> dict:
    from airlab_agent.guardrails.model import ACTIONS, CATEGORIES, MODES, SCOPES, SEVERITIES, STAGE_TR, STAGES
    return {"evaluators": tester.evaluator_catalog(), "stages": [{"key": k, "label": STAGE_TR[k]} for k in STAGES],
            "categories": list(CATEGORIES), "actions": list(ACTIONS), "modes": list(MODES),
            "severities": list(SEVERITIES), "scopes": list(SCOPES)}


@router.get("/policies")
async def policies(category: str | None = Query(None, max_length=32), q_: str | None = Query(None, alias="q",
                                                                                              max_length=100),
                   status: str | None = Query(None, max_length=16), _: User = Depends(perm(P.OBSERVABILITY_READ)),
                   s: AsyncSession = Depends(db)) -> dict:
    return {"items": await svc.list_policies(s, category=category, q=q_, status=status)}


@router.get("/policies/{pid}")
async def policy(pid: str, _: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)) -> dict:
    return await svc.policy_detail(s, pid)


def _data(body: PolicyIn) -> dict:
    d = body.model_dump(exclude_none=True, exclude={"note", "version"})
    if body.assignments is not None:
        d["assignments"] = [a.model_dump() for a in body.assignments]
    return d


@router.post("/policies", status_code=201)
async def create(body: PolicyIn, u: User = Depends(perm(P.GUARDRAIL_MANAGE)), s: AsyncSession = Depends(db)) -> dict:
    return await svc.create_policy(s, actor=_actor(u), data={**_data(body), "note": body.note})


@router.patch("/policies/{pid}")
async def update(pid: str, body: PolicyPatch, u: User = Depends(perm(P.GUARDRAIL_MANAGE)),
                 s: AsyncSession = Depends(db)) -> dict:
    return await svc.update_policy(s, pid, actor=_actor(u), version=body.version, data=_data(body), note=body.note)


@router.post("/policies/{pid}/clone", status_code=201)
async def clone(pid: str, body: CloneIn, u: User = Depends(perm(P.GUARDRAIL_MANAGE)),
                s: AsyncSession = Depends(db)) -> dict:
    return await svc.clone_policy(s, pid, actor=_actor(u), key=body.key, name=body.name)


@router.post("/policies/{pid}/versions/{version}/rollback")
async def rollback(pid: str, version: int, body: ActivateIn, u: User = Depends(perm(P.GUARDRAIL_MANAGE)),
                   s: AsyncSession = Depends(db)) -> dict:
    return await svc.activate_version(s, pid, version, actor=_actor(u), rollback=True, note=body.note)


@router.put("/policies/{pid}/assignments")
async def assignments(pid: str, body: AssignIn, u: User = Depends(perm(P.GUARDRAIL_MANAGE)),
                      s: AsyncSession = Depends(db)) -> dict:
    return await svc.assign(s, pid, actor=_actor(u), version=body.version,
                            assignments=[a.model_dump() for a in body.assignments])


@router.get("/effective")
async def effective(request: Request, agent_id: str | None = Query(None, max_length=64),
                    environment: str | None = Query(None, max_length=64),
                    mcp_server: str | None = Query(None, max_length=64), tool: str | None = Query(None, max_length=128),
                    node: str | None = Query(None, max_length=64), _: User = Depends(perm(P.OBSERVABILITY_READ))) -> dict:
    eng = await g_rt.load_engine(request.app.state.rt.sm)
    ctx = EvalContext(agent_id=agent_id or None, environment=environment or "default", mcp_server=mcp_server or None,
                      tool=tool or None, node=node or None)
    return {"revision": eng.revision, "context": ctx.view(), "items": eng.effective(ctx),
            "rule": "deny-overrides: en güçlü karar kazanır; mandatory policy her kapsamda enforce çalışır"}


# ------------------------------------------------------------------------------------------------ logs
@router.get("/evaluations")
async def evaluations(range: Range = "24h", decision: str | None = Query(None, max_length=24),
                      policy: str | None = Query(None, max_length=80), category: str | None = Query(None, max_length=32),
                      stage: str | None = Query(None, max_length=24), agent: str | None = Query(None, max_length=64),
                      run_id: str | None = Query(None, max_length=160), q_: str | None = Query(None, alias="q",
                                                                                               max_length=160),
                      enforced: bool | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                      _: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)) -> dict:
    return await q.logs(s, range_=range, decision=decision, policy=policy, category=category, stage=stage, agent=agent,
                        run_id=run_id, q=q_, enforced=enforced, limit=limit, offset=offset)


@router.get("/evaluations/{eid}")
async def evaluation(eid: str, _: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)) -> dict:
    d = await q.log_detail(s, eid)
    if d is None:
        raise svc.GuardrailError(404, "not_found", "değerlendirme bulunamadı")
    return d


# ------------------------------------------------------------------------------------------------ ihlaller
@router.get("/violations")
async def violations(status: str | None = Query(None, max_length=24), severity: str | None = Query(None, max_length=16),
                     category: str | None = Query(None, max_length=32), agent: str | None = Query(None, max_length=64),
                     q_: str | None = Query(None, alias="q", max_length=160), range: Range | None = None,
                     limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                     _: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)) -> dict:
    return await svc.list_violations(s, status=status, severity=severity, category=category, agent=agent, q=q_,
                                     since=q.since(range) if range else None, limit=limit, offset=offset)


@router.patch("/violations/{vid}")
async def triage(vid: str, body: ViolationPatch, u: User = Depends(perm(P.GUARDRAIL_TRIAGE)),
                 s: AsyncSession = Depends(db)) -> dict:
    return await svc.update_violation(s, vid, actor=_actor(u), status=body.status, assigned_to=body.assigned_to,
                                      note=body.note)


# ------------------------------------------------------------------------------------------------ onaylar
@router.get("/approvals")
async def approvals(status: str | None = Query(None, max_length=16), limit: int = Query(50, ge=1, le=200),
                    offset: int = Query(0, ge=0), _: User = Depends(perm(P.OBSERVABILITY_READ)),
                    s: AsyncSession = Depends(db)) -> dict:
    return await svc.list_approvals(s, status=status, limit=limit, offset=offset)


@router.post("/approvals/{aid}/approve")
async def approve(aid: str, body: DecisionIn, u: User = Depends(perm(P.APPROVAL_DECIDE)),
                  s: AsyncSession = Depends(db)) -> dict:
    return await svc.decide_approval(s, aid, actor_id=str(u.id), actor_role=_role(u), approve=True, reason=body.reason)


@router.post("/approvals/{aid}/reject")
async def reject(aid: str, body: DecisionIn, u: User = Depends(perm(P.APPROVAL_DECIDE)),
                 s: AsyncSession = Depends(db)) -> dict:
    return await svc.decide_approval(s, aid, actor_id=str(u.id), actor_role=_role(u), approve=False,
                                     reason=body.reason)


@router.post("/approvals/{aid}/cancel")
async def cancel(aid: str, u: User = Depends(perm(P.APPROVAL_DECIDE)), s: AsyncSession = Depends(db)) -> dict:
    return await svc.cancel_approval(s, aid, actor_id=str(u.id))


# ------------------------------------------------------------------------------------------------ tester
@router.get("/test/samples")
async def samples(_: User = Depends(perm(P.GUARDRAIL_TRIAGE))) -> dict:
    return {"items": [{"key": k, **v} for k, v in tester.SAMPLES.items()]}


@router.post("/test")
async def test(request: Request, body: TestIn, u: User = Depends(perm(P.GUARDRAIL_TRIAGE)),
               s: AsyncSession = Depends(db)) -> dict:
    return await tester.run(s, request.app.state.rt.sm, actor=_actor(u), actor_role=_role(u), stage=body.stage,
                            text=body.text, data=body.data, context=body.context, policy_keys=body.policy_keys,
                            draft=body.draft, expected_decision=body.expected_decision, is_attack=body.is_attack,
                            save=body.save)


@router.get("/test/history")
async def test_history(limit: int = Query(50, ge=1, le=200), _: User = Depends(perm(P.GUARDRAIL_TRIAGE)),
                       s: AsyncSession = Depends(db)) -> dict:
    return {"items": await tester.history(s, limit)}


# ------------------------------------------------------------------------------------------------ yapılandırma
@router.get("/config")
async def config(_: User = Depends(perm(P.OBSERVABILITY_READ)), s: AsyncSession = Depends(db)) -> dict:
    return await svc.get_config(s)


@router.put("/config")
async def put_config(body: ConfigIn, u: User = Depends(perm(P.GUARDRAIL_MANAGE)), s: AsyncSession = Depends(db)) -> dict:
    out = await svc.update_config(s, actor=_actor(u), revision=body.revision, data=body.data)
    g_rt._KILL["at"] = 0.0                     # bu süreçte kill switch bir sonraki pump'ta yenilenir
    return out


@router.post("/kill-switch")
async def kill_switch(body: KillIn, u: User = Depends(perm(P.GUARDRAIL_MANAGE)), s: AsyncSession = Depends(db)) -> dict:
    cur = await svc.get_config(s)
    data = {k: v for k, v in cur["data"].items()}
    data["kill_switch"] = {"global": body.global_, "agents": sorted(set(body.agents))}
    out = await svc.update_config(s, actor=_actor(u), revision=body.revision, data=data)
    await svc.audit(s, actor=_actor(u), action="guardrail.kill_switch", target="kill_switch",
                    after=data["kill_switch"], detail=body.reason)
    g_rt._KILL["state"], g_rt._KILL["at"] = data["kill_switch"], 0.0
    return out


@router.get("/audit")
async def audit(limit: int = Query(100, ge=1, le=500), _: User = Depends(perm(P.AUDIT_READ)),
                s: AsyncSession = Depends(db)) -> dict:
    return {"items": await svc.list_audit(s, limit)}


@router.get("/audit/verify")
async def audit_verify(_: User = Depends(perm(P.AUDIT_READ)), s: AsyncSession = Depends(db)) -> dict:
    return await svc.verify_audit(s)
