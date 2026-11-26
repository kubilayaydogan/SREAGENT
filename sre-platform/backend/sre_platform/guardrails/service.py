"""Guardrail yönetimi: policy CRUD + sürümleme / rollback, atama (kapsam), yapılandırma, audit zinciri, onay ve ihlal
yaşam döngüleri.

Güvenlik kuralları backend'de uygulanır (arayüz yalnız gösterir):
* mandatory policy disable / monitor yapılamaz, arşivlenemez, kapsamı global'den daraltılamaz; aksiyonu "allow" ya da
  "warn" gibi zayıflatan bir değere çekilemez.
* parametreler evaluator şemasıyla (pydantic, extra=forbid) doğrulanır; özel kalıplar yalnız regex'tir.
* her değişiklik yeni bir değişmez sürüm (checksum) + hash zincirli audit kaydı üretir; config revision artar ve
  sonraki koşular yeni snapshot'ı yükler.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from airlab_agent.guardrails import catalog, evaluators
from airlab_agent.guardrails.engine import DEFAULT_CONFIG, merged_config
from airlab_agent.guardrails.model import (ACTIONS, CATEGORIES, MODES, SCOPES, SEVERITIES, STAGES, action_hash)
from airlab_agent.redact import redact, redact_text
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import correlation_id
from ..db.models import (GuardrailApproval, GuardrailAuditEvent, GuardrailConfiguration, GuardrailPolicy,
                         GuardrailPolicyAssignment, GuardrailPolicyVersion, GuardrailViolation)

TENANT = "default"          # tek organizasyon modeli (security-decisions karar 1); tenant_id her kayıtta tutulur
WEAK_ACTIONS = {"allow", "warn"}
VIOLATION_STATUSES = ("open", "acknowledged", "investigating", "resolved", "false_positive")
VIOLATION_FLOW = {"open": {"acknowledged", "investigating", "resolved", "false_positive"},
                  "acknowledged": {"investigating", "resolved", "false_positive", "open"},
                  "investigating": {"resolved", "false_positive", "acknowledged"},
                  "resolved": {"open"}, "false_positive": {"open"}}


class GuardrailError(Exception):
    def __init__(self, status: int, code: str, message: str, details: list | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details or []


def now() -> datetime:
    return datetime.now(timezone.utc)


# ================================================================================================= audit (hash zinciri)
def _h(prev: str, row: dict) -> str:
    raw = json.dumps(row, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256((prev + raw).encode()).hexdigest()


async def audit(s: AsyncSession, *, actor: str, action: str, target: str | None = None, before: Any = None,
                after: Any = None, detail: str | None = None) -> None:
    # Zincir sırası: aynı anda iki yazıcı aynı prev_hash'i almasın (transaction-level advisory lock)
    await s.execute(text("SELECT pg_advisory_xact_lock(hashtext('sre.guardrail_audit'))"))
    prev = (await s.execute(select(GuardrailAuditEvent.hash).order_by(GuardrailAuditEvent.seq.desc()).limit(1)))\
        .scalar_one_or_none() or "0" * 64
    ts = now()
    cid = correlation_id.get()
    row = {"ts": ts.isoformat(), "tenant_id": TENANT, "actor": actor[:64], "action": action[:48],
           "target": (target or None) and target[:160], "before": redact(before, "audit") if before is not None else None,
           "after": redact(after, "audit") if after is not None else None,
           "detail": redact_text(detail, "audit")[:1000] if detail else None,
           "correlation_id": None if cid == "-" else cid}
    s.add(GuardrailAuditEvent(ts=ts, prev_hash=prev, hash=_h(prev, row), **{k: v for k, v in row.items() if k != "ts"}))
    await s.flush()


async def verify_audit(s: AsyncSession, limit: int = 100000) -> dict:
    rows = (await s.execute(select(GuardrailAuditEvent).order_by(GuardrailAuditEvent.seq).limit(limit))).scalars().all()
    if not rows:
        return {"ok": True, "checked": 0, "broken_at": None}
    prev = rows[0].prev_hash                # retention sonrası zincir en eski kalan kaydın prev_hash'inden başlar
    for r in rows:
        row = {"ts": r.ts.isoformat(), "tenant_id": r.tenant_id, "actor": r.actor, "action": r.action,
               "target": r.target, "before": r.before, "after": r.after, "detail": r.detail,
               "correlation_id": r.correlation_id}
        if r.prev_hash != prev or _h(prev, row) != r.hash:
            return {"ok": False, "checked": len(rows), "broken_at": r.seq}
        prev = r.hash
    return {"ok": True, "checked": len(rows), "broken_at": None}


async def list_audit(s: AsyncSession, limit: int = 100) -> list[dict]:
    rows = (await s.execute(select(GuardrailAuditEvent).order_by(GuardrailAuditEvent.seq.desc()).limit(limit))).scalars()
    return [{"seq": r.seq, "ts": r.ts.isoformat(), "actor": r.actor, "action": r.action, "target": r.target,
             "detail": r.detail, "before": r.before, "after": r.after, "hash": r.hash[:16]} for r in rows]


# ================================================================================================= yapılandırma
async def _config_row(s: AsyncSession, lock: bool = False) -> GuardrailConfiguration:
    q = select(GuardrailConfiguration).where(GuardrailConfiguration.id == 1)
    if lock:
        q = q.with_for_update()
    row = (await s.execute(q)).scalar_one_or_none()
    if row is None:
        await s.execute(pg_insert(GuardrailConfiguration).values(id=1, revision=1, data={}).on_conflict_do_nothing())
        row = (await s.execute(q)).scalar_one()
    return row


async def bump(s: AsyncSession) -> int:
    row = await _config_row(s, lock=True)
    row.revision += 1
    row.updated_at = now()
    return row.revision


async def get_config(s: AsyncSession) -> dict:
    row = await _config_row(s)
    return {"revision": row.revision, "data": merged_config(row.data), "updated_by": row.updated_by,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "fixed": {"fail_closed_categories": ["tool", "mcp", "data_protection", "memory", "execution", "action"],
                      "mandatory_enforce": True, "classifier_modes": ["deterministic"]}}


def validate_config(data: dict) -> dict:
    cfg = merged_config(data)
    bad = []
    if cfg["default_mode"] not in ("monitor", "enforce"):
        bad.append("default_mode: monitor | enforce")
    if not 1 <= int(cfg["evaluation_timeout_ms"]) <= 5000:
        bad.append("evaluation_timeout_ms 1..5000")
    if not 0 <= int(cfg["max_evaluation_retries"]) <= 3:
        bad.append("max_evaluation_retries 0..3")
    for k, lo, hi in (("evaluation_days", 1, 365), ("snapshot_days", 1, 90), ("audit_days", 30, 3650),
                      ("test_run_days", 1, 365)):
        v = int((cfg["retention"] or {}).get(k, 0))
        if not lo <= v <= hi:
            bad.append(f"retention.{k} {lo}..{hi}")
    if not 1000 <= int(cfg["masking"].get("max_snapshot_bytes", 0)) <= 64000:
        bad.append("masking.max_snapshot_bytes 1000..64000")
    if cfg["classifier"].get("mode") != "deterministic":
        bad.append("classifier.mode: yalnız 'deterministic' (model tabanlı sınıflandırıcı bu sürümde yok)")
    if not 1 <= int(cfg["approval_timeout_minutes"]) <= 10080:
        bad.append("approval_timeout_minutes 1..10080")
    for c, m in (cfg.get("fail_mode") or {}).items():
        if c not in ("input", "model_context", "output", "evidence") or m not in ("open", "closed"):
            bad.append(f"fail_mode.{c}: yalnız input/model_context/output/evidence için open|closed")
    ks = cfg.get("kill_switch") or {}
    if not isinstance(ks.get("global"), bool) or not isinstance(ks.get("agents"), list):
        bad.append("kill_switch: {global: bool, agents: [..]}")
    if cfg["notifications"].get("min_severity") not in SEVERITIES:
        bad.append("notifications.min_severity")
    if cfg.get("optional_defaults", {}).get("mode") not in ("monitor", "enforce", "disabled"):
        bad.append("optional_defaults.mode")
    unknown = set(data) - set(DEFAULT_CONFIG)
    if unknown:
        bad.append(f"bilinmeyen alan: {sorted(unknown)}")
    if bad:
        raise GuardrailError(422, "config_invalid", "geçersiz guardrail yapılandırması", bad)
    return cfg


async def update_config(s: AsyncSession, *, actor: str, revision: int, data: dict) -> dict:
    row = await _config_row(s, lock=True)
    if row.revision != revision:
        raise GuardrailError(409, "revision_conflict", "yapılandırma başka biri tarafından değiştirildi; yenileyin")
    cfg = validate_config(data)
    before = merged_config(row.data)
    row.data = {k: v for k, v in cfg.items()}
    row.revision += 1
    row.updated_by, row.updated_at = actor, now()
    await audit(s, actor=actor, action="guardrail.config.update", target="config", before=before, after=cfg)
    return await get_config(s)


# ================================================================================================= policy
def _checksum(d: dict) -> str:
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()


def policy_def(p: GuardrailPolicy, assignments: list) -> dict:
    return {"key": p.key, "name": p.name, "description": p.description, "category": p.category,
            "type": p.policy_type, "severity": p.severity, "mode": p.mode, "status": p.status,
            "mandatory": p.mandatory, "stages": list(p.stages or []), "action": p.action, "priority": p.priority,
            "params": p.params or {}, "bound_to": p.bound_to,
            "assignments": sorted([{"scope": a.scope, "value": a.value} for a in assignments],
                                  key=lambda x: (x["scope"], x["value"] or ""))}


def view(p: GuardrailPolicy, assignments: list) -> dict:
    d = policy_def(p, assignments)
    ev = evaluators.REGISTRY.get(p.policy_type)
    return {**d, "id": str(p.id), "builtin": p.builtin, "version": p.version, "latest_version": p.latest_version,
            "scope": min((a["scope"] for a in d["assignments"]), key=SCOPES.index, default="global"),
            "created_by": p.created_by, "updated_by": p.updated_by,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            "last_triggered_at": p.last_triggered_at.isoformat() if p.last_triggered_at else None,
            "type_title": ev.title if ev else ("başka katmanda uygulanır" if p.bound_to else p.policy_type),
            "enforced_by": p.bound_to or "guardrail_engine"}


def validate_def(d: dict, *, existing: GuardrailPolicy | None = None) -> dict:
    """Policy tanımı doğrulaması (backend tek kaynak). Geçersiz → GuardrailError 422."""
    bad: list[str] = []
    out = dict(d)
    if not isinstance(out.get("name"), str) or not 3 <= len(out["name"].strip()) <= 160:
        bad.append("name 3..160 karakter")
    if out.get("category") not in CATEGORIES:
        bad.append(f"category: {', '.join(CATEGORIES)}")
    t = out.get("type")
    builtin_bound = existing is not None and existing.bound_to
    ev = evaluators.REGISTRY.get(t)
    if not builtin_bound and ev is None:
        bad.append(f"type: kayıtlı evaluator türü olmalı ({', '.join(sorted(evaluators.REGISTRY))})")
    if out.get("severity") not in SEVERITIES:
        bad.append("severity")
    if out.get("mode") not in MODES:
        bad.append("mode: monitor | enforce | disabled")
    if out.get("action") not in ACTIONS:
        bad.append(f"action: {', '.join(ACTIONS)}")
    stages = out.get("stages") or (list(ev.stages) if ev else [])
    if not stages or any(st not in STAGES for st in stages):
        bad.append("stages: geçerli execution stage listesi")
    elif ev and any(st not in ev.stages for st in stages):
        bad.append(f"stages: bu evaluator yalnız {list(ev.stages)} aşamalarında çalışır")
    out["stages"] = list(stages)
    if not isinstance(out.get("priority", 100), int) or not 1 <= out.get("priority", 100) <= 1000:
        bad.append("priority 1..1000")
    if ev is not None:
        try:
            out["params"] = ev.Params(**(out.get("params") or {})).model_dump()
        except Exception as e:  # noqa: BLE001
            bad.append(f"params: {str(e)[:400]}")
    else:
        out["params"] = out.get("params") or {}
    asg = out.get("assignments") or [{"scope": "global", "value": None}]
    for a in asg:
        if a.get("scope") not in SCOPES:
            bad.append(f"assignment scope: {', '.join(SCOPES)}")
        elif a["scope"] != "global" and not (isinstance(a.get("value"), str) and 1 <= len(a["value"]) <= 160):
            bad.append(f"assignment {a['scope']}: değer gerekli")
    out["assignments"] = [{"scope": a["scope"], "value": None if a["scope"] == "global" else a["value"]} for a in asg
                          if a.get("scope") in SCOPES]
    mandatory = bool(existing.mandatory) if existing is not None else bool(out.get("mandatory"))
    out["mandatory"] = mandatory
    if mandatory:
        if out.get("mode") != "enforce":
            bad.append("mandatory policy disable ya da monitor yapılamaz")
        if out.get("status", "active") != "active":
            bad.append("mandatory policy devre dışı bırakılamaz / arşivlenemez")
        if not any(a["scope"] == "global" for a in out["assignments"]):
            bad.append("mandatory policy'nin kapsamı global'den daraltılamaz")
        if out.get("action") in WEAK_ACTIONS and (existing is None or existing.action not in WEAK_ACTIONS):
            bad.append("mandatory policy'nin aksiyonu zayıflatılamaz (allow / warn)")
    if existing is not None and existing.builtin:
        if out.get("type") != existing.policy_type:
            bad.append("yerleşik policy'nin türü değiştirilemez")
        if existing.bound_to and out.get("params"):
            out["params"] = {}
    if bad:
        raise GuardrailError(422, "policy_invalid", "geçersiz policy tanımı", bad)
    return out


async def _assignments(s: AsyncSession, pid: uuid.UUID) -> list[GuardrailPolicyAssignment]:
    return list((await s.execute(select(GuardrailPolicyAssignment)
                                 .where(GuardrailPolicyAssignment.policy_id == pid))).scalars())


async def _set_assignments(s: AsyncSession, p: GuardrailPolicy, items: list[dict]) -> None:
    for a in await _assignments(s, p.id):
        await s.delete(a)
    await s.flush()
    for a in items:
        s.add(GuardrailPolicyAssignment(policy_id=p.id, tenant_id=TENANT, scope=a["scope"], value=a["value"]))
    await s.flush()


async def _version(s: AsyncSession, p: GuardrailPolicy, d: dict, change: str, actor: str, note: str | None) -> int:
    nxt = (await s.execute(select(func.coalesce(func.max(GuardrailPolicyVersion.version), 0))
                           .where(GuardrailPolicyVersion.policy_id == p.id))).scalar_one() + 1
    s.add(GuardrailPolicyVersion(policy_id=p.id, version=nxt, snapshot=d, checksum=_checksum(d), change=change,
                                 note=note, created_by=actor))
    await s.flush()
    return nxt


def _apply(p: GuardrailPolicy, d: dict) -> None:
    p.name, p.description, p.category = d["name"].strip(), d.get("description"), d["category"]
    p.policy_type, p.severity, p.mode, p.status = d["type"], d["severity"], d["mode"], d.get("status", "active")
    p.stages, p.action, p.priority, p.params = d["stages"], d["action"], int(d.get("priority", 100)), d["params"]
    p.mandatory = d["mandatory"]


async def ensure_builtins(s: AsyncSession) -> int:
    """Eksik yerleşik policy'leri ekler (mevcutlara dokunmaz). İdempotent."""
    have = set((await s.execute(select(GuardrailPolicy.key))).scalars())
    n = 0
    for d in catalog.builtin_policies():
        if d["key"] in have:
            continue
        p = GuardrailPolicy(key=d["key"], tenant_id=TENANT, builtin=True, bound_to=d["bound_to"], version=1,
                            latest_version=1, created_by="system", updated_by="system", name=d["name"],
                            description=d["description"], category=d["category"], policy_type=d["type"],
                            severity=d["severity"], mode="enforce", status="active", stages=d["stages"],
                            action=d["action"], priority=d["priority"], params=d["params"], mandatory=d["mandatory"])
        ins = pg_insert(GuardrailPolicy).values(
            id=uuid.uuid4(), key=p.key, tenant_id=TENANT, builtin=True, bound_to=p.bound_to, version=1,
            latest_version=1, created_by="system", updated_by="system", name=p.name, description=p.description,
            category=p.category, policy_type=p.policy_type, severity=p.severity, mode="enforce", status="active",
            stages=p.stages, action=p.action, priority=p.priority, params=p.params, mandatory=p.mandatory,
        ).on_conflict_do_nothing(index_elements=["key"]).returning(GuardrailPolicy.id)
        pid = (await s.execute(ins)).scalar_one_or_none()
        if pid is None:
            continue
        s.add(GuardrailPolicyAssignment(policy_id=pid, tenant_id=TENANT, scope="global", value=None))
        snap = {**{k: d[k] for k in ("key", "name", "description", "category", "type", "severity", "mode", "status",
                                     "mandatory", "stages", "action", "priority", "params", "bound_to")},
                "assignments": [{"scope": "global", "value": None}]}
        s.add(GuardrailPolicyVersion(policy_id=pid, version=1, snapshot=snap, checksum=_checksum(snap), change="seed",
                                     created_by="system"))
        n += 1
    if n:
        await s.flush()
        await bump(s)
    return n


