"""Memory Manager: Memory Reader / Writer / Retriever / Validator / Policy / Retention sorumluluklarının platform
tarafı (API buradan çağırır).

Long-term kayıtlar dört fiziksel kaynaktan okunur ve aynı görünümle sunulur (kopya yok):

| record id     | Kaynak (tablo)               | memory_type | Yazılabilir mi                                    |
|---------------|------------------------------|-------------|---------------------------------------------------|
| mem:<uuid>    | sre.memory_records           | semantic    | evet (policy + versiyon)                          |
| ki:<uuid>     | sre.known_issues             | semantic    | düzenle / verify / arşiv (versiyon)               |
| rb:<uuid>     | sre.runbooks                 | procedural  | öneri → onay (four-eyes); agent yalnız approved   |
| ep:<inv uuid> | sre.agent_episodes           | episodic    | yalnız arşiv/geri al (investigation'dan türetilir) |
| st:<ns>|<key> | langgraph.store (Store)      | episodic / semantic / short_term | salt okunur (agent yazar)     |

Toplamlar kaynak başına ayrık satırlardır; checkpoint ve thread sayıları long-term toplamına girmez.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Text, cast, delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AgentEpisode, KnownIssue, MemoryEvent, MemoryRecord, MemoryRetrieval, MemoryVersion, Runbook
from ..repositories.base import ts_match, ts_rank
from . import events, policy
from .registry import DEFAULT_TENANT, EMBEDDED_AGENT, default_namespace

KINDS = {"mem": "memory_record", "ki": "known_issue", "rb": "runbook", "ep": "episode", "st": "store"}


class MemoryError_(Exception):
    def __init__(self, status: int, message: str, details: list | None = None):
        super().__init__(message)
        self.status, self.message, self.details = status, message, details or []


def _iso(d: datetime | None) -> str | None:
    return d.astimezone(timezone.utc).isoformat() if d else None


def parse_id(mid: str) -> tuple[str, str]:
    kind, _, rest = mid.partition(":")
    if kind not in KINDS or not rest:
        raise MemoryError_(404, "memory kaydı bulunamadı")
    if kind != "st":
        try:
            uuid.UUID(rest)
        except ValueError:
            raise MemoryError_(404, "memory kaydı bulunamadı") from None
    return kind, rest


# ------------------------------------------------------------------------------------------------ görünüm
def _base(**kw) -> dict:
    out = {"tenant_id": DEFAULT_TENANT, "agent_id": EMBEDDED_AGENT, "structured_data": {}, "source_run_id": None,
           "source_thread_id": None, "source_trace_id": None, "evidence_references": [], "confidence_score": None,
           "last_accessed_at": None, "access_count": None, "expires_at": None, "tags": [], "embedding_model": None,
           "embedding_status": "not_indexed", "metadata": {}, "source_trust": "trusted", "actions": []}
    out.update(kw)
    return out


def view_record(r: MemoryRecord) -> dict:
    acts = ["update", "archive", "verify", "reject_verification", "delete"] if r.status == "active" else \
        ["restore", "delete"]
    return _base(memory_id=f"mem:{r.id}", backing="sre.memory_records", tenant_id=r.tenant_id, agent_id=r.agent_id,
                 namespace=r.namespace, memory_type=r.memory_type, category=r.category, title=r.title,
                 content=r.content, structured_data=r.structured_data, source=r.source, source_trust=r.source_trust,
                 source_run_id=r.source_run_id, source_thread_id=r.source_thread_id,
                 source_trace_id=r.source_trace_id, evidence_references=r.evidence_references,
                 confidence_score=r.confidence_score, verification_status=r.verification_status,
                 approval_status=r.approval_status, status=r.status, created_by=r.created_by,
                 created_at=_iso(r.created_at), updated_at=_iso(r.updated_at),
                 last_accessed_at=_iso(r.last_accessed_at), access_count=r.access_count,
                 expires_at=_iso(r.expires_at), version=r.version, tags=r.tags, embedding_model=r.embedding_model,
                 embedding_status=r.embedding_status, metadata=r.meta, actions=acts)


def view_known_issue(k: KnownIssue) -> dict:
    parts = [("Açıklama", k.description), ("Belirtiler", k.symptoms), ("Root cause", k.root_cause),
             ("Çözüm", k.resolution)]
    return _base(memory_id=f"ki:{k.id}", backing="sre.known_issues",
                 namespace=default_namespace(EMBEDDED_AGENT, "semantic", "known_issue"), memory_type="semantic",
                 category="known_issue", title=k.title,
                 content="\n".join(f"{a}: {b}" for a, b in parts if b),
                 structured_data={"application_id": k.application_id, "mechanism": k.mechanism},
                 source=k.origin, verification_status="verified" if k.verified else "unverified",
                 approval_status="not_required", status="archived" if k.archived_at else "active",
                 created_at=_iso(k.created_at), updated_at=_iso(k.updated_at), version=k.version, tags=k.tags,
                 actions=(["update", "archive", "verify", "reject_verification"] if not k.archived_at
                          else ["restore"]))


def view_runbook(r: Runbook, pending: int = 0) -> dict:
    return _base(memory_id=f"rb:{r.id}", backing="sre.runbooks",
                 namespace=default_namespace(EMBEDDED_AGENT, "procedural", "runbook"), memory_type="procedural",
                 category="runbook", title=r.title, content=r.procedure,
                 structured_data={"application_id": r.application_id, "mechanism": r.mechanism,
                                  "risk_level": r.risk_level.value, "requires_approval": r.requires_approval,
                                  "description": r.description},
                 source=r.origin, verification_status="not_applicable", approval_status=r.approval_status,
                 status="archived" if r.archived_at else "active", created_at=_iso(r.created_at),
                 updated_at=_iso(r.updated_at), version=r.version, tags=r.tags,
                 metadata={"approved_by": r.approved_by, "approved_at": _iso(r.approved_at),
                           "pending_revisions": pending},
                 actions=(["propose_update", "archive"] + (["approve", "reject"] if r.approval_status == "pending"
                                                           else []) if not r.archived_at else ["restore"]))


OUTCOME_VERIFY = {"confirmed": "verified", "partially_confirmed": "unverified", "rejected": "rejected",
                  "unverified": "unverified"}


def view_episode(e: AgentEpisode) -> dict:
    return _base(memory_id=f"ep:{e.investigation_id}", backing="sre.agent_episodes",
                 namespace=default_namespace(EMBEDDED_AGENT, "episodic", "investigation"), memory_type="episodic",
                 category="investigation", title=e.summary,
                 content="\n".join(x for x in (f"Hipotez: {e.hypothesis}" if e.hypothesis else None,
                                               f"Çözüm: {e.resolution}" if e.resolution else None,
                                               f"Feedback notu: {e.feedback_note}" if e.feedback_note else None) if x)
                 or e.summary,
                 structured_data={"apps": e.apps, "source_apps": e.source_apps, "mechanism": e.mechanism,
                                  "mechanisms": e.mechanisms, "symptoms": e.symptoms, "severity": e.severity,
                                  "risk_score": float(e.risk_score) if e.risk_score is not None else None,
                                  "outcome": e.outcome, "feedback": e.feedback, "trigger": e.trigger,
                                  "window": [_iso(e.window_start), _iso(e.window_end)]},
                 source="investigation", source_trust="untrusted",
                 evidence_references=[f"inv:{e.investigation_id}"] + ([f"inc:{e.incident_id}"] if e.incident_id
                                                                      else []),
                 verification_status=OUTCOME_VERIFY.get(e.outcome, "unverified"), approval_status="not_required",
                 status="archived" if e.archived_at else "active", created_at=_iso(e.created_at),
                 updated_at=_iso(e.updated_at), version=None, tags=e.symptoms,
                 metadata={"confidence_label": e.confidence,
                           "note": "Episode investigation'dan türetilir; outcome feedback'ten gelir. Doğrulanmamış "
                                   "hipotez kesin bilgi değildir."},
                 actions=["archive"] if not e.archived_at else ["restore"])


def _store_type(prefix: str) -> str:
    return {"app": "episodic", "user": "semantic", "conversation": "short_term"}.get(prefix.split(".")[0], "semantic")


def view_store(row) -> dict:
    prefix, key, value = row["prefix"], row["key"], row["value"] or {}
    head = prefix.split(".")[0]
    ns_public = prefix if head == "app" else f"{head}.<id>" + (".context" if prefix.endswith(".context") else "")
    return _base(memory_id=f"st:{prefix}|{key}", backing="langgraph.store", namespace=f"store:{ns_public}",
                 memory_type=_store_type(prefix), category=f"store:{head}", title=value.get("title") or key,
                 content=json.dumps({k: value.get(k) for k in ("ref", "mechanism", "status", "severity",
                                                                 "risk_score", "root_cause_hypothesis") if k in value},
                                    ensure_ascii=False),
                 structured_data=value, source="agent", source_trust="untrusted",
                 evidence_references=[value["ref"]] if value.get("ref") else [], verification_status="unverified",
                 approval_status="not_required", status="active", created_at=_iso(row["created_at"]),
                 updated_at=_iso(row["updated_at"]), expires_at=_iso(row.get("expires_at")), version=None,
                 metadata={"store_key": key, "note": "AsyncPostgresStore notu; agent yazar, salt okunur"})


# ------------------------------------------------------------------------------------------------ okuma
async def _store_rows(s: AsyncSession, *, types: set[str], q: str | None, limit: int, user_id=None, admin=True):
    heads = [h for h, t in (("app", "episodic"), ("user", "semantic")) if t in types]
    if not heads:
        return [], 0
    conds = " OR ".join(f"prefix LIKE '{h}.%'" for h in heads)
    params: dict[str, Any] = {"lim": limit}
    extra = ""
    if "user" in heads and not admin:          # başkasının kullanıcı bağlamı görünmez
        extra = " AND (prefix NOT LIKE 'user.%' OR prefix = :mine)"
        params["mine"] = f"user.{user_id}.context"
    if q:
        extra += " AND (value::text ILIKE :q OR key ILIKE :q)"
        params["q"] = f"%{q}%"
    base = f"FROM langgraph.store WHERE ({conds}){extra}"
    rows = (await s.execute(text(f"SELECT prefix, key, value, created_at, updated_at, expires_at {base} "
                                 f"ORDER BY updated_at DESC LIMIT :lim"), params)).mappings().all()
    total = await s.scalar(text(f"SELECT count(*) {base}"), {k: v for k, v in params.items() if k != "lim"})
    return [view_store(r) for r in rows], int(total or 0)


async def list_records(s: AsyncSession, *, agent_id: str, memory_type: str | None = None, q: str | None = None,
                       status: str | None = None, verification: str | None = None, backing: str | None = None,
                       page: int = 1, size: int = 50, user=None, admin: bool = True) -> dict:
    types = {memory_type} if memory_type else {"semantic", "episodic", "procedural"}
    want = page * size
    items: list[dict] = []
    totals: dict[str, int] = {}
    if agent_id != EMBEDDED_AGENT:
        types = set()                              # mevcut kaynaklar yalnız gömülü agent'a aittir

    def want_backing(b: str) -> bool:
        return backing in (None, "", b)

    def active_filter(col_archived):
        if status == "archived":
            return col_archived.is_not(None)
        if status in (None, "", "active"):
            return col_archived.is_(None)
        return text("false")

    # memory_records (agent'a göre)
    if want_backing("sre.memory_records"):
        st = select(MemoryRecord).where(MemoryRecord.tenant_id == DEFAULT_TENANT, MemoryRecord.agent_id == agent_id,
                                        MemoryRecord.memory_type.in_(types or {"semantic", "episodic", "procedural"}))
        st = st.where(MemoryRecord.status == (status or "active")) if status != "all" else st
        if verification:
            st = st.where(MemoryRecord.verification_status == verification)
        if q:
            st = st.where(or_(ts_match(MemoryRecord.tsv, q), MemoryRecord.title.ilike(f"%{q}%")))
        totals["sre.memory_records"] = int(await s.scalar(select(func.count()).select_from(st.subquery())) or 0)
        items += [view_record(r) for r in (await s.execute(st.order_by(MemoryRecord.updated_at.desc())
                                                          .limit(want))).scalars()]
    if "semantic" in types and want_backing("sre.known_issues") and verification in (None, "", "verified",
                                                                                      "unverified"):
        st = select(KnownIssue)
        if status != "all":
            st = st.where(active_filter(KnownIssue.archived_at))
        if verification:
            st = st.where(KnownIssue.verified.is_(verification == "verified"))
        if q:
            st = st.where(or_(ts_match(KnownIssue.tsv, q), KnownIssue.title.ilike(f"%{q}%")))
        totals["sre.known_issues"] = int(await s.scalar(select(func.count()).select_from(st.subquery())) or 0)
        items += [view_known_issue(k) for k in (await s.execute(st.order_by(KnownIssue.updated_at.desc())
                                                               .limit(want))).scalars()]
    if "procedural" in types and want_backing("sre.runbooks") and not verification:
        st = select(Runbook)
        if status != "all":
            st = st.where(active_filter(Runbook.archived_at))
        if q:
            st = st.where(or_(ts_match(Runbook.tsv, q), Runbook.title.ilike(f"%{q}%")))
        totals["sre.runbooks"] = int(await s.scalar(select(func.count()).select_from(st.subquery())) or 0)
        rbs = list((await s.execute(st.order_by(Runbook.updated_at.desc()).limit(want))).scalars())
        pend = dict((await s.execute(select(MemoryVersion.record_id, func.count()).where(
            MemoryVersion.record_kind == "runbook", MemoryVersion.status == "pending",
            MemoryVersion.record_id.in_([str(r.id) for r in rbs])).group_by(MemoryVersion.record_id))).all())
        items += [view_runbook(r, pend.get(str(r.id), 0)) for r in rbs]
    if "episodic" in types and want_backing("sre.agent_episodes"):
        st = select(AgentEpisode)
        if status != "all":
            st = st.where(active_filter(AgentEpisode.archived_at))
        if verification:
            inv = {"verified": ["confirmed"], "rejected": ["rejected"],
                   "unverified": ["unverified", "partially_confirmed"]}.get(verification, [])
            st = st.where(AgentEpisode.outcome.in_(inv))
        if q:
            st = st.where(or_(ts_match(AgentEpisode.tsv, q), AgentEpisode.summary.ilike(f"%{q}%")))
        totals["sre.agent_episodes"] = int(await s.scalar(select(func.count()).select_from(st.subquery())) or 0)
        items += [view_episode(e) for e in (await s.execute(st.order_by(AgentEpisode.updated_at.desc())
                                                           .limit(want))).scalars()]
    if want_backing("langgraph.store") and status in (None, "", "active", "all") and not verification \
            and agent_id == EMBEDDED_AGENT:
        rows, n = await _store_rows(s, types=types, q=q, limit=want, user_id=getattr(user, "id", None),
                                    admin=admin)
        items += rows
        totals["langgraph.store"] = n
    items.sort(key=lambda x: x.get("updated_at") or x.get("created_at") or "", reverse=True)
    total = sum(totals.values())
    return {"items": items[(page - 1) * size: page * size], "total": total, "page": page, "size": size,
            "pages": max(1, -(-total // size)), "by_backing": totals,
            "note": "Kaynaklar ayrık tablolardır; toplam kayıt sayısı kaynak sayılarının toplamıdır."}


async def _row(s: AsyncSession, kind: str, rid: str, agent_id: str):
    if kind == "mem":
        r = await s.get(MemoryRecord, uuid.UUID(rid))
        if r is None or r.agent_id != agent_id or r.tenant_id != DEFAULT_TENANT:
            raise MemoryError_(404, "memory kaydı bulunamadı")       # başka agent'ın kaydı: varlığı sızdırılmaz
        return r
    if agent_id != EMBEDDED_AGENT:
        raise MemoryError_(404, "memory kaydı bulunamadı")
    model = {"ki": KnownIssue, "rb": Runbook, "ep": AgentEpisode}.get(kind)
    if model is None:
        raise MemoryError_(404, "memory kaydı bulunamadı")
    r = await s.get(model, uuid.UUID(rid))
    if r is None:
        raise MemoryError_(404, "memory kaydı bulunamadı")
    return r


def _view(kind: str, r) -> dict:
    return {"mem": view_record, "ki": view_known_issue, "rb": view_runbook, "ep": view_episode}[kind](r)


async def get_record(s: AsyncSession, mid: str, *, agent_id: str, user=None, admin: bool = True) -> dict:
    kind, rid = parse_id(mid)
    if kind == "st":
        prefix, _, key = rid.partition("|")
        if prefix.startswith("user.") and not admin and prefix != f"user.{getattr(user, 'id', None)}.context":
            raise MemoryError_(404, "memory kaydı bulunamadı")
        if prefix.startswith("conversation.") or agent_id != EMBEDDED_AGENT:
            raise MemoryError_(404, "memory kaydı bulunamadı")
        row = (await s.execute(text("SELECT prefix, key, value, created_at, updated_at, expires_at "
                                    "FROM langgraph.store WHERE prefix = :p AND key = :k"),
                               {"p": prefix, "k": key})).mappings().first()
        if row is None:
            raise MemoryError_(404, "memory kaydı bulunamadı")
        out = view_store(row)
    else:
        r = await _row(s, kind, rid, agent_id)
        out = _view(kind, r)
        if kind == "rb":
            out["pending_revisions"] = [_ver(v) for v in (await s.execute(
                select(MemoryVersion).where(MemoryVersion.record_kind == "runbook", MemoryVersion.record_id == rid,
                                            MemoryVersion.status == "pending")
                .order_by(MemoryVersion.created_at))).scalars()]
    events.publish("MEMORY_READ", memory_id=mid, memory_type=out["memory_type"], namespace=out["namespace"],
                   actor=getattr(user, "username", None), payload={"via": "platform_api"}, node="platform")
    return out


def _ver(v: MemoryVersion) -> dict:
    return {"id": str(v.id), "version": v.version, "change": v.change, "status": v.status, "actor": v.actor,
            "reason": v.reason, "decided_by": v.decided_by, "decided_at": _iso(v.decided_at),
            "created_at": _iso(v.created_at), "snapshot": v.snapshot}


async def versions(s: AsyncSession, mid: str, *, agent_id: str) -> list[dict]:
    kind, rid = parse_id(mid)
    if kind == "st":
        return []
    await _row(s, kind, rid, agent_id)
    rows = (await s.execute(select(MemoryVersion).where(MemoryVersion.record_kind == KINDS[kind],
                                                        MemoryVersion.record_id == rid)
                            .order_by(MemoryVersion.version.desc(), MemoryVersion.created_at.desc()))).scalars()
    return [_ver(v) for v in rows]


# ------------------------------------------------------------------------------------------------ yazma
def _snap(kind: str, r) -> dict:
    v = _view(kind, r)
    return {k: v.get(k) for k in ("title", "content", "structured_data", "category", "tags", "status",
                                  "verification_status", "approval_status", "confidence_score", "expires_at",
                                  "namespace", "source", "evidence_references")}


async def _next_version(s: AsyncSession, kind: str, rid: str) -> int:
    cur = await s.scalar(select(func.max(MemoryVersion.version)).where(MemoryVersion.record_kind == KINDS[kind],
                                                                      MemoryVersion.record_id == rid))
    return int(cur or 0) + 1


async def _add_version(s, kind: str, rid: str, version: int, change: str, snapshot: dict, actor: str,
                       status: str = "applied", reason: str | None = None, agent_id: str = EMBEDDED_AGENT) -> None:
    s.add(MemoryVersion(tenant_id=DEFAULT_TENANT, agent_id=agent_id, record_kind=KINDS[kind], record_id=rid,
                        version=version, change=change, status=status, snapshot=snapshot, actor=actor,
                        reason=reason))
    await s.flush()


async def _audit(s, actor: str, action: str, target: str, before=None, after=None, result="ok", detail=None):
    """Başarılı işlem: aynı transaction'da. Red / ret: ayrı transaction'da (istek geri alınsa da audit kalır)."""
    if result != "ok":
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from ..security import audit as sec_audit
        await sec_audit.record_separate(async_sessionmaker(s.bind, expire_on_commit=False), actor=actor,
                                        action=action, section="memory", target=target[:128], before=before,
                                        after=after, result=result, detail=detail)
        return
    from ..settings import service
    await service.audit(s, actor=actor, action=action, section="memory", target=target[:128], before=before,
                        after=after, result=result, detail=detail)


async def _reject(s, *, actor, target, memory_type, namespace, res: policy.PolicyResult, op: str):
    codes = [v["code"] for v in res.violations]
    et = "MEMORY_POLICY_VIOLATION" if any(c in ("secret_detected", "prompt_injection", "untrusted_procedural",
                                                "untrusted_self_verify") for c in codes) else "MEMORY_REJECTED"
    events.publish(et, memory_id=target, memory_type=memory_type, namespace=namespace, status="rejected",
                   actor=actor, payload={"operation": op, "violations": res.violations}, node="platform")
    await _audit(s, actor, f"memory_{op}", target or "-", result="rejected", detail=", ".join(codes))
    raise MemoryError_(422, "memory policy reddetti", res.violations)


async def create(s: AsyncSession, *, agent_id: str, actor: str, cfg: dict, data: dict) -> dict:
    mt = data["memory_type"]
    if mt == "episodic":
        raise MemoryError_(422, "episodic memory investigation'lardan türetilir; elle oluşturulamaz")
    source = data.get("source") or "manual"
    if mt == "procedural":
        return await propose_runbook(s, actor=actor, cfg=cfg, data=data)
    if not cfg.get("memory_enabled", True) or not cfg.get("long_term_enabled", True):
        raise MemoryError_(409, "long-term memory Settings'te kapalı")
    ns = data.get("namespace") or default_namespace(agent_id, mt, data.get("category"))
    from .registry import get as get_agent
    a = get_agent(agent_id)
    if a is None or not a.owns(ns):
        raise MemoryError_(403, "namespace bu agent'a ait değil")
    res = policy.validate_write(title=data["title"], content=data["content"],
                                structured_data=data.get("structured_data"), memory_type=mt, source=source, cfg=cfg,
                                requested_verification=data.get("verification_status"))
    if not res.ok:
        await _reject(s, actor=actor, target=None, memory_type=mt, namespace=ns, res=res, op="create")
    dup = await s.scalar(select(MemoryRecord.id).where(MemoryRecord.tenant_id == DEFAULT_TENANT,
                                                      MemoryRecord.agent_id == agent_id, MemoryRecord.namespace == ns,
                                                      MemoryRecord.content_hash == res.content_hash,
                                                      MemoryRecord.status == "active"))
    if dup is not None:
        events.publish("MEMORY_REJECTED", memory_id=f"mem:{dup}", memory_type=mt, namespace=ns, status="rejected",
                       actor=actor, payload={"reason": "duplicate", "policy": "content_hash"}, node="platform")
        raise MemoryError_(409, "aynı içerikte aktif kayıt var", [{"code": "duplicate", "memory_id": f"mem:{dup}"}])
    ttl = int(cfg.get("default_ttl_days") or 0)
    exp = data.get("expires_at") or (datetime.now(timezone.utc) + timedelta(days=ttl) if ttl else None)
    ver = "verified" if (data.get("verification_status") == "verified" and res.trust == "trusted") else "unverified"
    r = MemoryRecord(tenant_id=DEFAULT_TENANT, agent_id=agent_id, namespace=ns, memory_type=mt,
                     category=data.get("category"), title=res.title, content=res.content,
                     structured_data=res.structured_data, source=source, source_trust=res.trust,
                     source_run_id=data.get("source_run_id"), source_thread_id=data.get("source_thread_id"),
                     source_trace_id=data.get("source_trace_id"),
                     evidence_references=list(data.get("evidence_references") or []),
                     confidence_score=data.get("confidence_score"), verification_status=ver,
                     verified_by=actor if ver == "verified" else None, approval_status="not_required",
                     status="active", created_by=actor, expires_at=exp, tags=list(data.get("tags") or []),
                     content_hash=res.content_hash, meta={"redactions": res.redactions} if res.redactions else {})
    s.add(r)
    await s.flush()
    await s.refresh(r)
    await _add_version(s, "mem", str(r.id), 1, "create", _snap("mem", r), actor, agent_id=agent_id)
    events.publish("MEMORY_VALIDATED", memory_id=f"mem:{r.id}", memory_type=mt, namespace=ns, actor=actor,
                   payload=res.public(), node="platform")
    events.publish("MEMORY_WRITE", memory_id=f"mem:{r.id}", memory_type=mt, namespace=ns, actor=actor,
                   payload={"title": r.title, "source": source, "trust": res.trust, "version": 1}, node="platform")
    await _audit(s, actor, "memory_create", f"mem:{r.id}", after={"title": r.title, "memory_type": mt,
                                                                   "namespace": ns})
    return view_record(r)


async def update_record(s: AsyncSession, mid: str, *, agent_id: str, actor: str, cfg: dict, data: dict,
                        expected_version: int | None) -> dict:
    kind, rid = parse_id(mid)
    if kind == "rb":
        return await propose_runbook(s, actor=actor, cfg=cfg, data=data, runbook_id=rid)
    if kind not in ("mem", "ki"):
        raise MemoryError_(422, "bu kayıt türü düzenlenemez")
    r = await _row(s, kind, rid, agent_id)
    if expected_version is not None and r.version != expected_version:
        raise MemoryError_(409, f"kayıt değişmiş (güncel versiyon {r.version})")
    before = _snap(kind, r)
    title = data.get("title", r.title)
    if kind == "mem":
        content, sd, mt, source = data.get("content", r.content), data.get("structured_data", r.structured_data), \
            r.memory_type, r.source
    else:
        content = data.get("content") or "\n".join(x for x in (r.description, r.symptoms, r.root_cause,
                                                              r.resolution) if x)
        sd, mt, source = {}, "semantic", "manual"
    res = policy.validate_write(title=title, content=content, structured_data=sd, memory_type=mt, source=source,
                                cfg=cfg)
    if not res.ok:
        await _reject(s, actor=actor, target=mid, memory_type=mt, namespace=before.get("namespace"), res=res,
                      op="update")
    if kind == "mem":
        r.title, r.content, r.structured_data, r.content_hash = res.title, res.content, res.structured_data, \
            res.content_hash
        for k in ("category", "tags", "confidence_score", "evidence_references", "expires_at"):
            if k in data:
                setattr(r, k, data[k])
        if r.source_trust == "untrusted" and r.verification_status == "verified":
            r.verification_status = "unverified"        # içerik değişti → yeniden doğrulama
    else:
        r.title = res.title
        if "content" in data:
            r.description = res.content
        if "tags" in data:
            r.tags = data["tags"]
    r.version = (r.version or 1) + 1
    await s.flush()
    await s.refresh(r)                         # server-side alanlar (updated_at, tsv) expire olur → yeniden oku
    await _add_version(s, kind, rid, await _next_version(s, kind, rid), "update", _snap(kind, r), actor,
                       agent_id=agent_id)
    events.publish("MEMORY_UPDATE", memory_id=mid, memory_type=mt, namespace=before.get("namespace"), actor=actor,
                   payload={"version": r.version, "changed": sorted(k for k in data if k != "version")},
                   node="platform")
    await _audit(s, actor, "memory_update", mid, before={"title": before["title"]}, after={"title": r.title,
                                                                                         "version": r.version})
    return _view(kind, r)


async def set_status(s: AsyncSession, mid: str, *, agent_id: str, actor: str, op: str, reason: str | None = None
                     ) -> dict:
    """archive | restore | verify | reject_verification"""
    kind, rid = parse_id(mid)
    if kind == "st":
        raise MemoryError_(422, "Store notları salt okunurdur")
    r = await _row(s, kind, rid, agent_id)
    now = datetime.now(timezone.utc)
    ev = {"archive": "MEMORY_ARCHIVE", "restore": "MEMORY_UPDATE", "verify": "MEMORY_VALIDATED",
          "reject_verification": "MEMORY_REJECTED"}[op]
    if op in ("archive", "restore"):
        if kind == "mem":
            r.status = "archived" if op == "archive" else "active"
        else:
            r.archived_at = now if op == "archive" else None
    elif op in ("verify", "reject_verification"):
        if kind == "mem":
            r.verification_status = "verified" if op == "verify" else "rejected"
            r.verified_by = actor
        elif kind == "ki":
            r.verified = op == "verify"
        else:
            raise MemoryError_(422, "bu kayıt türünde doğrulama elle değiştirilmez (episode: feedback)")
    if kind in ("mem", "ki"):
        r.version = (r.version or 1) + 1
    await s.flush()
    await s.refresh(r)
    if kind != "ep":
        await _add_version(s, kind, rid, await _next_version(s, kind, rid), op, _snap(kind, r), actor,
                           reason=reason, agent_id=agent_id)
    v = _view(kind, r)
    events.publish(ev, memory_id=mid, memory_type=v["memory_type"], namespace=v["namespace"], actor=actor,
                   status="ok" if op != "reject_verification" else "rejected", payload={"operation": op,
                                                                                        "reason": reason},
                   node="platform")
    await _audit(s, actor, f"memory_{op}", mid, detail=reason)
    return v


async def delete_record(s: AsyncSession, mid: str, *, agent_id: str, actor: str) -> dict:
    kind, rid = parse_id(mid)
    if kind != "mem":
        raise MemoryError_(422, "yalnız sre.memory_records kayıtları silinebilir; diğerleri arşivlenir")
    r = await _row(s, kind, rid, agent_id)
    snap, mt = _snap("mem", r), r.memory_type
    await _add_version(s, "mem", rid, await _next_version(s, "mem", rid), "delete", snap, actor, agent_id=agent_id)
    await s.delete(r)
    await s.flush()
    events.publish("MEMORY_DELETE", memory_id=mid, memory_type=mt,
                   namespace=snap["namespace"], actor=actor, payload={"title": snap["title"]}, node="platform")
    await _audit(s, actor, "memory_delete", mid, before={"title": snap["title"]})
    return {"deleted": mid, "note": "versiyon geçmişi (silme anı dahil) korunur"}


# ------------------------------------------------------------------------------------------------ procedural
async def propose_runbook(s: AsyncSession, *, actor: str, cfg: dict, data: dict, runbook_id: str | None = None
                          ) -> dict:
    """Procedural memory önerisi. Yeni runbook 'pending' oluşturulur (agent görmez); mevcut runbook için öneri
    `memory_versions` içinde 'pending' revizyon olarak bekler — canlı runbook onaylanana kadar değişmez."""
    source = data.get("source") or "manual"
    title = data.get("title") or ""
    content = data.get("content") or ""
    r = None
    if runbook_id:
        r = await _row(s, "rb", runbook_id, EMBEDDED_AGENT)
        title, content = data.get("title") or r.title, data.get("content") or r.procedure
    res = policy.validate_write(title=title, content=content, structured_data=data.get("structured_data"),
                                memory_type="procedural", source=source, cfg=cfg,
                                requested_approval=data.get("approval_status"))
    if not res.ok:
        await _reject(s, actor=actor, target=f"rb:{runbook_id}" if runbook_id else None, memory_type="procedural",
                      namespace=default_namespace(EMBEDDED_AGENT, "procedural", "runbook"), res=res, op="propose")
    sd = res.structured_data or {}
    if r is None:
        from ..db.models import RiskLevel
        r = Runbook(title=res.title, procedure=res.content, description=sd.get("description"),
                    mechanism=sd.get("mechanism"), application_id=sd.get("application_id"),
                    risk_level=RiskLevel(sd.get("risk_level") or "low"),
                    requires_approval=bool(sd.get("requires_approval", False)), tags=list(data.get("tags") or []),
                    origin="memory:" + source, approval_status="pending", version=1)
        s.add(r)
        await s.flush()
        await s.refresh(r)
        await _add_version(s, "rb", str(r.id), 1, "create", {**_snap("rb", r), "proposed_by": actor}, actor,
                           status="pending")
        mid = f"rb:{r.id}"
    else:
        nv = await _next_version(s, "rb", str(r.id))
        await _add_version(s, "rb", str(r.id), max(nv, r.version + 1), "update",
                           {"title": res.title, "content": res.content, "structured_data": sd,
                            "tags": data.get("tags", r.tags), "proposed_by": actor, "base_version": r.version},
                           actor, status="pending", reason=data.get("reason"))
        mid = f"rb:{r.id}"
    events.publish("MEMORY_WRITE", memory_id=mid, memory_type="procedural",
                   namespace=default_namespace(EMBEDDED_AGENT, "procedural", "runbook"), actor=actor,
                   status="pending", payload={"approval": "pending", "title": res.title}, node="platform")
    await _audit(s, actor, "memory_propose", mid, after={"title": res.title})
    out = view_runbook(r, 1)
    out["proposal"] = "pending"
    return out


async def decide(s: AsyncSession, mid: str, *, actor: str, approve: bool, version_id: str | None = None,
                 reason: str | None = None) -> dict:
    """Procedural onay (four-eyes: öneren onaylayamaz)."""
    kind, rid = parse_id(mid)
    if kind != "rb":
        raise MemoryError_(422, "onay yalnız procedural memory (runbook) içindir")
    r = await _row(s, "rb", rid, EMBEDDED_AGENT)
    q = select(MemoryVersion).where(MemoryVersion.record_kind == "runbook", MemoryVersion.record_id == rid,
                                    MemoryVersion.status == "pending")
    if version_id:
        q = q.where(MemoryVersion.id == uuid.UUID(version_id))
    pv = (await s.execute(q.order_by(MemoryVersion.created_at))).scalars().first()
    if pv is None:
        raise MemoryError_(404, "bekleyen öneri yok")
    if pv.actor == actor:
        await _audit(s, actor, "memory_approve" if approve else "memory_reject", mid, result="denied",
                     detail="four-eyes: öneren onaylayamaz")
        raise MemoryError_(403, "four-eyes: öneriyi yapan kişi onaylayamaz / reddedemez")
    now = datetime.now(timezone.utc)
    pv.status, pv.decided_by, pv.decided_at = ("applied" if approve else "rejected"), actor, now
    if reason:
        pv.reason = (pv.reason + " | " if pv.reason else "") + f"karar: {reason}"
    if approve:
        snap = pv.snapshot or {}
        if pv.change == "update":
            r.title = snap.get("title") or r.title
            r.procedure = snap.get("content") or r.procedure
            sd = snap.get("structured_data") or {}
            if "description" in sd:
                r.description = sd["description"]
            if snap.get("tags") is not None:
                r.tags = snap["tags"]
            r.version = pv.version
        r.approval_status, r.approved_by, r.approved_at = "approved", actor, now
    elif pv.change == "create":
        r.approval_status = "rejected"
    await s.flush()
    await s.refresh(r)
    events.publish("MEMORY_APPROVED" if approve else "MEMORY_REJECTED", memory_id=mid, memory_type="procedural",
                   namespace=default_namespace(EMBEDDED_AGENT, "procedural", "runbook"), actor=actor,
                   status="ok" if approve else "rejected",
                   payload={"version": pv.version, "change": pv.change, "proposed_by": pv.actor}, node="platform")
    await _audit(s, actor, "memory_approve" if approve else "memory_reject", mid,
                 after={"version": pv.version, "proposed_by": pv.actor}, detail=reason)
    out = view_runbook(r)
    return out


async def approvals(s: AsyncSession) -> list[dict]:
    rows = (await s.execute(select(MemoryVersion, Runbook.title).join(
        Runbook, cast(Runbook.id, Text) == MemoryVersion.record_id).where(
        MemoryVersion.record_kind == "runbook", MemoryVersion.status == "pending")
        .order_by(MemoryVersion.created_at))).all()
    return [{**_ver(v), "memory_id": f"rb:{v.record_id}", "title": t} for v, t in rows]


# ------------------------------------------------------------------------------------------------ export
async def export(s: AsyncSession, *, agent_id: str, actor: str, memory_type: str | None, user=None) -> dict:
    data = await list_records(s, agent_id=agent_id, memory_type=memory_type, status="all", page=1, size=5000,
                              user=user, admin=True)
    items = [{k: v for k, v in it.items() if k != "actions"} for it in data["items"]]
    from airlab_agent.redact import redact
    items = redact(items, "export")
    await _audit(s, actor, "memory_export", f"{agent_id}:{memory_type or 'all'}", detail=f"{len(items)} kayıt")
    events.publish("MEMORY_READ", memory_type=memory_type or "mixed", actor=actor, node="platform",
                   payload={"operation": "export", "count": len(items)})
    return {"exported_at": datetime.now(timezone.utc).isoformat(), "agent_id": agent_id, "memory_type": memory_type,
            "count": len(items), "items": items}


# ------------------------------------------------------------------------------------------------ playground
STRATEGIES = {"exact": "Exact Match", "keyword": "Keyword Search (PostgreSQL full-text)",
              "fingerprint": "Fingerprint Similarity (episode belirti parmak izi)"}
NOT_IMPLEMENTED = {"semantic": "Semantic Search (vector) — embedding modeli/pgvector yok",
                   "hybrid": "Hybrid Search — vector bileşeni yok"}


async def playground(s: AsyncSession, *, agent_id: str, query: str, strategy: str, memory_types: list[str],
                     top_k: int, threshold: float | None, since_days: int | None, namespace: str | None,
                     apps: list[str], user=None) -> dict:
    """Salt okunur deneme araması: agent state'i ve access_count değişmez; olay üretilmez."""
    import time
    if strategy in NOT_IMPLEMENTED:
        raise MemoryError_(422, NOT_IMPLEMENTED[strategy] + " — implement edilmedi")
    if strategy not in STRATEGIES:
        raise MemoryError_(422, "bilinmeyen strateji")
    t0 = time.perf_counter()
    since = datetime.now(timezone.utc) - timedelta(days=since_days) if since_days else None
    results: list[dict] = []
    stages = [{"stage": "query", "output": {"query": query, "strategy": strategy, "types": memory_types,
                                            "namespace": namespace, "apps": apps}}]
    if agent_id != EMBEDDED_AGENT:
        memory_types = [t for t in memory_types if t == "semantic"]
    if strategy == "fingerprint":
        from ..repositories import episodes as ep_repo
        syms = [x.strip() for x in query.replace(",", " ").split() if ":" in x or ">" in x]
        fp = {"symptoms": syms, "mechanisms": [x for x in query.split() if x.isidentifier() and "_" in x],
              "mechanism": None}
        token = ep_repo.THRESHOLD.set(float(threshold) if threshold is not None else ep_repo.MIN_SIMILARITY)
        try:
            eps = await ep_repo.similar(s, fp, apps=apps, limit=top_k, since_days=since_days)
        finally:
            ep_repo.THRESHOLD.reset(token)
        for e in eps:
            results.append({"memory_id": "ep:" + e["ref"][4:], "memory_type": "episodic", "title": e["summary"],
                            "similarity": e["similarity"], "score_kind": "fingerprint similarity (0–1)",
                            "shared": e.get("shared_symptoms"), "outcome": e["outcome"]})
        stages.append({"stage": "search", "output": {"fingerprint": fp, "returned": len(results)}})
    else:
        per = []
        if "semantic" in memory_types:
            per.append(("mem", MemoryRecord, MemoryRecord.title, MemoryRecord.content))
            if agent_id == EMBEDDED_AGENT:
                per.append(("ki", KnownIssue, KnownIssue.title, KnownIssue.description))
        if "procedural" in memory_types and agent_id == EMBEDDED_AGENT:
            per.append(("rb", Runbook, Runbook.title, Runbook.procedure))
        if "episodic" in memory_types and agent_id == EMBEDDED_AGENT:
            per.append(("ep", AgentEpisode, AgentEpisode.summary, AgentEpisode.hypothesis))
        for kind, model, tcol, ccol in per:
            st = select(model)
            if strategy == "exact":
                st = st.where(or_(tcol.ilike(f"%{query}%"), ccol.ilike(f"%{query}%")))
                score = None
            else:
                score = ts_rank(model.tsv, query)
                st = st.add_columns(score.label("rank")).where(ts_match(model.tsv, query)).order_by(
                    text("rank DESC"))
            if model is MemoryRecord:
                st = st.where(MemoryRecord.agent_id == agent_id, MemoryRecord.tenant_id == DEFAULT_TENANT,
                              MemoryRecord.status == "active")
                if namespace:
                    st = st.where(MemoryRecord.namespace == namespace)
                if [t for t in memory_types if t != "semantic"] == [] and "semantic" in memory_types:
                    st = st.where(MemoryRecord.memory_type == "semantic")
            elif hasattr(model, "archived_at"):
                st = st.where(model.archived_at.is_(None))
            if since is not None:
                st = st.where((model.updated_at if hasattr(model, "updated_at") else model.created_at) >= since)
            for row in (await s.execute(st.limit(top_k))).all():
                obj, rank = (row[0], row[1]) if len(row) > 1 else (row[0], None)
                v = _view(kind, obj)
                results.append({"memory_id": v["memory_id"], "memory_type": v["memory_type"], "title": v["title"],
                                "similarity": None, "rank": round(float(rank), 4) if rank is not None else None,
                                "score_kind": "ts_rank_cd (sıralama puanı, similarity değil)" if rank is not None
                                else "eşleşme (puan yok)", "verification_status": v["verification_status"]})
        if strategy == "keyword":
            results.sort(key=lambda x: -(x.get("rank") or 0))
        stages.append({"stage": "search", "output": {"sources": [k for k, *_ in per], "returned": len(results)}})
    results = results[:top_k]
    from .context import estimate_tokens
    ms = (time.perf_counter() - t0) * 1000
    stages.append({"stage": "ranking", "output": {"method": "fingerprint similarity" if strategy == "fingerprint"
                                                  else "ts_rank_cd" if strategy == "keyword" else "yok (eşleşme)",
                                                  "reranking": "implement edilmedi"}})
    return {"strategy": strategy, "results": results, "latency_ms": round(ms, 2),
            "context_token_estimate": estimate_tokens(results), "token_estimate_method": "characters / 4",
            "stages": stages, "read_only": True,
            "note": "Playground agent state'ini, access_count'u ve memory event'lerini değiştirmez."}


