"""Araştırmalar, incident'lar, RCA, geri bildirim, bilgi bankası, onaylar, zamanlanmış işler."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.models import (ApprovalStatus, IncidentSource, IncidentStatus, InvestigationStatus, Severity, Trigger,
                          User)
from ...repositories import approvals, incidents, investigations, jobs, knowledge, rca
from ...repositories.base import Conflict, InvalidTransition
from airlab_agent.redact import redact_text

from ...security import audit
from ...services import rca_service
from ...security.permissions import P
from ..deps import current_user, db
from ..deps import require_perm as perm
from ..schemas import (ApprovalIn, ApprovalOut, DecisionIn, EvidenceOut, FeedbackIn, FeedbackOut, IncidentEventOut,
                       IncidentIn, IncidentOut, IncidentPatch, InvestigationDetailOut, InvestigationOut, JobOut,
                       JobRunOut, KnownIssueIn, KnownIssueOut, LinkIn, NoteIn, PageOut, RcaDetailOut, RcaGenerateIn,
                       RcaOut, RunbookIn, RunbookOut, ToolOut, page_of)

router = APIRouter()


def _csv_enum(v: str | None, enum):
    if not v:
        return None
    try:
        return [enum(x.strip()) for x in v.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(422, f"geçersiz değer: {v} (izinli: {', '.join(e.value for e in enum)})") from None


# ---------------------------------------------------------------- araştırmalar
@router.get("/investigations", response_model=PageOut[InvestigationOut], tags=["investigations"])
async def list_investigations(application_id: str | None = None, status_: str | None = Query(None, alias="status"),
                              severity: str | None = None, mechanism: str | None = None,
                              q: str | None = Query(None, max_length=200), since: datetime | None = None,
                              until: datetime | None = None, trigger: Trigger | None = None, mine: bool = False,
                              page: int = 1, size: int = 25, sort: str | None = None,
                              u: User = Depends(perm(P.RECORD_READ)), s: AsyncSession = Depends(db)):
    p = await investigations.list_(s, application_id=application_id, status=_csv_enum(status_, InvestigationStatus),
                                   severity=_csv_enum(severity, Severity), mechanism=mechanism, q=q, since=since,
                                   until=until, trigger=trigger, user_id=u.id if mine else None, page=page, size=size,
                                   sort=sort)
    return page_of(p, InvestigationOut)


@router.get("/investigations/{inv_id}", response_model=InvestigationDetailOut, tags=["investigations"])
async def get_investigation(inv_id: uuid.UUID, _: User = Depends(perm(P.RECORD_READ)), s: AsyncSession = Depends(db)):
    return await investigations.get(s, inv_id)


@router.get("/investigations/{inv_id}/trace", tags=["investigations"])
async def get_trace(inv_id: uuid.UUID, request: Request, _: User = Depends(perm(P.RECORD_READ)),
                    s: AsyncSession = Depends(db)):
    """LangSmith trace linki + platform run_id ↔ LangSmith run/trace eşlemeleri + MCP çağrısı span linki şablonu
    (`call_url_template`, `{call_id}` = çağrı kaydının kimliği). Tracing kapalıysa ya da trace yazılmadıysa url null."""
    from ...services import langsmith_trace
    await investigations.get(s, inv_id)                                  # yoksa 404
    return await langsmith_trace.trace_info(request.app.state.rt, s, inv_id)


@router.get("/investigations/{inv_id}/evidence", tags=["investigations"])
async def get_evidence(inv_id: uuid.UUID, _: User = Depends(perm(P.RECORD_READ)), s: AsyncSession = Depends(db)):
    inv = await investigations.get(s, inv_id, with_children=True)
    return [EvidenceOut.model_validate(e).model_dump(mode="json", by_alias=True)
            for e in sorted(inv.evidence, key=lambda e: (e.evidence_type.value, e.signal or ""))]


@router.get("/investigations/{inv_id}/tools", response_model=list[ToolOut], tags=["investigations"])
async def get_tools(inv_id: uuid.UUID, _: User = Depends(perm(P.RECORD_READ)), s: AsyncSession = Depends(db)):
    return (await investigations.get(s, inv_id, with_children=True)).tools


@router.get("/investigations/{inv_id}/similar", response_model=list[InvestigationOut], tags=["investigations"])
async def similar(inv_id: uuid.UUID, _: User = Depends(perm(P.RECORD_READ)), s: AsyncSession = Depends(db)):
    inv = await investigations.get(s, inv_id)
    return await investigations.similar(s, mechanism=inv.primary_mechanism,
                                        apps=list(set(inv.source_apps) | set(inv.affected_apps)), exclude_id=inv.id)


@router.get("/investigations/{inv_id}/recommendations", tags=["investigations"])
async def recommendations(inv_id: uuid.UUID, _: User = Depends(perm(P.RECORD_READ)), s: AsyncSession = Depends(db)):
    inv = await investigations.get(s, inv_id)
    mechs = [m["id"] for m in ((inv.report or {}).get("mechanisms") or [])]
    rec = await knowledge.recommend(s, mechanisms=mechs, apps=list(set(inv.source_apps) | set(inv.affected_apps)))
    return {"known_issues": [KnownIssueOut.model_validate(k) for k in rec["known_issues"]],
            "runbooks": [RunbookOut.model_validate(r) for r in rec["runbooks"]]}


@router.get("/investigations/{inv_id}/feedback", response_model=list[FeedbackOut], tags=["feedback"])
async def inv_feedback(inv_id: uuid.UUID, _: User = Depends(perm(P.RECORD_READ)), s: AsyncSession = Depends(db)):
    return await rca.feedback_for(s, inv_id)


# ---------------------------------------------------------------- incident'lar
async def _incident_full(s, inc_id):
    inc = await incidents.get(s, inc_id, with_events=True)
    linked = await incidents.investigations_of(s, inc_id)
    rcas = await rca.list_(s, incident_id=inc_id, size=20)
    return {**IncidentOut.model_validate(inc).model_dump(mode="json"),
            "timeline": [IncidentEventOut.model_validate(e).model_dump(mode="json") for e in inc.events],
            "investigations": [{**InvestigationOut.model_validate(i).model_dump(mode="json"), "relation": r}
                               for i, r in linked],
            "rca_reports": [RcaOut.model_validate(r).model_dump(mode="json") for r in rcas.items],
            "allowed_transitions": [x.value for x in incidents.allowed_transitions(inc.status)]}


@router.get("/incidents", response_model=PageOut[IncidentOut], tags=["incidents"])
async def list_incidents(status_: str | None = Query(None, alias="status"), severity: str | None = None,
                         application_id: str | None = None, q: str | None = Query(None, max_length=200),
                         since: datetime | None = None, until: datetime | None = None, page: int = 1, size: int = 25,
                         sort: str | None = None, _: User = Depends(perm(P.RECORD_READ)), s: AsyncSession = Depends(db)):
    p = await incidents.list_(s, status=_csv_enum(status_, IncidentStatus), severity=_csv_enum(severity, Severity),
                              application_id=application_id, q=q, since=since, until=until, page=page, size=size,
                              sort=sort)
    return page_of(p, IncidentOut)


@router.post("/incidents", status_code=201, tags=["incidents"])
async def create_incident(body: IncidentIn, u: User = Depends(perm(P.INCIDENT_WRITE)), s: AsyncSession = Depends(db)):
    if body.investigation_id:
        await investigations.get(s, body.investigation_id)
    inc = await incidents.create(s, title=body.title, description=body.description, severity=body.severity,
                                 application_id=body.application_id, investigation_id=body.investigation_id,
                                 source=IncidentSource.MANUAL, created_by=u.id)
    return await _incident_full(s, inc.id)


@router.get("/incidents/{inc_id}", tags=["incidents"])
async def get_incident(inc_id: uuid.UUID, _: User = Depends(perm(P.RECORD_READ)), s: AsyncSession = Depends(db)):
    return await _incident_full(s, inc_id)


@router.patch("/incidents/{inc_id}", tags=["incidents"])
async def patch_incident(inc_id: uuid.UUID, body: IncidentPatch, u: User = Depends(perm(P.INCIDENT_WRITE)), s: AsyncSession = Depends(db)):
    inc = await incidents.get(s, inc_id)
    fields = body.model_dump(exclude_unset=True, exclude={"status", "note"})
    if fields:
        await incidents.update_fields(s, inc, user_id=u.id, **fields)
    if body.status:
        await incidents.transition(s, inc, body.status, user_id=u.id, note=body.note)
    elif body.note:
        await incidents.add_note(s, inc_id, body.note, u.id)
    return await _incident_full(s, inc_id)


@router.post("/incidents/{inc_id}/investigations", tags=["incidents"])
async def link(inc_id: uuid.UUID, body: LinkIn, u: User = Depends(perm(P.INCIDENT_WRITE)), s: AsyncSession = Depends(db)):
    await incidents.get(s, inc_id)
    await investigations.get(s, body.investigation_id)
    await incidents.link_investigation(s, inc_id, body.investigation_id, relation=body.relation, user_id=u.id)
    return await _incident_full(s, inc_id)


@router.post("/incidents/{inc_id}/notes", tags=["incidents"])
async def note(inc_id: uuid.UUID, body: NoteIn, u: User = Depends(perm(P.INCIDENT_WRITE)), s: AsyncSession = Depends(db)):
    await incidents.get(s, inc_id)
    await incidents.add_note(s, inc_id, body.message, u.id)
    return await _incident_full(s, inc_id)


# ---------------------------------------------------------------- RCA
@router.get("/rca", response_model=PageOut[RcaOut], tags=["rca"])
async def list_rca(application_id: str | None = None, incident_id: uuid.UUID | None = None,
                   investigation_id: uuid.UUID | None = None, q: str | None = Query(None, max_length=200),
                   since: datetime | None = None, page: int = 1, size: int = 25, sort: str | None = None,
                   _: User = Depends(perm(P.RECORD_READ)), s: AsyncSession = Depends(db)):
    return page_of(await rca.list_(s, application_id=application_id, incident_id=incident_id,
                                   investigation_id=investigation_id, q=q, since=since, page=page, size=size,
                                   sort=sort), RcaOut)


@router.get("/rca/{rca_id}", response_model=RcaDetailOut, tags=["rca"])
async def get_rca(rca_id: uuid.UUID, _: User = Depends(perm(P.RECORD_READ)), s: AsyncSession = Depends(db)):
    return await rca.get(s, rca_id)


@router.get("/rca/{rca_id}/markdown", response_class=PlainTextResponse, tags=["rca"])
async def rca_markdown(rca_id: uuid.UUID, u: User = Depends(perm(P.RECORD_READ)), s: AsyncSession = Depends(db)):
    await audit.record(s, actor=u.username, action="export.rca_markdown", section="export", target=str(rca_id))
    r = await rca.get(s, rca_id)
    return PlainTextResponse(redact_text(r.markdown or "", "export"), media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="rca-{r.id}.md"'})


@router.post("/rca/{inv_id}/generate", response_model=RcaDetailOut, status_code=201, tags=["rca"])
async def generate_rca(inv_id: uuid.UUID, request: Request, body: RcaGenerateIn | None = None,
                       _: User = Depends(perm(P.INCIDENT_WRITE)), s: AsyncSession = Depends(db)):
    body = body or RcaGenerateIn()
    inv = await investigations.get(s, inv_id)
    if body.incident_id:
        await incidents.get(s, body.incident_id)
    rt = request.app.state.rt
    return await rca_service.generate(s, inv, llm=rt.llm if body.use_llm else None, incident_id=body.incident_id)


# ---------------------------------------------------------------- geri bildirim
@router.post("/feedback", response_model=FeedbackOut, status_code=201, tags=["feedback"])
async def feedback(body: FeedbackIn, u: User = Depends(current_user), s: AsyncSession = Depends(db)):
    inv = await investigations.get(s, body.investigation_id)
    if inv.status not in (InvestigationStatus.COMPLETED, InvestigationStatus.PARTIAL):
        raise HTTPException(status.HTTP_409_CONFLICT, "yalnız tamamlanmış araştırmalar değerlendirilebilir")
    if body.rca_id:
        r = await rca.get(s, body.rca_id)
        if r.investigation_id != inv.id:
            raise HTTPException(422, "RCA bu araştırmaya ait değil")
    return await rca.upsert_feedback(s, inv_id=inv.id, user_id=u.id, verdict=body.verdict, rating=body.rating,
                                     comment=body.comment, rca_id=body.rca_id)


@router.get("/feedback/summary", tags=["feedback"])
async def feedback_summary(_: User = Depends(perm(P.RECORD_READ)), s: AsyncSession = Depends(db)):
    return await rca.feedback_summary(s)


# ---------------------------------------------------------------- bilgi bankası
@router.get("/known-issues", response_model=PageOut[KnownIssueOut], tags=["knowledge"])
async def list_ki(application_id: str | None = None, mechanism: str | None = None,
                  q: str | None = Query(None, max_length=200), page: int = 1, size: int = 25, sort: str | None = None,
                  _: User = Depends(perm(P.RECORD_READ)), s: AsyncSession = Depends(db)):
    return page_of(await knowledge.list_known_issues(s, application_id=application_id, mechanism=mechanism, q=q,
                                                     page=page, size=size, sort=sort), KnownIssueOut)


@router.get("/known-issues/{kid}", tags=["knowledge"])
async def get_ki(kid: uuid.UUID, _: User = Depends(perm(P.RECORD_READ)), s: AsyncSession = Depends(db)):
    k = await knowledge.get_known_issue(s, kid)
    related = await incidents.list_(s, application_id=k.application_id, q=k.mechanism or k.title.split(" ")[0], size=10)
    inv = await investigations.list_(s, mechanism=k.mechanism, size=10) if k.mechanism else None
    return {**KnownIssueOut.model_validate(k).model_dump(mode="json"),
            "related_incidents": [IncidentOut.model_validate(i).model_dump(mode="json") for i in related.items],
            "related_investigations": [InvestigationOut.model_validate(i).model_dump(mode="json")
                                       for i in (inv.items if inv else [])]}


@router.post("/known-issues", response_model=KnownIssueOut, status_code=201, tags=["knowledge"])
async def create_ki(body: KnownIssueIn, u: User = Depends(perm(P.INCIDENT_WRITE)), s: AsyncSession = Depends(db)):
    return await knowledge.create_known_issue(s, {**body.model_dump(), "origin": "manual", "created_by": u.id})


@router.get("/runbooks", response_model=PageOut[RunbookOut], tags=["knowledge"])
async def list_rb(application_id: str | None = None, mechanism: str | None = None,
                  q: str | None = Query(None, max_length=200), page: int = 1, size: int = 25, sort: str | None = None,
                  _: User = Depends(perm(P.RECORD_READ)), s: AsyncSession = Depends(db)):
    return page_of(await knowledge.list_runbooks(s, application_id=application_id, mechanism=mechanism, q=q,
                                                 page=page, size=size, sort=sort), RunbookOut)


@router.get("/runbooks/{rid}", response_model=RunbookOut, tags=["knowledge"])
async def get_rb(rid: uuid.UUID, _: User = Depends(perm(P.RECORD_READ)), s: AsyncSession = Depends(db)):
    return await knowledge.get_runbook(s, rid)


@router.post("/runbooks", response_model=RunbookOut, status_code=201, tags=["knowledge"])
async def create_rb(body: RunbookIn, _: User = Depends(perm(P.KNOWLEDGE_MANAGE)), s: AsyncSession = Depends(db)):
    return await knowledge.create_runbook(s, {**body.model_dump(), "origin": "manual"})


# ---------------------------------------------------------------- onaylar (human-in-the-loop)
@router.get("/approvals", response_model=PageOut[ApprovalOut], tags=["approvals"])
async def list_approvals(status_: str | None = Query(None, alias="status"), page: int = 1, size: int = 25,
                         sort: str | None = None, _: User = Depends(perm(P.RECORD_READ)), s: AsyncSession = Depends(db)):
    await approvals.expire_due(s)
    return page_of(await approvals.list_(s, status=_csv_enum(status_, ApprovalStatus), page=page, size=size,
                                         sort=sort), ApprovalOut)


@router.post("/approvals", response_model=ApprovalOut, status_code=201, tags=["approvals"])
async def request_approval(body: ApprovalIn, u: User = Depends(perm(P.APPROVAL_REQUEST)), s: AsyncSession = Depends(db)):
    if body.runbook_id:
        await knowledge.get_runbook(s, body.runbook_id)
    a = await approvals.request(s, action_type=body.action_type, target=body.target,
                                justification=body.justification, requested_by=u.id,
                                investigation_id=body.investigation_id, incident_id=body.incident_id,
                                runbook_id=body.runbook_id)
    await audit.record(s, actor=u.username, action="approval.request", section="approvals", target=str(a.id),
                       after={"action_type": a.action_type, "target": a.target})
    return a


@router.post("/approvals/{aid}/decision", response_model=ApprovalOut, tags=["approvals"])
async def decide(aid: uuid.UUID, body: DecisionIn, request: Request, u: User = Depends(perm(P.APPROVAL_DECIDE)),
                 s: AsyncSession = Depends(db)):
    try:
        a = await approvals.decide(s, aid, approve=body.approve, decided_by=u.id, comment=body.comment)
    except (Conflict, InvalidTransition) as e:
        await audit.record_separate(request.app.state.rt.sm, actor=u.username, action="approval.decide",
                                    section="approvals", target=str(aid), result="denied",
                                    reason_code="four_eyes" if isinstance(e, Conflict) else "invalid_state", detail=str(e))
        raise
    await audit.record(s, actor=u.username, action="approval.decide", section="approvals", target=str(aid),
                       after={"status": a.status.value})
    return a


@router.post("/approvals/{aid}/cancel", response_model=ApprovalOut, tags=["approvals"])
async def cancel_approval(aid: uuid.UUID, u: User = Depends(perm(P.APPROVAL_REQUEST)), s: AsyncSession = Depends(db)):
    a = await approvals.cancel(s, aid, u.id)
    await audit.record(s, actor=u.username, action="approval.cancel", section="approvals", target=str(aid))
    return a


# ---------------------------------------------------------------- zamanlanmış işler
@router.get("/jobs", response_model=list[JobOut], tags=["jobs"])
async def list_jobs(_: User = Depends(perm(P.RECORD_READ)), s: AsyncSession = Depends(db)):
    return await jobs.list_jobs(s)


@router.get("/jobs/runs", response_model=list[JobRunOut], tags=["jobs"])
async def job_runs(limit: int = Query(30, ge=1, le=200), _: User = Depends(perm(P.RECORD_READ)),
                   s: AsyncSession = Depends(db)):
    return await jobs.recent_runs(s, limit=limit)


@router.post("/jobs/{name}/enabled", tags=["jobs"])
async def set_job(name: str, enabled: bool, _: User = Depends(perm(P.SETTINGS_WRITE)), s: AsyncSession = Depends(db)):
    await jobs.set_enabled(s, name, enabled)
    return {"name": name, "enabled": enabled}