async def get_policy(s: AsyncSession, pid: str) -> GuardrailPolicy:
    try:
        u = uuid.UUID(str(pid))
        p = await s.get(GuardrailPolicy, u)
    except ValueError:
        p = (await s.execute(select(GuardrailPolicy).where(GuardrailPolicy.key == pid))).scalar_one_or_none()
    if p is None:
        raise GuardrailError(404, "not_found", "policy bulunamadı")
    return p


async def list_policies(s: AsyncSession, *, category: str | None = None, q: str | None = None,
                        status: str | None = None) -> list[dict]:
    qq = select(GuardrailPolicy).order_by(GuardrailPolicy.category, GuardrailPolicy.priority, GuardrailPolicy.key)
    if category:
        qq = qq.where(GuardrailPolicy.category == category)
    if status:
        qq = qq.where(GuardrailPolicy.status == status)
    if q:
        like = f"%{q.lower()}%"
        qq = qq.where(func.lower(GuardrailPolicy.name).like(like) | func.lower(GuardrailPolicy.key).like(like))
    ps = list((await s.execute(qq)).scalars())
    asg = {}
    if ps:
        for a in (await s.execute(select(GuardrailPolicyAssignment)
                                  .where(GuardrailPolicyAssignment.policy_id.in_([p.id for p in ps])))).scalars():
            asg.setdefault(a.policy_id, []).append(a)
    return [view(p, asg.get(p.id, [])) for p in ps]