# ------------------------------------------------------------------------------------------------ otomatik extraction
async def extract_from_episode(s: AsyncSession, ep: AgentEpisode, inv, cfg: dict) -> str | None:
    """Feedback ile DOĞRULANMIŞ (outcome=confirmed) episode'dan servis bağımlılığı olgusu çıkarır.
    Kaynak 'agent_extraction' (güvenilmeyen) → unverified; insan doğrulayana kadar retrieval dışı."""
    if ep.outcome != "confirmed" or not ep.mechanism:
        return None
    rep = inv.report or {}
    c = rep.get("causality") or {}
    src, aff = c.get("source_apps") or [], c.get("affected_apps") or []
    if not src or not aff or set(src) == set(aff):
        return None
    title = f"{', '.join(src)} → {', '.join(a for a in aff if a not in src)} ({ep.mechanism})"[:300]
    content = (f"Doğrulanmış investigation'da {', '.join(src)} kaynaklı {ep.mechanism} etkisi "
               f"{', '.join(aff)} servislerinde gözlendi (bağımlılık olgusu adayı).")
    res = policy.validate_write(title=title, content=content, structured_data={"source_apps": src,
                                                                               "affected_apps": aff,
                                                                               "mechanism": ep.mechanism},
                                memory_type="semantic", source="agent_extraction", cfg=cfg)
    ns = default_namespace(EMBEDDED_AGENT, "semantic", "topology")
    if not res.ok:
        events.publish("MEMORY_REJECTED", memory_type="semantic", namespace=ns, status="rejected",
                       payload={"operation": "extract", "violations": res.violations})
        return None
    dup = await s.scalar(select(MemoryRecord.id).where(MemoryRecord.agent_id == EMBEDDED_AGENT,
                                                      MemoryRecord.namespace == ns,
                                                      MemoryRecord.content_hash == res.content_hash))
    if dup is not None:
        return None
    r = MemoryRecord(tenant_id=DEFAULT_TENANT, agent_id=EMBEDDED_AGENT, namespace=ns, memory_type="semantic",
                     category="topology", title=res.title, content=res.content, structured_data=res.structured_data,
                     source="agent_extraction", source_trust="untrusted", source_run_id=inv.run_id,
                     source_thread_id=inv.run_id, source_trace_id=str(inv.id),
                     evidence_references=[f"inv:{inv.id}"], verification_status="unverified",
                     approval_status="not_required", status="active", created_by="agent",
                     tags=sorted(set(src) | set(aff) | {ep.mechanism}), content_hash=res.content_hash)
    s.add(r)
    await s.flush()
    await _add_version(s, "mem", str(r.id), 1, "create", _snap("mem", r), "agent")
    events.publish("MEMORY_WRITE", memory_id=f"mem:{r.id}", memory_type="semantic", namespace=ns,
                   payload={"source": "agent_extraction", "verification": "unverified",
                            "from": f"inv:{inv.id}"}, actor="agent")
    return f"mem:{r.id}"