async def policy_detail(s: AsyncSession, pid: str) -> dict:
    p = await get_policy(s, pid)
    vs = (await s.execute(select(GuardrailPolicyVersion).where(GuardrailPolicyVersion.policy_id == p.id)
                          .order_by(GuardrailPolicyVersion.version.desc()))).scalars()
    return {**view(p, await _assignments(s, p.id)),
            "versions": [{"version": v.version, "change": v.change, "note": v.note, "checksum": v.checksum[:12],
                          "created_by": v.created_by, "created_at": v.created_at.isoformat(), "active": v.version == p.version,
                          "snapshot": v.snapshot} for v in vs],
            "evaluator": evaluators.describe(p.policy_type) if p.policy_type in evaluators.REGISTRY else None}


async def create_policy(s: AsyncSession, *, actor: str, data: dict) -> dict:
    data = {**data, "mode": data.get("mode") or (merged_config((await _config_row(s)).data)
                                                 .get("optional_defaults") or {}).get("mode", "enforce")}
    d = validate_def(data)
    key = (data.get("key") or "").strip().lower()
    if not key or not all(c.isalnum() or c == "_" for c in key) or len(key) > 80:
        raise GuardrailError(422, "policy_invalid", "key: küçük harf, rakam ve _ (en fazla 80)")
    if (await s.execute(select(GuardrailPolicy.id).where(GuardrailPolicy.key == key))).first():
        raise GuardrailError(409, "duplicate", "bu key ile policy var")
    p = GuardrailPolicy(key=key, tenant_id=TENANT, builtin=False, bound_to=None, version=1, latest_version=1,
                        created_by=actor, updated_by=actor)
    _apply(p, d)
    s.add(p)
    await s.flush()
    await _set_assignments(s, p, d["assignments"])
    snap = {**policy_def(p, await _assignments(s, p.id))}
    await _version(s, p, snap, "create", actor, data.get("note"))
    await bump(s)
    await audit(s, actor=actor, action="guardrail.policy.create", target=key, after=snap)
    return await policy_detail(s, str(p.id))


async def update_policy(s: AsyncSession, pid: str, *, actor: str, version: int, data: dict,
                        note: str | None = None) -> dict:
    p = await get_policy(s, pid)
    await s.refresh(p, with_for_update=True)
    if p.latest_version != version and p.version != version:
        raise GuardrailError(409, "version_conflict", "policy başka biri tarafından değiştirildi; yenileyin")
    before = policy_def(p, await _assignments(s, p.id))
    merged = {**before, **{k: v for k, v in data.items() if k in before or k == "assignments"}}
    if p.bound_to:
        merged["type"] = p.policy_type
    d = validate_def(merged, existing=p)
    _apply(p, d)
    await _set_assignments(s, p, d["assignments"])
    after = policy_def(p, await _assignments(s, p.id))
    if after == before:
        return await policy_detail(s, str(p.id))
    v = await _version(s, p, after, "update", actor, note)
    p.version = p.latest_version = v
    p.updated_by, p.updated_at = actor, now()
    await bump(s)
    await audit(s, actor=actor, action="guardrail.policy.update", target=p.key, before=before, after=after,
                detail=note)
    return await policy_detail(s, str(p.id))


async def activate_version(s: AsyncSession, pid: str, version: int, *, actor: str, rollback: bool = False,
                           note: str | None = None) -> dict:
    """Önceki bir sürümü yeniden aktif eder (rollback). Mandatory kuralları yine uygulanır; aktif sürüm yeni bir sürüm
    olarak kaydedilir (geçmiş değişmez)."""
    p = await get_policy(s, pid)
    await s.refresh(p, with_for_update=True)
    v = (await s.execute(select(GuardrailPolicyVersion).where(GuardrailPolicyVersion.policy_id == p.id,
                                                              GuardrailPolicyVersion.version == version)))\
        .scalar_one_or_none()
    if v is None:
        raise GuardrailError(404, "not_found", "sürüm bulunamadı")
    before = policy_def(p, await _assignments(s, p.id))
    d = validate_def({**v.snapshot, "status": v.snapshot.get("status", "active")}, existing=p)
    _apply(p, d)
    await _set_assignments(s, p, d["assignments"])
    after = policy_def(p, await _assignments(s, p.id))
    nv = await _version(s, p, after, "rollback" if rollback else "activate", actor,
                        note or f"v{version} yeniden aktif edildi")
    p.version = p.latest_version = nv
    p.updated_by, p.updated_at = actor, now()
    await bump(s)
    await audit(s, actor=actor, action="guardrail.policy.rollback" if rollback else "guardrail.policy.activate",
                target=p.key, before=before, after=after, detail=f"kaynak sürüm v{version}")
    return await policy_detail(s, str(p.id))