# ------------------------------------------------------------------------------------------------ retention
async def retention(s: AsyncSession, cfg: dict, *, dry_run: bool = False) -> dict:
    now = datetime.now(timezone.utc)
    out = {"expired": 0, "purged_records": 0, "purged_events": 0, "purged_retrievals": 0}
    if cfg.get("auto_archive_expired", True):
        q = select(MemoryRecord.id).where(MemoryRecord.status == "active", MemoryRecord.expires_at.is_not(None),
                                          MemoryRecord.expires_at < now)
        ids = list((await s.execute(q)).scalars())
        out["expired"] = len(ids)
        if ids and not dry_run:
            await s.execute(MemoryRecord.__table__.update().where(MemoryRecord.id.in_(ids)).values(status="expired"))
            for i in ids:
                events.publish("MEMORY_EXPIRED", memory_id=f"mem:{i}", memory_type="semantic", actor="system")
    days = int(cfg.get("memory_retention_days") or 0)
    if days:
        cond = (MemoryRecord.status.in_(["archived", "expired", "rejected"]),
                MemoryRecord.updated_at < now - timedelta(days=days))
        out["purged_records"] = int(await s.scalar(select(func.count()).where(*cond)) or 0)
        if not dry_run:
            await s.execute(delete(MemoryRecord).where(*cond))
    edays = int(cfg.get("memory_event_retention_days") or 90)
    cut = now - timedelta(days=edays)
    out["purged_events"] = int(await s.scalar(select(func.count()).where(MemoryEvent.ts < cut)) or 0)
    out["purged_retrievals"] = int(await s.scalar(select(func.count()).where(MemoryRetrieval.ts < cut)) or 0)
    if not dry_run:
        await s.execute(delete(MemoryEvent).where(MemoryEvent.ts < cut))
        await s.execute(delete(MemoryRetrieval).where(MemoryRetrieval.ts < cut))
    return out