async def clone_policy(s: AsyncSession, pid: str, *, actor: str, key: str, name: str | None = None) -> dict:
    p = await get_policy(s, pid)
    if p.bound_to:
        raise GuardrailError(409, "not_clonable", "başka katmanda uygulanan policy kopyalanamaz")
    d = policy_def(p, await _assignments(s, p.id))
    d.update(key=key, name=name or f"{p.name} (kopya)", mandatory=False)
    out = await create_policy(s, actor=actor, data=d)
    await audit(s, actor=actor, action="guardrail.policy.clone", target=key, detail=f"kaynak {p.key}")
    return out


async def assign(s: AsyncSession, pid: str, *, actor: str, version: int, assignments: list[dict]) -> dict:
    return await update_policy(s, pid, actor=actor, version=version, data={"assignments": assignments},
                               note="kapsam değişikliği")


# ================================================================================================= ihlaller
def fingerprint(ev: dict) -> str:
    raw = "|".join(str(ev.get(k) or "") for k in ("tenant_id", "policy_key", "code", "agent_id", "stage", "node",
                                                    "mcp_server", "tool"))
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


async def list_violations(s: AsyncSession, *, status: str | None = None, severity: str | None = None,
                          category: str | None = None, agent: str | None = None, q: str | None = None,
                          since: datetime | None = None, limit: int = 50, offset: int = 0) -> dict:
    qq = select(GuardrailViolation).where(GuardrailViolation.tenant_id == TENANT)
    if status:
        qq = qq.where(GuardrailViolation.status == status)
    if severity:
        qq = qq.where(GuardrailViolation.severity == severity)
    if category:
        qq = qq.where(GuardrailViolation.category == category)
    if agent:
        qq = qq.where((GuardrailViolation.agent_id == agent) | (GuardrailViolation.agent_key == agent))
    if since:
        qq = qq.where(GuardrailViolation.last_seen >= since)
    if q:
        like = f"%{q.lower()}%"
        qq = qq.where(func.lower(GuardrailViolation.reason).like(like) | func.lower(GuardrailViolation.policy_key)
                      .like(like) | (GuardrailViolation.run_id == q))
    total = (await s.execute(select(func.count()).select_from(qq.subquery()))).scalar_one()
    rows = (await s.execute(qq.order_by(GuardrailViolation.last_seen.desc()).limit(limit).offset(offset))).scalars()
    return {"total": total, "items": [violation_view(v) for v in rows]}


def violation_view(v: GuardrailViolation) -> dict:
    return {"id": str(v.id), "severity": v.severity, "category": v.category, "policy_key": v.policy_key,
            "agent_id": v.agent_id, "agent_key": v.agent_key, "run_id": v.run_id, "node": v.node, "stage": v.stage,
            "mcp_server": v.mcp_server, "tool": v.tool, "reason": v.reason, "code": v.code, "decision": v.decision,
            "action_taken": v.action_taken, "enforced": v.enforced, "first_seen": v.first_seen.isoformat(),
            "last_seen": v.last_seen.isoformat(), "occurrences": v.occurrences, "status": v.status,
            "assigned_to": v.assigned_to, "history": v.history or [], "fingerprint": v.fingerprint[:12],
            "last_evaluation_id": str(v.last_evaluation_id) if v.last_evaluation_id else None}


async def update_violation(s: AsyncSession, vid: str, *, actor: str, status: str | None = None,
                           assigned_to: str | None = None, note: str | None = None) -> dict:
    v = await s.get(GuardrailViolation, uuid.UUID(vid), with_for_update=True)
    if v is None or v.tenant_id != TENANT:
        raise GuardrailError(404, "not_found", "ihlal bulunamadı")
    before = {"status": v.status, "assigned_to": v.assigned_to}
    if status and status != v.status:
        if status not in VIOLATION_FLOW.get(v.status, set()):
            raise GuardrailError(409, "invalid_transition", f"{v.status} → {status} geçişi yapılamaz")
        if status == "open":
            # Kapanmış bir ihlali yeniden açmak: aynı fingerprint'le açık kayıt varsa çakışır
            dup = (await s.execute(select(GuardrailViolation.id).where(
                GuardrailViolation.fingerprint == v.fingerprint, GuardrailViolation.id != v.id,
                GuardrailViolation.status.in_(("open", "acknowledged", "investigating"))))).first()
            if dup:
                raise GuardrailError(409, "duplicate_open", "aynı ihlal için açık bir kayıt var")
        v.status = status
    if assigned_to is not None:
        v.assigned_to = assigned_to[:64] or None
    v.history = list(v.history or []) + [{"ts": now().isoformat(), "by": actor, "status": v.status,
                                          "assigned_to": v.assigned_to, "note": redact_text(note)[:500] if note else None}]
    v.updated_at, v.updated_by = now(), actor
    # NOT: ihlali çözmek ilgili policy'yi DEĞİŞTİRMEZ / devre dışı bırakmaz.
    await audit(s, actor=actor, action="guardrail.violation.update", target=str(v.id), before=before,
                after={"status": v.status, "assigned_to": v.assigned_to}, detail=note)
    return violation_view(v)


# ================================================================================================= onaylar
def approval_view(a: GuardrailApproval) -> dict:
    return {"id": str(a.id), "agent_id": a.agent_id, "agent_key": a.agent_key, "run_id": a.run_id,
            "thread_id": a.thread_id, "requested_action": a.requested_action, "target_resource": a.target_resource,
            "risk_level": a.risk_level, "requested_by": a.requested_by, "required_role": a.required_role,
            "requested_at": a.requested_at.isoformat(), "expires_at": a.expires_at.isoformat(), "status": a.status,
            "reason": a.reason, "decision_reason": a.decision_reason, "decided_by": a.decided_by,
            "decided_at": a.decided_at.isoformat() if a.decided_at else None, "action_params": a.action_params,
            "action_hash": a.action_hash[:16], "policy_key": a.policy_key, "policy_version": a.policy_version,
            "consumed_at": a.consumed_at.isoformat() if a.consumed_at else None, "result": a.result,
            "evaluation_id": str(a.evaluation_id) if a.evaluation_id else None}


async def expire_approvals(s: AsyncSession) -> int:
    r = await s.execute(update(GuardrailApproval).where(GuardrailApproval.status == "pending",
                                                        GuardrailApproval.expires_at < now())
                        .values(status="expired"))
    return r.rowcount or 0


async def list_approvals(s: AsyncSession, *, status: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    await expire_approvals(s)
    qq = select(GuardrailApproval).where(GuardrailApproval.tenant_id == TENANT)
    if status:
        qq = qq.where(GuardrailApproval.status == status)
    total = (await s.execute(select(func.count()).select_from(qq.subquery()))).scalar_one()
    rows = (await s.execute(qq.order_by(GuardrailApproval.requested_at.desc()).limit(limit).offset(offset))).scalars()
    return {"total": total, "items": [approval_view(a) for a in rows]}


async def decide_approval(s: AsyncSession, aid: str, *, actor_id: str, actor_role: str, approve: bool,
                          reason: str | None) -> dict:
    a = await s.get(GuardrailApproval, uuid.UUID(aid), with_for_update=True)
    if a is None or a.tenant_id != TENANT:
        raise GuardrailError(404, "not_found", "onay isteği bulunamadı")
    if a.status == "pending" and a.expires_at < now():
        a.status = "expired"
    if a.status != "pending":
        raise GuardrailError(409, "not_pending", f"onay isteği '{a.status}' durumunda; karar verilemez")
    if a.requested_by == actor_id:
        raise GuardrailError(403, "four_eyes", "isteği başlatan kendi isteğini onaylayamaz / reddedemez")
    order = ["viewer", "sre", "admin"]
    if a.required_role not in order or order.index(actor_role) < order.index(a.required_role):
        raise GuardrailError(403, "role_required", f"bu karar için '{a.required_role}' rolü gerekli")
    if approve and not (reason and reason.strip()):
        raise GuardrailError(422, "reason_required", "onay gerekçesi zorunlu")
    a.status = "approved" if approve else "rejected"
    a.decided_by, a.decided_at = actor_id, now()
    a.decision_reason = redact_text(reason or "")[:1000] or None
    await audit(s, actor=actor_id, action="guardrail.approval." + a.status, target=str(a.id),
                after={"status": a.status, "action": a.requested_action, "hash": a.action_hash[:16]}, detail=reason)
    return approval_view(a)


async def cancel_approval(s: AsyncSession, aid: str, *, actor_id: str) -> dict:
    a = await s.get(GuardrailApproval, uuid.UUID(aid), with_for_update=True)
    if a is None or a.tenant_id != TENANT:
        raise GuardrailError(404, "not_found", "onay isteği bulunamadı")
    if a.status not in ("pending", "approved"):
        raise GuardrailError(409, "not_cancellable", f"'{a.status}' durumundaki istek iptal edilemez")
    a.status = "cancelled"
    a.decided_by, a.decided_at = a.decided_by or actor_id, a.decided_at or now()
    await audit(s, actor=actor_id, action="guardrail.approval.cancelled", target=str(a.id))
    return approval_view(a)


async def consume_approval(s: AsyncSession, aid: str, *, consumer: str, tenant_id: str, agent_id: str | None,
                           action: str, resource: str, params: Any, recheck=None) -> GuardrailApproval:
    """Onaylı eylemi yürütmek isteyen bir yürütücünün TEK giriş noktası (bu sürümde yürütücü yok; altyapı hazır).
    Kontroller: durum approved, süresi dolmamış, tek kullanımlık (consumed değil), eylem/kaynak/tenant/parametre özeti
    birebir aynı (replay ve parametre değişikliği reddedilir), yürütme öncesi yetki + policy yeniden kontrolü
    (`recheck` → False ise reddedilir)."""
    a = await s.get(GuardrailApproval, uuid.UUID(aid), with_for_update=True)
    if a is None:
        raise GuardrailError(404, "not_found", "onay bulunamadı")
    if a.status == "pending" and a.expires_at < now():
        a.status = "expired"
    if a.status == "executed" or a.consumed_at is not None:
        raise GuardrailError(409, "replay", "onay daha önce kullanıldı (replay)")
    if a.status != "approved":
        raise GuardrailError(409, "not_approved", f"onay durumu '{a.status}'")
    if a.expires_at < now():
        a.status = "expired"
        raise GuardrailError(409, "expired", "onayın süresi doldu")
    h = action_hash(tenant_id=tenant_id, agent_id=agent_id, action=action, resource=resource, params=params)
    if h != a.action_hash or tenant_id != a.tenant_id:
        raise GuardrailError(409, "scope_mismatch", "onay bu eylem / kaynak / parametrelerle eşleşmiyor")
    if recheck is not None and not await recheck():
        a.status, a.result = "failed", "yürütme öncesi yetki / policy kontrolü reddetti"
        raise GuardrailError(403, "recheck_failed", a.result)
    a.status, a.consumed_at, a.consumed_by = "executed", now(), consumer
    await audit(s, actor=consumer, action="guardrail.approval.executed", target=str(a.id))
    return a


def approval_ttl(cfg: dict) -> timedelta:
    return timedelta(minutes=int(cfg.get("approval_timeout_minutes", 60)))
